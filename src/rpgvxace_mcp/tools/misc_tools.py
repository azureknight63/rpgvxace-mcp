"""Project-wide tools: what is open, whether it is sound, scripts, backups.

`search` and `raw_data` were here too, and moved to search_tools.py and
raw_tools.py: at six unrelated tools this file had no single subject a reader
could hold, and nothing in it was shared between them.
"""

from __future__ import annotations

import re
from typing import Any

from ..analysis import (
    DEFAULT_LIMIT,
    MAX_FINDINGS,
    VALIDATE_SCOPES,
    validate as run_validate,
)
from ..project import MAX_MAP_ID, ProjectError
from ..schema import (
    ARRAY,
    HASH,
    SCRIPTS,
    SchemaWalk,
    TILESET_FLAG_BITS,
    TILESET_FLAG_BITS_NOTE,
    TILE_ID_RANGES,
    TILE_ID_RANGES_NOTE,
)
from .registry import (
    check_op_args,
    str_list,
    int_list,
    ARR,
    BOOL,
    Context,
    DRY_RUN,
    INT,
    REFRESH,
    STR,
    ToolError,
    need_int,
    need_str,
    one_of,
    opt_bool,
    opt_int,
    opt_str,
    prop,
    schema,
    scopes_arg,
    tool,
)


# How many files one restore may name explicitly.
MAX_RESTORE_FILES = 200


# What each backups op reads. "create" snapshots the whole Data folder by
# definition, so it has no `files`; that belongs to "restore", which used to
# accept it on a create and snapshot everything anyway.
BACKUP_OP_ARGS = {
    "list": {"op"},
    "create": {"op", "label", "dry_run"},
    "restore": {"op", "backup", "files", "dry_run"},
    "prune": {"op", "dry_run"},
}

# What each raw_data op reads. `op` defaults to "get", which is the dangerous
# half: a caller that meant to write and forgot the op got back the value
# already there, under a "value" key that reads like confirmation of the write
# it thought it had made. So "get" refuses `value` outright.
# What each scripts op reads. A slot body is only ever written by "set", and
# "get" used to accept a `code` and hand back the body already there.
SCRIPT_OP_ARGS = {
    "list": {"op"},
    "get": {"op", "index"},
    "set": {"op", "index", "name", "code", "dry_run"},
}


# A script body this server will write. Well under the whole-file inflate
# budget, so a project stays readable by the tool that wrote it.
MAX_SCRIPT_CHARS = 2 * 1024 * 1024
MAX_SCRIPT_NAME_CHARS = 200


# How many maps one validate call will look at. A caller asking for more
# than a project could hold is asking by mistake.
MAX_MAP_IDS = 4096


@tool(
    "project_info",
    "Describe the open project",
    "What this server is working on: the project root, the engine it detected, "
    "the text encoding it will read and write, which data files exist, and how "
    "many entries each table holds. Call this first. Pass include_schema to "
    "see the fields each Ruby class actually carries in this project. A patch "
    "naming a field that is neither in this report nor documented for the "
    "class is refused rather than adding one -- a db_edit patch may name "
    "exactly the observed fields, while a map, event page or common event also "
    "accepts a field documented for its class that the entry happens not to "
    "carry yet. So this report is the floor, not the whole list.",
    schema({
        "include_schema": prop(BOOL, "Report the instance variables observed on "
                                     "each RPG:: class in this project."),
        "include_changes": prop(BOOL, "Include the recent change log for this project."),
        "refresh": REFRESH,
    }),
)
def project_info(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """What this server has open, and what is in it. Reads only."""
    refresh = opt_bool(args, "refresh")
    info = ctx.project.describe()

    counts, warnings = {}, {}
    for key, entry in ctx.project.data_files.items():
        if not ctx.project.exists(ctx.project.filename_for(key)):
            continue
        try:
            if entry.container in (ARRAY, HASH):
                counts[key] = len(ctx.project.entries(key, refresh))
            elif entry.container == SCRIPTS:
                # Only the slot count is wanted here, so skip inflating every
                # script body.
                counts[key] = len(ctx.project.load_scripts(with_code=False))
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
    info["maps"] = ctx.project.map_ids_on_disk(refresh=refresh)
    if opt_bool(args, "include_schema"):
        info["schema"] = _schema_report(ctx, refresh)
    if opt_bool(args, "include_changes"):
        info["recent_changes"] = ctx.project.recent_changes(20)
    return info


def _schema_report(ctx: Context, refresh: bool) -> dict[str, Any]:
    # One walk for the whole project, so its work budget is a project budget.
    walk = SchemaWalk()
    for key, entry in ctx.project.data_files.items():
        if entry.container == SCRIPTS or not ctx.project.exists(
                ctx.project.filename_for(key)):
            continue
        try:
            walk.visit(ctx.project.load_data(key, refresh))
        except (ProjectError, ValueError):
            continue
    for map_id in ctx.project.map_ids_on_disk():
        try:
            walk.visit(ctx.project.load_map(map_id, refresh))
        except (ProjectError, ValueError):
            continue
    observed = walk.fields

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
            # Real fields that simply have no description here. Named this
            # way round because the reference tables cover the map and event
            # classes and no database class, so for an Item or an Actor this
            # is every field -- which says nothing about whether a patch may
            # set them.
            "fields_without_a_description": [f for f in sorted(observed[cls])
                                             if f not in documented],
        }
    report: dict[str, Any] = {
        "classes": classes,
        "note": "Fields are those observed in this project's files. A db_edit "
                "patch may name exactly these; a map, event page or common "
                "event patch may also name a field documented for its class "
                "but not yet present on the entry. Descriptions come "
                "from the RGSS reference for %s where one exists, which covers "
                "the map and event classes; a database class has no reference "
                "table here, so its fields are listed with empty descriptions "
                "and are no less real for it." % ctx.project.profile.label,
        "tile_id_ranges": TILE_ID_RANGES,
        "tile_id_ranges_note": TILE_ID_RANGES_NOTE,
        "map_layers": ctx.project.profile.map_layers,
    }
    if ctx.project.profile.has_tileset_database:
        report["tileset_flag_bits"] = TILESET_FLAG_BITS
        report["tileset_flag_bits_note"] = TILESET_FLAG_BITS_NOTE
    if walk.truncated:
        # Say so rather than present a partial tally as the whole picture.
        report["incomplete"] = (
            "the walk stopped after %d values, so this list of fields may be "
            "missing some. That normally means a data file contains an unusual "
            "amount of shared structure." % walk.nodes_visited
        )
    return report


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
        "map_ids": prop(ARR, "Only check these maps. This narrows the "
                             "map-based checks only: common events and "
                             "troop pages belong to the project rather "
                             "than to a map, so they are always "
                             "included. At most %d." % MAX_MAP_IDS,
                        items={"type": INT}, maxItems=MAX_MAP_IDS),
        "limit": prop(INT, "Maximum findings to return (default %d)."
                      % DEFAULT_LIMIT, minimum=1, maximum=MAX_FINDINGS),
    }),
)
def validate(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Run the cross-reference checks. Reads only."""
    scopes = scopes_arg(args, "scopes", VALIDATE_SCOPES)
    # int_list, so a map id here is read exactly as it is everywhere else:
    # the local reader this replaced refused the integral 2.0 that every other
    # tool accepts, so sibling tools answered the same JSON differently.
    map_ids = int_list(args, "map_ids", MAX_MAP_IDS,
                       minimum=1, maximum=MAX_MAP_ID) or None
    return run_validate(ctx.project, scopes, map_ids,
                        opt_int(args, "limit", DEFAULT_LIMIT) or DEFAULT_LIMIT)


@tool(
    "scripts",
    "Read or replace Ruby scripts",
    "The contents of the script editor, stored zlib-compressed in "
    "the project's Scripts file. Reading is safe; replacing a script changes "
    "game logic, so "
    "it is backed up first. Editing scripts while the RPG Maker editor is open "
    "will lose your changes.",
    schema({
        "op": prop(STR, "list (names and sizes), get (one script's code), or set.",
                   enum=list(SCRIPT_OP_ARGS)),
        "index": prop(INT, "Which script slot, as reported by list."),
        "name": prop(STR, "For set: rename the slot."),
        "code": prop(STR, "For set: the new Ruby source for the slot, up "
                          "to %d characters." % MAX_SCRIPT_CHARS),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def scripts(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """List, read or replace a slot in the script editor.

    A body is bounded well below the whole-file inflate budget, so a
    project stays readable by the tool that wrote it. Staged.
    """
    op = one_of(args, "op", list(SCRIPT_OP_ARGS))
    check_op_args(op, args, SCRIPT_OP_ARGS, "scripts")
    if op == "set":
        code = opt_str(args, "code")
        if code is not None and len(code) > MAX_SCRIPT_CHARS:
            raise ToolError(
                "code is %d characters, above the %d this server writes. A "
                "script that large would also push the file past what it can "
                "read back." % (len(code), MAX_SCRIPT_CHARS))
        name = opt_str(args, "name")
        if name is not None and len(name) > MAX_SCRIPT_NAME_CHARS:
            raise ToolError("name is %d characters; the editor shows at most "
                            "about %d" % (len(name), MAX_SCRIPT_NAME_CHARS))
    # `list` reports line and character counts, so it needs every body; `get`
    # needs one; `set` needs only the slot count. Inflating a project's whole
    # script pack for a `set` is the common case here.
    index = need_int(args, "index") if op in ("get", "set") else None
    entries = ctx.project.load_scripts(
        with_code=op != "set",
        indices={index} if op == "get" and index is not None else None)

    if op == "list":
        return {"scripts": [
            {"index": s["index"], "name": s.get("name", ""),
             "lines": len((s.get("code") or "").splitlines()),
             "characters": len(s.get("code") or ""),
             "error": s.get("error")}
            for s in entries
        ], "total": len(entries)}

    if index is None:  # never: the branch above sets it for get and set
        raise ToolError("op %s needs an index" % op)
    if not 0 <= index < len(entries):
        raise ToolError("script index %d is outside 0..%d" % (index, len(entries) - 1))

    if op == "get":
        return entries[index]

    if name is None and code is None:
        raise ToolError("set needs a name, code, or both")
    dry_run = opt_bool(args, "dry_run")
    filename = ctx.project.filename_for("scripts")
    with ctx.staged(filename) as write_landed:
        ctx.project.set_script(index, name, code)
        write = write_landed.save(
            filename, dry_run=dry_run, tool="scripts",
            summary="updated script %d" % index)
    out: dict[str, Any] = {"index": index, "write": write}
    # Only what was actually given. Reporting `name: null` for a code-only set
    # reads as "the slot's name was cleared", which is the opposite of what
    # happened.
    if name is not None:
        out["renamed_to"] = name
    if code is not None:
        out["code_characters"] = len(code)
    return out


@tool(
    "backups",
    "Manage backups of the project data",
    "Every write backs up the file it touches, once per server run, under "
    ".rpgvxace-mcp/backups. Use this to snapshot the whole Data folder before a "
    "big change, to see what snapshots exist, or to roll back. Restoring itself "
    "takes a safety snapshot first. Close the RPG Maker editor before "
    "restoring: a restore copies whole files back over Data/ without the "
    "staleness check the editing tools make, so anything the editor is "
    "holding unsaved is overwritten -- recoverable only from that safety "
    "snapshot.",
    schema({
        "op": prop(STR, "list, create (snapshot every data file), restore, or prune.",
                   enum=["list", "create", "restore", "prune"]),
        "backup": prop(STR, "Which snapshot to restore, as named by list."),
        "files": prop(ARR, "For restore: only these files (default all in "
                           "the snapshot). At most %d." % MAX_RESTORE_FILES,
                      items={"type": STR}, maxItems=MAX_RESTORE_FILES),
        "label": prop(STR, "For create: a short label added to the snapshot name."),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def backups(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """List, take, restore or prune snapshots of the Data folder."""
    op = one_of(args, "op", list(BACKUP_OP_ARGS))
    check_op_args(op, args, BACKUP_OP_ARGS, "backups")
    if op == "list":
        return {"backups": ctx.project.list_backups(),
                "directory": str(ctx.project.backup_root()),
                "kept": ctx.project.backup_keep}
    dry_run = opt_bool(args, "dry_run")
    if op == "create":
        label = re.sub(r"[^A-Za-z0-9._-]", "-", opt_str(args, "label") or "manual")
        # Asked of the function that does the copying, like the prune
        # branch below: globbing here listed the symlinked data files
        # `snapshot` skips, so the preview named files no backup would hold.
        return ctx.project.snapshot(label, dry_run=dry_run)
    if op == "prune":
        if dry_run:
            # Asked of prune_backups itself: building the candidate list here
            # got both the key name and the rules wrong -- it claimed it would
            # delete this run's own snapshot and any folder a person had put
            # under backups/.
            return {"would_remove": ctx.project.prune_backups(dry_run=True),
                    "kept": ctx.project.backup_keep, "dry_run": True}
        return {"removed": ctx.project.prune_backups(),
                "kept": ctx.project.backup_keep}
    return ctx.project.restore_backup(
        need_str(args, "backup"),
        str_list(args, "files", MAX_RESTORE_FILES) or None,
        dry_run,
    )


