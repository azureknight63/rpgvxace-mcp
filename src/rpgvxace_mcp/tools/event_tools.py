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
from ..convert import apply_patch, normalize_strings, to_json
from ..project import MAX_MAP_ID
from ..rubytypes import RubyHash, RubyObject, RubyString
from ..schema import new_event, new_event_command, new_page
from .registry import (
    refuse_fields,
    ops_arg,
    StagedWrite,
    int_list,
    echo,
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
)

TRIGGERS = {0: "action button", 1: "player touch", 2: "event touch",
            3: "autorun", 4: "parallel process"}

DSL_HELP = (
    "Commands are written as a list of small objects, one per command. "
    'Supported forms: {"text": "line one\\nline two", "face_name": "Actor1"}; '
    '{"comment": "..."}; {"script": "ruby"}; {"switch": 5, "value": "on"}; '
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


def _text(ctx: Context, value: Any) -> str:
    return ctx.codec.decode(value.data) if isinstance(value, RubyString) else ""


def _page_view(ctx: Context, page: RubyObject, index: int,
               include_raw: bool) -> dict[str, Any]:
    commands = page.get("list") or []
    condition = page.get("condition")
    graphic = page.get("graphic")
    view: dict[str, Any] = {
        "page": index,
        "trigger": TRIGGERS.get(page.get("trigger"), page.get("trigger")),
        "trigger_code": page.get("trigger"),
        "priority_type": page.get("priority_type"),
        "move_type": page.get("move_type"),
        "move_speed": page.get("move_speed"),
        "move_frequency": page.get("move_frequency"),
        "walk_anime": page.get("walk_anime"),
        "step_anime": page.get("step_anime"),
        "direction_fix": page.get("direction_fix"),
        "through": page.get("through"),
        "condition": to_json(condition, ctx.codec) if condition is not None else None,
        "graphic": to_json(graphic, ctx.codec) if graphic is not None else None,
        "command_count": len(commands),
        "script": render_script(commands, ctx.codec),
    }
    if include_raw:
        view["commands"] = [to_json(c, ctx.codec) for c in commands]
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
        "event_ids": prop(ARR, "Only these event ids.", items={"type": INT}),
        "name_contains": prop(STR, "Only events whose name contains this text."),
        "page": prop(INT, "Only this page index (0-based)."),
        "include_raw": prop(BOOL, "Include raw command objects as well as the "
                                  "readable script."),
        "refresh": REFRESH,
    }),
)
def event_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Events on a map, or one common event, with rendered scripts."""
    include_raw = opt_bool(args, "include_raw")
    refresh = opt_bool(args, "refresh")
    common_id = opt_int(args, "common_event_id", minimum=1)
    map_id = opt_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    if common_id is not None and map_id is not None:
        # event_edit refuses this pair; reading used to ignore map_id silently,
        # so the same arguments meant two different things in sibling tools.
        raise ToolError(
            "pass exactly one of map_id or common_event_id; both were given, "
            "and map events and common events are read separately")

    if common_id is not None:
        common = ctx.project.get_entry("common_events", common_id, refresh)
        if common is None:
            raise ToolError("there is no common event %d" % common_id)
        commands = common.get("list") or []
        triggers = {0: "none", 1: "autorun", 2: "parallel"}
        result: dict[str, Any] = {
            "common_event_id": common_id,
            "name": _text(ctx, common.get("name")),
            "trigger": triggers.get(common.get("trigger"), common.get("trigger")),
            "switch_id": common.get("switch_id"),
            "command_count": len(commands),
            "script": render_script(commands, ctx.codec),
        }
        if include_raw:
            result["commands"] = [to_json(c, ctx.codec) for c in commands]
        return result

    if map_id is None:
        raise ToolError("pass map_id (for map events) or common_event_id")

    game_map = ctx.project.load_map(map_id, refresh)
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        return {"map_id": map_id, "events": []}

    wanted = set(int_list(args, "event_ids", MAX_EVENT_IDS_PER_CALL,
                          minimum=1, maximum=MAX_EVENT_ID))
    needle = (opt_str(args, "name_contains") or "").lower()
    only_page = opt_int(args, "page")

    out = []
    for event_id, event in events.sorted_items():
        if not isinstance(event, RubyObject):
            continue
        if wanted and event_id not in wanted:
            continue
        name = _text(ctx, event.get("name"))
        if needle and needle not in name.lower():
            continue
        pages = []
        for index, page in enumerate(event.get("pages") or []):
            if only_page is not None and index != only_page:
                continue
            if isinstance(page, RubyObject):
                pages.append(_page_view(ctx, page, index, include_raw))
        out.append({
            "id": event_id, "name": name,
            "x": event.get("x"), "y": event.get("y"),
            "page_count": len(event.get("pages") or []),
            "pages": pages,
        })
    return {"map_id": map_id, "events": out, "total": len(out),
            "note": "VX runs the LAST page whose conditions are satisfied, so a "
                    "later page with no conditions hides the pages before it."}


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
        "ops": prop(ARR, "Operations, applied in order.", items={
            "type": OBJ,
            "properties": {
                "op": {"type": STR, "enum": ["create", "update", "delete", "move",
                                             "set_page", "add_page", "delete_page",
                                             "set_commands", "append_commands"]},
                "event_id": {"type": INT, "description": "Which event to act on."},
                "page": {"type": INT, "description": "Page index, 0-based (default 0)."},
                "name": {"type": STR, "description": "Event name, for create/update."},
                "x": {"type": INT, "description": "Tile X, for create/move."},
                "y": {"type": INT, "description": "Tile Y, for create/move."},
                "patch": {"type": OBJ, "description":
                          "Page fields to set: trigger, priority_type, move_type, "
                          "move_speed, move_frequency, walk_anime, step_anime, "
                          "direction_fix, through, and nested condition/graphic "
                          'objects, e.g. {"trigger": 3, "graphic": '
                          '{"character_name": "People1", "character_index": 2}, '
                          '"condition": {"self_switch_valid": true}}'},
                "commands": {"type": ARR, "description":
                             "Command entries for set_commands/append_commands/create.",
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
    common_id = opt_int(args, "common_event_id", minimum=1)
    map_id = opt_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)

    if common_id is not None and map_id is not None:
        raise ToolError(
            "pass exactly one of map_id or common_event_id; both were given, "
            "and map events and common events are edited separately")
    if common_id is not None:
        return _edit_common_event(ctx, common_id, ops, dry_run)
    if map_id is None:
        raise ToolError("pass map_id (for map events) or common_event_id")

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

    applied = []
    for raw_op in ops:
        applied.append(_apply_event_op(ctx, events, raw_op, width, height))

    write = write_landed.save(
        filename, dry_run=dry_run, tool="event_edit",
        summary="; ".join(str(a.get("summary", a.get("op"))) for a in applied),
    )
    return {"map_id": map_id, "applied": applied, "write": write}


# The highest event id a map will hold. The editor numbers events from 1 and no
# real map comes near this.
MAX_EVENT_ID = 9999

# How many events one read will render. Each carries its whole command list as
# a readable script, so this is a response budget rather than a map limit.
MAX_EVENT_IDS_PER_CALL = 200

# Page fields a patch must not set, because the tool cannot then guarantee what
# it wrote is a page the interpreter can run.
UNPATCHABLE_PAGE_FIELDS = ("list", "id")


def _range_text(ids: list[int]) -> str:
    """The ids that exist, said briefly enough to put in a message."""
    if not ids:
        return "none at all"
    if len(ids) <= 12:
        return ", ".join(str(i) for i in sorted(ids))
    return "%d to %d" % (min(ids), max(ids))


def _check_page_patch(patch: dict[str, Any]) -> None:
    """Refuse a patch that would write a command list the tool did not build.

    A page's @list has invariants the DSL exists to hold -- a Show Text needs
    its 401 lines, a list needs its blank terminator -- and a patch goes
    straight through to the file, so it could leave a page the interpreter
    misreads. `set_commands` is the way to change a command list.
    """
    refuse_fields(
        patch, UNPATCHABLE_PAGE_FIELDS,
        "Use op \"set_commands\" or \"append_commands\" to change what a "
        "page runs, so the command list is built correctly.")


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
    if x < 0 or y < 0 or (width and x >= width) or (height and y >= height):
        raise ToolError(
            "(%d, %d) is outside this map, which is %dx%d. Tile coordinates start "
            "at (0, 0) in the top-left corner." % (x, y, width, height)
        )


def _apply_event_op(ctx: Context, events: RubyHash, raw_op: dict[str, Any],
                    width: int, height: int) -> dict[str, Any]:
    op = one_of(raw_op, "op", ["create", "update", "delete", "move", "set_page",
                               "add_page", "delete_page", "set_commands",
                               "append_commands"])

    if op == "create":
        used = {k for k in events.keys() if isinstance(k, int)}
        # `or next(...)` treated an explicit 0 as "choose one for me", so a
        # caller naming an id it should have been told was invalid silently got
        # a different event.
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
        _check_bounds(x, y, width, height)
        event = normalize_strings(
            new_event(event_id, opt_str(raw_op, "name") or "", x, y), ctx.codec)
        if opt_str(raw_op, "name"):
            apply_patch(event, {"name": raw_op["name"]}, ctx.codec)
        page = event.get("pages")[0]
        changes = []
        patch = opt_dict(raw_op, "patch")
        if patch:
            _check_page_patch(patch)
            changes = apply_patch(page, patch, ctx.codec)
        if raw_op.get("commands") is not None:
            page.set("list", _build_list(ctx, raw_op["commands"]))
        events.set(event_id, event)
        return {"op": op, "event_id": event_id, "x": x, "y": y, "changes": changes,
                "summary": "created event %d" % event_id}

    event_id = opt_int(raw_op, "event_id")
    if event_id is None:
        raise ToolError("%s needs an event_id" % op)
    event = events.get(event_id)
    if not isinstance(event, RubyObject):
        raise ToolError("this map has no event %d" % event_id)

    if op == "delete":
        events.delete(event_id)
        return {"op": op, "event_id": event_id,
                "summary": "deleted event %d" % event_id}

    if op == "move":
        x = opt_int(raw_op, "x", event.get("x"))
        y = opt_int(raw_op, "y", event.get("y"))
        _check_bounds(x, y, width, height)
        event.set("x", x)
        event.set("y", y)
        return {"op": op, "event_id": event_id, "x": x, "y": y,
                "summary": "moved event %d to (%d, %d)" % (event_id, x, y)}

    if op == "update":
        # Resolved and checked first: patching x and then finding y out of
        # bounds left the event half-moved in the cached map.
        wanted = {}
        for field in ("x", "y"):
            if raw_op.get(field) is not None:
                wanted[field] = opt_int(raw_op, field, minimum=0)
        if not wanted and opt_str(raw_op, "name") is None:
            raise ToolError("update needs a name, x or y")
        _check_bounds(wanted.get("x", event.get("x")),
                      wanted.get("y", event.get("y")), width, height)
        changes = []
        if opt_str(raw_op, "name") is not None:
            changes += apply_patch(event, {"name": raw_op["name"]}, ctx.codec)
        for field, value in wanted.items():
            changes += apply_patch(event, {field: value}, ctx.codec)
        if not changes:
            raise ToolError("update needs a name, x or y")
        return {"op": op, "event_id": event_id, "changes": changes,
                "summary": "updated event %d" % event_id}

    pages = event.get("pages") or []

    if op == "add_page":
        page = normalize_strings(new_page(), ctx.codec)
        patch = opt_dict(raw_op, "patch")
        _check_page_patch(patch)
        changes = apply_patch(page, patch, ctx.codec) if patch else []
        if raw_op.get("commands") is not None:
            page.set("list", _build_list(ctx, raw_op["commands"]))
        pages.append(page)
        event.set("pages", pages)
        return {"op": op, "event_id": event_id, "page": len(pages) - 1,
                "changes": changes,
                "summary": "added page %d to event %d" % (len(pages) - 1, event_id)}

    page_index = opt_int(raw_op, "page", 0) or 0
    if not 0 <= page_index < len(pages):
        raise ToolError("event %d has pages 0..%d, not %d"
                        % (event_id, len(pages) - 1, page_index))

    if op == "delete_page":
        if len(pages) == 1:
            raise ToolError(
                "event %d has only one page; delete the event itself instead"
                % event_id)
        pages.pop(page_index)
        event.set("pages", pages)
        return {"op": op, "event_id": event_id, "page": page_index,
                "summary": "deleted page %d of event %d" % (page_index, event_id)}

    page = pages[page_index]
    if not isinstance(page, RubyObject):
        raise ToolError("page %d of event %d is not an RPG::Event::Page"
                        % (page_index, event_id))

    if op == "set_page":
        patch = opt_dict(raw_op, "patch")
        _check_page_patch(patch)
        changes = apply_patch(page, patch, ctx.codec) if patch else []
        if raw_op.get("commands") is not None:
            page.set("list", _build_list(ctx, raw_op["commands"]))
            changes.append("replaced the command list")
        if not changes:
            raise ToolError("set_page needs a patch or commands")
        return {"op": op, "event_id": event_id, "page": page_index, "changes": changes,
                "summary": "updated page %d of event %d" % (page_index, event_id)}

    entries = opt_list(raw_op, "commands")
    if op == "set_commands":
        page.set("list", _build_list(ctx, entries))
        return {"op": op, "event_id": event_id, "page": page_index,
                "commands": len(page.get("list")),
                "summary": "rewrote page %d of event %d" % (page_index, event_id)}

    existing = list(page.get("list") or [])
    # The trailing blank command terminates the list, so new commands go before it.
    while existing and isinstance(existing[-1], RubyObject) and \
            (existing[-1].get("code", 0) or 0) == 0:
        existing.pop()
    page.set("list", existing + _build_list(ctx, entries))
    return {"op": op, "event_id": event_id, "page": page_index,
            "commands": len(page.get("list")),
            "summary": "appended to page %d of event %d" % (page_index, event_id)}


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
    for raw_op in ops:
        op = one_of(raw_op, "op", ["set_commands", "append_commands", "update"])
        if op == "update":
            patch = opt_dict(raw_op, "patch")
            if opt_str(raw_op, "name") is not None:
                patch = dict(patch, name=raw_op["name"])
            if not patch:
                raise ToolError("update needs a patch or name")
            _check_page_patch(patch)
            applied.append({"op": op,
                            "changes": apply_patch(common, patch, ctx.codec)})
            continue
        entries = opt_list(raw_op, "commands")
        if not entries:
            # An empty list would replace the whole body with a single blank
            # command, which is a deletion dressed up as an edit.
            raise ToolError(
                "%s needs at least one command. To empty a common event, set "
                "its trigger to none in the editor instead." % op)
        if op == "set_commands":
            common.set("list", _build_list(ctx, entries))
        else:
            common.set("list", _appended(ctx, common.get("list"), entries))
        applied.append({"op": op, "commands": len(common.get("list"))})

    write = write_landed.save(
        filename, dry_run=dry_run, tool="event_edit",
        summary="edited common event %d" % common_id,
    )
    return {"common_event_id": common_id, "applied": applied, "write": write}


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
    return kept + _build_list(ctx, entries)


def _build_list(ctx: Context, entries: list[Any]) -> list[RubyObject]:
    """Compile DSL entries into a command list the interpreter will accept."""
    try:
        commands = compile_commands(entries, ctx.codec)
    except CommandError as exc:
        raise ToolError(str(exc)) from exc
    commands = _insert_move_route_steps(commands)
    # Every list ends with a blank command at indent 0.
    commands.append(new_event_command(0, 0, []))
    return commands


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
        params = command.get("parameters") or []
        route = params[1] if len(params) > 1 else None
        if isinstance(route, RubyObject) and route.cls == "RPG::MoveRoute":
            out.extend(move_route_steps(route, command.get("indent", 0) or 0))
    return out


@tool(
    "command_reference",
    "Look up event command codes",
    "The event command codes this project's engine dispatches, and how to "
    "write them with "
    "event_edit. Search by name or look up a single code to see its parameter "
    "layout where that layout is confirmed.",
    schema({
        "code": prop(INT, "Look up one command code."),
        "search": prop(STR, "Find commands whose name contains this text."),
        "move_commands": prop(BOOL, "List move route command codes instead."),
    }),
)
def command_reference(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """The command codes this project's engine dispatches. Reads only."""
    if opt_bool(args, "move_commands"):
        return {
            "move_commands": [{"code": code, "name": name}
                              for code, name in sorted(MOVE_COMMAND_NAMES.items())],
            "note": "Move command codes come from the shared XP/VX/Ace move "
                    "command set rather than the VX reference manual. Confirm "
                    "against an existing route before relying on one.",
        }

    engine = ctx.project.engine
    specs = param_specs(engine)
    table = command_table(engine)

    code = opt_int(args, "code")
    if code is not None:
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
        if notes:
            entry["notes"] = notes
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
        "commands": rows,
        "total": len(rows),
        "authoring": DSL_HELP,
        "indent_rule": "Commands inside a branch or loop sit one indent deeper. "
                       "event_edit handles indentation for if/loop/choices; if you "
                       "write branch commands with raw, set indent yourself.",
    }
