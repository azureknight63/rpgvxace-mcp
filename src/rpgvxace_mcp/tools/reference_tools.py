"""`references` and `refactor`: where something is used, and moving it safely.

Two tools on one scan (`analysis.ref_sites`). `references` is the question
"where is item 12, skill 5, switch 40, map 4 used?" and `refactor` is its
consequence: renumber an entry, or name a switch, and every place that
refers to it follows. They share a scan on purpose: what `references`
reports is exactly what `refactor` would rewrite, so a preview is the
answer to a question the caller has already seen.

`refactor` fails closed. A table is renumberable only when its reference
sites have been reviewed and declared complete
(`analysis.RENUMBER_COMPLETE`); for any other table it refuses and names the
places that would be missed. It is atomic over files: every file is checked
for staleness before the first is written, and when a later write fails the
earlier ones are put back byte for byte.
"""

from __future__ import annotations

from typing import Any

from ..analysis import REFERENCE_RULES, WalkBudget
from ..project import ProjectError
from ..rubytypes import RubyHash, RubyObject
from ..schema import ALL_DATABASE_TABLES, ARRAY, MAX_MAP_ID
from .registry import (
    BOOL,
    Context,
    DRY_RUN,
    INT,
    STR,
    ToolError,
    check_op_args,
    decoded,
    echo,
    need_int,
    need_str,
    one_of,
    opt_bool,
    opt_int_default,
    partial,
    prop,
    schema,
    tool,
    write_reply,
)


# What `references` can be asked about. The tables the command rule index
# keys, plus the ones that index lacks (a skill is named by five commands it
# does not list), plus the two numbered namespaces that are not tables.
# `tests/test_references_tools.py` holds this equal to the analysis' own list,
# which is not imported here: it pulls in `switch_usage`, and the server's
# start-up must not.
REFERENCE_KIND_NAMES = tuple(sorted(
    set(REFERENCE_RULES)
    | {"classes", "enemies", "skills", "states", "switches", "tilesets",
       "variables"}))

# Sites listed in one reply. The total is always given beside them.
MAX_SITES = 200
DEFAULT_SITES = 50
# How far into the sites `offset` may reach: a reply is a window, and a
# project with more than this many references to one thing has a different
# problem than paging.
MAX_OFFSET = 100_000

# Switches and variables are numbered 1 to this: VX and VX Ace ignore a write
# above it, so nothing above it exists to be named.
MAX_SWITCH_ID = 5000
# The highest database id the tools address (db_edit's own ceiling).
MAX_REFERENCE_ID = 9999
# How far past the end of a table a renumber may reach, so one call cannot
# pad a table with a thousand empty slots. db_edit's create allows the same.
ARRAY_GROWTH_HEADROOM = 1000
# The longest switch or variable name this will write.
MAX_NAME_CHARS = 100

# The tables `refactor renumber` can be pointed at. Everything the database
# tools address, plus maps; a table outside the declared-complete list is
# refused at run time with the reason, which is more use than an enum that
# hides it.
RENUMBER_TABLES = tuple(sorted(set(ALL_DATABASE_TABLES) | {"maps"}))

# The tables `renumber` is offered for, for this tool's description. The
# authority is `analysis.RENUMBER_COMPLETE`; a test holds the two equal.
OFFERED_TABLES = ("common_events", "items", "maps", "skills", "troops")


REFACTOR_OP_ARGS = {
    "renumber": {"op", "table", "from_id", "to_id", "swap", "dry_run",
                 "limit", "offset", "allow_script_mentions"},
    "rename_switch": {"op", "id", "name", "dry_run"},
    "rename_variable": {"op", "id", "name", "dry_run"},
}


_TOOL = "refactor"


@tool(
    "references",
    "Find where an entry, map, switch or variable is used",
    "Where is item 12 / skill 5 / switch 40 / variable 3 / common event 7 / "
    "map 4 used? Read-only. `kind` is one of %s and `id` its number "
    "(switches and variables 1 to %d, the rest 1 to %d). Reply: `total` and "
    "`sites`, each with `where` (\"map 3 event 5 (Gate) page 0 command 12\") "
    "and `what` (the command name, or the database field such as "
    "\"learnings[0].skill_id\"). At most %d sites are listed per call "
    "(`limit`, default %d); `offset` pages on, up to %d; `sites_truncated` "
    "says there are more. `coverage` says exactly which kinds of site were "
    "scanned (event commands, page conditions, troop pages, database fields, "
    "System start positions) and which cannot be. Ruby scripts that mention "
    "the number are NOT references: they are text matches, counted apart in "
    "`script_mentions` (count and a few lines) and never in `total`. A "
    "project too large for one call's walk says so in `references_stopped`, "
    "and a file that could not be read is named in `references_not_read`; "
    "both mean `total` is a lower bound. Does not change anything -- "
    "`refactor` renumbers and renames. Command layouts are VX Ace's and VX's "
    "shared ones; database fields are modelled for VX Ace only."
    % (", ".join(REFERENCE_KIND_NAMES), MAX_SWITCH_ID, MAX_REFERENCE_ID, MAX_SITES,
       DEFAULT_SITES, MAX_OFFSET),
    schema({
        "kind": prop(STR, "What kind of thing: " + ", ".join(
            REFERENCE_KIND_NAMES) + ".", enum=list(REFERENCE_KIND_NAMES)),
        "id": prop(INT, "Its number: the id in its table, the map id, or the "
                        "switch or variable number.",
                   minimum=1, maximum=MAX_REFERENCE_ID),
        "limit": prop(INT, "How many sites to list (default %d, at most %d)."
                      % (DEFAULT_SITES, MAX_SITES),
                      minimum=1, maximum=MAX_SITES),
        "offset": prop(INT, "How many sites to skip, to page (at most %d)."
                       % MAX_OFFSET, minimum=0, maximum=MAX_OFFSET),
    }, ["kind", "id"]),
)
def references(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """List where one thing is used. Reads only; the walk is budgeted."""
    # On first call: `ref_sites` pulls in `switch_usage`, which the server's
    # start-up must not load (tests/test_startup.py).
    from ..analysis import coverage_for, scan_sites

    kind = one_of(args, "kind", list(REFERENCE_KIND_NAMES))
    ident = need_int(args, "id", minimum=1, maximum=MAX_REFERENCE_ID)
    if kind in ("switches", "variables") and ident > MAX_SWITCH_ID:
        raise ToolError("%s are numbered 1 to %d, got %d"
                        % (kind, MAX_SWITCH_ID, ident))
    limit = opt_int_default(args, "limit", DEFAULT_SITES, 1, MAX_SITES)
    offset = opt_int_default(args, "offset", 0, 0, MAX_OFFSET)

    budget = WalkBudget()
    scan = scan_sites(ctx.project, kind, {ident}, budget,
                      store_limit=offset + limit)
    window = scan.hits[offset:offset + limit]
    reply: dict[str, Any] = {
        "kind": kind, "id": ident, "total": scan.total,
        "offset": offset, "limit": limit,
        "sites": [hit.as_row() for hit in window],
    }
    reply.update(partial("sites", truncated=scan.total > offset + len(window)))
    reply["coverage"] = coverage_for(kind, ctx.project.engine)
    reply["script_mentions"] = _mentions(scan)
    reply.update(partial(
        "references", stopped=budget.note("references"),
        not_read=scan.unread))
    return reply


def _mentions(scan: Any) -> dict[str, Any]:
    """The text matches, labelled as what they are."""
    out: dict[str, Any] = {
        "count": scan.mention_total,
        "listed": [mention.as_row() for mention in scan.mentions],
        "note": "text matches in Ruby code (for example $game_switches[40] "
                "or $data_items[12]) found by pattern, not references: they "
                "are not counted in `total`, an index computed at run time "
                "is invisible, and a renumber does not rewrite them",
    }
    if scan.mentions_clipped:
        out["clipped"] = scan.mentions_clipped
    return out


@tool(
    "refactor",
    "Renumber an entry or map, or name a switch or variable",
    "Change a number and everything that refers to it, in one atomic step. "
    "op \"renumber\" moves `table` entry `from_id` to `to_id` (which must be "
    "free; with swap true both must exist and they exchange): the entry's "
    "own id and array position, every reference found by the same scan "
    "`references` uses, and for maps the Map### file, the MapInfos key, "
    "children's parent_id, Transfer Player and Set Vehicle Location commands "
    "and the System start, editor and vehicle maps. It FAILS CLOSED: only "
    "tables whose reference sites have been reviewed and declared complete "
    "(%s, on VX Ace) can be renumbered; any other table is refused with the "
    "places that would be missed. It also refuses -- and a dry run lists as "
    "`blocked` -- when the scan could not read every file or ran out of "
    "walk, when references already name the free `to_id`, and when Ruby "
    "scripts mention the number (pass allow_script_mentions true to proceed: "
    "scripts are never rewritten). Skills 1 and 2 are the engine's Attack "
    "and Guard and cannot move. dry_run lists the sites that would be "
    "rewritten with `before` and what they would become (%d per call, "
    "`limit`; `offset` pages on, up to %d). The write is atomic: every file "
    "is checked for staleness first, and if a later write fails the earlier "
    "ones are restored byte for byte. Files that need no change are not "
    "touched. ops \"rename_switch\" and \"rename_variable\" set the name "
    "in System's list for `id` (1 to %d, name at most %d characters). Does "
    "not rename database entries (db_edit does), and does not edit scripts."
    % (", ".join(OFFERED_TABLES), MAX_SITES, MAX_OFFSET, MAX_SWITCH_ID,
       MAX_NAME_CHARS),
    schema({
        "op": prop(STR, "What to do.", enum=list(REFACTOR_OP_ARGS)),
        "table": prop(STR, "For renumber: the table, or maps.",
                      enum=list(RENUMBER_TABLES)),
        "from_id": prop(INT, "For renumber: the entry or map to move.",
                        minimum=1, maximum=MAX_REFERENCE_ID),
        "to_id": prop(INT, "For renumber: where it goes. Must be free unless "
                           "swap is true.", minimum=1, maximum=MAX_REFERENCE_ID),
        "swap": prop(BOOL, "For renumber: exchange two existing entries "
                           "(from_id and to_id) instead of moving one into a "
                           "free id."),
        "id": prop(INT, "For rename_switch and rename_variable: the number "
                        "(1 to %d)." % MAX_SWITCH_ID,
                   minimum=1, maximum=MAX_SWITCH_ID),
        "name": prop(STR, "For rename_switch and rename_variable: the new "
                          "name, on one line.", maxLength=MAX_NAME_CHARS),
        "limit": prop(INT, "For a renumber preview: how many sites to list "
                           "(default %d, at most %d)."
                      % (DEFAULT_SITES, MAX_SITES),
                      minimum=1, maximum=MAX_SITES),
        "offset": prop(INT, "For a renumber preview: how many sites to skip "
                            "(at most %d)." % MAX_OFFSET,
                       minimum=0, maximum=MAX_OFFSET),
        "allow_script_mentions": prop(
            BOOL, "For renumber: go ahead although Ruby scripts mention the "
                  "number. They are not rewritten."),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def refactor(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Renumber or rename. Every file is staged, and saves together or not at all."""
    op = one_of(args, "op", list(REFACTOR_OP_ARGS))
    check_op_args(op, args, REFACTOR_OP_ARGS, "refactor")
    dry_run = opt_bool(args, "dry_run")
    if op == "renumber":
        return _renumber(ctx, args, dry_run)
    return _rename(ctx, op, args, dry_run)


# -- rename_switch / rename_variable -----------------------------------------


def _rename(ctx: Context, op: str, args: dict[str, Any],
            dry_run: bool) -> dict[str, Any]:
    kind = "switches" if op == "rename_switch" else "variables"
    noun = "switch" if kind == "switches" else "variable"
    ident = need_int(args, "id", minimum=1, maximum=MAX_SWITCH_ID)
    name = need_str(args, "name")
    if len(name) > MAX_NAME_CHARS:
        raise ToolError("name takes at most %d characters, got %d"
                        % (MAX_NAME_CHARS, len(name)))
    if any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ToolError("a %s name is one line of text, with no control "
                        "characters" % noun)
    # Refused before anything is touched, so a name this project's code page
    # cannot hold costs nothing.
    new_string = ctx.codec.make_string(name)
    filename = ctx.project.filename_for("system")
    with ctx.staged(filename) as landed:
        system = ctx.project.load_data("system")
        names = system.get(kind) if isinstance(system, RubyObject) else None
        if not isinstance(names, list):
            raise ToolError("System has no %s list to name %s %d in"
                            % (kind, noun, ident))
        before = decoded(ctx, names[ident]) if ident < len(names) else ""
        if before == name:
            raise ToolError("%s %d is already named %s" % (noun, ident,
                                                           echo(name)))
        while len(names) <= ident:
            names.append(ctx.codec.make_string(""))
        names[ident] = new_string
        write = landed.save(filename, dry_run=dry_run, tool=_TOOL,
                            summary="named %s %d %s" % (noun, ident,
                                                        echo(name)))
    return write_reply({"kind": kind, "id": ident, "before": before,
                        "after": name, "write": write}, dry_run)


# -- renumber ----------------------------------------------------------------


class _Plan:
    """What a renumber would do, worked out without changing anything."""

    __slots__ = ("table", "from_id", "to_id", "swap", "mapping", "hits",
                 "unique", "blockers", "files", "moves", "scan", "budget",
                 "extra_locations")

    def __init__(self, table: str, from_id: int, to_id: int, swap: bool):
        self.table = table
        self.from_id = from_id
        self.to_id = to_id
        self.swap = swap
        self.mapping = ({from_id: to_id, to_id: from_id} if swap
                        else {from_id: to_id})
        self.hits: list[Any] = []
        self.unique: list[Any] = []
        self.blockers: list[str] = []
        # Every file whose cached value changes and is saved in place.
        self.files: set[str] = set()
        self.moves: list[str] = []
        self.scan: Any = None
        self.budget: "WalkBudget | None" = None
        self.extra_locations = 0


def _renumber(ctx: Context, args: dict[str, Any],
              dry_run: bool) -> dict[str, Any]:
    from ..analysis import RESERVED_IDS, renumber_support

    project = ctx.project
    table = one_of(args, "table", list(RENUMBER_TABLES))
    from_id = need_int(args, "from_id", minimum=1, maximum=MAX_REFERENCE_ID)
    to_id = need_int(args, "to_id", minimum=1, maximum=MAX_REFERENCE_ID)
    swap = opt_bool(args, "swap")
    limit = opt_int_default(args, "limit", DEFAULT_SITES, 1, MAX_SITES)
    offset = opt_int_default(args, "offset", 0, 0, MAX_OFFSET)
    allow_scripts = opt_bool(args, "allow_script_mentions")

    if table == "map_infos":
        raise ToolError(
            "map_infos is the map tree, not a table of its own: renumber "
            "table \"maps\", which moves the tree entry with the file.")
    if project.engine != project.profile.key:
        raise ToolError(
            "this is a %s project described with the %s model, so what "
            "refers to an entry is not known and renumber is refused."
            % (project.engine, project.profile.label))
    supported, why = renumber_support(project.engine, table)
    if not supported:
        raise ToolError(why)
    if from_id == to_id:
        raise ToolError("from_id and to_id are both %d; there is nothing to "
                        "move" % from_id)
    if to_id > MAX_MAP_ID and table == "maps":
        raise ToolError("to_id must be at most %d for a map" % MAX_MAP_ID)
    reserved = RESERVED_IDS.get(table, frozenset())
    taken = sorted(reserved & {from_id, to_id})
    if taken:
        raise ToolError(
            "%s %s %s used by the engine by number (skill 1 is Attack and "
            "skill 2 is Guard, wherever the scripts say so), so it cannot be "
            "renumbered or swapped"
            % (table, ", ".join(str(i) for i in taken),
               "is" if len(taken) == 1 else "are"))

    plan = _plan(ctx, table, from_id, to_id, swap, allow_scripts)
    if dry_run:
        return write_reply(_reply(ctx, plan, limit, offset, dry_run=True),
                           True)
    if plan.blockers:
        raise ToolError("renumber refused: " + "; ".join(plan.blockers))
    return _apply(ctx, plan, limit, offset, allow_scripts)


def _plan(ctx: Context, table: str, from_id: int, to_id: int, swap: bool,
          allow_scripts: bool) -> _Plan:
    """Check the request against the project and scan for every site."""
    from ..analysis import scan_sites, unique_hits

    project = ctx.project
    plan = _Plan(table, from_id, to_id, swap)
    if table == "maps":
        _check_maps(ctx, plan)
    else:
        _check_entries(ctx, plan)

    budget = WalkBudget()
    # Both ids, even when only one moves: whatever already names the free
    # target is a reason to stop, and cannot be seen by looking for the source.
    scan = scan_sites(project, table, {from_id, to_id}, budget)
    plan.scan, plan.budget = scan, budget
    plan.hits = scan.hits
    plan.unique = unique_hits(scan.hits)
    plan.extra_locations = len(scan.hits) - len(plan.unique)

    if budget.exhausted:
        plan.blockers.append(
            "the scan did not finish, so references may have been missed: %s"
            % budget.note("renumber's reference scan"))
    if scan.unread:
        plan.blockers.append(
            "these could not be read, so a reference in them would be "
            "missed: %s" % ", ".join("%s (%s)" % (name, why)
                                     for name, why in sorted(
                                         scan.unread.items())))
    if scan.mention_total and not allow_scripts:
        shown = "; ".join("%s: %s" % (m.where, m.text)
                          for m in scan.mentions[:3])
        plan.blockers.append(
            "%d Ruby script line(s) mention %s %d or %d and scripts are not "
            "rewritten (%s). Fix them by hand afterwards and pass "
            "allow_script_mentions true, or leave this number alone"
            % (scan.mention_total, table, from_id, to_id, shown))
    if not swap:
        dangling = [hit for hit in plan.unique if hit.value == to_id]
        if dangling:
            plan.blockers.append(
                "%d existing site(s) already name %s %d, which has no entry, "
                "so moving %d there would make them point at it (first: %s). "
                "Point them somewhere else first, or pick another to_id"
                % (len(dangling), table, to_id, from_id, dangling[0].where))
    unmovable = [hit for hit in plan.unique if hit.holder is None]
    if unmovable:
        plan.blockers.append(
            "%d site(s) hold the number where it cannot be rewritten (first: "
            "%s)" % (len(unmovable), unmovable[0].where))

    plan.files = {hit.file for hit in plan.unique if hit.file}
    if table == "maps":
        _plan_map_files(ctx, plan)
    else:
        plan.files.add(project.filename_for(table))
    return plan


def _check_entries(ctx: Context, plan: _Plan) -> None:
    """The array-table half of the checks: the entries exist, or the target is free."""
    project, table = ctx.project, plan.table
    container = project.load_data(table)
    if project.container_kind(table, container) != ARRAY:
        raise ToolError("%s is not an id-indexed array, so its entries "
                        "cannot be renumbered" % table)
    for entry_id in ((plan.from_id, plan.to_id) if plan.swap
                     else (plan.from_id,)):
        entry = project.get_entry(table, entry_id)
        if entry is None:
            raise ToolError("%s has no entry %d to %s"
                            % (table, entry_id,
                               "swap" if plan.swap else "move"))
        if not isinstance(entry, RubyObject):
            raise ToolError("%s entry %d is not an object, so it cannot be "
                            "renumbered" % (table, entry_id))
        if entry.has("id") and entry.get("id") != entry_id:
            raise ToolError(
                "%s entry %d carries id %s, so it is not where its own id "
                "says; renumbering it would compound the damage. Fix the "
                "entry first" % (table, entry_id, echo(entry.get("id"))))
    if not plan.swap:
        if project.get_entry(table, plan.to_id) is not None:
            raise ToolError(
                "%s %d is already taken. Pick a free id, or pass swap true "
                "to exchange the two" % (table, plan.to_id))
        ceiling = min(MAX_REFERENCE_ID, len(container) + ARRAY_GROWTH_HEADROOM)
        if plan.to_id > ceiling:
            raise ToolError(
                "to_id %d is too high: %s has %d slots, so ids up to %d are "
                "allowed (the rest would be empty slots)"
                % (plan.to_id, table, len(container), ceiling))


def _check_maps(ctx: Context, plan: _Plan) -> None:
    """The map half: file and tree entry must agree about what exists."""
    project = ctx.project
    infos = project.load_data("map_infos")
    if not isinstance(infos, RubyHash):
        raise ToolError("MapInfos is not a hash, so the map tree cannot be "
                        "renumbered")
    on_disk = project.map_ids_present()
    wanted = (plan.from_id, plan.to_id) if plan.swap else (plan.from_id,)
    for map_id in wanted:
        has_file, has_entry = map_id in on_disk, infos.get(map_id) is not None
        if not (has_file and has_entry):
            raise ToolError(
                "map %d %s, so it cannot be %s; map_list shows which maps "
                "have both" % (
                    map_id,
                    "has no map file and no tree entry"
                    if not (has_file or has_entry)
                    else "has a tree entry but no map file" if has_entry
                    else "has a map file but no tree entry",
                    "swapped" if plan.swap else "moved"))
    if not plan.swap:
        if plan.to_id in on_disk or infos.get(plan.to_id) is not None:
            raise ToolError(
                "map %d is already taken. Pick a free id, or pass swap true "
                "to exchange the two" % plan.to_id)
        if project.exists(project.map_file(plan.to_id)):
            raise ToolError("%s already exists" % project.map_file(plan.to_id))


def _plan_map_files(ctx: Context, plan: _Plan) -> None:
    """Which files a map renumber saves, creates and removes."""
    project = ctx.project
    plan.files.add(project.filename_for("map_infos"))
    old, new = project.map_file(plan.from_id), project.map_file(plan.to_id)
    if plan.swap:
        plan.files.update((old, new))
    else:
        # The old file is deleted, so a change to it is carried by the new
        # one; saving it first would only write a file about to go.
        plan.files.discard(old)
        plan.moves = [old, new]


def _row(hit: Any, mapping: dict[int, int], also: int = 0) -> dict[str, Any]:
    row = hit.as_row()
    row["before"] = hit.value
    row["after"] = mapping.get(hit.value, hit.value)
    if also:
        row["also_reached_at"] = also
    return row


def _own_changes(ctx: Context, plan: _Plan) -> list[str]:
    """What happens to the entry itself, apart from what refers to it."""
    project, table = ctx.project, plan.table
    out: list[str] = []
    pairs = ([(plan.from_id, plan.to_id), (plan.to_id, plan.from_id)]
             if plan.swap else [(plan.from_id, plan.to_id)])
    for old, new in pairs:
        if table == "maps":
            out.append("map %d becomes map %d: tree entry key %d -> %d, "
                       "%s -> %s" % (old, new, old, new, project.map_file(old),
                                     project.map_file(new)))
        else:
            out.append("%s %d moves to slot %d and its own id field becomes "
                       "%d" % (table, old, new, new))
    return out


def _writes_planned(plan: _Plan) -> list[str]:
    names = sorted(plan.files)
    if plan.moves:
        names += [plan.moves[1]]
    return names


def _reply(ctx: Context, plan: _Plan, limit: int, offset: int,
           dry_run: bool) -> dict[str, Any]:
    """The preview and the act, in one shape."""
    extra: dict[str, int] = {}
    for hit in plan.hits:
        key = hit.key
        if key is not None:
            extra[key] = extra.get(key, 0) + 1
    rows = [_row(hit, plan.mapping, extra.get(hit.key, 1) - 1 if hit.key
                 else 0) for hit in plan.unique[offset:offset + limit]]
    reply: dict[str, Any] = {
        "table": plan.table, "from_id": plan.from_id, "to_id": plan.to_id,
        "swap": plan.swap,
        "moves": _own_changes(ctx, plan),
        "sites_total": len(plan.unique),
        "shared_locations": plan.extra_locations,
        "offset": offset, "limit": limit,
        "sites": rows,
    }
    reply.update(partial("sites", truncated=len(plan.unique) > offset + len(rows)))
    reply["blocked"] = list(plan.blockers)
    reply["writes"] = _writes_planned(plan)
    reply["script_mentions"] = _mentions(plan.scan)
    return reply


def _apply(ctx: Context, plan: _Plan, limit: int, offset: int,
           allow_scripts: bool) -> dict[str, Any]:
    """Stage every file, rewrite, and save all of them or none."""
    project = ctx.project
    project.require_writable("renumbering %s %d" % (plan.table, plan.from_id))
    names = set(plan.files) | set(plan.moves)
    with ctx.staged(*sorted(names)) as landed:
        # Scanned again with the files pinned: the first scan only learned
        # which files are involved. Walking the project loads (and may evict)
        # far more than is held here, and a rewrite has to land in the
        # objects the cache keeps, not in a copy that was let go.
        again = _plan(ctx, plan.table, plan.from_id, plan.to_id, plan.swap,
                      allow_scripts)
        if again.blockers or again.files != plan.files \
                or again.moves != plan.moves \
                or len(again.unique) != len(plan.unique):
            raise ToolError(
                "the project changed while the renumber was being prepared "
                "(%s), so nothing was written. Run it again"
                % ("; ".join(again.blockers) or "a different set of sites"))
        from ..analysis import rewrite_hits
        changed = rewrite_hits(again.hits, again.mapping)
        if changed != len(again.unique):
            raise ToolError(
                "%d of %d sites were rewritten; refusing to save a "
                "partial renumber" % (changed, len(again.unique)))
        _move_entry(ctx, again)
        writes = _write_all(ctx, again, landed)
    reply = _reply(ctx, again, limit, offset, dry_run=False)
    reply["writes"] = writes
    return reply


def _move_entry(ctx: Context, plan: _Plan) -> None:
    """The entry itself: its slot and own id, or the map's tree key and file."""
    project, table = ctx.project, plan.table
    if table == "maps":
        infos = project.load_data("map_infos")
        keyed = [(plan.mapping.get(key, key) if type(key) is int else key,
                  value) for key, value in infos.pairs]
        infos.pairs = keyed
        if plan.swap:
            project.exchange_cached(project.map_file(plan.from_id),
                                    project.map_file(plan.to_id))
        return
    container = project.load_data(table)
    if plan.swap:
        a, b = plan.from_id, plan.to_id
        container[a], container[b] = container[b], container[a]
        moved = (a, b)
    else:
        while len(container) <= plan.to_id:
            container.append(None)
        container[plan.to_id] = container[plan.from_id]
        container[plan.from_id] = None
        moved = (plan.to_id,)
    for slot in moved:
        entry = container[slot]
        if isinstance(entry, RubyObject) and entry.has("id"):
            entry.set("id", slot)


def _write_all(ctx: Context, plan: _Plan, landed: Any) -> list[dict[str, Any]]:
    """Save every file, and put the earlier ones back if a later one fails.

    Checked for staleness first, for all of them: the cheapest failure is the
    one that happens before anything is written. Then in an order that leaves
    the project loadable at each step -- files saved in place, the moved
    map's new file created, its old file removed last. If any step fails the
    files already written are restored from the bytes read before the first
    write, and the new file is removed.
    """
    project = ctx.project
    in_place = sorted(plan.files)
    old = plan.moves[0] if plan.moves else None
    new = plan.moves[1] if plan.moves else None
    project.check_fresh(*in_place, *([old] if old else []))
    originals = {name: project.disk_bytes(name)
                 for name in in_place + ([old] if old else [])}
    summary = "renumber %s %d -> %d%s" % (
        plan.table, plan.from_id, plan.to_id, " (swap)" if plan.swap else "")
    done: list[tuple[str, str]] = []
    writes: list[dict[str, Any]] = []
    try:
        for name in in_place:
            writes.append(landed.save(name, tool=_TOOL, summary=summary))
            done.append(("restore", name))
        if old and new:
            project.create_file(new, project.load_file(old))
            done.append(("remove", new))
            writes.append(landed.save(new, tool=_TOOL, summary=summary))
            writes.append(project.delete_file(old, tool=_TOOL))
            done.append(("restore", old))
    except Exception as exc:  # noqa: BLE001 - undo, then report whatever it was
        failed = _roll_back(project, done, originals)
        reason = "%s: %s" % (type(exc).__name__, exc)
        if failed:
            raise ToolError(
                "renumber failed (%s) and %d file(s) could not be put back: "
                "%s. Restore them from the backups folder before doing "
                "anything else." % (reason, len(failed), ", ".join(failed))
            ) from exc
        raise ToolError(
            "renumber failed (%s). Every file was put back as it was, so "
            "nothing changed." % reason) from exc
    return writes


def _roll_back(project: Any, done: list[tuple[str, str]],
               originals: dict[str, "bytes | None"]) -> list[str]:
    """Undo ``done`` newest first. Returns the names that could not be undone."""
    failed: list[str] = []
    for action, name in reversed(done):
        try:
            project.restore_bytes(name, originals.get(name)
                                  if action == "restore" else None)
        except (ProjectError, OSError):
            failed.append(name)
    return failed
