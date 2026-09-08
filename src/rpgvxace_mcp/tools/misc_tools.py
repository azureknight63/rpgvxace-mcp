"""Project-wide tools: info, validation, search, scripts, backups, raw access."""

from __future__ import annotations

import re
from typing import Any

from ..analysis import Location, iter_command_lists, validate as run_validate
from ..commands import command_name, summarize
from ..convert import from_json, to_json
from ..project import ProjectError
from ..rubytypes import RubyHash, RubyObject, RubyString, Table
from ..schema import (
    ALL_TABLE_KEYS,
    TILESET_FLAG_BITS,
    TILE_ID_RANGES,
    observe_class_fields,
)
from .registry import (
    ARR,
    BOOL,
    Context,
    DRY_RUN,
    INT,
    OBJ,
    REFRESH,
    STR,
    ToolError,
    need_int,
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

SEARCH_SCOPES = ["events", "database", "maps", "scripts"]
VALIDATE_SCOPES = ["maps", "events", "assets", "database"]


@tool(
    "project_info",
    "Describe the open project",
    "What this server is working on: the project root, the engine it detected, "
    "the text encoding it will read and write, which data files exist, and how "
    "many entries each table holds. Call this first. Pass include_schema to see "
    "the fields each Ruby class actually carries in this project, which is the "
    "authority for what db_edit patches may set.",
    schema({
        "include_schema": prop(BOOL, "Report the instance variables observed on "
                                     "each RPG:: class in this project."),
        "include_changes": prop(BOOL, "Include the recent change log for this project."),
        "refresh": REFRESH,
    }),
)
def project_info(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    refresh = opt_bool(args, "refresh")
    info = ctx.project.describe()

    counts, warnings = {}, {}
    for key, entry in ctx.project.data_files.items():
        if not ctx.project.exists(ctx.project.filename_for(key)):
            continue
        try:
            if entry.container in ("array", "hash"):
                counts[key] = len(ctx.project.entries(key, refresh))
            elif entry.container == "scripts":
                counts[key] = len(ctx.project.load_scripts())
            else:
                ctx.project.load_data(key, refresh)
                counts[key] = 1
        except (ProjectError, ValueError) as exc:
            warnings[key] = str(exc)
        file_warnings = ctx.project.file_warnings(ctx.project.filename_for(key))
        if file_warnings:
            warnings.setdefault(key, "; ".join(file_warnings))

    info["entry_counts"] = counts
    if warnings:
        info["file_warnings"] = warnings
    info["maps"] = ctx.project.map_ids_on_disk()
    if opt_bool(args, "include_schema"):
        info["schema"] = _schema_report(ctx, refresh)
    if opt_bool(args, "include_changes"):
        info["recent_changes"] = ctx.project.recent_changes(20)
    return info


def _schema_report(ctx: Context, refresh: bool) -> dict[str, Any]:
    observed: dict[str, dict[str, int]] = {}
    for key, entry in ctx.project.data_files.items():
        if entry.container == "scripts" or not ctx.project.exists(
                ctx.project.filename_for(key)):
            continue
        try:
            observe_class_fields(ctx.project.load_data(key, refresh), observed)
        except (ProjectError, ValueError):
            continue
    for map_id in ctx.project.map_ids_on_disk():
        try:
            observe_class_fields(ctx.project.load_map(map_id), observed)
        except (ProjectError, ValueError):
            continue

    documented_all = ctx.project.profile.class_fields
    classes = {}
    for cls in sorted(observed):
        documented = documented_all.get(cls, {})
        fields = {}
        for field in sorted(observed[cls]):
            fields[field] = documented.get(field, "")
        classes[cls] = {
            "fields": fields,
            "instances_seen": max(observed[cls].values()) if observed[cls] else 0,
            "undocumented_fields": [f for f in sorted(observed[cls])
                                    if f not in documented],
        }
    return {
        "classes": classes,
        "note": "Fields are those observed in this project's files. Descriptions "
                "come from the RGSS2 reference where one exists; a field with an "
                "empty description is real but undocumented here.",
        "tile_id_ranges": TILE_ID_RANGES,
        "map_layers": ctx.project.profile.map_layers,
        "tileset_flag_bits": (TILESET_FLAG_BITS
                              if "tilesets" in ctx.project.data_files else None),
    }


@tool(
    "validate",
    "Check the project for broken references",
    "Look for the mistakes that neither the editor nor a quick playtest shows: "
    "transfers to missing maps or off-map coordinates, event graphics and audio "
    "whose files are gone, encounters using troops that do not exist, event "
    "pages that a later page permanently hides, autorun pages that can never "
    "stop, and command lists that reference deleted database entries.",
    schema({
        "scopes": prop(ARR, "Which checks to run (default all): "
                            + ", ".join(VALIDATE_SCOPES),
                       items={"type": STR, "enum": VALIDATE_SCOPES}),
        "map_ids": prop(ARR, "Only check these maps.", items={"type": INT}),
        "limit": prop(INT, "Maximum findings to return (default 200).", minimum=1),
    }),
)
def validate(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    scopes = [s for s in opt_list(args, "scopes") if s in VALIDATE_SCOPES] or VALIDATE_SCOPES
    map_ids = [int(m) for m in opt_list(args, "map_ids")] or None
    return run_validate(ctx.project, scopes, map_ids, opt_int(args, "limit", 200) or 200)


@tool(
    "search",
    "Search the whole project",
    "Find text or command codes anywhere in the project: dialogue and comments "
    "in event command lists, database entry names and descriptions, map names, "
    "and the RGSS2 scripts. Every hit says exactly where it lives so you can go "
    "straight to it with event_get or db_get.",
    schema({
        "query": prop(STR, "Text to look for (case-insensitive substring, or a "
                           "regular expression when regex is true)."),
        "regex": prop(BOOL, "Treat query as a Python regular expression."),
        "command_code": prop(INT, "Find every event command with this code, "
                                  "instead of searching text."),
        "scopes": prop(ARR, "Where to look (default all): " + ", ".join(SEARCH_SCOPES),
                       items={"type": STR, "enum": SEARCH_SCOPES}),
        "limit": prop(INT, "Maximum hits to return (default 100).", minimum=1),
    }),
)
def search(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    scopes = [s for s in opt_list(args, "scopes") if s in SEARCH_SCOPES] or SEARCH_SCOPES
    limit = opt_int(args, "limit", 100) or 100
    code_filter = opt_int(args, "command_code")
    query = opt_str(args, "query")
    if code_filter is None and not query:
        raise ToolError("pass query (text to find) or command_code")

    matcher = None
    if query:
        if opt_bool(args, "regex"):
            try:
                pattern = re.compile(query, re.IGNORECASE)
            except re.error as exc:
                raise ToolError("invalid regular expression: %s" % exc)
            matcher = pattern.search
        else:
            needle = query.lower()
            matcher = lambda text: needle in text.lower()  # noqa: E731

    hits: list[dict[str, Any]] = []

    def add(location: Location, text: str, extra: dict[str, Any] | None = None) -> bool:
        row = {"where": location.describe(), "location": location.as_dict(),
               "text": text}
        if extra:
            row.update(extra)
        hits.append(row)
        return len(hits) >= limit

    if "events" in scopes:
        for location, commands in iter_command_lists(ctx.project):
            for index, command in enumerate(commands):
                if not isinstance(command, RubyObject):
                    continue
                code = command.get("code", 0)
                if code_filter is not None:
                    if code != code_filter:
                        continue
                    here = _at(location, index)
                    if add(here, summarize(command, ctx.codec),
                           {"code": code, "command": command_name(code)}):
                        return _result(hits, limit, scopes)
                    continue
                for text in _command_texts(ctx, command):
                    if matcher and matcher(text):
                        here = _at(location, index)
                        if add(here, text, {"code": code,
                                            "command": command_name(code)}):
                            return _result(hits, limit, scopes)

    if code_filter is None and "database" in scopes:
        for key, entry in ctx.project.data_files.items():
            if entry.container not in ("array", "hash"):
                continue
            try:
                rows = ctx.project.entries(key)
            except (ProjectError, ValueError):
                continue
            for entry_id, item in rows:
                if not isinstance(item, RubyObject):
                    continue
                for field, value in item.ivars.items():
                    if not isinstance(value, RubyString):
                        continue
                    text = ctx.codec.decode(value.data)
                    if text and matcher and matcher(text):
                        location = Location("database", table=key, entry_id=entry_id,
                                            field=field.lstrip("@"))
                        if add(location, text):
                            return _result(hits, limit, scopes)

    if code_filter is None and "maps" in scopes:
        try:
            for map_id, info in ctx.project.entries("map_infos"):
                if not isinstance(info, RubyObject):
                    continue
                name = info.get("name")
                text = ctx.codec.decode(name.data) if isinstance(name, RubyString) else ""
                if text and matcher and matcher(text):
                    if add(Location("map", map_id=map_id, field="name"), text):
                        return _result(hits, limit, scopes)
        except (ProjectError, ValueError):
            pass

    if code_filter is None and "scripts" in scopes:
        try:
            scripts_list = ctx.project.load_scripts()
        except (ProjectError, ValueError):
            scripts_list = []
        for script in scripts_list:
            code_text = script.get("code") or ""
            for line_number, line in enumerate(code_text.splitlines(), start=1):
                if matcher and matcher(line):
                    location = Location("script", entry_id=script["index"],
                                        index=line_number)
                    if add(location, line.strip(),
                           {"script_name": script.get("name")}):
                        return _result(hits, limit, scopes)

    return _result(hits, limit, scopes)


def _result(hits: list[dict[str, Any]], limit: int, scopes: list[str]) -> dict[str, Any]:
    return {"hits": hits, "count": len(hits), "truncated": len(hits) >= limit,
            "scopes": scopes}


def _at(location: Location, index: int) -> Location:
    return Location(location.kind, location.map_id, location.event_id,
                    location.event_name, location.page, index, location.entry_id,
                    location.table, location.field)


def _command_texts(ctx: Context, command: RubyObject) -> list[str]:
    out = []
    for value in command.get("parameters") or []:
        if isinstance(value, RubyString):
            out.append(ctx.codec.decode(value.data))
        elif isinstance(value, list):
            out.extend(ctx.codec.decode(v.data) for v in value
                       if isinstance(v, RubyString))
    return [t for t in out if t]


@tool(
    "scripts",
    "Read or replace RGSS2 scripts",
    "The contents of the script editor, stored zlib-compressed in "
    "Scripts.rvdata. Reading is safe; replacing a script changes game logic, so "
    "it is backed up first. Editing scripts while the RPG Maker editor is open "
    "will lose your changes.",
    schema({
        "op": prop(STR, "list (names and sizes), get (one script's code), or set.",
                   enum=["list", "get", "set"]),
        "index": prop(INT, "Which script slot, as reported by list."),
        "name": prop(STR, "For set: rename the slot."),
        "code": prop(STR, "For set: the new Ruby source for the slot."),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def scripts(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    op = one_of(args, "op", ["list", "get", "set"])
    entries = ctx.project.load_scripts()

    if op == "list":
        return {"scripts": [
            {"index": s["index"], "name": s.get("name", ""),
             "lines": len((s.get("code") or "").splitlines()),
             "characters": len(s.get("code") or ""),
             "error": s.get("error")}
            for s in entries
        ], "total": len(entries)}

    index = need_int(args, "index")
    if not 0 <= index < len(entries):
        raise ToolError("script index %d is outside 0..%d" % (index, len(entries) - 1))

    if op == "get":
        return entries[index]

    name, code = opt_str(args, "name"), opt_str(args, "code")
    if name is None and code is None:
        raise ToolError("set needs a name, code, or both")
    ctx.project.set_script(index, name, code)
    write = ctx.project.save_file(
        ctx.project.filename_for("scripts"), dry_run=opt_bool(args, "dry_run"),
        tool="scripts", summary="updated script %d" % index)
    return {"index": index, "name": name, "write": write}


@tool(
    "backups",
    "Manage backups of the project data",
    "Every write backs up the file it touches, once per server run, under "
    ".rpgvxace-mcp/backups. Use this to snapshot the whole Data folder before a "
    "big change, to see what snapshots exist, or to roll back. Restoring itself "
    "takes a safety snapshot first.",
    schema({
        "op": prop(STR, "list, create (snapshot every data file), restore, or prune.",
                   enum=["list", "create", "restore", "prune"]),
        "backup": prop(STR, "Which snapshot to restore, as named by list."),
        "files": prop(ARR, "For restore: only these files (default all in the "
                           "snapshot).", items={"type": STR}),
        "label": prop(STR, "For create: a short label added to the snapshot name."),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def backups(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    op = one_of(args, "op", ["list", "create", "restore", "prune"])
    if op == "list":
        return {"backups": ctx.project.list_backups(),
                "directory": str(ctx.project.backup_root()),
                "kept": ctx.project.backup_keep}
    if op == "create":
        label = re.sub(r"[^A-Za-z0-9._-]", "-", opt_str(args, "label") or "manual")
        return ctx.project.snapshot(label)
    if op == "prune":
        return {"removed": ctx.project.prune_backups(), "kept": ctx.project.backup_keep}
    return ctx.project.restore_backup(
        need_str(args, "backup"),
        [str(f) for f in opt_list(args, "files")] or None,
        opt_bool(args, "dry_run"),
    )


@tool(
    "raw_data",
    "Read or write any field by path",
    "An escape hatch for anything the other tools do not cover. Addresses a "
    "value by path, e.g. table \"system\" path \"party_members\", or map 1 path "
    '"events/1/pages/0/list/3/parameters/0". Values are the same JSON shapes '
    "db_get returns, so objects need a __class__ key. Use the purpose-built "
    "tools where they exist: they validate, and this does not.",
    schema({
        "op": prop(STR, "get (default) or set.", enum=["get", "set"]),
        "table": prop(STR, "A data table name, e.g. system, actors, map_infos.",
                      enum=ALL_TABLE_KEYS),
        "map_id": prop(INT, "A map to address instead of a table."),
        "path": prop(STR, "Slash-separated path from the top of the file. Empty "
                          "for the whole file."),
        "value": prop(OBJ, "For set: the new value, wrapped as {\"value\": ...} so "
                           "any JSON type can be passed."),
        "tables": prop(STR, "How to render RGSS Table fields.",
                       enum=["summary", "full"]),
        "dry_run": DRY_RUN,
        "refresh": REFRESH,
    }),
    writes=True,
)
def raw_data(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    op = one_of(args, "op", ["get", "set"], "get")
    table, map_id = opt_str(args, "table"), opt_int(args, "map_id")
    if (table is None) == (map_id is None):
        raise ToolError("pass exactly one of table or map_id")
    refresh = opt_bool(args, "refresh")

    if map_id is not None:
        filename = ctx.project.map_file(map_id)
        root = ctx.project.load_map(map_id, refresh)
    else:
        filename = ctx.project.filename_for(table)
        root = ctx.project.load_data(table, refresh)

    path = [p for p in (opt_str(args, "path") or "").split("/") if p]
    mode = one_of(args, "tables", ["summary", "full"], "summary")

    if op == "get":
        value = _resolve(root, path)
        return {"file": filename, "path": "/".join(path),
                "type": type(value).__name__,
                "value": to_json(value, ctx.codec, mode)}

    if not path:
        raise ToolError("set needs a path; replacing a whole file is not supported")
    if "value" not in args or not isinstance(args["value"], dict) \
            or "value" not in args["value"]:
        raise ToolError('set needs value as {"value": <the new value>}')

    parent = _resolve(root, path[:-1])
    key = path[-1]
    new_value = from_json(args["value"]["value"], ctx.codec)
    before = to_json(_resolve(root, path), ctx.codec, "summary")
    _assign(parent, key, new_value)
    write = ctx.project.save_file(filename, dry_run=opt_bool(args, "dry_run"),
                                  tool="raw_data",
                                  summary="set %s" % "/".join(path))
    return {"file": filename, "path": "/".join(path), "before": before,
            "after": to_json(new_value, ctx.codec, "summary"), "write": write}


def _resolve(value: Any, path: list[str]) -> Any:
    current = value
    for depth, key in enumerate(path):
        trail = "/".join(path[:depth]) or "the top of the file"
        if isinstance(current, RubyObject):
            if not current.has(key):
                raise ToolError("%s has no field %r (it has: %s)"
                                % (trail, key, ", ".join(current.fields())))
            current = current.get(key)
        elif isinstance(current, list):
            current = _index_list(current, key, trail)
        elif isinstance(current, RubyHash):
            lookup = int(key) if _is_int(key) else key
            if lookup not in current:
                raise ToolError("%s has no key %r (keys: %s)"
                                % (trail, key, current.keys()[:20]))
            current = current.get(lookup)
        elif isinstance(current, Table):
            raise ToolError("%s is an RGSS Table; use map_tiles to read or write "
                            "its cells" % trail)
        else:
            raise ToolError("cannot descend into %s at %s"
                            % (type(current).__name__, trail))
    return current


def _index_list(items: list[Any], key: str, trail: str) -> Any:
    if not _is_int(key):
        raise ToolError("%s is an array, so %r must be an index" % (trail, key))
    index = int(key)
    if not -len(items) <= index < len(items):
        raise ToolError("%s has %d items, so index %d is out of range"
                        % (trail, len(items), index))
    return items[index]


def _assign(parent: Any, key: str, value: Any) -> None:
    if isinstance(parent, RubyObject):
        if not parent.has(key):
            raise ToolError(
                "%s has no field %r. raw_data will not invent fields, because the "
                "editor rejects objects with unexpected instance variables."
                % (parent.cls, key)
            )
        parent.set(key, value)
    elif isinstance(parent, list):
        if not _is_int(key):
            raise ToolError("an array index must be a number, got %r" % key)
        index = int(key)
        if not -len(parent) <= index < len(parent):
            raise ToolError("index %d is out of range for %d items" % (index, len(parent)))
        parent[index] = value
    elif isinstance(parent, RubyHash):
        parent.set(int(key) if _is_int(key) else key, value)
    else:
        raise ToolError("cannot assign into a %s" % type(parent).__name__)


def _is_int(text: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", text))
