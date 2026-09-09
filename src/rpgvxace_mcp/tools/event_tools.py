"""Event tools: read event pages, and author the command lists inside them."""

from __future__ import annotations

from typing import Any

from ..commands import (
    CODE_BLANK,
    CONTINUATION_CODES,
    CommandError,
    MOVE_COMMAND_NAMES,
    command_name,
    command_table,
    compile_commands,
    move_route_steps,
    param_specs,
)
from ..commands import render_script
from ..convert import (
    TABLE_MODES,
    apply_patch,
    new_walk,
    normalize_strings,
    to_json,
)
from ..analysis import WalkBudget
from ..project import MAX_MAP_ID
from ..rubytypes import RubyHash, RubyObject, as_list
from ..schema import (
    COMMON_EVENT_TRIGGER_NAMES,
    TRIGGER_NAMES,
    on_map,
    unreadable_events_note,
    new_event,
    new_event_command,
    new_page,
)
from .registry import (
    write_reply,
    MAX_OPS,
    check_op_args,
    echo,
    known_fields,
    refuse_unknown_fields,
    refuse_fields,
    ops_arg,
    StagedWrite,
    int_list,
    opt_dict,
    ARR,
    BOOL,
    Context,
    DRY_RUN,
    INT,
    OBJ,
    REFRESH,
    STR,
    ToolError,
    one_of,
    opt_bool,
    opt_int,
    opt_list,
    opt_str,
    prop,
    schema,
    tool,
    decoded,
)

# The highest event id a map will hold. The editor numbers events from 1 and no
# real map comes near this.
MAX_EVENT_ID = 9999

# How many events one read will render. Each carries its whole command list as
# a readable script, so this is a response budget rather than a map limit.
MAX_EVENT_IDS_PER_CALL = 200

# The most commands one page or common event will be written with. The editor's
# own list becomes unusable far below this; the point is that a compiled list
# had no ceiling at all, so one large request could write a page nothing can
# open. Counted after compiling, since one DSL entry can become several
# commands.
MAX_COMMANDS_PER_LIST = 10000

# How many ids a refusal lists before it switches to naming the range. Enough
# to read at a glance; a message longer than this is not one a caller uses.
MAX_IDS_LISTED = 12


def _commands_arg(raw_op: dict[str, Any], op: str,
                  required: bool = False,
                  remedy: str = "remove the page with op \"delete_page\""
                  ) -> "list[Any] | None":
    """The commands one op was given, refusing an empty list.

    Two of the five ops that take `commands` refused an empty list and the
    three that take it alongside a patch did not, so
    `{"op": "set_page", "commands": []}` compiled nothing, wrote the lone
    blank terminator and reported "replaced the command list" -- a deletion
    wearing the name of an edit. All five now ask here, so the rule cannot
    hold in some of them and not the others.

    ``required`` marks the ops whose whole purpose is the command list, where
    leaving it out is as wrong as passing an empty one. ``remedy`` differs
    between a page and a common event, which has no page to delete.

    Returns None only for an op that may legitimately omit it, which is
    different from passing an empty list -- and is why this is not `opt_list`.
    """
    if raw_op.get("commands") is None:
        if required:
            raise ToolError(
                "op \"%s\" needs at least one command; it was given none. "
                "To leave the list alone, use a different op." % op)
        return None
    entries = opt_list(raw_op, "commands")
    if not entries:
        raise ToolError(
            "op \"%s\" was given an empty commands list, which would leave "
            "nothing but a blank terminator behind -- a deletion wearing the "
            "name of an edit. Pass at least one command, or %s." % (op, remedy))
    return entries


# What each op reads on a *common* event. A common event has no pages and no
# position, so it takes three ops and far fewer arguments -- and this loop is a
# different one from the map-event loop above, which is how it came to accept
# `commands` on an "update" (dropping them), and an `event_id` that means
# nothing here. Both tables are read by the same gate now.
COMMON_EVENT_OP_ARGS = {
    "update": {"op", "name", "patch"},
    "set_commands": {"op", "commands"},
    "append_commands": {"op", "commands"},
}

# What each op reads on a map event. Anything else a caller puts in an op is
# refused rather than dropped: `update` never looked at `commands`, so one op
# asking to rename an event and rewrite its command list renamed it, discarded
# the commands and reported "updated event 1".
#
# `add_page` takes no `page`: it appends, and the index it appended at comes
# back in the result. Held to the schema by a test, so a new op cannot land
# without an entry here.
EVENT_OP_ARGS = {
    "create": {"op", "event_id", "name", "x", "y", "patch", "commands"},
    "update": {"op", "event_id", "name", "x", "y"},
    "delete": {"op", "event_id"},
    "move": {"op", "event_id", "x", "y"},
    "set_page": {"op", "event_id", "page", "patch", "commands"},
    "add_page": {"op", "event_id", "patch", "commands"},
    "delete_page": {"op", "event_id", "page"},
    "set_commands": {"op", "event_id", "page", "commands"},
    "append_commands": {"op", "event_id", "page", "commands"},
}


DSL_HELP = (
    "Commands are written as a list of small objects, one per command. "
    'Supported forms: {"text": "line one\\nline two", "face_name": "Actor1"}; '
    '{"comment": "..."}; {"script": "ruby"}; {"switch": 5, "value": "on"}; '
    '{"switch": 5, "switch_end": 9, "value": "on"} sets a range of switches; '
    '{"self_switch": "A", "value": "on"}; '
    '{"variable": 3, "op": "add", "value": 1} (also from_variable, random); '
    '{"wait": 60}; {"common_event": 2}; {"label": "top"}; {"jump": "top"}; '
    '{"transfer": {"map_id": 2, "x": 5, "y": 6, "direction": 2, "fade": 0}}; '
    '{"exit": true}; {"break_loop": true}; '
    '{"if": {"switch": 1, "is": "on"}, "then": [...], "else": [...]}; '
    '{"loop": [...]}; '
    '{"choices": ["Yes", "No"], "branches": [[...], [...]], "cancel_branch": [...]}. '
    'Conditions accept switch, self_switch, variable (with compare and value or '
    'from_variable) and script. For any other command, use '
    '{"raw": {"code": 231, "parameters": [...]}} -- see the command_reference '
    "tool for codes, and read a working example from the project before "
    "guessing a parameter layout.\n\n"
    'Limits the compiler enforces: Show Choices takes 1 to 4 choices, and '
    '"branches" pairs with them by position. "text" may be a string with \\n '
    "or an array of lines, and is split into four-line message boxes "
    "automatically. Entries nest through then/else/loop/branches to a bounded "
    'depth. A "raw" entry inherits the surrounding indent unless it sets one.'
)


def refuse_inert(args: dict[str, Any], names: "tuple[str, ...]",
                 because: str) -> None:
    """Refuse an argument this branch does not read.

    `check_op_args` does this for a tool that selects with an `op`. These two
    select with something else -- which id was given, which of three switches
    -- so the gate does not reach them, and each dropped the arguments its
    branch has no use for. The rule is the same one and so is the reason: a
    dropped argument reports the same success as one that was applied.
    """
    inert = sorted(name for name in names if args.get(name) is not None)
    if inert:
        raise ToolError(
            "%s does not read %s. %s"
            % (because, " or ".join(echo(name) for name in inert),
               "Ask for one or the other."))


def _one_target(args: dict[str, Any], verb: str) -> "tuple[int | None, int | None]":
    """Which of the two things this call names, refusing both or neither.

    Map events and common events live in different files and are reached by
    different arguments, so exactly one has to be given. Written once because
    it was written twice -- and the two copies had already drifted, one
    ignoring `map_id` silently where the other refused it, so the same pair of
    arguments meant two different things in sibling tools.
    """
    common_id = opt_int(args, "common_event_id", minimum=1)
    map_id = opt_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    if common_id is not None and map_id is not None:
        raise ToolError(
            "pass exactly one of map_id or common_event_id; both were given, "
            "and map events and common events are %s separately" % verb)
    if common_id is None and map_id is None:
        raise ToolError("pass map_id (for map events) or common_event_id")
    return common_id, map_id


def _page_view(ctx: Context, page: RubyObject, index: int,
               include_raw: bool, walk: Any,
               budget: "WalkBudget | None" = None) -> dict[str, Any]:
    commands = as_list(page.get("list"))
    if budget is not None and not budget.spend(len(commands)):
        # Charged before rendering, because rendering is the cost: a page whose
        # @list is two thousand links to one command renders two thousand
        # lines, and fifty links to that page renders a hundred thousand.
        return {"page": index, "command_count": len(commands),
                "not_read": "the command walk stopped before this page"}
    condition = page.get("condition")
    graphic = page.get("graphic")
    view: dict[str, Any] = {
        "page": index,
        "trigger": TRIGGER_NAMES.get(page.get("trigger"), page.get("trigger")),
        "trigger_code": page.get("trigger"),
        "priority_type": page.get("priority_type"),
        "move_type": page.get("move_type"),
        "move_speed": page.get("move_speed"),
        "move_frequency": page.get("move_frequency"),
        "walk_anime": page.get("walk_anime"),
        "step_anime": page.get("step_anime"),
        "direction_fix": page.get("direction_fix"),
        "through": page.get("through"),
        "condition": (to_json(condition, ctx.codec, TABLE_MODES[0], walk)
                      if condition is not None else None),
        "graphic": (to_json(graphic, ctx.codec, TABLE_MODES[0], walk)
                    if graphic is not None else None),
        "command_count": len(commands),
        "script": render_script(commands, ctx.codec),
    }
    if include_raw:
        # The caller's budget, so a read of two hundred events cannot spend
        # two hundred budgets. The number of commands on a page comes out of
        # the file, so this is the only bound on the size of that list.
        view["commands"] = [to_json(c, ctx.codec, TABLE_MODES[0], walk)
                            for c in commands]
    return view


@tool(
    "event_get",
    "Read events and their command lists",
    "Read the events on a map, or a common event, including their command lists "
    "rendered as readable, indented script lines. Pass include_raw to also get "
    "the exact code/indent/parameters of every command, which is what you need "
    "before editing anything unusual.",
    schema({
        "map_id": prop(INT, "Read events from this map."),
        "common_event_id": prop(INT, "Read this common event instead of a map's events."),
        "event_ids": prop(ARR, "Only these event ids; at most %d per call, "
                               "each 1 to %d."
                               % (MAX_EVENT_IDS_PER_CALL, MAX_EVENT_ID),
                          items={"type": INT, "minimum": 1,
                                 "maximum": MAX_EVENT_ID},
                          maxItems=MAX_EVENT_IDS_PER_CALL),
        "name_contains": prop(STR, "Only events whose name contains this text."),
        # The negative case is said out loud because a caller who learned
        # `-1` from raw_data's paths -- where it does mean the last element --
        # will try it here. This module's own comment further down calls that
        # silence "the one reading a caller could not check".
        "page": prop(INT, "Only this page index (0-based). A negative "
                          "index such as -1 is refused rather than "
                          "counted from the end: "
                          "raw_data's paths do count from the end, and these "
                          "two tools differ on that.", minimum=0),
        "include_raw": prop(BOOL, "Include raw command objects as well as the "
                                  "readable script."),
        "refresh": REFRESH,
    }),
)
def event_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Events on a map, or one common event, with rendered scripts."""
    include_raw = opt_bool(args, "include_raw")
    refresh = opt_bool(args, "refresh")
    common_id, map_id = _one_target(args, "read")

    if common_id is not None:
        refuse_inert(
            args, ("event_ids", "name_contains", "page"),
            "a common event has no pages and is addressed by its own id, so "
            "reading one")
        common = ctx.project.get_entry("common_events", common_id, refresh)
        if common is None:
            raise ToolError("there is no common event %d" % common_id)
        if not isinstance(common, RubyObject):
            # The same refusal `_edit_common_event` makes, in the same words.
            # The read path called `.get("list")` straight away, so a file
            # holding an Array or a String in that slot raised AttributeError
            # -- reported with the hint "an argument was not the shape the
            # tool expected", which is a diagnosis about the caller for a
            # fact about the file.
            raise ToolError(
                "common event %d does not hold an RPG::CommonEvent object, "
                "so it cannot be read. Run validate to see what is wrong "
                "with the file." % common_id)
        # A slot that holds an RPG::CommonEvent whose @list is not an
        # Array is the same class of crafted file, one level down.
        commands = as_list(common.get("list"))
        # One allowance and one conversion budget for this reply, the same two
        # the map branch below takes. This branch had neither: it rendered the
        # whole list, so a common event whose @list is 250,000 links to one
        # command -- a few kilobytes on disk -- produced a thirty-megabyte
        # reply; and it built a fresh `new_walk()` per command, which is the
        # defect `to_json`'s docstring describes as fixed.
        #
        # `take` rather than `spend`: one list has no surrounding loop, so a
        # prefix is worth more than nothing. `command_count` stays the real
        # length either way -- what was rendered is a separate fact.
        command_budget = WalkBudget()
        walk = new_walk()
        shown = commands[:command_budget.take(len(commands))]
        result: dict[str, Any] = {
            "common_event_id": common_id,
            "name": decoded(ctx, common.get("name")),
            "trigger": COMMON_EVENT_TRIGGER_NAMES.get(
                common.get("trigger"), common.get("trigger")),
            "switch_id": common.get("switch_id"),
            "command_count": len(commands),
            "script": render_script(shown, ctx.codec),
        }
        if include_raw:
            result["commands"] = [
                to_json(c, ctx.codec, TABLE_MODES[0], walk) for c in shown]
        stopped = command_budget.note("event_get")
        if stopped:
            result["commands_rendered"] = len(shown)
            result["command_walk_stopped"] = stopped
        return result

    game_map = ctx.project.load_map(map_id, refresh)
    # One conversion budget for the whole reply. This renders every
    # command of every event asked for, and the number of commands on a
    # page comes out of the file.
    walk = new_walk()
    # And one allowance for the commands themselves, which is a different
    # budget from the JSON one: `new_walk` bounds how much a value expands
    # into a reply, and this bounds how many commands are visited at all.
    command_budget = WalkBudget()
    events = game_map.get("events")
    unreadable = unreadable_events_note(events)
    if unreadable:
        # `matching_events` on every path, including this one: two of the
        # three returns carried it and this one did not, so a caller reading
        # the count had to know which shape it had been given.
        return {"map_id": map_id, "events": [], "matching_events": 0,
                "events_note": unreadable}
    if events is None:
        return {"map_id": map_id, "events": [], "matching_events": 0}

    wanted = set(int_list(args, "event_ids", MAX_EVENT_IDS_PER_CALL,
                          minimum=1, maximum=MAX_EVENT_ID))
    needle = (opt_str(args, "name_contains") or "").lower()
    # Bounded, and reported when nothing has it. Used only as a filter, a
    # page index no event has returned every event with an empty `pages` list
    # and a non-zero `page_count` -- and `event_edit` refuses the same value
    # while `raw_data` gives -1 the opposite meaning, so silence here was the
    # one reading a caller could not check.
    only_page = opt_int(args, "page", minimum=0)

    out = []
    for event_id, event in events.sorted_items():
        # One step per event before anything about it is read. `_page_view`
        # spends the same budget per page, but an event with no pages reached
        # none of that -- and the events hash's size comes out of the file.
        if not command_budget.spend(1):
            break
        if not isinstance(event, RubyObject):
            continue
        if wanted and event_id not in wanted:
            continue
        name = decoded(ctx, event.get("name"))
        if needle and needle not in name.lower():
            continue
        pages = []
        for index, page in enumerate(as_list(event.get("pages"))):
            # Before both skips. `_page_view` charges per command, so a slot
            # that is not a page cost nothing -- and naming `page` made it
            # worse rather than better, because the `continue` for every
            # other index sat above the only charge on the path.
            if not command_budget.spend(1):
                break
            if only_page is not None and index != only_page:
                continue
            if isinstance(page, RubyObject):
                pages.append(_page_view(ctx, page, index, include_raw, walk,
                                        command_budget))
        out.append({
            "id": event_id, "name": name,
            "x": event.get("x"), "y": event.get("y"),
            "page_count": len(as_list(event.get("pages"))),
            "pages": pages,
        })
    result: dict[str, Any] = {
        "map_id": map_id, "events": out,
        # Named for what it counted, as db_list's `matching_entries` and
        # map_list's `matching_maps` already are: `event_ids`, `name_contains`
        # and `page` all filter this list, and "total" read as the number of
        # events on the map -- so a filtered read looked like the whole of a
        # much emptier map.
        "matching_events": len(out),
        "note": "VX runs the LAST page whose conditions are satisfied, so a "
                "later page with no conditions hides the pages before it."}
    if only_page is not None and not any(e["pages"] for e in out):
        result["page_note"] = (
            "no event on this map has a page %d; the pages each one does have "
            "are counted in page_count" % only_page)
    stopped = command_budget.note("event_get")
    if stopped:
        # One reply built once, with each thing it has to admit added to it.
        # Three separate early returns is how a note ended up unreachable
        # behind another, and how `stopped` came to be read on a path that
        # never assigned it.
        result["command_walk_stopped"] = stopped
    return result


@tool(
    "event_edit",
    "Create, update or delete events and their commands",
    "Author events on a map, or rewrite a common event's commands. Operations "
    "run in order against one map (or one common event) and the file is saved "
    "once at the end, after backing it up. " + DSL_HELP,
    schema({
        "map_id": prop(INT, "The map to edit. Required for every op except when "
                            "editing a common event."),
        "common_event_id": prop(INT, "Edit this common event's command list "
                                     "instead of a map. Only the ops "
                                     "set_commands, append_commands and update "
                                     "apply to a common event -- the page ops "
                                     "are map-only. Pass exactly one of map_id "
                                     "or common_event_id."),
        "ops": prop(ARR, "Operations, applied in order. At most %d per "
                         "call." % MAX_OPS,
                    maxItems=MAX_OPS, items={
            "additionalProperties": False,
            "type": OBJ,
            "properties": {
                "op": {"type": STR, "enum": list(EVENT_OP_ARGS)},
                "event_id": {"type": INT, "minimum": 1,
                             "maximum": MAX_EVENT_ID,
                             "description": "Which event to act on, 1 to %d."
                                            % MAX_EVENT_ID},
                "page": {"type": INT, "minimum": 0, "description":
                         "Page index, 0-based (default 0). A negative "
                         "index such as -1 is refused rather than "
                         "counted from the end, unlike raw_data's "
                         "paths."},
                "name": {"type": STR, "description": "Event name, for create/update."},
                "x": {"type": INT, "minimum": 0, "description":
                      "Tile X, for create, update and move."},
                "y": {"type": INT, "minimum": 0, "description":
                      "Tile Y, for create, update and move."},
                "patch": {"type": OBJ, "description":
                          "Page fields to set: trigger, priority_type, move_type, "
                          "move_speed, move_frequency, walk_anime, step_anime, "
                          "direction_fix, through, and nested condition/graphic "
                          'objects, e.g. {"trigger": 3, "graphic": '
                          '{"character_name": "People1", "character_index": 2}, '
                          '"condition": {"self_switch_valid": true}}. '
                          "Against a common_event_id this patches the "
                          "RPG::CommonEvent instead, whose fields are name, "
                          "trigger and switch_id -- a page field there is "
                          "refused, because a common event has no pages."},
                "commands": {"type": ARR,
                             "maxItems": MAX_COMMANDS_PER_LIST,
                             # Generated from the table that decides it. Named
                             # by hand, this said three of the five ops that
                             # read `commands`, so a model wanting a page with
                             # commands on it split one op into two.
                             "description": "Command entries, for op %s. At "
                                            "most %d, and a page holds no "
                                            "more than that in total."
                                            % (", ".join(sorted(
                                                op for op, reads
                                                in EVENT_OP_ARGS.items()
                                                if "commands" in reads)),
                                               MAX_COMMANDS_PER_LIST),
                             "items": {"type": OBJ}},
            },
            "required": ["op"],
        }),
        "dry_run": DRY_RUN,
    }, ["ops"]),
    writes=True,
)
def event_edit(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Apply ops to one map's events, or to one common event.

    Mutations are staged, so a dry run, a refusal or an op that fails
    partway leaves nothing for a later write to pick up. Refuses a patch
    that sets a page's command list directly -- use set_commands, which
    builds a list the interpreter can run.
    """
    # ops_arg checks both targets' op lists in one place: the common-event
    # branch used to skip the shape check and turn a non-object op into an
    # internal error.
    ops = ops_arg(args)
    dry_run = opt_bool(args, "dry_run")
    common_id, map_id = _one_target(args, "edited")
    if common_id is not None:
        return _edit_common_event(ctx, common_id, ops, dry_run)

    filename = ctx.project.map_file(map_id)
    with ctx.staged(filename) as write_landed:
        return _apply_event_ops(ctx, map_id, filename, ops, dry_run,
                                write_landed)


def _apply_event_ops(ctx: Context, map_id: int, filename: str,
                     ops: list[Any], dry_run: bool,
                     write_landed: StagedWrite) -> dict[str, Any]:
    game_map = ctx.project.load_map(map_id)
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        if events is not None:
            # Replacing it would throw away whatever is there, which on a
            # damaged or unmodelled-engine file is the user's events.
            raise ToolError(
                "map %d holds its events as %s rather than a Hash, so this "
                "tool cannot edit them without discarding what is there. Run "
                "validate to see what is wrong with the file."
                % (map_id, type(events).__name__))
        events = RubyHash()
        game_map.set("events", events)
    width, height = game_map.get("width") or 0, game_map.get("height") or 0

    # One request, and therefore one index, for the whole call. Built per
    # op it was the `MAX_OPS` multiplier its own comment describes rather
    # than the fix for it: `events.items()` copies every pair and the
    # comprehension iterates them all, so 200 ops over a crafted map's 2.5
    # million pairs was ~10^9 steps plus 200 allocations of a ~130 MB dict.
    # `add`/`remove` only mean anything across ops, which is the other half
    # of why there is one request.
    request = _EventRequest(ctx, events, width, height)
    applied = []
    for index, raw_op in enumerate(ops):
        applied.append(_apply_event_op(request, raw_op, "ops[%d]" % index))

    write = write_landed.save(
        filename, dry_run=dry_run, tool="event_edit",
        summary="; ".join(str(a.get("summary", a.get("op"))) for a in applied),
    )
    return write_reply({"map_id": map_id, "applied": applied,
                        "write": write}, dry_run)


def _range_text(ids: list[int]) -> str:
    """The ids that exist, said briefly enough to put in a message."""
    if not ids:
        return "none at all"
    if len(ids) <= MAX_IDS_LISTED:
        return ", ".join(str(i) for i in sorted(ids))
    return "%d to %d" % (min(ids), max(ids))


def _page_fields(ctx: Context, page: RubyObject) -> set[str]:
    """What a patch to an event page may name."""
    return known_fields(ctx, page, "RPG::Event::Page")


def _check_page_patch(patch: dict[str, Any]) -> None:
    """Refuse a patch that would write a command list the tool did not build.

    A page's @list has invariants the DSL exists to hold -- a Show Text needs
    its 401 lines, a list needs its blank terminator -- and a patch goes
    straight through to the file, so it could leave a page the interpreter
    misreads. `set_commands` is the way to change a command list.

    @id is refused for an unrelated reason and gets its own remedy: it is the
    entry's own slot number, so there is nothing to say about command lists to
    a caller who named it. One message for both fields sent anyone patching
    @id off to read about a DSL that has nothing to do with it.
    """
    refuse_fields(
        patch, ("list",),
        "Use op \"set_commands\" or \"append_commands\" to change what a "
        "page runs, so the command list is built correctly.")
    refuse_fields(
        patch, ("id",),
        "An entry's id is its own position, which the engine subscripts it "
        "by: "
        "leave it out of the patch.")


def _check_bounds(x: Any, y: Any, width: int, height: int) -> None:
    """Keep events on the map: the editor cannot select one placed outside it.

    A map whose width or height does not read as a positive number is refused
    rather than treated as unbounded -- "we could not tell" is not a reason to
    allow a placement nothing can check.
    """
    if not isinstance(width, int) or not isinstance(height, int) \
            or width < 1 or height < 1:
        raise ToolError(
            "this map does not report a usable size (%r x %r), so an event "
            "position cannot be checked against it. Run validate to see what "
            "is wrong with the map." % (width, height))
    if not isinstance(x, int) or not isinstance(y, int):
        raise ToolError("event coordinates must be whole numbers, got (%r, %r)" % (x, y))
    # `on_map`, not a fourth copy of the arithmetic. The `and width` /
    # `and height` clauses this line used to carry were dead: both are
    # refused above unless they are >= 1, which is what the docstring says
    # was decided -- so the code contradicted its own history note.
    if not on_map(x, y, width, height):
        raise ToolError(
            "(%d, %d) is outside this map, which is %dx%d. Tile coordinates start "
            "at (0, 0) in the top-left corner." % (x, y, width, height)
        )


class _EventRequest:
    """What every event_edit op needs, so each op takes three arguments.

    The ops resolve in three tiers -- an event, that event's page list, one
    page of it -- and each tier has a check that must not be skipped. As one
    function those checks sat between the branches in written order, which is
    how `add_page` came to name a page index that is only assigned two tiers
    down, and how `@pages` came to be used without the type check `@events`
    gets. As methods, an op asks for the tier it needs and gets the check with
    it.
    """

    __slots__ = ("ctx", "events", "width", "height", "_by_id")

    def __init__(self, ctx: Context, events: RubyHash, width: int, height: int):
        self.ctx = ctx
        self.events = events
        self.width = width
        self.height = height
        # One index for the whole call. `RubyHash` is a list of pairs, so
        # every `get` and every `keys()` is a linear scan whose length comes
        # out of the file -- and `MAX_OPS` is a caller-controlled multiplier
        # on it: a 10 MB crafted map holds about 2.5 million pairs, so two
        # hundred ops is 5x10^8 comparisons in one uninterruptible call. The
        # class docstring on `RubyHash` prescribes exactly this ("a caller
        # doing repeated lookups in a loop should hoist dict(hash.items())
        # out of it") and no caller did it.
        #
        # Numbers only, and `float` as well as `int`: an event id is a
        # number, and `_find` compares with `==`, where `1 == 1.0` -- so
        # indexing both keeps the answer identical to the scan's rather than
        # merely close to it. A key that is neither is not an event id and
        # cannot be reached by `event_id`, which comes from `opt_int`.
        self._by_id = {key: value for key, value in events.items()
                       if isinstance(key, (int, float))}

    def event_id(self, raw_op: dict[str, Any], op: str) -> int:
        event_id = opt_int(raw_op, "event_id")
        if event_id is None:
            raise ToolError("%s needs an event_id" % op)
        return event_id

    def event(self, raw_op: dict[str, Any], op: str) -> "tuple[int, RubyObject]":
        """The event an op names, or a refusal saying it is not there."""
        event_id = self.event_id(raw_op, op)
        event = self._by_id.get(event_id)
        if not isinstance(event, RubyObject):
            raise ToolError("this map has no event %d" % event_id)
        return event_id, event

    @property
    def used_ids(self) -> "set[int]":
        """Which event ids this map already has, without rescanning it."""
        return {key for key in self._by_id if isinstance(key, int)}

    def add(self, event_id: int, event: RubyObject) -> None:
        """Put a new event in the map *and* in the index.

        Both, in one method, because an index that the create op forgets to
        update is worse than no index: the next op in the same batch would be
        told the event it just created does not exist.
        """
        self.events.set(event_id, event)
        self._by_id[event_id] = event

    def remove(self, event_id: int) -> None:
        """Take an event out of the map and out of the index."""
        self.events.delete(event_id)
        self._by_id.pop(event_id, None)

    def pages(self, event: RubyObject, event_id: int) -> list:
        """One event's page list, type-checked like the map's @events.

        A nil @pages becomes a fresh list, which `add_page` then fills; a
        @pages that is present but not a list is refused rather than replaced,
        because replacing it would throw away whatever is there -- on a damaged
        or unmodelled-engine file, the user's event pages.
        """
        pages = event.get("pages")
        if pages is None:
            return []
        if not isinstance(pages, list):
            raise ToolError(
                "event %d holds its pages as %s rather than a list, so this "
                "tool cannot edit them without discarding what is there. Run "
                "validate to see what is wrong with the map."
                % (event_id, type(pages).__name__))
        return pages

    def page_index(self, raw_op: dict[str, Any], pages: list,
                   event_id: int) -> int:
        """Which page an op names, checked against the ones that exist."""
        index = opt_int(raw_op, "page", 0) or 0
        if 0 <= index < len(pages):
            return index
        if not pages:
            # "pages 0..-1" is what naming the range arithmetically printed.
            raise ToolError(
                "event %d has no pages at all, so there is no page %d to act "
                "on. Add one with op \"add_page\"." % (event_id, index))
        raise ToolError("event %d has pages %s, not %d"
                        % (event_id, _range_text(list(range(len(pages)))),
                           index))

    def page(self, raw_op: dict[str, Any], pages: list,
             event_id: int) -> "tuple[int, RubyObject]":
        index = self.page_index(raw_op, pages, event_id)
        page = pages[index]
        if not isinstance(page, RubyObject):
            raise ToolError("page %d of event %d is not an RPG::Event::Page"
                            % (index, event_id))
        return index, page

    def patch_page(self, page: RubyObject, raw_op: dict[str, Any],
                   what: str) -> list:
        """Apply an op's patch to one page, refusing what a patch may not say.

        Every op that takes a patch goes through here, so the two refusals --
        a field the page does not have, and a field the DSL owns -- cannot
        reach some ops and miss others.
        """
        patch = opt_dict(raw_op, "patch")
        _check_page_patch(patch)
        if not patch:
            return []
        refuse_unknown_fields(patch, _page_fields(self.ctx, page), what)
        return apply_patch(page, patch, self.ctx.codec)

    def set_commands(self, page: RubyObject, raw_op: dict[str, Any],
                     op: str) -> bool:
        """Put an op's commands on a page. True if it had any to put."""
        entries = _commands_arg(raw_op, op)
        if entries is None:
            return False
        page.set("list", _build_list(self.ctx, entries))
        return True


def _op_create(request: _EventRequest, raw_op: dict[str, Any],
               op: str) -> dict[str, Any]:
    """A new event, with its first page."""
    used = request.used_ids
    # `or next(...)` treated an explicit 0 as "choose one for me", so a caller
    # naming an id it should have been told was invalid silently got a
    # different event.
    event_id = opt_int(raw_op, "event_id", minimum=1, maximum=MAX_EVENT_ID)
    if event_id is None:
        event_id = next((i for i in range(1, MAX_EVENT_ID + 1)
                         if i not in used), None)
        if event_id is None:
            raise ToolError("this map already has %d events, which is as "
                            "many as it can hold" % MAX_EVENT_ID)
    elif event_id in used:
        raise ToolError("event %d already exists on this map" % event_id)

    x = opt_int(raw_op, "x", 0, minimum=0) or 0
    y = opt_int(raw_op, "y", 0, minimum=0) or 0
    _check_bounds(x, y, request.width, request.height)
    event = normalize_strings(
        new_event(event_id, opt_str(raw_op, "name") or "", x, y),
        request.ctx.codec)
    if opt_str(raw_op, "name"):
        # Again, through apply_patch: `new_event` puts the name in as UTF-8
        # from the template, and normalize_strings re-encodes it, but a name
        # this project's code page cannot hold has to be refused rather than
        # mangled -- which is what apply_patch's conversion does. The same
        # reasoning as _create_map's second write of a map name.
        apply_patch(event, {"name": raw_op["name"]}, request.ctx.codec)

    page = event.get("pages")[0]
    changes = request.patch_page(page, raw_op, "a new event's first page")
    request.set_commands(page, raw_op, op)
    request.add(event_id, event)
    return {"op": op, "event_id": event_id, "x": x, "y": y, "changes": changes,
            "summary": "created event %d" % event_id}


def _op_delete(request: _EventRequest, raw_op: dict[str, Any],
               op: str) -> dict[str, Any]:
    event_id, _event = request.event(raw_op, op)
    request.remove(event_id)
    return {"op": op, "event_id": event_id,
            "summary": "deleted event %d" % event_id}


def _op_move(request: _EventRequest, raw_op: dict[str, Any],
             op: str) -> dict[str, Any]:
    event_id, event = request.event(raw_op, op)
    x = opt_int(raw_op, "x", event.get("x"))
    y = opt_int(raw_op, "y", event.get("y"))
    _check_bounds(x, y, request.width, request.height)
    event.set("x", x)
    event.set("y", y)
    return {"op": op, "event_id": event_id, "x": x, "y": y,
            "summary": "moved event %d to (%d, %d)" % (event_id, x, y)}


def _op_update(request: _EventRequest, raw_op: dict[str, Any],
               op: str) -> dict[str, Any]:
    """An event's name and position. The page is set_page's business."""
    event_id, event = request.event(raw_op, op)
    # Resolved and checked before anything is written: patching x and then
    # finding y out of bounds left the event half-moved in the cached map.
    wanted = {}
    for field in ("x", "y"):
        if raw_op.get(field) is not None:
            wanted[field] = opt_int(raw_op, field, minimum=0)
    if not wanted and opt_str(raw_op, "name") is None:
        raise ToolError("update needs a name, x or y")
    _check_bounds(wanted.get("x", event.get("x")),
                  wanted.get("y", event.get("y")),
                  request.width, request.height)

    changes = []
    if opt_str(raw_op, "name") is not None:
        changes += apply_patch(event, {"name": raw_op["name"]}, request.ctx.codec)
    for field, value in wanted.items():
        changes += apply_patch(event, {field: value}, request.ctx.codec)
    # No second "nothing changed" test here. `apply_patch` appends a change
    # line for every assignment it makes, and the guard above already refused
    # the call that names none of the three fields -- so the test that used to
    # sit here could not fire, while repeating that refusal's wording eleven
    # lines below it.
    return {"op": op, "event_id": event_id, "changes": changes,
            "summary": "updated event %d" % event_id}


def _op_add_page(request: _EventRequest, raw_op: dict[str, Any],
                 op: str) -> dict[str, Any]:
    event_id, event = request.event(raw_op, op)
    pages = request.pages(event, event_id)
    page = normalize_strings(new_page(), request.ctx.codec)
    # The page being built is not in `pages` yet, so the index it will land at
    # is len(pages). Naming the index of a page that already exists here
    # raised UnboundLocalError, a crash, in place of a refusal.
    changes = request.patch_page(
        page, raw_op, "the new page %d of event %d" % (len(pages), event_id))
    request.set_commands(page, raw_op, op)
    pages.append(page)
    event.set("pages", pages)
    return {"op": op, "event_id": event_id, "page": len(pages) - 1,
            "changes": changes,
            "summary": "added page %d to event %d" % (len(pages) - 1, event_id)}


def _op_delete_page(request: _EventRequest, raw_op: dict[str, Any],
                    op: str) -> dict[str, Any]:
    event_id, event = request.event(raw_op, op)
    pages = request.pages(event, event_id)
    page_index = request.page_index(raw_op, pages, event_id)
    if len(pages) == 1:
        raise ToolError(
            "event %d has only one page; delete the event itself instead"
            % event_id)
    pages.pop(page_index)
    event.set("pages", pages)
    return {"op": op, "event_id": event_id, "page": page_index,
            "summary": "deleted page %d of event %d" % (page_index, event_id)}


def _op_set_page(request: _EventRequest, raw_op: dict[str, Any],
                 op: str) -> dict[str, Any]:
    event_id, event = request.event(raw_op, op)
    pages = request.pages(event, event_id)
    page_index, page = request.page(raw_op, pages, event_id)
    changes = request.patch_page(
        page, raw_op, "page %d of event %d" % (page_index, event_id))
    if request.set_commands(page, raw_op, op):
        changes.append("replaced the command list")
    if not changes:
        raise ToolError("set_page needs a patch or commands")
    return {"op": op, "event_id": event_id, "page": page_index,
            "changes": changes,
            "summary": "updated page %d of event %d" % (page_index, event_id)}


def _op_write_commands(request: _EventRequest, raw_op: dict[str, Any],
                     op: str) -> dict[str, Any]:
    event_id, event = request.event(raw_op, op)
    pages = request.pages(event, event_id)
    page_index, page = request.page(raw_op, pages, event_id)
    entries = _commands_arg(raw_op, op, required=True)
    count = _write_commands(request.ctx, page, entries, op)
    summary = ("rewrote page %d of event %d" if op == "set_commands"
               else "appended to page %d of event %d") % (page_index, event_id)
    return {"op": op, "event_id": event_id, "page": page_index,
            "commands": count, "summary": summary}


# One entry per op name in EVENT_OP_ARGS, which is what `one_of` offers and
# what the schema advertises. A test holds the three to each other.
_EVENT_OPS = {
    "create": _op_create,
    "update": _op_update,
    "delete": _op_delete,
    "move": _op_move,
    "set_page": _op_set_page,
    "add_page": _op_add_page,
    "delete_page": _op_delete_page,
    "set_commands": _op_write_commands,
    "append_commands": _op_write_commands,
}


def _apply_event_op(request: "_EventRequest", raw_op: dict[str, Any],
                    where: str = "ops") -> dict[str, Any]:
    """Run one event_edit op against the cached map. Saves nothing.

    Takes the request rather than building one: the index it carries is
    per *call*, and constructing it here made that comment false and left
    the `MAX_OPS` multiplier it describes in place.
    """
    op = one_of(raw_op, "op", list(EVENT_OP_ARGS))
    check_op_args(op, raw_op, EVENT_OP_ARGS, where)
    return _EVENT_OPS[op](request, raw_op, op)


def _edit_common_event(ctx: Context, common_id: int, ops: list[Any],
                       dry_run: bool) -> dict[str, Any]:
    common = ctx.project.get_entry("common_events", common_id)
    if common is None:
        known = [i for i, _ in ctx.project.entries("common_events")]
        raise ToolError(
            "there is no common event %d; this project has %s"
            % (common_id, _range_text(known)))
    if not isinstance(common, RubyObject):
        raise ToolError(
            "common event %d does not hold an RPG::CommonEvent object, so it "
            "cannot be edited. Run validate to see what is wrong with the file."
            % common_id)
    filename = ctx.project.filename_for("common_events")
    with ctx.staged(filename) as write_landed:
        return _apply_common_ops(ctx, common_id, common, filename, ops,
                                 dry_run, write_landed)


def _apply_common_ops(ctx: Context, common_id: int, common: RubyObject,
                      filename: str, ops: list[Any], dry_run: bool,
                      write_landed: StagedWrite) -> dict[str, Any]:
    applied = []
    for index, raw_op in enumerate(ops):
        op = one_of(raw_op, "op", list(COMMON_EVENT_OP_ARGS))
        check_op_args(op, raw_op, COMMON_EVENT_OP_ARGS, "ops[%d]" % index)
        if op == "update":
            patch = opt_dict(raw_op, "patch")
            if opt_str(raw_op, "name") is not None:
                patch = dict(patch, name=raw_op["name"])
            if not patch:
                raise ToolError("update needs a patch or name")
            _check_page_patch(patch)
            refuse_unknown_fields(patch,
                                  known_fields(ctx, common, "RPG::CommonEvent"),
                                  "common event %d" % common_id)
            applied.append({"op": op,
                            "changes": apply_patch(common, patch, ctx.codec)})
            continue
        entries = _commands_arg(
            raw_op, op, required=True,
            remedy="set its trigger to none in the editor, which is how a "
                   "common event is turned off")
        applied.append({"op": op,
                        "commands": _write_commands(ctx, common, entries, op)})

    write = write_landed.save(
        filename, dry_run=dry_run, tool="event_edit",
        summary="edited common event %d" % common_id,
    )
    return write_reply({"common_event_id": common_id, "applied": applied,
                        "write": write}, dry_run)


def _write_commands(ctx: Context, holder: RubyObject, entries: list[Any],
                    op: str) -> int:
    """Put a command list on whatever holds one, and say how long it now is.

    A page and a common event both hold their commands in @list and both take
    the same two ops, so the rule -- "set_commands" replaces, anything else
    appends before the terminator -- is decided here rather than once per
    holder. It had been written out twice, and only one copy would have been
    found by anyone changing what appending means.
    """
    if op == "set_commands":
        holder.set("list", _build_list(ctx, entries))
    else:
        holder.set("list", _appended(ctx, holder.get("list"), entries))
    return len(holder.get("list"))


def _appended(ctx: Context, existing: Any, entries: list[Any]) -> list[Any]:
    """``existing`` with its trailing blank command dropped, then ``entries``.

    New commands go before the terminator, never after it: the interpreter
    reads a blank command as "this page is finished", so anything past one is
    unreachable. Written once, because the two append paths that need it would
    otherwise have to be fixed together whenever the convention changes.
    """
    kept = list(existing or [])
    while kept and isinstance(kept[-1], RubyObject) and \
            (kept[-1].get("code", CODE_BLANK) or CODE_BLANK) == CODE_BLANK:
        kept.pop()
    combined = kept + _build_list(ctx, entries)
    # Checked again on the whole list: _build_list only ever sees the part
    # being added, so appending a little at a time to an already-long page
    # would otherwise never trip the ceiling.
    _check_list_length(combined)
    return combined


def _build_list(ctx: Context, entries: list[Any]) -> list[RubyObject]:
    """Compile DSL entries into a command list the interpreter will accept."""
    try:
        commands = compile_commands(entries, ctx.codec)
    except CommandError as exc:
        raise ToolError(str(exc)) from exc
    commands = _insert_move_route_steps(commands)
    # Every list ends with a blank command at indent 0.
    commands.append(new_event_command(0, 0, []))
    _check_list_length(commands)
    return commands


def _check_list_length(commands: list[Any]) -> None:
    """Refuse a command list longer than a page should ever hold.

    Nesting depth and the number of ops per call were bounded and the compiled
    length was not, so one large request -- up to MAX_OPS append_commands ops
    inside the transport's own message ceiling -- could write a page carrying
    far more commands than the editor will open, with no cap and no warning.
    Counted after compiling, because one DSL entry becomes several commands.
    """
    if len(commands) > MAX_COMMANDS_PER_LIST:
        raise ToolError(
            "that would leave %d commands on one page, above the %d this "
            "server writes. The editor becomes unusable long before this, and "
            "a page this long is almost always a loop that should be a common "
            "event: move the body into one with db_edit and call it."
            % (len(commands), MAX_COMMANDS_PER_LIST))


def _insert_move_route_steps(commands: list[RubyObject]) -> list[RubyObject]:
    """Add the 505 lines the editor writes beside a Set Move Route command.

    The interpreter ignores them, but without them the editor shows an empty
    move route where the steps should be.
    """
    out: list[RubyObject] = []
    for index, command in enumerate(commands):
        out.append(command)
        if command.get("code") != 205:
            continue
        following = commands[index + 1] if index + 1 < len(commands) else None
        if isinstance(following, RubyObject) and following.get("code") == 505:
            continue
        params = as_list(command.get("parameters"))
        route = params[1] if len(params) > 1 else None
        if isinstance(route, RubyObject) and route.cls == "RPG::MoveRoute":
            out.extend(move_route_steps(route, command.get("indent", 0) or 0))
    return out


@tool(
    "command_reference",
    "Look up event command codes",
    "The event command codes this project's engine dispatches, and how to "
    "write them with event_edit. Search by name or look up a single code to "
    "see its parameter layout. For an engine outside the VX family (only "
    "reachable with --allow-other-engines) the codes and layouts reported are "
    "VX Ace's, which are not that engine's -- every reply says which it is in "
    "\"layouts_are_this_engines\", because a parameter layout believed to be "
    "confirmed is how a caller writes a command the interpreter misreads.",
    schema({
        "code": prop(INT, "Look up one command code."),
        "search": prop(STR, "Find commands whose name contains this text."),
        "move_commands": prop(BOOL, "List move route command codes instead."),
    }),
)
def command_reference(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """The command codes this project's engine dispatches. Reads only.

    `command_table` and `param_specs` fall back to VX Ace's tables for an
    engine this server does not model. That is right for reading -- an
    unmodelled engine is still mostly describable -- and wrong to present as
    confirmed, which is what this tool did: it never asked. So every reply now
    says whether the layouts really are this engine's, from the same property
    analysis.py and _create_map ask.
    """
    engine = ctx.project.engine
    specs = param_specs(engine)
    table = command_table(engine)
    modelled = ctx.project.command_layouts_modelled
    borrowed = None if modelled else (
        "This is a %s project, which this server does not model. The codes "
        "and parameter layouts below are VX Ace's and are not confirmed for "
        "this engine -- XP's Show Text, for one, keeps the message text where "
        "Ace keeps the face graphic. Read a command out of an event that "
        "already works (event_get with include_raw) before authoring one."
        % engine)

    if opt_bool(args, "move_commands"):
        refuse_inert(args, ("code", "search"),
                     "move_commands lists the move route codes, which are a "
                     "separate set from the command codes, so it")
        # Below `modelled`, not above it: this branch used to return first and
        # so was the one reply that omitted the key the description promises
        # every reply carries. A move route's codes are borrowed for an
        # unmodelled engine exactly as the command codes are.
        moves: dict[str, Any] = {
            "move_commands": [{"code": code, "name": name}
                              for code, name in sorted(MOVE_COMMAND_NAMES.items())],
            "layouts_are_this_engines": modelled,
            "note": "Move command codes come from the shared XP/VX/Ace move "
                    "command set rather than the VX reference manual. Confirm "
                    "against an existing route before relying on one.",
        }
        if borrowed:
            moves["engine_warning"] = borrowed
        return moves

    code = opt_int(args, "code")
    if code is not None:
        refuse_inert(args, ("search",),
                     "a lookup of one code answers for that code alone, so it")
        entry: dict[str, Any] = {"code": code, "name": command_name(code)}
        # A list, because both notes can apply at once: a code this engine
        # does not dispatch AND whose layout is unrecorded used to lose the
        # first message to the second, leaving the model hunting for an example
        # of a command its engine cannot run.
        notes: list[str] = []
        if code not in table:
            notes.append(
                "%s does not dispatch this code, so it should not appear in "
                "this project's events." % ctx.project.profile.label)
        if code in specs:
            entry["parameters"] = specs[code]
        else:
            entry["parameters"] = None
            notes.append("This command's parameter layout is not recorded here. "
                         "Read an event that already uses it (event_get with "
                         "include_raw) and copy the shape.")
        entry["layouts_are_this_engines"] = modelled
        if borrowed:
            notes.append(borrowed)
        if notes:
            # `layout_notes`, because `notes` was also a *sentence* in
            # `search`'s reply -- one key, two types, and a caller reading
            # either had to know which tool it came from. These are notes
            # about one command's parameter layout, so the key says so.
            entry["layout_notes"] = notes
        return entry

    needle = (opt_str(args, "search") or "").lower()
    rows = [
        {"code": code, "name": name,
         "parameters": specs.get(code),
         "continuation": code in CONTINUATION_CODES}
        for code, name in sorted(table.items())
        if not needle or needle in name.lower()
    ]
    return {
        "engine": ctx.project.profile.label,
        "layouts_are_this_engines": modelled,
        "commands": rows,
        # `search` filters these, so this is the number that matched and not
        # the size of the table. Same rename, same reason as event_get above.
        "matching_commands": len(rows),
        **({"engine_warning": borrowed} if borrowed else {}),
        "authoring": DSL_HELP,
        "indent_rule": "Commands inside a branch or loop sit one indent deeper. "
                       "event_edit handles indentation for if/loop/choices; if you "
                       "write branch commands with raw, set indent yourself.",
    }
