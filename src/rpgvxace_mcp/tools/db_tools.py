"""Database tools: list, read and edit the id-indexed tables under ``Data/``."""

from __future__ import annotations

import copy
from typing import Any

from ..analysis import find_references
from ..convert import apply_patch, to_json
from ..project import ProjectError
from ..rubytypes import RubyObject, RubyString
from ..schema import ALL_DATABASE_TABLES, ALL_TABLE_KEYS, ARRAY, HASH
from .registry import (
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
TABLE_LIST = ", ".join(ALL_DATABASE_TABLES)


def _table_arg(ctx: Context, args: dict[str, Any]) -> str:
    table = need_str(args, "table")
    entry = ctx.project.data_files.get(table)
    if entry is None:
        if table in ALL_TABLE_KEYS:
            raise ToolError(
                "%s has no %s table. %s" % (ctx.project.profile.label, table,
                                            _absence_hint(ctx, table)))
        raise ToolError("unknown table %r; this project has %s"
                        % (table, ", ".join(sorted(ctx.project.data_files))))
    if entry.container not in (ARRAY, HASH):
        raise ToolError(
            "%s is not an id-indexed table. Use raw_data for %s, or the scripts "
            "tool for the script editor." % (table, table)
        )
    return table


def _absence_hint(ctx: Context, table: str) -> str:
    try:
        ctx.project.profile.data_file(table)
    except KeyError as exc:
        return str(exc.args[0]).split(". ", 1)[-1]
    return ""


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
    "Field names match the RGSS2 instance variables minus the leading @, which "
    "is what db_edit patches expect.",
    schema({
        "table": prop(STR, "Which table to read from.", enum=ALL_TABLE_KEYS),
        "ids": prop(ARR, "Entry ids to read.", items={"type": INT}),
        "id": prop(INT, "A single entry id, if you only want one."),
        "tables": prop(STR, "How to render RGSS Table fields such as parameter "
                            "curves: summary (shape only, the default) or full "
                            "(every cell).", enum=["summary", "full"]),
        "refresh": REFRESH,
    }, ["table"]),
)
def db_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
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

    ids = [int(i) for i in opt_list(args, "ids")]
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
        "table": prop(STR, "Which table to edit.", enum=ALL_DATABASE_TABLES),
        "ops": prop(ARR, "Operations, applied in order.", items={
            "type": OBJ,
            "properties": {
                "op": {"type": STR, "enum": ["update", "create", "delete"]},
                "id": {"type": INT, "description":
                       "Entry id. Optional for create, which appends by default."},
                "clone_from": {"type": INT, "description":
                               "For create: copy this entry, then apply the patch."},
                "patch": {"type": OBJ, "description":
                          "Fields to set, using RGSS2 field names without the @."},
            },
            "required": ["op"],
        }),
        "dry_run": DRY_RUN,
    }, ["table", "ops"]),
    writes=True,
)
def db_edit(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    table = _table_arg(ctx, args)
    ops = opt_list(args, "ops")
    if not ops:
        raise ToolError("ops must contain at least one operation")
    dry_run = opt_bool(args, "dry_run")

    container = ctx.project.load_data(table)
    kind = ctx.project.container_kind(table, container)
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []

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
            elif ctx.project.get_entry(table, entry_id) is not None:
                raise ToolError(
                    "%s already has an entry %d; use op \"update\" to change it"
                    % (table, entry_id)
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
            if isinstance(entry, RubyObject) and entry.has("id"):
                entry.set("id", entry_id)
            changes = apply_patch(entry, patch, ctx.codec) if patch else []
            ctx.project.set_entry(table, entry_id, entry)
            applied.append({
                "op": op, "id": entry_id, "cloned_from": clone_from, "changes": changes,
            })

        else:  # delete
            entry_id = opt_int(raw_op, "id")
            if entry_id is None:
                raise ToolError("delete needs an id")
            if ctx.project.get_entry(table, entry_id) is None:
                raise ToolError("%s has no entry %d" % (table, entry_id))
            references = find_references(ctx.project, table, entry_id, limit=25)
            if kind == ARRAY:
                # Keep the slot so every later id keeps its position, which is
                # what the editor does when you clear an entry.
                ctx.project.set_entry(table, entry_id, None)
            else:
                container.delete(entry_id)
            applied.append({
                "op": op, "id": entry_id,
                "references_found": len(references),
                "references": references,
            })
            if references:
                warnings.append(
                    "entry %d is still referenced by %d event command(s); those "
                    "commands now point at nothing" % (entry_id, len(references))
                )

    filename = ctx.project.filename_for(table)
    summary = "; ".join(
        "%s %s" % (item["op"], item["id"]) for item in applied
    )
    write = ctx.project.save_file(filename, dry_run=dry_run, tool="db_edit",
                                 summary=summary)
    return {"table": table, "applied": applied, "warnings": warnings, "write": write}


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
