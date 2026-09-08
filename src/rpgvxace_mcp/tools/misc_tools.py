"""Project-wide tools: info, validation, search, scripts, backups, raw access."""

from __future__ import annotations

import re
from typing import Any

from ..analysis import (
    DEFAULT_LIMIT,
    Location,
    MAX_FINDINGS,
    VALIDATE_SCOPES,
    iter_command_lists,
    validate as run_validate,
)
from ..commands import command_name, summarize
from ..convert import TextCodec, _hash_key, from_json, to_json
from ..project import MAX_MAP_ID, ProjectError
from ..rubytypes import (
    RubyHash,
    RubyIvars,
    RubyObject,
    RubyString,
    Table,
    _bare_name,
)
from ..schema import (
    ALL_TABLE_KEYS,
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
    str_list,
    int_list,
    echo,
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

# One search's ceiling on hits, on how much of a hit's text comes back, and on
# the size of a caller-supplied pattern.
MAX_HITS = 1000

# How many files one restore may name explicitly.
MAX_RESTORE_FILES = 200

# How many database rows one listing returns. Paging is what `offset`
# is for; a listing larger than this is not one a caller reads.
MAX_ROWS = 500
MAX_HIT_CHARS = 300
MAX_PATTERN_CHARS = 512

# How much of one candidate string a pattern is matched against. A minified
# script line can be tens of thousands of characters, and match cost grows with
# the subject; a hit past this point is not one a person was looking for.
MAX_SUBJECT_CHARS = 2000


SEARCH_SCOPES = ["events", "database", "maps", "scripts"]

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
    "many entries each table holds. Call this first. Pass include_schema to see "
    "the fields each Ruby class actually carries in this project, which is "
    "what db_edit and map_edit patches are checked against: a patch naming a "
    "field the entry does not have is refused rather than adding one.",
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
        "note": "Fields are those observed in this project's files, and are "
                "what a db_edit or map_edit patch may name. Descriptions come "
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
    scopes = _scopes_arg(args, "scopes", VALIDATE_SCOPES)
    # int_list, so a map id here is read exactly as it is everywhere else:
    # the local reader this replaced refused the integral 2.0 that every other
    # tool accepts, so sibling tools answered the same JSON differently.
    map_ids = int_list(args, "map_ids", MAX_MAP_IDS,
                       minimum=1, maximum=MAX_MAP_ID) or None
    return run_validate(ctx.project, scopes, map_ids,
                        opt_int(args, "limit", DEFAULT_LIMIT) or DEFAULT_LIMIT)


@tool(
    "search",
    "Search the whole project",
    "Find text or command codes anywhere in the project: dialogue and comments "
    "in event command lists, database entry names and descriptions, map names, "
    "and the Ruby scripts. Every hit says exactly where it lives so you can go "
    "straight to it with event_get or db_get.",
    schema({
        "query": prop(STR, "Text to look for (case-insensitive substring, or a "
                           "regular expression when regex is true)."),
        "regex": prop(BOOL, "Treat query as a Python regular expression."),
        "command_code": prop(INT, "Find every event command with this code, "
                                  "instead of searching text."),
        "scopes": prop(ARR, "Where to look (default all): " + ", ".join(SEARCH_SCOPES),
                       items={"type": STR, "enum": SEARCH_SCOPES}),
        "limit": prop(INT, "Maximum hits to return (default 100, at most "
                           "%d)." % MAX_HITS, minimum=1, maximum=MAX_HITS),
    }),
)
def search(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Find text or a command code across the project.

    Reports which scopes it actually read, because an empty result means
    nothing without knowing where the search went.
    """
    scopes = _scopes_arg(args, "scopes", SEARCH_SCOPES)
    limit = opt_int(args, "limit", 100, minimum=1, maximum=MAX_HITS) or 100
    code_filter = opt_int(args, "command_code")
    query = opt_str(args, "query")
    if code_filter is None and not query:
        raise ToolError("pass query (text to find) or command_code")
    if code_filter is not None:
        if query:
            raise ToolError(
                "pass query or command_code, not both: a command_code search "
                "matches on the code and ignores text.")
        if "events" not in scopes:
            raise ToolError(
                "a command_code search only has anything to look at in the "
                "events scope, because only event command lists hold commands. "
                "Include \"events\" in scopes.")

    matcher = _matcher(args, query)

    # A command_code search only has anything to look at in event command
    # lists, so the other scopes are skipped. Recorded as it goes, because
    # reporting the scopes that were *asked for* told the model those had been
    # searched and come back empty.
    searched: list[str] = []
    # A scope whose file would not load was not searched either, and saying it
    # came back empty is the same lie as reporting a skipped scope: recorded
    # separately so the caller can tell "nothing there" from "could not look".
    unsearchable: dict[str, str] = {}
    hits: list[dict[str, Any]] = []

    def add(location: Location, text: str, extra: dict[str, Any] | None = None) -> bool:
        # Capped like a finding's text: one minified script line or crafted
        # note would otherwise be reflected whole into the response.
        if len(text) > MAX_HIT_CHARS:
            text = text[:MAX_HIT_CHARS] + "... (%d characters)" % len(text)
        row = {"where": location.describe(), "location": location.as_dict(),
               "text": text}
        if extra:
            row.update(extra)
        hits.append(row)
        return len(hits) >= limit

    if "events" in scopes:
        searched.append("events")
        for location, commands in iter_command_lists(ctx.project):
            for index, command in enumerate(commands):
                if not isinstance(command, RubyObject):
                    continue
                code = command.get("code", 0)
                if code_filter is not None:
                    if code != code_filter:
                        continue
                    here = location.at(index)
                    if add(here, summarize(command, ctx.codec),
                           {"code": code, "command": command_name(code)}):
                        return _result(hits, limit, searched, None, unsearchable)
                    continue
                for text in _command_texts(ctx, command):
                    if matcher and matcher(text):
                        here = location.at(index)
                        if add(here, text, {"code": code,
                                            "command": command_name(code)}):
                            return _result(hits, limit, searched, None, unsearchable)

    if code_filter is None and "database" in scopes:
        for key, entry in ctx.project.data_files.items():
            if entry.container not in (ARRAY, HASH):
                continue
            try:
                rows = ctx.project.entries(key)
            except (ProjectError, ValueError) as exc:
                unsearchable[key] = str(exc)
                continue
            if "database" not in searched:
                # On the first table that reads, so an early return inside the
                # walk keeps it and a table that will not load does not claim
                # it. Per-table failures are reported in `unsearchable`.
                searched.append("database")
            for entry_id, item in rows:
                if not isinstance(item, RubyObject):
                    continue
                for field, value in item.ivars.items():
                    if not isinstance(value, RubyString):
                        continue
                    text = ctx.codec.decode(value.data)
                    if text and matcher and matcher(text):
                        location = Location("database", table=key, entry_id=entry_id,
                                            field=_bare_name(field))
                        if add(location, text):
                            return _result(hits, limit, searched, None, unsearchable)

    if code_filter is None and "maps" in scopes:
        try:
            rows = ctx.project.entries("map_infos")
            # Recorded once the read succeeds and before the walk: after the
            # walk, an early return on reaching the limit lost the scope the
            # hits came from; before the read, a file that would not load
            # claimed to have been searched.
            searched.append("maps")
            for map_id, info in rows:
                if not isinstance(info, RubyObject):
                    continue
                name = info.get("name")
                text = ctx.codec.decode(name.data) if isinstance(name, RubyString) else ""
                if text and matcher and matcher(text):
                    if add(Location("map", map_id=map_id, field="name"), text):
                        return _result(hits, limit, searched, None, unsearchable)
        except (ProjectError, ValueError) as exc:
            unsearchable["map_infos"] = str(exc)

    unreadable: list[int] = []
    if code_filter is None and "scripts" in scopes:
        try:
            scripts_list = ctx.project.load_scripts()
            searched.append("scripts")
        except (ProjectError, ValueError) as exc:
            unsearchable["scripts"] = str(exc)
            scripts_list = []
        for script in scripts_list:
            if "code" not in script:
                # A slot whose body would not inflate was not searched, and
                # saying so beats reporting no hits in it.
                unreadable.append(script["index"])
                continue
            for line_number, line in enumerate(script["code"].splitlines(),
                                               start=1):
                if matcher and matcher(line):
                    if add(_script_at(script, line_number), line.strip(),
                           {"script_name": script.get("name")}):
                        return _result(hits, limit, searched, unreadable, unsearchable)

    return _result(hits, limit, searched, unreadable, unsearchable)


def _result(hits: list[dict[str, Any]], limit: int, searched: list[str],
            unreadable: list[int] | None = None,
            unsearchable: dict[str, str] | None = None) -> dict[str, Any]:
    """One search's answer, saying what it looked at as well as what it found.

    An empty result means nothing without knowing where the search went: a
    command_code search skips the text scopes, and a file that would not load
    was never read. Both are reported rather than folded into "no hits".
    """
    out: dict[str, Any] = {
        "hits": hits, "count": len(hits), "truncated": len(hits) >= limit,
        "scopes_searched": searched,
    }
    if unreadable:
        out["scripts_not_searched"] = unreadable
    if unsearchable:
        out["could_not_read"] = unsearchable
    return out


def _script_at(script: dict[str, Any], line: int) -> Location:
    """Where a script hit is, named the way the scripts tool takes it.

    A script slot is not an event command list: the fields are a slot index
    and a line number, so they are named that rather than reusing `entry_id`
    and `index`, which mean something else everywhere else.
    """
    location = Location("script", entry_id=script["index"], index=line)
    return location


# The quantifiers that can repeat a group an unbounded number of times.
_QUANTIFIERS = "+*{"


def _refuse_repeated_group(pattern: str) -> None:
    """Refuse any quantifier applied to a group.

    Python's ``re`` backtracks exponentially on a repeated group whose body can
    match the same text more than one way -- ``(a+)+``, ``((a+))+``,
    ``(?:a+)+``, ``(a|a)+``, ``(a{1,2})+`` -- and a match in progress cannot be
    interrupted, so one search wedges this single-threaded server for good.

    Deciding which repeated groups are actually safe means analysing the body.
    The earlier attempt here was a flat regex matching only the simplest
    spelling, and every form above walked straight past it -- while its own
    docstring claimed the subject cap covered the rest, which was not true:
    2**2000 is not a bound. So this refuses the whole shape, scanning
    parentheses properly and skipping escapes and character classes.

    It is blunt: ``(abc)+`` is perfectly safe and is refused too. That is the
    trade worth making, because a refusal costs one rephrased search while the
    failure it prevents cannot be recovered from at all.
    """
    depth = 0
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if char == "[":
            # Inside a character class, brackets and parens are literal.
            index += 1
            while index < len(pattern) and pattern[index] != "]":
                index += 2 if pattern[index] == "\\" else 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
            after = index + 1
            if after < len(pattern) and pattern[after] in _QUANTIFIERS:
                raise ToolError(
                    "that pattern repeats a group -- the %r after a closing "
                    "bracket. A repeated group can take exponential time to "
                    "match and cannot be interrupted, so this server refuses "
                    "the shape rather than try to judge which cases are safe. "
                    "Repeat a single character or a character class instead "
                    "(a+ or [abc]+), or search for a plain substring."
                    % pattern[after])
        index += 1



def _matcher(args: dict[str, Any], query: str | None):
    """The text test one search uses, compiled once.

    A caller-supplied pattern is bounded in length and rejected if it nests
    too deeply to compile: `re` runs uninterruptibly, so a pattern that
    backtracks badly would wedge this single-threaded server, and the pattern
    can be suggested by project text the model has just read.
    """
    if not query:
        return None
    if not opt_bool(args, "regex"):
        needle = query.lower()
        return lambda text: needle in text.lower()
    if len(query) > MAX_PATTERN_CHARS:
        raise ToolError(
            "the pattern is %d characters, above the %d this server compiles. "
            "Search for a plain substring instead, or narrow the pattern."
            % (len(query), MAX_PATTERN_CHARS))
    _refuse_repeated_group(query)
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error as exc:
        raise ToolError("invalid regular expression: %s" % exc)
    except RecursionError:
        raise ToolError("that pattern nests too deeply to compile")

    # Matched against a bounded prefix of each candidate. This bounds the
    # input to one match; what bounds the *shape* is the refusal above. Neither
    # alone is enough, and the cap is not a substitute for it: exponential
    # backtracking on a 2000-character subject is still unbounded in practice.
    def search(text: str):
        return pattern.search(text[:MAX_SUBJECT_CHARS])

    return search


def _scopes_arg(args: dict[str, Any], key: str,
                allowed: "tuple[str, ...] | list[str]") -> list[str]:
    """The scopes a caller asked for, refusing names that do nothing.

    Dropping an unrecognised name and running everything instead is the
    opposite of what the rest of these tools do with a bad argument: the
    caller believes it narrowed the search and is handed the whole project.
    """
    asked = opt_list(args, key)
    unknown = [s for s in asked if s not in allowed]
    if unknown:
        raise ToolError("unknown %s %s; choose from %s"
                        % (key, ", ".join(echo(u) for u in unknown),
                           ", ".join(allowed)))
    return [s for s in asked if s in allowed] or list(allowed)





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
    "Read or replace Ruby scripts",
    "The contents of the script editor, stored zlib-compressed in "
    "Scripts.rvdata. Reading is safe; replacing a script changes game logic, so "
    "it is backed up first. Editing scripts while the RPG Maker editor is open "
    "will lose your changes.",
    schema({
        "op": prop(STR, "list (names and sizes), get (one script's code), or set.",
                   enum=["list", "get", "set"]),
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
    op = one_of(args, "op", ["list", "get", "set"])
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
    op = one_of(args, "op", ["list", "create", "restore", "prune"])
    if op == "list":
        return {"backups": ctx.project.list_backups(),
                "directory": str(ctx.project.backup_root()),
                "kept": ctx.project.backup_keep}
    dry_run = opt_bool(args, "dry_run")
    if op == "create":
        label = re.sub(r"[^A-Za-z0-9._-]", "-", opt_str(args, "label") or "manual")
        if dry_run:
            return {"would_snapshot": sorted(
                p.name for p in ctx.project.data_dir.glob(
                    "*" + ctx.project.extension)),
                "label": label, "dry_run": True}
        return ctx.project.snapshot(label)
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


@tool(
    "raw_data",
    "Read or write any field by path",
    "An escape hatch for anything the other tools do not cover. Addresses a "
    "value by path, e.g. table \"system\" path \"party_members\", or map 1 path "
    '"events/1/pages/0/list/3/parameters/0". Values are the same JSON shapes '
    "db_get returns, so objects need a __class__ key. Use the purpose-built "
    "tools where they exist: they validate, and this does not. It still "
    "refuses four things, because each produces a file the editor cannot open "
    "rather than an unusual value: the scripts table (a slot body is "
    "compressed Ruby -- use the scripts tool); a map's data, width, height or "
    "events at any depth (use map_edit resize, map_tiles, event_edit); "
    "swapping a structure for a scalar or the reverse; and a field the object "
    "does not already have.",
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
    """Read or write any field by path: the unvalidated escape hatch.

    Deliberately permissive about values, and deliberately not about
    shapes: it refuses replacing an object, hash, array or Table with a
    value of another kind, and refuses a map's data, width, height or
    events wholesale, because those produce a file the editor cannot
    open. Staged like every other write.
    """
    op = one_of(args, "op", ["get", "set"], "get")
    table = opt_str(args, "table")
    map_id = opt_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
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
        value = _resolve(root, path, ctx.codec)
        return {"file": filename, "path": "/".join(path),
                "type": type(value).__name__,
                "value": to_json(value, ctx.codec, mode)}

    if not path:
        raise ToolError("set needs a path; replacing a whole file is not supported")
    if "value" not in args or not isinstance(args["value"], dict) \
            or "value" not in args["value"]:
        raise ToolError('set needs value as {"value": <the new value>}')

    if table == "scripts":
        raise ToolError(
            "the scripts table cannot be written through raw_data: a slot body "
            "is zlib-compressed Ruby, and writing it as anything else stops "
            "the game booting. Use the scripts tool, which compresses it.")
    if map_id is not None:
        _check_structural(path)
    parent = _resolve(root, path[:-1], ctx.codec)
    key = path[-1]
    new_value = from_json(args["value"]["value"], ctx.codec)
    existing = _resolve(root, path, ctx.codec)
    _check_same_kind(existing, new_value, "/".join(path))
    before = to_json(existing, ctx.codec, "summary")

    dry_run = opt_bool(args, "dry_run")
    with ctx.staged(filename) as write_landed:
        _assign(parent, key, new_value, ctx.codec)
        write = write_landed.save(filename, dry_run=dry_run,
                                  tool="raw_data",
                                  summary="set %s" % "/".join(path))
    return {"file": filename, "path": "/".join(path), "before": before,
            "after": to_json(new_value, ctx.codec, "summary"), "write": write}


# Map fields whose value has to agree with the tile table or with the map's own
# structure. raw_data is the unvalidated escape hatch, but writing one of these
# on its own produces a file the editor refuses to open, which is not reach --
# it is just breakage.
STRUCTURAL_MAP_PATHS = ("data", "width", "height", "events")


def _check_structural(path: list[str]) -> None:
    """Refuse any path into a map field the rest of the map has to agree with.

    At any depth, not just the first segment: replacing an event's page list
    through "events/1/pages" produces the same map the interpreter cannot run
    as replacing "events" wholesale. Bare names, because `_resolve` accepts
    either spelling of an ivar.
    """
    if path and _bare_name(path[0]) in STRUCTURAL_MAP_PATHS:
        raise ToolError(
            "%s cannot be replaced through raw_data, because the rest of the "
            "map has to agree with it. Use map_edit op=\"resize\" for the "
            "size, map_tiles for tiles, and event_edit for events."
            % _bare_name(path[0]))


def _check_same_kind(existing: Any, replacement: Any, path: str) -> None:
    """Refuse swapping a structure for something that is not one.

    ``apply_patch`` refuses this and raw_data did not, so an Integer could take
    the place of an event Hash or a tile Table -- a file neither the editor nor
    the game can open. Anything else raw_data still allows: this is the one
    guard it keeps, because being able to write an odd *value* is the point,
    and being able to write an odd *shape* is not.
    """
    kinds = ((RubyObject, "an object"), (RubyHash, "a hash"),
             (Table, "an RGSS Table"), (list, "an array"))
    for kind, described in kinds:
        # Both directions: putting a structure where a scalar was produces the
        # same file the editor will not open as the reverse.
        if isinstance(existing, kind) != isinstance(replacement, kind):
            held, given = ((described, type(replacement).__name__)
                           if isinstance(existing, kind)
                           else (type(existing).__name__, described))
            raise ToolError(
                "%s currently holds %s, and replacing it with %s would leave a "
                "file the editor cannot open. Set a field inside it instead, or "
                "use the tool that owns this structure." % (path, held, given))


def _resolve(value: Any, path: list[str], codec: TextCodec) -> Any:
    """Walk a path into a loaded file.

    The codec is needed because a Ruby Hash can be keyed by RubyStrings as well
    as by integers, and looking such a key up as a bare Python string finds
    nothing while reporting that the key does not exist.
    """
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
            lookup = _hash_key(current, key, codec)
            if lookup not in current:
                raise ToolError("%s has no key %r (keys: %s)"
                                % (trail, key, current.keys()[:20]))
            current = current.get(lookup)
        elif isinstance(current, RubyIvars):
            # A value the codec wrapped to keep an instance-variable record it
            # could not store inline. Unwrap and retry the SAME key: the
            # wrapper is not a level of the path.
            current = current.inner
            return _resolve(current, path[depth:], codec)
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


def _assign(parent: Any, key: str, value: Any,
            codec: TextCodec) -> None:
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
        # The same resolution the read side uses. Keying differently here meant
        # a set on a RubyString-keyed hash appended a new bare-str key beside
        # the pair that had just been read, so the reported before/after was a
        # lie and the write then failed in the codec.
        parent.set(_hash_key(parent, key, codec), value)
    else:
        raise ToolError("cannot assign into a %s" % type(parent).__name__)


def _is_int(text: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", text))
