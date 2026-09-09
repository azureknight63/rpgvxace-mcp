"""Guards on how much work one call may do.

Every walk, every ceiling, every budget. The recurring defect here has two
shapes and both have appeared more than once: a bound that counts nodes
where the cost is bytes, and a charge placed after an `isinstance` test so
that the slot it skips is free. A file whose objects share children can
describe millions of nodes in a few kilobytes, and this server is
single-threaded over stdio -- so an unbounded walk is a session the user
cannot interrupt.
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


from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp import commands as command_module  # noqa: E402
from rpgvxace_mcp.analysis import (  # noqa: E402
    MAX_FINDINGS,
)
from rpgvxace_mcp.project import (  # noqa: E402
    Project,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
    RubyString,
    Table,
)
from rpgvxace_mcp import convert  # noqa: E402
from rpgvxace_mcp.convert import ConversionError  # noqa: E402
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
)

# The tool modules this file names directly. Registration -- the
# side effect of importing them -- is `tool_case`'s job; these are
# here because the tests below read their op tables.
from rpgvxace_mcp.tools import (  # noqa: E402
    db_tools,
    event_tools,
    map_read_tools,
)

from tool_case import (  # noqa: E402
    EmptyPageMap,
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


class TestValidateSurvivesHostileData(ValidateTest):
    """A .rvdata2 need not be one the editor wrote.

    Every one of these used to raise out of validate, so a single odd ivar
    denied the whole tool for that project rather than producing one finding.
    """

    def test_a_tile_table_that_is_not_a_table(self):
        game_map = self.project.load_map(1)
        game_map.set("data", 7)
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="bad data")
        self.ctx = Context(Project(self.tmp / "project"))
        found = self.findings("tile_data_unreadable", scopes=["maps"])
        self.assertTrue(found)
        self.assertIn("int", found[0]["message"])

    def test_a_map_id_no_filename_can_express(self):
        infos = self.project.load_data("map_infos")
        infos.set(0, marshal_rb.load(marshal_rb.dump(infos.sorted_items()[0][1])))
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="map id 0")
        self.ctx = Context(Project(self.tmp / "project"))
        found = self.findings("bad_map_id", scopes=["maps"])
        self.assertTrue(found, "id 0 is not a map id")

    def test_a_parent_chain_that_loops(self):
        infos = self.project.load_data("map_infos")
        ids = [i for i, _ in infos.sorted_items()]
        self.assertGreaterEqual(len(ids), 2)
        for index, map_id in enumerate(ids[:2]):
            infos.get(map_id).set("parent_id", ids[1 - index])
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="cycle")
        self.ctx = Context(Project(self.tmp / "project"))
        found = self.findings("map_tree_cycle", scopes=["maps"])
        self.assertEqual(len(found), 2, "both maps in the loop, and only those")

    def test_a_parent_id_that_is_not_a_number(self):
        infos = self.project.load_data("map_infos")
        first = infos.sorted_items()[0][0]
        infos.get(first).set("parent_id", [1, 2])
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="list parent")
        self.ctx = Context(Project(self.tmp / "project"))
        # The point is that this completes at all: an unhashable parent_id used
        # to raise TypeError out of the whole run.
        self.assertIn("counts", self.call("validate", scopes=["maps"]))

    def test_a_long_event_name_is_capped_in_the_message(self):
        """Long enough to need capping, short enough that the byte-slice
        shortcut does not fire: the other path through the same helper."""
        game_map, event_id, event = self.first_event()
        event.set("name", self.project.codec.make_string("y" * 400))
        event.get("pages")[0].get("list").insert(0, RubyObject(
            "RPG::EventCommand",
            {"@code": 201, "@indent": 0, "@parameters": [0, 4321, 0, 0, 0, 0]}))
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="long-ish name")
        self.ctx = Context(Project(self.tmp / "project"))
        rows = self.call("validate", scopes=["events"])
        self.assertTrue(rows["findings"])
        for finding in rows["findings"]:
            self.assertLess(len(json.dumps(finding)), 1200)

    def test_an_event_name_of_absurd_length(self):
        game_map, event_id, event = self.first_event()
        event.set("name", self.project.codec.make_string("x" * 500000))
        # Give the event something to be reported for, so there is a finding
        # for the name to inflate.
        event.get("pages")[0].get("list").insert(0, RubyObject(
            "RPG::EventCommand",
            {"@code": 201, "@indent": 0, "@parameters": [0, 4321, 0, 0, 0, 0]}))
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="long name")
        self.ctx = Context(Project(self.tmp / "project"))
        rows = self.call("validate", scopes=["events", "assets"])
        self.assertTrue(rows["findings"], "the long name is on a real event")
        for finding in rows["findings"]:
            # The whole finding, because the name reaches the response through
            # "where" as well as through the message.
            self.assertLess(len(json.dumps(finding)), 4000,
                            "one crafted name inflated every finding about it")


class TestCollectionIsBounded(ValidateTest):
    """MAX_FINDINGS says it is "a ceiling on findings collected, not just
    findings returned". It has to be one: a page of broken jumps produces a
    finding per command, so an unbounded collection builds tens of thousands
    of objects in order to report ten."""

    def test_a_flood_of_findings_stops_at_the_ceiling(self):
        game_map = self.project.load_map(1)
        event = game_map.get("events").sorted_items()[0][1]
        # Jump to Label with no matching Label: one finding each.
        flood = [RubyObject("RPG::EventCommand",
                            {"@code": 119, "@indent": 0,
                             "@parameters": [
                                 self.project.codec.make_string("nowhere")]})
                 for _ in range(MAX_FINDINGS * 3)]
        flood.append(RubyObject("RPG::EventCommand",
                                {"@code": 0, "@indent": 0, "@parameters": []}))
        event.get("pages")[0].set("list", flood)
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="flood")
        self.ctx = self.reopen()

        rows = self.call("validate", scopes=["events"], limit=10)
        collected = sum(rows["counts"].values())
        self.assertLessEqual(collected, MAX_FINDINGS)
        self.assertIn("stopped_early", rows,
                      "the caller has to be told this is not the whole picture")


class TestDamagedPagesAreRefusedNotCrashedOn(ToolTest):
    """@pages must get the type check @events already has.

    On a crafted or unmodelled-engine map, add_page reached `.append` and
    delete_page reached `len()` on whatever the file held -- an AttributeError
    or TypeError where a "run validate" refusal belongs. A nil @pages was also
    silently replaced with a fresh list, which is the discarding
    _apply_event_ops refuses by name for @events.
    """

    def break_pages(self, value):
        game_map = self.project.load_map(1)
        game_map.get("events").get(1).set("pages", value)
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="broken pages")

    def test_a_non_list_pages_is_refused(self):
        self.break_pages(self.project.codec.make_string("not a list"))
        # add_page appends and so takes no page; the others act on one.
        for op, extra in (("add_page", {}), ("delete_page", {"page": 0}),
                          ("set_page", {"page": 0, "patch": {"trigger": 1}})):
            with self.subTest(op=op):
                with self.assertRaises(ToolError) as caught:
                    self.call("event_edit", map_id=1, dry_run=True, ops=[
                        dict(op=op, event_id=1, **extra)])
                self.assertIn("validate", str(caught.exception))

    def test_an_event_with_no_pages_is_named_clearly(self):
        self.break_pages([])
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "set_page", "event_id": 1, "page": 0,
                 "patch": {"trigger": 1}}])
        message = str(caught.exception)
        self.assertIn("no pages", message)
        self.assertNotIn("0..-1", message)


class TestGrowingAMapIsBoundedToo(ToolTest):
    """The whole-table ceiling applied to shrinking and not to growing.

    The check lived inside the function that counts discarded tiles, which
    returns before reaching it when both axes grow -- so a grow was bounded
    only by the Table's own allocation cap.
    """

    def test_a_grow_past_the_ceiling_is_refused(self):
        game_map = self.project.load_map(1)
        # A small table with more layers than the editor writes -- extents come
        # out of a file that need not be one the editor wrote. Growing this to
        # the largest side allowed is 5 million cells, past the ceiling, while
        # the table being grown *from* is well inside it. That is the case the
        # old check could not see.
        game_map.set("data", Table(20, 15, 5))
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="five layers")
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="resize", map_id=1, width=1000,
                      height=1000, dry_run=True)
        message = str(caught.exception)
        self.assertIn("cells", message)
        # And the remedy is not a tool that cannot resize.
        self.assertNotIn("read it in pieces with map_tiles", message)

    def test_an_ordinary_resize_still_works(self):
        out = self.call("map_edit", op="resize", map_id=1, width=25,
                        height=20, dry_run=True)
        self.assertIn("resized from 20x15 to 25x20", out["would_change"])


class TestOneCommandListHasACeiling(ToolTest):
    """A compiled command list had no bound at all.

    Nesting depth and ops-per-call were bounded; the compiled length was not,
    so one large request could write a page carrying far more commands than
    the editor will open, with no cap and no warning.
    """

    def test_a_list_past_the_ceiling_is_refused(self):
        # Inside the declared maxItems on `commands`, which counts entries,
        # and past the ceiling on the compiled list, which counts commands:
        # one {"text": ...} becomes a 101 and its 401 line.
        many = [{"text": "line"}
                for _ in range(event_tools.MAX_COMMANDS_PER_LIST // 2 + 1)]
        self.assertLessEqual(len(many), event_tools.MAX_COMMANDS_PER_LIST)
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "set_commands", "event_id": 1, "page": 0,
                 "commands": many}])
        self.assertIn("commands on one page", str(caught.exception))

    def test_appending_cannot_walk_past_it_a_little_at_a_time(self):
        """The append path concatenates, so it needs its own check."""
        # One {"text": ...} compiles to two commands, a 101 and its 401 line,
        # so a quarter of the ceiling in entries is about half of it in
        # commands -- two of these fit, three do not.
        quarter = event_tools.MAX_COMMANDS_PER_LIST // 4
        batch = [{"text": "line"} for _ in range(quarter)]
        self.call("event_edit", map_id=1, ops=[
            {"op": "set_commands", "event_id": 1, "page": 0,
             "commands": batch}])
        with self.assertRaises(ToolError):
            for _ in range(3):
                self.call("event_edit", map_id=1, ops=[
                    {"op": "append_commands", "event_id": 1, "page": 0,
                     "commands": batch}])


class TestReadingManyEntriesSharesOneBudget(ToolTest):
    """The conversion budget was per entry, so per response it was unbounded.

    A tool reading MAX_IDS_PER_CALL entries gave each its own 2-million-node
    allowance, which is not what the budget was chosen to permit.
    """

    def test_one_walk_is_shared_across_the_ids(self):
        walk = convert.new_walk()
        before = walk.budget
        for _ in range(3):
            convert.to_json(self.project.load_data("items"), self.project.codec,
                            "summary", walk)
        self.assertLess(walk.budget, before)

    def test_db_get_still_reads_several_ids(self):
        out = self.call("db_get", table="items", ids=[1, 2])
        self.assertEqual(len(out["entries"]), 2)

    def test_the_budget_bounds_the_response_not_each_entry(self):
        """Reading two entries must spend one allowance, not two.

        The real allowance is two million nodes, which no fixture reaches, so
        a small one is put in its place: with the budget shared, the second
        entry runs out; with one per entry -- which is what this was before --
        both succeed however small it is, because the bound never sees the
        first entry's work.
        """
        self.addCleanup(setattr, db_tools, "new_walk", convert.new_walk)

        # What one entry actually costs, measured rather than guessed, so this
        # keeps working if the fixture's items change shape.
        probe = convert.new_walk()
        before = probe.budget
        convert.to_json(self.project.get_entry("items", 1), self.project.codec,
                        "summary", probe)
        one_entry = before - probe.budget
        self.assertGreater(one_entry, 0)

        # Room for one entry and a little over -- never for two.
        db_tools.new_walk = lambda: convert._Walk(budget=one_entry + 2)
        with self.assertRaises((ToolError, ConversionError)):
            self.call("db_get", table="items", ids=[1, 2])


class TestOneBudgetForAWholeResponse(ToolTest):
    """A string's ivar record must not start its own walk.

    `_string_to_json` rendered the record with the *public* `to_json`, which
    begins a fresh `_Walk` -- so both the node budget and the depth counter
    restarted once per string, and a few kilobytes of crafted file could ask
    for an unbounded walk.
    """

    def carrying_ivars(self, inner):
        """A RubyString whose ivar record holds `inner`."""
        text = self.project.codec.make_string("hi")
        text.ivars = dict(text.ivars)
        text.ivars["@extra"] = inner
        return text

    def test_a_string_ivar_is_charged_to_the_callers_budget(self):
        nested = [[1] * 50] * 50          # about 2,550 nodes
        walk = convert.new_walk()
        before = walk.budget
        convert.to_json(self.carrying_ivars(nested), self.project.codec,
                        "summary", walk)
        self.assertGreater(before - walk.budget, 2000)

    def test_a_string_ivar_cannot_reset_the_depth_limit(self):
        deep = "x"
        for _ in range(convert.MAX_JSON_DEPTH + 20):
            deep = [deep]
        with self.assertRaises(ConversionError) as caught:
            convert.to_json(self.carrying_ivars(deep), self.project.codec)
        self.assertIn("nests deeper", str(caught.exception))


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


class TestSharedChildrenCannotAmplifyAWalk(SharedObjectMap, ToolTest):
    """A file whose objects share children must not buy an unbounded walk.

    Ruby Marshal has object links and this codec supports them, so a few
    kilobytes can describe a structure that *renders* as millions of nodes:
    one command, a list of two thousand links to it, fifty links to that page,
    fifty links to that event. Measured before the fix, from a 7.4 KB map:
    event_get 36s, search 20s, validate 13s -- on a single-threaded stdio
    server, with no way for the user to interrupt any of them.

    `marshal_rb` bounds nesting depth and never object count, and the walk
    budget `_Walk` applies to one JSON conversion does not reach the command
    walk at all.
    """

    def test_event_get_stops_and_says_so(self):
        self.craft_shared_map()
        out = self.assert_bounded("event_get", map_id=1)
        self.assertIn("commands", " ".join(str(v) for v in out.values()))
        # The key, not a substring of the whole reply. Searching every value
        # for "stopped" also matches a note about something else entirely,
        # which is how the validate case below came to be satisfied by the
        # findings ceiling rather than by the walk bound.
        self.assertIn("command_walk_stopped", out,
                      "the reply does not say the walk was cut short: %s"
                      % sorted(out))

    def test_search_stops_and_says_so(self):
        self.craft_shared_map()
        out = self.assert_bounded("search", query="zzz-nothing-matches",
                                  scopes=["events"])
        # A search that stopped early must not read as "there are none".
        self.assertTrue(out.get("may_have_more") or "stopped" in str(out),
                        "an empty result from a cut-short walk reads as "
                        "nothing found: %s" % sorted(out))

    def test_validate_stops_and_says_so(self):
        """The walk's own signal, which `truncated` is not.

        `truncated` says the findings list hit its limit; `command_walk_stopped`
        says the walk was abandoned. Measured: with the walk charge reverted
        this map collects 200 findings and answers `truncated: True` in 6.9
        seconds, so an assertion of `truncated or "stopped" in str(out)` was
        satisfied by the findings ceiling and passed with the bound gone --
        and the 10-second wall-clock allowance did not catch it either,
        because 6.9 is under 10. Two facts, one assertion, and the weaker
        fact was the one that answered.
        """
        self.craft_shared_map()
        out = self.assert_bounded("validate", map_ids=[1], scopes=["events"])
        self.assertIn("command_walk_stopped", out,
                      "validate reports a clean bill from a walk that never "
                      "finished: %s" % sorted(out))


class TestSearchBoundsItsTotalWork(ToolTest):
    """A pattern measured against 2 KB then runs over the whole project.

    `measure_cost` bounds *one* match: up to two seconds across ten subjects
    of 2000 characters, so about 0.2s against any one candidate. Nothing
    bounded how many candidates there are. `search` applies the pattern once
    per script line and once per string field in the project -- of the order of
    a hundred thousand candidates -- so a pattern chosen to sit just under that
    clock passed the gate and then cost that much again per candidate. `limit`
    does not help: a pattern picked to be slow is one that matches nothing, so
    the collector never fills and every scope runs to the end.

    A character budget cannot fix this. To be a real worst-case bound it would
    have to be about ten thousand characters, which is not a usable search; to
    be usable it would have to be megabytes, which bounds nothing. Time
    composes where characters do not -- one match is bounded by the
    measurement, so the sum can be bounded by a deadline checked between
    matches, which is the one place it *can* be checked, since `re` cannot be
    interrupted mid-match.

    The clock is injected so these tests do not race a real one.
    """

    def matcher_with_clock(self, ticks, seconds=1.0):
        """A matcher whose clock returns each of `ticks` in turn."""
        from rpgvxace_mcp.tools import search_tools
        readings = iter(ticks)
        return search_tools._Matcher(lambda text: False, whole=True,
                                     clock=lambda: next(readings),
                                     seconds=seconds)

    def test_the_matcher_stops_when_its_time_is_spent(self):
        # First reading sets the deadline at 0.0 + 1.0; the third is past it.
        matcher = self.matcher_with_clock([0.0, 0.5, 1.5, 2.0])
        self.assertFalse(matcher("x"))
        self.assertFalse(matcher.exhausted, "stopped while it still had time")
        matcher("x")
        self.assertTrue(matcher.exhausted, "ran past its deadline")

    def test_an_exhausted_matcher_does_not_test_again(self):
        """Once stopped it must stay stopped, and must not read the clock.

        The iterator runs out after the reading that exhausts it, so a later
        call reaching the clock raises StopIteration rather than passing
        quietly.
        """
        matcher = self.matcher_with_clock([0.0, 1.5])
        matcher("x")
        self.assertTrue(matcher.exhausted)
        self.assertFalse(matcher("x"))
        self.assertFalse(matcher("x"))

    def test_a_search_that_stopped_early_says_so(self):
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        # No time at all, so the first candidate string exhausts it.
        search_tools.MAX_SEARCH_SECONDS = 0.0

        out = self.call("search", query="zzz-nothing-matches-this")
        self.assertIn("text_scan_stopped", out)
        self.assertTrue(out["may_have_more"],
                        "a scan that stopped must not read as 'no hits'")
        self.assertEqual(out["count"], 0)

    def test_a_stopped_search_keeps_what_it_found(self):
        """Stopping is not failing: the hits already collected are real."""
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        search_tools.MAX_SEARCH_SECONDS = 0.0
        out = self.call("search", query="Potion")
        self.assertIn("text_scan_stopped", out)
        self.assertIsInstance(out["hits"], list)

    def test_a_search_that_gave_up_does_not_claim_the_later_scopes(self):
        """A scope records itself before it tests anything in it.

        So a search that has spent its deadline must *stop*, not walk on
        answering False: walking on would load and decode the rest of the
        project and then report all four scopes as searched, which is the
        defect `searching()` exists to prevent -- one step further along.
        """
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        search_tools.MAX_SEARCH_SECONDS = 0.0

        out = self.call("search", query="Potion")
        self.assertEqual(out["scopes_searched"], ["events"],
                         "a search that gave up in the first scope reported "
                         "reading the ones it never tested: %s"
                         % out["scopes_searched"])

    def test_an_ordinary_search_does_not_stop(self):
        out = self.call("search", query="Potion")
        self.assertNotIn("text_scan_stopped", out)
        self.assertTrue(out["hits"], "the fixture project has a Potion")


class TestEveryWalkOnAWritePathIsBounded(SharedObjectMap, ToolTest):
    """`WalkBudget` says it is the only thing between a crafted file and a
    walk the user must kill the server to escape. Three walks never got one.

    `find_references` is the worst of them, because it is reached from
    `db_edit op="delete"` and `map_edit op="delete"` -- a *write* path. It
    stops as soon as it has `limit` hits, so it is bounded exactly when the
    answer is "yes, lots"; when the answer is "nothing points at it" it has
    walked every command in the project to find that out. On the crafted map
    that is five million visits, and the reply then says
    `references_found: 0` with `references_checked: true`, which reads as
    "safe to delete".

    `event_get`'s common-event branch never took a budget either, and built a
    fresh conversion budget per command -- the exact defect `to_json`'s
    docstring describes as fixed.
    """

    def test_deleting_an_entry_bounds_its_reference_scan(self):
        self.craft_shared_map()
        out = self.assert_bounded("db_edit", table="items", dry_run=True,
                                  ops=[{"op": "delete", "id": 1}])
        self.assertTrue(
            "stopped" in str(out) or "incomplete" in str(out),
            "a scan that never finished reports nothing points at the entry: "
            "%s" % out)

    def test_a_bounded_scan_does_not_claim_the_entry_is_free(self):
        """The claim, not just the timing: `references_checked` must not lie.

        Zero references and `references_checked: true` together mean "nothing
        points at this, delete it". A scan that stopped early can produce that
        pair honestly for neither reason, so it must report as a third state.
        """
        self.craft_shared_map()
        out = self.assert_bounded("db_edit", table="items", dry_run=True,
                                  ops=[{"op": "delete", "id": 1}])
        # `would_apply` on a dry run, not `applied` -- see
        # TestADryRunDoesNotReadAsDone for why the two names differ.
        row = out["would_apply"][0]
        self.assertTrue(row.get("references_scan_stopped"),
                        "the reply does not say the scan stopped: %s" % row)
        self.assertFalse(
            row["references_checked"],
            "a scan that stopped early is reported as a completed check, so "
            "%d reference(s) reads as all of them"
            % row.get("references_found", 0))
        self.assertTrue(
            any("did not finish" in warning for warning in out["warnings"]),
            "nothing in the warnings says the check was incomplete: %s"
            % out["warnings"])

    def test_deleting_a_map_bounds_its_reference_scan(self):
        self.craft_shared_map()
        out = self.assert_bounded("map_edit", op="delete", map_id=3,
                                  dry_run=True)
        self.assertIn("incoming_transfers", out)
        self.assertTrue(
            "stopped" in str(out) or "incomplete" in str(out),
            "a map delete whose transfer scan never finished says the map is "
            "unreferenced: %s" % out)

    def test_reading_a_common_event_is_bounded(self):
        """One shared command repeated past the ceiling, in a common event."""
        common = self.project.get_entry("common_events", 1)
        command = RubyObject("RPG::EventCommand")
        command.set("code", 108)
        command.set("indent", 0)
        command.set("parameters", [self.project.codec.make_string("x")])
        # Above MAX_WALK_STEPS, so a budget must stop it.
        common.set("list", [command] * 250_000)
        self.project.save_file(self.project.filename_for("common_events"),
                               tool="test", summary="one shared command")
        self.ctx = self.reopen()

        # Either bound may fire: the walk budget truncates and says so,
        # or the conversion refuses because the reply would not fit the
        # transport. Both are bounded and both name a remedy; a short reply
        # that says nothing is the outcome this suite exists to forbid.
        from rpgvxace_mcp.convert import ConversionError as CE
        try:
            out = self.assert_bounded("event_get", common_event_id=1,
                                      include_raw=True)
        except (CE, ToolError) as exc:
            self.assertIn("read a smaller part", str(exc).lower())
            return
        self.assertIn("command_walk_stopped", out)

    def test_a_bounded_common_event_still_reports_its_real_length(self):
        """Stopping the render must not misreport how long the list is."""
        common = self.project.get_entry("common_events", 1)
        command = RubyObject("RPG::EventCommand")
        command.set("code", 108)
        command.set("indent", 0)
        command.set("parameters", [self.project.codec.make_string("x")])
        common.set("list", [command] * 250_000)
        self.project.save_file(self.project.filename_for("common_events"),
                               tool="test", summary="one shared command")
        self.ctx = self.reopen()

        out = self.assert_bounded("event_get", common_event_id=1)
        self.assertEqual(out["command_count"], 250_000)
        self.assertIn("command_walk_stopped", out)

    def test_an_ordinary_common_event_is_unaffected(self):
        out = self.call("event_get", common_event_id=1, include_raw=True)
        self.assertNotIn("command_walk_stopped", out)
        self.assertIn("commands", out)

    def test_an_ordinary_delete_still_reports_its_references(self):
        out = self.call("db_edit", table="items", dry_run=True,
                        ops=[{"op": "delete", "id": 1}])
        self.assertNotIn("stopped", str(out))


class TestNoWalkCollectsPastTheCeiling(ToolTest):
    """Every walk that appends inside a loop stops at MAX_FINDINGS.

    MAX_FINDINGS' comment claims it is enforced "in every walk that appends
    inside a loop". It was not: `_check_map_assets` and `_check_database_assets`
    had no guard, `check_tilesets` guarded `findings` while its loop appended to
    `checker.findings` -- so that break could never fire -- and
    `check_event_pages` and `_check_encounters` had none either.

    `validate` slices the report to MAX_FINDINGS and checks it between scopes,
    which is why none of this shows up in a reply: the cost is the collecting,
    and one crafted map can produce a finding per event page.
    """

    # Small, so the ceiling is reached without building a huge map. It is read
    # as a module attribute at each guard, so patching it exercises the real
    # branch.
    CEILING = 4

    # Enough broken references to pass any small ceiling several times over.
    EVENTS, PAGES = 40, 40

    def setUp(self):
        super().setUp()
        from rpgvxace_mcp import analysis
        from rpgvxace_mcp.analysis import lint
        self.analysis = analysis
        # `lint` as well as the package: the guards read the constant from
        # the module they live in, and `analysis` is now a re-export -- so
        # patching only the package left every real branch reading 5000 and
        # the assertions failing against a ceiling nothing had lowered.
        for module in (analysis, lint):
            original = module.MAX_FINDINGS
            self.addCleanup(setattr, module, "MAX_FINDINGS", original)
            module.MAX_FINDINGS = self.CEILING

    def craft_broken_map(self):
        """Map 1, where every event page names a graphic that is not there.

        One page object shared by every slot, so the file stays small and the
        finding count is EVENTS x PAGES.
        """
        game_map = self.project.load_map(1)
        graphic = RubyObject("RPG::Event::Page::Graphic")
        graphic.set("character_name",
                    self.project.codec.make_string("no-such-sheet"))
        graphic.set("character_index", 0)

        page = RubyObject("RPG::Event::Page")
        page.set("condition", RubyObject("RPG::Event::Page::Condition"))
        page.set("graphic", graphic)
        for name, value in (("trigger", 0), ("priority_type", 0),
                            ("move_type", 0), ("move_speed", 3),
                            ("move_frequency", 3), ("walk_anime", True),
                            ("step_anime", False), ("direction_fix", False),
                            ("through", False), ("list", [])):
            page.set(name, value)

        event = RubyObject("RPG::Event")
        event.set("id", 1)
        event.set("name", self.project.codec.make_string("E"))
        event.set("x", 0)
        event.set("y", 0)
        event.set("pages", [page] * self.PAGES)

        events = RubyHash()
        for event_id in range(1, self.EVENTS + 1):
            events.set(event_id, event)
        game_map.set("events", events)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)

    def test_the_asset_walk_stops_collecting(self):
        self.craft_broken_map()
        found = self.analysis.check_assets(self.project, [1])
        self.assertLessEqual(len(found), self.CEILING,
                             "the asset walk collected %d findings past a "
                             "ceiling of %d" % (len(found), self.CEILING))

    def test_the_asset_checker_itself_stops_collecting(self):
        """The bound belongs to the collector, not to each of its callers.

        Four walks share this object, and the one guard that existed was
        written against the wrong list. A collector that refuses past its
        ceiling cannot be got wrong by a fifth caller.
        """
        checker = self.analysis._AssetChecker(self.project)
        location = self.analysis._map_at(1, "parallax_name")
        for _ in range(self.CEILING * 5):
            checker.check("no-such-parallax", "parallaxes", location)
        self.assertLessEqual(len(checker.findings), self.CEILING)
        self.assertTrue(checker.full)

    def test_the_page_walk_stops_collecting(self):
        """Shadowed pages: one finding per event, and EVENTS is from the file."""
        self.craft_broken_map()
        found = self.analysis.check_event_pages(self.project, [1])
        self.assertLessEqual(len(found), self.CEILING,
                             "the page walk collected %d findings past a "
                             "ceiling of %d" % (len(found), self.CEILING))

    def test_the_tileset_walk_stops_collecting(self):
        """Its guard was on `findings` while the loop appended to the checker."""
        tables = self.analysis._Tables(self.project)
        broken = []
        for entry_id, tileset in tables.rows_of("tilesets"):
            if isinstance(tileset, RubyObject):
                tileset.set("tileset_names",
                            [self.project.codec.make_string("no-such-sheet")
                             for _ in range(20)])
                broken.append(entry_id)
        if not broken:
            self.skipTest("no tilesets in this engine's fixture")
        found = self.analysis.check_tilesets(self.project, [1], tables)
        self.assertLessEqual(len(found), self.CEILING,
                             "the tileset walk collected %d findings past a "
                             "ceiling of %d" % (len(found), self.CEILING))

    def test_an_ordinary_project_still_reports_everything(self):
        """The ceiling must not clip a real project's findings."""
        from rpgvxace_mcp import analysis
        analysis.MAX_FINDINGS = 5000
        out = self.call("validate")
        self.assertNotIn("findings_ceiling_reached", out)
        self.assertFalse(out["truncated"])


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
        "MAX_SUBJECTS_PER_CHARACTER": "how many subjects the measurement builds",
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


class TestAnEmptyPageStillCostsSomething(EmptyPageMap, ToolTest):
    """A visit has to be charged for, or the product is free."""

    def test_the_budget_charges_for_a_list_of_no_commands(self):
        from rpgvxace_mcp import analysis
        budget = analysis.WalkBudget()
        for _ in range(analysis.MAX_WALK_STEPS + 1):
            if not budget.spend(0):
                break
        self.assertTrue(budget.exhausted,
                        "a budget charged with zero-length lists never ran "
                        "out, so the walk it bounds is unbounded")

    def test_the_walk_charges_per_event_and_per_page(self):
        """Not per command: the product is what a crafted file buys."""
        from rpgvxace_mcp import analysis
        budget = analysis.WalkBudget()
        visited = 0
        for _ in analysis.iter_command_lists(self.project, None, budget):
            visited += 1
        self.assertTrue(budget.exhausted or visited < 1000,
                        "the walk neither ran out nor finished small")

    def test_search_stops_and_says_so(self):
        self.craft_empty_page_map()
        out = self.assert_bounded("search", query="zzz-no-match",
                                  scopes=["events"])
        self.assertTrue(
            out.get("may_have_more") or "command_walk_stopped" in out,
            "an empty result from an unbounded walk reads as 'there are "
            "none': %s" % sorted(out))

    def test_validate_events_stops_and_says_so(self):
        self.craft_empty_page_map()
        out = self.assert_bounded("validate", map_ids=[1], scopes=["events"])
        self.assertTrue(out.get("truncated") or "command_walk_stopped" in out,
                        "validate gave a clean bill from a walk that never "
                        "finished: %s" % sorted(out))

    def test_validate_assets_is_bounded(self):
        """`_check_map_assets` took no budget at all.

        It walks events x pages itself rather than through
        `iter_command_lists`, and its only guard was the asset checker's
        finding ceiling -- which an empty graphic name never reaches, because
        `check` returns before it collects.
        """
        self.craft_empty_page_map()
        self.assert_bounded("validate", map_ids=[1], scopes=["assets"])

    def test_the_page_rule_walk_is_bounded(self):
        """`check_event_pages` took no budget either.

        Its ceiling is on findings, and a page with a condition flag set
        produces none -- so it walked every page of every event for nothing.
        """
        self.craft_empty_page_map()
        self.assert_bounded("validate", map_ids=[1], scopes=["events"])

    def test_event_get_is_bounded(self):
        """Bounded, and either outcome says why.

        Since `MAX_JSON_NODES` was sized to what the transport can carry,
        this map's reply is past that ceiling -- so the conversion refuses
        with a message naming the remedy instead of returning a truncated
        reply with a note. Both are bounded and both are actionable; what
        would not be acceptable is a reply that is short and silent about it.
        """
        from rpgvxace_mcp.convert import ConversionError as CE
        self.craft_empty_page_map()
        try:
            out = self.assert_bounded("event_get", map_id=1)
        except (CE, ToolError) as exc:
            self.assertIn("read a smaller part", str(exc).lower())
            return
        self.assertTrue(
            any("stopped" in str(v) or "not read" in str(v)
                for v in out.values()),
            "the reply does not say the walk was cut short: %s" % sorted(out))

    def test_map_get_is_bounded(self):
        """MAX_EVENT_SUMMARY_ROWS counted page rows only.

        `rows` was incremented inside the pages loop, while the event row was
        appended unconditionally outside it -- so events with empty @pages
        cost nothing and the summary was unbounded in the one dimension a
        crafted file controls most cheaply.
        """
        self.craft_empty_page_map()
        out = self.assert_bounded("map_get", map_id=1, include_events=True)
        self.assertLessEqual(len(out["events"]),
                             map_read_tools.MAX_EVENT_SUMMARY_ROWS)
        self.assertIn("events_truncated", out)

    def test_every_row_counts_toward_the_summary_ceiling(self):
        """Events as well as pages, against one ceiling."""
        self.craft_empty_page_map()
        out = self.assert_bounded("map_get", map_id=1, include_events=True)
        rows = len(out["events"]) + sum(len(e["pages"]) for e in out["events"])
        self.assertLessEqual(rows, map_read_tools.MAX_EVENT_SUMMARY_ROWS + 1)

    def test_deleting_a_map_is_bounded(self):
        self.craft_empty_page_map()
        out = self.assert_bounded("map_edit", op="delete", map_id=3,
                                  dry_run=True)
        self.assertTrue(
            "stopped" in str(out) or "incomplete" in str(out),
            "a transfer scan that never finished says the map is "
            "unreferenced: %s" % sorted(out))

    def test_an_ordinary_project_is_unaffected(self):
        """The floor must not make a real project look crafted."""
        out = self.call("validate")
        self.assertNotIn("command_walk_stopped", out)
        out = self.call("map_get", map_id=1, include_events=True)
        self.assertNotIn("events_truncated", out)


class TestOneBatchGetsOneWalkAllowance(ToolTest):
    """A batch of deletes traverses the project once, not once per delete.

    `find_references` returns early only when it *finds* hits, so deleting
    an entry nothing points at -- the ordinary case -- is the one that costs
    a full walk of every map event page, every common event and every troop
    page. Sharing one allowance across the batch capped the damage and left
    most of the batch reporting its scan as unfinished; one bulk pass
    removes it, and `validate` states the same rule for itself one module
    over.

    Counts the *traversals*, and patches the module the scanner calls
    `iter_command_lists` from. An earlier version counted `WalkBudget`
    allocations through `analysis.WalkBudget`, which `db_tools` binds at
    import -- so after `analysis` became a package the patch reached nothing
    and the assertion held whatever the code did.
    """

    def test_a_batch_shares_one_allowance(self):
        from rpgvxace_mcp.analysis import references
        walks = []
        original = references.iter_command_lists

        def counted(*args, **kwargs):
            walks.append(1)
            return original(*args, **kwargs)

        references.iter_command_lists = counted
        self.addCleanup(setattr, references, "iter_command_lists", original)
        ids = [entry_id for entry_id, _ in self.project.entries("items")][:4]
        self.assertGreater(len(ids), 1, "the fixture needs several items")
        self.call("db_edit", table="items", dry_run=True,
                  ops=[{"op": "delete", "id": i} for i in ids])
        # Exactly one, not "at most one": a batch that scans nothing also
        # walks the project zero times, and that is the other way to get
        # this wrong -- every delete would then report no references from a
        # scan that never ran, which is the answer a caller acts on
        # destructively.
        self.assertEqual(len(walks), 1,
                         "a %d-delete batch walked the whole project %d "
                         "times" % (len(ids), len(walks)))


class TestEveryWalkSpendsItsAllowance(ToolTest):
    """Each walk is asked directly whether it charged for what it visited.

    A wall-clock assertion can only fail when a walk is slow enough, which
    makes it a test of the fixture as much as of the code -- and it cannot
    tell "bounded" from "small". The budget knows exactly, so it is the thing
    to ask.
    """

    SLOTS = 5000

    def setUp(self):
        super().setUp()
        from rpgvxace_mcp import analysis
        self.analysis = analysis

    def map_with(self, events):
        game_map = self.project.load_map(1)
        game_map.set("events", events)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)

    def hash_of(self, value):
        events = RubyHash()
        for event_id in range(1, self.SLOTS + 1):
            events.set(event_id, value)
        return events

    def event_with_pages(self, pages):
        event = RubyObject("RPG::Event")
        event.set("id", 1)
        event.set("name", self.project.codec.make_string("E"))
        event.set("x", 0)
        event.set("y", 0)
        event.set("pages", pages)
        return event

    def spent(self, walk):
        """Run a walk against a small allowance and report whether it charged."""
        budget = self.analysis.WalkBudget(limit=64)
        walk(budget)
        return budget

    def test_an_events_hash_of_non_events_is_charged_for(self):
        """Skipped by `continue`, so it used to cost nothing."""
        self.map_with(self.hash_of(42))
        budget = self.spent(lambda b: list(
            self.analysis.iter_command_lists(self.project, [1], b)))
        self.assertTrue(budget.exhausted,
                        "a hash of %d non-event values was walked free"
                        % self.SLOTS)

    def test_a_pages_list_of_non_pages_is_charged_for(self):
        """Same shape one level down."""
        self.map_with(self.hash_of(self.event_with_pages([7] * self.SLOTS)))
        budget = self.spent(lambda b: list(
            self.analysis.iter_command_lists(self.project, [1], b)))
        self.assertTrue(budget.exhausted,
                        "a @pages list of %d non-page values was walked free"
                        % self.SLOTS)

    def test_the_page_rule_walk_spends_the_allowance(self):
        """`check_event_pages` does not go through `iter_command_lists`."""
        self.map_with(self.hash_of(self.event_with_pages([])))
        budget = self.spent(
            lambda b: self.analysis.check_event_pages(self.project, [1], b))
        self.assertTrue(budget.exhausted,
                        "the page rule walk charged nothing for %d events"
                        % self.SLOTS)

    def test_the_asset_walk_spends_the_allowance(self):
        """`_check_map_assets` does not either -- its sibling always did.

        Called directly rather than through `check_assets`, which also hands
        the budget to `_check_command_assets`: that walk charges through
        `iter_command_lists`, so it would exhaust the allowance on its own and
        this test would pass whatever the map walk did.
        """
        self.map_with(self.hash_of(self.event_with_pages([])))
        checker = self.analysis._AssetChecker(self.project)
        budget = self.spent(
            lambda b: self.analysis._check_map_assets(
                self.project, checker, [1], b))
        self.assertTrue(budget.exhausted,
                        "the map asset walk charged nothing for %d events"
                        % self.SLOTS)

    # ------------------------------------------------------------------
    # The two branches below the map branch. `iter_command_lists` walks
    # maps, then common events, then troops; round 7 charged per event and
    # per page slot in the first and left the other two charging only
    # `len(commands)`, which is zero for a slot that is not a page.
    # ------------------------------------------------------------------

    def db_with(self, table, value):
        """Replace a whole database table's container and reopen."""
        filename = self.project.filename_for(table)
        self.project.path_for(filename).write_bytes(marshal_rb.dump(value))
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)

    def troop_with_pages(self, pages):
        troop = RubyObject("RPG::Troop")
        troop.set("id", 1)
        troop.set("name", self.project.codec.make_string("T"))
        troop.set("members", [])
        troop.set("pages", pages)
        return troop

    def test_a_troops_array_of_non_troops_is_charged_for(self):
        """The troop branch reached `spend` only past two isinstance tests."""
        self.db_with("troops", [None] + [42] * self.SLOTS)
        budget = self.spent(lambda b: list(
            self.analysis.iter_command_lists(self.project, [1], b)))
        self.assertTrue(budget.exhausted,
                        "an array of %d non-troop values was walked free"
                        % self.SLOTS)

    def test_a_troop_pages_list_of_non_pages_is_charged_for(self):
        """One shared troop whose @pages is a long list of integers.

        This is the shape that costs a product rather than a sum: the pages
        list is written once and every row is a link to the same troop, so
        the file stays small while the inner loop runs rows x pages times.

        The allowance is sized so that only the *inner* charge can spend it:
        50 rows against a limit of 2,000 leaves the per-row charge unable to
        exhaust it, while 50 x 5,000 slots exhausts it many times over. With
        a small allowance the per-row charge answered on its own, which is
        why the guard on the inner one read VACUOUS.
        """
        troop = self.troop_with_pages([7] * self.SLOTS)
        self.db_with("troops", [None] + [troop] * 50)
        budget = self.analysis.WalkBudget(limit=2000)
        list(self.analysis.iter_command_lists(self.project, [1], budget))
        self.assertTrue(budget.exhausted,
                        "a troop @pages list of %d non-page values was "
                        "walked free" % self.SLOTS)

    def test_a_common_events_array_of_non_events_is_charged_for(self):
        """Linear in file size, so cheaper -- but still free per slot."""
        self.db_with("common_events", [None] + [42] * self.SLOTS)
        budget = self.spent(lambda b: list(
            self.analysis.iter_command_lists(self.project, [1], b)))
        self.assertTrue(budget.exhausted,
                        "an array of %d non-common-event values was walked "
                        "free" % self.SLOTS)

    def test_an_ordinary_project_does_not_exhaust_a_real_allowance(self):
        """The charges must not make a real project look crafted."""
        budget = self.analysis.WalkBudget()
        list(self.analysis.iter_command_lists(self.project, None, budget))
        self.assertFalse(budget.exhausted)
        self.assertGreater(budget.remaining,
                           self.analysis.MAX_WALK_STEPS * 0.9,
                           "a fixture project spent more than a tenth of the "
                           "allowance, so the charges are too heavy")


class TestAnEventWithNoPagesStillCostsARow(ToolTest):
    """`map_get`'s ceiling counted page rows, so an event with none was free.

    Four bytes per hash pair on disk buys one five-key dict in the reply, and
    `MAX_TOOL_RESULT_CHARS` is measured after the reply has been serialised --
    so it bounds the response and not the work or the memory.
    """

    def craft(self):
        event = RubyObject("RPG::Event")
        event.set("id", 1)
        event.set("name", self.project.codec.make_string("E"))
        event.set("x", 0)
        event.set("y", 0)
        event.set("pages", [])                     # no pages at all
        events = RubyHash()
        for event_id in range(1, map_read_tools.MAX_EVENT_SUMMARY_ROWS * 3):
            events.set(event_id, event)
        game_map = self.project.load_map(1)
        game_map.set("events", events)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()

    def test_the_summary_stops_at_the_ceiling(self):
        self.craft()
        out = self.call("map_get", map_id=1, include_events=True)
        self.assertLessEqual(len(out["events"]),
                             map_read_tools.MAX_EVENT_SUMMARY_ROWS,
                             "%d event rows came back against a ceiling of %d"
                             % (len(out["events"]),
                                map_read_tools.MAX_EVENT_SUMMARY_ROWS))

    def test_and_says_that_it_stopped(self):
        self.craft()
        out = self.call("map_get", map_id=1, include_events=True)
        self.assertIn("events_truncated", out)

    def test_event_get_is_bounded_the_same_way(self):
        self.craft()
        out = self.call("event_get", map_id=1)
        self.assertLess(len(out["events"]),
                        map_read_tools.MAX_EVENT_SUMMARY_ROWS * 3)


class TestNarrowingValidateActuallyNarrowsIt(ToolTest):
    """`map_ids` exists to make a run cheap, and bought almost nothing.

    `check_commands` built its `map_sizes` lookup from `iter_maps(project)`
    with no ids, so every map file in the project was loaded and Marshal-
    parsed even when the caller asked about one map. Parsing the maps is most
    of what a validate costs, so `validate map_ids=[5] scopes=["events"]`
    measured at 74% of a whole-project run.

    The comment above it was right that the *check* cannot be narrowed -- a
    transfer on map 1 can target any map -- but that is an argument about
    which maps may be *looked up*, not about loading them all up front. A
    lookup that parses a map the first time a Transfer Player names it
    satisfies both.
    """

    def loaded_maps(self, **arguments):
        """Which map files a validate run actually parsed.

        Watched on `self.ctx.project`, which is the object the tool uses. It
        was `self.project` -- a different object after a `reopen()` -- so the
        spy saw nothing, `seen` came back empty, and the assertion passed
        whatever the code did. Proving the guard is what surfaced that.
        """
        seen = []
        project = self.ctx.project
        original = project.load_map

        def spy(map_id, refresh=False):
            seen.append(map_id)
            return original(map_id, refresh)

        project.load_map = spy
        self.addCleanup(setattr, project, "load_map", original)
        self.call("validate", **arguments)
        self.assertTrue(seen, "the spy observed no map loads at all, so this "
                              "test cannot decide anything")
        return seen

    def test_a_narrowed_run_does_not_parse_every_map(self):
        """A map nothing points at must not be read.

        The fixture's two maps do not settle this: map 1 transfers to map 2,
        so "loads every map" and "loads the maps its transfers name" are the
        same set. A third map nobody references tells them apart.
        """
        spare = self.call("map_edit", op="create", name="Unreferenced",
                          width=20, height=15)["map_id"]
        self.ctx = self.reopen()

        loaded = set(self.loaded_maps(map_ids=[1], scopes=["events"]))
        self.assertNotIn(
            spare, loaded,
            "a run narrowed to map 1 parsed map %d, which nothing transfers "
            "to, so map_ids bought nothing: parsed %s"
            % (spare, sorted(loaded)))

    def test_a_whole_run_still_checks_every_map(self):
        """Narrowing the loading must not narrow the checking."""
        out = self.call("validate", scopes=["events"])
        self.assertNotIn("command_walk_stopped", out)

    def test_a_transfer_to_an_undersized_map_is_still_caught(self):
        """The finding the eager lookup existed to produce.

        A Transfer Player on map 1 targeting a map that is too small must be
        reported even though that map is outside `map_ids` -- which is the
        whole reason the lookup covers every map.
        """
        target = None
        for map_id, _ in self.project.entries("map_infos"):
            if map_id != 1 and self.project.exists(self.project.map_file(map_id)):
                target = map_id
                break
        if target is None:
            self.skipTest("the fixture has only one map file")

        small = self.project.load_map(target)
        width, height = small.get("width"), small.get("height")
        game_map = self.project.load_map(1)
        event = game_map.get("events").sorted_items()[0][1]
        command = RubyObject("RPG::EventCommand")
        command.set("code", 201)
        command.set("indent", 0)
        # Well outside the target map, whatever size it is.
        command.set("parameters", [0, target, width + 50, height + 50, 0, 0])
        event.get("pages")[0].set("list", [command])
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()

        out = self.call("validate", map_ids=[1], scopes=["events"])
        rules = [f["rule"] for f in out["findings"]]
        self.assertIn("transfer_outside_map", rules,
                      "the transfer check stopped seeing maps outside the "
                      "scope, which is what the eager lookup was for: %s"
                      % rules)


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


class TestTheMapScopeStopsCollecting(ToolTest):
    """`check_maps` accumulates, so `check_maps` is what has to stop.

    One map with thousands of encounters pointing at a troop that is not
    there is thousands of findings from one file -- and the encounter list's
    length comes out of the file, so it is not a bound.
    """

    CEILING = 4
    ENCOUNTERS = 5000

    def setUp(self):
        super().setUp()
        from rpgvxace_mcp import analysis
        self.analysis = analysis
        original = analysis.MAX_FINDINGS
        from rpgvxace_mcp.analysis import lint
        self.addCleanup(setattr, lint, "MAX_FINDINGS", original)
        lint.MAX_FINDINGS = self.CEILING
        self.addCleanup(setattr, analysis, "MAX_FINDINGS", original)
        analysis.MAX_FINDINGS = self.CEILING

    def craft_bad_encounters(self):
        """Map 1, with an encounter list full of a troop id nothing defines."""
        troop_ids = {entry_id for entry_id, _ in self.project.entries("troops")}
        missing = max(troop_ids) + 1 if troop_ids else 99
        game_map = self.project.load_map(1)
        if self.ace:
            entry = RubyObject("RPG::Map::Encounter")
            entry.set("troop_id", missing)
            entry.set("weight", 10)
            entry.set("region_set", [])
            encounters = [entry] * self.ENCOUNTERS
        else:
            encounters = [missing] * self.ENCOUNTERS
        game_map.set("encounter_list", encounters)
        game_map.set("encounter_step", 30)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)
        return missing

    def craft_bad_encounters_everywhere(self):
        """The same list on every map file, so the outer loop is the subject."""
        troop_ids = {entry_id for entry_id, _ in self.project.entries("troops")}
        missing = max(troop_ids) + 1 if troop_ids else 99
        ids = sorted(self.project.map_ids_on_disk())
        for map_id in ids:
            game_map = self.project.load_map(map_id)
            if self.ace:
                entry = RubyObject("RPG::Map::Encounter")
                entry.set("troop_id", missing)
                entry.set("weight", 10)
                entry.set("region_set", [])
                encounters = [entry] * self.ENCOUNTERS
            else:
                encounters = [missing] * self.ENCOUNTERS
            game_map.set("encounter_list", encounters)
            game_map.set("encounter_step", 30)
            self.project.path_for(self.project.map_file(map_id)).write_bytes(
                marshal_rb.dump(game_map))
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)
        return ids

    def test_the_map_scope_stops_at_the_ceiling(self):
        """Across maps, which is where the discarded guard shows.

        One map cannot show it: `_check_encounters` caps its own list at
        MAX_FINDINGS, so a single map returns at most that many. The defect is
        that `check_maps` then throws that list away and starts a fresh one
        for the next map -- so N maps contribute N times the ceiling, and the
        map count comes from the project.
        """
        maps = self.craft_bad_encounters_everywhere()
        self.assertGreater(len(maps), 1, "this needs more than one map")
        tables = self.analysis._Tables(self.project)
        found = self.analysis.check_maps(self.project, maps, tables)
        self.assertLessEqual(
            len(found), self.CEILING,
            "check_maps collected %d findings from %d maps against a ceiling "
            "of %d, because the guard was on a list it discards"
            % (len(found), len(maps), self.CEILING))

    def test_the_encounter_walk_stops_too(self):
        """Directly, so the sub-walk is bounded and not just its caller."""
        missing = self.craft_bad_encounters()
        game_map = self.project.load_map(1)
        troop_ids = {entry_id for entry_id, _ in self.project.entries("troops")}
        self.assertNotIn(missing, troop_ids)
        found = self.analysis._check_encounters(
            self.project, 1, game_map, troop_ids)
        self.assertLessEqual(len(found), self.CEILING)

    def test_a_real_encounter_list_is_still_checked(self):
        """The ceiling must not stop a project's genuine findings."""
        self.analysis.MAX_FINDINGS = 5000
        missing = self.craft_bad_encounters()
        tables = self.analysis._Tables(self.project)
        found = self.analysis.check_maps(self.project, [1], tables)
        self.assertTrue(found, "no finding at all for a missing troop")
        self.assertTrue(any(f.rule == "missing_troop" for f in found),
                        [f.rule for f in found])
        self.assertIn(str(missing), " ".join(f.message for f in found))

    def test_the_dimension_check_still_runs(self):
        """`check_maps` calls two sub-checks; stopping must not lose the other."""
        self.analysis.MAX_FINDINGS = 5000
        game_map = self.project.load_map(1)
        game_map.set("width", 999)          # disagrees with the tile table
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.project = Project(self.tmp / "project")
        tables = self.analysis._Tables(self.project)
        found = self.analysis.check_maps(self.project, [1], tables)
        self.assertTrue(any("999" in f.message for f in found),
                        [f.message for f in found])


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
        cmd = RubyObject("RPG::EventCommand")
        cmd.set("code", code)
        cmd.set("indent", 0)
        cmd.set("parameters", parameters)
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
        event = RubyObject("RPG::Event")
        event.set("id", 1)
        event.set("name", self.project.codec.make_string("E"))
        event.set("x", 0)
        event.set("y", 0)
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


class TestASkippedPageSlotIsStillCountedForReading(ToolTest):
    """Round 7 made every appended row count. Not every examined slot.

    `map_get`'s summary and `event_get`'s page list both test their ceiling
    at the top of the pages loop and then `continue` past a slot that is not
    an RPG::Event::Page -- so the ceiling is reached only through the *outer*
    per-event charge, and the inner loop runs to completion once per event.
    A crafted map is a hash of 2,000 links to one event whose @pages is a
    long Array of integers: small on disk, a product to walk.
    """

    EVENTS, SLOTS = 200, 20_000

    def craft(self, pages):
        game_map = self.project.load_map(1)
        event = RubyObject("RPG::Event")
        event.set("id", 1)
        event.set("name", self.project.codec.make_string("E"))
        event.set("x", 0)
        event.set("y", 0)
        event.set("pages", pages)
        events = RubyHash()
        for event_id in range(1, self.EVENTS + 1):
            events.set(event_id, event)
        game_map.set("events", events)
        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(marshal_rb.dump(game_map))
        # The shape only matters because the file is small.
        self.assertLess(path.stat().st_size, 256 * 1024)
        self.ctx = self.reopen()

    def test_the_map_summary_counts_a_slot_it_skips(self):
        """`_event_summaries` is asked how many rows it charged for."""
        from rpgvxace_mcp.tools import map_read_tools
        self.craft([7] * self.SLOTS)
        game_map = self.project.load_map(1)
        rows, cut_short = map_read_tools._event_summaries(self.ctx, game_map)
        self.assertTrue(cut_short,
                        "a map of %d events x %d non-page slots reported no "
                        "truncation" % (self.EVENTS, self.SLOTS))
        self.assertLessEqual(
            len(rows), map_read_tools.MAX_EVENT_SUMMARY_ROWS,
            "the summary collected more rows than its own ceiling")

    def test_the_map_summary_counts_an_event_slot_it_skips(self):
        """A hash of values that are not events at all.

        The per-page charge cannot reach these -- there are no pages -- so
        this is the one shape only the outer charge answers. With real
        events in the hash both charges fire and either alone suffices,
        which is why the guard on the outer one read VACUOUS.
        """
        from rpgvxace_mcp.tools import map_read_tools
        events = RubyHash()
        for event_id in range(1,
                              map_read_tools.MAX_EVENT_SUMMARY_ROWS + 500):
            events.set(event_id, 42)
        game_map = self.project.load_map(1)
        game_map.set("events", events)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()
        rows, cut_short = map_read_tools._event_summaries(
            self.ctx, self.ctx.project.load_map(1))
        self.assertTrue(cut_short,
                        "a hash of %d non-event values was walked free"
                        % (map_read_tools.MAX_EVENT_SUMMARY_ROWS + 499))

    def test_the_event_page_list_charges_for_a_slot_it_skips(self):
        """`event_get`'s loop builds its own budget, so ask the reply.

        200 x 20,000 slots is twenty times MAX_WALK_STEPS, and 200 events on
        their own is a two-hundredth of it -- so `command_walk_stopped` is
        present exactly when the inner slots were charged for.
        """
        self.craft([7] * self.SLOTS)
        result = self.call("event_get", map_id=1)
        self.assertIn("command_walk_stopped", result,
                      "%d events x %d non-page slots were walked free"
                      % (self.EVENTS, self.SLOTS))

    def test_naming_one_page_does_not_make_the_rest_free(self):
        """`page=0` puts the `continue` above the charge for every other slot."""
        self.craft([7] * self.SLOTS)
        result = self.call("event_get", map_id=1, page=0)
        self.assertIn("command_walk_stopped", result,
                      "naming one page walked %d x %d slots free"
                      % (self.EVENTS, self.SLOTS))


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


class TestEveryScopeSpendsTheSameAllowance(ToolTest):
    """`validate` built its walk budget after two of its four scopes ran.

    `walk_budget = WalkBudget()` sits below the `"maps"` branch, so the map
    tree, the map properties, the encounters and the tilesets walked with no
    allowance at all -- and `"database"` was never given it either. Both are
    row x child products out of a file: a Tilesets array of a million links
    to one row whose @tileset_names is a shared million-entry Array is 10^12
    iterations, and `MAX_FINDINGS` cannot bound it because `_AssetChecker`
    returns before it counts anything when a sheet name is empty.
    """

    ROWS, CHILDREN = 400, 4000

    def db_bytes(self, table, value):
        self.project.path_for(self.project.filename_for(table)).write_bytes(
            marshal_rb.dump(value))
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)

    def shared_tileset(self):
        """One row, its sheet-name list shared, every slot an empty name."""
        tileset = RubyObject("RPG::Tileset")
        tileset.set("id", 1)
        tileset.set("name", self.project.codec.make_string("T"))
        tileset.set("mode", 1)
        # Empty names: `_AssetChecker.check` returns before it counts one, so
        # the collector ceiling never fills and cannot bound this.
        tileset.set("tileset_names",
                    [self.project.codec.make_string("")] * self.CHILDREN)
        return tileset

    def test_the_maps_scope_is_given_an_allowance(self):
        """Sized so only the per-slot charge can spend it.

        400 rows against a limit of 2,000 leaves the per-row charge unable
        to exhaust it; 400 x 4,000 slots exhausts it many times over. With a
        small allowance the per-row charge answered on its own, and the
        guard on the inner one read VACUOUS.
        """
        from rpgvxace_mcp import analysis
        self.db_bytes("tilesets", [None] + [self.shared_tileset()] * self.ROWS)
        budget = analysis.WalkBudget(limit=2000)
        analysis.check_tilesets(self.project, [1],
                                analysis._Tables(self.project), budget)
        self.assertTrue(budget.exhausted,
                        "%d tileset rows x %d sheet slots were walked free"
                        % (self.ROWS, self.CHILDREN))

    def test_the_maps_scope_says_it_stopped(self):
        """A walk that stopped and did not say so is the worse half."""
        self.db_bytes("tilesets", [None] + [self.shared_tileset()] * self.ROWS)
        out = self.call("validate", scopes=["maps"], map_ids=[1])
        self.assertIn("command_walk_stopped", out,
                      "the maps scope stopped early and reported a clean "
                      "bill: %s" % sorted(out))

    def test_the_database_scope_is_given_an_allowance(self):
        from rpgvxace_mcp import analysis
        budget = analysis.WalkBudget(limit=0)
        analysis.check_database(self.project,
                                analysis._Tables(self.project), budget)
        self.assertTrue(budget.exhausted,
                        "the database scope walked with no allowance spent")

    def test_a_parent_chain_stops_walking_as_well_as_collecting(self):
        """`_Findings.add` already caps what is *kept*; this caps the walking.

        Its own docstring says so -- "a caller that forgets `room` still
        cannot exceed the ceiling" -- so a test that counts findings passes
        with the loop guard gone, which is why that guard read VACUOUS. The
        budget is the observable half: one MapInfos that is a single cycle
        through every entry reaches the chain loop once, and without a charge
        per link the walk is free.
        """
        from rpgvxace_mcp import analysis
        infos = RubyHash()
        total = 5000
        for map_id in range(1, total + 1):
            info = RubyObject("RPG::MapInfo")
            info.set("name", self.project.codec.make_string("M%d" % map_id))
            info.set("parent_id", map_id + 1 if map_id < total else 1)
            infos.set(map_id, info)
        # `_parent_cycles` directly, with a collector of its own. Through
        # `check_map_tree` the outer loop reports every map that has no file
        # on disk, which fills the collector to MAX_FINDINGS before the
        # chain loop is reached -- so the test would pass on a walk that
        # never happened.
        budget = analysis.WalkBudget(limit=100)
        analysis._parent_cycles(dict(infos.items()), analysis._Findings(),
                                budget)
        self.assertTrue(budget.exhausted,
                        "a %d-entry cycle was walked free" % total)

    def test_a_parent_chain_cannot_collect_past_the_ceiling(self):
        """The ceiling was on the outer loop; one long cycle is one pass."""
        from rpgvxace_mcp import analysis
        infos = RubyHash()
        total = MAX_FINDINGS + 200
        for map_id in range(1, total + 1):
            info = RubyObject("RPG::MapInfo")
            info.set("name", self.project.codec.make_string("M%d" % map_id))
            # Every map's parent is the next one, and the last points back:
            # one cycle through every entry, reached in one outer iteration.
            info.set("parent_id", map_id + 1 if map_id < total else 1)
            infos.set(map_id, info)
        self.db_bytes("map_infos", infos)
        findings = analysis.check_map_tree(self.project)
        self.assertLessEqual(len(findings), MAX_FINDINGS,
                             "a %d-entry cycle collected %d findings against "
                             "a ceiling of %d"
                             % (total, len(findings), MAX_FINDINGS))

    def test_an_ordinary_project_still_reports_its_real_findings(self):
        """The charges must not make a real project look crafted."""
        out = self.call("validate", scopes=["maps", "database"])
        self.assertNotIn("command_walk_stopped", out)


class TestOneContainerSetForEveryWalk(ToolTest):
    """Five walks encoded "which wrappers hold other values" and disagreed.

    `collect_strings` descended into three kinds where `schema._observe`
    walks eight, so encoding detection never saw a string inside a
    `RubyIvars` -- and `analysis`' own comment records that this codec really
    does wrap values in `RubyIvars` in files the editor writes.
    """

    def test_a_string_inside_an_ivar_wrapper_is_found(self):
        from rpgvxace_mcp.project import collect_strings
        from rpgvxace_mcp.rubytypes import RubyIvars
        wrapped = RubyIvars([self.project.codec.make_string("Kiryu")],
                            {"@a": 1})
        out = []
        collect_strings(wrapped, out)
        self.assertIn(b"Kiryu", out,
                      "a string inside a RubyIvars was invisible to "
                      "encoding detection")

    def test_a_string_inside_a_struct_is_found(self):
        from rpgvxace_mcp.project import collect_strings
        from rpgvxace_mcp.rubytypes import RubyStruct
        struct = RubyStruct("Point",
                            {"@name": self.project.codec.make_string("Kiryu")})
        out = []
        collect_strings(struct, out)
        self.assertIn(b"Kiryu", out)

    def test_a_hash_key_that_is_a_string_is_found(self):
        from rpgvxace_mcp.project import collect_strings
        pairs = RubyHash()
        pairs.set(self.project.codec.make_string("Kiryu"), 1)
        out = []
        collect_strings(pairs, out)
        self.assertIn(b"Kiryu", out)

    def test_the_two_walks_agree_on_what_holds_children(self):
        """The claim, not one case of it.

        Asked of every wrapper class this codec builds: if `ruby_children`
        finds nothing inside one, `_observe` must not either -- otherwise the
        two walks are back to disagreeing and only one of them says so.
        """
        from rpgvxace_mcp import rubytypes
        from rpgvxace_mcp.rubytypes import ruby_children
        from rpgvxace_mcp.schema import _observe
        marker = self.project.codec.make_string("Kiryu")
        cases = {
            "RubyObject": rubytypes.RubyObject("C", {"@a": marker}),
            "RubyStruct": rubytypes.RubyStruct("S", {"@a": marker}),
            "RubyIvars": rubytypes.RubyIvars([marker], {}),
            "RubyUserMarshal": rubytypes.RubyUserMarshal("U", marker),
            "RubyData": rubytypes.RubyData("D", marker),
            "RubyUClass": rubytypes.RubyUClass("C", [marker]),
            "RubyExtended": rubytypes.RubyExtended(["M"], [marker]),
        }
        disagree = []
        for name, value in cases.items():
            reachable = any(child is marker or
                            (isinstance(child, list) and marker in child)
                            for child in ruby_children(value))
            observed: dict = {}
            _observe(value, observed, 0, set(), [1000])
            if not reachable:
                disagree.append(name)
        self.assertEqual(disagree, [],
                         "ruby_children finds nothing inside these, so a "
                         "string in one is invisible to encoding detection")


class TestOneBatchGetsOneReferenceWalk(ToolTest):
    """A delete's reference scan ran once per op over unchanged lists.

    `find_references` returns early only on reaching its limit, so the
    ordinary case -- deleting an entry nothing points at -- is the one that
    costs a full traversal. The shared allowance capped the damage and left
    the batch reporting most of its scans as unfinished rather than making
    them cheap.
    """

    def deletes(self, count=None):
        """One delete per entry the fixture's items table really has.

        Taken from the table rather than a range, because a range past the
        end is refused and the refusal would make this suite pass without
        walking anything.
        """
        ids = [row["id"] for row in
               self.call("db_list", table="items")["entries"]]
        return [{"op": "delete", "id": i} for i in ids[:count or len(ids)]]

    def test_a_batch_of_deletes_answers_every_scan(self):
        ops = self.deletes()
        out = self.call("db_edit", table="items", dry_run=True, ops=ops)
        unfinished = [row for row in out["would_apply"]
                      if row.get("references_scan_stopped")]
        self.assertEqual(unfinished, [],
                         "%d of %d deletes reported their reference scan as "
                         "unfinished" % (len(unfinished), len(ops)))

    def test_every_delete_still_reports_its_own_references(self):
        out = self.call("db_edit", table="items", dry_run=True,
                        ops=self.deletes(2))
        for row in out["would_apply"]:
            self.assertIn("references_found", row)
            self.assertTrue(row["references_checked"])

    def test_the_bulk_scan_answers_each_id_separately(self):
        """One walk, two different answers -- which is the whole risk.

        A bulk pass that bucketed its hits wrongly would report the same
        count for every id in the batch, and every one would look
        plausible. So this adds a reference to item 2 and checks that item
        2's count moves by exactly one while item 1's does not.

        Against a baseline rather than against zero: the demo project
        already references item 1, so "nothing names item 1" was an
        assumption. And the version before that searched for a Change Items
        command, kept the first hit in a local, and never looked at it --
        so it asserted only that *some* count came back.
        """
        before = {row["id"]: row["references_found"] for row in self.call(
            "db_edit", table="items", dry_run=True,
            ops=[{"op": "delete", "id": 1},
                 {"op": "delete", "id": 2}])["would_apply"]}

        game_map = self.project.load_map(1)
        command = RubyObject("RPG::EventCommand")
        command.set("code", 126)          # Change Items
        command.set("indent", 0)
        command.set("parameters", [2, 0, 0, 1])
        page = game_map.get("events").get(1).get("pages")[0]
        page.set("list", list(page.get("list")) [:-1] + [command]
                 + [list(page.get("list"))[-1]])
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="a reference to item 2")
        self.ctx = self.reopen()

        out = self.call("db_edit", table="items", dry_run=True, ops=[
            {"op": "delete", "id": 1}, {"op": "delete", "id": 2}])
        answers = {row["id"]: row["references_found"]
                   for row in out["would_apply"]}
        self.assertEqual(answers[1], before[1],
                         "item 1 gained a reference it was not given: "
                         "%s -> %s" % (before[1], answers[1]))
        self.assertEqual(answers[2], before[2] + 1,
                         "item 2 was given one more reference and the batch "
                         "reports %s -> %s" % (before[2], answers[2]))


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


class TestOneIndexPerEventEditCall(ToolTest):
    """`RubyHash.get` is a linear scan and `MAX_OPS` multiplies it.

    The class docstring on `RubyHash` prescribes hoisting
    `dict(hash.items())` out of a loop of lookups, and no caller did it: a
    batch of two hundred ops against a crafted map's two and a half million
    pairs is 5x10^8 comparisons in one call this server cannot be
    interrupted out of.
    """

    def test_a_batch_of_reads_does_not_rescan_the_map(self):
        from rpgvxace_mcp.rubytypes import RubyHash as RH
        scans = []
        original = RH._find

        def counting(self, key):
            scans.append(key)
            return original(self, key)

        RH._find = counting
        try:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "update", "event_id": 1, "name": "R%d" % i}
                for i in range(20)])
        finally:
            RH._find = original
        # Twenty ops, and the index is built once from `items()`. A handful
        # of scans for the map's own fields is fine; twenty lookups of event
        # 1 is the defect.
        self.assertLess(scans.count(1), 20,
                        "event 1 was looked up by linear scan %d times for "
                        "%d ops" % (scans.count(1), 20))

    def test_an_event_created_in_a_batch_is_visible_to_a_later_op(self):
        """An index the create op forgets to update is worse than none."""
        out = self.call("event_edit", map_id=1, ops=[
            {"op": "create", "event_id": 40, "name": "New", "x": 1, "y": 1},
            {"op": "update", "event_id": 40, "name": "Renamed"}])
        self.assertEqual(len(out["applied"]), 2)

    def test_an_event_deleted_in_a_batch_is_gone_for_a_later_op(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, ops=[
                {"op": "delete", "event_id": 1},
                {"op": "update", "event_id": 1, "name": "Renamed"}])
        self.assertIn("no event 1", str(caught.exception))


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


class TestEveryLintWalkTakesAnAllowance(unittest.TestCase):
    """Every function in `lint` that loops over file data takes a budget.

    Three rounds have now fixed unbudgeted walks one at a time, found by
    reading: `_check_map_assets` and `check_tilesets` in round 7, the whole
    maps and database scopes in round 8, and `_check_autorun`,
    `_check_system` and `_check_database_assets` in round 9. The pattern is
    always the same -- a loop whose iterable comes out of a file and whose
    charge sits after an `isinstance` test, or is absent.

    `TestNoWalkCollectsPastTheCeiling` and `prove_guards` both work, and
    both enumerate walks *by name*, so neither can see a walk nobody
    thought to add. This asks the module instead: a function that loops and
    does not take a `budget` parameter is either listed below with a reason
    or a defect.
    """

    # Functions that loop and legitimately take no allowance. A reason
    # each, because this is the escape hatch and an escape hatch without a
    # reason is where unbudgeted walks come back. Every name is checked
    # against the module, so a rename cannot leave a stale excuse.
    NO_BUDGET_NEEDED = {
        "validate": "loops over the map ids the caller named, which "
                    "`check_args` bounds by the declared maxItems",
        "_check_references": "loops over the rules one command code has, "
                             "which is this server's own table",
        "_check_shadowing": "loops over one event's pages, which the "
                            "caller of `check_event_pages` has charged for",
    }

    def module(self):
        return (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp" / "analysis" / "lint.py")

    def looping_functions(self):
        """Every top-level function in `lint` with a loop in it."""
        import ast
        tree = ast.parse(self.module().read_text(encoding="utf-8"))
        out = {}
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if any(isinstance(inner, (ast.For, ast.While))
                   for inner in ast.walk(node)):
                out[node.name] = [a.arg for a in node.args.args]
        return out

    def test_every_looping_function_takes_a_budget_or_is_excused(self):
        unbudgeted = sorted(
            name for name, args in self.looping_functions().items()
            if "budget" not in args and name not in self.NO_BUDGET_NEEDED)
        self.assertEqual(
            unbudgeted, [],
            "these loop over data out of a file and cannot be stopped: "
            + ", ".join(unbudgeted))

    def test_no_excuse_outlives_its_function(self):
        """A reason for a function that was renamed away is a stale reason."""
        looping = set(self.looping_functions())
        stale = sorted(set(self.NO_BUDGET_NEEDED) - looping)
        self.assertEqual(stale, [],
                         "these are excused from taking a budget and no "
                         "longer loop: " + ", ".join(stale))

    def test_every_budget_taking_function_spends_it(self):
        """A parameter nothing reads is worse than no parameter.

        A `budget` argument that is accepted and never spent reads, at
        every call site, as a walk that is bounded.
        """
        import ast
        tree = ast.parse(self.module().read_text(encoding="utf-8"))
        idle = []
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            if "budget" not in [a.arg for a in node.args.args]:
                continue
            body = ast.unparse(node)
            if "budget.spend" not in body and "budget)" not in body:
                idle.append(node.name)
        self.assertEqual(idle, [],
                         "these take a budget and neither spend it nor pass "
                         "it on: " + ", ".join(idle))


class TestOneEventsIndexPerCall(ToolTest):
    """The index is built once per call, and the old test could not tell.

    `TestOneIndexPerEventEditCall` counted `RubyHash._find` calls, which the
    index removes whether it is built once or once per op -- so it passed
    while `_apply_event_op` constructed a fresh `_EventRequest` inside the
    per-op loop, leaving intact the `MAX_OPS` multiplier its own comment
    describes. `events.items()` copies every pair, so 200 ops over a
    crafted map's 2.5 million pairs was ~10^9 steps.

    This counts the copies instead, which only the hoist can bound.
    """

    def test_a_batch_builds_the_index_once(self):
        from rpgvxace_mcp.rubytypes import RubyHash as RH
        copies = []
        original = RH.items

        def counting(self):
            copies.append(1)
            return original(self)

        RH.items = counting
        try:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "update", "event_id": 1, "name": "R%d" % i}
                for i in range(8)])
        finally:
            RH.items = original
        self.assertLessEqual(copies.count(1), 2,
                             "eight ops copied the events hash %d times"
                             % copies.count(1))

    def test_an_event_created_in_a_batch_is_still_visible_later(self):
        """The index has to be maintained across ops, not merely built once."""
        out = self.call("event_edit", map_id=1, ops=[
            {"op": "create", "event_id": 41, "name": "New", "x": 1, "y": 1},
            {"op": "update", "event_id": 41, "name": "Renamed"}])
        self.assertEqual(len(out["applied"]), 2)

    def test_an_event_deleted_in_a_batch_is_gone_for_a_later_op(self):
        with self.assertRaises(ToolError):
            self.call("event_edit", map_id=1, ops=[
                {"op": "delete", "event_id": 1},
                {"op": "update", "event_id": 1, "name": "Renamed"}])


class TestATableCostsWhatItsCellsCost(ToolTest):
    """A Table cost the conversion budget one node however many cells it had.

    Which made it the one payload shape `MAX_JSON_NODES` could not see, in
    the round that resized that constant so a conversion cannot build more
    than the transport will carry. Measured: a map whose @encounter_list is
    250,000 links to one 256-cell Table is 0.51 MB on disk, and
    `map_get map_id=1` with no arguments built about a gigabyte of lists and
    threw it away at the transport.
    """

    def craft_shared_tables(self, count):
        game_map = self.project.load_map(1)
        shared = Table(16, 16, 1)
        game_map.set("encounter_list", [shared] * count)
        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(marshal_rb.dump(game_map))
        # Small on disk is the whole point.
        self.assertLess(path.stat().st_size, 2 * 1024 * 1024)
        self.ctx = self.reopen()

    def test_a_map_of_shared_tables_is_refused_rather_than_built(self):
        from rpgvxace_mcp.convert import ConversionError
        self.craft_shared_tables(250_000)
        with self.assertRaises((ConversionError, ToolError)) as caught:
            self.call("map_get", map_id=1)
        self.assertIn("read a smaller part", str(caught.exception).lower())

    def test_an_ordinary_map_still_reads(self):
        out = self.call("map_get", map_id=1)
        self.assertIn("properties", out)

    def test_an_omitted_table_says_so_in_the_shared_vocabulary(self):
        """`cells_truncated` and `cells_note`, not a `hint` of its own."""
        entry = self.call("db_get", table="tilesets" if self.ace else "actors",
                          id=1)["entries"][0]["entry"]
        big = next(v for v in entry.values()
                   if isinstance(v, dict) and "cells_omitted" in v)
        self.assertTrue(big["cells_truncated"])
        self.assertIn("map_tiles", big["cells_note"])


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


class TestOneCommandCannotOutrunTheDeadline(ToolTest):
    """`_command_texts` built every string before the matcher saw one.

    `MAX_SEARCH_SECONDS` is checked *between* matches, so this was the one
    decode on the search path the deadline could not see: one command whose
    @parameters is 30,000 links to a 30 KB string is ~150 KB of file and
    900 MB of `str`, built before a single comparison.
    """

    def craft(self, links, size):
        game_map = self.project.load_map(1)
        text = self.project.codec.make_string("z" * size)
        command = RubyObject("RPG::EventCommand")
        command.set("code", 401)
        command.set("indent", 0)
        command.set("parameters", [text] * links)
        page = game_map.get("events").get(1).get("pages")[0]
        page.set("list", [command] + list(page.get("list")))
        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(marshal_rb.dump(game_map))
        self.assertLess(path.stat().st_size, 512 * 1024)
        self.ctx = self.reopen()

    def test_one_command_is_bounded(self):
        import time
        self.craft(30_000, 30_000)
        start = time.perf_counter()
        self.call("search", query="not-present", scopes=["events"])
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0,
                        "one crafted command took %.1fs to search" % elapsed)

    def test_an_ordinary_command_is_still_searched(self):
        out = self.call("search", query="Potion", scopes=["events", "database"])
        self.assertIn("hits", out)

    def test_a_hit_is_sanitised(self):
        """Text out of a file, into a reply a terminal renders."""
        game_map = self.project.load_map(1)
        command = RubyObject("RPG::EventCommand")
        command.set("code", 401)
        command.set("indent", 0)
        command.set("parameters", [self.project.codec.make_string(
            "find" + chr(27) + "[31m")])
        page = game_map.get("events").get(1).get("pages")[0]
        page.set("list", [command] + list(page.get("list")))
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()
        out = self.call("search", query="find", scopes=["events"])
        self.assertTrue(out["hits"], "the crafted line should be found")
        self.assertNotIn(chr(27), out["hits"][0]["text"])


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
