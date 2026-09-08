"""Database tools: list, read and edit the id-indexed tables under ``Data/``."""

from __future__ import annotations

import copy
from typing import Any

from ..analysis import REFERENCE_RULES, find_references
from ..convert import apply_patch, to_json
from ..project import ProjectError
from ..rubytypes import RubyObject, RubyString
from ..schema import ALL_DATABASE_TABLES, ALL_TABLE_KEYS, ARRAY, HASH, SCRIPTS, SINGLE
from .registry import (
    bare_name,
    MAX_OPS,
    refuse_unknown_fields,
    opt_dict,
    ops_arg,
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
    opt_list,
    opt_str,
    prop,
    schema,
    tool,
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

# Tool schemas are built before a project is open, so the enum is the union of
# every engine's tables; the runtime check below rejects the ones this project
# does not have, and says why.
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

# The operations db_edit accepts. Named once, so the schema that advertises
# them and the reader that enforces them cannot drift apart.
DB_OPS = ("update", "create", "delete")

# How many of the references to a deleted entry are listed back. Enough to see
# the shape of the problem without turning a delete into a report.
REFERENCE_PREVIEW = 25

# Everything db_edit will actually change. map_infos is left out
# deliberately: the tree and the map files have to move together, and
# only map_edit does both, so advertising it here would offer an edit
# that is always refused.
EDITABLE_DATABASE_TABLES = [t for t in ALL_DATABASE_TABLES
                            if t != "map_infos"]


def _table_arg(ctx: Context, args: dict[str, Any]) -> str:
    table = need_str(args, "table")
    entry = ctx.project.data_files.get(table)
    if entry is None:
        if table in ALL_TABLE_KEYS:
            raise ToolError(
                "%s has no %s table. %s"
                % (ctx.project.profile.label, table,
                   ctx.project.profile.absence_hint(table)))
        raise ToolError("unknown table %r; this project has %s"
                        % (table, ", ".join(sorted(ctx.project.data_files))))
    if entry.container not in (ARRAY, HASH):
        raise ToolError(
            "%s is not an id-indexed table. Use raw_data for %s, or the scripts "
            "tool for the script editor." % (table, table)
        )
    return table


def _brief_field(ctx: Context, value: Any) -> Any:
    if isinstance(value, RubyString):
        return ctx.codec.decode(value.data)
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
    fields = list(SUMMARY_FIELDS.get(table, [])) + [
        f for f in opt_list(args, "fields") if isinstance(f, str)
    ]

    rows = []
    for entry_id, entry in entries:
        if id_from is not None and entry_id < id_from:
            continue
        if id_to is not None and entry_id > id_to:
            continue
        name = ""
        if isinstance(entry, RubyObject):
            raw_name = entry.get("name")
            if isinstance(raw_name, RubyString):
                name = ctx.codec.decode(raw_name.data)
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
        "total_entries": len(rows),
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
        "table": prop(STR, "Which table to read from.", enum=ALL_TABLE_KEYS),
        "ids": prop(ARR, "Entry ids to read; at most %d per call."
                    % MAX_IDS_PER_CALL, items={"type": INT},
                    maxItems=MAX_IDS_PER_CALL),
        "id": prop(INT, "A single entry id, if you only want one."),
        "tables": prop(STR, "How to render RGSS Table fields such as parameter "
                            "curves: summary (shape only, the default) or full "
                            "(every cell).", enum=["summary", "full"]),
        "refresh": REFRESH,
    }, ["table"]),
)
def db_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Whole entries, every field as stored. Reads only."""
    table = need_str(args, "table")
    if table not in ctx.project.data_files:
        raise ToolError("this %s project has no %r table; it has %s"
                        % (ctx.project.profile.label, table,
                           ", ".join(sorted(ctx.project.data_files))))
    if ctx.project.data_files[table].container == SCRIPTS:
        raise ToolError(
            "the scripts table holds zlib-compressed Ruby, which this tool "
            "would hand back as base64. Use the scripts tool, which inflates "
            "and decodes it.")
    refresh = opt_bool(args, "refresh")
    mode = one_of(args, "tables", ["summary", "full"], "summary")

    if ctx.project.data_files[table].container == SINGLE:
        value = ctx.project.load_data(table, refresh)
        return {"table": table, "entry": to_json(value, ctx.codec, mode)}

    ids = int_list(args, "ids", MAX_IDS_PER_CALL, minimum=0)
    single = opt_int(args, "id")
    if single is not None:
        ids.append(single)
    if not ids:
        raise ToolError("pass ids (an array) or id (a single number)")

    out, missing = [], []
    for entry_id in ids:
        entry = ctx.project.get_entry(table, entry_id, refresh)
        refresh = False  # only re-read the file once
        if entry is None:
            missing.append(entry_id)
        else:
            out.append({"id": entry_id, "entry": to_json(entry, ctx.codec, mode)})
    result: dict[str, Any] = {"table": table, "entries": out}
    if missing:
        result["missing_ids"] = missing
        result["note"] = ("ids with no entry are empty slots; db_edit with "
                          'op "create" fills them in')
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
    "preview.",
    schema({
        "table": prop(STR, "Which table to edit. map_infos is not editable "
                           "here: a tree entry and its Map### file have to "
                           "change together, so use map_edit for those.",
                      enum=EDITABLE_DATABASE_TABLES),
        "ops": prop(ARR, "Operations, applied in order.",
                    maxItems=MAX_OPS, items={
            "additionalProperties": False,
            "type": OBJ,
            "properties": {
                "op": {"type": STR, "enum": ["update", "create", "delete"]},
                "id": {"type": INT, "description":
                       "Entry id. Optional for create, which appends by default."},
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
    return {"table": table, "applied": applied, "warnings": warnings,
            "write": write}


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

    for raw_op in ops:
        op = one_of(raw_op, "op", list(DB_OPS))
        patch = opt_dict(raw_op, "patch")
        applied.append(_DB_OPS[op](request, raw_op, patch))
    return applied, request.warnings


class _DbRequest:
    """What every db_edit op needs, so each one takes two arguments.

    Also collects the warnings, which several ops add to.
    """

    __slots__ = ("ctx", "table", "container", "kind", "warnings")

    def __init__(self, ctx: Context, table: str, container: Any, kind: str):
        self.ctx = ctx
        self.table = table
        self.container = container
        self.kind = kind
        self.warnings: list[str] = []

    def entry_id(self, raw_op: dict[str, Any], op: str,
                 existing: bool) -> int:
        """The id an op names, checked for being one this table can hold."""
        entry_id = opt_int(raw_op, "id")
        if entry_id is None:
            raise ToolError("%s needs an id" % op)
        _check_id(self.table, entry_id, self.container, self.kind,
                  existing=existing)
        return entry_id

    def fields_of(self, entry: Any) -> set[str]:
        """The bare field names an entry carries, if it is an object at all."""
        return set(entry.fields()) if isinstance(entry, RubyObject) else set()

    def sibling_fields(self) -> set[str]:
        """The fields any existing entry in this table carries.

        What a blank create is judged against: the class model documents no
        database class, so the project's own entries are the only authority
        for which fields one of these has.
        """
        for _, row in self.ctx.project.entries(self.table):
            if isinstance(row, RubyObject):
                return set(row.fields())
        return set()

    def refuse_id_patch(self, patch: dict[str, Any], entry_id: int) -> None:
        """Refuse a patch that would move an entry's own id.

        An RGSS entry in an id-indexed array carries @id equal to its own slot,
        and the engine subscripts the array on that. Setting @id to anything
        else leaves an entry the engine cannot find.
        """
        if not _carries_id(self.kind):
            return
        # Bare, like every other refusal list: "@id" is the same field.
        given = next((patch[key] for key in patch
                      if bare_name(key) == "id"), None)
        if given not in (None, entry_id):
            raise ToolError(
                "the patch sets id %s but this entry lives in slot %d; an RGSS "
                "entry's id is its position, so leave id out of the patch"
                % (echo(given), entry_id))

    def require(self, entry_id: int, op: str) -> Any:
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
    entry = request.require(entry_id, "update")
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
        _check_id(table, entry_id, request.container, request.kind)
    else:
        _check_id(table, entry_id, request.container, request.kind)
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
    if isinstance(entry, RubyObject) and _carries_id(request.kind):
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
               patch: dict[str, Any]) -> dict[str, Any]:
    """Clear an entry, reporting what still points at it."""
    ctx, table, kind = request.ctx, request.table, request.kind
    entry_id = request.entry_id(raw_op, "delete", existing=True)
    if ctx.project.get_entry(table, entry_id) is None:
        raise ToolError("%s has no entry %d" % (table, entry_id))

    scanned = table in REFERENCE_RULES
    references = (find_references(ctx.project, table, entry_id,
                                  limit=REFERENCE_PREVIEW) if scanned else [])
    if kind == ARRAY:
        # Keep the slot, so every later id keeps its position. That is what the
        # editor does when you clear an entry.
        ctx.project.set_entry(table, entry_id, None)
    else:
        request.container.delete(entry_id)

    if references:
        request.warnings.append(
            "entry %d is still referenced by %d event command(s); those "
            "commands now point at nothing" % (entry_id, len(references)))
    elif not scanned:
        # A zero here would read as "nothing points at it", when in fact
        # nothing looked: only some tables have command parameter rules.
        request.warnings.append(
            "no reference scan exists for %s, so this deletion was not checked "
            "against anything that might point at entry %d. Run validate "
            "afterwards." % (table, entry_id))
    return {"op": "delete", "id": entry_id,
            "references_checked": scanned,
            "references_found": len(references) if scanned else None,
            "references": references}


_DB_OPS = {"update": _op_update, "create": _op_create, "delete": _op_delete}


def _check_id(table: str, entry_id: int, container: Any, kind: str,
              existing: bool = False) -> None:
    """Refuse an entry id that cannot mean what the caller intended.

    ``project.set_entry`` pads an array up to the id it is given and indexes it
    directly, so a negative id used to write over the *last* entry (Python's
    ``value[-1]``) and a huge one padded the table with nil slots -- both
    reported as a successful create.
    """
    if entry_id < 0:
        raise ToolError(
            "id must not be negative, got %d. Database entries are numbered "
            "from 1." % entry_id)
    if kind == ARRAY and entry_id == 0:
        raise ToolError(
            "id 0 is not an entry: RPG Maker keeps slot 0 empty so that every "
            "id matches its position in the array. Ids start at 1.")
    if existing:
        return
    ceiling = (min(MAX_ENTRY_ID, len(container) + ARRAY_GROWTH_HEADROOM)
               if kind == ARRAY else MAX_ENTRY_ID)
    if entry_id > ceiling:
        raise ToolError(
            "id %d is too high; this table has %d entries, so ids up to %d can "
            "be created. Creating id %d would fill the table with %d empty "
            "slots." % (entry_id, len(container), ceiling, entry_id,
                        entry_id - len(container)))


def _carries_id(kind: str) -> bool:
    """Whether entries in this container carry their own @id.

    An id-indexed Array does: the engine subscripts it by id, and each entry
    repeats that id in @id. A Hash-keyed table does not -- MapInfos entries are
    found by their key, and RPG::MapInfo has no @id to set. Keyed off the
    container rather than the documented field list, because the field tables
    describe what the editor shows and do not list @id at all.
    """
    return kind == ARRAY


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
