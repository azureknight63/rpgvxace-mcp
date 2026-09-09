"""Guards on how far one call may walk: every budget and allowance.

The recurring defect has two shapes and both have appeared more
than once: a bound that counts nodes where the cost is bytes, and a
charge placed after an `isinstance` test so that the slot it skips
is free. A file whose objects share children can describe millions
of nodes in a few kilobytes, and this server is single-threaded
over stdio -- so an unbounded walk is a session the user cannot
interrupt.
"""

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))


from rpgvxace_mcp import convert, marshal_rb  # noqa: E402
from rpgvxace_mcp.analysis import MAX_FINDINGS  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
    Table,
)
from rpgvxace_mcp.convert import ConversionError  # noqa: E402
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    ToolError,
)
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
        self.assertTrue(out.get("findings_truncated") or "command_walk_stopped" in out,
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


# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so running this file directly
# called `unittest.main()` (which exits) before the classes below it
# existed, and reported success having skipped them. pytest collects
# either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()


class TestOneCommandWalkForAWholeValidate(ToolTest):
    """`validate` reads each command list once, not once per rule that wants it.

    `check_commands` and `_check_command_assets` each iterated every command
    of every page, and both charged the same shared allowance -- the charge
    comes from the file, not from the rule, so the second walk cost exactly
    what the first did. Command walking was twice its minimum on the default
    run, whatever the project, and a caller near the ceiling was refused at
    half the real depth.

    The asset walk read a strict subset of what `check_commands` already had
    in locals: the code, the parameters and the location. So it is the same
    loop now, and the two rules that need it are two `if`s in it.

    Counted at `iter_command_lists`, which is the thing being paid for.
    Counting findings would not show this: both walks produce the right
    answers, which is why three rounds of reading did not find it.
    """

    def walks(self, **arguments):
        """How many command-list traversals one validate makes."""
        from rpgvxace_mcp.analysis import walk as walk_module
        original = walk_module.iter_command_lists
        counted = []

        def counting(*args, **kwargs):
            counted.append(1)
            return original(*args, **kwargs)

        # Patched on every module that bound the name, for the reason
        # `lower_the_finding_ceiling` exists: `from .walk import
        # iter_command_lists` binds it in the importer, so patching only
        # `walk` leaves every real call reading the original.
        import sys
        patched = [module for name, module in sorted(sys.modules.items())
                   if name.startswith("rpgvxace_mcp")
                   and getattr(module, "iter_command_lists", None) is original]
        self.assertTrue(patched, "nothing bound the name, so this patched "
                                 "nothing and would count zero")
        for module in patched:
            self.addCleanup(setattr, module, "iter_command_lists", original)
            module.iter_command_lists = counting
        self.call("validate", **arguments)
        return len(counted)

    def test_a_default_validate_walks_the_commands_once(self):
        self.assertEqual(self.walks(), 1)

    def test_asking_for_assets_alone_still_walks_them(self):
        """The fallback: without the events scope there is no walk to join."""
        self.assertEqual(self.walks(scopes=["assets"]), 1)

    def test_asking_for_events_alone_walks_them_once(self):
        self.assertEqual(self.walks(scopes=["events"]), 1)

    def test_neither_scope_walks_no_commands(self):
        self.assertEqual(self.walks(scopes=["database"]), 0)


class TestTheMergedWalkStillFindsBothKinds(ValidateTest):
    """Sharing one loop must not lose either rule's findings.

    The asset checks moved into `check_commands`' loop, which is gated by
    `command_layouts_modelled` and by the finding ceiling in a different
    place than they were. Both rules are checked on a map crafted to break
    both at once, and in each scope on its own.
    """

    def craft_a_missing_face_and_a_bad_jump(self):
        _, _, event = self.first_event(1)
        page = event.get("pages")[0]
        text = RubyObject("RPG::EventCommand")
        text.set("code", 101)                 # Show Text: face graphic
        text.set("indent", 0)
        text.set("parameters",
                 [self.project.codec.make_string("NoSuchFace"), 0, 0, 2])
        jump = RubyObject("RPG::EventCommand")
        jump.set("code", 119)                 # Jump to Label, with no Label
        jump.set("indent", 0)
        jump.set("parameters", [self.project.codec.make_string("nowhere")])
        page.set("list", [text, jump])
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="crafted")
        self.ctx = self.reopen()

    def rules(self, **arguments):
        self.craft_a_missing_face_and_a_bad_jump()
        return {f["rule"] for f in self.findings(**arguments)}

    def test_a_default_run_reports_both(self):
        found = self.rules()
        self.assertIn("missing_graphic", found)
        self.assertIn("missing_label", found)

    def test_the_assets_scope_alone_reports_the_asset(self):
        self.assertIn("missing_graphic", self.rules(scopes=["assets"]))

    def test_the_events_scope_alone_reports_the_jump(self):
        found = self.rules(scopes=["events"])
        self.assertIn("missing_label", found)
        self.assertNotIn("missing_graphic", found,
                         "the events scope answered an asset question it "
                         "was not asked")


class TestALocationIsBuiltWhenItIsReported(ValidateTest):
    """`Location.at` runs per finding, not per command walked.

    `check_commands` opened its loop body with `here = location.at(index)`,
    unconditionally, and `here` is read only inside a `Finding(...)`. On the
    developer's real 39-map project that was 9,819 copies for two findings
    -- one allocation and, because `at` copied slot by slot through
    `getattr`/`setattr`, twenty-six named attribute operations each, better
    than 99.9% of them discarded.

    Measured at 1.9% of a real validate, which is small; the reason to fix
    it anyway is that it scales with commands walked, so the worst case is
    the whole 200,000-step allowance turned into 200,000 dead objects, and
    this server is single-threaded over stdio.

    Counted rather than timed: a timing test on a fraction this size would
    be a race.
    """

    COMMANDS = 400

    def craft_a_long_clean_page(self):
        """One page of commands that break no rule at all."""
        _, _, event = self.first_event(1)
        page = event.get("pages")[0]
        comment = RubyObject("RPG::EventCommand")
        comment.set("code", 108)               # Comment: names nothing
        comment.set("indent", 0)
        comment.set("parameters", [self.project.codec.make_string("x")])
        page.set("list", [comment] * self.COMMANDS)
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="a clean page")
        self.ctx = self.reopen()

    def copies(self, **arguments):
        from rpgvxace_mcp.analysis.walk import Location
        original = Location.at
        counted = []

        def counting(self, index):
            counted.append(1)
            return original(self, index)

        self.addCleanup(setattr, Location, "at", original)
        Location.at = counting
        out = self.call("validate", **arguments)
        return len(counted), out

    def test_a_clean_page_builds_almost_no_locations(self):
        self.craft_a_long_clean_page()
        copies, out = self.copies()
        total = sum(out["counts"].values())
        self.assertLess(
            copies, self.COMMANDS,
            "%d locations built for %d commands and %d findings, so one is "
            "being built per command walked" % (copies, self.COMMANDS, total))

    def test_a_finding_still_says_where_it_is(self):
        """The point of the location, on the page that has one."""
        _, _, event = self.first_event(1)
        page = event.get("pages")[0]
        jump = RubyObject("RPG::EventCommand")
        jump.set("code", 119)                  # Jump to Label, with no Label
        jump.set("indent", 0)
        jump.set("parameters", [self.project.codec.make_string("nowhere")])
        page.set("list", [jump])
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="a bad jump")
        self.ctx = self.reopen()
        found = [f for f in self.call("validate")["findings"]
                 if f["rule"] == "missing_label"]
        self.assertTrue(found, "the rule stopped reporting")
        self.assertIn("command 0", found[0]["where"])
        self.assertIn("event", found[0]["where"])


class TestACopiedLocationCarriesEveryField(unittest.TestCase):
    """`at` copies every field, checked rather than looped.

    It used to copy through `for name in COPIED: setattr(...)`, which is
    safe and is also 26 named attribute operations per call. Written out
    now, and this is what keeps that safe: two earlier versions of `at`
    built the copy positionally and silently dropped `table` and `field`,
    which is a finding that names the wrong place.
    """

    def test_every_copied_field_survives(self):
        from rpgvxace_mcp.analysis.walk import Location
        values = {name: "v-%s" % name for name in Location.COPIED}
        here = Location("db", **values)
        clone = here.at(7)
        self.assertEqual(clone.kind, "db")
        self.assertEqual(clone.index, 7)
        for name in Location.COPIED:
            if name == "index":
                continue
            with self.subTest(field=name):
                self.assertEqual(getattr(clone, name), values[name],
                                 "%s was dropped by the copy" % name)

    def test_the_copy_sets_nothing_outside_the_slots(self):
        """So a field added to `__slots__` and not to `at` cannot pass."""
        from rpgvxace_mcp.analysis.walk import Location
        here = Location("db", table="items", entry_id=3)
        clone = here.at(1)
        for name in Location.__slots__:
            with self.subTest(field=name):
                # Every slot is assigned: an unassigned one raises here.
                getattr(clone, name)


class TestASetResolvesItsPathOnce(ToolTest):
    """`raw_data op="set"` reaches the value through the parent it has.

    It resolved the parent, then resolved the whole path again for the value
    -- re-walking every segment the parent had already reached. The segments
    are cheap; the charge is not. Each hash segment charges the shared
    allowance `len(hash)`, so `events/1/pages/0/...` charged the events hash
    twice, and a caller near the ceiling was refused at half the real depth
    of the path it asked for.

    `existing` is `parent[key]`, which is one segment from a value already in
    hand.

    Counted at `WalkBudget.spend`, which is the thing being charged. The old
    code produced the right value, which is why reading found this and tests
    did not.
    """

    def charged(self, **arguments):
        from rpgvxace_mcp.analysis import walk as walk_module
        original = walk_module.WalkBudget.spend
        spent = []

        def counting(inner, nodes=1):
            spent.append(nodes)
            return original(inner, nodes)

        self.addCleanup(setattr, walk_module.WalkBudget, "spend", original)
        walk_module.WalkBudget.spend = counting
        self.call("raw_data", **arguments)
        return sum(spent)

    def test_a_set_charges_its_prefix_once(self):
        """The same path, read and written, costs the same to reach."""
        # `map_infos` is a RubyHash keyed by map id, so the first
        # segment is a hash scan -- which is the segment that was charged
        # twice.
        path = "1/order"
        read = self.charged(table="map_infos", path=path)
        written = self.charged(table="map_infos", path=path, op="set",
                               value={"value": 3}, dry_run=True)
        self.assertLessEqual(
            written, read,
            "a set charged %d where the matching get charged %d, so it is "
            "walking the path twice" % (written, read))

    def test_a_set_still_says_where_a_bad_key_is(self):
        """The trail in the message names the segments already walked."""
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", table="map_infos", op="set",
                      path="1/nosuchfield", value={"value": 1})
        message = str(caught.exception)
        self.assertIn("nosuchfield", message)
        self.assertIn("1", message,
                      "the message lost the path it had reached: %s" % message)

    def test_a_set_still_sets(self):
        self.call("raw_data", table="map_infos", op="set",
                  path="1/order", value={"value": 4})
        out = self.call("raw_data", table="map_infos", path="1/order")
        self.assertEqual(out["value"], 4)
