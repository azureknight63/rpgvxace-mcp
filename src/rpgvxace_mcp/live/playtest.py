"""Walking, interacting and asserting, on top of the bridge's primitives.

The bridge can press keys, step frames and read state. A play-tester wants
three things made of those: walk the player to a tile, press the action key
at something and see what it did, and check that the game ended up where a
test said it should. This module is that layer, and it holds three rules.

**The game decides what is walkable.** The bridge's `passability` op asks the
game's own `Game_Player#passable?` for each cell, so an event that blocks,
a boat and a counter are in the answer and nothing here recomputes them from
tile flags. A breadth-first search over that grid plans the route.

**Walking is a closed loop.** One key, then a few frames, then a look at
where the player is: never a pre-computed run of keys, because an NPC in the
way, a transfer tile or a message is only found out by looking. Every
iteration ends in one of a small set of verdicts (`classify`), and every
loop is bounded by a frame budget and a wall clock that the caller can read
in the tool's description.

**Nothing here talks to the game directly.** Everything goes through a
`driver` (see `live_tools._Driver`), so this module imports no session and
no tool, and a refusal is a `LiveError`, which the tool turns into a
`ToolError`.
"""

from __future__ import annotations

import operator
import time
from collections import deque
from typing import Any, Callable, NamedTuple

from . import bridge
from .limits import LiveError


# The longest route walk_to or interact will follow. A longer one is refused
# before the player moves: walk in stages, so each stage is checked.
MAX_WALK_PATH = 200


# The most frames one press may be given to move the player a tile. A tile
# takes 16 frames at normal speed; the default leaves a little over.
MAX_FRAMES_PER_STEP = 60
DEFAULT_FRAMES_PER_STEP = 20


# The most frames and seconds one walk, interact or scenario may spend, and
# what each takes when the caller does not say.
MAX_PLAYTEST_FRAMES = 6000
MAX_PLAYTEST_SECONDS = 120
DEFAULT_WALK_FRAMES = 2400
DEFAULT_WALK_SECONDS = 60
DEFAULT_SCENARIO_FRAMES = 3000
DEFAULT_SCENARIO_SECONDS = 90


# How many frames interact runs after the action key, unless told.
DEFAULT_INTERACT_FRAMES = 12


# How many switches or variables one reply lists as changed, per kind.
MAX_DIFF_ENTRIES = 100


# How many comparisons one `expect` holds, how many steps a scenario holds,
# and how many characters of step results a scenario reply carries.
MAX_EXPECT_CHECKS = 40
MAX_SCENARIO_STEPS = 50
MAX_SCENARIO_RESULT_CHARS = 120_000


# The sections one walk reads after each press.
WALK_SECTIONS = ("player", "message")


# The sections an interaction compares before and after.
DIFF_SECTIONS = ("player", "gold", "switches", "variables", "message",
                 "interpreter")


# How far around the player and the target a map too big to read whole is
# read, in tiles; and how many presses are made at a tile that did not move.
_WALK_MARGIN = 16
_ATTEMPTS = 2
_SETTLE_CHUNK = 4


# Overridable by a test, so the wall clock can be tested without waiting.
_clock: Callable[[], float] = time.monotonic


DIRECTIONS = (2, 4, 6, 8)
DELTA = {2: (0, 1), 4: (-1, 0), 6: (1, 0), 8: (0, -1)}
KEY_OF = {2: "DOWN", 4: "LEFT", 6: "RIGHT", 8: "UP"}
_BIT = {2: 1, 4: 2, 6: 4, 8: 8}


# What a caller is told about each reason a walk ended.
STOP_NOTES = {
    "arrived": "The player is on the target tile.",
    "blocked": "The player did not move when the key was pressed, so "
               "something the map data does not show is in the way (an "
               "event that moved there, a vehicle). Call walk_to again to "
               "re-plan from here.",
    "moved_elsewhere": "Something moved the player off the planned route "
                       "-- a transfer, or an event's move route. The walk "
                       "stopped there instead of guessing; look at `player` "
                       "and plan again.",
    "message_open": "A message window is open, so the player cannot walk. "
                    "Press C with `input` and `step` to advance it.",
    "battle": "A battle scene started. The walk stopped; the game is in "
              "the battle scene until it ends.",
    "scene_changed": "The game left the map scene (a menu, a shop, the "
                     "title); the walk stopped there.",
    "frame_budget": "The frame budget ran out before the player arrived. "
                    "Raise max_frames or walk in stages.",
    "time_budget": "The wall-clock budget ran out before the player "
                   "arrived. Raise max_seconds or walk in stages.",
    "no_path": "The game's own passability finds no route to the target "
              "from the player's position; nothing was pressed.",
}


class Limits(NamedTuple):
    """What one walk may spend."""

    frames_per_step: int
    max_frames: int
    max_seconds: int


# ---------------------------------------------------------------------------
# Reading what the game said
# ---------------------------------------------------------------------------


def _whole(reply: Any, key: str, where: str) -> int:
    value = reply.get(key) if isinstance(reply, dict) else None
    if isinstance(value, bool) or not isinstance(value, int):
        raise LiveError("the game's %s reply has no whole number %s"
                        % (where, key))
    return value


def player_of(state: Any) -> dict:
    """The player in a `state` reply, or a refusal if there is none yet."""
    found = state.get("player") if isinstance(state, dict) else None
    if not isinstance(found, dict):
        raise LiveError("the game has no player on a map yet (it may be on "
                        "the title screen or between scenes); wait for the "
                        "map scene")
    for key in ("map_id", "x", "y"):
        _whole(found, key, "state")
    return found


def position(state: Any) -> "tuple[int, int]":
    found = player_of(state)
    return found["x"], found["y"]


def scene_of(state: Any) -> str:
    scene = state.get("scene") if isinstance(state, dict) else None
    return scene if isinstance(scene, str) else ""


def message_of(state: Any) -> "dict | None":
    """The open message window, as a short view, or None when there is none."""
    found = state.get("message") if isinstance(state, dict) else None
    if not isinstance(found, dict) or not (found.get("busy")
                                           or found.get("visible")):
        return None
    texts = found.get("texts")
    choices = found.get("choices")
    return {"texts": list(texts) if isinstance(texts, list) else [],
            "choices": list(choices) if isinstance(choices, list) else []}


def view_of(state: Any) -> dict:
    """The player's place, as a reply reports it."""
    found = player_of(state)
    return {key: found.get(key) for key in ("map_id", "x", "y", "direction")}


class Grid:
    """The game's passability for a rectangle of one map.

    Each cell is a bit set -- which of the four directions the player could
    step from it -- exactly as the bridge sends it. A cell outside the
    rectangle is unknown, and the search treats a step into it as a wall.
    """

    def __init__(self, reply: Any):
        self.map_id = _whole(reply, "map_id", "passability")
        self.width = _whole(reply, "width", "passability")
        self.height = _whole(reply, "height", "passability")
        self.x0 = _whole(reply, "x0", "passability")
        self.y0 = _whole(reply, "y0", "passability")
        self.x1 = _whole(reply, "x1", "passability")
        self.y1 = _whole(reply, "y1", "passability")
        rows = reply.get("rows")
        wide, high = self.x1 - self.x0 + 1, self.y1 - self.y0 + 1
        if (not isinstance(rows, list) or len(rows) != high
                or wide < 1 or high < 1
                or not all(isinstance(row, str) and len(row) == wide
                           for row in rows)):
            raise LiveError("the game's passability reply is not the %dx%d "
                            "grid it described" % (wide, high))
        try:
            self.cells = [[int(char, 16) for char in row] for row in rows]
        except ValueError:
            raise LiveError("the game's passability reply holds a cell that "
                            "is not a hexadecimal digit") from None

    def holds(self, x: int, y: int) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1

    def on_map(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def steps_from(self, x: int, y: int) -> "list[tuple[int, int]]":
        """The tiles the player can step to from (x, y), in a fixed order."""
        if not self.holds(x, y):
            return []
        bits = self.cells[y - self.y0][x - self.x0]
        out = []
        for direction in DIRECTIONS:
            dx, dy = DELTA[direction]
            if bits & _BIT[direction] and self.holds(x + dx, y + dy):
                out.append((x + dx, y + dy))
        return out


def plan(grid: Grid, start: "tuple[int, int]",
         goals: "set[tuple[int, int]]") -> "list[tuple[int, int]] | None":
    """The shortest route from start to the nearest goal; None if none.

    The tiles after `start`, ending on the goal reached. `[]` when start is
    already a goal. Breadth first, over the grid the game reported, so the
    work is bounded by the window: `bridge.MAX_PASS_CELLS` cells.
    """
    if start in goals:
        return []
    parent: "dict[tuple[int, int], tuple[int, int] | None]" = {start: None}
    queue = deque([start])
    while queue:
        here = queue.popleft()
        for there in grid.steps_from(*here):
            if there in parent:
                continue
            parent[there] = here
            if there in goals:
                route = [there]
                while parent[route[-1]] != start:
                    route.append(parent[route[-1]])
                route.reverse()
                return route
            queue.append(there)
    return None


def direction_between(here: "tuple[int, int]", there: "tuple[int, int]") -> int:
    delta = (there[0] - here[0], there[1] - here[1])
    for direction, step in DELTA.items():
        if step == delta:
            return direction
    raise ValueError("%r is not one step from %r" % (there, here))


# ---------------------------------------------------------------------------
# One run: the clock and the frame count a walk is held to
# ---------------------------------------------------------------------------


class Run:
    """The budget of one walk or interaction, and the driver it spends it on.

    Frames are the game's own frame counter, read off the envelope of every
    reply, so what is spent is what the game ran, not what was asked for.
    """

    def __init__(self, driver: Any, limits: Limits):
        self.driver = driver
        self.limits = limits
        self.began = _clock()
        self.first_frame: "int | None" = None
        self.frame: "int | None" = None

    def seen(self, reply: Any) -> Any:
        frame = reply.get("frame") if isinstance(reply, dict) else None
        if isinstance(frame, int) and not isinstance(frame, bool):
            if self.first_frame is None:
                self.first_frame = frame
            self.frame = frame
        return reply

    @property
    def frames_used(self) -> int:
        if self.first_frame is None or self.frame is None:
            return 0
        return self.frame - self.first_frame

    @property
    def frames_left(self) -> int:
        return self.limits.max_frames - self.frames_used

    def seconds(self) -> float:
        return _clock() - self.began

    def out_of_time(self) -> bool:
        return self.seconds() >= self.limits.max_seconds

    def state(self, sections: "tuple[str, ...]") -> dict:
        return self.seen(self.driver.state(list(sections)))

    def step(self, frames: int) -> dict:
        return self.seen(self.driver.step(frames))

    def press(self, key: str, frames: int = 1) -> dict:
        return self.seen(self.driver.press(key, frames))

    def begin(self, sections: "tuple[str, ...]") -> dict:
        """The first look at the game, with the game paused.

        Paused, because a closed loop reads between frames: a game that
        keeps running while this thinks has moved on by the time it looks.
        `step` leaves the game paused as well, so this changes nothing a
        step would not.
        """
        first = self.state(sections)
        if not first.get("paused"):
            self.seen(self.driver.pause())
        return first


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def read_grid(run: Run, player: dict,
              points: "list[tuple[int, int]]") -> Grid:
    """The passability that covers the player and every point in `points`.

    The whole map when it is within `bridge.MAX_PASS_CELLS` cells; otherwise a
    window around the player and the points, with a margin so a detour
    around a pond is in it. A point off the map, or so far away that the
    window would be too large, is a refusal before anything moves.
    """
    px, py = player["x"], player["y"]
    probe = Grid(run.seen(run.driver.passability(px, py, px, py)))
    for x, y in points:
        if not probe.on_map(x, y):
            raise LiveError("(%d, %d) is outside map %d, which is %dx%d"
                            % (x, y, probe.map_id, probe.width, probe.height))
    if probe.width * probe.height <= bridge.MAX_PASS_CELLS:
        window = (0, 0, probe.width - 1, probe.height - 1)
    else:
        xs = [px] + [x for x, _ in points]
        ys = [py] + [y for _, y in points]
        window = (max(min(xs) - _WALK_MARGIN, 0),
                  max(min(ys) - _WALK_MARGIN, 0),
                  min(max(xs) + _WALK_MARGIN, probe.width - 1),
                  min(max(ys) + _WALK_MARGIN, probe.height - 1))
        cells = (window[2] - window[0] + 1) * (window[3] - window[1] + 1)
        if cells > bridge.MAX_PASS_CELLS:
            raise LiveError(
                "the target is too far from the player to plan in one go "
                "(the area between them is %d cells, and the game is asked "
                "about at most %d at a time). Walk to a nearer waypoint "
                "first." % (cells, bridge.MAX_PASS_CELLS))
    return Grid(run.seen(run.driver.passability(*window)))


def refusal_to_move(state: dict) -> "str | None":
    """Why the player cannot walk right now, if it cannot."""
    scene = scene_of(state)
    if scene.startswith("Scene_Battle"):
        return "battle"
    if message_of(state):
        return "message_open"
    if scene != "Scene_Map":
        return "scene_changed"
    return None


# ---------------------------------------------------------------------------
# The closed loop
# ---------------------------------------------------------------------------


def classify(state: dict, start_map: int, here: "tuple[int, int]",
             expected: "tuple[int, int]", target: "tuple[int, int]") -> str:
    """What one press did: a verdict, in the order the checks must run.

    A battle outranks everything, because it is what the caller most needs
    to hear. Being somewhere that is neither the tile left nor the tile
    aimed for -- or on another map, or mid-transfer -- is `moved_elsewhere`
    before arrival is tested, so a transfer to the same coordinates of a
    different map is not an arrival. The target is tested before the
    message: a walk that ends on an event's tile has arrived, and the
    message that opened is reported beside it.
    """
    found = player_of(state)
    spot = (found["x"], found["y"])
    if scene_of(state).startswith("Scene_Battle"):
        return "battle"
    if (found.get("transferring") or found["map_id"] != start_map
            or spot not in (here, expected)):
        return "moved_elsewhere"
    if spot == target:
        return "arrived"
    if message_of(state):
        return "message_open"
    if scene_of(state) != "Scene_Map":
        return "scene_changed"
    return "progress" if spot == expected else "same"


def settle(run: Run, state: dict) -> dict:
    """Step on, a few frames at a time, until the player has stopped moving.

    A tile takes 16 frames at normal speed and the position changes at the
    start of the step, so the first look can find the player part-way. A
    press made then is lost, because the player ignores input while moving.
    Bounded by one more `frames_per_step`, and by the frame budget.
    """
    waited = 0
    while (player_of(state).get("moving") and waited < run.limits.frames_per_step
           and run.frames_left >= _SETTLE_CHUNK):
        run.step(_SETTLE_CHUNK)
        waited += _SETTLE_CHUNK
        state = run.state(WALK_SECTIONS)
    return state


def follow(run: Run, route: "list[tuple[int, int]]", state: dict,
           target: "tuple[int, int]") -> "tuple[str, int, dict]":
    """Walk a planned route one looked-at tile at a time.

    Returns (verdict, tiles walked, the last state). Each tile: press the
    key, step `frames_per_step`, look, let a step in progress finish, then
    classify. A press that moved nothing is repeated once (a character in
    the way may have just left) before the walk reports `blocked`.
    """
    here = position(state)
    start_map = player_of(state)["map_id"]
    walked = 0
    if not route:
        return "arrived", 0, state
    for expected in route:
        key = KEY_OF[direction_between(here, expected)]
        attempts = 0
        while True:
            if run.out_of_time():
                return "time_budget", walked, state
            if run.frames_left < run.limits.frames_per_step:
                return "frame_budget", walked, state
            run.press(key)
            run.step(run.limits.frames_per_step)
            state = settle(run, run.state(WALK_SECTIONS))
            verdict = classify(state, start_map, here, expected, target)
            if verdict == "progress":
                walked += 1
                here = expected
                break
            if verdict == "arrived":
                return verdict, walked + 1, state
            if verdict != "same":
                return verdict, walked, state
            attempts += 1
            if attempts >= _ATTEMPTS:
                return "blocked", walked, state
    raise LiveError("the walk ended without a verdict; this is a bug in the "
                    "server, please report it")


def summary(run: Run, reason: str, route: "list[tuple[int, int]]", walked: int,
            state: dict) -> dict:
    """A walk's reply: why it stopped, where it left the player."""
    out: dict[str, Any] = {
        "stop_reason": reason, "arrived": reason == "arrived",
        "note": STOP_NOTES[reason],
        "path": [list(tile) for tile in route], "path_length": len(route),
        "steps_taken": walked, "player": view_of(state),
        "scene": scene_of(state), "frames_used": run.frames_used,
        "seconds": round(run.seconds(), 2)}
    if run.frame is not None:
        out["frame"] = run.frame
    if reason == "moved_elsewhere" and player_of(state).get("transferring"):
        # Seen on a real game: the player stops on the exit tile, still on
        # the old map, because the transfer happens on the next frames.
        out["transfer_pending"] = True
        out["note"] += (" A transfer has been ordered but not yet made: "
                        "`step` a few frames, then read `state` for the new "
                        "map.")
    message = message_of(state)
    if message:
        out["message"] = message
    return out


def walk_to(driver: Any, target: "tuple[int, int]", map_id: "int | None",
            limits: Limits) -> dict:
    """Walk the player to a tile, looking after every step."""
    run = Run(driver, limits)
    state = run.begin(WALK_SECTIONS)
    found = player_of(state)
    if map_id is not None and found["map_id"] != map_id:
        raise LiveError("the player is on map %d, not map %d, so nothing was "
                        "pressed" % (found["map_id"], map_id))
    stopped = refusal_to_move(state)
    if stopped:
        return summary(run, stopped, [], 0, state)
    grid = read_grid(run, found, [target])
    route = plan(grid, position(state), {target})
    if route is None:
        return summary(run, "no_path", [], 0, state)
    refuse_long_route(route)
    verdict, walked, state = follow(run, route, state, target)
    return summary(run, verdict, route, walked, state)


def refuse_long_route(route: "list[tuple[int, int]]") -> None:
    if len(route) > MAX_WALK_PATH:
        raise LiveError(
            "the route is %d tiles, and one walk follows at most %d. Walk "
            "to a waypoint partway, then on" % (len(route), MAX_WALK_PATH))


# ---------------------------------------------------------------------------
# Interacting
# ---------------------------------------------------------------------------


def _id_of(key: Any) -> "int | None":
    if isinstance(key, int) and not isinstance(key, bool):
        return key
    if isinstance(key, str) and key.isascii() and key.isdigit() and len(key) < 10:
        return int(key)
    return None


def diff_states(before: dict, after: dict) -> dict:
    """What an interaction changed: switches, variables and gold.

    A switch the bridge does not list is off, and a variable it does not
    list is 0, so the two lists are compared as sets of ids. A section the
    bridge cut off at its entry ceiling, or could not read, is said so in
    `changes_notes` (the tool turns them into the `changes_truncated` and
    `changes_note` keys; this layer sits below the function that owns those
    suffixes): a diff of half a list is not a diff, and presenting it as one
    would report "nothing changed".
    """
    changes: dict[str, Any] = {}
    notes = []
    for name, off in (("switches", False), ("variables", 0)):
        old, new = before.get(name), after.get(name)
        if not isinstance(old, dict) or not isinstance(new, dict):
            notes.append("%s could not be read, so their changes are not "
                         "shown" % name)
            continue
        if any(name in (state.get("truncated") or ())
               for state in (before, after)):
            notes.append("%s were cut at %d entries by the bridge, so "
                         "changes beyond that are not shown"
                         % (name, bridge.MAX_STATE_ENTRIES))
        rows = []
        for key in sorted(set(old) | set(new), key=lambda k: (_id_of(k) is None,
                                                           _id_of(k) or 0, str(k))):
            before_value, after_value = old.get(key, off), new.get(key, off)
            if before_value != after_value:
                rows.append({"id": _id_of(key) if _id_of(key) is not None
                             else key,
                             "from": before_value, "to": after_value})
        if len(rows) > MAX_DIFF_ENTRIES:
            notes.append("more than %d %s changed; the first %d are shown"
                         % (MAX_DIFF_ENTRIES, name, MAX_DIFF_ENTRIES))
            rows = rows[:MAX_DIFF_ENTRIES]
        if rows:
            changes[name] = rows
    if before.get("gold") != after.get("gold"):
        changes["gold"] = {"from": before.get("gold"), "to": after.get("gold")}
    out: dict[str, Any] = {"changes": changes}
    if notes:
        out["changes_notes"] = notes
    return out


def interact(driver: Any, direction: "int | None", event_id: "int | None",
             limits: Limits, frames: int) -> dict:
    """Face something, press the action key once, report what changed.

    With `event_id`, first walk to the nearest tile beside the event from
    which facing it is possible. With `direction`, face that way where the
    player stands. With neither, press the key facing wherever the player
    already faces.
    """
    run = Run(driver, limits)
    before = run.begin(DIFF_SECTIONS)
    found = player_of(before)
    stopped = refusal_to_move(before)
    if stopped:
        return dict(summary(run, stopped, [], 0, before), interacted=False)
    walked: "dict | None" = None
    state = before
    if event_id is not None:
        located = run.seen(driver.locate(event_id))
        if located.get("erased"):
            raise LiveError("event %d is erased, so there is nothing to "
                            "interact with" % event_id)
        ex, ey = _whole(located, "x", "locate"), _whole(located, "y", "locate")
        grid = read_grid(run, found, [(ex, ey)])
        stands = {}
        for toward in DIRECTIONS:
            dx, dy = DELTA[toward]
            stands[(ex - dx, ey - dy)] = toward
        route = plan(grid, position(before), set(stands))
        if route is None:
            return dict(summary(run, "no_path", [], 0, before),
                        interacted=False)
        refuse_long_route(route)
        reached = route[-1] if route else position(before)
        verdict, count, state = follow(run, route, before, reached)
        walked = {"steps_taken": count, "path_length": len(route)}
        if verdict != "arrived":
            return dict(summary(run, verdict, route, count, state),
                        interacted=False)
        direction = stands[reached]
    state = settle(run, state)
    if direction is not None:
        run.seen(driver.face(direction))
    run.press("C")
    run.step(frames)
    after = run.state(DIFF_SECTIONS)
    where = view_of(after)
    out: dict[str, Any] = {
        "interacted": True, "facing": where.get("direction"),
        "player_before": view_of(before), "player": where,
        "moved": (where["x"], where["y"]) != position(before),
        "message_open": message_of(after) is not None,
        "scene": scene_of(after), "frames_used": run.frames_used,
        "seconds": round(run.seconds(), 2)}
    if run.frame is not None:
        out["frame"] = run.frame
    message = message_of(after)
    if message:
        out["message"] = message
    running = after.get("interpreter")
    if isinstance(running, dict) and running.get("running"):
        out["event_running"] = running.get("event_id")
        other = running.get("event_id")
        if (event_id is not None and isinstance(other, int)
                and other not in (0, event_id)):
            # Seen on a real game: an enemy event touched the player on the
            # walk, and the key press went to its wait, not to the target.
            out["note"] = (
                "event %d was running, not event %d, so the key press may "
                "not have reached event %d (an enemy that touched the player "
                "on the way, or a parallel event). Check `message` and "
                "`changes`; call interact again once it has finished."
                % (other, event_id, event_id))
    if walked:
        out["walked"] = walked
    out.update(diff_states(before, after))
    return out


# ---------------------------------------------------------------------------
# Expectations: what a scenario step asserts about the game afterwards
# ---------------------------------------------------------------------------


COMPARES = {"eq": operator.eq, "ne": operator.ne, "gt": operator.gt,
            "ge": operator.ge, "lt": operator.lt, "le": operator.le}


class Check(NamedTuple):
    """One comparison: `kind` names where the value is read from."""

    kind: str  # player, switch, variable, gold or party
    key: Any  # the player field, or the switch or variable id
    compare: str
    expected: Any

    @property
    def label(self) -> str:
        if self.kind == "player":
            return "player.%s" % self.key
        if self.kind == "switch":
            return "switches[%d]" % self.key
        if self.kind == "variable":
            return "variables[%d]" % self.key
        return "party_size" if self.kind == "party" else "gold"


_SECTION_OF = {"player": "player", "switch": "switches",
               "variable": "variables", "gold": "gold", "party": "party"}


def sections_for(checks: "list[Check]") -> "list[str]":
    """The state sections these checks read, once each, in a stable order."""
    wanted = {_SECTION_OF[check.kind] for check in checks}
    return [name for name in bridge.STATE_SECTIONS if name in wanted]


def _listed(state: dict, section: str, key: int, absent: Any) -> "tuple[Any, str | None]":
    """One id's value from a listing that omits the off ones.

    A section cut off by the bridge's ceiling lists the lowest ids first, so
    an id past the last one listed is unknown rather than off.
    """
    listing = state.get(section)
    if not isinstance(listing, dict):
        return None, "%s could not be read" % section
    if str(key) in listing:
        return listing[str(key)], None
    if section in (state.get("truncated") or ()):
        ids = [_id_of(name) for name in listing]
        if key > max([i for i in ids if i is not None] or [0]):
            return None, ("%s were cut at %d entries and %d is past them"
                          % (section, bridge.MAX_STATE_ENTRIES, key))
    return absent, None


def _actual(check: Check, state: dict) -> "tuple[Any, str | None]":
    errors = state.get("errors")
    section = _SECTION_OF[check.kind]
    if isinstance(errors, dict) and section in errors:
        return None, "the %s section could not be read: %s" % (
            section, errors[section])
    if check.kind == "player":
        found = state.get("player")
        if not isinstance(found, dict):
            return None, "there is no player on a map"
        return found.get(check.key), None
    if check.kind == "switch":
        value, note = _listed(state, "switches", check.key, False)
        return value, note
    if check.kind == "variable":
        return _listed(state, "variables", check.key, 0)
    if check.kind == "gold":
        return state.get("gold"), None
    party = state.get("party")
    if not isinstance(party, list):
        return None, "the party could not be read"
    return len(party), None


def evaluate(checks: "list[Check]", state: dict) -> "dict | None":
    """The first comparison that does not hold, or None if all do."""
    for check in checks:
        actual, note = _actual(check, state)
        held = False
        if note is None:
            try:
                held = bool(COMPARES[check.compare](actual, check.expected))
            except TypeError:
                note = "%s is not a number" % check.label
        if not held:
            out = {"check": check.label, "compare": check.compare,
                   "expected": check.expected, "actual": actual}
            if note:
                out["note"] = note
            return out
    return None
