"""The `live` tool: run a scratch copy of the game and drive it.

The work is in `rpgvxace_mcp.live`; this module is the tool's surface --
its schema, its argument checks, and the shape of its replies. Every
argument is checked here before anything reaches the game, so a refusal
names the argument rather than arriving as a Ruby error from inside it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..live import bridge, limits
from ..live.limits import LiveError
from ..project import MAX_MAP_ID, ReadOnlyError
from ..schema import on_map
from .registry import (
    ARR,
    BOOL,
    Context,
    INT,
    ImageReply,
    OBJ,
    STR,
    ToolError,
    as_int,
    check_op_args,
    echo,
    need_int,
    need_str,
    one_of,
    opt_bool,
    opt_dict,
    opt_int,
    opt_int_default,
    opt_list,
    partial,
    prop,
    schema,
    tool,
)

if TYPE_CHECKING:
    from ..live import session


# What each op reads.
LIVE_OP_ARGS = {
    "start": {"op", "paused", "skip_title", "allow_eval"},
    "stop": {"op"},
    "status": {"op"},
    "state": {"op", "sections"},
    "pause": {"op"},
    "resume": {"op"},
    "step": {"op", "frames"},
    "input": {"op", "keys", "frames"},
    "set": {"op", "switches", "variables", "self_switches", "gold", "items"},
    "teleport": {"op", "map_id", "x", "y", "direction"},
    "screenshot": {"op"},
    "eval": {"op", "code"},
}


# The ops that are a request to the bridge and nothing more.
FORWARDED_OPS = ("state", "pause", "resume", "step", "input", "set",
                 "teleport", "eval")


# How many frames one step runs, and how long one key press is held.
MAX_STEP_FRAMES = 1200
MAX_HOLD_FRAMES = 600


# How many keys one press holds together, and how many entries one `set`
# changes.
MAX_CHORD_KEYS = 8
MAX_SET_ENTRIES = 500


# The longest Ruby `eval` takes.
MAX_EVAL_CHARS = 20_000


# The range RPG Maker's own editor allows for gold and a variable.
GOLD_LIMIT = 99_999_999
VARIABLE_LIMIT = 99_999_999


# How many frames a step is expected to take per second, and the slack on
# top: a step waits `frames / 60 * 2 + REPLY_SECONDS` before calling the
# game unresponsive, since a game can run slower than its 60 frames.
FRAMES_PER_SECOND = 60


ITEM_KINDS = ("item", "weapon", "armor")
SELF_SWITCH_LETTERS = ("A", "B", "C", "D")
DIRECTIONS = (2, 4, 6, 8)


_SET_KEYS = ("switches", "variables", "self_switches", "gold", "items")


# The refusal when the operator's half of the eval gate is not turned. A
# model cannot turn it, so it says who can and how.
_EVAL_NOT_ALLOWED = (
    "eval is off on this server: it runs arbitrary Ruby inside the game "
    "process, so it needs the server started with --allow-live-eval, which "
    "only the person who configured this server can add to its command "
    "line.")


@tool(
    "live",
    "Run and drive a test copy of the game",
    "Play-test the game under your control. `start` copies the project to "
    "a scratch folder (never the project itself: save files and this "
    "server's backups are left out) and runs that copy's Game.exe with a "
    "bridge script injected above Main; the window appears on the desktop. "
    "One session at a time; `stop` ends the game and deletes the copy, and "
    "the game also ends if this server does. VX Ace only, Windows only.\n\n"
    "Ops: `status`; `state` (map, player x/y/direction, party, gold, items, "
    "switches, variables, self switches, the running event and the message "
    "window -- each section lists at most %d entries); `pause`, `resume`, "
    "and `step` (run exactly `frames` frames, at most %d, then pause -- "
    "this is what makes a test deterministic); `input` (hold virtual keys "
    "for `frames` frames, at most %d; presses queue one after another with "
    "one released frame between them, so press then `step` to see the "
    "effect; nothing is sent to the operating system); `set` switches, "
    "variables, self switches, gold and item counts for test setup (at "
    "most %d entries); `teleport` the player (waits up to %d frames for "
    "the transfer); `screenshot` of the game window (a PNG, halved until "
    "it is under %d bytes); `eval` a Ruby string in the game (at most %d "
    "characters; only when this server was started with "
    "--allow-live-eval by the person who configured it AND the session "
    "was started with allow_eval -- `status` and project_info say whether "
    "the server allows it).\n\n"
    "Waits are bounded: a game must come up within %g seconds and answer "
    "each request within %g (a step waits in proportion to its frames). "
    "A Ruby exception in the game is caught and reported as `crash` with "
    "its backtrace -- script slots above Main keep the project's numbers "
    "-- and the game keeps answering state and eval so it can be "
    "inspected. A dialog the game shows is read back in the refusal. "
    "Text from the game is cut at %d characters. A project of more than "
    "%d files or %d bytes is not copied. Refused entirely under "
    "--read-only."
    % (bridge.MAX_STATE_ENTRIES, MAX_STEP_FRAMES, MAX_HOLD_FRAMES,
       MAX_SET_ENTRIES, bridge.TELEPORT_FRAMES,
       limits.MAX_SCREENSHOT_BYTES, MAX_EVAL_CHARS, limits.BOOT_SECONDS,
       limits.REPLY_SECONDS, bridge.MAX_BRIDGE_TEXT,
       limits.MAX_SCRATCH_FILES, limits.MAX_SCRATCH_BYTES),
    schema({
        "op": prop(STR, "What to do.", enum=list(LIVE_OP_ARGS)),
        "paused": prop(BOOL, "For start: pause at the first frame, so "
                             "nothing happens until you step."),
        "skip_title": prop(BOOL, "For start: go straight to a new game, "
                                 "skipping the title screen (default "
                                 "true)."),
        "allow_eval": prop(BOOL, "For start: let eval run Ruby in this "
                                 "session. Off unless asked for, and refused "
                                 "unless the server was started with "
                                 "--allow-live-eval."),
        "sections": prop(ARR, "For state: which sections (default all).",
                         items={"type": STR,
                                "enum": list(bridge.STATE_SECTIONS)},
                         maxItems=len(bridge.STATE_SECTIONS)),
        "frames": prop(INT, "For step: frames to run (default 1, at most "
                            "%d). For input: frames to hold the keys "
                            "(default 1, at most %d)."
                       % (MAX_STEP_FRAMES, MAX_HOLD_FRAMES),
                       minimum=1, maximum=MAX_STEP_FRAMES),
        "keys": prop(ARR, "For input: keys held together, by RGSS name: "
                          + ", ".join(bridge.KEYS)
                          + ". C is confirm, B is cancel. At most %d."
                          % MAX_CHORD_KEYS,
                     items={"type": STR, "enum": list(bridge.KEYS)},
                     maxItems=MAX_CHORD_KEYS),
        "switches": prop(OBJ, "For set: switch id -> true or false, e.g. "
                              "{\"12\": true}."),
        "variables": prop(OBJ, "For set: variable id -> whole number "
                               "between -%d and %d."
                          % (VARIABLE_LIMIT, VARIABLE_LIMIT)),
        "self_switches": prop(ARR, "For set: self switches to change.",
                              items={"type": OBJ, "properties": {
                                  "map_id": prop(INT, "The map."),
                                  "event_id": prop(INT, "The event."),
                                  "letter": prop(STR, "A, B, C or D.",
                                                 enum=list(SELF_SWITCH_LETTERS)),
                                  "value": prop(BOOL, "On or off."),
                              }},
                              maxItems=MAX_SET_ENTRIES),
        "gold": prop(INT, "For set: the party's gold.", minimum=0,
                     maximum=GOLD_LIMIT),
        "items": prop(ARR, "For set: how many of an item, weapon or armor "
                           "the party holds.",
                      items={"type": OBJ, "properties": {
                          "kind": prop(STR, "item, weapon or armor.",
                                       enum=list(ITEM_KINDS)),
                          "id": prop(INT, "The database id.", minimum=1),
                          "count": prop(INT, "How many (0 to 99).",
                                        minimum=0, maximum=99),
                      }},
                      maxItems=MAX_SET_ENTRIES),
        "map_id": prop(INT, "For teleport: the map.", minimum=1,
                       maximum=MAX_MAP_ID),
        "x": prop(INT, "For teleport: the column.", minimum=0),
        "y": prop(INT, "For teleport: the row.", minimum=0),
        "direction": prop(INT, "For teleport: 2 down, 4 left, 6 right, "
                               "8 up (default: unchanged)."),
        "code": prop(STR, "For eval: Ruby, evaluated at the top level. The "
                          "reply is its `inspect`.",
                     maxLength=MAX_EVAL_CHARS),
    }, ["op"]),
    writes=True,
)
def live(ctx: Context, args: dict[str, Any]) -> Any:
    """Dispatch one live op. A failure in the session is a tool error."""
    op = one_of(args, "op", list(LIVE_OP_ARGS))
    check_op_args(op, args, LIVE_OP_ARGS, "live")
    try:
        return _LIVE_OPS[op](ctx, args)
    except LiveError as exc:
        raise ToolError(str(exc)) from exc


def _session():
    """The `live.session` module, loaded on first use.

    It is the Windows side (`ctypes`, the screenshot path) and a server that
    never starts a game should not pay for it at start-up; the tool's schema
    and its error handling need only `live.limits`. Tests that shorten
    `session.REPLY_SECONDS` still reach the module this returns.
    """
    from ..live import session

    return session


def _running() -> "session.Session":
    found = _session().current()
    if found is None:
        raise ToolError("no live session is running; call live with "
                        "op \"start\" first")
    return found


def _start(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    if ctx.project.read_only:
        raise ReadOnlyError(
            "this server is running read-only, so no game was started. A "
            "live session runs a copy and never writes the project, but "
            "--read-only means nothing is launched either.")
    if ctx.project.engine != "vx_ace":
        raise ToolError("live sessions drive RPG Maker VX Ace games; this "
                        "project is %s" % ctx.project.profile.label)
    allow_eval = opt_bool(args, "allow_eval")
    if allow_eval and not ctx.project.allow_live_eval:
        raise ToolError(_EVAL_NOT_ALLOWED + " No game was started; start "
                        "again without allow_eval to play-test without it.")
    return _session().start(ctx.project.root, {
        "paused": opt_bool(args, "paused"),
        "skip_title": opt_bool(args, "skip_title", True),
        "allow_eval": allow_eval,
    })


def _stop(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    return _session().stop()


def _status(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    found = _session().current()
    allowed = {"server_allows_eval": ctx.project.allow_live_eval}
    if found is None:
        return {"running": False, **allowed}
    out = dict(found.describe(), **allowed)
    if found.alive():
        try:
            out.update(found.request("ping", seconds=min(
                2.0, _session().REPLY_SECONDS)))
        except LiveError as exc:
            out.update(partial("ping", notes=(str(exc),)))
    return out


def _state(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    sections = opt_list(args, "sections")
    reply = _running().request("state", {"sections": sections} if sections
                               else {})
    for section in reply.pop("truncated", None) or ():
        reply.update(partial(str(section), truncated=True))
    errors = reply.pop("errors", None)
    if errors:
        reply.update(partial("sections", not_read=errors))
    return reply


def _pause(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    return _running().request("pause")


def _resume(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    return _running().request("resume")


def _step(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    frames = opt_int_default(args, "frames", 1, 1, MAX_STEP_FRAMES)
    seconds = _session().REPLY_SECONDS + 2.0 * frames / FRAMES_PER_SECOND
    return _running().request("step", {"frames": frames}, seconds)


def _input(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    keys = opt_list(args, "keys")
    if not keys:
        raise ToolError("input needs keys, e.g. [\"C\"]")
    unknown = [key for key in keys if key not in bridge.KEYS]
    if unknown:
        raise ToolError("unknown key%s %s; RGSS names its keys %s"
                        % ("s" if len(unknown) > 1 else "",
                           ", ".join(echo(key) for key in unknown),
                           ", ".join(bridge.KEYS)))
    frames = opt_int_default(args, "frames", 1, 1, MAX_HOLD_FRAMES)
    return _running().request("input", {"keys": list(keys),
                                        "frames": frames})


def _ids(args: dict[str, Any], key: str, check: Any) -> dict[int, Any]:
    """A `{"12": value}` argument as `{12: value}`, every entry checked."""
    given = opt_dict(args, key)
    if len(given) > MAX_SET_ENTRIES:
        raise ToolError("%s names %d entries; one set changes at most %d"
                        % (key, len(given), MAX_SET_ENTRIES))
    out = {}
    for name, value in given.items():
        if not (isinstance(name, str) and name.isdigit() and int(name) >= 1):
            raise ToolError("%s is keyed by id, a whole number from 1; got "
                            "%s" % (key, echo(name)))
        out[int(name)] = check("%s[%s]" % (key, name), value)
    return out


def _a_bool(what: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ToolError("%s must be true or false, got %s" % (what, echo(value)))
    return value


def _a_variable(what: str, value: Any) -> int:
    return as_int(value, what, -VARIABLE_LIMIT, VARIABLE_LIMIT)


def _set(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    if all(args.get(key) is None for key in _SET_KEYS):
        raise ToolError("set needs at least one of %s" % ", ".join(_SET_KEYS))
    request: dict[str, Any] = {
        "switches": _ids(args, "switches", _a_bool),
        "variables": _ids(args, "variables", _a_variable),
    }
    self_switches = []
    for index, entry in enumerate(opt_list(args, "self_switches")):
        where = "self_switches[%d]" % index
        if not isinstance(entry, dict):
            raise ToolError("%s must be an object" % where)
        self_switches.append([
            need_int(entry, "map_id", 1, MAX_MAP_ID),
            need_int(entry, "event_id", 1),
            one_of(entry, "letter", list(SELF_SWITCH_LETTERS)),
            _a_bool(where + ".value", entry.get("value")),
        ])
    items = []
    for index, entry in enumerate(opt_list(args, "items")):
        if not isinstance(entry, dict):
            raise ToolError("items[%d] must be an object" % index)
        items.append([one_of(entry, "kind", list(ITEM_KINDS)),
                      need_int(entry, "id", 1),
                      need_int(entry, "count", 0, 99)])
    request["self_switches"] = self_switches
    request["items"] = items
    gold = opt_int(args, "gold", None, 0, GOLD_LIMIT)
    if gold is not None:
        request["gold"] = gold
    return _running().request("set", request)


def _teleport(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    map_id = need_int(args, "map_id", 1, MAX_MAP_ID)
    x = need_int(args, "x", 0)
    y = need_int(args, "y", 0)
    direction = opt_int(args, "direction")
    if direction is not None and direction not in DIRECTIONS:
        raise ToolError("direction must be 2, 4, 6 or 8, got %d" % direction)
    if map_id not in ctx.project.map_ids_on_disk():
        raise ToolError("map %d does not exist in this project" % map_id)
    game_map = ctx.project.load_map(map_id)
    width, height = game_map.get("width"), game_map.get("height")
    if not (isinstance(width, int) and isinstance(height, int)
            and on_map(x, y, width, height)):
        raise ToolError("(%d, %d) is outside map %d, which is %sx%s"
                        % (x, y, map_id, width, height))
    request = {"map_id": map_id, "x": x, "y": y}
    if direction is not None:
        request["direction"] = direction
    seconds = (_session().REPLY_SECONDS
               + 2.0 * bridge.TELEPORT_FRAMES / FRAMES_PER_SECOND)
    return _running().request("teleport", request, seconds)


def _screenshot(ctx: Context, args: dict[str, Any]) -> ImageReply:
    data, info = _running().screenshot()
    return ImageReply(info, data)


def _eval(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Run Ruby in the game, if both the server and the session allow it.

    The server's flag is read here on every call, not copied into the
    session, so it is the operator's setting that decides and not what it
    was when the game started. The session's flag is checked here and
    again by the bridge, which read it from the session's config before
    the game ran a frame: the first check is the clear refusal, the second
    is the one a stale server cannot skip. When both are missing the
    refusal names both, so a caller does not fix one and meet the other.
    """
    running = _running()
    missing = []
    if not ctx.project.allow_live_eval:
        missing.append(_EVAL_NOT_ALLOWED)
    if not running.options.get("allow_eval"):
        missing.append("eval is off in this session. It runs arbitrary Ruby "
                       "inside the game process, so it is only on when "
                       "asked for: stop, then start with allow_eval: true.")
    if missing:
        raise ToolError(" ".join(missing))
    return running.request("eval", {"code": need_str(args, "code")})


_LIVE_OPS = {
    "start": _start,
    "stop": _stop,
    "status": _status,
    "state": _state,
    "pause": _pause,
    "resume": _resume,
    "step": _step,
    "input": _input,
    "set": _set,
    "teleport": _teleport,
    "screenshot": _screenshot,
    "eval": _eval,
}
