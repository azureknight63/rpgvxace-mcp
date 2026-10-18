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
from rpgvxace_mcp.live import wire  # noqa: E402


# What the fake answers in its crash loop, as the Ruby bridge does.
CRASH_LOOP_OPS = ("ping", "state", "eval", "quit")


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

    def bridge(self, name):
        return Path(wire.BRIDGE_DIR) / name

    def write(self, name, value):
        wire.write_atomic(self.bridge(name), wire.to_ruby(value))

    def status(self):
        return {"frame": self.frame, "paused": self.paused,
                "scene": "Scene_Map", "crashed": self.crash is not None}

    def respond(self, seq, ok, result=None, error=None):
        reply = {"seq": seq, "ok": ok, "frame": self.frame,
                 "paused": self.paused, "crashed": self.crash is not None}
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
            if self.queue:
                keys, left = self.queue[0]
                self.queue[0] = (keys, left - 1)
                if left - 1 <= 0:
                    self.queue.pop(0)
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

    def poll(self):
        path = self.bridge(wire.REQUEST_FILE)
        if not path.exists():
            return
        if self.behaviour.get("ignore_requests"):
            return
        request = wire.from_ruby(marshal_rb.load(path.read_bytes()))
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
        self.queue.append((list(args["keys"]), args["frames"]))
        return dict(self.status(), queued=len(self.queue))

    def op_state(self, seq, args):
        return dict(self.status(), player=dict(self.player),
                    switches={k: v for k, v in self.switches.items() if v},
                    variables={k: v for k, v in self.variables.items() if v},
                    gold=self.gold, crash=self.crash)

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
