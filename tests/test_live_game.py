"""The `live` tool against the real engine. Opt-in: it opens a game window.

Skipped unless both are set:

    RPGVXACE_LIVE_TESTS=1
    RPGVXACE_TEST_PROJECT=<a VX Ace project folder with Game.exe>

and optionally RPGVXACE_LIVE_SHOTS=<folder> to keep the screenshots it
takes. Windows only, with RPG Maker VX Ace's RTP installed.

Every test runs on a scratch copy and stops its session, and the class
checks at the end that the project's own Data folder and save files are
byte-for-byte what they were. The game window appears on the desktop while
this runs; nothing here sends a key to the operating system.
"""

import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from rpgvxace_mcp.live import bridge, session  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyString  # noqa: E402
from rpgvxace_mcp.tools import live_tools  # noqa: E402,F401
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    ImageReply,
    REGISTRY,
    ToolError,
)


def _guarded_files(root):
    """The files a test run must never change: Data/ and the save slots."""
    root = Path(root)
    found = sorted((root / "Data").rglob("*"))
    found += sorted(root.glob("Save*.rvdata2"))
    return [path for path in found if path.is_file()]


def _digest(root):
    digest = hashlib.sha256()
    for path in _guarded_files(root):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


@unittest.skipUnless(os.environ.get("RPGVXACE_LIVE_TESTS") == "1"
                     and os.environ.get("RPGVXACE_TEST_PROJECT"),
                     "set RPGVXACE_LIVE_TESTS=1 and RPGVXACE_TEST_PROJECT to "
                     "run the real game")
class TestTheRealGame(unittest.TestCase):
    """Start, read, press, teleport, screenshot, crash, stop -- for real."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(os.environ["RPGVXACE_TEST_PROJECT"])
        cls.before = _digest(cls.root)
        # The server flag is on here: this suite exercises eval on the real
        # engine. That it is refused without the flag is checked below.
        cls.ctx = Context(Project(cls.root, allow_live_eval=True))
        shots = os.environ.get("RPGVXACE_LIVE_SHOTS")
        cls.shots = Path(shots) if shots else None

    @classmethod
    def tearDownClass(cls):
        session.stop()
        after = _digest(cls.root)
        if after != cls.before:
            raise AssertionError("the project's Data or save files changed "
                                 "during a live test run")

    def setUp(self):
        self.addCleanup(session.stop)

    def live(self, op, **arguments):
        return REGISTRY["live"].run(self.ctx, dict(arguments, op=op))

    def keep(self, reply, name):
        if self.shots is not None:
            self.shots.mkdir(parents=True, exist_ok=True)
            (self.shots / name).write_bytes(reply.png)

    def test_a_whole_session(self):
        started = self.live("start", paused=True, allow_eval=True)
        self.assertEqual(started["scene"], "Scene_Map")
        game_dir = Path(started["scratch_dir"])
        self.assertNotEqual(game_dir.resolve(), self.root.resolve())
        self.assertFalse(list(game_dir.glob("Save*.rvdata2")),
                         "save files were copied into the test run")

        # Paused at the first frame, and stepping is exact.
        state = self.live("state")
        frame = state["frame"]
        self.assertTrue(state["paused"])
        self.assertEqual(self.live("step", frames=10)["frame"], frame + 10)
        self.assertEqual(self.live("state")["frame"], frame + 10)

        # A virtual key reaches the game's own Input calls.
        self.live("input", keys=["DOWN"], frames=5)
        self.live("step", frames=2)
        probe = self.live("eval", code="[Input.press?(:DOWN), "
                                       "Input.trigger?(:DOWN), Input.dir4]")
        self.assertEqual(probe["inspect"], "[true, false, 2]")

        # And moves the player: held left, the player at least turns left.
        self.live("step", frames=10)
        self.live("input", keys=["LEFT"], frames=20)
        self.live("step", frames=40)
        player = self.live("state", sections=["player"])["player"]
        self.assertEqual(player["direction"], 4)

        # Teleport, within the same map.
        target_y = max(0, player["y"] - 2)
        moved = self.live("teleport", map_id=player["map_id"], x=player["x"],
                          y=target_y)
        self.assertEqual((moved["player"]["x"], moved["player"]["y"]),
                         (player["x"], target_y))

        # Test setup, read back.
        self.live("set", switches={"1": True}, variables={"1": 42}, gold=777)
        state = self.live("state", sections=["switches", "variables", "gold"])
        self.assertTrue(state["switches"].get("1"))
        self.assertEqual(state["variables"].get("1"), 42)
        self.assertEqual(state["gold"], 777)

        # A screenshot of the window, as a real PNG.
        self.live("step", frames=2)
        shot = self.live("screenshot")
        self.assertIsInstance(shot, ImageReply)
        self.assertEqual(shot.png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertGreater(shot.body["width"], 100)
        self.keep(shot, "live_session.png")

        # A Ruby exception in the game's own update is caught and reported.
        self.live("eval", code=(
            "class Scene_Map; alias mcp_live_test_update update; "
            "def update; mcp_live_test_update; nil.deliberate_failure; end; "
            "end"))
        with self.assertRaises(ToolError) as caught:
            self.live("step", frames=3)
        self.assertIn("deliberate_failure", str(caught.exception))
        status = self.live("status")
        self.assertTrue(status["running"], "the crash ended the game")
        self.assertEqual(status["crash"]["class"], "NoMethodError")
        self.assertTrue(any("Scene_Base" in line
                            for line in status["crash"]["backtrace"]))
        # The wreck can still be read.
        self.assertIn("player", self.live("state", sections=["player"]))
        self.keep(self.live("screenshot"), "live_after_crash.png")

        stopped = self.live("stop")
        self.assertTrue(stopped["scratch_removed"])
        self.assertFalse(game_dir.exists())
        self.assertFalse(session.pid_alive(started["pid"]))

    def test_the_title_screen_and_a_real_key(self):
        started = self.live("start", skip_title=False, paused=True)
        self.assertEqual(started["scene"], "Scene_Title")
        self.live("step", frames=60)
        self.keep(self.live("screenshot"), "live_title.png")
        self.live("input", keys=["C"], frames=2)
        self.assertEqual(self.live("step", frames=120)["scene"], "Scene_Map")

    def test_it_keeps_running_without_focus_and_minimised(self):
        """RGSS3 stops whenever its window is inactive; the session must not.

        A small window of this test's own takes the focus, as a user
        clicking back to their editor would, and then the game is
        minimised. Stepping and a screenshot must still work, and the
        focus must stay where it was put.
        """
        import ctypes
        import tkinter
        from rpgvxace_mcp.live.winapi import windows_of
        user32 = ctypes.windll.user32
        self.live("start", paused=True)
        game = [w["hwnd"] for w in windows_of(session.current().pid)
                if w["class"] == "RGSS Player"][0]
        thief = tkinter.Tk()
        self.addCleanup(thief.destroy)
        thief.title("rpgvxace-mcp live test: focus")
        thief.geometry("240x60+20+20")
        thief.attributes("-topmost", True)
        thief.update()
        thief.focus_force()
        thief.update()
        time.sleep(0.5)
        self.assertNotEqual(user32.GetForegroundWindow(), game,
                            "the test could not take focus from the game")
        frame = self.live("state")["frame"]
        self.assertEqual(self.live("step", frames=30)["frame"], frame + 30)
        user32.ShowWindow(ctypes.c_void_p(game), 7)  # minimised, not active
        time.sleep(0.3)
        self.assertEqual(self.live("step", frames=30)["frame"], frame + 60)
        shot = self.live("screenshot")
        self.assertGreater(shot.body["width"], 100)
        self.keep(shot, "live_unfocused.png")
        self.assertNotEqual(user32.GetForegroundWindow(), game,
                            "the session took the focus back")

    def test_eval_is_off_unless_asked_for(self):
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("eval", code="1 + 1")
        self.assertIn("allow_eval", str(caught.exception))

    def test_allow_eval_without_the_server_flag_launches_nothing(self):
        ctx = Context(Project(self.root))
        with self.assertRaises(ToolError) as caught:
            REGISTRY["live"].run(ctx, {"op": "start", "allow_eval": True})
        self.assertIn("--allow-live-eval", str(caught.exception))
        self.assertIsNone(session.current())

    def test_a_wedged_game_is_reported_and_ended(self):
        self.live("start", allow_eval=True)
        with mock.patch.object(session, "REPLY_SECONDS", 3.0):
            started = time.monotonic()
            with self.assertRaises(ToolError) as caught:
                self.live("eval", code="loop { }")
            self.assertLess(time.monotonic() - started, 6)
        self.assertIn("did not answer", str(caught.exception))
        stopping = time.monotonic()
        self.assertTrue(self.live("stop")["scratch_removed"])
        self.assertLess(time.monotonic() - stopping, 10)

    def test_an_error_while_the_scripts_load_is_read_off_the_dialog(self):
        """Above the bridge, nothing can catch it: RGSS shows a message box.

        A slot that raises as it loads is put at the top of the copy's
        script list (the copy's, never the project's), so the game stops in
        the engine's own error dialog. `start` must read that dialog, end
        the game, and delete the copy, rather than wait out its timeout.
        """
        original = bridge.inject

        def inject_and_break(scripts):
            index = original(scripts)
            scripts.insert(0, [1, RubyString(b"Broken", {"E": True}),
                               RubyString(zlib.compress(
                                   b'raise "deliberate load-time failure"'))])
            return index

        scratch = Path(tempfile.mkdtemp(prefix="rpgvxace-live-game-"))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        with mock.patch.object(bridge, "inject", inject_and_break), \
                mock.patch.object(session, "scratch_root", lambda: scratch):
            started = time.monotonic()
            with self.assertRaises(ToolError) as caught:
                self.live("start")
            took = time.monotonic() - started
        self.assertIn("deliberate load-time failure", str(caught.exception))
        self.assertLess(took, session.BOOT_SECONDS)
        self.assertIsNone(session.current())
        self.assertEqual(list(scratch.iterdir()), [],
                         "the copy is still there, so its Game.exe may be too")


# Last in the file, deliberately. `TestTheMainBlockIsLast` holds this.
if __name__ == "__main__":
    unittest.main()
