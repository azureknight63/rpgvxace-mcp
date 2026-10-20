"""A stand-in for Game.exe that speaks the live bridge's file protocol.

The unit tests for `live` run this instead of the real engine, so they need
neither RPG Maker nor Windows. It plays the part of the injected Ruby
bridge: it reads `config.bin`, announces itself in `ready.bin`, and answers
each `request.bin` with a `response.bin` in the same Marshal shape. Frames
are counted the way the bridge counts them -- one per pass of the loop while
not paused -- so `step` can be checked exactly.

Behaviour the tests need to provoke comes from `fake.json` beside the game,
which the test's launcher writes before starting this:

    {"never_ready": true}       never write ready.bin (a game that hangs
                                while booting)
    {"ignore_requests": true}   come up, then never answer (a game wedged
                                in a loop that never reaches Graphics.update)
    {"crash_at_frame": N}       raise at frame N: write crash.bin and keep
                                answering, as the bridge's crash loop does
    {"exit_on": "state"}        exit with status 3 on receiving that op
    {"dialog": "text"}          show a real Windows message box while
                                booting, as RGSS does for an error raised
                                while the scripts load (Windows only)

    {"map": {...}}              a tiny map the player can walk on (below)

A map, for the walk and interact tests, is a grid and a few special cells:

    {"map": {"rows": ["...#", "..E."],      '#' is a wall, '.' is floor
             "start": [0, 0],               where the player begins
             "frames_per_tile": 4,          frames one tile of walking takes
             "events": [{"id": 1, "x": 2, "y": 1, "text": "Hello",
                         "switch": 7, "variable": 3, "gold": 10}],
             "teleports": [{"x": 3, "y": 0, "map_id": 2,
                            "to_x": 0, "to_y": 0}],
             "stale": [[1, 0]],             free in `passability`, but the
                                            player cannot actually enter it
             "battle": [[2, 0]],            entering it starts a battle
             "enter_message": [[0, 1]]}}   entering it opens a message

An event is solid (and `"erased": true` is only what `locate` says about
it). Facing one and pressing C opens its `text` as a message window, sets its
`switch`, adds one to its `variable` and `gold` to the party's; C closes
the message again. Without a map the player does not move, as before.

Run from the scratch game directory, as the real Game.exe is.
"""

import json
import os
import sys
import time
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "src"))

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.live import bridge, wire  # noqa: E402


# What the fake answers in its crash loop, as the Ruby bridge does.
CRASH_LOOP_OPS = ("ping", "state", "eval", "quit")


# RGSS3's key for each direction, and where a step in it goes.
DIRECTION_KEYS = (("DOWN", 2), ("LEFT", 4), ("RIGHT", 6), ("UP", 8))
DELTA = {2: (0, 1), 4: (-1, 0), 6: (1, 0), 8: (0, -1)}


class FakeGame:
    """One fake game, its state, and the bridge loop around it."""

    def __init__(self, behaviour, config):
        self.behaviour = behaviour
        self.config = config
        self.frame = 0
        self.paused = bool(config.get("paused"))
        self.step_left = 0
        self.pending_step = None
        self.crash = None
        self.queue = []
        self.switches = {}
        self.variables = {}
        self.gold = 0
        self.player = {"map_id": 1, "x": 3, "y": 4, "direction": 2}
        self.gap = False
        self.scene = "Scene_Map"
        self.message = None
        self.moving_left = 0
        self.transfer_left = 0
        self.transfer_to = None
        self.map = behaviour.get("map")
        if self.map:
            self.player.update(x=self.map["start"][0], y=self.map["start"][1])

    def bridge(self, name):
        return Path(wire.BRIDGE_DIR) / name

    def write(self, name, value):
        wire.write_atomic(self.bridge(name), wire.to_ruby(value))

    def status(self):
        return {"frame": self.frame, "paused": self.paused,
                "scene": self.scene, "crashed": self.crash is not None}

    def respond(self, seq, ok, result=None, error=None):
        reply = {"seq": seq, "ok": ok, "frame": self.frame,
                 "paused": self.paused, "scene": self.scene,
                 "crashed": self.crash is not None}
        if ok:
            reply["result"] = result
        else:
            reply["error"] = error
        self.write(wire.RESPONSE_FILE, reply)

    def run(self):
        if self.behaviour.get("dialog"):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, self.behaviour["dialog"],
                                             "Fake Game", 0x10)
        if self.behaviour.get("never_ready"):
            while True:
                time.sleep(0.05)
        self.write(wire.READY_FILE, {"token": self.config.get("token"),
                                     "pid": os.getpid(), "frame": 0,
                                     "scene": "Scene_Map"})
        while True:
            time.sleep(0.002)
            self.tick()

    def tick(self):
        if not self.paused and self.crash is None:
            self.frame += 1
            keys, first = [], False
            if self.gap:
                self.gap = False
            elif self.queue:
                entry = self.queue[0]
                keys, first = entry[0], entry[2]
                entry[1:] = [entry[1] - 1, False]
                if entry[1] <= 0:
                    self.queue.pop(0)
                    self.gap = True
            if self.map:
                self.update_player(keys, first)
            if self.frame == self.behaviour.get("crash_at_frame"):
                self.crash = {"class": "NoMethodError",
                              "message": "undefined method `x' for nil",
                              "backtrace": ["{0042} Fake_Script:7:in `boom'"],
                              "frame": self.frame}
                self.write(wire.CRASH_FILE, self.crash)
                self.pending_step = None
            if self.step_left:
                self.step_left -= 1
                if not self.step_left:
                    self.paused = True
                    seq, self.pending_step = self.pending_step, None
                    self.respond(seq, True, self.status())
        if self.pending_step is None:
            self.poll()

    # -- the map ------------------------------------------------------------
    def cell(self, x, y):
        rows = self.map["rows"]
        if 0 <= y < len(rows) and 0 <= x < len(rows[y]):
            return rows[y][x]
        return None

    def event_at(self, x, y):
        for event in self.map.get("events", ()):
            if (event["x"], event["y"]) == (x, y):
                return event
        return None

    def reported_free(self, x, y):
        """What `passability` says: a floor cell with no event on it."""
        return self.cell(x, y) not in (None, "#") and not self.event_at(x, y)

    def really_free(self, x, y):
        return (self.reported_free(x, y)
                and [x, y] not in self.map.get("stale", ()))

    def update_player(self, keys, first):
        """One frame of the player: finish a step, or take input."""
        if self.scene != "Scene_Map":
            return
        if self.transfer_left:
            self.transfer_left -= 1
            if not self.transfer_left:
                self.player.update(self.transfer_to)
            return
        if self.moving_left:
            self.moving_left -= 1
            if not self.moving_left:
                self.arrive()
            return
        if self.message:
            if first and "C" in keys:
                self.message = None
            return
        for key, direction in DIRECTION_KEYS:
            if key in keys:
                self.try_move(direction)
                return
        if first and "C" in keys:
            self.press_action()

    def try_move(self, direction):
        dx, dy = DELTA[direction]
        x, y = self.player["x"] + dx, self.player["y"] + dy
        self.player["direction"] = direction
        if self.really_free(x, y):
            self.player.update(x=x, y=y)
            self.moving_left = self.map.get("frames_per_tile", 4)

    def arrive(self):
        here = [self.player["x"], self.player["y"]]
        for port in self.map.get("teleports", ()):
            if (port["x"], port["y"]) == tuple(here):
                self.transfer_left = 2
                self.transfer_to = {"map_id": port["map_id"],
                                    "x": port["to_x"], "y": port["to_y"]}
        if here in self.map.get("battle", ()):
            self.scene = "Scene_Battle"
        if here in self.map.get("enter_message", ()):
            self.open_message("You step on something.")

    def open_message(self, text):
        self.message = {"busy": True, "visible": True, "texts": [text],
                        "choices": []}

    def press_action(self):
        dx, dy = DELTA[self.player["direction"]]
        event = self.event_at(self.player["x"] + dx, self.player["y"] + dy)
        if event is None:
            return
        if event.get("text"):
            self.open_message(event["text"])
        if event.get("switch"):
            self.switches[str(event["switch"])] = True
        self.gold += event.get("gold", 0)
        if event.get("variable"):
            key = str(event["variable"])
            self.variables[key] = self.variables.get(key, 0) + 1

    def poll(self):
        path = self.bridge(wire.REQUEST_FILE)
        if not path.exists():
            return
        if self.behaviour.get("ignore_requests"):
            return
        try:
            raw = path.read_bytes()
        except OSError:
            # Windows refuses a read while the client's os.replace of the
            # file is in flight; the next tick finds it whole.
            return
        request = wire.from_ruby(marshal_rb.load(raw))
        path.unlink()
        seq, op = request["seq"], request["op"]
        args = request.get("args") or {}
        if op == self.behaviour.get("exit_on"):
            os._exit(3)
        if self.crash is not None and op not in CRASH_LOOP_OPS:
            self.respond(seq, False, error="the game raised %s; only %s "
                         "answer now" % (self.crash["class"],
                                         ", ".join(CRASH_LOOP_OPS)))
            return
        handler = getattr(self, "op_" + op, None)
        if handler is None:
            self.respond(seq, False, error="unknown op %s" % op)
            return
        try:
            result = handler(seq, args)
        except ValueError as exc:
            self.respond(seq, False, error=str(exc))
            return
        if result is not None:
            self.respond(seq, True, result)

    def op_ping(self, seq, args):
        return self.status()

    def op_quit(self, seq, args):
        self.respond(seq, True, self.status())
        os._exit(0)

    def op_pause(self, seq, args):
        self.paused = True
        return self.status()

    def op_resume(self, seq, args):
        self.paused = False
        return self.status()

    def op_step(self, seq, args):
        self.step_left = args["frames"]
        self.paused = False
        self.pending_step = seq
        return None

    def op_input(self, seq, args):
        self.queue.append([list(args["keys"]), args["frames"], True])
        return dict(self.status(), queued=len(self.queue))

    def op_state(self, seq, args):
        everything = dict(
            player=dict(self.player, moving=self.moving_left > 0,
                        transferring=self.transfer_left > 0),
            party=[{"id": 1, "name": "Hero", "level": 1}
                   for _ in range(self.behaviour.get("party_size", 1))],
            switches={k: v for k, v in self.switches.items() if v},
            variables={k: v for k, v in self.variables.items() if v},
            gold=self.gold,
            interpreter={"running": "interpreter_event" in self.behaviour,
                         "event_id": self.behaviour.get("interpreter_event", 0),
                         "in_battle": self.scene == "Scene_Battle"},
            message=self.message or {"busy": False, "visible": False,
                                     "texts": [], "choices": []})
        wanted = args.get("sections") or list(everything)
        return dict(self.status(), crash=self.crash, truncated=[], errors={},
                    **{name: everything[name] for name in wanted})

    def window(self, args):
        """The cells `passability` was asked for, clipped to the map."""
        rows = self.map["rows"]
        width, height = len(rows[0]), len(rows)
        x0 = min(max(args.get("x0", 0), 0), width - 1)
        y0 = min(max(args.get("y0", 0), 0), height - 1)
        x1 = min(max(args.get("x1", width - 1), 0), width - 1)
        y1 = min(max(args.get("y1", height - 1), 0), height - 1)
        if x1 < x0 or y1 < y0:
            raise ValueError("the window is empty")
        cells = (x1 - x0 + 1) * (y1 - y0 + 1)
        if cells > bridge.MAX_PASS_CELLS:
            raise ValueError("a window of %d cells is more than the %d one "
                             "read allows" % (cells, bridge.MAX_PASS_CELLS))
        return width, height, x0, y0, x1, y1

    def op_passability(self, seq, args):
        if not self.map:
            raise ValueError("there is no map to read")
        width, height, x0, y0, x1, y1 = self.window(args)
        rows = []
        for y in range(y0, y1 + 1):
            line = ""
            for x in range(x0, x1 + 1):
                bits = 0
                for bit, direction in ((1, 2), (2, 4), (4, 6), (8, 8)):
                    dx, dy = DELTA[direction]
                    if self.reported_free(x + dx, y + dy):
                        bits |= bit
                line += "%x" % bits
            rows.append(line)
        return {"map_id": self.player["map_id"], "width": width,
                "height": height, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "rows": rows}

    def op_locate(self, seq, args):
        event = self.map_event(args["event_id"])
        return {"map_id": self.player["map_id"], "event_id": args["event_id"],
                "x": event["x"], "y": event["y"], "direction": 2,
                "through": False, "erased": bool(event.get("erased"))}

    def map_event(self, event_id):
        for event in (self.map or {}).get("events", ()):
            if event["id"] == event_id:
                return event
        raise ValueError("map %d has no event %s"
                         % (self.player["map_id"], event_id))

    def op_face(self, seq, args):
        if self.moving_left:
            raise ValueError("the player is moving; step until it stops")
        self.player["direction"] = args["direction"]
        return dict(self.status(), direction=args["direction"])

    def op_set(self, seq, args):
        # Keys arrive as strings: `wire.from_ruby` reads them for JSON,
        # where the Ruby bridge sees the Integers that were sent.
        for key, value in (args.get("switches") or {}).items():
            if not 1 <= int(key) <= 100:
                raise ValueError("switch %s is outside 1..100" % key)
            self.switches[key] = value
        self.variables.update(args.get("variables") or {})
        if args.get("gold") is not None:
            self.gold = args["gold"]
        return self.status()

    def op_teleport(self, seq, args):
        self.player.update(map_id=args["map_id"], x=args["x"], y=args["y"])
        return dict(self.status(), player=dict(self.player))

    def op_eval(self, seq, args):
        if not self.config.get("allow_eval"):
            raise ValueError("eval was not enabled when this session started")
        return {"inspect": repr(args["code"]), "class": "String"}


def main():
    behaviour_file = Path("fake.json")
    behaviour = (json.loads(behaviour_file.read_text(encoding="utf-8"))
                 if behaviour_file.exists() else {})
    config = wire.from_ruby(marshal_rb.load(
        (Path(wire.BRIDGE_DIR) / wire.CONFIG_FILE).read_bytes()))
    FakeGame(behaviour, config).run()


if __name__ == "__main__":
    main()
