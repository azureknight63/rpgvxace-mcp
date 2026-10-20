"""The `live` tool: driving a scratch copy of the game over the bridge.

None of this needs RPG Maker. The engine is played by
`tests/fixtures/fake_game.py`, a Python stub that speaks the bridge's file
protocol, and the Ruby half of the bridge is checked as text. The suite
that runs the real Game.exe is `test_live_game.py`, and it is opt-in.

What is held here is the Python side's contract: one session at a time, a
copy that is never the project, a reply or a refusal within a bounded time
whatever the game does, and a session that leaves no process and no
scratch folder behind.
"""

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from build_project import build  # noqa: E402
from tool_case import ToolTest  # noqa: E402

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.live import (  # noqa: E402
    bridge,
    frame,
    session,
    wire,
)
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.render import png  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyHash, RubyString  # noqa: E402
from rpgvxace_mcp.server import MAX_TOOL_RESULT_CHARS, Server  # noqa: E402
from rpgvxace_mcp.tools import live_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import Context, REGISTRY, ToolError  # noqa: E402


FAKE_GAME = HERE / "fixtures" / "fake_game.py"


def fake_launcher(behaviour=None):
    """A `launch_command` that starts the fake game, behaving as asked."""

    def launch(game_dir):
        if behaviour:
            (Path(game_dir) / "fake.json").write_text(json.dumps(behaviour),
                                                      encoding="utf-8")
        return [sys.executable, str(FAKE_GAME)]

    return launch


def tree_digest(root):
    """Every file under a folder and its bytes, as one hash."""
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


class TestTheWireRoundTrips(unittest.TestCase):
    """What Python sends is what Ruby's Marshal.load would read, and back."""

    def test_a_request_survives_marshal(self):
        value = {"seq": 3, "op": "set", "args": {
            "switches": {12: True, 13: False}, "gold": 500,
            "name": "Café", "ratio": 0.5, "none": None,
            "keys": ["C", "DOWN"]}}
        back = wire.from_ruby(marshal_rb.load(marshal_rb.dump(
            wire.to_ruby(value))))
        # Keys come back as strings, because a reply is JSON next.
        value["args"]["switches"] = {"12": True, "13": False}
        self.assertEqual(back, value)

    def test_strings_go_out_tagged_as_utf8(self):
        """Ruby 1.9 reads an untagged String as ASCII-8BIT."""
        out = wire.to_ruby("Café")
        self.assertIsInstance(out, RubyString)
        self.assertEqual(out.ivars, {"E": True})

    def test_a_reply_is_sanitised_on_the_way_in(self):
        """Text from the game is text this server did not write."""
        raw = RubyHash([(RubyString(b"name"),
                         RubyString(b"a\x1b[31mb", {"E": True}))])
        self.assertNotIn("\x1b", wire.from_ruby(raw)["name"])

    def test_a_deep_reply_is_cut_rather_than_recursing(self):
        value = []
        for _ in range(wire.MAX_WIRE_DEPTH + 10):
            value = [value]
        self.assertIn("too deep", json.dumps(wire.from_ruby(value)))


class TestThePngEncoder(unittest.TestCase):
    """A PNG a standard decoder reads back to the same pixels."""

    def decode(self, data):
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        pos, chunks = 8, {}
        while pos < len(data):
            length, kind = struct.unpack(">I4s", data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + length]
            crc = struct.unpack(">I", data[pos + 8 + length:pos + 12 + length])
            self.assertEqual(crc[0], zlib.crc32(kind + body) & 0xFFFFFFFF)
            chunks.setdefault(kind, b"")
            chunks[kind] += body
            pos += 12 + length
        width, height, depth, colour = struct.unpack(">IIBB",
                                                     chunks[b"IHDR"][:10])
        raw = zlib.decompress(chunks[b"IDAT"])
        stride = width * 3 + 1
        rows = [raw[i * stride + 1:(i + 1) * stride] for i in range(height)]
        self.assertTrue(all(raw[i * stride] == 0 for i in range(height)))
        return width, height, depth, colour, b"".join(rows)

    def test_pixels_round_trip(self):
        pixels = bytes(range(2 * 3 * 3)) * 1
        width, height, depth, colour, back = self.decode(
            png.encode(3, 2, pixels, 3))
        self.assertEqual((width, height, depth, colour), (3, 2, 8, 2))
        self.assertEqual(back, pixels)

    def test_bgra_is_converted(self):
        """What GDI hands back is BGRA; a PNG is RGB."""
        self.assertEqual(frame.bgra_to_rgb(b"\x01\x02\x03\xff\x04\x05\x06\x00"),
                         b"\x03\x02\x01\x06\x05\x04")

    def test_a_short_buffer_is_refused(self):
        with self.assertRaises(ValueError):
            png.encode(4, 4, b"\x00" * 10, 3)


class TestTheBridgeText(unittest.TestCase):
    """The Ruby half, checked as text: RGSS3 is Ruby 1.9.2 with no stdlib."""

    def source(self):
        return bridge.ruby_source()

    def test_it_requires_nothing(self):
        """`require` of anything fails inside Game.exe: there is no stdlib.

        Checked on the real engine: socket, json, yaml and zlib all raise
        LoadError there. Zlib and Win32API exist as built-in constants.
        """
        for line in self.source().splitlines():
            self.assertNotRegex(line.strip(), r"^(require|load)\b", line)

    def test_a_step_of_no_frames_is_refused_not_left_pending(self):
        """A step that never finishes answers nothing, and `poll` then
        returns early for every later request, ping and quit included."""
        self.assertIn("frames must be at least 1", self.source())

    def test_a_request_that_is_not_a_hash_is_answered_not_fatal(self):
        """`request["seq"]` in the rescue raised again for an Array, and the
        game went into the crash loop over a stray file."""
        source = self.source()
        self.assertIn("a request is a Hash", source)
        self.assertIn("request.is_a?(Hash) ? request", source)

    def test_it_serves_every_op_the_tool_sends_it(self):
        for op in bridge.BRIDGE_OPS:
            with self.subTest(op=op):
                self.assertIn('when "%s"' % op, self.source())

    def test_every_op_the_tool_forwards_is_one_the_bridge_serves(self):
        forwarded = set(live_tools.FORWARDED_OPS)
        self.assertLessEqual(forwarded, set(bridge.BRIDGE_OPS))

    def test_the_file_names_are_the_ones_python_uses(self):
        """One spelling of each file, substituted into the Ruby."""
        for name in (wire.BRIDGE_DIR, wire.CONFIG_FILE, wire.READY_FILE,
                     wire.REQUEST_FILE, wire.RESPONSE_FILE, wire.CRASH_FILE):
            with self.subTest(name=name):
                self.assertIn('"%s"' % name, self.source())

    def test_every_key_python_accepts_is_a_key_ruby_knows(self):
        for key in bridge.KEYS:
            with self.subTest(key=key):
                self.assertIn(":%s" % key, self.source())

    def test_eval_is_refused_in_ruby_unless_the_session_allowed_it(self):
        """The Ruby half of the eval gate: the one a request file can reach.

        The tool refuses first, on the server flag and the session's own, but
        the request file is plain data in a folder the game polls, so a
        request written by hand never passes through the tool. The bridge
        has to check what it was started with before it evaluates anything.
        """
        method = self.source().split("def self.evaluate(", 1)[1]
        method = method.split("\n  def ", 1)[0]
        self.assertIn('@config["allow_eval"]', method,
                      "the bridge evaluates without checking that this "
                      "session was started with allow_eval")
        self.assertLess(method.index('@config["allow_eval"]'),
                        method.index("eval("),
                        "the bridge evaluates before it checks allow_eval")

    def test_it_is_ascii(self):
        """So no source-encoding comment is needed in a 1.9 script slot."""
        self.source().encode("ascii")

    def test_it_never_rescues_a_reset_or_an_exit(self):
        """F12 raises RGSSReset and `exit` raises SystemExit: both are flow.

        Every broad rescue in the bridge is preceded, in its own method, by
        one that lets those two through -- otherwise F12 would be reported
        as a crash and `quit` could not end the game.
        """
        for method in re.split(r"\n\s*def ", self.source())[1:]:
            if "rescue Exception" not in method:
                continue
            with self.subTest(method=method.split("\n", 1)[0]):
                self.assertIn("rescue RGSSReset, SystemExit", method)
                self.assertLess(method.index("rescue RGSSReset, SystemExit"),
                                method.index("rescue Exception"))

    def test_the_blocks_balance(self):
        """A cheap stand-in for a parser: every opener has an `end`.

        Exact for the bridge only because it is written without one-line
        bodies or modifier forms at the start of a line, which is the
        style this holds it to.
        """
        opener = re.compile(
            r"^\s*(module|class|def|if|unless|case|while|until|begin)\b"
            r"|\bdo(\s*\|[^|]*\|)?\s*$")
        code = [line.split(" # ", 1)[0] for line in self.source().splitlines()
                if not line.lstrip().startswith("#")]
        opened = sum(1 for line in code if opener.search(line))
        closed = sum(1 for line in code if re.match(r"^\s*end\b", line))
        self.assertEqual(opened, closed)


class TestInjection(unittest.TestCase):
    """The bridge goes into a copy's script list, right above Main."""

    def scripts(self):
        def slot(name, code):
            return [1, RubyString(name.encode(), {"E": True}),
                    RubyString(zlib.compress(code.encode()))]
        return [slot("Vocab", "module Vocab; end"),
                slot("Scene_Map", "class Scene_Map; end"),
                slot("Main", "rgss_main { SceneManager.run }\n"),
                slot("", "")]

    def test_it_lands_right_above_main(self):
        scripts = self.scripts()
        index = bridge.inject(scripts)
        self.assertEqual(index, 2)
        self.assertEqual(scripts[2][1].data.decode(), bridge.BRIDGE_SLOT_NAME)
        self.assertIn(b"rgss_main", zlib.decompress(scripts[3][2].data))
        self.assertEqual(zlib.decompress(scripts[2][2].data).decode(),
                         bridge.ruby_source())

    def test_the_slots_above_keep_their_numbers(self):
        """So a backtrace's slot number is the project's own slot number."""
        scripts = self.scripts()
        before = [row[1].data for row in scripts[:2]]
        bridge.inject(scripts)
        self.assertEqual([row[1].data for row in scripts[:2]], before)

    def test_a_project_with_no_main_is_refused(self):
        scripts = self.scripts()[:2]
        with self.assertRaises(ValueError) as caught:
            bridge.inject(scripts)
        self.assertIn("rgss_main", str(caught.exception))

    def test_it_is_not_injected_twice(self):
        scripts = self.scripts()
        bridge.inject(scripts)
        with self.assertRaises(ValueError):
            bridge.inject(scripts)


class LiveTest(ToolTest):
    """A tool test whose `live` sessions run the fake game, and always stop."""

    behaviour = None

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(session, "launch_command",
                                    fake_launcher(self.behaviour))
        patcher.start()
        self.addCleanup(patcher.stop)
        # Scratch copies go to a folder of this test's own, so a real
        # session running on the same machine -- or the orphan sweep --
        # never meets one of these, and the count below is exact.
        self.scratch = Path(tempfile.mkdtemp(prefix="rpgvxace-live-test-"))
        self.addCleanup(shutil.rmtree, self.scratch, ignore_errors=True)
        root = mock.patch.object(session, "scratch_root",
                                 lambda: self.scratch)
        root.start()
        self.addCleanup(root.stop)
        self.addCleanup(self.stop_quietly)

    def stop_quietly(self):
        try:
            REGISTRY["live"].run(self.ctx, {"op": "stop"})
        except ToolError:
            pass

    def refused_before_the_game(self, op, **arguments):
        """The refusal, and proof that the request never reached the game.

        The fake game also refuses what it is sent with a ToolError, so a
        bare `assertRaises` was satisfied by the game's refusal when the
        Python-side check was gone.
        """
        self.live("start")
        with mock.patch.object(session.Session, "request",
                               side_effect=AssertionError("reached the game")):
            with self.assertRaises(ToolError) as caught:
                self.live(op, **arguments)
        return str(caught.exception)

    def live(self, op, **arguments):
        return self.call("live", op=op, **arguments)


class TestASessionStartsAndStops(LiveTest):

    def test_start_reports_a_running_session(self):
        out = self.live("start")
        self.assertEqual(out["op"], "start")
        self.assertTrue(out["running"])
        self.assertIsInstance(out["pid"], int)

    def test_how_it_started_is_not_mistaken_for_how_it_is(self):
        """`paused` was both the start option and the live state.

        A status reply carried `options.paused: true` beside `paused: false`
        after a resume, which reads as a contradiction.
        """
        self.live("start", paused=True)
        self.live("resume")
        out = self.live("status")
        self.assertNotIn("options", out)
        self.assertTrue(out["started_with"]["paused"])
        self.assertFalse(out["paused"])

    def test_the_copy_is_not_the_project_and_leaves_it_alone(self):
        before = tree_digest(self.project.root)
        out = self.live("start")
        game_dir = Path(out["scratch_dir"])
        self.assertFalse(str(game_dir).startswith(str(self.project.root)))
        self.assertTrue((game_dir / "Data").is_dir())
        self.live("state")
        self.live("stop")
        self.assertEqual(tree_digest(self.project.root), before)

    def test_saves_are_not_copied(self):
        (Path(self.project.root) / "Save01.rvdata2").write_bytes(b"save")
        out = self.live("start")
        self.assertFalse((Path(out["scratch_dir"]) / "Save01.rvdata2").exists())

    def test_a_version_control_folder_is_not_copied(self):
        """A game kept in git has a .git folder: history, not game data.

        It is also the folder whose objects are read-only, which kept the
        copy from being deleted at all (see the next test).
        """
        objects = Path(self.project.root) / ".git" / "objects" / "ab"
        objects.mkdir(parents=True)
        (objects / "cdef").write_bytes(b"blob")
        out = self.live("start")
        self.assertFalse((Path(out["scratch_dir"]) / ".git").exists())
        self.assertTrue((Path(out["scratch_dir"]) / "Data").is_dir())

    def test_a_read_only_file_does_not_keep_the_copy_from_being_deleted(self):
        """Windows will not delete a read-only file, and copy2 keeps the flag."""
        locked = Path(self.project.root) / "Data" / "Notes.txt"
        locked.write_bytes(b"read-only in the project")
        os.chmod(locked, stat.S_IREAD)
        self.addCleanup(os.chmod, locked, stat.S_IWRITE | stat.S_IREAD)
        out = self.live("start")
        copy = Path(out["scratch_dir"]) / "Data" / "Notes.txt"
        self.assertTrue(copy.exists())
        stopped = self.live("stop")
        self.assertTrue(stopped["scratch_removed"])
        self.assertFalse(Path(out["scratch_dir"]).exists())

    def test_the_copy_carries_the_bridge(self):
        out = self.live("start")
        scripts = marshal_rb.load(
            (Path(out["scratch_dir"]) / "Data" / "Scripts.rvdata2").read_bytes())
        names = [row[1].data.decode("utf-8", "replace") for row in scripts]
        self.assertIn(bridge.BRIDGE_SLOT_NAME, names)

    def test_stop_ends_the_process_and_deletes_the_copy(self):
        out = self.live("start")
        game_dir = Path(out["scratch_dir"])
        stopped = self.live("stop")
        self.assertTrue(stopped["stopped"])
        self.assertFalse(game_dir.exists())
        self.assertFalse(session.pid_alive(out["pid"]))
        self.assertFalse(self.live("status")["running"])

    def test_a_dead_servers_copy_is_swept_and_a_live_ones_is_not(self):
        """A killed server's job object ends its game; this ends its copy."""

        def folder(name, owner):
            path = self.scratch / (session.SCRATCH_PREFIX + name)
            path.mkdir()
            if owner is not None:
                (path / session.MARKER_FILE).write_text(
                    json.dumps({"server_pid": owner}), encoding="utf-8")
            return path

        dead = folder("dead", 0x7FFFFFF0)
        ours = folder("ours", os.getpid())
        unmarked = folder("unmarked", None)
        self.live("start")
        self.assertFalse(dead.exists())
        self.assertTrue(ours.exists())
        self.assertTrue(unmarked.exists())

    def test_only_one_session_at_a_time(self):
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("start")
        self.assertIn("stop", str(caught.exception))

    def test_an_op_with_no_session_says_to_start_one(self):
        with self.assertRaises(ToolError) as caught:
            self.live("state")
        self.assertIn("start", str(caught.exception))

    def test_status_without_a_session(self):
        self.assertFalse(self.live("status")["running"])

    def test_stop_without_a_session_is_not_an_error(self):
        self.assertFalse(self.live("stop")["stopped"])


class TestTheGameIsDriven(LiveTest):

    def test_state_reads_the_game(self):
        self.live("start")
        out = self.live("state")
        self.assertEqual(out["player"]["map_id"], 1)

    def test_step_runs_exactly_that_many_frames(self):
        self.live("start", paused=True)
        before = self.live("state")["frame"]
        out = self.live("step", frames=7)
        self.assertEqual(out["frame"] - before, 7)
        self.assertTrue(out["paused"])
        self.assertEqual(self.live("state")["frame"], out["frame"])

    def test_pause_holds_the_frame(self):
        self.live("start")
        frame = self.live("pause")["frame"]
        time.sleep(0.1)
        self.assertEqual(self.live("state")["frame"], frame)

    def test_every_request_tells_the_game_it_is_active(self):
        """RGSS3 stops running the moment its window loses focus.

        Found on the real engine: with another window in front, or the game
        minimised, Graphics.update blocks inside the engine and the bridge
        never sees a request. Posting WM_ACTIVATEAPP to the game's own
        window brings it back without taking focus from anyone, so every
        request -- and every wait for an answer -- sends it.
        """
        self.live("start")
        pid = session.current().pid
        calls = []
        with mock.patch.object(session, "keep_awake", calls.append):
            self.live("state")
        self.assertIn(pid, calls)

    def test_set_then_state(self):
        self.live("start")
        self.live("set", switches={"5": True}, variables={"3": 42}, gold=77)
        out = self.live("state")
        self.assertEqual(out["switches"], {"5": True})
        self.assertEqual(out["variables"], {"3": 42})
        self.assertEqual(out["gold"], 77)

    def test_a_refusal_from_the_game_is_a_tool_error(self):
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("set", switches={"500": True})
        self.assertIn("outside", str(caught.exception))

    def test_input_is_queued(self):
        self.live("start", paused=True)
        out = self.live("input", keys=["C"], frames=3)
        self.assertEqual(out["queued"], 1)

    def test_an_unknown_key_is_refused_before_it_is_sent(self):
        message = self.refused_before_the_game("input", keys=["ENTER"])
        # The offending name and the keys it could have been.
        self.assertIn("'ENTER'", message)
        self.assertIn("DOWN", message)

    def test_teleport(self):
        self.live("start")
        out = self.live("teleport", map_id=1, x=2, y=2)
        self.assertEqual(out["player"], {"map_id": 1, "x": 2, "y": 2,
                                         "direction": 2})

    def test_eval_is_refused_unless_the_session_allowed_it(self):
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("eval", code="1 + 1")
        self.assertIn("allow_eval", str(caught.exception))

    def test_eval_when_allowed(self):
        self.project.allow_live_eval = True
        self.live("start", allow_eval=True)
        self.assertEqual(self.live("eval", code="1 + 1")["inspect"],
                         repr("1 + 1"))

    def test_a_screenshot_without_a_window_is_a_clean_refusal(self):
        """The fake game has no window; that is a refusal, not a crash."""
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("screenshot")
        self.assertIn("window", str(caught.exception))


class TestEvalNeedsTheOperatorAndTheSession(LiveTest):
    """`eval` runs arbitrary Ruby in a process on the user's desktop.

    So it takes two keys: `--allow-live-eval` on the server's command line,
    which only the human who configured the server can turn, and
    `allow_eval: true` on the session, which the model asks for. Either
    alone is not enough, and the model can see which one is missing.
    """

    def test_the_server_flag_is_off_by_default(self):
        self.assertFalse(self.project.allow_live_eval)

    def test_start_with_allow_eval_is_refused_without_the_server_flag(self):
        with self.assertRaises(ToolError) as caught:
            self.live("start", allow_eval=True)
        self.assertIn("--allow-live-eval", str(caught.exception))
        self.assertIsNone(session.current(), "the refusal launched a game")
        self.assertEqual(list(self.scratch.iterdir()), [],
                         "the refusal left a scratch copy behind")

    def test_start_without_allow_eval_needs_no_server_flag(self):
        self.assertTrue(self.live("start")["running"])

    def test_the_server_flag_alone_does_not_turn_eval_on(self):
        self.project.allow_live_eval = True
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("eval", code="1 + 1")
        self.assertIn("allow_eval", str(caught.exception))

    def test_eval_checks_the_server_flag_as_well(self):
        """The second check: a session that allowed eval, on a server that
        no longer does, still refuses -- the flag is read per call, not
        copied once into the session."""
        self.project.allow_live_eval = True
        self.live("start", allow_eval=True)
        self.project.allow_live_eval = False
        with self.assertRaises(ToolError) as caught:
            self.live("eval", code="1 + 1")
        self.assertIn("--allow-live-eval", str(caught.exception))

    def test_status_says_whether_the_server_allows_eval(self):
        self.assertIs(self.live("status")["server_allows_eval"], False)
        self.project.allow_live_eval = True
        self.assertIs(self.live("status")["server_allows_eval"], True)
        self.live("start", allow_eval=True)
        out = self.live("status")
        self.assertIs(out["server_allows_eval"], True)
        self.assertTrue(out["started_with"]["allow_eval"])

    def test_project_info_says_whether_the_server_allows_eval(self):
        self.assertIs(self.call("project_info")["live_eval_allowed"], False)
        self.project.allow_live_eval = True
        self.assertIs(self.call("project_info")["live_eval_allowed"], True)

    def test_the_tool_description_names_the_flag(self):
        self.assertIn("--allow-live-eval", REGISTRY["live"].description)

    def test_it_is_a_command_line_option_off_by_default(self):
        from rpgvxace_mcp.__main__ import build_parser
        parser = build_parser()
        self.assertFalse(parser.parse_args(["proj"]).allow_live_eval)
        self.assertTrue(parser.parse_args(
            ["proj", "--allow-live-eval"]).allow_live_eval)

    def test_main_hands_the_flag_to_the_project(self):
        from rpgvxace_mcp import __main__ as entry
        seen = {}

        def opened(*args, **kwargs):
            seen.update(kwargs)
            raise entry.ProjectError("stop here")

        with mock.patch.object(entry, "Project", opened), \
                mock.patch("sys.stderr"):
            self.assertEqual(entry.main(["proj", "--allow-live-eval"]), 1)
        self.assertIs(seen.get("allow_live_eval"), True)


class TestAScreenshotReachesTheClientAsAnImage(LiveTest):
    """The PNG rides in its own content block, after the JSON text."""

    def call_over_the_server(self):
        server = Server(self.project)
        server.initialized = True
        return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "live",
                                         "arguments": {"op": "screenshot"}}})

    def test_the_image_block_follows_the_text(self):
        picture = png.encode(2, 1, b"\x10\x20\x30\x40\x50\x60", 3)
        self.live("start")
        with mock.patch.object(session.Session, "screenshot",
                               lambda _: (picture, {"width": 2, "height": 1})):
            result = self.call_over_the_server()["result"]
        self.assertFalse(result["isError"])
        text, image = result["content"]
        self.assertEqual(json.loads(text["text"])["op"], "screenshot")
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(image["data"]), picture)

    def test_the_largest_screenshot_fits_a_reply(self):
        """A screenshot is halved until it is under its ceiling, and that
        ceiling must leave the JSON room inside the server's budget, or the
        largest allowed screenshot is always refused."""
        inline = -(-session.MAX_SCREENSHOT_BYTES // 3) * 4
        self.assertLessEqual(inline + 256 * 1024, MAX_TOOL_RESULT_CHARS)

    def test_an_image_counts_toward_the_reply_budget(self):
        picture = b"\x89PNG" + b"\x00" * (MAX_TOOL_RESULT_CHARS // 4 * 3)
        self.live("start")
        with mock.patch.object(session.Session, "screenshot",
                               lambda _: (picture, {"width": 1, "height": 1})):
            result = self.call_over_the_server()["result"]
        self.assertTrue(result["isError"])
        # Refused for its size, and not by some other failure on the path.
        self.assertIn(str(MAX_TOOL_RESULT_CHARS), result["content"][0]["text"])
        self.assertEqual(len(result["content"]), 1)


class TestAnArgumentIsCheckedBeforeTheGameSeesIt(LiveTest):

    def test_step_is_bounded(self):
        message = self.refused_before_the_game(
            "step", frames=live_tools.MAX_STEP_FRAMES + 1)
        self.assertIn("at most %d" % live_tools.MAX_STEP_FRAMES, message)

    def test_a_switch_id_must_be_a_number(self):
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("set", switches={"five": True})
        self.assertIn("five", str(caught.exception))

    def test_a_switch_value_must_be_a_boolean(self):
        message = self.refused_before_the_game("set", switches={"5": 1})
        self.assertIn("true or false", message)

    def test_set_needs_something_to_set(self):
        message = self.refused_before_the_game("set")
        self.assertIn("at least one of", message)

    def test_an_argument_another_op_reads_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.live("status", frames=3)
        self.assertIn("frames", str(caught.exception))

    def test_a_teleport_off_the_map_is_refused(self):
        self.live("start")
        width = self.project.load_map(1).get("width")
        with self.assertRaises(ToolError) as caught:
            self.live("teleport", map_id=1, x=width, y=0)
        self.assertIn("outside", str(caught.exception))

    def test_a_teleport_to_a_missing_map_is_refused(self):
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("teleport", map_id=999, x=0, y=0)
        self.assertIn("999", str(caught.exception))


class TestReadOnlyRefusesALaunch(unittest.TestCase):
    """`--read-only` explores a project; launching it is not exploring.

    Only `start` is refused. Every other op acts on a session, and a
    read-only server cannot have one, so refusing them as well would be a
    refusal no call could ever reach.
    """

    def test_start_is_refused(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-live-ro-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build(tmp / "project")
        ctx = Context(Project(tmp / "project", read_only=True))
        with mock.patch.object(session, "launch_command", fake_launcher()):
            with self.assertRaises(Exception) as caught:
                REGISTRY["live"].run(ctx, {"op": "start"})
        self.assertIn("read-only", str(caught.exception))
        self.assertIsNone(session.current())


class TestTheServerNeverWaitsForever(LiveTest):
    """Every way a game can stop answering ends in a reply, in bounded time."""

    def setUp(self):
        super().setUp()
        # Short, so the suite does not spend minutes proving a timeout.
        for name, value in (("BOOT_SECONDS", 2.0), ("REPLY_SECONDS", 1.0)):
            patcher = mock.patch.object(session, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def relaunch(self, behaviour):
        patcher = mock.patch.object(session, "launch_command",
                                    fake_launcher(behaviour))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_game_that_never_comes_up(self):
        self.relaunch({"never_ready": True})
        started = time.monotonic()
        with self.assertRaises(ToolError) as caught:
            self.live("start")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("did not come up", str(caught.exception))
        self.assertIsNone(session.current(),
                          "a failed start left a session behind")

    @unittest.skipUnless(sys.platform == "win32", "a Win32 message box")
    def test_a_dialog_while_booting_is_read_and_not_waited_out(self):
        """RGSS reports an error in a script slot above the bridge in a
        modal message box, and the game then waits for a click forever.

        Found on the real engine: the dialog was read correctly, but the
        refusal opened with "did not come up within 60 seconds" four
        seconds in -- a timeout that had not happened.
        """
        self.relaunch({"dialog": "Script 'Broken' line 1: RuntimeError"})
        with mock.patch.object(session, "BOOT_SECONDS", 30.0):
            started = time.monotonic()
            with self.assertRaises(ToolError) as caught:
                self.live("start")
            self.assertLess(time.monotonic() - started, 15)
        message = str(caught.exception)
        self.assertIn("Script 'Broken' line 1: RuntimeError", message)
        self.assertNotIn("within", message)
        self.assertIsNone(session.current())

    def test_a_failed_start_leaves_no_copy(self):
        self.relaunch({"never_ready": True})
        before = set(session.scratch_root().glob(session.SCRATCH_PREFIX + "*"))
        with self.assertRaises(ToolError):
            self.live("start")
        after = set(session.scratch_root().glob(session.SCRATCH_PREFIX + "*"))
        self.assertEqual(after - before, set())

    def test_a_game_that_stops_answering(self):
        self.relaunch({"ignore_requests": True})
        self.live("start")
        started = time.monotonic()
        with self.assertRaises(ToolError) as caught:
            self.live("state")
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("did not answer", str(caught.exception))
        self.assertTrue(self.live("status")["running"],
                        "a slow answer is not a dead game")

    def test_stop_waits_once_for_a_wedged_game(self):
        """A game that did not take `quit` is not given a second chance.

        Found on the real engine: `eval "loop { }"` wedges the bridge, and
        stop then spent the quit timeout and the exit timeout back to back
        before killing it -- six seconds to end a game already known dead.
        """
        self.relaunch({"ignore_requests": True})
        self.live("start")
        started = time.monotonic()
        stopped = self.live("stop")
        self.assertLess(time.monotonic() - started,
                        session.QUIT_SECONDS * 1.5)
        self.assertTrue(stopped["scratch_removed"])

    def test_a_game_that_exits(self):
        self.relaunch({"exit_on": "state"})
        self.live("start")
        with self.assertRaises(ToolError) as caught:
            self.live("state")
        self.assertIn("exited", str(caught.exception))
        status = self.live("status")
        self.assertFalse(status["running"])
        self.assertEqual(status["exit_code"], 3)

    def test_a_ruby_exception_is_reported_not_hung_on(self):
        self.relaunch({"crash_at_frame": 3})
        self.live("start")
        # Long: the loop leaves on the first status that carries the crash,
        # so a generous deadline costs nothing when the machine is quick, and
        # a loaded one no longer ends in a KeyError that names nothing.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            status = self.live("status")
            if status.get("crash"):
                break
            time.sleep(0.05)
        self.assertTrue(status.get("crash"),
                        "no crash was reported within the deadline: %s"
                        % status)
        self.assertEqual(status["crash"]["class"], "NoMethodError")
        self.assertIn("Fake_Script", status["crash"]["backtrace"][0])
        # The crash loop still answers state, so a post-mortem is possible.
        self.assertEqual(self.live("state")["crash"]["class"], "NoMethodError")
        with self.assertRaises(ToolError) as caught:
            self.live("step", frames=1)
        self.assertIn("NoMethodError", str(caught.exception))


class TestTheToolIsRegistered(unittest.TestCase):

    def test_the_op_table_matches_the_schema(self):
        spec = REGISTRY["live"].schema["properties"]["op"]
        self.assertEqual(set(spec["enum"]), set(live_tools.LIVE_OP_ARGS))

    def test_it_is_not_advertised_as_read_only(self):
        """It launches a process; a client must not auto-approve that."""
        annotations = REGISTRY["live"].definition()["annotations"]
        self.assertFalse(annotations["readOnlyHint"])


# Last in the file, deliberately. `TestTheMainBlockIsLast` holds this.
if __name__ == "__main__":
    unittest.main()
