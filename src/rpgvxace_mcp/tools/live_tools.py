"""The `live` tool: run a scratch copy of the game and drive it.

The work is in `rpgvxace_mcp.live`; this module is the tool's surface --
its schema, its argument checks, and the shape of its replies. Every
argument is checked here before anything reaches the game, so a refusal
names the argument rather than arriving as a Ruby error from inside it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ..live import bridge, limits, playtest
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
    REGISTRY,
    STR,
    ToolError,
    as_int,
    check_args,
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
    "walk_to": {"op", "x", "y", "map_id", "frames_per_step", "max_frames",
                "max_seconds"},
    "interact": {"op", "direction", "event_id", "frames", "frames_per_step",
                 "max_frames", "max_seconds"},
    "scenario": {"op", "steps", "max_frames", "max_seconds"},
    "screenshot": {"op"},
    "eval": {"op", "code"},
}


# The ops that are a request to the bridge and nothing more.
FORWARDED_OPS = ("state", "pause", "resume", "step", "input", "set",
                 "teleport", "eval")


# The ops a scenario step may name: every one that leaves the session as it
# found it. `start` and `stop` change which game is running, `eval` runs
# arbitrary Ruby behind a gate that only the operator can open, and a
# scenario inside a scenario is a loop the step ceiling would not bound.
SCENARIO_OPS = ("status", "state", "pause", "resume", "step", "input", "set",
                "teleport", "walk_to", "interact", "screenshot")


# The largest column, row or event id a walk or an interaction names. A map
# is at most 500 tiles a side and has far fewer events than this.
MAX_TILE_INDEX = 9999
MAX_LIVE_EVENT_ID = 9999


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
    "Play-testing ops, built on those. `walk_to` (x, y; map_id only checks "
    "which map the player is on): routes over the game's own passability "
    "(blocking events and vehicles included; the whole map when it has at "
    "most %d cells, else a window around the player and the target), then "
    "walks it as a closed loop -- press a direction, step "
    "`frames_per_step` frames (default %d, at most %d), look where the "
    "player is, repeat -- and replies with the planned `path`, `steps_taken`, "
    "the final `player` and a `stop_reason`: arrived, blocked (did not "
    "move), moved_elsewhere (a transfer or an event moved the player), "
    "message_open, battle, scene_changed, frame_budget, time_budget or "
    "no_path. A route over %d tiles is refused before the player moves. "
    "`max_frames` (default %d, at most %d) and `max_seconds` (default %d, "
    "at most %d) bound it; the clock is read between requests, so a call "
    "can run one request's wait past `max_seconds`. `interact`: face "
    "`direction` where the player stands, or give `event_id` to walk to the "
    "nearest tile beside that event and face it (with neither, press facing "
    "the way the player already faces), press C once, step `frames` frames "
    "(default %d), and reply with whether a message opened and `changes` -- "
    "switches, variables and gold before against after, at most %d entries "
    "each. It does not advance an open message: press C with `input`. "
    "`scenario`: `steps` (at most %d), each {op, args, expect}, run in "
    "order. Any op but start, stop, eval and scenario is allowed, so a "
    "scenario cannot reach eval. `expect` holds at most %d comparisons "
    "against the game after the step: map_id, x, y, direction, gold, "
    "party_size (a number, or {eq|ne|gt|ge|lt|le: number}), switches "
    "({id: true|false}) and variables ({id: number, or a comparison}). It "
    "stops at the first step that fails or errors and says which and why "
    "(expected against actual); `max_frames` and `max_seconds` bound the "
    "whole scenario (defaults %d and %d, same ceilings), and the step "
    "results are cut at %d characters in total. walk_to, interact and "
    "scenario leave the game paused, as `step` does; coordinates and event "
    "ids are at most %d.\n\n"
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
       limits.MAX_SCREENSHOT_BYTES, MAX_EVAL_CHARS,
       bridge.MAX_PASS_CELLS, playtest.DEFAULT_FRAMES_PER_STEP,
       playtest.MAX_FRAMES_PER_STEP, playtest.MAX_WALK_PATH,
       playtest.DEFAULT_WALK_FRAMES, playtest.MAX_PLAYTEST_FRAMES,
       playtest.DEFAULT_WALK_SECONDS, playtest.MAX_PLAYTEST_SECONDS,
       playtest.DEFAULT_INTERACT_FRAMES, playtest.MAX_DIFF_ENTRIES,
       playtest.MAX_SCENARIO_STEPS, playtest.MAX_EXPECT_CHECKS,
       playtest.DEFAULT_SCENARIO_FRAMES, playtest.DEFAULT_SCENARIO_SECONDS,
       playtest.MAX_SCENARIO_RESULT_CHARS, MAX_TILE_INDEX,
       limits.BOOT_SECONDS,
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
                            "(default 1, at most %d). For interact: "
                            "frames to run after the action key (default "
                            "%d, at most %d)."
                       % (MAX_STEP_FRAMES, MAX_HOLD_FRAMES,
                          playtest.DEFAULT_INTERACT_FRAMES, MAX_STEP_FRAMES),
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
        "map_id": prop(INT, "For teleport: the map. For walk_to: the map the "
                            "player must be on (a check; nothing is pressed "
                            "if it is not).", minimum=1, maximum=MAX_MAP_ID),
        "x": prop(INT, "For teleport and walk_to: the column.", minimum=0,
                  maximum=MAX_TILE_INDEX),
        "y": prop(INT, "For teleport and walk_to: the row.", minimum=0,
                  maximum=MAX_TILE_INDEX),
        "direction": prop(INT, "For teleport: 2 down, 4 left, 6 right, "
                               "8 up (default: unchanged). For interact: "
                               "the way to face (default: the way the "
                               "player already faces)."),
        "event_id": prop(INT, "For interact: walk to the nearest tile beside "
                              "this event on the current map and face it.",
                         minimum=1, maximum=MAX_LIVE_EVENT_ID),
        "frames_per_step": prop(
            INT, "For walk_to and interact's walk: frames to run after each "
                 "key press before looking at the player (default %d, at "
                 "most %d)." % (playtest.DEFAULT_FRAMES_PER_STEP,
                                playtest.MAX_FRAMES_PER_STEP),
            minimum=1, maximum=playtest.MAX_FRAMES_PER_STEP),
        "max_frames": prop(
            INT, "For walk_to, interact and scenario: the most frames the "
                 "call may run (default %d for walk_to and interact, %d for "
                 "scenario; at most %d)."
                 % (playtest.DEFAULT_WALK_FRAMES,
                    playtest.DEFAULT_SCENARIO_FRAMES,
                    playtest.MAX_PLAYTEST_FRAMES),
            minimum=1, maximum=playtest.MAX_PLAYTEST_FRAMES),
        "max_seconds": prop(
            INT, "For walk_to, interact and scenario: the wall-clock "
                 "seconds the call may take, checked between requests "
                 "(default %d for walk_to and interact, %d for scenario; at "
                 "most %d)." % (playtest.DEFAULT_WALK_SECONDS,
                                playtest.DEFAULT_SCENARIO_SECONDS,
                                playtest.MAX_PLAYTEST_SECONDS),
            minimum=1, maximum=playtest.MAX_PLAYTEST_SECONDS),
        "steps": prop(
            ARR, "For scenario: the steps, run in order. `op` is one of %s "
                 "(not start, stop, eval or scenario); `args` are that op's "
                 "own arguments; `expect` is checked after the step. At "
                 "most %d." % (", ".join(SCENARIO_OPS),
                               playtest.MAX_SCENARIO_STEPS),
            items={"type": OBJ, "properties": {
                "op": prop(STR, "The op to run.", enum=list(SCENARIO_OPS)),
                "args": prop(OBJ, "The op's arguments, without `op`."),
                "expect": prop(OBJ, "Checks against the game after the step "
                                    "(see the tool description): map_id, x, "
                                    "y, direction, gold, party_size, "
                                    "switches, variables."),
            }},
            maxItems=playtest.MAX_SCENARIO_STEPS),
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
        raise ToolError(
            "live sessions drive RPG Maker VX Ace games, and this project "
            "is %s. The bridge a session injects is written for RGSS3 "
            "(Ruby 1.9.2, VX Ace's window and Input) and has not been "
            "written or tried for RGSS1 or RGSS2, whose scripts, Ruby 1.8 "
            "and engine libraries differ; no VX or XP engine exists here to "
            "build and test one against. See docs/engine_parity.md."
            % ctx.project.profile.label)
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
    # The bridge builds a method name from each; only the advertised readers
    # may be reached, and a bad name is a refusal here, not a Ruby error there.
    for section in sections:
        if section not in bridge.STATE_SECTIONS:
            raise ToolError("unknown state section %s; choose from %s"
                            % (echo(section), ", ".join(bridge.STATE_SECTIONS)))
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
    return _running().request("step", {"frames": frames},
                              _step_seconds(frames))


def _step_seconds(frames: int) -> float:
    """How long a step of `frames` is waited for before the game is called
    unresponsive: the ordinary wait, plus twice the time the frames take."""
    return _session().REPLY_SECONDS + 2.0 * frames / FRAMES_PER_SECOND


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


def _ids(args: dict[str, Any], key: str, check: Any,
         limit: int = MAX_SET_ENTRIES) -> dict[int, Any]:
    """A `{"12": value}` argument as `{12: value}`, every entry checked."""
    given = opt_dict(args, key)
    if len(given) > limit:
        raise ToolError("%s names %d entries; at most %d are read at once"
                        % (key, len(given), limit))
    out = {}
    for name, value in given.items():
        # ASCII digits only, and few: `isdigit()` passes "²", which
        # `int()` rejects, and a 5,000-digit key trips the int-string limit.
        if not (isinstance(name, str) and name.isascii() and name.isdigit()
                and len(name) <= 9 and int(name) >= 1):
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


class _Driver:
    """The running session, as `live.playtest` talks to it.

    Each method is one request to the bridge, waited for as long as the same
    request is waited for when a caller makes it directly: a step in
    proportion to its frames, anything else for the ordinary reply time. So
    a walk of a hundred looks is a hundred bounded waits, and the walk's own
    clock and frame budget are what bound the hundred.
    """

    def __init__(self, running: "session.Session"):
        self.running = running

    def state(self, sections: "list[str]") -> dict[str, Any]:
        return self.running.request("state", {"sections": sections})

    def pause(self) -> dict[str, Any]:
        return self.running.request("pause")

    def press(self, key: str, frames: int) -> dict[str, Any]:
        return self.running.request("input", {"keys": [key],
                                              "frames": frames})

    def step(self, frames: int) -> dict[str, Any]:
        return self.running.request("step", {"frames": frames},
                                    _step_seconds(frames))

    def passability(self, x0: int, y0: int, x1: int, y1: int) -> dict[str, Any]:
        return self.running.request("passability", {
            "x0": x0, "y0": y0, "x1": x1, "y1": y1})

    def locate(self, event_id: int) -> dict[str, Any]:
        return self.running.request("locate", {"event_id": event_id})

    def face(self, direction: int) -> dict[str, Any]:
        return self.running.request("face", {"direction": direction})


def _a_direction(args: dict[str, Any]) -> "int | None":
    direction = opt_int(args, "direction")
    if direction is not None and direction not in DIRECTIONS:
        raise ToolError("direction must be 2, 4, 6 or 8, got %s"
                        % echo(direction))
    return direction


def _limits(args: dict[str, Any], frames: int, seconds: int) -> playtest.Limits:
    """What a walk may spend: the caller's ceilings, or these defaults."""
    return playtest.Limits(
        opt_int_default(args, "frames_per_step",
                        playtest.DEFAULT_FRAMES_PER_STEP, 1,
                        playtest.MAX_FRAMES_PER_STEP),
        opt_int_default(args, "max_frames", frames, 1,
                        playtest.MAX_PLAYTEST_FRAMES),
        opt_int_default(args, "max_seconds", seconds, 1,
                        playtest.MAX_PLAYTEST_SECONDS))


def _walk_to(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    x = need_int(args, "x", 0, MAX_TILE_INDEX)
    y = need_int(args, "y", 0, MAX_TILE_INDEX)
    map_id = opt_int(args, "map_id", None, 1, MAX_MAP_ID)
    limits_ = _limits(args, playtest.DEFAULT_WALK_FRAMES,
                      playtest.DEFAULT_WALK_SECONDS)
    return playtest.walk_to(_Driver(_running()), (x, y), map_id, limits_)


def _interact(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    direction = _a_direction(args)
    event_id = opt_int(args, "event_id", None, 1, MAX_LIVE_EVENT_ID)
    if direction is not None and event_id is not None:
        raise ToolError("interact takes a direction or an event_id, not "
                        "both: with an event_id the direction is the one "
                        "that faces the event")
    frames = opt_int_default(args, "frames", playtest.DEFAULT_INTERACT_FRAMES,
                             1, MAX_STEP_FRAMES)
    limits_ = _limits(args, playtest.DEFAULT_WALK_FRAMES,
                      playtest.DEFAULT_WALK_SECONDS)
    reply = playtest.interact(_Driver(_running()), direction, event_id,
                              limits_, frames)
    notes = reply.pop("changes_notes", None)
    if notes:
        reply.update(partial("changes", truncated=True, notes=notes))
    return reply


# The numbers an `expect` may compare against: the range a variable has.
def _a_number(what: str, value: Any) -> int:
    return as_int(value, what, -VARIABLE_LIMIT, VARIABLE_LIMIT)


def _comparisons(what: str, value: Any) -> "list[tuple[str, int]]":
    """`5` as eq 5, or `{"ge": 5, "lt": 9}` as both, every operator checked."""
    if not isinstance(value, dict):
        return [("eq", _a_number(what, value))]
    if not value or len(value) > len(playtest.COMPARES):
        raise ToolError("%s compares with one to %d of %s, got %d"
                        % (what, len(playtest.COMPARES),
                           ", ".join(playtest.COMPARES), len(value)))
    out = []
    for name, number in value.items():
        if name not in playtest.COMPARES:
            raise ToolError("%s compares with one of %s, not %s"
                            % (what, ", ".join(playtest.COMPARES), echo(name)))
        out.append((name, _a_number("%s.%s" % (what, name), number)))
    return out


_EXPECT_PLAYER = ("map_id", "x", "y", "direction")
_EXPECT_KEYS = _EXPECT_PLAYER + ("switches", "variables", "gold", "party_size")


def _parse_expect(expect: Any) -> "list[playtest.Check]":
    """A step's `expect` as comparisons, every key and value checked.

    An empty `expect` is refused rather than passed: a test that asserts
    nothing and reports success is the failure a scenario exists to catch.
    """
    if expect is None:
        return []
    if not isinstance(expect, dict):
        raise ToolError("expect must be an object, got %s" % echo(expect))
    unknown = sorted(str(key) for key in expect if key not in _EXPECT_KEYS)
    if unknown:
        raise ToolError("expect has no check %s; choose from %s"
                        % (", ".join(echo(key) for key in unknown[:5]),
                           ", ".join(_EXPECT_KEYS)))
    checks: "list[playtest.Check]" = []
    for key in _EXPECT_PLAYER:
        if expect.get(key) is not None:
            checks.extend(playtest.Check("player", key, name, number)
                          for name, number in _comparisons(key, expect[key]))
    for number, wanted in _ids(expect, "switches", _a_bool,
                               playtest.MAX_EXPECT_CHECKS).items():
        checks.append(playtest.Check("switch", number, "eq", wanted))
    for number, pairs in _ids(expect, "variables", _comparisons,
                              playtest.MAX_EXPECT_CHECKS).items():
        checks.extend(playtest.Check("variable", number, name, value)
                      for name, value in pairs)
    for key, kind in (("gold", "gold"), ("party_size", "party")):
        if expect.get(key) is not None:
            checks.extend(playtest.Check(kind, key, name, number)
                          for name, number in _comparisons(key, expect[key]))
    if not checks:
        raise ToolError("expect checks nothing; name at least one of %s"
                        % ", ".join(_EXPECT_KEYS))
    if len(checks) > playtest.MAX_EXPECT_CHECKS:
        raise ToolError("expect makes %d comparisons; one step checks at "
                        "most %d" % (len(checks), playtest.MAX_EXPECT_CHECKS))
    return checks


# What a scenario refuses by name, and why. Anything else not in
# SCENARIO_OPS is refused with the plain list.
_NOT_IN_A_SCENARIO = {
    "start": "start and stop change which game is running",
    "stop": "start and stop change which game is running",
    "eval": "eval runs arbitrary Ruby and is gated by the server's operator, "
            "so no other op may lead to it",
    "scenario": "a scenario inside a scenario would not be bounded by the "
                "step ceiling",
}


def _parse_steps(args: dict[str, Any]) -> "list[tuple[str, dict, list]]":
    """Every step of a scenario, checked in full before the first is run.

    A step is held to what the same op would be held to if called alone:
    the tool's schema bounds, then the op's own list of arguments it reads
    (so a stray argument is refused rather than dropped), then the op's
    handler when it runs. Doing the first two for every step up front means
    a typo in step 9 does not cost the player steps 1 to 8.
    """
    raw = opt_list(args, "steps")
    if not raw:
        raise ToolError("scenario needs steps, e.g. [{\"op\": \"walk_to\", "
                        "\"args\": {\"x\": 3, \"y\": 4}, \"expect\": "
                        "{\"x\": 3}}]")
    if len(raw) > playtest.MAX_SCENARIO_STEPS:
        raise ToolError("scenario has %d steps; one scenario runs at most %d"
                        % (len(raw), playtest.MAX_SCENARIO_STEPS))
    declared = REGISTRY["live"].schema
    steps = []
    for index, step in enumerate(raw):
        where = "steps[%d]" % index
        try:
            if not isinstance(step, dict):
                raise ToolError("must be an object, got %s" % echo(step))
            unknown = sorted(str(key) for key in step
                             if key not in ("op", "args", "expect"))
            if unknown:
                raise ToolError("has no field %s; it takes op, args and "
                                "expect" % ", ".join(echo(k) for k in unknown))
            op = step.get("op")
            if isinstance(op, str) and op in _NOT_IN_A_SCENARIO:
                raise ToolError("op %s is not allowed in a scenario: %s"
                                % (echo(op), _NOT_IN_A_SCENARIO[op]))
            op = one_of(step, "op", list(SCENARIO_OPS))
            inner = opt_dict(step, "args")
            if "op" in inner:
                raise ToolError("args must not hold `op`; it goes beside "
                                "args")
            merged = dict(inner, op=op)
            check_args(declared, merged, "args")
            check_op_args(op, merged, LIVE_OP_ARGS, "scenario step")
            steps.append((op, merged, _parse_expect(step.get("expect"))))
        except ToolError as exc:
            raise ToolError("%s %s" % (where, exc)) from exc
    return steps


def _frame_of(reply: Any, default: int) -> int:
    frame = reply.get("frame") if isinstance(reply, dict) else None
    if isinstance(frame, int) and not isinstance(frame, bool):
        return frame
    return default


def _clip_budget(args: dict[str, Any], key: str, default: int,
                 remaining: float) -> None:
    """Hold a walking step to what is left of the scenario's budget."""
    given = opt_int_default(args, key, default, 1,
                            playtest.MAX_PLAYTEST_FRAMES
                            if key == "max_frames"
                            else playtest.MAX_PLAYTEST_SECONDS)
    args[key] = max(1, min(given, int(remaining)))


def _scenario(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Run steps in order; stop at the first that fails, and say why.

    A step fails when its op is refused, when it reports it did not do what
    it was asked (a walk that did not arrive, an interaction that was not
    made), or when its `expect` does not hold. The reply is the whole
    story either way: every step run, with its result, and for the one that
    stopped it the expected and the actual.
    """
    steps = _parse_steps(args)
    max_frames = opt_int_default(args, "max_frames",
                                 playtest.DEFAULT_SCENARIO_FRAMES, 1,
                                 playtest.MAX_PLAYTEST_FRAMES)
    max_seconds = opt_int_default(args, "max_seconds",
                                  playtest.DEFAULT_SCENARIO_SECONDS, 1,
                                  playtest.MAX_PLAYTEST_SECONDS)
    running = _running()
    began = playtest._clock()
    frame0 = frame = _frame_of(running.request("state",
                                               {"sections": ["player"]}), 0)
    results: "list[dict[str, Any]]" = []
    chars = 0
    clipped = False
    stop = "completed"
    failed_step: "int | None" = None
    failed_expect: "dict | None" = None
    for index, (op, step_args, checks) in enumerate(steps):
        used = frame - frame0
        elapsed = playtest._clock() - began
        if elapsed >= max_seconds:
            stop = "time_budget"
            break
        if used >= max_frames:
            stop = "frame_budget"
            break
        record: "dict[str, Any]" = {"index": index, "op": op}
        try:
            if op == "step" and used + opt_int_default(
                    step_args, "frames", 1, 1, MAX_STEP_FRAMES) > max_frames:
                stop = "frame_budget"
                break
            if op in ("walk_to", "interact"):
                _clip_budget(step_args, "max_frames",
                             playtest.DEFAULT_WALK_FRAMES, max_frames - used)
                _clip_budget(step_args, "max_seconds",
                             playtest.DEFAULT_WALK_SECONDS,
                             max_seconds - elapsed)
            reply = _LIVE_OPS[op](ctx, step_args)
        except (ToolError, LiveError) as exc:
            record.update(ok=False, error=str(exc))
            stop, failed_step = "step_failed", index
            results.append(record)
            break
        if isinstance(reply, ImageReply):
            reply = reply.body
        frame = _frame_of(reply, frame)
        reply.pop("path", None)
        record.update(ok=True, result=reply)
        if (op == "walk_to" and not reply.get("arrived")) or (
                op == "interact" and not reply.get("interacted")):
            record["ok"] = False
            stop, failed_step = "step_failed", index
        elif checks:
            seen = running.request("state", {
                "sections": playtest.sections_for(checks)})
            frame = _frame_of(seen, frame)
            failed_expect = playtest.evaluate(checks, seen)
            if failed_expect is None:
                record["expect_passed"] = True
            else:
                record["expect_failed"] = failed_expect
                stop, failed_step = "expectation_failed", index
        size = len(json.dumps(record, default=str))
        if chars + size > playtest.MAX_SCENARIO_RESULT_CHARS:
            clipped = True
            record = {key: value for key, value in record.items()
                      if key != "result"}
            record["result_omitted"] = True
            size = len(json.dumps(record, default=str))
        chars += size
        results.append(record)
        if failed_step is not None:
            break
    out: "dict[str, Any]" = {
        "passed": stop == "completed", "stop_reason": stop,
        "steps_run": len(results), "steps_total": len(steps),
        "frames_used": frame - frame0,
        "seconds": round(playtest._clock() - began, 2), "results": results}
    if failed_step is not None:
        out["failed_step"] = failed_step
    if failed_expect is not None:
        out["failed_expect"] = failed_expect
    if clipped:
        out.update(partial("results", truncated=True, notes=(
            "step results passed %d characters, so later ones are listed "
            "without their result" % playtest.MAX_SCENARIO_RESULT_CHARS,)))
    return out


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
    "walk_to": _walk_to,
    "interact": _interact,
    "scenario": _scenario,
    "screenshot": _screenshot,
    "eval": _eval,
}
