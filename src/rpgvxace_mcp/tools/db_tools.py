"""Database tools: list, read and edit the id-indexed tables under ``Data/``."""

from __future__ import annotations

import copy
from typing import Any

from ..analysis import (
    REFERENCE_PREVIEW,
    REFERENCE_RULES,
    ReferencePreview,
    WalkBudget,
    reference_previews,
)
from ..convert import TABLE_MODES, apply_patch, new_walk, to_json
from ..project import ProjectError
from ..rubytypes import RubyObject, RubyString
from ..schema import ALL_DATABASE_TABLES, ALL_TABLE_KEYS, ARRAY, HASH, SCRIPTS, SINGLE
from .registry import (
    write_reply,
    bare_name,
    check_op_args,
    MAX_OPS,
    refuse_unknown_fields,
    opt_dict,
    ops_arg,
    str_list,
    echo,
    int_list,
    ARR,
    Context,
    DRY_RUN,
    INT,
    OBJ,
    REFRESH,
    STR,
    ToolError,
    need_str,
    one_of,
    opt_bool,
    opt_int,
    opt_str,
    prop,
    schema,
    tool,
    decoded,
)

# Extra fields worth showing beside the name when listing a table.
SUMMARY_FIELDS: dict[str, list[str]] = {
    "actors": ["class_id", "initial_level", "character_name", "face_name"],
    "classes": ["position"],
    "skills": ["mp_cost", "base_damage", "scope"],
    "items": ["price", "consumable", "scope"],
    "weapons": ["price", "atk"],
    "armors": ["price", "kind"],
    "enemies": ["maxhp", "exp", "gold", "battler_name"],
    "troops": ["members"],
    "states": ["restriction", "priority"],
    "animations": ["frame_max", "animation1_name"],
    "common_events": ["trigger", "switch_id", "list"],
    "map_infos": ["parent_id", "order"],
    "areas": ["map_id", "order"],
    "tilesets": ["mode", "tileset_names"],
}

# The highest entry id this server will create. RPG Maker's own editor tops out
# far below this; the point is to refuse an id that would pad a database array
# with hundreds of thousands of nil slots.
MAX_ENTRY_ID = 9999

# How far past the end of an id-indexed array one create may reach. Creating
# beyond this would pad the table with empty slots the caller did not ask for.
ARRAY_GROWTH_HEADROOM = 1000

# How many ids one db_get call will fetch. Each one is a lookup plus its own
# JSON conversion budget.
MAX_IDS_PER_CALL = 200

# How many extra field names one listing looks up per row.
MAX_EXTRA_FIELDS = 32

# How many rows one listing returns. `offset` is what paging is for; a listing
# larger than this is not one a caller reads.
MAX_ROWS = 500

TABLE_LIST = ", ".join(ALL_DATABASE_TABLES)

# The two table lists the schemas below advertise.
#
# Both are unions across engines, because a tool schema is built before any
# project is open: the runtime check in `_table_arg` rejects the tables this
# particular project does not have, and says why. An enum is still the only
# place a caller can learn what a tool takes before calling it, so each list
# is narrowed to what its own tool will really accept.

# The operations db_edit accepts, and what each one reads. One table, so the
# schema that advertises them, the reader that enforces them and the dispatcher
# that runs them cannot drift apart -- the comment here claimed that while
# three separate lists of the same three names sat in this file, and a test now
# holds the dispatcher to this.
#
# `delete` reads no patch: an op meaning to rename an entry and delete it did
# the delete and reported nothing amiss.
DB_OP_ARGS = {
    "update": {"op", "id", "patch"},
    "create": {"op", "id", "clone_from", "patch"},
    "delete": {"op", "id"},
}


# Every table db_get will read. Scripts is left out rather than advertised and
# then refused at runtime: it is the script editor, not an id-indexed table,
# and the scripts tool is what reads it.
READABLE_DATABASE_TABLES = [t for t in ALL_TABLE_KEYS if t != "scripts"]

# Everything db_edit will actually change. map_infos is left out deliberately:
# the tree and the map files have to move together and only map_edit does
# both, so advertising it here would offer an edit that is always refused.
#
# This paragraph used to sit above the readable list, where it read as a
# description of that one -- which is how a comment goes wrong without
# anything being wrong with the code.
EDITABLE_DATABASE_TABLES = [t for t in ALL_DATABASE_TABLES
                            if t != "map_infos"]


def _table_arg(ctx: Context, args: dict[str, Any],
               allow_single: bool = False) -> str:
    """The `table` argument, refused if the calling tool cannot act on it.

    ``allow_single`` is for db_get, which legitimately reads a one-object file
    like System while every other caller here needs an id-indexed table.

    That one difference is why db_get used to carry its own copy of these
    refusals, and the copy had drifted into being worse: it named the fifteen
    tables the project does have and said nothing about *why* the asked-for
    one was missing, so `db_get table="areas"` on a VX Ace project answered a
    question the caller had not asked, while `db_list table="areas"` explained
    that VX Ace replaced encounter areas with map regions and said to paint
    region ids on tile layer 3. Same question, same project, and only one of
    the two answers was usable.
    """
    table = need_str(args, "table")
    entry = ctx.project.data_files.get(table)
    if entry is None:
        if table in ALL_TABLE_KEYS:
            # A table this *engine* does not have, which is a different fact
            # from a name nobody recognises -- and the only one of the two a
            # caller can do something about.
            raise ToolError(
                "%s has no %s table. %s"
                % (ctx.project.profile.label, table,
                   ctx.project.profile.absence_hint(table)))
        raise ToolError("unknown table %r; this project has %s"
                        % (table, ", ".join(sorted(ctx.project.data_files))))
    if entry.container == SCRIPTS:
        # Named apart from the generic one-object case: raw_data refuses the
        # scripts table outright, so offering raw_data as the remedy here
        # would be a remedy that cannot work.
        raise ToolError(
            "%s is the script editor, not an id-indexed table. Use the "
            "scripts tool, which inflates and decodes a slot body for you."
            % table)
    if entry.container == SINGLE and allow_single:
        return table
    if entry.container not in (ARRAY, HASH):
        raise ToolError(
            "%s is one object, not an id-indexed table. Read a field of it "
            "with raw_data, or the whole thing with db_get." % table)
    return table


def _brief_field(ctx: Context, value: Any) -> Any:
    """One field of a listing row, small enough to scan.

    A string goes through `decoded`, which caps and sanitises it: this is a
    listing, so one crafted name would otherwise be repeated into every row
    of the reply.
    """
    if isinstance(value, RubyString):
        return decoded(ctx, value)
    if isinstance(value, list):
        return {"count": len(value)}
    if isinstance(value, RubyObject):
        return "<%s>" % value.cls
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return "<%s>" % type(value).__name__


@tool(
    "db_list",
    "List database entries",
    "List entries from one database table with a short summary of each. Start "
    "here to find the id you need, then use db_get for full detail. Tables: "
    + TABLE_LIST + ".",
    schema({
        "table": prop(STR, "Which table to list. One of: " + TABLE_LIST,
                      enum=ALL_DATABASE_TABLES),
        "name_contains": prop(STR, "Only entries whose name contains this text "
                                   "(case-insensitive)."),
        "id_from": prop(INT, "Lowest id to include."),
        "id_to": prop(INT, "Highest id to include."),
        "fields": prop(ARR, "Extra field names to include for each entry, "
                            "beyond the defaults for the table. At most %d."
                            % MAX_EXTRA_FIELDS, items={"type": STR},
                       maxItems=MAX_EXTRA_FIELDS),
        "limit": prop(INT, "Maximum entries to return (default 50, at most "
                           "%d)." % MAX_ROWS, minimum=1, maximum=MAX_ROWS),
        "offset": prop(INT, "Skip this many matching entries first.",
                       minimum=0),
        "refresh": REFRESH,
    }, ["table"]),
)
def db_list(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """One table, summarised, filtered and paged. Reads only."""
    table = _table_arg(ctx, args)
    refresh = opt_bool(args, "refresh")
    entries = ctx.project.entries(table, refresh)

    id_from, id_to = opt_int(args, "id_from"), opt_int(args, "id_to")
    needle = (opt_str(args, "name_contains") or "").lower()
    # str_list rather than a filter: dropping a non-string entry left a
    # caller who passed [["price"]] with rows carrying no extra fields and no
    # error, which is the silent coercion every other argument here refuses.
    fields = list(SUMMARY_FIELDS.get(table, [])) + str_list(
        args, "fields", MAX_EXTRA_FIELDS)

    rows = []
    for entry_id, entry in entries:
        if id_from is not None and entry_id < id_from:
            continue
        if id_to is not None and entry_id > id_to:
            continue
        # Through `decoded`, like every other text this layer returns. The
        # fifth bare copy of that decode lived here, on the one field of the
        # row that is always present -- while `_brief_field` below goes
        # through it and says why: a listing repeats one crafted name into
        # every row. The filter reads the capped text too, deliberately: a
        # needle past 200 characters is not a name anyone is looking for, and
        # the alternative is decoding a megabyte per row to find out.
        name = decoded(ctx, entry.get("name")) if isinstance(
            entry, RubyObject) else ""
        if needle and needle not in name.lower():
            continue
        row: dict[str, Any] = {"id": entry_id, "name": name}
        if isinstance(entry, RubyObject):
            for field in fields:
                if entry.has(field):
                    row[field] = _brief_field(ctx, entry.get(field))
        else:
            row["value"] = _brief_field(ctx, entry)
        rows.append(row)

    offset = opt_int(args, "offset", 0, minimum=0) or 0
    limit = opt_int(args, "limit", 50, minimum=1, maximum=MAX_ROWS) or 50
    window = rows[offset:offset + limit]
    return {
        "table": table,
        "file": ctx.project.filename_for(table),
        "class": ctx.project.data_files[table].cls,
        # The number matching the filters, which is what was counted --
        # "total" read as the size of the table, so a filtered listing looked
        # like the whole of a much smaller one.
        "matching_entries": len(rows),
        "returned": len(window),
        "offset": offset,
        "entries": window,
    }


@tool(
    "db_get",
    "Read database entries",
    "Read complete database entries, with every field as stored in the file. "
    "Field names match the RGSS instance variables minus the leading @, which "
    "is what db_edit patches expect.",
    schema({
        "table": prop(STR, "Which table to read from. Not scripts -- a slot "
                           "body is compressed Ruby, so the scripts tool "
                           "reads those.", enum=READABLE_DATABASE_TABLES),
        # The same bound `id` declares below, and deliberately no maximum:
        # asking about an id that does not exist is a legitimate question, and
        # the answer -- "missing_ids", with a note on whether db_edit could
        # create it -- is more use than a refusal. MAX_ENTRY_ID is the ceiling
        # on what this server will *create*, which is a different question.
        #
        # The two used to disagree: `ids=[999999999]` was accepted and
        # `id=999999999` refused, one request with two spellings and two
        # answers about what is legal.
        "ids": prop(ARR, "Entry ids to read; at most %d per call. An id with "
                         "no entry comes back under \"missing_ids\" rather "
                         "than as an error." % MAX_IDS_PER_CALL,
                    items={"type": INT, "minimum": 0},
                    maxItems=MAX_IDS_PER_CALL),
        # No maximum, matching `ids` above: this reads, and a read of an id
        # that is not there is answered rather than refused. It used to carry
        # `maximum=MAX_ENTRY_ID` -- the *create* ceiling -- while `ids` had
        # none, so the same request refused or succeeded depending on how it
        # was spelled.
        "id": prop(INT, "A single entry id, if you only want one. The editor "
                        "numbers entries from 1; an id with no entry comes "
                        "back under \"missing_ids\".", minimum=0),
        "tables": prop(STR, "How to render RGSS Table fields such as parameter "
                            "curves: summary (shape only, the default) or full "
                            "(every cell).", enum=list(TABLE_MODES)),
        "refresh": REFRESH,
    }, ["table"]),
)
def db_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Whole entries, every field as stored. Reads only."""
    table = _table_arg(ctx, args, allow_single=True)
    refresh = opt_bool(args, "refresh")
    mode = one_of(args, "tables", list(TABLE_MODES), TABLE_MODES[0])

    if ctx.project.data_files[table].container == SINGLE:
        # Refused rather than ignored: `db_get table="system" ids=[1,2]` used
        # to return the whole System object, which reads as though the ids had
        # been honoured and two entries had come back.
        given = [name for name in ("ids", "id") if args.get(name) is not None]
        if given:
            raise ToolError(
                "%s is one object, not a table of entries, so %s does not "
                "apply to it. Leave it out to read the whole thing, or read "
                "one field with raw_data."
                % (table, " or ".join(echo(name) for name in given)))
        value = ctx.project.load_data(table, refresh)
        return {"table": table, "entry": to_json(value, ctx.codec, mode)}

    ids = int_list(args, "ids", MAX_IDS_PER_CALL, minimum=0)
    # No maximum, matching `ids` above. MAX_ENTRY_ID is the create ceiling,
    # and this reads: an id with no entry is answered under "missing_ids",
    # with a note on whether db_edit could fill it in.
    single = opt_int(args, "id", minimum=0)
    if single is not None:
        ids.append(single)
    if not ids:
        raise ToolError("pass ids (an array) or id (a single number)")

    out, missing = [], []
    # One budget for the whole response. Each entry used to get its own, so
    # reading MAX_IDS_PER_CALL crafted entries was an unbounded walk in
    # aggregate however tight the per-entry bound was.
    walk = new_walk()
    for entry_id in ids:
        entry = ctx.project.get_entry(table, entry_id, refresh)
        refresh = False  # only re-read the file once
        if entry is None:
            missing.append(entry_id)
        else:
            out.append({"id": entry_id,
                        "entry": to_json(entry, ctx.codec, mode, walk)})
    result: dict[str, Any] = {"table": table, "entries": out}
    if missing:
        result["missing_ids"] = missing
        # Only the ids db_edit will actually accept get that advice, and
        # "above zero" was not that test. db_edit refuses two more classes:
        # an id far past the end of the table, which would pad it with empty
        # slots, and every id in a table it will not touch at all. The comment
        # here claimed this filter already did that.
        if table not in EDITABLE_DATABASE_TABLES:
            result["note"] = (
                "ids with no entry are empty slots. db_edit does not change "
                "%s, so it cannot fill these in -- the map tree is edited "
                "with map_edit, which moves the tree entry and the map file "
                "together." % table
                if table == "map_infos" else
                "ids with no entry are empty slots, and db_edit does not "
                "change %s." % table)
            return result
        container = ctx.project.load_data(table)
        kind = ctx.project.container_kind(table, container)
        creatable = [i for i in missing
                     if _can_create(container, kind, i)]
        refused = [i for i in missing if i not in creatable]
        result["note"] = (
            'ids with no entry are empty slots; db_edit with op "create" '
            "fills in %s" % (", ".join(str(i) for i in creatable)
                             if creatable else "none of these"))
        if refused:
            result["note"] += (
                ". It will not create %s: id 0 is never an entry -- RPG Maker "
                "keeps slot 0 empty so every id matches its position -- and an "
                "id far past the end of the table would pad it with empty "
                "slots. Create the ids in between first."
                % ", ".join(str(i) for i in refused))
    return result


@tool(
    "db_edit",
    "Create, update or delete database entries",
    "Apply a batch of changes to one database table and save the file. "
    "Patches merge field by field, so send only what changes: "
    '{"op": "update", "id": 3, "patch": {"name": "Elixir", "price": 900}}. '
    "Nested objects merge too. To add an entry, prefer "
    '{"op": "create", "clone_from": 1, "patch": {...}} -- cloning an existing '
    "entry guarantees the new one carries exactly the fields this project's "
    "editor expects. Every write backs the file up first; pass dry_run to "
    "preview. A delete reports up to %d of the event commands that still "
    "point at the entry, with \"references_truncated\" when there are more "
    "and \"references_scan_stopped\" when the scan gave up before finishing "
    "-- that last one means the zero is not an answer."
    % REFERENCE_PREVIEW,
    schema({
        "table": prop(STR, "Which table to edit. map_infos is not editable "
                           "here: a tree entry and its Map### file have to "
                           "change together, so use map_edit for those.",
                      enum=EDITABLE_DATABASE_TABLES),
        "ops": prop(ARR, "Operations, applied in order. At most %d per "
                         "call." % MAX_OPS,
                    maxItems=MAX_OPS, items={
            "additionalProperties": False,
            "type": OBJ,
            "properties": {
                "op": {"type": STR, "enum": list(DB_OP_ARGS)},
                # No declared minimum or maximum. The real ceiling is
                # this table's own length plus a little headroom, which a
                # static bound cannot express -- and declaring %d would be
                # worse than declaring nothing, because ids far below it are
                # refused for a small table. `_check_id` names the table's
                # size in its refusal, which is the answer a caller can act
                # on.
                "id": {"type": INT, "description":
                       "Entry id, numbered from 1. For an id-indexed "
                       "table op \"create\" accepts an id up to about a "
                       "thousand past the end, and its refusal says exactly "
                       "how far; a hash-keyed table (VX's areas) has no slots "
                       "to pad, so any id up to %d. Optional for create, "
                       "which appends by default." % MAX_ENTRY_ID},
                "clone_from": {"type": INT, "description":
                               "For create: copy this entry, then apply the patch."},
                "patch": {"type": OBJ, "description":
                          "Fields to set, using RGSS field names without the @."},
            },
            "required": ["op"],
        }),
        "dry_run": DRY_RUN,
    }, ["table", "ops"]),
    writes=True,
)
def db_edit(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Apply ops to one table and save it.

    Refuses map_infos, an id outside 1..MAX_ENTRY_ID or far past the end of the
    table, slot 0 of an id-indexed array, and a patch that would move an
    entry's own id. Every mutation is staged, so nothing survives unless the
    write lands.
    """
    table = _table_arg(ctx, args)
    if table == "map_infos":
        # A tree entry and its Map### file have to change together, and
        # map_edit is what knows how: it refuses to orphan a child map, warns
        # about transfers into the map, and removes the file alongside the
        # entry. Editing the tree from here left the two out of step.
        raise ToolError(
            "map_infos cannot be edited here, because a tree entry and its "
            "Map### file have to change together. Use map_edit with op "
            "\"create\", \"delete\" or \"update\" instead.")
    ops = ops_arg(args)
    dry_run = opt_bool(args, "dry_run")
    filename = ctx.project.filename_for(table)

    # Everything below mutates the cached container. `staged` drops that cache
    # again unless the write actually lands, so a dry run, a refusal, or an op
    # that fails halfway leaves nothing behind for a later write to pick up.
    with ctx.staged(filename) as write_landed:
        applied, warnings = _apply_db_ops(ctx, table, ops)
        summary = "; ".join("%s %s" % (item["op"], item["id"]) for item in applied)
        write = write_landed.save(filename, dry_run=dry_run, tool="db_edit",
                                  summary=summary)
    # `write_reply` rather than a copy of the rule here: this file had the
    # only correct version of it, and five of the other six writing tools
    # phrased a preview as a completed write.
    return write_reply({"table": table, "applied": applied,
                        "warnings": warnings, "write": write}, dry_run)


def _apply_db_ops(ctx: Context, table: str,
                  ops: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                      list[str]]:
    """Run every op against the cached table, in order.

    Returns what was applied and any warnings. Nothing here saves: the caller
    owns the write, and owns discarding these mutations if it does not happen.
    """
    container = ctx.project.load_data(table)
    kind = ctx.project.container_kind(table, container)
    request = _DbRequest(ctx, table, container, kind)
    applied: list[dict[str, Any]] = []

    # Every delete's reference scan first, in one pass. Asking per op meant
    # one whole-project traversal per delete over the same unchanged command
    # lists, and the shared allowance turned that into a batch where the
    # first few ops were answered and the rest reported their scan as
    # unfinished. Read here rather than inside `_op_delete` so the batch pays
    # for one walk; ids that are not deletes are not asked about.
    request.references = reference_previews(
        ctx.project, table,
        {entry_id for entry_id in (opt_int(raw_op, "id")
                                   for raw_op in ops
                                   if raw_op.get("op") == "delete")
         if entry_id is not None},
        "a delete's reference scan", request.walk)

    for index, raw_op in enumerate(ops):
        op = one_of(raw_op, "op", list(DB_OP_ARGS))
        check_op_args(op, raw_op, DB_OP_ARGS, "ops[%d]" % index)
        patch = opt_dict(raw_op, "patch")
        applied.append(_DB_OPS[op](request, raw_op, patch))
    return applied, request.warnings


class _DbRequest:
    """What every db_edit op needs, so each one takes two arguments.

    Also collects the warnings, which several ops add to.
    """

    __slots__ = ("ctx", "table", "container", "kind", "warnings", "walk",
                 "references", "_siblings")

    def __init__(self, ctx: Context, table: str, container: Any, kind: str):
        self.ctx = ctx
        self.table = table
        self.container = container
        self.kind = kind
        self.warnings: list[str] = []
        # One allowance for every reference scan this call makes. A delete
        # asks what still points at the entry, and that walk returns early
        # only when it finds hits -- so `ops` carrying two hundred deletes of
        # unreferenced entries used to get two hundred separate allowances,
        # two hundred whole-project traversals in one uninterruptible call.
        # `validate` states the same rule for itself one module over.
        self.walk = WalkBudget()
        # Every delete's reference scan, answered in one pass before the ops
        # are dispatched. Filled by `_apply_db_ops`; empty for a batch with
        # no deletes in it.
        self.references: dict[int, ReferencePreview] = {}
        # `sibling_fields` re-runs `project.entries(table)` -- a
        # `container_kind` call and a `sorted_items()` sort -- and a create
        # asks it twice. Neither answer can change inside one call, and
        # `MAX_OPS` is 200 of them.
        self._siblings: "set[str] | None" = None

    def entry_id(self, raw_op: dict[str, Any], op: str,
                 existing: bool) -> int:
        """The id an op names, checked for being one this table can hold."""
        entry_id = opt_int(raw_op, "id")
        if entry_id is None:
            raise ToolError("%s needs an id" % op)
        _check_id(self, entry_id, existing=existing)
        return entry_id

    def fields_of(self, entry: Any) -> set[str]:
        """The bare field names an entry carries, if it is an object at all."""
        return set(entry.fields()) if isinstance(entry, RubyObject) else set()

    def sibling_fields(self) -> set[str]:
        """The fields any existing entry in this table carries.

        What a blank create is judged against: the class model documents no
        database class, so the project's own entries are the only authority
        for which fields one of these has.

        Answered once per call. It cannot change while a call is running --
        nothing here adds a field to an existing sibling -- and asking again
        re-sorts the whole table.
        """
        if self._siblings is None:
            self._siblings = set()
            for _, row in self.ctx.project.entries(self.table):
                if isinstance(row, RubyObject):
                    self._siblings = set(row.fields())
                    break
        return self._siblings

    def refuse_id_patch(self, patch: dict[str, Any], entry_id: int) -> None:
        """Refuse a patch that would move an entry's own id.

        An RGSS entry in an id-indexed array carries @id equal to its own slot,
        and the engine subscripts the array on that. Setting @id to anything
        else leaves an entry the engine cannot find.
        """
        if not _carries_id(self):
            return
        # Bare, like every other refusal list: "@id" is the same field.
        given = next((patch[key] for key in patch
                      if bare_name(key) == "id"), None)
        if given not in (None, entry_id):
            raise ToolError(
                "the patch sets id %s but this entry lives in slot %d; an RGSS "
                "entry's id is its position, so leave id out of the patch"
                % (echo(given), entry_id))

    def require(self, entry_id: int) -> Any:
        """The entry at an id, or a refusal naming what to do instead."""
        entry = self.ctx.project.get_entry(self.table, entry_id)
        if entry is None:
            raise ToolError(
                "%s has no entry %d. Use op \"create\" (optionally with "
                "clone_from) to add it." % (self.table, entry_id))
        return entry


def _op_update(request: _DbRequest, raw_op: dict[str, Any],
               patch: dict[str, Any]) -> dict[str, Any]:
    """Change fields on an entry that already exists."""
    entry_id = request.entry_id(raw_op, "update", existing=True)
    entry = request.require(entry_id)
    request.refuse_id_patch(patch, entry_id)
    refuse_unknown_fields(patch, request.fields_of(entry),
                          "%s entry %d" % (request.table, entry_id))
    changes = apply_patch(entry, patch, request.ctx.codec)
    return {"op": "update", "id": entry_id, "changes": changes}


def _op_create(request: _DbRequest, raw_op: dict[str, Any],
               patch: dict[str, Any]) -> dict[str, Any]:
    """Add an entry, cloned from another or built blank."""
    ctx, table = request.ctx, request.table
    entry_id = opt_int(raw_op, "id")
    if entry_id is None:
        entry_id = _next_free_id(request.container, request.kind)
        # Checked too: a hash-keyed table's existing keys come out of a file,
        # so the id derived from them is not automatically a usable one.
        _check_id(request, entry_id)
    else:
        _check_id(request, entry_id)
        if ctx.project.get_entry(table, entry_id) is not None:
            raise ToolError(
                "%s already has an entry %d; use op \"update\" to change it"
                % (table, entry_id))

    clone_from = opt_int(raw_op, "clone_from")
    if clone_from is not None:
        template = ctx.project.get_entry(table, clone_from)
        if template is None:
            raise ToolError("cannot clone %s %d: there is no such entry"
                            % (table, clone_from))
        entry = copy.deepcopy(template)
    else:
        entry = _blank_entry(ctx, table)
        request.warnings.append(
            "entry %d was built from a blank %s rather than cloned, so it "
            "carries only the fields given in the patch. If the editor "
            "complains, create it with clone_from instead."
            % (entry_id, ctx.project.data_files[table].cls))

    if patch:
        # A clone knows its own fields; a blank entry has none, so a sibling
        # in the same table is what says which fields this class carries.
        refuse_unknown_fields(patch, request.fields_of(entry) or
                              request.sibling_fields(),
                              "a new %s entry" % table)
    changes = apply_patch(entry, patch, ctx.codec) if patch else []
    if isinstance(entry, RubyObject) and _carries_id(request):
        # After the patch, and unconditionally: every RGSS entry in an
        # id-indexed array carries @id equal to its own slot, a blank entry has
        # no fields at all to test for, and a patch naming a different id would
        # break the invariant the engine subscripts these arrays on.
        request.refuse_id_patch(patch, entry_id)
        entry.set("id", entry_id)

    ctx.project.set_entry(table, entry_id, entry)
    return {"op": "create", "id": entry_id, "cloned_from": clone_from,
            "changes": changes}


def _op_delete(request: _DbRequest, raw_op: dict[str, Any],
               _patch: dict[str, Any]) -> dict[str, Any]:
    """Clear an entry, reporting what still points at it.

    Takes the patch argument it never reads, because `_DB_OPS` dispatches all
    three ops through one signature -- and `check_op_args` refuses a patch on
    a delete before this is reached, so there is never one to ignore. Named
    with a leading underscore so that is visible here.
    """
    ctx, table, kind = request.ctx, request.table, request.kind
    entry_id = request.entry_id(raw_op, "delete", existing=True)
    if ctx.project.get_entry(table, entry_id) is None:
        raise ToolError("%s has no entry %d" % (table, entry_id))

    scanned = table in REFERENCE_RULES
    # A scan that stopped early is a third state beside "found some" and
    # "found none": the walk returns early only when it finds hits, so
    # "nothing points at it" is exactly the answer that costs a full
    # traversal. `reference_preview` carries all three facts, because each of
    # them used to be derived here and again in map_edit.
    # From the one pass `_apply_db_ops` made before dispatching, so a batch
    # of two hundred deletes is one traversal rather than two hundred.
    preview = (request.references.get(entry_id)
               or ReferencePreview([], False, None)) if scanned else \
        ReferencePreview([], False, None)
    # `preview.as_reply` carries `truncated` and `incomplete` into the
    # reply now, so only the rows are still read here.
    references, incomplete = preview.rows, preview.incomplete
    if kind == ARRAY:
        # Keep the slot, so every later id keeps its position. That is what the
        # editor does when you clear an entry.
        ctx.project.set_entry(table, entry_id, None)
    else:
        request.container.delete(entry_id)

    if references:
        request.warnings.append(
            "entry %d is still referenced by %s event command(s); those "
            "commands now point at nothing" % (entry_id, preview.counted))
    elif not scanned:
        # A zero here would read as "nothing points at it", when in fact
        # nothing looked: only some tables have command parameter rules.
        request.warnings.append(
            "no reference scan exists for %s, so this deletion was not checked "
            "against anything that might point at entry %d. Run validate "
            "afterwards." % (table, entry_id))
    if incomplete:
        # Said as a warning as well as a field, because the field is a
        # sentence a caller can skip and this changes what the zero means.
        request.warnings.append(
            "the reference scan for entry %d did not finish, so the %d "
            "reference(s) reported are the ones found before it stopped and "
            "not the whole story: %s"
            % (entry_id, len(references), incomplete))
    reply = {"op": "delete", "id": entry_id,
             # Not a plain boolean any more: a scan that started and stopped
             # is neither "checked" nor "no scan exists".
             "references_checked": scanned and not incomplete,
             "references_found": len(references) if scanned else None}
    # The other three keys from `as_reply`, so this path and map_edit's
    # delete cannot drift again: `references`, `references_truncated`, and
    # `references_scan_stopped` only when the scan did stop.
    reply.update(preview.as_reply("references"))
    return reply


_DB_OPS = {"update": _op_update, "create": _op_create, "delete": _op_delete}


def _check_id(request: "_DbRequest", entry_id: int,
              existing: bool = False) -> None:
    """Refuse an entry id that cannot mean what the caller intended.

    ``project.set_entry`` pads an array up to the id it is given and indexes it
    directly, so a negative id used to write over the *last* entry (Python's
    ``value[-1]``) and a huge one padded the table with nil slots -- both
    reported as a successful create.
    """
    container, kind = request.container, request.kind
    if entry_id < 0:
        raise ToolError(
            "id must not be negative, got %d. Database entries are numbered "
            "from 1." % entry_id)
    if entry_id == 0 and _carries_id(request):
        # Asked of the same property as everything else about ids, rather than
        # of ARRAY-versus-HASH: VX's `areas` is Hash-keyed and numbers its
        # entries from 1 like every other table, so id 0 was accepted there
        # and then written with @id = 0.
        raise ToolError(
            "id 0 is not an entry: RPG Maker keeps slot 0 empty so that every "
            "id matches its position. Ids start at 1.")
    if existing:
        return
    ceiling = _create_ceiling(container, kind)
    if entry_id > ceiling:
        raise ToolError(
            "id %d is too high; this table has %d entries, so ids up to %d can "
            "be created. Creating id %d would fill the table with %d empty "
            "slots." % (entry_id, len(container), ceiling, entry_id,
                        entry_id - len(container)))


def _can_create(container: Any, kind: str, entry_id: int) -> bool:
    """Whether op "create" would accept this id, asked without raising.

    `_check_id` is the authority and refuses by raising, which is right at a
    write. A read that wants to *offer* create needs the same answer as a
    question -- and computing it a second time by hand is how db_get came to
    promise a create that db_edit then refused.
    """
    return entry_id >= 1 and entry_id <= _create_ceiling(container, kind)


def _create_ceiling(container: Any, kind: str) -> int:
    """The highest id op "create" will accept for this container.

    An id-indexed Array grows, so it is its length plus a little headroom. A
    Hash-keyed table has no slots to pad, so its only ceiling is the global
    one -- which is why the refusal below branches on the kind rather than
    telling a Hash it would "fill the table with empty slots".
    """
    if kind == ARRAY:
        return min(MAX_ENTRY_ID, len(container) + ARRAY_GROWTH_HEADROOM)
    return MAX_ENTRY_ID


def _carries_id(request: "_DbRequest") -> bool:
    """Whether entries in this table carry their own @id.

    Every id-indexed Array does: the engine subscripts it by id and each entry
    repeats that id in @id. Most Hash-keyed tables do not -- a MapInfos entry
    is found by its key and RPG::MapInfo has no id field.

    But not all of them. VX's `areas` is Hash-keyed and RPG::Area documents an
    `id`, so deciding this from ARRAY-versus-HASH -- as this did, in a
    docstring that asserted a Hash-keyed table never carries one -- meant an
    area created here was written with no @id at all, and a patch moving @id to
    another number was accepted. So it is decided from the class this table
    holds, which is the thing the question is actually about.

    Asked of the documented fields and of a live sibling, because either alone
    is incomplete: a table this server has no field list for is still readable,
    and a table whose entries are all blank has no sibling to ask.
    """
    if request.kind == ARRAY:
        return True
    held = request.ctx.project.data_files[request.table].cls
    documented = request.ctx.project.profile.class_fields.get(held or "", {})
    if "id" in documented:
        return True
    return "id" in request.sibling_fields()


def _next_free_id(container: Any, kind: str) -> int:
    if kind == ARRAY:
        return max(len(container), 1)
    keys = [k for k in container.keys() if isinstance(k, int)]
    return max(keys) + 1 if keys else 1


def _blank_entry(ctx: Context, table: str) -> RubyObject:
    cls = ctx.project.data_files[table].cls
    if cls == "-":
        raise ProjectError("%s entries cannot be created generically" % table)
    return RubyObject(cls, {})
