"""Hostile files and wrong values that the Marshal layer and `convert` must bound.

Each class pins one thing a sweep found: a few bytes of file costing a hundred
times their size in reply, a writer that wrote what the reader then refuses, a
byte silently cut to fit, and a Bignum or an unhashable key in a place that
only expected a name.
"""

import io
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.convert import (  # noqa: E402
    TextCodec,
    decoded_text,
    refuse_invented_in_replacement,
)
from rpgvxace_mcp.marshal_rb import MAX_DEPTH, MAX_LOAD_WARNINGS, MarshalError  # noqa: E402
from rpgvxace_mcp.docs import workflow_document  # noqa: E402
from rpgvxace_mcp.paths import safe_component  # noqa: E402
from rpgvxace_mcp.project import ProjectError  # noqa: E402
from rpgvxace_mcp.server import Server  # noqa: E402
from rpgvxace_mcp.schema import SchemaWalk  # noqa: E402
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyFloat,
    RubyHash,
    RubyRegexp,
    printable_text,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tool_case import CraftedMap, ToolTest, ValidateTest  # noqa: E402


def count_bytes(n):
    """A Marshal packed integer, without its `i` tag and header."""
    return marshal_rb.dump(n)[3:]


def array_of(count, element):
    return b"\x04\x08[" + count_bytes(count) + element * count


class TestAFileCannotMakeAnUnboundedListOfWarnings(unittest.TestCase):
    """Three bytes of empty `I` record cost about 118 characters of warning.

    The list goes into a tool reply whole, so a 128 MB file was 5 GB of reply.
    """

    def test_repeated_empty_ivar_records_are_capped(self):
        data = array_of(3000, b"I0\x00")
        _, warnings = marshal_rb.load_with_warnings(data)
        self.assertLessEqual(len(warnings), MAX_LOAD_WARNINGS + 1)
        self.assertLess(sum(map(len, warnings)), 20_000)

    def test_the_cap_says_how_many_were_left_out(self):
        _, warnings = marshal_rb.load_with_warnings(array_of(3000, b"I0\x00"))
        self.assertIn("2950", warnings[-1])

    def test_a_few_warnings_are_all_kept(self):
        _, warnings = marshal_rb.load_with_warnings(array_of(3, b"I0\x00"))
        self.assertEqual(len(warnings), 3)

    def test_a_long_symbol_is_clipped_in_its_warning(self):
        name = b"A" * 100_000
        symbol = b"I:" + count_bytes(len(name)) + name + b"\x06:\x06ET"
        data = (b"\x04\x08[" + count_bytes(1) + b"o" + symbol + b"\x00")
        _, warnings = marshal_rb.load_with_warnings(data)
        self.assertTrue(warnings)
        self.assertLess(sum(map(len, warnings)), 1000)


class TestTheWriterRefusesWhatTheReaderWould(unittest.TestCase):
    """A value written deeper than the reader's limit made an unreadable file."""

    def nest(self, depth):
        value = 1
        for _ in range(depth):
            value = [value]
        return value

    def test_deep_is_a_marshal_error_not_a_recursion_error(self):
        with self.assertRaises(MarshalError):
            marshal_rb.dump(self.nest(5000))

    def test_what_is_written_can_be_read_back(self):
        value = self.nest(MAX_DEPTH - 2)
        self.assertEqual(marshal_rb.load(marshal_rb.dump(value)), value)

    def test_just_past_the_limit_is_refused_not_written(self):
        with self.assertRaises(MarshalError):
            marshal_rb.dump(self.nest(MAX_DEPTH + 2))


class TestARegexpOptionIsAByte(unittest.TestCase):
    def test_out_of_range_is_refused_not_cut(self):
        for options in (256, -1, 10 ** 6):
            with self.subTest(options=options), self.assertRaises(MarshalError):
                marshal_rb.dump(RubyRegexp(b"a", options))

    def test_every_byte_still_writes(self):
        marshal_rb.dump(RubyRegexp(b"a", 255))


class TestANameThatIsAHugeIntegerIsDescribed(unittest.TestCase):
    def test_a_bignum_name_does_not_raise(self):
        text = decoded_text(TextCodec("utf-8"), 10 ** 5000)
        self.assertLess(len(text), 200)
        self.assertIn("bits", text)

    def test_an_ordinary_number_is_its_digits(self):
        self.assertEqual(decoded_text(TextCodec("utf-8"), 42), "42")


class TestAReplacementHashWithAnArrayKey(unittest.TestCase):
    """Ruby allows an Array as a hash key; `dict()` of the pairs raises."""

    def test_no_type_error(self):
        existing = RubyHash([([1, 2], 3), ("a", 1)])
        replacement = RubyHash([([1, 2], 4), ("a", 2)])
        refuse_invented_in_replacement(existing, replacement, "field")


class TestASpaceInANameIsKept(unittest.TestCase):
    """`isprintable()` is False for every space but U+0020.

    A Japanese item name with an ideographic space (U+3000) came back with a
    question mark where the space was.
    """

    def test_unicode_spaces_survive(self):
        for space in ("　", " ", " "):
            with self.subTest(space=repr(space)):
                self.assertEqual(printable_text("a" + space + "b"),
                                 "a" + space + "b")

    def test_what_is_unsafe_is_still_replaced(self):
        self.assertEqual(printable_text("a\x1bb\udcffc d"), "a?b?c?d")


class TestHashKeysThatCollideDoNotMakeALookupQuadratic(unittest.TestCase):
    """CPython hashes an int modulo 2**61 - 1, so a Bignum key can be made to
    land in the bucket of any other: n of them cost n squared probes on the
    first lookup, from a file a megabyte or two long.
    """

    def test_colliding_bignum_keys(self):
        modulus = 2 ** 61 - 1
        pairs = [(1 + k * modulus, k) for k in range(30_000)]
        table = RubyHash(pairs)
        started = time.monotonic()
        self.assertEqual(table.get(1 + 29_999 * modulus), 29_999)
        self.assertIsNone(table.get(5))
        self.assertLess(time.monotonic() - started, 2.0)

    def test_ordinary_keys_are_still_indexed(self):
        table = RubyHash([(k, k * 2) for k in range(1000)] + [("a", 1)])
        self.assertEqual(table.get(999), 1998)
        self.assertEqual(table.get("a"), 1)


class TestAFloatKeepsItsSign(unittest.TestCase):
    """0.0 == -0.0, so a float edited to the other zero kept its old bytes."""

    def test_negative_zero_is_not_written_as_zero(self):
        stale = RubyFloat(-0.0, b"0")
        self.assertEqual(marshal_rb.dump(stale), marshal_rb.dump(-0.0))
        self.assertNotEqual(marshal_rb.dump(stale),
                            marshal_rb.dump(RubyFloat(0.0, b"0")))

    def test_an_untouched_float_keeps_its_bytes(self):
        self.assertEqual(marshal_rb.dump(RubyFloat(1.5, b"1.5")),
                         marshal_rb.dump(RubyFloat(1.5, b"1.5")))
        self.assertIn(b"0.1", marshal_rb.dump(RubyFloat(0.1, b"0.1")))


class TestAnEntryIdBelowZeroIsRefused(ToolTest):
    """`value[-1] = entry` replaces the last entry and reports a create."""

    def test_set_entry(self):
        with self.assertRaises(ProjectError):
            self.project.set_entry("actors", -1, None)


class TestAWriteThatFailsIsAProjectError(ToolTest):
    """A refused folder or a held file is a message, not a traceback."""

    def test_a_temp_file_that_cannot_be_made(self):
        with mock.patch("rpgvxace_mcp.project.tempfile.mkstemp",
                        side_effect=PermissionError("denied")):
            with self.assertRaises(ProjectError) as caught:
                self.project.load_data("actors")
                self.project.save_file(self.project.filename_for("actors"))
        self.assertIn("could not write", str(caught.exception))

    def test_a_file_that_cannot_be_deleted(self):
        name = self.project.filename_for("actors")
        with mock.patch("pathlib.Path.unlink",
                        side_effect=PermissionError("in use")):
            with self.assertRaises(ProjectError) as caught:
                self.project.delete_file(name)
        self.assertIn("could not delete", str(caught.exception))

    def test_a_script_table_that_is_not_a_list(self):
        with mock.patch.object(type(self.project), "load_data",
                               return_value={"a": 1}):
            with self.assertRaises(ProjectError):
                self.project.set_script(0, name="x")


class TestASchemaWalkDoesNotCopyForEveryLink(unittest.TestCase):
    """One Array that links to itself N times: each link copied N elements."""

    def test_self_links_are_linear(self):
        shared = []
        shared.extend([shared] * 40_000)
        started = time.monotonic()
        SchemaWalk().visit(shared)
        self.assertLess(time.monotonic() - started, 1.0)


class TestAHostileChangeLog(ToolTest):
    """changes.jsonl lives in the project folder, which is untrusted."""

    def log(self, *lines):
        directory = self.project.work_dir()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "changes.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def test_a_deeply_nested_line_is_skipped(self):
        self.log("[" * 30_000, '{"tool": "ok"}')
        self.assertEqual(self.project.recent_changes(), [{"tool": "ok"}])

    def test_a_line_that_is_not_an_object_is_skipped(self):
        self.log("5", '"x"', "[1]", '{"tool": "ok"}')
        self.assertEqual(self.project.recent_changes(), [{"tool": "ok"}])

    def test_a_negative_limit_is_an_empty_answer(self):
        self.log('{"tool": "ok"}')
        self.assertEqual(self.project.recent_changes(limit=-3), [])


class TestTwoSnapshotsInOneSecondAreTwoSnapshots(ToolTest):
    """The stamp has one-second resolution; the second overwrote the first.

    Two restores in a second made two `pre-restore` safety snapshots, and the
    second replaced the first, which held the user's original data.
    """

    def test_the_first_is_kept(self):
        import datetime as real
        fixed = real.datetime(2030, 1, 2, 3, 4, 5)
        name = self.project.filename_for("actors")
        path = self.project.path_for(name)
        original = path.read_bytes()
        with mock.patch("rpgvxace_mcp.backups.datetime") as clock:
            clock.now.return_value = fixed
            first = self.project.snapshot("pre-restore")
            path.write_bytes(original + b"changed")
            second = self.project.snapshot("pre-restore")
        self.assertNotEqual(first["backup"], second["backup"])
        kept = self.project.path_for(name).parent.parent
        first_copy = self.project._backup_dir(first["backup"]) / name
        self.assertEqual(first_copy.read_bytes(), original)


class TestAMessageWithAHugeIntegerDoesNotEndTheSession(ToolTest):
    """json.loads raises ValueError, not JSONDecodeError, past 4300 digits."""

    def test_the_session_goes_on(self):
        huge = '{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":' + "1" * 5000 + "}}"
        after = '{"jsonrpc":"2.0","id":2,"method":"ping"}'
        stdin = io.StringIO(huge + "\n" + after + "\n")
        stdout = io.StringIO()
        Server(self.project).serve(stdin, stdout)
        replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertGreaterEqual(len(replies), 2)


class TestANameWithATrailingNewlineIsNotAComponent(unittest.TestCase):
    def test_refused(self):
        for name in ("Map001.rvdata2\n", "a\n"):
            with self.subTest(name=name), self.assertRaises(Exception):
                safe_component(name, "file name")

    def test_ordinary_still_passes(self):
        self.assertEqual(safe_component("Map001.rvdata2", "file name"),
                         "Map001.rvdata2")


class TestTheWorkflowGuideDoesNotDenyTheLiveTool(ToolTest):
    def test_no_false_claim(self):
        text = workflow_document(self.project)
        self.assertNotIn("no live state", text)
        self.assertNotIn("does not talk to a running game", text)


class TestABignumIdIsNotATallyKey(unittest.TestCase):
    """An unclamped read id reaches the report, and str() of it raises."""

    def test_read_ids_are_clamped_like_write_ids(self):
        from rpgvxace_mcp.analysis.switch_usage import _Tally
        tally = _Tally()
        tally.read(10 ** 5000, {"where": "x", "how": "y"})
        tally.read(7, {"where": "x", "how": "y"})
        self.assertEqual(list(tally.reads), [7])


class TestAnyCommonEventCallIsNotQuadratic(unittest.TestCase):
    """Each common event with a non-literal call pushed the whole set again."""

    def test_called_from_with_many_unknown_calls(self):
        from rpgvxace_mcp.analysis.dead_ends import _ScriptExits
        exits = _ScriptExits()
        exits.common_calls = {i: None for i in range(1, 30_001)}
        exits.anywhere = {1}
        started = time.monotonic()
        everything = exits.called_from([])
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(set(range(1, 30_001)) <= everything)

    def test_a_known_chain_is_still_followed(self):
        from rpgvxace_mcp.analysis.dead_ends import _ScriptExits
        exits = _ScriptExits()
        exits.common_calls = {1: {2}, 2: {3}, 3: set(), 4: set()}
        exits.anywhere = {1}
        self.assertEqual(exits.called_from([]), {1, 2, 3})


class TestAnEnormousIdInAScriptIsUnresolvedNotAnError(unittest.TestCase):
    """int() of more than 4,300 digits raises, and it ended the whole tool."""

    def test_a_transfer_literal(self):
        from rpgvxace_mcp.analysis.reachability import _scan_script
        scan = _scan_script("$game_temp.reserve_transfer(%s, 1, 1)" % ("9" * 5000))
        self.assertEqual(scan.literals, [])
        self.assertEqual(scan.unresolved, 1)

    def test_a_new_map_literal(self):
        from rpgvxace_mcp.analysis.reachability import _scan_script
        scan = _scan_script("$game_player.new_map_id = %s;" % ("9" * 5000))
        self.assertEqual(scan.literals, [])

    def test_ordinary_literals_are_still_read(self):
        from rpgvxace_mcp.analysis.reachability import _scan_script
        self.assertEqual(
            _scan_script("$game_temp.reserve_transfer(12, 1, 1)").literals, [12])


class TestAnEnormousCommonEventIdIsAnyNotAnError(ToolTest):
    def test_a_script_naming_one(self):
        from rpgvxace_mcp.analysis.dead_ends import _calls
        from rpgvxace_mcp.schema import new_event_command
        text = "reserve_common_event(%s)" % ("9" * 5000)
        command = new_event_command(355, 0, [self.project.codec.make_string(text)])
        self.assertIsNone(_calls(self.project, [command]))


class TestAStatisticOverHugeNumbersIsSkippedNotAnError(unittest.TestCase):
    def test_a_bignum_is_not_a_number(self):
        from rpgvxace_mcp.analysis.stat_outliers import _number
        self.assertIsNone(_number(10 ** 400))
        self.assertIsNone(_number(-10 ** 400))
        self.assertEqual(_number(50), 50)
        self.assertEqual(_number(2.5), 2.5)

    def test_extreme_floats_do_not_overflow_the_fences(self):
        from rpgvxace_mcp.analysis.stat_outliers import _summarize
        values = [("a%d" % i, i, float(i)) for i in range(10)]
        values += [("big", 99, 1e308), ("small", 100, -1e308)]
        _summarize(values, limit=5)

    def test_round_refuses_a_non_finite(self):
        from rpgvxace_mcp.analysis.stat_outliers import _round
        self.assertIsNone(_round(float("inf")))
        self.assertIsNone(_round(float("nan")))
        self.assertEqual(_round(2.0), 2)

    def test_a_table_with_no_depth_is_not_read(self):
        from rpgvxace_mcp.analysis.stat_outliers import _read
        from rpgvxace_mcp.rubytypes import RubyObject, Table
        table = Table(10, 10, 0, [])
        entry = RubyObject("RPG::Class", {"@exp_params": table})
        self.assertIsNone(_read(entry, ("curve", "exp_params", 1, 1)))


class TestAnRtpNameFromGameIniIsShort(ToolTest):
    """Every incomplete-pool finding quotes the unresolved RTP names, and
    there can be thousands of findings: a 1 MB `RTP=` multiplied by them."""

    def test_a_huge_name_is_clipped(self):
        (self.tmp / "project" / "Game.ini").write_text(
            "[Game]\nRTP=" + "A" * 500_000 + "\n", encoding="cp1252")
        project = self.reopen().project
        self.assertTrue(project.rtp_names)
        self.assertTrue(all(len(name) <= 80 for name in project.rtp_names))

    def test_a_real_name_is_unchanged(self):
        (self.tmp / "project" / "Game.ini").write_text(
            "[Game]\nRTP=RPGVXAce\n", encoding="cp1252")
        self.assertEqual(self.reopen().project.rtp_names, ["RPGVXAce"])


class TestAnIdTooBigToBeAnIdIsNotAnId(unittest.TestCase):
    """A Bignum out of a file is not a game id, and formatting one into a
    finding raises past 4,300 digits."""

    def test_the_walk_reads_it_as_absent(self):
        from rpgvxace_mcp.analysis.walk import _int
        self.assertIsNone(_int(10 ** 5000))
        self.assertIsNone(_int(-10 ** 5000))
        self.assertEqual(_int(1234), 1234)
        self.assertIsNone(_int(True))


class TestValidateSurvivesBignumIdsInAFile(ValidateTest):
    def test_a_crafted_map(self):
        from rpgvxace_mcp import marshal_rb
        from rpgvxace_mcp.rubytypes import RubyHash
        from rpgvxace_mcp.schema import new_event, new_event_command, new_page
        huge = 10 ** 5000
        page = new_page()
        page.set("list", [
            new_event_command(201, 0, [0, huge, huge, huge, 0, 0]),
            new_event_command(0, 0, []),
        ])
        event = new_event(1, "E")
        event.set("pages", [page, page])
        events = RubyHash()
        events.set(huge, event)
        game_map = self.project.load_map(1)
        game_map.set("events", events)
        game_map.set("width", huge)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()
        out = self.call("validate")
        self.assertIsInstance(out, dict)


class TestGameIniCannotPointTheScriptPackOutsideTheCopy(unittest.TestCase):
    """`Scripts=` is the project's own text: an absolute or `..` path made
    the bridge-injected pack be written outside the scratch copy."""

    def test_the_path_stays_inside(self):
        import tempfile
        from rpgvxace_mcp.live.session import _scripts_path
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game"
            game.mkdir()
            default = game / "Data" / "Scripts.rvdata2"
            for value in ("C:" + chr(92) + "elsewhere" + chr(92) + "x.rvdata2",
                          ".." + chr(92) + ".." + chr(92) + "x.rvdata2",
                          "/etc/x", "Data/../../x"):
                with self.subTest(value=value):
                    (game / "Game.ini").write_text(
                        "[Game]\nScripts=%s\n" % value, encoding="utf-8")
                    chosen = _scripts_path(game)
                    self.assertEqual(chosen, default)

    def test_a_real_relative_path_is_kept(self):
        import tempfile
        from rpgvxace_mcp.live.session import _scripts_path
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game"
            game.mkdir()
            (game / "Game.ini").write_text(
                "[Game]\nScripts=Data" + chr(92) + "Other.rvdata2\n", encoding="utf-8")
            self.assertEqual(_scripts_path(game), game / "Data" / "Other.rvdata2")


class TestAReplyFromTheGameCannotExpandWithoutLimit(unittest.TestCase):
    """A Marshal link shares a list, and 32 levels of [x, x] is 2**32 nodes."""

    def test_a_shared_list_is_bounded(self):
        from rpgvxace_mcp.live.wire import from_ruby
        x = [1]
        for _ in range(32):
            x = [x, x]
        started = time.monotonic()
        shown = from_ruby(x)
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertTrue(shown)

    def test_an_ordinary_reply_is_unchanged(self):
        from rpgvxace_mcp.live.wire import from_ruby
        self.assertEqual(from_ruby([1, [2, 3], None]), [1, [2, 3], None])


class TestAScriptSlotBombIsRefused(unittest.TestCase):
    def test_a_slot_that_inflates_past_the_limit_is_empty(self):
        import zlib
        from rpgvxace_mcp.live import bridge
        from rpgvxace_mcp.rubytypes import RubyString
        bomb = zlib.compress(b"A" * (80 * 1024 * 1024), 9)
        row = [1, RubyString(b"x"), RubyString(bomb)]
        self.assertEqual(bridge._slot_code(row), b"")

    def test_an_ordinary_slot_is_read(self):
        import zlib
        from rpgvxace_mcp.live import bridge
        from rpgvxace_mcp.rubytypes import RubyString
        row = [1, RubyString(b"x"), RubyString(zlib.compress(b"puts 1"))]
        self.assertEqual(bridge._slot_code(row), b"puts 1")


class TestTheSessionIgnoresAMarkerItCannotRead(unittest.TestCase):
    def test_a_marker_with_a_non_numeric_owner(self):
        import tempfile
        from unittest import mock
        from rpgvxace_mcp.live import session
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / (session.SCRATCH_PREFIX + "x")
            folder.mkdir()
            (folder / session.MARKER_FILE).write_text(
                '{"server_pid": "x"}', encoding="utf-8")
            with mock.patch.object(session, "scratch_root", return_value=Path(tmp)):
                self.assertEqual(session.sweep_orphans(), [])

    def test_an_xp_save_is_a_save(self):
        from rpgvxace_mcp.live import session
        for name in ("Save1.rxdata", "Save01.rvdata2", "Save3.rvdata"):
            with self.subTest(name=name):
                self.assertTrue(session._SAVE_FILE.match(name))
        self.assertFalse(session._SAVE_FILE.match("Actors.rxdata"))


class TestATileWhoseShapeTheTableLacksIsNotDrawn(unittest.TestCase):
    """A wall has 16 shapes and a waterfall 4; `autotile_block` says 0-47."""

    def test_compose_returns_none_not_an_index_error(self):
        from rpgvxace_mcp.autotile import (
            TILE_ID_MAX, autotile_block, is_autotile)
        from rpgvxace_mcp.render.canvas import new_surface
        from rpgvxace_mcp.render.tiles import (
            TABLES, compose_tile, sheet_needed)
        found = []
        for tile_id in range(1, TILE_ID_MAX):
            if sheet_needed(tile_id) is None or not is_autotile(tile_id):
                continue
            try:
                _, _, _, table, shape = autotile_block(tile_id)
            except Exception:
                continue
            if table in TABLES and shape >= len(TABLES[table]):
                found.append(tile_id)
                break
        self.assertTrue(found, "no tile id with an out-of-range shape found")
        sheets = {sheet_needed(found[0]): new_surface(1024, 1024)}
        self.assertIsNone(compose_tile(found[0], sheets))


class TestAPageTriggerThatIsNotANumberDoesNotBreakTheRead(ToolTest):
    def test_a_list_trigger(self):
        game_map = self.project.load_map(1)
        events = game_map.get("events")
        event = events.sorted_items()[0][1]
        page = event.get("pages")[0]
        page.set("trigger", [0, 0])
        event.set("x", [1] * 50)
        from rpgvxace_mcp import marshal_rb
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()
        out = self.call("event_get", map_id=1)
        self.assertTrue(out["events"])
        shown = out["events"][0]["pages"][0]
        self.assertEqual(shown["trigger"], shown["trigger_code"])

    def test_the_shared_budget_goes_where_to_json_expects_it(self):
        """`walk` is the fourth parameter; the third is the table mode."""
        from unittest import mock
        from rpgvxace_mcp.tools import event_tools
        real = event_tools.to_json
        calls = []

        def spy(value, codec, tables="summary", walk=None):
            calls.append(tables)
            return real(value, codec, tables, walk)

        with mock.patch.object(event_tools, "to_json", spy):
            self.call("event_get", map_id=1)
        self.assertTrue(calls)
        self.assertTrue(all(isinstance(mode, str) for mode in calls), calls)


class TestTheRegexGateSeesWhatANegatedClassMatches(unittest.TestCase):
    """`[^a]+[^a]+b` was accepted: its alphabet was {a, b}, and a run of `a`
    fails at once while a run of `b` matches at once -- so nothing the gate
    measured was slow, and 3,000 Japanese characters took minutes."""

    def refused(self, pattern):
        from rpgvxace_mcp.tools import pattern_safety
        with self.assertRaises(pattern_safety.PatternTooCostly):
            pattern_safety.check_pattern(pattern)

    def test_a_negated_literal(self):
        self.refused(r"[^a]+[^a]+b")

    def test_a_negated_class(self):
        self.refused(r"[^\x00a]+[^\x00a]+b")

    def test_a_range_is_not_only_its_endpoints(self):
        self.refused(r"[a-z]+[a-z]+[az]")

    def test_a_repeated_body_of_two_characters(self):
        """`(?:ab)+(?:ab)+!` took 4.7 s on 2,000 characters and was accepted:
        the subjects were runs of one character and a mixture that put the
        literal `!` between `a` and `b`, so nothing alternated `ab`."""
        self.refused(r"(?:ab)+(?:ab)+!")

    def test_a_repeated_body_of_a_letter_and_a_digit(self):
        self.refused(r"(?:x\d)+(?:x\d)+!")

    def test_an_ordinary_search_still_passes(self):
        from rpgvxace_mcp.tools import pattern_safety
        for pattern in (r"Potion", r"^Hero\d+$", r"[A-Z][a-z]+ Sword",
                        r"HP: ?\d+"):
            with self.subTest(pattern=pattern):
                pattern_safety.check_pattern(pattern)


class TestAPaintKindIsAShortAsciiSpelling(unittest.TestCase):
    """`str.isdigit()` is True for "\u00b2", which `int()` then rejects with a
    bare ValueError; a 10 MB spelling was echoed whole into every refusal."""

    def parse(self, spec):
        from rpgvxace_mcp.tools.map_paint_tools import _parse_kind
        return _parse_kind(spec, "kind")

    def test_a_non_ascii_digit_is_a_refusal(self):
        from rpgvxace_mcp.tools.registry import ToolError
        for spec in ("\u00b2", "A2:\u00b2", "\u0663"):
            with self.subTest(spec=spec), self.assertRaises(ToolError):
                self.parse(spec)

    def test_a_long_spelling_is_refused_before_it_is_echoed(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError) as caught:
            self.parse("A2:0" + " x" * 5_000_000)
        self.assertLess(len(str(caught.exception)), 2000)

    def test_a_digit_run_past_the_int_limit_is_a_refusal(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            self.parse("9" * 5000)

    def test_ordinary_spellings_still_parse(self):
        self.assertEqual(self.parse("A2:0").sheet, "A2")
        self.assertEqual(self.parse(" a5:3 ").sheet, "A5")


class TestAppendingToADamagedCommandListIsARefusal(unittest.TestCase):
    def test_a_list_that_is_a_number(self):
        from rpgvxace_mcp.tools.event_tools import _appended
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            _appended(None, 5, [])

    def test_a_list_that_is_a_string_is_not_split_into_characters(self):
        from rpgvxace_mcp.tools.event_tools import _appended
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            _appended(None, "abc", [])


class TestAScopeListIsNotRunOnceForEachRepeat(unittest.TestCase):
    """`scopes=["summary"] * 1,000,000` ran the summary a million times and
    echoed the million names back."""

    ALLOWED = ("summary", "lint", "reachability")

    def asked(self, scopes):
        from rpgvxace_mcp.tools.registry import scopes_arg
        return scopes_arg({"scopes": scopes}, "scopes", self.ALLOWED)

    def test_duplicates_collapse_in_first_seen_order(self):
        self.assertEqual(
            self.asked(["lint", "summary", "lint"] * 1000),
            ["lint", "summary"])

    def test_an_ordinary_list_is_unchanged(self):
        self.assertEqual(self.asked(["summary", "lint"]), ["summary", "lint"])

    def test_none_asked_runs_them_all(self):
        self.assertEqual(self.asked([]), list(self.ALLOWED))

    def test_a_flood_of_unknown_names_is_a_short_refusal(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError) as caught:
            self.asked(["nope%d" % i for i in range(50_000)])
        self.assertLess(len(str(caught.exception)), 4000)


class TestTheLiveToolChecksWhatItForwards(unittest.TestCase):
    """`sections` went to the game unchecked (the bridge builds a method name
    from it); an id key of non-ASCII or enormous digits raised a bare
    ValueError instead of a refusal."""

    def test_an_unknown_state_section_is_refused_here(self):
        from rpgvxace_mcp.tools import live_tools
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError) as caught:
            live_tools._state(None, {"sections": ["player", "exit"]})
        self.assertIn("exit", str(caught.exception))

    def test_a_section_that_is_not_text_is_refused(self):
        from rpgvxace_mcp.tools import live_tools
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            live_tools._state(None, {"sections": [5]})

    def test_a_non_ascii_digit_key_is_a_refusal(self):
        from rpgvxace_mcp.tools import live_tools
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            live_tools._ids({"switches": {"²": 1}}, "switches",
                            lambda where, value: value)

    def test_an_enormous_digit_key_is_a_refusal(self):
        from rpgvxace_mcp.tools import live_tools
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            live_tools._ids({"switches": {"9" * 5000: 1}}, "switches",
                            lambda where, value: value)


class TestMapEditAgreesWithTheTree(ToolTest):
    """Deleting a map that is neither in the tree nor on disk re-saved
    MapInfos and reported success; a damaged tree entry crashed a create."""

    def test_deleting_a_map_that_does_not_exist_is_a_refusal(self):
        from rpgvxace_mcp.tools.registry import ToolError
        before = self.project.recent_changes(50)
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="delete", map_id=999)
        self.assertIn("999", str(caught.exception))
        self.assertEqual(self.project.recent_changes(50), before)

    def test_the_preview_refuses_it_too(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            self.call("map_edit", op="delete", map_id=999, dry_run=True)

    def test_a_tree_entry_with_a_text_order_does_not_stop_a_create(self):
        infos = self.project.load_data("map_infos")
        for _, info in self.project.entries("map_infos"):
            info.set("order", "first")
        self.assertTrue(infos is not None)
        result = self.call("map_edit", op="create", name="After", width=20,
                           height=15, dry_run=True)
        self.assertEqual(result["op"], "create")

    def test_a_tree_entry_that_is_not_an_object_does_not_stop_a_create(self):
        infos = self.project.load_data("map_infos")
        infos.set(777, None)
        result = self.call("map_edit", op="create", name="After", width=20,
                           height=15, dry_run=True)
        self.assertEqual(result["op"], "create")


class TestOneCommandDecodesABoundedNumberOfTexts(unittest.TestCase):
    """The cap was applied to the parameters and again to each inner list, so
    one command could be 64 x 64 decodes."""

    def test_nested_lists_share_one_budget(self):
        from rpgvxace_mcp.tools import search_tools
        shared = marshal_rb.RubyString(b"x" * 100)
        command = marshal_rb.RubyObject("RPG::EventCommand")
        command.set("parameters", [[shared] * 64 for _ in range(64)])
        calls = []

        def spy(ctx, value, matcher):
            calls.append(1)
            return "x"

        with mock.patch.object(search_tools, "subject_text", spy):
            search_tools._command_texts(None, command, None)
        self.assertLessEqual(len(calls), search_tools.MAX_TEXTS_PER_COMMAND)


class TestMapListSurvivesAHostileTree(ToolTest):
    """One MapInfos key that is not a map id (0, negative, over the ceiling,
    text) failed `map_list` for the whole project, and rows before `offset`
    were built, with a map load each, only to be thrown away."""

    def donor(self):
        return next(info for _, info in self.project.entries("map_infos"))

    def test_a_key_that_is_not_a_map_id_does_not_fail_the_listing(self):
        infos = self.project.load_data("map_infos")
        for key in (0, -4, 10 ** 9, "x"):
            infos.set(key, self.donor())
        result = self.call("map_list")
        self.assertTrue(result["maps"])
        shown = {row["id"] for row in result["maps"]}
        self.assertIn(1, shown)

    def test_rows_before_the_offset_are_not_built(self):
        for _ in range(3):
            self.call("map_edit", op="create", name="More", width=20, height=15)
        self.project.map_ids_on_disk(refresh=True)
        wanted = self.call("map_list")["matching_maps"]
        self.assertGreaterEqual(wanted, 4)
        with mock.patch.object(self.project, "map_file",
                               wraps=self.project.map_file) as spy:
            result = self.call("map_list", offset=wanted - 1, limit=5)
        self.assertEqual(result["returned"], 1)
        self.assertLessEqual(spy.call_count, 1)

    def test_the_offset_is_bounded(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            self.call("map_list", offset=10 ** 12)


class TestMapGetSurvivesHostileEventFields(CraftedMap, ToolTest):
    """A list-valued @trigger, an enormous condition id and a table whose
    third extent is huge each failed or inflated the whole `map_get`."""

    def crafted(self, condition=None, trigger=None):
        from rpgvxace_mcp.rubytypes import RubyObject
        self.craft_page_map([], 1, 1, 64 * 1024, "a hostile page",
                            condition=condition)
        game_map = self.ctx.project.load_map(1)
        page = game_map.get("events").get(1).get("pages")[0]
        if trigger is not None:
            page.set("trigger", trigger)

    def test_a_list_trigger_is_described_not_a_crash(self):
        self.crafted(trigger=[1, 2])
        result = self.call("map_get", map_id=1, include_events=True)
        self.assertTrue(result["events"])

    def test_a_bignum_condition_id_is_described_not_a_crash(self):
        from rpgvxace_mcp.rubytypes import RubyObject
        condition = RubyObject("RPG::Event::Page::Condition")
        condition.set("switch1_valid", True)
        condition.set("switch1_id", 10 ** 5000)
        self.crafted(condition=condition)
        result = self.call("map_get", map_id=1, include_events=True)
        self.assertTrue(result["events"])

    def test_a_huge_third_extent_does_not_list_every_layer(self):
        from rpgvxace_mcp.rubytypes import Table
        game_map = self.ctx.project.load_map(1)
        game_map.set("data", Table(0, 0, 60000, []))
        result = self.call("map_get", map_id=1, tiles="counts")
        self.assertLess(len(result["tile_table"]["layers"]), 100)
        self.assertLess(len(result["tile_counts"]), 100)


class TestMapRenderIsBoundedAndStaysInItsFolder(ToolTest):
    """A map of events each naming a different character sheet decoded them
    all; the render folder was created before it was checked, left a temp file
    on a failed write, and trimmed pictures it never wrote."""

    def render(self, **args):
        return self.call("map_render", map_id=1, **args)

    def test_a_map_of_many_character_sheets_decodes_a_bounded_number(self):
        from rpgvxace_mcp.schema import new_event
        from rpgvxace_mcp.tools import map_render_tools
        game_map = self.ctx.project.load_map(1)
        events = RubyHash()
        for number in range(1, 61):
            event = new_event(number, "E", 0, 0)
            graphic = event.get("pages")[0].get("graphic")
            graphic.set("character_name",
                        self.ctx.project.codec.make_string("c%d" % number))
            events.set(number, event)
        game_map.set("events", events)
        asked = []
        real = map_render_tools._load_graphic

        def spy(ctx, kind, name):
            if kind == "characters":
                asked.append(name)
            return real(ctx, kind, name)

        with mock.patch.object(map_render_tools, "_load_graphic", spy):
            result = self.render(events="graphics")
        self.assertLessEqual(len(asked), 32)
        self.assertIsNotNone(result)

    def test_a_failed_move_leaves_no_temp_file(self):
        from rpgvxace_mcp.tools import map_render_tools
        with mock.patch.object(map_render_tools.os, "replace",
                               side_effect=OSError("denied")):
            with self.assertRaises(Exception):
                self.render()
        folder = self.ctx.project.render_dir()
        self.assertEqual(list(folder.glob("*.tmp")), [])

    def test_only_its_own_pictures_are_trimmed(self):
        folder = self.ctx.project.render_dir()
        folder.mkdir(parents=True, exist_ok=True)
        keep = folder / "map-notes.png"
        keep.write_bytes(b"not mine")
        # Shaped like ours to the glob's wildcards, and not ours: ten
        # characters that are not a hash.
        lookalike = folder / "map-0001-holidaypic.png"
        lookalike.write_bytes(b"not mine either")
        for number in range(60):
            (folder / ("map-0001-%010d.png" % number)).write_bytes(b"x")
        self.render()
        self.assertTrue(keep.exists())
        self.assertTrue(lookalike.exists(),
                        "a file that only looks like a render was trimmed")

    def test_the_folder_is_checked_before_it_is_made(self):
        import subprocess
        import tempfile
        outside = Path(tempfile.mkdtemp())
        work = self.ctx.project.work_dir()
        if work.exists():
            import shutil
            shutil.rmtree(work)
        done = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(work), str(outside)],
            capture_output=True)
        if done.returncode != 0:
            self.skipTest("cannot make a junction here")
        try:
            with self.assertRaises(Exception):
                self.render()
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            subprocess.run(["cmd", "/c", "rmdir", str(work)])


class TestTheBackupsToolIsBoundedAndExplicit(ToolTest):
    """`files: []` meant "restore every file"; names of any length were
    echoed whole into refusals; a huge label became a raw OSError."""

    def test_an_empty_file_list_is_not_every_file(self):
        from rpgvxace_mcp.tools.registry import ToolError
        self.call("backups", op="create", label="before")
        stamp = self.call("backups", op="list")["backups"][0]["backup"]
        with self.assertRaises(ToolError) as caught:
            self.call("backups", op="restore", backup=stamp, files=[],
                      dry_run=True)
        self.assertIn("files", str(caught.exception))

    def test_a_long_backup_name_is_refused_short(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError) as caught:
            self.call("backups", op="restore", backup="x" * 100_000)
        self.assertLess(len(str(caught.exception)), 1000)

    def test_a_long_label_is_refused(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            self.call("backups", op="create", label="y" * 100_000)

    def test_a_long_file_name_is_refused_short(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError) as caught:
            self.call("backups", op="restore", backup="whatever",
                      files=["z" * 100_000], dry_run=True)
        self.assertLess(len(str(caught.exception)), 1000)


class TestProjectInfoRefreshRereadsTheScripts(ToolTest):

    def test_refresh_reaches_the_script_file(self):
        seen = []
        real = self.project.load_data

        def spy(key, refresh=False):
            seen.append((key, refresh))
            return real(key, refresh)

        with mock.patch.object(self.project, "load_data", spy):
            self.call("project_info", refresh=True)
        self.assertIn(("scripts", True), seen)


class TestRawDataRefusesBeforeItLoadsAndKeepsItsShapeGuard(ToolTest):
    """Cheap refusals ran after the file was unmarshalled; a wrapped array
    could be replaced by a number; a non-ASCII digit segment was a bare
    ValueError."""

    def test_a_too_deep_path_does_not_load_the_file(self):
        from rpgvxace_mcp.tools.registry import ToolError
        loads = []
        real = self.project.load_data

        def spy(key, refresh=False):
            loads.append(key)
            return real(key, refresh)

        with mock.patch.object(self.project, "load_data", spy):
            with self.assertRaises(ToolError):
                self.call("raw_data", table="actors",
                          path="/".join(["1"] * 500))
        self.assertNotIn("actors", loads)

    def test_a_set_without_a_path_does_not_load_the_file(self):
        from rpgvxace_mcp.tools.registry import ToolError
        loads = []
        real = self.project.load_data

        def spy(key, refresh=False):
            loads.append(key)
            return real(key, refresh)

        with mock.patch.object(self.project, "load_data", spy):
            with self.assertRaises(ToolError):
                # With a value, so the path rule is the only refusal in play.
                self.call("raw_data", op="set", table="actors",
                          value={"value": 1})
        self.assertNotIn("actors", loads)

    def test_a_wrapped_array_cannot_become_a_number(self):
        from rpgvxace_mcp.rubytypes import RubyIvars
        from rpgvxace_mcp.tools.registry import ToolError
        actor = self.project.load_data("actors")[1]
        actor.set("note", RubyIvars([1, 2], {"@x": 1}))
        with self.assertRaises(ToolError):
            self.call("raw_data", op="set", table="actors", path="1/note",
                      value={"value": 5}, dry_run=True)

    def test_a_non_ascii_digit_segment_is_a_refusal(self):
        from rpgvxace_mcp.tools.registry import ToolError
        with self.assertRaises(ToolError):
            self.call("raw_data", table="actors", path="²")


class TestTheRereviewOfTheScrubFixes(ToolTest):
    """Defects the targeted re-review found in the sweep's own fixes."""

    def test_a_command_iterates_a_bounded_number_of_parameters(self):
        """The shared decode budget counted strings only, and the slices that
        capped the iteration were gone: a Marshal link to one million-element
        list of integers was walked in full by every command that held it."""
        from rpgvxace_mcp.tools import search_tools

        class Counting(list):
            walked = 0

            def __iter__(self):
                Counting.walked += 1
                return list.__iter__(self)

        big = Counting([0] * 100_000)
        command = marshal_rb.RubyObject("RPG::EventCommand")
        command.set("parameters", [big, big])
        search_tools._command_texts(None, command, None)
        # Never iterated: the cap is a slice, which a list subclass does not
        # see. (An unbounded loop iterates it once per outer element.)
        self.assertEqual(Counting.walked, 0)

        flat = marshal_rb.RubyObject("RPG::EventCommand")
        flat.set("parameters", Counting([0] * 100_000))
        Counting.walked = 0
        seen = []
        original = search_tools.subject_text
        search_tools.subject_text = lambda *a: seen.append(a) or "x"
        try:
            search_tools._command_texts(None, flat, None)
        finally:
            search_tools.subject_text = original
        self.assertEqual(seen, [])
        self.assertEqual(Counting.walked, 0)

    def test_a_predictable_temp_name_is_not_written_through(self):
        """`<picture>.tmp` was a name an attacker could predict (the picture
        is named by a hash of its bytes) and `write_bytes` follows a link
        planted there. The temp file is now made new, under a name of its own."""
        from rpgvxace_mcp.tools import map_render_tools
        first = self.call("map_render", map_id=1)
        folder = self.ctx.project.render_dir()
        picture = next(folder.glob("map-0001-*.png"))
        picture.unlink()
        planted = folder / (picture.name + ".tmp")
        planted.write_bytes(b"SENTINEL")
        self.call("map_render", map_id=1)
        self.assertEqual(planted.read_bytes(), b"SENTINEL",
                         "the render wrote through a name it could not have "
                         "made for itself")
        self.assertTrue(picture.exists())
        self.assertIsNotNone(first)
        self.assertIsNotNone(map_render_tools)

    def test_a_snapshot_with_the_longest_label_can_be_restored(self):
        """A label at its cap made a snapshot name longer than the restore
        tool would take, so recovery was blocked by the safety net."""
        from rpgvxace_mcp.tools import misc_tools
        label = "y" * misc_tools.MAX_LABEL_CHARS
        self.call("backups", op="create", label=label)
        name = self.call("backups", op="list")["backups"][0]["backup"]
        self.assertGreater(len(name), misc_tools.MAX_LABEL_CHARS)
        out = self.call("backups", op="restore", backup=name, dry_run=True)
        self.assertTrue(out["dry_run"])

    def test_a_huge_order_in_the_tree_does_not_become_the_next_order(self):
        infos = self.project.load_data("map_infos")
        for _, info in self.project.entries("map_infos"):
            info.set("order", 2 ** 62)
        self.call("map_edit", op="create", name="Next", width=20, height=15)
        # The new map's own entry, not the maximum: the hostile ones are in
        # the same file.
        made = [info.get("order") for _, info
                in self.reopen().project.entries("map_infos")
                if getattr(info.get("name"), "data", b"")[:4] == b"Next"]
        self.assertEqual(len(made), 1)
        self.assertLess(made[0], 2 ** 30)
        self.assertIsNotNone(infos)

    def test_an_empty_file_list_is_refused_by_the_store_too(self):
        from rpgvxace_mcp.project import ProjectError
        self.call("backups", op="create", label="s")
        name = self.call("backups", op="list")["backups"][0]["backup"]
        with self.assertRaises(ProjectError):
            self.project.restore_backup(name, [], dry_run=True)


if __name__ == "__main__":
    unittest.main()
