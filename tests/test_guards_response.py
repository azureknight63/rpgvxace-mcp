"""Guards on how large one reply may be, in bytes rather than in nodes.

The other half of the bounding story, and the half that was wrong
for longer: every bound in this project counted nodes and none
counted bytes, so a reply of a quarter-million short strings passed
every check and was thirty megabytes. Caches, buffers and declared
ceilings are here for the same reason -- they are measured in what
they cost to hold, not in how many things they hold.
"""

import json
import re
import sys
import unittest
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))


from rpgvxace_mcp import (  # noqa: E402
    commands as command_module,
    marshal_rb,
)
from rpgvxace_mcp.schema import (  # noqa: E402
    new_event,
    new_event_command,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
    RubyString,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    REGISTRY,
    ToolError,
)
from rpgvxace_mcp.tools import map_read_tools  # noqa: E402
from tool_case import (  # noqa: E402
    SharedObjectMap,
    ToolTest,
    ValidateTest,
)


class TestScriptInflateLimit(ToolTest):
    def test_a_compression_bomb_is_reported_not_inflated(self):
        """A .rvdata2 need not be editor-written, and zlib expands ~1000:1."""
        scripts = self.project.load_data("scripts")
        # Compressed a megabyte at a time: holding the whole 64 MB to build the
        # bomb would make the test itself the thing that runs out of memory.
        packer = zlib.compressobj()
        chunks = [packer.compress(b"\0" * (1024 * 1024)) for _ in range(64)]
        chunks.append(packer.flush())
        scripts[0] = [1, self.project.codec.make_string("bomb"),
                      RubyString(b"".join(chunks))]
        self.project.save_file(self.project.filename_for("scripts"),
                               tool="test", summary="bomb")
        listing = self.reopen().project.load_scripts()
        self.assertIn("more than", listing[0]["error"])
        self.assertNotIn("code", listing[0])


class TestRefusalsAreBounded(ToolTest):
    """A refusal quotes untrusted file content, so it has to be capped.

    `refuse_unknown_fields` interpolates the whole of `", ".join(known)`, and
    `known` comes out of the file -- so an entry carrying a few hundred
    thousand instance variables produced a multi-megabyte error. `_tool_error`
    does not truncate, and the response ceiling guards only the success
    payload, so the frame went out at whatever size the file dictated.

    And `echo` exists twice with the same name and value -- here and in
    server.py -- where the server's copy slices before `repr` "because the
    whole point is not to materialise a value that may be megabytes" and this
    one did not.
    """

    MANY = 5000

    def entry_with_many_fields(self):
        entry = self.project.get_entry("items", 1)
        for index in range(self.MANY):
            entry.set("extra_%d" % index, index)
        self.project.save_file(self.project.filename_for("items"),
                               tool="test", summary="many fields")
        self.ctx = self.reopen()

    def test_an_unknown_field_refusal_is_capped(self):
        self.entry_with_many_fields()
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items", dry_run=True, ops=[
                {"op": "update", "id": 1, "patch": {"nosuchfield": 1}}])
        message = str(caught.exception)
        self.assertIn("nosuchfield", message)
        self.assertLess(len(message), 4000,
                        "the refusal grew with the file: %d characters"
                        % len(message))

    def test_echo_does_not_materialise_a_huge_repr(self):
        """One `echo`, slicing before repr, as server.py's copy documents."""
        from rpgvxace_mcp.tools import registry
        from rpgvxace_mcp import server
        # One function, not two under the same name.
        self.assertIs(server.echo, registry.echo)
        shown = registry.echo("x" * 5_000_000)
        self.assertLess(len(shown), registry.MAX_ECHO_CHARS + 60)


class TestMapGetBoundsItsEventSummary(SharedObjectMap, ToolTest):
    """`_event_summaries` walks events x pages and both come from the file.

    Every other whole-file walk in map_tools goes through `_check_budget` or a
    WalkBudget. This one built a row per page of every event, so the shared
    -object map -- fifty events of fifty pages, all one object -- produced
    2500 rows from 7 KB, and nothing in the reply said the list was long
    because the file was crafted rather than because the map is big.
    """

    def test_the_summary_stops_at_its_ceiling(self):
        self.craft_shared_map()
        out = self.assert_bounded("map_get", map_id=1, include_events=True)
        rows = sum(len(event["pages"]) for event in out["events"])
        self.assertLessEqual(rows, map_read_tools.MAX_EVENT_SUMMARY_ROWS)

    def test_a_stopped_summary_says_so(self):
        self.craft_shared_map()
        out = self.assert_bounded("map_get", map_id=1, include_events=True)
        self.assertIn("events_truncated", out)

    def test_an_ordinary_map_is_summarised_whole(self):
        out = self.call("map_get", map_id=1, include_events=True)
        self.assertNotIn("events_truncated", out)
        self.assertTrue(out["events"], "the fixture map has events")


class TestEveryCallerFacingCeilingIsDeclared(unittest.TestCase):
    """Every ceiling is declared to callers, or listed here as internal.

    A bound the caller cannot see is a refusal they cannot predict and a
    truncation they cannot detect. Ten were enforced without appearing
    anywhere in the advertised surface; four of those were reachable by an
    ordinary call.

    The internal set below is the other half of the claim, and it is checked
    too: a name in it that no longer exists fails, so the list cannot quietly
    accumulate ceilings that were renamed away.

    What the scan sees: a name assigned an arithmetic expression of digits,
    shifts and products. A ceiling derived from another one --
    `MAX_TOOL_RESULT_CHARS = MAX_RESPONSE_CHARS // 2` -- is not matched, and
    is covered by whichever ceiling it is derived from. Said here rather than
    left to be discovered, because an undeclared bound is exactly what this
    test is about and its own coverage is a bound of the same kind.
    """

    # Ceilings a caller cannot reach or would gain nothing from knowing.
    # Each one needs a reason, because "internal" is the escape hatch here and
    # an escape hatch without a reason is where undeclared bounds come back.
    INTERNAL = {
        # Robustness limits on reading a file this server did not write. A
        # caller cannot influence them; a file that trips one is reported as
        # unreadable, which is the actionable fact.
        "MAX_DEPTH": "marshal nesting in a file being read",
        "MAX_STRING_SCAN_DEPTH": "how deep a file is scanned for strings",
        "MAX_TABLE_AXIS": "an RGSS Table axis, fixed by the format",
        # Cosmetic clamps inside a message or a rendered line.
        "MAX_CHANGE_LINE_CHARS": "how much of a value one change line "
                                 "shows; the line is a log entry and a "
                                 "reply phrase, not something a caller "
                                 "asks for",
        "MAX_ECHO_CHARS": "how much of a bad argument an error quotes back",
        "MAX_NAMES_LISTED": "how many field names a refusal lists",
        "MAX_IDS_LISTED": "how many ids a refusal lists",
        "MAX_RENDER_INDENT": "indent clamp on one rendered script line",
        "MAX_NORMALIZE_DEPTH": "recursion clamp while restyling strings",
        # Internal to the pattern gate, whose refusals explain themselves in
        # the terms a caller can act on -- the pattern's shape, not the score.
        "MAX_BOUNDED_WEIGHT": "one atom's weight in the shape score",
        "MAX_BACKTRACK_PATHS": "the shape score's own budget",
        "UNBOUNDED_WEIGHT": "one atom's weight in the shape score",
        "MAX_SINGLE_CHARACTER_SUBJECTS": "how many of a pattern's characters "
                                         "get a homogeneous subject of "
                                         "their own",
        "MAX_MEASURED_CACHED": "how many passed patterns are remembered",
        # Server plumbing, not a tool contract.
        "MAX_BATCH_MESSAGES": "JSON-RPC batch size",
        # Authoring depth, reported by the compiler's own error.
        "MAX_AUTHORED_INDENT": "indent depth the DSL compiler accepts",
        "MAX_DSL_DEPTH": "nesting depth the DSL compiler accepts",
        # Size limits on reading a file this server did not write. A file that
        # trips one is reported as unreadable, with the limit and the actual
        # size in the message; there is no request a caller could have made
        # differently.
        "MAX_DATA_FILE_BYTES": "how large a data file may be to read",
        "MAX_SCRIPT_BYTES": "how large the script pack may be to read",
        "MAX_GAME_INI_BYTES": "how large Game.ini may be to read",
        "MAX_TABLE_CELLS": "cells in one RGSS Table being read",
        "MAX_PAYLOAD_BYTES": "a base64 payload in a write, named in its own "
                             "refusal along with the size that was sent",
        # JSON-RPC framing. These *refuse* rather than truncate, and say to
        # narrow the request, so there is nothing a caller cannot detect.
        "MAX_MESSAGE_CHARS": "one JSON-RPC request frame",
        "MAX_RESPONSE_CHARS": "one JSON-RPC response frame",
        # Crossing this costs a re-parse and nothing else: no refusal, no
        # truncation, nothing in a reply. A caller cannot detect it and has
        # nothing to do about it, which is the test for "internal" here.
        "MAX_CACHED_BYTES": "how much decoded file the cache keeps",
    }

    CEILING = re.compile(
        r"^(MAX_[A-Z0-9_]+|TOP_TILE_IDS|REFERENCE_PREVIEW|PREFLIGHT_SECONDS"
        r"|UNBOUNDED_WEIGHT)\s*=\s*([0-9_.<*\s]+?)\s*(?:#.*)?$")

    def ceilings(self):
        """Every numeric ceiling defined in the source, by name.

        The value side accepts an arithmetic expression, not just a literal:
        `MAX_BACKTRACK_PATHS = 1 << 14` is a ceiling, and a scan that skipped
        it would let this suite report full coverage of a set it had quietly
        narrowed -- the same defect the suite exists to catch.
        """
        found: dict[str, tuple[str, str]] = {}
        root = Path(__file__).resolve().parent.parent / "src"
        for path in sorted(root.rglob("*.py")):
            for line in path.read_text(encoding="utf-8").split("\n"):
                match = self.CEILING.match(line)
                if not match:
                    continue
                expression = match.group(2)
                # Digits, shifts and products only, so this is arithmetic on
                # the characters the pattern already allowed and not an eval
                # of whatever the file says.
                if not re.fullmatch(r"[0-9_.<*\s]+", expression):
                    continue
                try:
                    value = str(eval(expression, {}, {}))
                except (SyntaxError, TypeError, ValueError):
                    continue
                name = match.group(1)
                # Keyed by name, so a name defined twice used to overwrite
                # itself here and only one of the two was ever checked. The
                # file comes back with the value, and
                # `test_no_ceiling_is_defined_twice` reads it.
                if name in found:
                    found[name] = (found[name][0],
                                   "%s and %s" % (found[name][1], path.name))
                else:
                    found[name] = (value, path.name)
        return found

    def values(self):
        """Just the numbers, for the tests that only compare those."""
        return {name: value for name, (value, _) in self.ceilings().items()}

    def advertised(self):
        """Everything a caller can read before calling: descriptions and schemas.

        Thousands separators are stripped, so "10,000" in a sentence and
        10000 in a schema are the same number to the check below. Without
        that, excluding a comma at the boundary -- which is what stops "25"
        matching inside "1,025" -- also rejected `"maxItems": 10000,`, where
        the comma is JSON punctuation. The check then reported a ceiling
        declared twice as undeclared.
        """
        parts = []
        for tool in REGISTRY.values():
            parts.append(tool.description)
            parts.append(json.dumps(tool.schema))
        surface = " || ".join(parts)
        return re.sub(r"(?<=\d),(?=\d\d\d(?!\d))", "", surface)

    def test_the_source_has_ceilings_to_check(self):
        """So a broken scan cannot make this suite vacuously pass."""
        self.assertGreater(len(self.values()), 30)

    def test_no_ceiling_is_defined_twice(self):
        """Two definitions of one ceiling are two facts nothing keeps equal.

        `MAX_MAP_ID` was defined in project.py and again in schema.py, both
        9999: the tools imported project's while `schema.map_filename`
        enforced schema's, so the id range a tool accepts and the id range a
        filename can express could drift apart silently. This scan keys by
        name, so it could not see that at all until it kept the file too --
        which is the same defect one level up from the one it checks for.
        """
        twice = sorted("%s (in %s)" % (name, where)
                       for name, (_, where) in self.ceilings().items()
                       if " and " in where)
        self.assertEqual(twice, [],
                         "these ceilings are defined more than once; keep one "
                         "definition and import it")

    def test_every_ceiling_is_declared_or_named_internal(self):
        surface = self.advertised()
        undeclared = []
        for name, value in sorted(self.values().items()):
            if name in self.INTERNAL:
                continue
            forms = {value, str(int(float(value)))}
            forms.add("{:,}".format(int(float(value))))
            # Digit boundaries, because a bare substring is not a match:
            # "25" appears inside "125" and "1025", so REFERENCE_PREVIEW = 25
            # read as declared by any description that happened to name a
            # larger number. A check that passes for the wrong reason is the
            # thing this suite is about.
            found = any(
                re.search(r"(?<![0-9.])%s(?![0-9.])" % re.escape(form),
                          surface)
                for form in forms)
            if not found:
                undeclared.append("%s = %s" % (name, value))
        self.assertEqual(
            undeclared, [],
            "these ceilings are enforced but never advertised, so a caller "
            "cannot predict the refusal or detect the truncation. Declare "
            "each in the description of the tool that enforces it, or add it "
            "to INTERNAL with the reason it is unreachable.")

    def test_the_internal_list_has_not_gone_stale(self):
        names = set(self.values())
        gone = sorted(set(self.INTERNAL) - names)
        self.assertEqual(gone, [],
                         "INTERNAL names ceilings that no longer exist, so it "
                         "is excusing nothing and hiding the next one")


class TestATileTallyUsesTheBuffer(ToolTest):
    """`_tile_counts` walked cell by cell; its own sibling proves the fast form.

    `_cells_outside` in the same file counts over slices of the flat buffer,
    and its docstring says why: "the per-cell version made two Python calls
    for each of up to four million cells on a single-threaded server".
    `_tile_counts` runs under the same four-million-cell budget and did the
    per-cell version.
    """

    def test_the_tally_matches_a_cell_by_cell_count(self):
        """The fast form has to agree with the slow one, layer by layer."""
        from collections import Counter
        data = self.project.load_map(1).get("data")
        counts = map_read_tools._tile_counts(data)
        for z in range(data.zsize):
            slow: Counter = Counter()
            for y in range(data.ysize):
                for x in range(data.xsize):
                    slow[data.get(x, y, z)] += 1
            layer = counts[str(z)]
            self.assertEqual(layer["distinct_tile_ids"], len(slow))
            top = {row["tile_id"]: row["count"] for row in layer["most_common"]}
            for tile_id, count in top.items():
                self.assertEqual(count, slow[tile_id],
                                 "layer %d tile %d" % (z, tile_id))

    def test_a_tally_of_a_painted_map_finds_what_was_painted(self):
        data = self.project.load_map(1).get("data")
        data.set(0, 0, 0, 4242)
        data.set(1, 0, 0, 4242)
        counts = map_read_tools._tile_counts(data)
        top = {row["tile_id"]: row["count"]
               for row in counts["0"]["most_common"]}
        self.assertEqual(top.get(4242), 2)

    def test_every_layer_is_reported(self):
        data = self.project.load_map(1).get("data")
        counts = map_read_tools._tile_counts(data)
        self.assertEqual(sorted(counts), sorted(str(z) for z in range(data.zsize)))


class TestTheFileCacheIsBounded(ToolTest):
    """The cache evicts, oldest first, and never drops a staged file."""

    def setUp(self):
        super().setUp()
        from rpgvxace_mcp import project as project_module
        self.module = project_module
        original = project_module.MAX_CACHED_BYTES
        self.addCleanup(setattr, project_module, "MAX_CACHED_BYTES", original)
        # Small enough that the fixture's own files cross it.
        project_module.MAX_CACHED_BYTES = 2048

    def read_everything(self):
        """Touch every data file, so the cache is asked to hold them all."""
        for key in sorted(self.project.data_files):
            try:
                self.project.load_data(key)
            except Exception:
                pass
        for map_id in sorted(self.project.map_ids_on_disk()):
            self.project.load_map(map_id)

    def cached_bytes(self):
        return sum(entry.size for entry in self.project._cache.values())

    def test_the_cache_stays_within_its_ceiling(self):
        """Or holds exactly one file, which is the honest exception.

        A file larger than the whole ceiling still has to be held to be
        served, so the bound is "within the ceiling, or one entry" rather than
        an absolute byte count. Anything looser would not be a bound.
        """
        self.read_everything()
        held, count = self.cached_bytes(), len(self.project._cache)
        self.assertTrue(
            held <= self.module.MAX_CACHED_BYTES or count == 1,
            "the cache holds %d bytes of source across %d files against a "
            "ceiling of %d, and Marshal decodes at about 32 times that"
            % (held, count, self.module.MAX_CACHED_BYTES))

    def test_it_keeps_something(self):
        """A cache that evicts everything is a cache that does nothing."""
        self.read_everything()
        self.assertTrue(self.project._cache,
                        "eviction emptied the cache entirely")

    def test_the_most_recent_read_survives(self):
        """Oldest first, so the file a caller is working on stays."""
        self.read_everything()
        last = self.project.filename_for("items")
        self.project.load_data("items")          # now the newest entry
        self.assertIn(last, self.project._cache,
                      "the file read most recently was evicted, so a caller "
                      "working through one table re-parses it every call")

    def test_a_staged_file_is_never_evicted(self):
        """The hazard: eviction between the mutation and the write.

        Every editing tool changes the value in the cache and then asks for it
        to be saved. Dropping that entry in between would write the file as it
        was on disk and report the edit as applied.
        """
        filename = self.project.filename_for("items")
        with self.ctx.staged(filename) as write_landed:
            entry = self.project.get_entry("items", 1)
            entry.set("name", self.project.codec.make_string("Marked"))
            # Enough other reads to push the budget well over.
            self.read_everything()
            self.assertIn(filename, self.project._cache,
                          "the staged file was evicted mid-write")
            write_landed.save(filename, tool="test", summary="marked")

        self.assertEqual(
            self.reopen().project.get_entry("items", 1).get("name").data,
            b"Marked",
            "the edit did not reach the disk, so eviction discarded it")

    def test_an_ordinary_project_is_never_evicted_from(self):
        """The ceiling is ten times the real project; it must not bite."""
        self.module.MAX_CACHED_BYTES = 16 * 1024 * 1024
        self.read_everything()
        held = len(self.project._cache)
        self.read_everything()
        self.assertEqual(len(self.project._cache), held,
                         "a fixture-sized project is being evicted from, so "
                         "every call re-parses")


class TestARenderedCommandLineIsBoundedInBytes(ToolTest):
    """Every bound in this project counted nodes; none counted bytes.

    `analysis._text` and `registry.decoded` both go through
    `convert.decoded_text`, which caps at MAX_TEXT_CHARS and slices before it
    decodes. `commands._text` and `commands._value` did neither, and two
    branches of `summarize` fan out over a list whose length comes out of the
    file -- so one command holding an Array of a million links to one
    megabyte string asked for a terabyte of decode and a join that does not
    fit in memory, while `WalkBudget` charged it a single step.
    """

    # Links, so the file stays small: a million of these is ~3 MB on disk.
    FANOUT = 200_000
    LONG = 400_000

    def command(self, code, parameters):
        cmd = new_event_command(code, 0, parameters)
        return cmd

    def line_for(self, code, parameters):
        return command_module.summarize(self.command(code, parameters),
                                        self.project.codec)

    def assert_short(self, line, what):
        """A cap of a few thousand characters, not one of a few hundred.

        The point is that the length does not follow the file's, so the
        assertion is loose on purpose: a renderer that caps each of forty
        fields at 200 characters is doing the right thing and is allowed to
        produce eight thousand.
        """
        self.assertLess(len(line), 20_000,
                        "%s rendered %d characters" % (what, len(line)))

    def test_one_long_string_parameter_is_capped(self):
        """Code 401 is a text line: the whole parameter is the message."""
        huge = self.project.codec.make_string("A" * self.LONG)
        self.assert_short(self.line_for(401, [huge]), "a text line")

    def test_a_long_choice_list_is_capped(self):
        """Show Choices renders every choice, and the list comes from a file."""
        choice = self.project.codec.make_string("yes")
        self.assert_short(self.line_for(102, [[choice] * self.FANOUT, 0]),
                          "a Show Choices line")

    def test_only_the_entries_that_survive_the_cap_are_rendered(self):
        """The cap is on the work, and `_capped_join` already caps the result.

        So a length assertion passes whether or not the slice is there --
        which is exactly why the guard on it read VACUOUS. This counts how
        many times the renderer is called, which only the slice can bound.
        """
        from rpgvxace_mcp.rubytypes import MAX_NAMES_LISTED, rendered
        calls = []

        def counting(value):
            calls.append(value)
            return str(value)

        rendered(list(range(self.FANOUT)), counting)
        self.assertLessEqual(
            len(calls), MAX_NAMES_LISTED,
            "%d of %d entries were rendered to produce a result that keeps "
            "%d" % (len(calls), self.FANOUT, MAX_NAMES_LISTED))

    def test_a_long_parameter_list_is_capped(self):
        """A command with no declared spec joins every parameter it carries."""
        self.assert_short(self.line_for(999, [7] * self.FANOUT),
                          "an unknown command's parameter list")

    def test_a_long_string_in_a_specced_field_is_capped(self):
        """The `field=value` branch renders each declared field with `_text`."""
        huge = self.project.codec.make_string("A" * self.LONG)
        self.assert_short(self.line_for(101, [huge, 0, 0, 2]),
                          "a Show Text face field")

    def test_a_crafted_command_is_bounded_through_event_get(self):
        """The reachable path, so the cap is not only a unit-test property."""
        game_map = self.project.load_map(1)
        huge = self.project.codec.make_string("A" * self.LONG)
        page = RubyObject("RPG::Event::Page")
        page.set("condition", RubyObject("RPG::Event::Page::Condition"))
        page.set("graphic", RubyObject("RPG::Event::Page::Graphic"))
        page.set("trigger", 0)
        page.set("list", [self.command(401, [huge])])
        event = new_event(1, "E")
        event.set("pages", [page])
        events = RubyHash()
        events.set(1, event)
        game_map.set("events", events)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()
        result = self.call("event_get", map_id=1, event_ids=[1])
        rendered = json.dumps(result)
        self.assertLess(len(rendered), 100_000,
                        "event_get returned %d characters for one command"
                        % len(rendered))


class TestARawPathCannotBeUnboundedlyLong(ToolTest):
    """Nothing capped the number of path segments, and a hash can be cyclic.

    `_read_hash_pairs` registers a hash in the link table before it reads its
    pairs, so a file may legally hold a hash that contains itself. Each hash
    segment then costs three linear scans of its pairs, and the path's length
    comes from the argument -- so `path="events/" + "1/" * 4_000_000` is a
    walk this server cannot be interrupted out of. The path is the thing to
    bound: `to_json` already refuses a cycle, and `_resolve` does not need to
    know about one.
    """

    def test_a_path_with_too_many_segments_is_refused(self):
        long_path = "/".join(["events"] + ["1"] * 5000)
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="get", map_id=1, path=long_path)
        self.assertIn("segment", str(caught.exception).lower())

    def test_the_refusal_says_the_ceiling(self):
        from rpgvxace_mcp.tools.raw_tools import MAX_PATH_SEGMENTS
        long_path = "/".join(["events"] + ["1"] * 5000)
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="get", map_id=1, path=long_path)
        self.assertIn(str(MAX_PATH_SEGMENTS), str(caught.exception))

    def test_an_ordinary_path_still_resolves(self):
        out = self.call("raw_data", op="get", map_id=1, path="events/1/name")
        self.assertIn("value", out)

    def test_the_ceiling_is_named_in_the_argument_description(self):
        """`TestEveryCallerFacingCeilingIsDeclared` enforces this too.

        Asserted here as well because that suite checks the ceiling appears
        *somewhere* in the advertised surface, and this one checks it is on
        the argument a caller would read before sending a long path.
        """
        from rpgvxace_mcp.tools.raw_tools import MAX_PATH_SEGMENTS
        spec = REGISTRY["raw_data"].schema["properties"]["path"]
        self.assertIn(str(MAX_PATH_SEGMENTS), spec["description"])


class TestTheNodeBudgetFitsTheResponse(unittest.TestCase):
    """A conversion this server permits must be one it can answer with.

    `MAX_JSON_NODES` and `MAX_TOOL_RESULT_CHARS` were chosen independently,
    and `_dump_json` indents -- so every node costs at least four characters
    of output and a payload that reached the node budget serialised to four
    times the response ceiling. The largest conversion allowed was always
    thrown away after the work had been done and the memory allocated, which
    is the distinction `map_read_tools` already spells out for its own
    ceiling: a response limit bounds the response, not the work.

    Here rather than as a comment, because `server` imports `convert` and not
    the other way round, so the relationship cannot be written as an
    expression.
    """

    # The smallest number of output characters one rendered node can cost.
    # `{"a": 1}` at two spaces of indent is more than this; the divisor is
    # deliberately conservative.
    CHARS_PER_NODE = 8

    def test_a_full_conversion_could_be_returned(self):
        from rpgvxace_mcp.convert import MAX_JSON_NODES
        from rpgvxace_mcp.server import MAX_TOOL_RESULT_CHARS
        self.assertLessEqual(
            MAX_JSON_NODES * self.CHARS_PER_NODE, MAX_TOOL_RESULT_CHARS,
            "a conversion of %d nodes serialises past the %d-character "
            "response ceiling, so the largest one allowed is always "
            "refused after the work is done"
            % (MAX_JSON_NODES, MAX_TOOL_RESULT_CHARS))

    def test_it_is_not_so_small_that_a_real_answer_is_refused(self):
        """The other direction: a whole real map has to convert."""
        from rpgvxace_mcp.convert import MAX_JSON_NODES
        self.assertGreater(MAX_JSON_NODES, 100_000)


class TestTheTileRectangleUsesTheBuffer(ToolTest):
    """The third per-cell `Table.get` loop, in the primary tile path.

    `_tile_counts` and `_cells_outside` were both converted to row slices
    and both carry a docstring giving the reason; `map_tiles` -- the tool
    whose whole job is reading and writing tiles -- was not converted with
    them. Pinned against a cell-by-cell read rather than timed, because a
    timing test is a race and this change has no behaviour of its own.
    """

    def cell_by_cell(self, table, x, y, width, height, z):
        return [[table.get(x + dx, y + dy, z) for dx in range(width)]
                for dy in range(height)]

    def test_a_read_matches_a_cell_by_cell_read(self):
        out = self.call("map_tiles", op="read", map_id=1, x=2, y=3,
                        width=5, height=4, z=0)
        table = self.project.load_map(1).get("data")
        self.assertEqual(out["rows"],
                         self.cell_by_cell(table, 2, 3, 5, 4, 0))

    def test_every_layer_reads_the_same_way(self):
        table = self.project.load_map(1).get("data")
        for z in range(table.zsize):
            with self.subTest(z=z):
                out = self.call("map_tiles", op="read", map_id=1, x=0, y=0,
                                width=table.xsize, height=table.ysize, z=z)
                self.assertEqual(out["rows"],
                                 self.cell_by_cell(table, 0, 0, table.xsize,
                                                   table.ysize, z))

    def test_a_fill_writes_what_a_read_comes_back_with(self):
        self.call("map_tiles", op="fill", map_id=1, x=1, y=1, width=3,
                  height=2, z=0, tile_id=2816)
        out = self.call("map_tiles", op="read", map_id=1, x=1, y=1, width=3,
                        height=2, z=0)
        self.assertEqual(out["rows"], [[2816] * 3] * 2)

    def test_a_fill_does_not_touch_the_cell_beside_it(self):
        before = self.call("map_tiles", op="read", map_id=1, x=0, y=0,
                           width=6, height=4, z=0)["rows"]
        self.call("map_tiles", op="fill", map_id=1, x=1, y=1, width=3,
                  height=2, z=0, tile_id=2816)
        after = self.call("map_tiles", op="read", map_id=1, x=0, y=0,
                          width=6, height=4, z=0)["rows"]
        for y in range(4):
            for x in range(6):
                if 1 <= x <= 3 and 1 <= y <= 2:
                    continue
                self.assertEqual(after[y][x], before[y][x],
                                 "(%d, %d) changed outside the rectangle"
                                 % (x, y))

    def test_a_write_round_trips_through_the_file(self):
        """The mask `Table.set` used to apply has to still be applied."""
        self.call("map_tiles", op="write", map_id=1, x=0, y=0, z=0,
                  width=2, height=2, rows=[[1, 2], [3, 4]])
        out = self.call("map_tiles", op="read", map_id=1, x=0, y=0, width=2,
                        height=2, z=0)
        self.assertEqual(out["rows"], [[1, 2], [3, 4]])


class TestAListingBuildsOnlyItsWindow(ToolTest):
    """`MAX_ROWS` capped what came back and not what was built.

    So an array of a million links to one row made a million reply dicts
    before the window was taken -- and `map_list` had no window at all.
    `MAX_TOOL_RESULT_CHARS` does not save that: this module's own comment
    says it "bounds the response and not the work or the memory".
    """

    def test_db_list_builds_only_the_window(self):
        out = self.call("db_list", table="items", limit=1)
        self.assertEqual(len(out["entries"]), 1)
        self.assertGreater(out["matching_entries"], 1,
                           "the count must still say how many matched")

    def test_map_list_takes_a_window(self):
        out = self.call("map_list", limit=1)
        self.assertEqual(len(out["maps"]), 1)
        self.assertGreater(out["matching_maps"], 1)

    def test_map_list_pages(self):
        first = self.call("map_list", limit=1)["maps"][0]["id"]
        second = self.call("map_list", limit=1, offset=1)["maps"][0]["id"]
        self.assertNotEqual(first, second)

    def test_both_listings_declare_their_ceiling(self):
        """A bound a caller cannot see is one they hit by surprise."""
        from rpgvxace_mcp.tools.map_read_tools import MAX_MAP_ROWS
        spec = REGISTRY["map_list"].schema["properties"]["limit"]
        self.assertEqual(spec["maximum"], MAX_MAP_ROWS)


class TestAPathCannotOutscanItsFile(ToolTest):
    """`MAX_PATH_SEGMENTS` bounds the path's length, not the product.

    Each hash segment costs a linear scan of that hash, and
    `_read_hash_pairs` registers a hash before reading its pairs -- so a
    file may legally hold one that contains itself. 64 segments over a
    large hash is still the segment count times the pair count.
    """

    def craft_self_referencing_hash(self, pairs):
        game_map = self.project.load_map(1)
        events = RubyHash()
        for key in range(pairs):
            events.set(key, key)
        events.set(9999, events)          # legal Marshal: a hash in itself
        game_map.set("events", events)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()

    def test_a_deep_path_over_a_large_hash_is_refused(self):
        from rpgvxace_mcp.tools.raw_tools import MAX_PATH_SEGMENTS
        self.craft_self_referencing_hash(20_000)
        path = "/".join(["events"] + ["9999"] * (MAX_PATH_SEGMENTS - 1))
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="get", map_id=1, path=path)
        self.assertIn("hash entries", str(caught.exception))

    def test_an_ordinary_path_still_resolves(self):
        out = self.call("raw_data", op="get", map_id=1, path="events/1/name")
        self.assertIn("value", out)


class TestASearchSaysWhatItDidNotRead(ValidateTest):
    """A plain substring search reports a subject it truncated.

    `_Matcher` exists because "a regex reads the first 2,000 characters and
    a plain search reads all of it" was invisible, and its docstring says
    so. The command scope then truncated the *text* before the matcher saw
    it -- `value.data[:MAX_SUBJECT_CHARS * 4]` in `_command_texts` -- while
    `clipped` is counted only `if not self.whole`. So for a plain search
    the difference moved out of the matcher and became invisible again:
    the needle past 8,000 bytes of one script line returns no hits, and the
    reply says nothing about a subject it cut.

    Both halves are checked here, because the reply being honest matters
    whether or not this particular needle is found: either the hit comes
    back, or the reply says a subject was clipped. Silence with neither is
    the defect.
    """

    # Past `MAX_SUBJECT_CHARS * 4`, which is where `_command_texts` cuts.
    FAR = 9000

    def craft_a_long_line(self, needle):
        """One comment line with `needle` past the byte cap."""
        _, _, event = self.first_event(1)
        page = event.get("pages")[0]
        text = "z" * self.FAR + needle
        page.set("list", [new_event_command(
            108, 0, [self.project.codec.make_string(text)])])
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="a long line")
        self.ctx = self.reopen()

    def test_a_plain_search_is_honest_about_a_cut_subject(self):
        self.craft_a_long_line("findme")
        out = self.call("search", query="findme", scopes=["events"])
        found = bool(out.get("hits"))
        said = ("strings_clipped_note" in out
                or out.get("may_have_more") is True)
        self.assertTrue(
            found or said,
            "the needle is past the byte cap, no hit came back, and the "
            "reply says nothing about a clipped subject: %s" % sorted(out))

    def test_a_regex_search_still_says_it_clipped(self):
        """The half that already worked, so this cannot pass by regressing it."""
        self.craft_a_long_line("findme")
        out = self.call("search", query="findme", regex=True,
                        scopes=["events"])
        self.assertTrue(
            bool(out.get("hits")) or "strings_clipped_note" in out,
            "a regex search neither found it nor said it clipped: %s"
            % sorted(out))

    def test_a_needle_inside_the_cap_is_still_found(self):
        """So neither test above passes by the search finding nothing ever."""
        self.craft_a_long_line("findme")
        out = self.call("search", query="zzzz", scopes=["events"])
        self.assertTrue(out["hits"], "the ordinary case stopped working")


# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so running this file directly
# called `unittest.main()` (which exits) before the classes below it
# existed, and reported success having skipped them. pytest collects
# either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()
