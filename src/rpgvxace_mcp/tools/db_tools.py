"""Database tools: list, read and edit the id-indexed tables under ``Data/``."""

from __future__ import annotations

import copy
from typing import Any

from ..analysis import REFERENCE_RULES, find_references
from ..convert import apply_patch, to_json
from ..project import ProjectError
from ..rubytypes import RubyObject, RubyString
from ..schema import ALL_DATABASE_TABLES, ALL_TABLE_KEYS, ARRAY, HASH
from .registry import (
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

TABLE_LIST = ", ".join(ALL_DATABASE_TABLES)

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
        "fields": prop(ARR, "Extra field names to include for each entry, beyond "
                            "the defaults for the table.", items={"type": STR}),
        "limit": prop(INT, "Maximum entries to return (default 50).", minimum=1),
        "offset": prop(INT, "Skip this many matching entries first."),
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

    offset = opt_int(args, "offset", 0) or 0
    limit = opt_int(args, "limit", 50) or 50
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
    refresh = opt_bool(args, "refresh")
    mode = one_of(args, "tables", ["summary", "full"], "summary")

    if ctx.project.data_files[table].container == "single":
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
        "ops": prop(ARR, "Operations, applied in order.", items={
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

    Refuses map_infos, an id outside 1..MAX_ENTRY_ID or far past the end
    of the table, slot 0 of an id-indexed array, and a patch that would
    move an entry's own id. Every mutation is staged, so nothing
    survives unless the write lands.
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
    ops = opt_list(args, "ops")
    if not ops:
        raise ToolError("ops must contain at least one operation")
    dry_run = opt_bool(args, "dry_run")

    container = ctx.project.load_data(table)
    kind = ctx.project.container_kind(table, container)
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []
    filename = ctx.project.filename_for(table)

    # Everything below mutates the cached container. `staged` drops that cache
    # again unless the write actually lands, so a dry run, a refusal, or an op
    # that fails halfway leaves nothing behind for a later write to pick up.
    with ctx.staged(filename) as write_landed:
        for raw_op in ops:
            if not isinstance(raw_op, dict):
                raise ToolError("each op must be an object, got %r" % (raw_op,))
            op = one_of(raw_op, "op", ["update", "create", "delete"])
            patch = raw_op.get("patch") or {}
            if patch and not isinstance(patch, dict):
                raise ToolError("patch must be an object")

            if op == "update":
                entry_id = opt_int(raw_op, "id")
                if entry_id is None:
                    raise ToolError("update needs an id")
                _check_id(table, entry_id, container, kind, existing=True)
                entry = ctx.project.get_entry(table, entry_id)
                if entry is None:
                    raise ToolError(
                        "%s has no entry %d. Use op \"create\" (optionally with "
                        "clone_from) to add it." % (table, entry_id)
                    )
                changes = apply_patch(entry, patch, ctx.codec)
                applied.append({"op": op, "id": entry_id, "changes": changes})

            elif op == "create":
                entry_id = opt_int(raw_op, "id")
                if entry_id is None:
                    entry_id = _next_free_id(container, kind)
                else:
                    _check_id(table, entry_id, container, kind)
                    if ctx.project.get_entry(table, entry_id) is not None:
                        raise ToolError(
                            "%s already has an entry %d; use op \"update\" to "
                            "change it" % (table, entry_id)
                        )
                clone_from = opt_int(raw_op, "clone_from")
                if clone_from is not None:
                    template = ctx.project.get_entry(table, clone_from)
                    if template is None:
                        raise ToolError("cannot clone %s %d: there is no such entry"
                                        % (table, clone_from))
                    entry = copy.deepcopy(template)
                else:
                    entry = _blank_entry(ctx, table)
                    warnings.append(
                        "entry %d was built from a blank %s rather than cloned, so it "
                        "carries only the fields given in the patch. If the editor "
                        "complains, create it with clone_from instead."
                        % (entry_id, ctx.project.data_files[table].cls)
                    )
                changes = apply_patch(entry, patch, ctx.codec) if patch else []
                if isinstance(entry, RubyObject) and _carries_id(kind):
                    # After the patch, and unconditionally: every RGSS data
                    # class carries @id equal to its own slot, a blank entry has
                    # no fields at all to test for, and a patch naming a
                    # different id would break the invariant the engine
                    # subscripts these arrays on.
                    if patch.get("id") not in (None, entry_id):
                        raise ToolError(
                            "the patch sets id %s but this entry lives in slot "
                            "%d; an RGSS entry's id is its position, so leave "
                            "id out of the patch"
                            % (echo(patch.get("id")), entry_id))
                    entry.set("id", entry_id)
                ctx.project.set_entry(table, entry_id, entry)
                applied.append({
                    "op": op, "id": entry_id, "cloned_from": clone_from, "changes": changes,
                })

            else:  # delete
                entry_id = opt_int(raw_op, "id")
                if entry_id is None:
                    raise ToolError("delete needs an id")
                _check_id(table, entry_id, container, kind, existing=True)
                if ctx.project.get_entry(table, entry_id) is None:
                    raise ToolError("%s has no entry %d" % (table, entry_id))
                scanned = table in REFERENCE_RULES
                references = (find_references(ctx.project, table, entry_id, limit=25)
                              if scanned else [])
                if kind == ARRAY:
                    # Keep the slot so every later id keeps its position, which is
                    # what the editor does when you clear an entry.
                    ctx.project.set_entry(table, entry_id, None)
                else:
                    container.delete(entry_id)
                applied.append({
                    "op": op, "id": entry_id,
                    "references_checked": scanned,
                    "references_found": len(references) if scanned else None,
                    "references": references,
                })
                if references:
                    warnings.append(
                        "entry %d is still referenced by %d event command(s); "
                        "those commands now point at nothing"
                        % (entry_id, len(references))
                    )
                elif not scanned:
                    # A zero here would read as "nothing points at it", when in
                    # fact nothing was looked at: only some tables have command
                    # parameter rules to scan for.
                    warnings.append(
                        "no reference scan exists for %s, so this deletion was "
                        "not checked against anything that might point at entry "
                        "%d. Run validate afterwards." % (table, entry_id)
                    )

        summary = "; ".join(
            "%s %s" % (item["op"], item["id"]) for item in applied
        )
        write = ctx.project.save_file(filename, dry_run=dry_run, tool="db_edit",
                                      summary=summary)
        if not dry_run:
            write_landed.done()
    return {"table": table, "applied": applied, "warnings": warnings, "write": write}


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
