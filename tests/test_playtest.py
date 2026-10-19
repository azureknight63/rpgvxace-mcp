"""Play-testing the live game: walk_to, interact and scenario.

The engine is played by `tests/fixtures/fake_game.py`, whose tiny map model
(a grid, a player, a few special cells) is enough to drive the whole closed
loop and every reason it can stop. What those tests cannot reach is the Ruby
that asks the real game about passability: that is checked as text here and
by hand against a real game at the end (see docs/live.md).

Every expectation below is worked out by hand from the map in the test, not
read back from the code under test.
"""

import itertools
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from test_live import LiveTest, fake_launcher  # noqa: E402

from rpgvxace_mcp.live import bridge, playtest, session  # noqa: E402
from rpgvxace_mcp.live.limits import LiveError  # noqa: E402
from rpgvxace_mcp.tools import live_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import REGISTRY, ToolError  # noqa: E402


def grid(rows, x0=0, y0=0, width=None, height=None):
    """A passability reply for the rows given, as the bridge would send it."""
    return {"map_id": 1, "width": width or x0 + len(rows[0]),
            "height": height or y0 + len(rows), "x0": x0, "y0": y0,
            "x1": x0 + len(rows[0]) - 1, "y1": y0 + len(rows) - 1,
            "rows": rows}


class TestTheGridAndTheSearch(unittest.TestCase):
    """The pure half: decode the game's cells, find the shortest route."""

    def test_a_cells_bits_are_down_left_right_up(self):
        # 1 is down: from the top cell the player can go down and no other way.
        found = playtest.Grid(grid(["1", "0"]))
        self.assertEqual(found.steps_from(0, 0), [(0, 1)])
        self.assertEqual(found.steps_from(0, 1), [])
        # 8 is up, 2 left, 4 right.
        found = playtest.Grid(grid(["0", "8"]))
        self.assertEqual(found.steps_from(0, 1), [(0, 0)])
        found = playtest.Grid(grid(["402"]))
        self.assertEqual(found.steps_from(0, 0), [(1, 0)])
        self.assertEqual(found.steps_from(1, 0), [])
        self.assertEqual(found.steps_from(2, 0), [(1, 0)])

    def test_a_route_is_the_shortest_one_around_a_wall(self):
        # 3x3 with a wall at (1,1), cell by cell (down=1 left=2 right=4 up=8):
        #  (0,0) right|down = 5     (1,0) left|right = 6   (2,0) left|down = 3
        #  (0,1) down|up = 9        (1,1) wall = 0         (2,1) down|up = 9
        #  (0,2) right|up = c       (1,2) left|right = 6   (2,2) left|up = a
        rows = ["563", "909", "c6a"]
        found = playtest.Grid(grid(rows))
        route = playtest.plan(found, (0, 0), {(2, 2)})
        self.assertEqual(len(route), 4)
        self.assertNotIn((1, 1), route)
        self.assertEqual(route[-1], (2, 2))
        previous = (0, 0)
        for tile in route:
            self.assertEqual(abs(tile[0] - previous[0])
                             + abs(tile[1] - previous[1]), 1)
            previous = tile

    def test_a_one_way_cell_is_one_way(self):
        # (1,0) can be left only to the right: nothing returns through it.
        row = playtest.Grid(grid(["442"]))
        self.assertEqual(playtest.plan(row, (0, 0), {(2, 0)}), [(1, 0), (2, 0)])
        self.assertIsNone(playtest.plan(row, (2, 0), {(0, 0)}))

    def test_no_route_is_none_and_standing_on_the_goal_is_empty(self):
        found = playtest.Grid(grid(["400"]))
        self.assertIsNone(playtest.plan(found, (0, 0), {(2, 0)}))
        self.assertEqual(playtest.plan(found, (0, 0), {(0, 0)}), [])

    def test_the_nearest_of_several_goals_is_reached(self):
        found = playtest.Grid(grid(["66666"]))
        route = playtest.plan(found, (2, 0), {(0, 0), (3, 0)})
        self.assertEqual(route, [(3, 0)])

    def test_a_window_is_read_at_its_own_origin(self):
        # Columns 5..6 of row 3 of a larger map. (5,3) can go left, out of
        # the window, and right, into it: only the right is a known step.
        found = playtest.Grid(grid(["60"], x0=5, y0=3))
        self.assertEqual(found.steps_from(5, 3), [(6, 3)])
        self.assertTrue(found.holds(6, 3))
        self.assertFalse(found.holds(4, 3))
        self.assertEqual(found.steps_from(4, 3), [])

    def test_a_malformed_grid_is_refused(self):
        good = grid(["60", "00"])
        bad = {
            "too few rows": dict(good, rows=["60"]),
            "a short row": dict(good, rows=["60", "0"]),
            "a non-hex cell": dict(good, rows=["60", "0g"]),
            "a boolean width": dict(good, width=True),
            "rows not a list": dict(good, rows="6000"),
            "an inverted window": dict(good, x1=-1),
        }
        # A string of the right length whose characters are one-wide rows.
        bad["rows a string that looks like rows"] = dict(
            grid(["6", "0"]), rows="60")
        for label, reply in bad.items():
            with self.subTest(label):
                with self.assertRaises(LiveError):
                    playtest.Grid(reply)

    def test_direction_between_adjacent_tiles(self):
        self.assertEqual(playtest.direction_between((2, 2), (2, 3)), 2)
        self.assertEqual(playtest.direction_between((2, 2), (1, 2)), 4)
        self.assertEqual(playtest.direction_between((2, 2), (3, 2)), 6)
        self.assertEqual(playtest.direction_between((2, 2), (2, 1)), 8)
        with self.assertRaises(ValueError):
            playtest.direction_between((0, 0), (2, 0))


class TestTheBridgeTextForWalking(unittest.TestCase):
    """The new Ruby, checked as text: it cannot be run here."""

    def source(self):
        return bridge.ruby_source()

    def method(self, name):
        body = self.source().split("def self.%s(" % name, 1)[1]
        return body.split("\n  def ", 1)[0]

    def test_the_new_ops_are_served(self):
        for op in ("passability", "locate", "face"):
            with self.subTest(op=op):
                self.assertIn(op, bridge.BRIDGE_OPS)
                self.assertIn('when "%s"' % op, self.source())

    def test_passability_asks_the_games_own_passable_and_recomputes_nothing(self):
        """The whole point: blocking events and boats are in the answer."""
        text = self.method("pass_bits")
        self.assertIn("$game_player.passable?(x, y, direction)", text)
        self.assertNotIn("$game_map.passable?", self.source())
        self.assertNotIn("tileset_flags", self.source())

    def test_a_cell_that_raises_is_left_unpassable_not_fatal(self):
        text = self.method("pass_bits")
        self.assertIn("rescue StandardError", text)
        # StandardError, so RGSSReset (an Exception) and SystemExit pass.
        self.assertNotIn("rescue Exception", text)

    def test_the_read_is_bounded_before_it_loops(self):
        text = self.method("passability")
        self.assertIn("if cells > MAX_PASS_CELLS", text)
        self.assertLess(text.index("if cells > MAX_PASS_CELLS"),
                        text.index(".each do"))
        self.assertIn("MAX_PASS_CELLS = %d" % bridge.MAX_PASS_CELLS,
                      self.source())

    def test_face_will_not_turn_a_moving_player(self):
        text = self.method("face")
        self.assertIn("moving?", text)
        self.assertLess(text.index("moving?"), text.index("set_direction"))

    def test_locate_checks_the_id_before_it_indexes(self):
        text = self.method("locate")
        self.assertIn("is_a?(Integer)", text)
        self.assertLess(text.index("is_a?(Integer)"),
                        text.index("$game_map.events["))

    def test_the_ruby_stays_ascii_and_has_no_require(self):
        self.source().encode("ascii")
        for line in self.source().splitlines():
            self.assertNotRegex(line.strip(), r"^(require|load)\b")


class PlaytestTest(LiveTest):
    """A live test whose game has a tiny map."""

    def play(self, rows, start=(0, 0), paused=True, frames_per_tile=4,
             allow_eval=False, **extra):
        """Start the fake game on a map of `rows` and return the start reply."""
        cell = {"rows": rows, "start": list(start),
                "frames_per_tile": frames_per_tile}
        cell.update(extra.pop("map_extra", {}))
        behaviour = {"map": cell}
        behaviour.update(extra)
        patcher = mock.patch.object(session, "launch_command",
                                    fake_launcher(behaviour))
        patcher.start()
        self.addCleanup(patcher.stop)
        if allow_eval:
            return self.live("start", paused=paused, allow_eval=True)
        return self.live("start", paused=paused)

    def where(self):
        found = self.live("state", sections=["player"])["player"]
        return found["x"], found["y"]

    def walk(self, x, y, **arguments):
        arguments.setdefault("frames_per_step", 6)
        return self.live("walk_to", x=x, y=y, **arguments)

    def requests(self):
        """Every request the session makes from now on: (op, args, seconds)."""
        calls = []
        real = session.Session.request

        def spy(this, op, args=None, seconds=None):
            calls.append((op, args, seconds))
            return real(this, op, args, seconds)

        patcher = mock.patch.object(session.Session, "request", spy)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def clock(self, step):
        """A wall clock that moves `step` seconds on each reading."""
        ticks = itertools.count(0, step)
        patcher = mock.patch.object(playtest, "_clock", lambda: next(ticks))
        patcher.start()
        self.addCleanup(patcher.stop)


class TestWalkingToATile(PlaytestTest):

    def test_it_walks_a_corridor_and_says_so(self):
        self.play(["......"])
        out = self.walk(5, 0)
        self.assertEqual(out["stop_reason"], "arrived")
        self.assertTrue(out["arrived"])
        self.assertEqual(out["steps_taken"], 5)
        self.assertEqual(out["path"], [[1, 0], [2, 0], [3, 0], [4, 0], [5, 0]])
        self.assertEqual(out["path_length"], 5)
        self.assertEqual(out["player"]["x"], 5)
        self.assertEqual(out["player"]["y"], 0)
        # The game agrees, asked separately.
        self.assertEqual(self.where(), (5, 0))

    def test_it_goes_around_a_wall(self):
        self.play(["...", ".#.", "..."])
        out = self.walk(2, 2)
        self.assertEqual(out["stop_reason"], "arrived")
        self.assertEqual(out["steps_taken"], 4)
        self.assertNotIn([1, 1], out["path"])
        self.assertEqual(self.where(), (2, 2))

    def test_an_event_that_blocks_is_routed_around(self):
        """The fake's passability counts an event cell as blocked, as the
        game's passable? does; the route must not go through it."""
        self.play(["...", "..."],
                  map_extra={"events": [{"id": 1, "x": 1, "y": 0}]})
        out = self.walk(2, 0)
        self.assertEqual(out["stop_reason"], "arrived")
        self.assertNotIn([1, 0], out["path"])
        self.assertEqual(out["steps_taken"], 4)

    def test_already_there_is_arrived_without_a_step(self):
        self.play(["..."])
        out = self.walk(0, 0)
        self.assertEqual(out["stop_reason"], "arrived")
        self.assertEqual(out["steps_taken"], 0)
        self.assertEqual(out["path"], [])
        self.assertEqual(out["frames_used"], 0)

    def test_every_step_is_looked_at_before_the_next_key(self):
        """Closed loop: press, step, state -- and never two presses in a row."""
        self.play(["....."])
        calls = self.requests()
        self.walk(3, 0)
        ops = [op for op, _, _ in calls]
        first_press = ops.index("input")
        loop = ops[first_press:]
        self.assertEqual(loop[:3], ["input", "step", "state"])
        presses = [i for i, op in enumerate(loop) if op == "input"]
        self.assertEqual(len(presses), 3)
        for before, after in zip(presses, presses[1:]):
            self.assertIn("state", loop[before:after],
                          "two key presses with no look between them")

    def test_each_step_is_waited_for_in_proportion_to_its_frames(self):
        self.play(["..."])
        calls = self.requests()
        self.walk(2, 0, frames_per_step=6)
        steps = [seconds for op, _, seconds in calls if op == "step"]
        self.assertTrue(steps)
        for seconds in steps:
            self.assertEqual(seconds, session.REPLY_SECONDS + 2.0 * 6 / 60)

    def test_the_game_is_left_paused(self):
        self.play(["..."], paused=False)
        self.assertFalse(self.live("status")["paused"])
        self.walk(1, 0)
        self.assertTrue(self.live("status")["paused"])

    def test_a_target_the_map_does_not_have_is_refused_before_moving(self):
        self.play(["..."])
        with self.assertRaises(ToolError) as caught:
            self.walk(9, 0)
        self.assertIn("outside map 1", str(caught.exception))
        self.assertIn("3x1", str(caught.exception))
        self.assertEqual(self.where(), (0, 0))

    def test_the_wrong_map_is_refused_before_moving(self):
        self.play(["..."])
        calls = self.requests()
        with self.assertRaises(ToolError) as caught:
            self.walk(1, 0, map_id=2)
        self.assertIn("map 1, not map 2", str(caught.exception))
        self.assertNotIn("input", [op for op, _, _ in calls])

    def test_the_right_map_is_accepted(self):
        self.play(["..."])
        self.assertEqual(self.walk(1, 0, map_id=1)["stop_reason"], "arrived")

    def test_a_walled_in_target_is_no_path_and_nothing_is_pressed(self):
        self.play(["..#..."])
        calls = self.requests()
        out = self.walk(5, 0)
        self.assertEqual(out["stop_reason"], "no_path")
        self.assertFalse(out["arrived"])
        self.assertEqual(out["path"], [])
        self.assertEqual(out["frames_used"], 0)
        self.assertNotIn("input", [op for op, _, _ in calls])
        self.assertEqual(self.where(), (0, 0))

    def test_a_route_over_the_ceiling_is_refused_before_moving(self):
        self.play(["......"])
        with mock.patch.object(playtest, "MAX_WALK_PATH", 3):
            with self.assertRaises(ToolError) as caught:
                self.walk(5, 0)
        self.assertIn("5 tiles", str(caught.exception))
        self.assertIn("at most 3", str(caught.exception))
        self.assertEqual(self.where(), (0, 0))

    def test_a_route_at_the_ceiling_is_walked(self):
        self.play(["....."])
        with mock.patch.object(playtest, "MAX_WALK_PATH", 4):
            self.assertEqual(self.walk(4, 0)["stop_reason"], "arrived")


class TestWhyAWalkStops(PlaytestTest):

    def test_blocked_when_the_game_will_not_let_the_player_in(self):
        """(1,0) is free in the passability and not in fact: a character that
        moved there since. The key is pressed twice, then the walk gives up."""
        self.play(["....."], map_extra={"stale": [[1, 0]]})
        out = self.walk(4, 0, frames_per_step=6)
        self.assertEqual(out["stop_reason"], "blocked")
        self.assertFalse(out["arrived"])
        self.assertEqual(out["steps_taken"], 0)
        self.assertEqual(out["player"]["x"], 0)
        # Two attempts of six frames each, counted from the game's own frames.
        self.assertEqual(out["frames_used"], 12)

    def test_blocked_part_way_reports_how_far_it_got(self):
        self.play(["....."], map_extra={"stale": [[3, 0]]})
        out = self.walk(4, 0)
        self.assertEqual(out["stop_reason"], "blocked")
        self.assertEqual(out["steps_taken"], 2)
        self.assertEqual(out["player"]["x"], 2)

    def test_moved_elsewhere_by_a_transfer_in_progress(self):
        """Six frames after entering the teleport tile the transfer is still
        pending: the player is on the tile, transferring."""
        self.play(["....."], map_extra={"teleports": [
            {"x": 2, "y": 0, "map_id": 2, "to_x": 0, "to_y": 0}]})
        out = self.walk(4, 0, frames_per_step=6)
        self.assertEqual(out["stop_reason"], "moved_elsewhere")
        self.assertEqual(out["steps_taken"], 1)

    def test_moved_elsewhere_to_another_map(self):
        self.play(["....."], map_extra={"teleports": [
            {"x": 2, "y": 0, "map_id": 2, "to_x": 0, "to_y": 0}]})
        out = self.walk(4, 0, frames_per_step=12)
        self.assertEqual(out["stop_reason"], "moved_elsewhere")
        self.assertEqual(out["player"]["map_id"], 2)
        self.assertEqual((out["player"]["x"], out["player"]["y"]), (0, 0))

    def test_a_transfer_to_the_same_coordinates_is_not_an_arrival(self):
        """Target (2,0) on map 1, and a teleport on (2,0) to (2,0) of map 2."""
        self.play(["...."], map_extra={"teleports": [
            {"x": 2, "y": 0, "map_id": 2, "to_x": 2, "to_y": 0}]})
        out = self.walk(2, 0, frames_per_step=12)
        self.assertEqual(out["stop_reason"], "moved_elsewhere")
        self.assertFalse(out["arrived"])

    def test_message_open_when_a_step_opens_one(self):
        self.play(["....."], map_extra={"enter_message": [[2, 0]]})
        out = self.walk(4, 0)
        self.assertEqual(out["stop_reason"], "message_open")
        self.assertEqual(out["player"]["x"], 2)
        self.assertEqual(out["message"]["texts"], ["You step on something."])

    def test_message_open_before_the_first_step_means_no_step(self):
        self.play(["....."], map_extra={"events": [
            {"id": 1, "x": 1, "y": 0, "text": "Hello"}]})
        self.live("interact", direction=6)
        calls = self.requests()
        out = self.walk(4, 0)
        self.assertEqual(out["stop_reason"], "message_open")
        self.assertEqual(out["steps_taken"], 0)
        self.assertEqual(out["message"]["texts"], ["Hello"])
        self.assertNotIn("input", [op for op, _, _ in calls])

    def test_arriving_on_a_message_is_still_an_arrival(self):
        self.play(["..."], map_extra={"enter_message": [[2, 0]]})
        out = self.walk(2, 0)
        self.assertEqual(out["stop_reason"], "arrived")
        self.assertEqual(out["message"]["texts"], ["You step on something."])

    def test_a_battle_stops_the_walk(self):
        self.play(["....."], map_extra={"battle": [[2, 0]]})
        out = self.walk(4, 0)
        self.assertEqual(out["stop_reason"], "battle")
        self.assertEqual(out["scene"], "Scene_Battle")
        self.assertEqual(out["player"]["x"], 2)

    def test_a_battle_on_the_target_tile_is_a_battle_not_an_arrival(self):
        self.play(["..."], map_extra={"battle": [[2, 0]]})
        self.assertEqual(self.walk(2, 0)["stop_reason"], "battle")

    def test_the_frame_budget_stops_the_walk(self):
        self.play(["......"])
        out = self.walk(5, 0, frames_per_step=6, max_frames=10)
        self.assertEqual(out["stop_reason"], "frame_budget")
        # One tile of six frames fits; a second would need 12.
        self.assertEqual(out["steps_taken"], 1)
        self.assertEqual(out["frames_used"], 6)

    def test_a_budget_smaller_than_one_step_does_nothing(self):
        self.play(["...."])
        out = self.walk(3, 0, frames_per_step=6, max_frames=5)
        self.assertEqual(out["stop_reason"], "frame_budget")
        self.assertEqual(out["steps_taken"], 0)
        self.assertEqual(out["frames_used"], 0)

    def test_the_wall_clock_stops_the_walk(self):
        self.play(["......"])
        # Each reading of the clock is ten seconds later. The walk starts at
        # 0 and checks before each tile: 10, 20, then 30 is over 25.
        self.clock(10)
        out = self.walk(5, 0, max_seconds=25)
        self.assertEqual(out["stop_reason"], "time_budget")
        self.assertEqual(out["steps_taken"], 2)
        self.assertEqual(self.where(), (2, 0))

    def test_every_reason_has_a_sentence(self):
        for reason in ("arrived", "blocked", "moved_elsewhere", "message_open",
                       "battle", "scene_changed", "frame_budget",
                       "time_budget", "no_path"):
            self.assertTrue(playtest.STOP_NOTES[reason].strip(), reason)


class TestWalkingOnABigMap(PlaytestTest):

    def big(self):
        """70 x 70: more cells than one passability read allows."""
        self.assertGreater(70 * 70, bridge.MAX_PASS_CELLS)
        return ["." * 70] * 70

    def test_a_map_over_the_cell_ceiling_is_read_in_a_window(self):
        self.play(self.big())
        asked = []
        real = live_tools._Driver.passability

        def spy(this, x0, y0, x1, y1):
            asked.append((x0, y0, x1, y1))
            return real(this, x0, y0, x1, y1)

        with mock.patch.object(live_tools._Driver, "passability", spy):
            out = self.walk(3, 0)
        self.assertEqual(out["stop_reason"], "arrived")
        # The probe, then the target and player with a sixteen-tile margin.
        self.assertEqual(asked, [(0, 0, 0, 0), (0, 0, 19, 16)])

    def test_a_small_map_is_read_whole(self):
        self.play(["....", "...."])
        asked = []
        real = live_tools._Driver.passability

        def spy(this, x0, y0, x1, y1):
            asked.append((x0, y0, x1, y1))
            return real(this, x0, y0, x1, y1)

        with mock.patch.object(live_tools._Driver, "passability", spy):
            self.walk(3, 1)
        self.assertEqual(asked, [(0, 0, 0, 0), (0, 0, 3, 1)])

    def test_a_target_too_far_to_plan_in_one_window_is_refused(self):
        self.play(self.big())
        with self.assertRaises(ToolError) as caught:
            self.walk(69, 69)
        self.assertIn("waypoint", str(caught.exception))
        self.assertIn(str(bridge.MAX_PASS_CELLS), str(caught.exception))
        self.assertEqual(self.where(), (0, 0))

    def test_the_fake_refuses_an_oversized_window_as_the_game_does(self):
        """So the Python side's own bound is not the only thing in the way."""
        self.play(self.big())
        running = session.current()
        with self.assertRaises(LiveError) as caught:
            running.request("passability", {"x0": 0, "y0": 0, "x1": 69,
                                            "y1": 69})
        self.assertIn(str(bridge.MAX_PASS_CELLS), str(caught.exception))


class TestArgumentsAreCheckedBeforeTheGameSeesThem(LiveTest):
    """Same strict readers and per-op table as every other live op."""

    def refuses(self, op, **arguments):
        """The refusal text, with proof the request never reached the game.

        Unlike `refused_before_the_game`, callable again within one test.
        """
        if session.current() is None:
            self.live("start")
        with mock.patch.object(session.Session, "request",
                               side_effect=AssertionError("reached the game")):
            with self.assertRaises(ToolError) as caught:
                self.live(op, **arguments)
        return str(caught.exception)

    def test_walk_to_needs_both_coordinates(self):
        self.assertIn("x is required",
                      self.refuses("walk_to", y=1))
        self.assertIn("y is required",
                      self.refuses("walk_to", x=1))

    def test_a_coordinate_must_be_a_whole_number_in_range(self):
        for label, value in (("a boolean", True), ("a float", 1.5),
                             ("a string", "3"), ("a list", [1]),
                             ("negative", -1), ("past the ceiling", 10000),
                             ("a non-ASCII digit", "²")):
            with self.subTest(label):
                self.refuses("walk_to", x=value, y=0)

    def test_an_enormous_coordinate_is_a_refusal_not_a_crash(self):
        """as_int quoted the value back with %d, which CPython refuses above
        4,300 digits: a ValueError out of the reader instead of a ToolError."""
        for value in (10 ** 5000, -10 ** 5000):
            with self.subTest(sign=value > 0):
                message = self.refuses("walk_to", x=value,
                                                       y=0)
                self.assertIn("x must be at", message)
                self.assertLess(len(message), 400)

    def test_the_budget_arguments_are_bounded(self):
        for name, value, text in (
                ("frames_per_step", playtest.MAX_FRAMES_PER_STEP + 1,
                 "at most %d" % playtest.MAX_FRAMES_PER_STEP),
                ("frames_per_step", 0, "at least 1"),
                ("max_frames", playtest.MAX_PLAYTEST_FRAMES + 1,
                 "at most %d" % playtest.MAX_PLAYTEST_FRAMES),
                ("max_seconds", playtest.MAX_PLAYTEST_SECONDS + 1,
                 "at most %d" % playtest.MAX_PLAYTEST_SECONDS),
                ("max_seconds", True, "whole number")):
            with self.subTest(name=name, value=value):
                message = self.refuses(
                    "walk_to", x=1, y=0, **{name: value})
                self.assertIn(text, message)

    def test_an_argument_another_op_reads_is_refused_not_dropped(self):
        for op, extra in (("walk_to", {"keys": ["C"]}),
                          ("walk_to", {"event_id": 1}),
                          ("interact", {"x": 1}),
                          ("scenario", {"frames": 3}),
                          ("status", {"steps": []}),
                          ("teleport", {"max_frames": 5})):
            with self.subTest(op=op, extra=extra):
                arguments = dict(extra)
                if op == "walk_to":
                    arguments.update(x=1, y=0)
                if op == "teleport":
                    arguments.update(map_id=1, x=0, y=0)
                if op == "scenario":
                    arguments["steps"] = [{"op": "status"}]
                message = self.refuses(op, **arguments)
                self.assertIn("does not read", message)

    def test_interact_takes_a_direction_or_an_event_not_both(self):
        message = self.refuses("interact", direction=6,
                                               event_id=1)
        self.assertIn("not both", message)

    def test_interact_checks_its_direction_and_event(self):
        self.assertIn("2, 4, 6 or 8", self.refuses(
            "interact", direction=5))
        self.refuses("interact", event_id=0)
        self.refuses("interact", event_id=10 ** 5000)
        self.refuses("interact", frames=0)

    def test_no_session_is_a_clear_refusal_for_each_new_op(self):
        for op, arguments in (("walk_to", {"x": 1, "y": 0}),
                              ("interact", {}),
                              ("scenario", {"steps": [{"op": "status"}]})):
            with self.subTest(op=op):
                with self.assertRaises(ToolError) as caught:
                    self.live(op, **arguments)
                self.assertIn("start", str(caught.exception))


class TestInteracting(PlaytestTest):

    SHRINE = {"events": [{"id": 1, "x": 4, "y": 0, "text": "Hello",
                          "switch": 7, "variable": 3}]}

    def test_an_event_is_walked_to_faced_and_pressed(self):
        self.play(["....."], map_extra=self.SHRINE)
        out = self.live("interact", event_id=1)
        self.assertTrue(out["interacted"])
        # Four tiles away: three steps to stand beside it, facing right.
        self.assertEqual(out["walked"], {"steps_taken": 3, "path_length": 3})
        self.assertEqual((out["player"]["x"], out["player"]["y"]), (3, 0))
        self.assertEqual(out["facing"], 6)
        self.assertTrue(out["message_open"])
        self.assertEqual(out["message"]["texts"], ["Hello"])
        self.assertEqual(out["changes"]["switches"],
                         [{"id": 7, "from": False, "to": True}])
        self.assertEqual(out["changes"]["variables"],
                         [{"id": 3, "from": 0, "to": 1}])
        self.assertNotIn("gold", out["changes"])
        # And the game really is in that state.
        state = self.live("state")
        self.assertEqual(state["switches"], {"7": True})
        self.assertEqual(state["variables"], {"3": 1})

    def test_it_never_walks_onto_the_event(self):
        self.play(["....."], map_extra=self.SHRINE)
        out = self.live("interact", event_id=1)
        self.assertNotEqual((out["player"]["x"], out["player"]["y"]), (4, 0))

    def test_it_comes_at_the_event_from_the_nearest_side(self):
        """Event at (1,1) of a 3x3; the player at (0,0) is one step from the
        tile above it, (1,0), and from the tile beside it, (0,1), and
        farther from the other two."""
        self.play(["...", "...", "..."],
                  map_extra={"events": [{"id": 1, "x": 1, "y": 1,
                                         "switch": 5}]})
        out = self.live("interact", event_id=1)
        self.assertEqual(out["walked"]["steps_taken"], 1)
        self.assertIn((out["player"]["x"], out["player"]["y"]),
                      [(1, 0), (0, 1)])
        self.assertEqual(out["changes"]["switches"],
                         [{"id": 5, "from": False, "to": True}])

    def test_already_beside_it_there_is_nothing_to_walk(self):
        self.play(["....."], start=(3, 0), map_extra=self.SHRINE)
        out = self.live("interact", event_id=1)
        self.assertEqual(out["walked"], {"steps_taken": 0, "path_length": 0})
        self.assertTrue(out["message_open"])

    def test_a_direction_faces_where_the_player_stands(self):
        self.play(["....."], start=(3, 0), map_extra=self.SHRINE)
        out = self.live("interact", direction=6)
        self.assertEqual(out["facing"], 6)
        self.assertEqual(out["message"]["texts"], ["Hello"])
        self.assertNotIn("walked", out)

    def test_facing_a_wall_changes_nothing_and_says_so(self):
        self.play(["....."], start=(3, 0), map_extra=self.SHRINE)
        out = self.live("interact", direction=2)
        self.assertTrue(out["interacted"])
        self.assertEqual(out["facing"], 2)
        self.assertFalse(out["message_open"])
        self.assertEqual(out["changes"], {})
        self.assertNotIn("message", out)

    def test_facing_a_direction_does_not_walk_even_down_an_open_tile(self):
        """Turning with a direction key would step into open floor; the
        bridge's `face` only turns."""
        self.play(["..."], start=(1, 0))
        out = self.live("interact", direction=4)
        self.assertEqual((out["player"]["x"], out["player"]["y"]), (1, 0))
        self.assertFalse(out["moved"])
        self.assertEqual(out["facing"], 4)

    def test_neither_presses_the_key_facing_the_way_it_already_faces(self):
        self.play(["....."], start=(3, 0), map_extra=self.SHRINE)
        self.live("interact", direction=2)  # face down, a wall
        out = self.live("interact")
        self.assertEqual(out["facing"], 2)
        self.assertFalse(out["message_open"])

    def test_an_event_nothing_can_reach_is_no_path(self):
        self.play(["..#E#", "..###"],
                  map_extra={"events": [{"id": 1, "x": 3, "y": 0}]})
        out = self.live("interact", event_id=1)
        self.assertFalse(out["interacted"])
        self.assertEqual(out["stop_reason"], "no_path")
        self.assertEqual(self.where(), (0, 0))

    def test_an_event_that_is_not_there_is_a_refusal_from_the_game(self):
        self.play(["....."], map_extra=self.SHRINE)
        with self.assertRaises(ToolError) as caught:
            self.live("interact", event_id=9)
        self.assertIn("no event 9", str(caught.exception))

    def test_an_erased_event_is_refused(self):
        self.play(["....."], map_extra={"events": [
            {"id": 1, "x": 4, "y": 0, "erased": True}]})
        with self.assertRaises(ToolError) as caught:
            self.live("interact", event_id=1)
        self.assertIn("erased", str(caught.exception))
        self.assertEqual(self.where(), (0, 0))

    def test_an_open_message_is_not_pressed_through(self):
        self.play(["....."], start=(3, 0), map_extra=self.SHRINE)
        self.live("interact", direction=6)
        out = self.live("interact", direction=6)
        self.assertFalse(out["interacted"])
        self.assertEqual(out["stop_reason"], "message_open")
        # The variable counted once: the second press never reached the event.
        self.assertEqual(self.live("state")["variables"], {"3": 1})

    def test_a_walk_that_does_not_arrive_does_not_press(self):
        self.play(["....."], map_extra=dict(self.SHRINE, stale=[[1, 0]]))
        out = self.live("interact", event_id=1)
        self.assertFalse(out["interacted"])
        self.assertEqual(out["stop_reason"], "blocked")
        self.assertEqual(self.live("state")["switches"], {})

    def test_gold_changes_are_reported(self):
        self.play(["....."], start=(3, 0), map_extra={"events": [
            {"id": 1, "x": 4, "y": 0, "gold": 25}]})
        self.live("set", gold=50)
        out = self.live("interact", direction=6)
        self.assertEqual(out["changes"], {"gold": {"from": 50, "to": 75}})

    def test_the_frames_after_the_press_are_a_choice(self):
        self.play(["....."], start=(3, 0), map_extra=self.SHRINE)
        out = self.live("interact", direction=6, frames=5)
        # One frame is the press itself, inside the five.
        self.assertEqual(out["frames_used"], 5)


class TestWhatChangedBetweenTwoLooks(unittest.TestCase):

    def test_a_switch_that_went_off_is_a_change_too(self):
        out = playtest.diff_states({"switches": {"4": True}, "variables": {}},
                                   {"switches": {}, "variables": {}})
        self.assertEqual(out["changes"]["switches"],
                         [{"id": 4, "from": True, "to": False}])

    def test_a_variable_back_to_zero_leaves_the_listing_and_is_a_change(self):
        out = playtest.diff_states({"switches": {}, "variables": {"2": 9}},
                                   {"switches": {}, "variables": {}})
        self.assertEqual(out["changes"]["variables"],
                         [{"id": 2, "from": 9, "to": 0}])

    def test_ids_are_ordered_as_numbers(self):
        out = playtest.diff_states(
            {"switches": {}, "variables": {}},
            {"switches": {"10": True, "9": True, "100": True},
             "variables": {}})
        self.assertEqual([row["id"] for row in out["changes"]["switches"]],
                         [9, 10, 100])

    def test_nothing_changed_is_an_empty_object_without_a_partial_key(self):
        same = {"switches": {"1": True}, "variables": {"2": 3}, "gold": 5}
        out = playtest.diff_states(same, dict(same))
        self.assertEqual(out, {"changes": {}})

    def test_gold_is_from_and_to(self):
        out = playtest.diff_states(
            {"switches": {}, "variables": {}, "gold": 5},
            {"switches": {}, "variables": {}, "gold": 80})
        self.assertEqual(out["changes"]["gold"], {"from": 5, "to": 80})

    def test_more_changes_than_the_ceiling_are_cut_and_say_so(self):
        many = {str(i): True for i in range(1, playtest.MAX_DIFF_ENTRIES + 50)}
        out = playtest.diff_states({"switches": {}, "variables": {}},
                                   {"switches": many, "variables": {}})
        rows = out["changes"]["switches"]
        self.assertEqual(len(rows), playtest.MAX_DIFF_ENTRIES)
        self.assertEqual(rows[0]["id"], 1)
        self.assertIn(str(playtest.MAX_DIFF_ENTRIES), out["changes_notes"][0])

    def test_a_section_the_bridge_cut_off_is_not_reported_as_unchanged(self):
        out = playtest.diff_states(
            {"switches": {"1": True}, "variables": {}, "truncated": []},
            {"switches": {"1": True}, "variables": {},
             "truncated": ["switches"]})
        self.assertIn("switches", out["changes_notes"][0])

    def test_the_tool_turns_the_notes_into_the_partial_answer_keys(self):
        """The vocabulary's keys are built by `partial`, in the tools layer."""
        fake = {"interacted": True, "changes": {},
                "changes_notes": ["switches were cut"]}
        with mock.patch.object(live_tools, "_running"),                 mock.patch.object(playtest, "interact", return_value=fake):
            out = live_tools._interact(None, {"direction": 6})
        self.assertNotIn("changes_notes", out)
        self.assertIs(out["changes_truncated"], True)
        self.assertEqual(out["changes_note"], "switches were cut")

    def test_a_section_that_could_not_be_read_is_said_so(self):
        out = playtest.diff_states({"switches": None, "variables": {}},
                                   {"switches": None, "variables": {}})
        self.assertIn("could not be read", out["changes_notes"][0])


class TestExpectations(unittest.TestCase):
    """What `expect` accepts and how it judges a state."""

    STATE = {"player": {"map_id": 1, "x": 3, "y": 4, "direction": 2},
             "switches": {"5": True}, "variables": {"3": 5}, "gold": 100,
             "party": [{}, {}], "truncated": [], "errors": {}}

    def judge(self, expect, state=None):
        checks = live_tools._parse_expect(expect)
        return playtest.evaluate(checks, state or self.STATE)

    def test_every_check_that_holds_passes(self):
        self.assertIsNone(self.judge({
            "map_id": 1, "x": 3, "y": {"ge": 4}, "direction": 2,
            "switches": {"5": True, "6": False},
            "variables": {"3": {"gt": 4, "lt": 6}, "9": 0},
            "gold": {"le": 100}, "party_size": 2}))

    def test_each_comparison_by_hand(self):
        # The variable is 5; each row is operator -> (vs 4, vs 5, vs 6).
        table = {"eq": (False, True, False), "ne": (True, False, True),
                 "gt": (True, False, False), "ge": (True, True, False),
                 "lt": (False, False, True), "le": (False, True, True)}
        for name, truth in table.items():
            for number, expected in zip((4, 5, 6), truth):
                with self.subTest(op=name, against=number):
                    failure = self.judge({"variables": {"3": {name: number}}})
                    self.assertEqual(failure is None, expected)

    def test_a_failure_says_what_was_expected_and_what_was_there(self):
        cases = [
            ({"x": 4}, {"check": "player.x", "compare": "eq", "expected": 4,
                        "actual": 3}),
            ({"switches": {"6": True}},
             {"check": "switches[6]", "compare": "eq", "expected": True,
              "actual": False}),
            ({"variables": {"3": {"ne": 5}}},
             {"check": "variables[3]", "compare": "ne", "expected": 5,
              "actual": 5}),
            ({"variables": {"9": 1}},
             {"check": "variables[9]", "compare": "eq", "expected": 1,
              "actual": 0}),
            ({"gold": {"gt": 100}},
             {"check": "gold", "compare": "gt", "expected": 100,
              "actual": 100}),
            ({"party_size": 3},
             {"check": "party_size", "compare": "eq", "expected": 3,
              "actual": 2}),
            ({"map_id": 2},
             {"check": "player.map_id", "compare": "eq", "expected": 2,
              "actual": 1}),
        ]
        for expect, failure in cases:
            with self.subTest(expect=expect):
                self.assertEqual(self.judge(expect), failure)

    def test_the_first_failing_check_is_the_one_reported(self):
        failure = self.judge({"x": 3, "y": 9, "gold": 0})
        self.assertEqual(failure["check"], "player.y")

    def test_a_listing_cut_at_the_ceiling_cannot_vouch_for_a_later_id(self):
        state = dict(self.STATE, truncated=["switches"],
                     switches={"2": True, "3": True})
        self.assertIsNone(self.judge({"switches": {"2": True}}, state))
        self.assertIsNone(self.judge({"switches": {"1": False}}, state))
        failure = self.judge({"switches": {"9": False}}, state)
        self.assertIn("past", failure["note"])

    def test_a_section_that_failed_to_read_is_a_failure_not_a_pass(self):
        state = dict(self.STATE, errors={"variables": "boom"})
        failure = self.judge({"variables": {"3": 5}}, state)
        self.assertIn("could not be read", failure["note"])
        self.assertIn("boom", failure["note"])

    def test_a_variable_holding_text_cannot_be_compared_as_a_number(self):
        state = dict(self.STATE, variables={"3": "#<Foo>"})
        failure = self.judge({"variables": {"3": {"ge": 1}}}, state)
        self.assertIn("not a number", failure["note"])

    def test_no_player_on_a_map_fails_a_position_check(self):
        failure = self.judge({"x": 1}, dict(self.STATE, player=None))
        self.assertIn("no player", failure["note"])

    def test_only_the_sections_the_checks_read_are_asked_for(self):
        checks = live_tools._parse_expect({"x": 1, "gold": 2, "switches":
                                           {"1": True}})
        self.assertEqual(playtest.sections_for(checks),
                         ["player", "gold", "switches"])

    def test_a_bad_expect_is_refused_with_a_reason(self):
        for label, expect, text in (
                ("not an object", [1], "must be an object"),
                ("an unknown check", {"hp": 3}, "no check"),
                ("empty", {}, "checks nothing"),
                ("a boolean number", {"x": True}, "whole number"),
                ("a float", {"gold": 1.5}, "whole number"),
                ("a string", {"y": "3"}, "whole number"),
                ("an unknown operator", {"x": {"near": 3}}, "one of eq"),
                ("an empty comparison", {"x": {}}, "compares with"),
                ("a non-numeric id", {"switches": {"five": True}}, "five"),
                ("a non-boolean switch", {"switches": {"5": 1}},
                 "true or false"),
                ("a huge number", {"gold": 10 ** 5000}, "at most"),
                ("a huge id", {"variables": {"9" * 5000: 1}}, "id")):
            with self.subTest(label):
                with self.assertRaises(ToolError) as caught:
                    live_tools._parse_expect(expect)
                self.assertIn(text, str(caught.exception))

    def test_too_many_comparisons_are_refused(self):
        many = {str(i): True for i in range(1, playtest.MAX_EXPECT_CHECKS + 2)}
        with self.assertRaises(ToolError) as caught:
            live_tools._parse_expect({"switches": many})
        self.assertIn("at most %d" % playtest.MAX_EXPECT_CHECKS,
                      str(caught.exception))
        # Spread over several keys, too: the ceiling is on the total.
        half = {str(i): True for i in range(1, playtest.MAX_EXPECT_CHECKS // 2 + 2)}
        with self.assertRaises(ToolError):
            live_tools._parse_expect({"switches": half,
                                      "variables": {k: 1 for k in half}})

    def test_exactly_the_ceiling_is_accepted(self):
        exact = {str(i): True for i in range(1, playtest.MAX_EXPECT_CHECKS + 1)}
        self.assertEqual(len(live_tools._parse_expect({"switches": exact})),
                         playtest.MAX_EXPECT_CHECKS)


class TestAScenario(PlaytestTest):

    SHRINE = TestInteracting.SHRINE

    def scenario(self, steps, **arguments):
        return self.live("scenario", steps=steps, **arguments)

    def test_a_passing_scenario_reports_every_step(self):
        self.play(["....."], map_extra=self.SHRINE)
        out = self.scenario([
            {"op": "walk_to", "args": {"x": 3, "y": 0, "frames_per_step": 6},
             "expect": {"x": 3, "y": 0, "map_id": 1}},
            {"op": "interact", "args": {"direction": 6},
             "expect": {"switches": {"7": True},
                        "variables": {"3": {"ge": 1}}}},
            {"op": "set", "args": {"gold": 250}, "expect": {"gold": 250}},
        ])
        self.assertTrue(out["passed"])
        self.assertEqual(out["stop_reason"], "completed")
        self.assertEqual(out["steps_run"], 3)
        self.assertEqual(out["steps_total"], 3)
        self.assertNotIn("failed_step", out)
        self.assertEqual([r["op"] for r in out["results"]],
                         ["walk_to", "interact", "set"])
        self.assertEqual([r["index"] for r in out["results"]], [0, 1, 2])
        for record in out["results"]:
            self.assertTrue(record["ok"])
            self.assertTrue(record["expect_passed"])
        self.assertEqual(out["results"][0]["result"]["stop_reason"], "arrived")
        self.assertTrue(out["results"][1]["result"]["message_open"])

    def test_it_stops_at_the_first_failed_expectation(self):
        self.play(["....."], map_extra=self.SHRINE)
        out = self.scenario([
            {"op": "walk_to", "args": {"x": 2, "y": 0, "frames_per_step": 6},
             "expect": {"x": 99}},
            {"op": "set", "args": {"switches": {"9": True}}},
        ])
        self.assertFalse(out["passed"])
        self.assertEqual(out["stop_reason"], "expectation_failed")
        self.assertEqual(out["failed_step"], 0)
        self.assertEqual(out["failed_expect"], {
            "check": "player.x", "compare": "eq", "expected": 99, "actual": 2})
        self.assertEqual(out["results"][0]["expect_failed"],
                         out["failed_expect"])
        self.assertEqual(out["steps_run"], 1)
        self.assertEqual(out["steps_total"], 2)
        # The second step never ran.
        self.assertEqual(self.live("state")["switches"], {})

    def test_the_failing_step_is_named_among_passing_ones(self):
        self.play(["....."], map_extra=self.SHRINE)
        out = self.scenario([
            {"op": "set", "args": {"gold": 10}, "expect": {"gold": 10}},
            {"op": "set", "args": {"gold": 20}, "expect": {"gold": {"lt": 20}}},
            {"op": "set", "args": {"gold": 30}},
        ])
        self.assertEqual(out["failed_step"], 1)
        self.assertEqual(out["failed_expect"]["actual"], 20)
        self.assertEqual([r["index"] for r in out["results"]], [0, 1])

    def test_a_step_without_expect_runs_and_asserts_nothing(self):
        self.play(["...."])
        out = self.scenario([{"op": "set", "args": {"gold": 7}}])
        self.assertTrue(out["passed"])
        self.assertNotIn("expect_passed", out["results"][0])

    def test_a_walk_that_does_not_arrive_fails_the_scenario(self):
        self.play(["....."], map_extra={"stale": [[1, 0]]})
        out = self.scenario([
            {"op": "walk_to", "args": {"x": 4, "y": 0}},
            {"op": "set", "args": {"gold": 1}},
        ])
        self.assertEqual(out["stop_reason"], "step_failed")
        self.assertEqual(out["failed_step"], 0)
        self.assertFalse(out["results"][0]["ok"])
        self.assertEqual(out["results"][0]["result"]["stop_reason"], "blocked")
        self.assertEqual(out["steps_run"], 1)

    def test_an_interaction_that_was_not_made_fails_the_scenario(self):
        self.play(["....."], start=(3, 0), map_extra=self.SHRINE)
        out = self.scenario([
            {"op": "interact", "args": {"direction": 6}},
            {"op": "interact", "args": {"direction": 6}},
        ])
        self.assertEqual(out["stop_reason"], "step_failed")
        self.assertEqual(out["failed_step"], 1)

    def test_a_step_the_game_refuses_is_a_failed_step_with_the_reason(self):
        self.play(["...."])
        out = self.scenario([
            {"op": "set", "args": {"switches": {"500": True}}},
            {"op": "set", "args": {"gold": 1}},
        ])
        self.assertEqual(out["stop_reason"], "step_failed")
        self.assertEqual(out["failed_step"], 0)
        self.assertFalse(out["results"][0]["ok"])
        self.assertIn("outside", out["results"][0]["error"])

    def test_a_walks_path_is_not_repeated_in_a_scenario(self):
        self.play(["...."])
        out = self.scenario([{"op": "walk_to", "args": {"x": 3, "y": 0,
                                                       "frames_per_step": 6}}])
        result = out["results"][0]["result"]
        self.assertNotIn("path", result)
        self.assertEqual(result["path_length"], 3)

    def test_party_size_is_checked_against_the_game(self):
        self.play(["..."], party_size=2)
        self.assertTrue(self.scenario([
            {"op": "status", "expect": {"party_size": 2}}])["passed"])
        failed = self.scenario([
            {"op": "status", "expect": {"party_size": {"gt": 2}}}])
        self.assertEqual(failed["failed_expect"]["actual"], 2)

    def test_a_screenshot_step_reports_without_the_image(self):
        """The fake has no window, so the step fails; what matters is that
        the op is allowed and the failure is the window's, not a refusal."""
        self.play(["..."])
        out = self.scenario([{"op": "screenshot"}])
        self.assertEqual(out["stop_reason"], "step_failed")
        self.assertIn("window", out["results"][0]["error"])


class TestAScenarioCannotReachEval(PlaytestTest):
    """The eval gate has two keys the model cannot turn. A scenario is the
    obvious back door: it runs ops chosen by the model. It must not run eval,
    or start a new session that asks for it, or run another scenario that
    might."""

    def start_with_both_keys(self):
        self.project.allow_live_eval = True
        self.play(["...."], allow_eval=True)

    def refused(self, steps):
        with mock.patch.object(session.Session, "request",
                               side_effect=AssertionError("reached the game")):
            with self.assertRaises(ToolError) as caught:
                self.live("scenario", steps=steps)
        return str(caught.exception)

    def test_eval_is_refused_even_with_both_keys_turned(self):
        self.start_with_both_keys()
        self.assertEqual(self.live("eval", code="1 + 1")["class"], "String")
        message = self.refused([
            {"op": "status"},
            {"op": "eval", "args": {"code": "1 + 1"}}])
        self.assertIn("steps[1]", message)
        self.assertIn("not allowed in a scenario", message)
        self.assertIn("eval", message)

    def test_start_stop_and_scenario_are_refused_by_name(self):
        self.start_with_both_keys()
        for op in ("start", "stop", "scenario"):
            with self.subTest(op=op):
                message = self.refused([{"op": op}])
                self.assertIn("not allowed in a scenario", message)

    def test_the_refusal_happens_before_any_step_runs(self):
        """Step 0 is harmless; if the refusal came late, it would have run."""
        self.start_with_both_keys()
        self.refused([{"op": "set", "args": {"gold": 99}},
                      {"op": "eval", "args": {"code": "1"}}])
        self.assertEqual(self.live("state")["gold"], 0)

    def test_the_allowed_ops_are_exactly_the_harmless_ones(self):
        self.assertEqual(
            set(live_tools.SCENARIO_OPS),
            {"status", "state", "pause", "resume", "step", "input", "set",
             "teleport", "walk_to", "interact", "screenshot"})
        for gated in ("start", "stop", "eval", "scenario"):
            self.assertNotIn(gated, live_tools.SCENARIO_OPS)
        self.assertLessEqual(set(live_tools.SCENARIO_OPS),
                             set(live_tools.LIVE_OP_ARGS))

    def test_the_schema_does_not_offer_them_either(self):
        item = REGISTRY["live"].schema["properties"]["steps"]["items"]
        offered = set(item["properties"]["op"]["enum"])
        self.assertEqual(offered, set(live_tools.SCENARIO_OPS))

    def test_an_op_hidden_in_a_step_argument_is_refused(self):
        self.start_with_both_keys()
        message = self.refused([{"op": "status", "args": {"op": "eval"}}])
        self.assertIn("steps[0]", message)
        self.assertIn("must not hold `op`", message)


class TestAScenarioIsBounded(PlaytestTest):

    def scenario(self, steps, **arguments):
        return self.live("scenario", steps=steps, **arguments)

    def test_too_many_steps_are_refused_before_any_runs(self):
        self.play(["..."])
        steps = [{"op": "set", "args": {"gold": 5}}] * (
            playtest.MAX_SCENARIO_STEPS + 1)
        with self.assertRaises(ToolError) as caught:
            self.scenario(steps)
        self.assertIn("at most %d" % playtest.MAX_SCENARIO_STEPS,
                      str(caught.exception))
        self.assertEqual(self.live("state")["gold"], 0)

    def test_exactly_the_step_ceiling_runs(self):
        self.play(["..."])
        steps = [{"op": "status"}] * playtest.MAX_SCENARIO_STEPS
        out = self.scenario(steps)
        self.assertTrue(out["passed"])
        self.assertEqual(out["steps_run"], playtest.MAX_SCENARIO_STEPS)

    def test_the_step_ceiling_holds_when_the_handler_is_called_directly(self):
        self.play(["..."])
        steps = [{"op": "status"}] * (playtest.MAX_SCENARIO_STEPS + 1)
        with self.assertRaises(ToolError) as caught:
            live_tools._scenario(self.ctx, {"steps": steps})
        self.assertIn("at most", str(caught.exception))

    def test_an_empty_scenario_is_refused(self):
        self.play(["..."])
        with self.assertRaises(ToolError) as caught:
            self.scenario([])
        self.assertIn("needs steps", str(caught.exception))

    def test_the_total_frames_are_bounded(self):
        self.play(["..."])
        out = self.scenario([
            {"op": "step", "args": {"frames": 6}},
            {"op": "step", "args": {"frames": 6}},
            {"op": "step", "args": {"frames": 6}},
        ], max_frames=10)
        # The first six fit; a second six would make twelve.
        self.assertEqual(out["stop_reason"], "frame_budget")
        self.assertFalse(out["passed"])
        self.assertEqual(out["steps_run"], 1)
        self.assertEqual(out["frames_used"], 6)

    def test_frames_spent_by_a_walk_count_against_the_scenario(self):
        self.play(["......"])
        out = self.scenario([
            {"op": "walk_to", "args": {"x": 5, "y": 0, "frames_per_step": 6}},
            {"op": "set", "args": {"gold": 1}},
        ], max_frames=15)
        # The scenario's 15 frames cap the walk at two tiles of six.
        self.assertEqual(out["results"][0]["result"]["stop_reason"],
                         "frame_budget")
        self.assertEqual(out["results"][0]["result"]["steps_taken"], 2)
        self.assertLessEqual(out["frames_used"], 15)
        self.assertEqual(out["stop_reason"], "step_failed")

    def test_a_step_cannot_raise_the_scenarios_ceiling(self):
        self.play(["......"])
        out = self.scenario([
            {"op": "walk_to", "args": {"x": 5, "y": 0, "frames_per_step": 6,
                                       "max_frames": 6000}},
        ], max_frames=15)
        self.assertEqual(out["results"][0]["result"]["steps_taken"], 2)

    def test_the_total_time_is_bounded(self):
        self.play(["..."])
        # Ten seconds per reading: began at 0, then 10 and 20 pass a 25-second
        # budget and 30 does not.
        self.clock(10)
        out = self.scenario([{"op": "status"}] * 5, max_seconds=25)
        self.assertEqual(out["stop_reason"], "time_budget")
        self.assertEqual(out["steps_run"], 2)

    def test_a_walk_inside_is_held_to_what_time_is_left(self):
        self.play(["......"])
        seen = []
        real = live_tools._walk_to

        def spy(ctx, args):
            seen.append(dict(args))
            return real(ctx, args)

        self.clock(10)
        with mock.patch.dict(live_tools._LIVE_OPS, {"walk_to": spy}):
            self.scenario([{"op": "walk_to", "args": {"x": 1, "y": 0,
                                                      "max_seconds": 100}}],
                          max_seconds=45)
        # Began at 0; the first check read 10; 35 seconds left.
        self.assertEqual(seen[0]["max_seconds"], 35)

    def test_step_results_are_cut_at_their_ceiling_and_say_so(self):
        self.play(["..."])
        with mock.patch.object(playtest, "MAX_SCENARIO_RESULT_CHARS", 700):
            out = self.scenario([{"op": "state"}] * 6)
        self.assertIs(out["results_truncated"], True)
        self.assertIn("700", out["results_note"])
        self.assertEqual(out["steps_run"], 6)
        self.assertIn("result", out["results"][0])
        omitted = [r for r in out["results"] if r.get("result_omitted")]
        self.assertTrue(omitted)
        for record in omitted:
            self.assertNotIn("result", record)
            self.assertIn("ok", record)
        total = sum(len(json.dumps(r)) for r in out["results"])
        self.assertLess(total, 700 + 300)

    def test_a_whole_reply_at_the_default_ceiling_fits_the_servers_budget(self):
        from rpgvxace_mcp.server import MAX_TOOL_RESULT_CHARS
        self.assertLess(playtest.MAX_SCENARIO_RESULT_CHARS + 4096,
                        MAX_TOOL_RESULT_CHARS)


class TestAScenarioIsCheckedUpFront(PlaytestTest):

    def refused(self, steps, **arguments):
        if session.current() is None:
            self.play(["...."])
        gold_before = self.live("state")["gold"]
        with self.assertRaises(ToolError) as caught:
            self.live("scenario", steps=steps, **arguments)
        # Nothing ran: a bad later step costs nothing.
        self.assertEqual(self.live("state")["gold"], gold_before)
        return str(caught.exception)

    def test_a_bad_step_names_its_index(self):
        message = self.refused([
            {"op": "set", "args": {"gold": 5}},
            {"op": "step", "args": {"frames": 0}}])
        self.assertIn("steps[1]", message)
        self.assertIn("at least 1", message)

    def test_a_stray_argument_is_refused_not_dropped(self):
        message = self.refused([{"op": "status", "args": {"frames": 3}}])
        self.assertIn("steps[0]", message)
        self.assertIn("does not read", message)

    def test_an_unknown_argument_is_refused(self):
        message = self.refused([{"op": "set", "args": {"golds": 3}}])
        self.assertIn("golds", message)

    def test_an_unknown_op_is_refused_with_the_choices(self):
        message = self.refused([{"op": "dance"}])
        self.assertIn("must be one of", message)
        self.assertIn("walk_to", message)

    def test_a_step_that_is_not_an_object_is_refused(self):
        for step in ("walk_to", 3, None, [{"op": "status"}]):
            with self.subTest(step=step):
                self.assertIn("steps[0]", self.refused([step]))

    def test_an_unknown_step_field_is_refused(self):
        self.assertIn("expects", self.refused(
            [{"op": "status", "expects": {"gold": 1}}]))

    def test_args_must_be_an_object(self):
        self.refused([{"op": "status", "args": [1]}])

    def test_a_bad_expect_names_its_step(self):
        message = self.refused([{"op": "status"},
                                {"op": "status", "expect": {"hp": 1}}])
        self.assertIn("steps[1]", message)

    def test_steps_must_be_a_list(self):
        self.play(["..."])
        with self.assertRaises(ToolError):
            self.live("scenario", steps={"op": "status"})

    def test_the_budget_arguments_are_bounded(self):
        self.play(["..."])
        for name, value in (("max_frames", playtest.MAX_PLAYTEST_FRAMES + 1),
                            ("max_seconds", playtest.MAX_PLAYTEST_SECONDS + 1),
                            ("max_frames", 0)):
            with self.subTest(name=name, value=value):
                with self.assertRaises(ToolError):
                    self.live("scenario", steps=[{"op": "status"}],
                              **{name: value})


class TestPlaytestNeverWaitsForever(PlaytestTest):

    def setUp(self):
        super().setUp()
        for name, value in (("BOOT_SECONDS", 2.0), ("REPLY_SECONDS", 1.0)):
            patcher = mock.patch.object(session, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_wedged_game_is_a_reply_within_a_bounded_time(self):
        self.play(["...."], ignore_requests=True)
        for op, arguments in (("walk_to", {"x": 2, "y": 0}),
                              ("interact", {"direction": 6}),
                              ("scenario", {"steps": [{"op": "status"}]})):
            with self.subTest(op=op):
                started = time.monotonic()
                with self.assertRaises(ToolError) as caught:
                    self.live(op, **arguments)
                self.assertLess(time.monotonic() - started, 10)
                self.assertIn("did not answer", str(caught.exception))

    def test_a_game_that_dies_mid_walk_is_reported(self):
        self.play(["....."], exit_on="step")
        with self.assertRaises(ToolError) as caught:
            self.walk(4, 0)
        self.assertIn("exited", str(caught.exception))


class TestTheSurfaceNamesWhatItDoes(unittest.TestCase):

    def test_the_description_names_the_new_ops_and_their_stop_reasons(self):
        text = REGISTRY["live"].description
        for word in ("walk_to", "interact", "scenario", "arrived", "blocked",
                     "moved_elsewhere", "message_open", "battle",
                     "frame_budget", "time_budget", "no_path", "eval",
                     "closed loop", "passability"):
            with self.subTest(word=word):
                self.assertIn(word, text)

    def test_the_ceilings_are_named_with_their_numbers(self):
        surface = REGISTRY["live"].description + json.dumps(
            REGISTRY["live"].schema)
        for number in (bridge.MAX_PASS_CELLS, playtest.MAX_WALK_PATH,
                       playtest.MAX_FRAMES_PER_STEP,
                       playtest.MAX_PLAYTEST_FRAMES,
                       playtest.MAX_PLAYTEST_SECONDS,
                       playtest.MAX_SCENARIO_STEPS,
                       playtest.MAX_EXPECT_CHECKS, playtest.MAX_DIFF_ENTRIES,
                       playtest.MAX_SCENARIO_RESULT_CHARS):
            with self.subTest(number=number):
                self.assertIn(str(number), surface)

    def test_every_new_argument_is_read_by_its_op(self):
        reads = live_tools.LIVE_OP_ARGS
        self.assertEqual(reads["walk_to"],
                         {"op", "x", "y", "map_id", "frames_per_step",
                          "max_frames", "max_seconds"})
        self.assertEqual(reads["interact"],
                         {"op", "direction", "event_id", "frames",
                          "frames_per_step", "max_frames", "max_seconds"})
        self.assertEqual(reads["scenario"],
                         {"op", "steps", "max_frames", "max_seconds"})

    def test_the_new_ops_are_not_read_only(self):
        self.assertFalse(REGISTRY["live"].definition()["annotations"]
                         ["readOnlyHint"])


# Last in the file, deliberately. `TestTheMainBlockIsLast` holds this.
if __name__ == "__main__":
    unittest.main()
