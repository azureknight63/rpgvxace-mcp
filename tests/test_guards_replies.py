"""Guards on the honesty of an answer.

An empty result means nothing without knowing where the search went, a
count named `total` reads as the size of a table it was not, and a name out
of a file this server did not write goes into a reply a terminal renders.
Every rule here is about the difference between "we found nothing" and "we
did not look".
"""

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from build_project import build  # noqa: E402

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.analysis import (  # noqa: E402
    REFERENCE_PREVIEW,
    validate as run_validate,
)
from rpgvxace_mcp.project import (  # noqa: E402
    Project,
)
from rpgvxace_mcp.schema import new_event  # noqa: E402
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
)
from rpgvxace_mcp.tools.search_tools import (  # noqa: E402
    MAX_SUBJECT_BYTES,
    MAX_SUBJECT_CHARS,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    PARTIAL_SUFFIXES,
    REGISTRY,
    ToolError,
)

# The tool modules this file names directly. Registration -- the
# side effect of importing them -- is `tool_case`'s job; these are
# here because the tests below read their op tables.
from rpgvxace_mcp.tools import (  # noqa: E402
    event_tools,
    map_read_tools,
)

from tool_case import (  # noqa: E402
    ToolTest as _ToolTest,
    ValidateTest as _ValidateTest,
)


class _BuildOnFirstUse:
    """Defer the demo-project build until a test first touches the project.

    `ToolTest.setUp` builds and opens a whole project for every method, and
    a good many methods here only parse `src` or compare two tables and never
    read it. `tmp`, `project` and `ctx` are what `setUp` creates, so each is a
    property that runs the real `setUp` on first read or write -- a test that
    uses the project gets exactly the fixture it always got, and one that does
    not no longer pays for it. Writes build first too, so an early assignment
    cannot be overwritten by a later build.

    A class may also set `shared_fixture = True` when none of its tests
    writes: it then gets one project for the class instead of one per
    method. That is only safe while the claim is true, so it is enforced
    rather than trusted -- every file's size and mtime is recorded when
    the project is built and compared after each test, and a test that
    changed anything fails by name.
    """

    _built = False
    shared_fixture = False

    @staticmethod
    def _signature(root):
        return sorted((str(path.relative_to(root)), path.stat().st_size,
                       path.stat().st_mtime_ns)
                      for path in root.rglob("*") if path.is_file())

    @classmethod
    def _shared(cls):
        """The class's one project, built by the real `setUp` on first use."""
        if "_shared_fixture" not in cls.__dict__:
            holder = _ToolTest("setUp")
            holder.engine, holder.read_only = cls.engine, cls.read_only
            holder.setUp()
            cls.addClassCleanup(holder.doCleanups)
            cls._shared_fixture = (
                holder, cls._signature(holder.tmp / "project"))
        return cls._shared_fixture

    def _assert_unchanged(self, holder, before):
        self.assertEqual(
            self._signature(holder.tmp / "project"), before,
            "%s sets shared_fixture and a test wrote to the project it "
            "shares" % type(self).__name__)

    def setUp(self):
        self._built = False

    def _ensure_built(self):
        if self._built:
            return
        self._built = True
        if not self.shared_fixture:
            super().setUp()
            return
        holder, before = self._shared()
        self.addCleanup(self._assert_unchanged, holder, before)
        self.__dict__.update(_tmp=holder.tmp, _project=holder.project,
                             _ctx=holder.ctx)

    @property
    def tmp(self):
        self._ensure_built()
        return self.__dict__["_tmp"]

    @tmp.setter
    def tmp(self, value):
        self._ensure_built()
        self.__dict__["_tmp"] = value

    @property
    def project(self):
        self._ensure_built()
        return self.__dict__["_project"]

    @project.setter
    def project(self, value):
        self._ensure_built()
        self.__dict__["_project"] = value

    @property
    def ctx(self):
        self._ensure_built()
        return self.__dict__["_ctx"]

    @ctx.setter
    def ctx(self, value):
        self._ensure_built()
        self.__dict__["_ctx"] = value


class ToolTest(_BuildOnFirstUse, _ToolTest):
    pass


class ValidateTest(_BuildOnFirstUse, _ValidateTest):
    pass


class TestRtpOverrideIsHonest(unittest.TestCase):
    def test_an_explicit_rtp_does_not_claim_every_rtp_was_located(self):
        """--rtp answers for every declared RTP, so the report must not also
        claim each one was found where the engine installed it."""
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-rtp-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build(tmp / "project", "vx_ace")
        (tmp / "project" / "Game.ini").write_text(
            "[Game]\nRTP1=RPGVXAce\nRTP2=SomethingElse\n"
            "Library=System\\RGSS300.dll\nScripts=Data\\Scripts.rvdata2\n"
            "Title=Demo\n", encoding="utf-8")
        pool = tmp / "pool"
        (pool / "Graphics" / "Characters").mkdir(parents=True)
        project = Project(tmp / "project", rtp_paths=[str(pool)])
        note = project.describe()["rtp"]["note"]
        self.assertIn("--rtp", note)
        self.assertNotIn("All declared RTPs were located", note)


class TestValidateRespectsTheRtp(ValidateTest):
    """An unlocatable RTP means a missing file is a guess, not a finding.

    This is the guard that removed 185 false warnings, and one rule was
    bypassing it: check_tilesets had its own inline comparison, so a project
    whose RTP could not be found got up to nine spurious warnings per tileset
    for the default sheets that live in the RTP -- and they outranked the
    downgraded ones, crowding real findings out of the limit.
    """

    def setUp(self):
        super().setUp()
        if not self.ace:
            self.skipTest("tilesets are a VX Ace database")

    def test_a_tileset_sheet_miss_is_downgraded_with_an_unresolved_rtp(self):
        self.call("db_edit", table="tilesets", ops=[{
            "op": "update", "id": 1,
            "patch": {"tileset_names": ["RtpOnlySheet", "", "", "", "", "", "", "", ""]}}])
        self.declare_missing_rtp()
        found = self.findings("missing_graphic", scopes=["maps"])
        self.assertTrue(found, "the sheet is genuinely absent locally")
        for finding in found:
            self.assertEqual(finding["severity"], "info")
            self.assertIn("NoSuchRtpAnywhere", finding["message"])

    def test_a_tileset_sheet_miss_is_a_warning_when_the_pool_is_complete(self):
        self.call("db_edit", table="tilesets", ops=[{
            "op": "update", "id": 1,
            "patch": {"tileset_names": ["NotThere", "", "", "", "", "", "", "", ""]}}])
        found = self.findings("missing_graphic", scopes=["maps"])
        self.assertTrue(found)
        self.assertEqual(found[0]["severity"], "warning")
        self.assertIn("sheet A1", found[0]["message"])

    def test_a_tileset_sheet_case_mismatch_is_reported_as_one(self):
        """Certain regardless of the RTP: the file is there, spelled otherwise."""
        pool = sorted(self.project.asset_names("tilesets"))
        self.assertTrue(pool, "the fixture ships tileset graphics")
        self.call("db_edit", table="tilesets", ops=[{
            "op": "update", "id": 1,
            "patch": {"tileset_names": [pool[0].upper() if pool[0].islower()
                                        else pool[0].lower(),
                                        "", "", "", "", "", "", "", ""]}}])
        found = self.findings("asset_case_mismatch", scopes=["maps"])
        self.assertTrue(found)
        self.assertIn("sheet A1", found[0]["message"])


class TestValidateTellsUnreadableFromEmpty(ValidateTest):
    def test_an_unreadable_table_is_one_finding_not_a_dangling_reference_each(self):
        """A table that will not load is not an empty table.

        The editor rewrites every data file when it saves, so catching one
        half-written is ordinary -- and treating it as empty made every
        reference into it an error, which is the loudest possible way to be
        wrong.
        """
        self.corrupt("Troops")
        rows = self.call("validate", scopes=["maps", "events", "database"])
        rules = [f["rule"] for f in rows["findings"]]
        self.assertEqual(rules.count("table_unreadable"), 1, rules)
        self.assertNotIn("missing_troop", rules)
        unreadable = [f for f in rows["findings"]
                      if f["rule"] == "table_unreadable"][0]
        self.assertIn("troops", unreadable["message"])
        self.assertIn("validate again", unreadable["message"])


class TestValidatePageNumbersMatchTheTools(ValidateTest):
    def test_a_page_finding_names_the_page_the_tools_take(self):
        """The message and the location have to agree, and both have to be the
        0-based index event_edit accepts -- otherwise a model reading "page 2"
        edits the third page."""
        game_map, event_id, event = self.first_event()
        pages = event.get("pages")
        pages.append(marshal_rb.load(marshal_rb.dump(pages[0])))
        added = len(pages) - 1
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="second page")
        self.ctx = Context(Project(self.tmp / "project"))
        found = self.findings("shadowed_pages", scopes=["events"])
        self.assertTrue(found)
        page = found[0]["location"]["page"]
        self.assertEqual(page, added, "the page reported is the one that wins")
        self.assertIn("page %d of event" % page, found[0]["message"])
        self.assertIn("page %d" % page, found[0]["where"])


class TestValidateDistinguishesMissingFromBroken(ValidateTest):
    def test_a_transfer_to_an_unparseable_map_is_not_called_missing(self):
        """"Does not exist" would send an agent recreating a file that is
        already there. Existence is the file being present."""
        target = [m for m in self.project.map_ids_on_disk() if m != 1]
        self.assertTrue(target, "the fixture ships more than one map")
        target = target[0]
        game_map, event_id, event = self.first_event()
        commands = event.get("pages")[0].get("list")
        commands.insert(0, RubyObject("RPG::EventCommand", {
            "@code": 201, "@indent": 0,
            "@parameters": [0, target, 0, 0, 0, 0]}))
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="transfer")
        self.corrupt("Map%03d" % target)
        self.ctx = Context(Project(self.tmp / "project"))
        rows = self.call("validate", scopes=["maps", "events"])
        rules = [f["rule"] for f in rows["findings"]]
        self.assertNotIn("transfer_to_missing_map", rules)
        self.assertIn("map_unreadable", rules)


class TestValidateScoping(ValidateTest):
    def test_repeated_map_ids_are_counted_once(self):
        rows = self.call("validate", scopes=["maps"], map_ids=[1, 1, 1, 1])
        self.assertEqual(rows["maps_examined"], 1)

    def test_a_map_id_that_is_not_there_is_reported_not_examined(self):
        rows = self.call("validate", scopes=["maps"], map_ids=[1, 4321])
        self.assertEqual(rows["maps_examined"], 1)
        self.assertIn("no_such_map", [f["rule"] for f in rows["findings"]])

    def test_a_negative_limit_is_refused_at_the_boundary(self):
        """The schema declares minimum 1, and that is now enforced."""
        with self.assertRaises(ToolError) as caught:
            self.call("validate", scopes=["database"], limit=-5)
        self.assertIn("at least 1", str(caught.exception))

    def test_a_negative_limit_does_not_drop_findings(self):
        """The clamp inside validate is the backstop for any other caller:
        a negative slice would take findings from the wrong end."""
        self.corrupt("Troops")
        rows = run_validate(self.project, ["database"], limit=-5)
        self.assertTrue(rows["findings"], "a negative limit sliced the list")


class TestValidateChecksTheStartPosition(ValidateTest):
    def test_a_start_position_off_the_start_map_is_reported(self):
        """The one transfer the engine performs before the player can do
        anything about it, checked the same way every other transfer is."""
        game_map = self.project.load_map(1)
        width, height = game_map.get("width"), game_map.get("height")
        self.call("raw_data", op="set", table="system", path="start_map_id",
                  value={"value": 1})
        self.call("raw_data", op="set", table="system", path="start_x",
                  value={"value": width + 5})
        found = self.findings("start_position_outside_map", scopes=["database"])
        self.assertTrue(found)
        self.assertIn("%sx%s" % (width, height), found[0]["message"])
        self.assertIn("boots with the party off the map", found[0]["message"])

    def test_a_start_position_on_the_map_is_not_reported(self):
        self.assertFalse(self.findings("start_position_outside_map",
                                       scopes=["database"]))


class TestSearchReportsWhatItSearched(ToolTest):

    shared_fixture = True
    def test_a_command_code_search_does_not_claim_to_have_searched_text(self):
        """The other scopes hold no commands, so they are skipped -- and
        reporting them as searched told the model they had come back empty."""
        result = self.call("search", command_code=101, scopes=["events", "database"])
        self.assertEqual(result["scopes_searched"], ["events"])

    def test_an_unknown_scope_is_refused_not_ignored(self):
        with self.assertRaises(ToolError) as caught:
            self.call("search", query="x", scopes=["scrpits"])
        self.assertIn("choose from", str(caught.exception))

    def test_a_huge_pattern_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("search", query="a" * 1000, regex=True)
        self.assertIn("characters", str(caught.exception))


class TestSearchDoesNotClaimToHaveRead(ValidateTest):
    def test_a_table_that_would_not_load_is_reported_not_counted(self):
        """Recording a scope before reading it meant an unreadable file came
        back as "searched, nothing found" -- the same lie as reporting a scope
        that was skipped."""
        self.corrupt("Troops")
        self.ctx = self.reopen()
        result = self.call("search", query="zzz", scopes=["database"])
        self.assertIn("troops", result["files_not_read"])
        # The scope still counts as searched, because the other tables read.
        self.assertIn("database", result["scopes_searched"])

    def test_a_scope_whose_only_file_fails_is_not_called_searched(self):
        self.corrupt("MapInfos")
        # Reopened, because setUp already read MapInfos into the cache and a
        # cached copy is still perfectly readable.
        self.ctx = self.reopen()
        result = self.call("search", query="zzz", scopes=["maps"])
        self.assertNotIn("maps", result["scopes_searched"])
        self.assertIn("map_infos", result["files_not_read"])


class TestScriptHitsSayWhere(ToolTest):
    def test_a_script_hit_names_its_slot_and_line(self):
        """Location had no "script" branch, so every script hit's `where` was
        the bare word "script"."""
        result = self.call("search", query="def ", scopes=["scripts"])
        self.assertTrue(result["hits"])
        where = result["hits"][0]["where"]
        self.assertIn("script ", where)
        self.assertIn("line ", where)


class TestFindingCountsAreTrustworthy(ValidateTest):
    def test_counts_survive_truncation(self):
        """Truncating by arrival order before counting meant a flood of
        warnings from one check could push out a later check's errors, and
        counts then reported zero errors while errors existed."""
        self.corrupt("Troops")
        self.ctx = self.reopen()
        # An unreadable table (error) and a map id that is not there
        # (warning), so there is more than one finding and they differ in
        # severity.
        rows = self.call("validate", scopes=["maps", "events", "database"],
                         map_ids=[1, 4321], limit=1)
        self.assertEqual(len(rows["findings"]), 1)
        # The one returned is the most severe of the two.
        self.assertEqual(rows["findings"][0]["severity"], "error")
        # And the tally counts both, not just the row that came back.
        self.assertGreaterEqual(rows["counts"]["error"], 1)
        self.assertGreaterEqual(rows["counts"]["warning"], 1)
        self.assertGreater(sum(rows["counts"].values()), len(rows["findings"]))


class TestSearchSaysWhatItDidNotRead(ToolTest):
    """Both searches read a bounded prefix; a caller must be told which.

    "No hits" from a regex over a project with long script lines was
    indistinguishable from a search that had genuinely looked everywhere.
    The two windows are different sizes and are measured in different
    units -- MAX_SUBJECT_CHARS characters of a decoded string for a regex,
    MAX_SUBJECT_BYTES bytes of the raw string for a plain search -- and
    this class's own docstring used to say a plain search read every
    string in full, which was true of the version before `subject_text`
    sliced.
    """

    def long_note(self):
        """Put a needle past the prefix a regex will look at."""
        item = self.project.get_entry("items", 1)
        padding = "x" * (MAX_SUBJECT_CHARS + 500)
        item.set("note", self.project.codec.make_string(padding + "NEEDLE"))
        self.project.save_file(self.project.filename_for("items"),
                               tool="test", summary="long note")

    def test_a_clipped_regex_search_says_so(self):
        self.long_note()
        out = self.call("search", query="NEEDLE", regex=True,
                        scopes=["database"])
        self.assertEqual(out["hits"], [])
        self.assertIn("strings_clipped_note", out)
        # Comma-formatted, like every other bound this server reports.
        self.assertIn("{:,}".format(MAX_SUBJECT_CHARS),
                      out["strings_clipped_note"])

    def test_a_substring_search_reads_past_the_regex_prefix(self):
        """Past the regex window, not past its own.

        This asserted a clean reply for a needle 2,500 bytes in and was
        named for reading the whole string -- which it does not do, and
        the case below is what tells the two claims apart.
        """
        self.long_note()
        out = self.call("search", query="NEEDLE", scopes=["database"])
        self.assertEqual(out["count"], 1)
        # Inside the plain window, so nothing is claimed to have been cut.
        self.assertNotIn("strings_clipped_note", out)

    def test_a_substring_search_past_its_own_window_says_so(self):
        item = self.project.get_entry("items", 1)
        padding = "x" * (MAX_SUBJECT_BYTES + 500)
        item.set("note", self.project.codec.make_string(padding + "NEEDLE"))
        self.project.save_file(self.project.filename_for("items"),
                               tool="test", summary="a very long note")
        out = self.call("search", query="NEEDLE", scopes=["database"])
        self.assertEqual(out["hits"], [])
        self.assertIn("strings_clipped_note", out)
        # The byte figure, because that is the cut this path made.
        self.assertIn("{:,}".format(MAX_SUBJECT_BYTES),
                      out["strings_clipped_note"])
        # And the flag the tool description says covers anything not read.
        self.assertTrue(out.get("may_have_more"))


class TestCommandReferenceSaysWhoseLayoutsTheseAre(ToolTest):
    """An unmodelled engine's layouts are Ace's, and must not read as its own.

    `command_table` and `param_specs` fall back to Ace for an engine this
    server does not model. Reading is still useful; presenting those layouts as
    confirmed is how a caller writes a raw command with the wrong parameter
    order, which the interpreter then misreads.
    """

    shared_fixture = True

    def test_a_modelled_engine_says_yes(self):
        out = self.call("command_reference", search="text")
        self.assertTrue(out["layouts_are_this_engines"])
        self.assertNotIn("engine_warning", out)

    def unmodelled(self):
        """The project reporting an engine the server has no profile for."""
        from unittest import mock
        patcher = mock.patch.object(self.ctx.project, "engine", "mv")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_unmodelled_engine_says_no_and_warns(self):
        """The other side of every test above, which only ever saw True.

        With the answer hard-coded to True this whole class stayed green.
        """
        self.unmodelled()
        out = self.call("command_reference", search="text")
        self.assertFalse(out["layouts_are_this_engines"])
        self.assertIn("mv", out["engine_warning"])

    def test_an_unmodelled_engine_says_no_for_one_code(self):
        self.unmodelled()
        out = self.call("command_reference", code=101)
        self.assertFalse(out["layouts_are_this_engines"])
        self.assertTrue(any("mv" in note for note in out["layout_notes"]),
                        "no note names the engine whose layout is borrowed: "
                        "%s" % out.get("layout_notes"))

    def test_an_unmodelled_engine_says_no_for_the_move_listing(self):
        self.unmodelled()
        out = self.call("command_reference", move_commands=True)
        self.assertFalse(out["layouts_are_this_engines"])

    def test_one_code_says_it_too(self):
        out = self.call("command_reference", code=101)
        self.assertTrue(out["layouts_are_this_engines"])

    def test_the_move_route_listing_says_it_too(self):
        """This branch returned before the engine was ever consulted.

        The tool description promises every reply carries the key, and the
        move-route listing was the one that did not -- so a model reading move
        codes on an unmodelled engine got no caveat while believing it had one.
        """
        out = self.call("command_reference", move_commands=True)
        self.assertTrue(out["layouts_are_this_engines"])
        self.assertTrue(out["move_commands"])

    def test_move_commands_refuses_the_arguments_it_ignores(self):
        """It reads neither `code` nor `search`, and used to accept both."""
        for extra in ({"code": 101}, {"search": "text"}):
            with self.subTest(extra=extra):
                with self.assertRaises(ToolError) as caught:
                    self.call("command_reference", move_commands=True, **extra)
                self.assertIn(sorted(extra)[0], str(caught.exception))


class TestReferenceCountsDoNotUnderstate(ToolTest):
    """A preview limit reported as a total is a count that misleads.

    find_references was asked for REFERENCE_PREVIEW hits and len() of the
    result was reported as the number of references, so an entry referenced
    three hundred times was said to be referenced twenty-five times -- and a
    caller could believe it had seen them all.
    """

    def flood_references(self, count):
        """Point `count` event commands at item 1."""
        commands = [{"raw": {"code": 126, "parameters": [1, 0, 0, 1]}}
                    for _ in range(count)]
        self.call("event_edit", map_id=1, ops=[
            {"op": "set_commands", "event_id": 1, "page": 0,
             "commands": commands}])

    def test_a_count_past_the_preview_says_at_least(self):
        self.flood_references(REFERENCE_PREVIEW + 10)
        out = self.call("db_edit", table="items", dry_run=True, ops=[
            {"op": "delete", "id": 1}])
        warning = " ".join(out["warnings"])
        self.assertIn("at least", warning)

    def test_a_count_inside_the_preview_is_exact(self):
        self.flood_references(3)
        out = self.call("db_edit", table="items", dry_run=True, ops=[
            {"op": "delete", "id": 1}])
        warning = " ".join(out["warnings"])
        # However many the demo project already had, a count inside the
        # preview is the real one and is not hedged.
        self.assertNotIn("at least", warning)
        self.assertIn("event command", warning)


class TestTileTallySaysWhatItLeftOut(ToolTest):
    """A tally that showed twelve ids promised "which tile ids appear"."""

    def test_it_reports_the_ids_it_did_not_list(self):
        # More distinct ids on one layer than the tally lists.
        rows = [[(x + y * 7) % 400 + 1 for x in range(7)] for y in range(7)]
        self.call("map_tiles", map_id=1, op="write", x=0, y=0, width=7,
                  height=7, z=0, rows=rows)
        counts = self.call("map_get", map_id=1,
                           tiles="counts")["tile_counts"]
        layer = counts["0"]
        self.assertGreater(layer["distinct_tile_ids"], map_read_tools.TOP_TILE_IDS)
        self.assertEqual(
            layer["tile_ids_not_shown"],
            layer["distinct_tile_ids"] - len(layer["most_common"]))
        self.assertGreater(layer["tile_ids_not_shown"], 0)


class TestMapReferenceCountsDoNotUnderstate(ToolTest):
    """The same preview-as-total that db_edit had, on the map path."""

    def test_a_count_past_the_preview_says_at_least(self):
        # More Transfer Player commands pointing at map 2 than the preview.
        transfers = [{"transfer": {"map_id": 2, "x": 0, "y": 0}}
                     for _ in range(REFERENCE_PREVIEW + 5)]
        self.call("event_edit", map_id=1, ops=[
            {"op": "set_commands", "event_id": 1, "page": 0,
             "commands": transfers}])
        # Map 2 has children in the demo project, so preview the delete.
        out = self.call("map_edit", op="delete", map_id=2, dry_run=True)
        self.assertTrue(out["incoming_transfers_truncated"])
        self.assertEqual(len(out["incoming_transfers"]),
                         REFERENCE_PREVIEW)


class TestAFilteredListSaysItWasFiltered(ToolTest):
    """A count of what matched must not read as a count of what exists.

    db_list renamed exactly this to `matching_entries` with a comment saying
    why; map_list kept `total`. And `map_files_without_tree_entry` was set to
    an empty list whenever a filter was given, without looking -- so a
    filtered call reported "no orphan map files" from a check that never ran,
    which is the "a zero would read as nothing looked" failure this codebase
    guards against everywhere else.
    """

    def test_the_count_names_what_it_counted(self):
        everything = self.call("map_list")
        filtered = self.call("map_list", parent_id=1)
        self.assertLess(len(filtered["maps"]), len(everything["maps"]))
        self.assertNotIn("total", filtered)
        self.assertEqual(filtered["matching_maps"], len(filtered["maps"]))

    def test_a_filter_does_not_claim_there_are_no_orphans(self):
        """The orphan check is one set difference over the whole tree.

        With a real orphan present, because comparing two empty lists passes
        whether the check ran or not.
        """
        # A map file with no tree entry: exactly what the key reports.
        self.call("map_edit", op="create", name="Ghost", map_id=9)
        infos = self.project.load_data("map_infos")
        infos.delete(9)
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="orphan the file")
        self.ctx = self.reopen()

        unfiltered = self.call("map_list")
        self.assertIn(9, unfiltered["map_files_without_tree_entry"])
        filtered = self.call("map_list", parent_id=1)
        self.assertEqual(filtered["map_files_without_tree_entry"],
                         unfiltered["map_files_without_tree_entry"])


class TestUnreadableStructuresAreNotReportedAsEmpty(ToolTest):
    """"We could not read it" and "there is nothing there" are different.

    `event_edit` refuses a map whose @events is not a Hash, by name. The three
    read tools returned an empty list instead, so a model concluded the map
    has no events when the field is unreadable.
    """

    def break_events(self):
        game_map = self.project.load_map(1)
        game_map.set("events", self.project.codec.make_string("not a hash"))
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="broken events")
        self.ctx = self.reopen()

    def test_event_get_says_it_could_not_read_them(self):
        self.break_events()
        out = self.call("event_get", map_id=1)
        self.assertEqual(out["events"], [])
        self.assertIn("validate", " ".join(str(v) for v in out.values()))

    def test_map_get_says_so_too(self):
        self.break_events()
        out = self.call("map_get", map_id=1)
        self.assertIn("validate", " ".join(str(v) for v in out.values()))

    def test_map_list_says_so_too(self):
        self.break_events()
        row = next(m for m in self.call("map_list", include_details=True)["maps"]
                   if m["id"] == 1)
        self.assertIn("validate", " ".join(str(v) for v in row.values()))


class TestAdviceNamesOnlyWhatWillWork(ToolTest):
    """A remedy the sibling tool refuses is not a remedy.

    db_get's note offered `db_edit op="create"` for every missing id above
    zero, and db_edit refuses two whole classes of those: an id far past the
    end of the table (it would pad the table with empty slots), and any id at
    all in a table db_edit will not touch. The comment above the filter
    claimed it named "only the ids db_edit will actually accept".
    """

    shared_fixture = True

    def test_an_id_past_the_create_ceiling_is_not_offered(self):
        out = self.call("db_get", table="items", ids=[1, 500000])
        self.assertIn("500000", str(out["missing_ids"]))
        self.assertNotIn("fills in 500000", out["note"])
        self.assertIn("500000", out["note"])      # named, as refused

    def test_a_table_db_edit_will_not_touch_is_not_offered(self):
        out = self.call("db_get", table="map_infos", ids=[500])
        self.assertNotIn('db_edit with op "create"', out["note"])
        self.assertIn("map_edit", out["note"])

    def test_an_ordinary_empty_slot_is_still_offered(self):
        total = self.call("db_list", table="items")["matching_entries"]
        out = self.call("db_get", table="items", ids=[total + 1])
        self.assertIn("fills in %d" % (total + 1), out["note"])


class TestArgumentDescriptionsComeFromTheTables(ToolTest):
    """A description that lists the wrong ops sends a caller the wrong way.

    `commands` said "for set_commands/append_commands/create" while
    EVENT_OP_ARGS lets set_page and add_page read it too -- so a model wanting
    a page with commands on it splits one op into two, or omits the commands.
    Generated from the table, so the two cannot disagree again.
    """

    def ops_reading(self, table, argument):
        return sorted(op for op, reads in table.items() if argument in reads)

    def test_the_commands_description_names_every_op_that_reads_it(self):
        properties = (REGISTRY["event_edit"].schema["properties"]["ops"]
                      ["items"]["properties"])
        described = properties["commands"]["description"]
        for op in self.ops_reading(event_tools.EVENT_OP_ARGS, "commands"):
            self.assertIn(op, described,
                          "%s reads commands and the description omits it" % op)

    def test_the_patch_description_covers_a_common_event(self):
        properties = (REGISTRY["event_edit"].schema["properties"]["ops"]
                      ["items"]["properties"])
        self.assertIn("RPG::CommonEvent", properties["patch"]["description"])


class TestACountIsNamedForWhatItCounted(ToolTest):
    """A filtered reply must not call its count `total`.

    db_list and map_list already carry this rename -- `matching_entries` and
    `matching_maps` -- with comments saying why: "total" read as the size of
    the table, so a filtered listing looked like the whole of a much smaller
    one. event_get and command_reference both filter and both still said
    `total`.
    """

    def test_a_filtered_event_read_names_its_count(self):
        every = self.call("event_get", map_id=1)
        self.assertIn("matching_events", every)
        self.assertNotIn("total", every,
                         "a count named `total` on a filterable reply")

    def test_the_count_follows_the_filter(self):
        every = self.call("event_get", map_id=1)["matching_events"]
        one = self.call("event_get", map_id=1, event_ids=[1])
        self.assertEqual(one["matching_events"], len(one["events"]))
        self.assertLessEqual(one["matching_events"], every)

    def test_a_map_with_no_events_names_its_count_the_same_way(self):
        """The zero case is a second literal, so it can drift from the first."""
        game_map = self.project.load_map(1)
        game_map.set("events", RubyHash())
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()

        out = self.call("event_get", map_id=1)
        self.assertEqual(out["events"], [])
        self.assertEqual(out["matching_events"], 0)
        self.assertNotIn("total", out)

    def test_a_filtered_command_reference_names_its_count(self):
        every = self.call("command_reference")
        self.assertIn("matching_commands", every)
        self.assertNotIn("total", every)
        some = self.call("command_reference", search="text")
        self.assertEqual(some["matching_commands"], len(some["commands"]))
        self.assertLess(some["matching_commands"], every["matching_commands"])

    def test_an_unfiltered_count_keeps_its_honest_name(self):
        """`scripts op="list"` returns every slot, so `total` is true there."""
        out = self.call("scripts", op="list")
        self.assertEqual(out["total"], len(out["scripts"]))


class TestAScopeIsClaimedOnlyOnceRead(ToolTest):
    """`_Hits.searching` exists so a scope that was asked for is not reported
    as read. The events scope claimed itself before opening anything.

    Every other scope moved its claim after a successful load, each with a
    comment saying why -- "before it, a file that would not load claimed to
    have been searched". The events walk kept its claim on the first line.
    """

    def break_every_events_file(self):
        """Leave nothing in the project that holds an event command list."""
        for name in ("common_events", "troops"):
            self.project.path_for(
                self.project.filename_for(name)).write_bytes(b"not marshal")
        for map_id, _ in self.project.entries("map_infos"):
            path = self.project.path_for(self.project.map_file(map_id))
            if path.exists():
                path.write_bytes(b"not marshal")
        self.ctx = self.reopen()

    def test_an_unreadable_project_does_not_claim_the_events_scope(self):
        self.break_every_events_file()
        out = self.call("search", query="anything", scopes=["events"])
        self.assertNotIn(
            "events", out["scopes_searched"],
            "the events scope was reported as searched when nothing in it "
            "could be opened, so zero hits reads as zero matches")

    def test_it_says_that_it_read_nothing_rather_than_going_quiet(self):
        """One `scopes_not_read` mapping, not one key per scope.

        This asked for `events_note`, built as `"%s_note" % scope` -- a key
        name made out of data, so a caller had to guess which of four
        spellings would come back.
        """
        self.break_every_events_file()
        out = self.call("search", query="anything", scopes=["events"])
        self.assertTrue(out.get("scopes_not_read", {}).get("events"),
                        "nothing in the reply explains the empty result: %s"
                        % sorted(out))

    def test_a_readable_project_still_claims_it(self):
        out = self.call("search", query="Potion", scopes=["events"])
        self.assertIn("events", out["scopes_searched"])
        self.assertNotIn("scopes_not_read", out)


class TestNoDeadPublicApi(unittest.TestCase):
    """A public method nothing calls is a method a reader has to understand.

    Four were never referenced from src or tests: `EngineProfile.filename_for`
    (duplicating `Project.filename_for`, which is the one that is used),
    `EngineProfile.database_tables`, `EngineProfile.map_filename`, and
    `Project.forget_assets` -- whose own sibling docstring instructs callers
    to call it.
    """

    # `Project.forget_assets` is deliberately not here. It looked dead too,
    # and turned out to be the only cure for a live bug -- see
    # TestAnAssetAddedMidSessionIsSeen. Unreferenced is a reason to ask why,
    # not a reason to delete.
    GONE = ("EngineProfile.filename_for", "EngineProfile.database_tables",
            "EngineProfile.map_filename")

    def test_they_are_gone(self):
        from rpgvxace_mcp.project import Project
        from rpgvxace_mcp.schema import EngineProfile
        owners = {"EngineProfile": EngineProfile, "Project": Project}
        still_here = []
        for dotted in self.GONE:
            owner, method = dotted.split(".")
            if hasattr(owners[owner], method):
                still_here.append(dotted)
        self.assertEqual(still_here, [],
                         "these are unreferenced from src and tests; remove "
                         "them or give them a caller")

    def test_nothing_calls_one_on_a_profile(self):
        """A call site left behind is worse than the method.

        Matched on the receiver, not the bare name: `filename_for` and
        `map_filename` are live and correct on `Project` and as a module
        function in `schema`, and only the `EngineProfile` methods went. A
        grep for the bare names flags all three of those, which is how a test
        ends up describing something other than what it checks.
        """
        root = Path(__file__).resolve().parent.parent / "src"
        calls = []
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for dotted in self.GONE:
                method = dotted.split(".")[1]
                for receiver in ("profile.", "PROFILE.", "_profile."):
                    if receiver + method in text:
                        calls.append("%s calls %s%s"
                                     % (path.name, receiver, method))
        self.assertEqual(calls, [], "; ".join(calls))


class TestACraftedNameIsBoundedInAReplyToo(ToolTest):
    """A name from the file is capped and sanitised wherever it is printed.

    `analysis._text` has done this for findings since the round that added it.
    The tools' own copies of the same one-line decode did neither, so the
    reasoning in that docstring held on one path out of two.
    """

    HUGE = 1_000_000

    def name_map_one(self, text):
        infos = self.project.load_data("map_infos")
        first = infos.items()[0]
        first[1].set("name", self.project.codec.make_string(text))
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="a crafted name")
        self.ctx = self.reopen()
        return first[0]

    def longest_name(self, rows, key="name"):
        return max((str(row[key]) for row in rows), key=len)

    def test_map_list_caps_a_crafted_name(self):
        self.name_map_one("A" * self.HUGE)
        name = self.longest_name(self.call("map_list")["maps"])
        self.assertLess(
            len(name), self.HUGE,
            "a %d-character name went into the reply whole" % len(name))

    def test_map_list_sanitises_control_characters(self):
        """By id, not by length -- picking the longest name was vacuous.

        `longest_name` answered "Riverbend Cave" (14 characters) rather
        than the crafted "Town" plus two control characters (10), so
        this assertion was about a name nothing had touched. Measured:
        it passed while the escape went into the reply intact.
        """
        map_id = self.name_map_one("Town\x07\x1b[31m")
        rows = self.call("map_list")["maps"]
        name = next(str(r["name"]) for r in rows if r["id"] == map_id)
        self.assertNotIn("\x07", name)
        self.assertNotIn("\x1b", name)

    def test_map_get_caps_it_too(self):
        map_id = self.name_map_one("B" * self.HUGE)
        out = self.call("map_get", map_id=map_id)
        self.assertLess(len(str(out["name"])), self.HUGE)

    def test_a_capped_name_says_it_was_capped(self):
        """Silently truncating a name is how a caller looks for the wrong map."""
        self.name_map_one("C" * self.HUGE)
        name = self.longest_name(self.call("map_list")["maps"])
        self.assertIn("bytes", name,
                      "the reply gives no sign the name was cut: %r"
                      % name[-40:])

    def test_an_ordinary_name_is_untouched(self):
        self.name_map_one("Riverbend")
        names = [str(row["name"]) for row in self.call("map_list")["maps"]]
        self.assertIn("Riverbend", names)

    def test_an_event_name_is_bounded_as_well(self):
        game_map = self.project.load_map(1)
        event = game_map.get("events").sorted_items()[0][1]
        event.set("name", self.project.codec.make_string("D" * self.HUGE))
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()
        out = self.call("map_get", map_id=1, include_events=True)
        self.assertLess(len(self.longest_name(out["events"])), self.HUGE)


class TestOneDecodeForTheWholeToolsLayer(unittest.TestCase):
    """Four spellings of one line, and only one of them was the careful one."""

    def test_the_shared_one_exists(self):
        from rpgvxace_mcp.tools.registry import decoded
        self.assertTrue(callable(decoded))

    def test_no_tool_module_defines_its_own(self):
        """A function named `_text` is one way to write a second copy."""

        root = Path(__file__).resolve().parent.parent / "src"
        folder = root / "rpgvxace_mcp" / "tools"
        copies = []
        for path in sorted(folder.glob("*.py")):
            if path.name == "registry.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                if node.name in ("_text", "_name", "_decoded"):
                    copies.append("%s defines %s" % (path.name, node.name))
        self.assertEqual(copies, [], "; ".join(copies))

    # Modules that decode for the *matcher* rather than for a reply, and
    # how many such calls each may make. A cap of 200 characters would mean
    # searching only the first 200 of a script line, so these read more than
    # a reply may carry -- and `_Hits.add` caps what actually reaches the
    # response. A count rather than a bare exemption, so a new bare decode
    # in the same module is still caught, and this test refuses an
    # allowance larger than the calls behind it.
    #
    # One now, not three: `subject_text` is the single place that decides
    # how much of a string a search reads, and the three call sites go
    # through it. It also slices before decoding, which the database scope
    # did not -- and reports the cut, which is what made a plain search
    # silently miss a needle past 8,000 bytes.
    MATCHER_DECODES = {"search_tools.py": 1}

    def test_no_tool_module_calls_the_codec_directly(self):
        """And an inline call is the way it was actually written.

        The name-based scan above could not see any of the four inline
        copies its own docstring names, which is the same vacuity as a
        ceiling test that keyed on a bare constant name: the defect was
        `ctx.codec.decode(value.data)` written in place, not a helper. This
        looks for the call.
        """

        root = Path(__file__).resolve().parent.parent / "src"
        folder = root / "rpgvxace_mcp" / "tools"
        counts: dict[str, int] = {}
        calls = []
        for path in sorted(folder.glob("*.py")):
            if path.name == "registry.py":
                continue        # where `decoded` lives
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr == "decode"
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "codec"):
                    counts[path.name] = counts.get(path.name, 0) + 1
                    calls.append("%s: %s" % (path.name, ast.unparse(node)))
        over = [name for name, found in counts.items()
                if found > self.MATCHER_DECODES.get(name, 0)]
        self.assertEqual(
            over, [],
            "these decode a value out of a file without the cap and the "
            "sanitising `decoded` applies: " + "; ".join(calls))

    def test_no_matcher_exemption_outlives_its_calls(self):
        """An allowance that is larger than the code needs hides a copy."""

        root = (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp" / "tools")
        for name, allowed in self.MATCHER_DECODES.items():
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            found = sum(
                1 for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "decode"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "codec")
            self.assertEqual(
                found, allowed,
                "%s is allowed %d bare decodes and makes %d; lower the "
                "allowance so the next one is caught" % (name, allowed, found))

    def test_it_agrees_with_the_analysis_one(self):
        """Two receivers, one implementation -- so they cannot drift."""
        from rpgvxace_mcp.analysis import _text
        from rpgvxace_mcp.convert import TextCodec
        from rpgvxace_mcp.rubytypes import RubyString
        from rpgvxace_mcp.tools.registry import decoded

        class Holder:
            codec = TextCodec("utf-8")

        value = RubyString(("E" * 5000).encode("utf-8"))
        self.assertEqual(decoded(Holder(), value), _text(Holder(), value))


class TestAScriptSlotIdIsCalledAnId(ToolTest):
    """`schema` and `docs` both call it an id; only the code said "magic".

    `scripts op="get"` returns the project layer's row verbatim, so whatever
    the code calls the field is what the model reads.
    """

    shared_fixture = True

    def test_the_reply_names_it_id(self):
        slot = self.call("scripts", op="list")["scripts"][0]["index"]
        out = self.call("scripts", op="get", index=slot)
        self.assertIn("id", out)
        self.assertNotIn("magic", out,
                         "the reply calls the slot id a magic number: %s"
                         % sorted(out))

    def test_the_documented_shape_agrees(self):
        """The two descriptions the model reads before calling."""
        from rpgvxace_mcp.schema import ACE_DATA_FILES
        # The row layout itself, not the two letters "id" (which "provide"
        # also holds).
        self.assertIn("[id, name",
                      ACE_DATA_FILES["scripts"].description.lower())

    def test_the_value_is_still_the_slot_id(self):
        """Renaming must not change what is reported."""
        rows = self.call("scripts", op="list")["scripts"]
        self.assertTrue(rows, "no script slots to read")
        slots = self.project.load_data("scripts")
        for row in rows:
            got = self.call("scripts", op="get", index=row["index"])
            self.assertEqual(got["index"], row["index"])
            # The renamed field, against the slot's own id in the file.
            self.assertEqual(got["id"], slots[row["index"]][0])


class TestATruncatedSummarySaysSoOnEveryPath(ToolTest):
    """The row budget expiring on the *last* event reported no truncation.

    The pages loop `break`s on the ceiling, then the event's row is appended
    and the outer loop ends normally at `return out, False`. So one event
    with more pages than the ceiling came back as a short page list with no
    `events_truncated` at all -- a reply that is wrong rather than partial,
    which is the one outcome this module's comments are all about avoiding.
    """

    def craft_one_event_with_pages(self, count):
        game_map = self.project.load_map(1)
        page = RubyObject("RPG::Event::Page")
        page.set("condition", RubyObject("RPG::Event::Page::Condition"))
        page.set("graphic", RubyObject("RPG::Event::Page::Graphic"))
        page.set("trigger", 0)
        page.set("list", [])
        event = new_event(1, "E")
        event.set("pages", [page] * count)
        events = RubyHash()
        events.set(1, event)
        game_map.set("events", events)
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()

    def test_a_single_event_over_the_ceiling_is_reported_as_cut_short(self):
        from rpgvxace_mcp.tools import map_read_tools
        self.craft_one_event_with_pages(
            map_read_tools.MAX_EVENT_SUMMARY_ROWS + 50)
        rows, cut_short = map_read_tools._event_summaries(
            self.ctx, self.project.load_map(1))
        self.assertTrue(cut_short,
                        "one event with more pages than the ceiling reported "
                        "a complete summary")

    def test_the_reply_carries_the_note(self):
        """The flag has to reach the caller, not only the helper's return."""
        from rpgvxace_mcp.tools import map_read_tools
        self.craft_one_event_with_pages(
            map_read_tools.MAX_EVENT_SUMMARY_ROWS + 50)
        result = self.call("map_get", map_id=1)
        self.assertIn("events_truncated", result)

    def test_a_row_says_how_many_pages_the_event_really_has(self):
        """`event_get` adds `page_count` for exactly this reason."""
        from rpgvxace_mcp.tools import map_read_tools
        total = map_read_tools.MAX_EVENT_SUMMARY_ROWS + 50
        self.craft_one_event_with_pages(total)
        rows, _ = map_read_tools._event_summaries(
            self.ctx, self.project.load_map(1))
        self.assertEqual(rows[0]["page_count"], total,
                         "a truncated page list did not say how many pages "
                         "the event has")


class TestAListedNameIsCappedLikeEveryOtherName(ToolTest):
    """The fifth bare decode in the tools layer, in a listing.

    `registry.decoded`'s docstring says four copies of this call were
    consolidated and "cannot differ again". `db_list` kept a fifth, on the
    one field of the row that is always present -- while `_brief_field`
    seven lines below goes through `decoded`, and its own docstring gives the
    reason: "this is a listing, so one crafted name would otherwise be
    repeated into every row".
    """

    LONG = 300_000

    def craft_named_entry(self, name):
        items = self.project.load_data("items")
        entry = items[1]
        entry.set("name", self.project.codec.make_string(name))
        self.project.save_file(self.project.filename_for("items"),
                               tool="test", summary="long name")
        self.ctx = self.reopen()

    def test_a_long_entry_name_is_capped_in_the_row(self):
        self.craft_named_entry("A" * self.LONG)
        rows = self.call("db_list", table="items")["entries"]
        first = next(r for r in rows if r["id"] == 1)
        self.assertLess(len(first["name"]), 1000,
                        "db_list returned a %d-character name"
                        % len(first["name"]))

    def test_the_cap_says_what_it_cut(self):
        """A name silently cut is how a caller looks for the wrong entry."""
        self.craft_named_entry("A" * self.LONG)
        rows = self.call("db_list", table="items")["entries"]
        first = next(r for r in rows if r["id"] == 1)
        # Either unit: a long RubyString is sliced and reported in
        # bytes, a long str in characters.
        self.assertTrue("characters" in first["name"]
                        or "bytes" in first["name"],
                        "a capped name did not say it was capped: %r"
                        % first["name"][-40:])

    def test_an_escape_in_a_name_is_sanitised(self):
        self.craft_named_entry("Potion" + chr(27) + "[31m")
        rows = self.call("db_list", table="items")["entries"]
        first = next(r for r in rows if r["id"] == 1)
        self.assertNotIn(chr(27), first["name"])

    def test_an_ordinary_name_is_unchanged(self):
        rows = self.call("db_list", table="items")["entries"]
        self.assertTrue(any(r["name"] for r in rows),
                        "capping removed every name in the table")
        # And left the short ones exactly as they were.
        names = {r["id"]: r["name"] for r in rows}
        self.assertEqual(names.get(1), "Potion")
        self.assertEqual(names.get(2), "Hi-Potion")

    def test_the_filter_still_finds_an_ordinary_name(self):
        rows = self.call("db_list", table="items")["entries"]
        wanted = next(r for r in rows if r["name"])
        found = self.call("db_list", table="items",
                          name_contains=wanted["name"][:4].lower())["entries"]
        self.assertTrue(any(r["id"] == wanted["id"] for r in found))


class TestEveryReferenceTargetHasANoun(unittest.TestCase):
    """`TABLE_NOUNS` is a hand-kept table parallel to two others.

    Read at two bare-subscript sites, so a reference rule or a nested
    reference added without a noun raises KeyError in the middle of a
    `validate` walk -- reported to the caller as an internal error about a
    dictionary.
    """

    def test_every_reference_rule_target_is_named(self):
        from rpgvxace_mcp.analysis import (MAP_TARGET, NESTED_DB_REFERENCES,
                                           REFERENCE_RULES, TABLE_NOUNS)
        wanted = (set(REFERENCE_RULES) - {MAP_TARGET}) | {
            ref.target for ref in NESTED_DB_REFERENCES}
        missing = sorted(wanted - set(TABLE_NOUNS))
        self.assertEqual(missing, [],
                         "these tables are referenced by a rule and have no "
                         "noun, so naming one raises KeyError mid-walk")

    def test_no_noun_outlives_its_table(self):
        """The other half: a noun nothing reads is a dead table entry.

        "Read" means either named by a reference rule -- where the walk
        subscripts this table by the rule's target -- or subscripted
        literally somewhere in the source. Both, because two findings name
        their noun directly rather than through a rule: the tileset sheet
        check and the actor class check.
        """
        from rpgvxace_mcp.analysis import (MAP_TARGET, NESTED_DB_REFERENCES,
                                           REFERENCE_RULES, TABLE_NOUNS)
        root = Path(__file__).resolve().parent.parent / "src"
        literal = set()
        for path in sorted((root / "rpgvxace_mcp").rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Subscript)
                        and isinstance(node.value, ast.Name)
                        and node.value.id == "TABLE_NOUNS"
                        and isinstance(node.slice, ast.Constant)):
                    literal.add(node.slice.value)
        used = ((set(REFERENCE_RULES) - {MAP_TARGET})
                | {ref.target for ref in NESTED_DB_REFERENCES} | literal)
        self.assertEqual(sorted(set(TABLE_NOUNS) - used), [],
                         "these nouns name a table nothing reads")


class TestACodeWithAConstantIsSpelledWithIt(unittest.TestCase):
    """`commands` names its codes and then wrote the numbers anyway.

    `CONDITION_KINDS`' comment says this was fixed for the COND_* set --
    "eight of the thirteen constants were dead while the function that
    should have used them spelled their numbers out" -- and the same thing
    was true of CODE_* for `summarize`, which dispatched on all eight by
    number.

    Checked rather than described, because a constant nobody reads is
    indistinguishable from no constant at all.
    """

    def module(self):
        return (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp" / "commands.py")

    def readers(self):
        """`commands.py` and every module that imports from it.

        The scan said "no function compares a code to a number that has a
        name" and read one file, so three sites in `event_tools` -- a
        module that already imports `CODE_BLANK` from here -- spelled
        codes this table names. `schema.py` is not in the list and cannot
        be: `commands` imports *from* `schema`, so a code constant is not
        available to it, and its one blank command is built with a
        literal for that reason.
        """
        package = self.module().parent
        out = [self.module()]
        for path in sorted(package.rglob("*.py")):
            if path == self.module():
                continue
            text = path.read_text(encoding="utf-8")
            if "commands import" in text or "from ..commands" in text:
                out.append(path)
        self.assertGreater(len(out), 2, "found no importers, so this scan "
                                        "would read one file")
        return out

    def constants(self):
        """Every CODE_* constant, by the number it names."""
        tree = ast.parse(self.module().read_text(encoding="utf-8"))
        out = {}
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            targets, values = node.targets[0], node.value
            pairs = (zip(targets.elts, values.elts)
                     if isinstance(targets, ast.Tuple)
                     and isinstance(values, ast.Tuple)
                     else [(targets, values)])
            for name, value in pairs:
                if (isinstance(name, ast.Name)
                        and name.id.startswith("CODE_")
                        and isinstance(value, ast.Constant)):
                    out[value.value] = name.id
        return out

    def test_no_function_compares_a_code_to_a_number_that_has_a_name(self):
        known = self.constants()
        self.assertGreater(len(known), 8, "the constants were not found")
        spelled = []
        for path in self.readers():
            spelled += self.compared_in(path, known)
        self.assertEqual(spelled, [],
                         "these compare a code to a number this module has "
                         "already named: " + "; ".join(spelled))

    def compared_in(self, path, known):
        """Each `code == N` in one file where N is a named code."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        spelled = []
        for node in ast.walk(tree):
            left = node.left if isinstance(node, ast.Compare) else None
            named = (left.id if isinstance(left, ast.Name)
                     else left.attr if isinstance(left, ast.Attribute)
                     else "")
            call = (isinstance(left, ast.Call)
                    and isinstance(left.func, ast.Attribute)
                    and left.func.attr == "get"
                    and left.args
                    and isinstance(left.args[0], ast.Constant)
                    and left.args[0].value == "code")
            if not (isinstance(node, ast.Compare)
                    and (named in ("code", "code_int") or call)):
                continue
            for comparator in node.comparators:
                items = (comparator.elts if isinstance(comparator, ast.Tuple)
                         else [comparator])
                for item in items:
                    if (isinstance(item, ast.Constant)
                            and item.value in known):
                        spelled.append("%s:%d: %s should be %s"
                                       % (path.name, item.lineno, item.value,
                                          known[item.value]))
        return spelled

    def emitted(self, tree):
        """Every code an emit site names, as (line, node) pairs.

        Every site, not only the ones spelled as a literal: the sanity
        check below counts these, and counting only literals would read
        "the emit sites were not found" as the sites got fixed.

        The first argument of `_cmd` and `new_event_command` *is* the code
        of the command being built, so a number there is the same defect as
        a number in a comparison -- and once `summarize` dispatches from a
        table rather than a chain, the comparison scan above no longer
        reaches the codes it renders, which are the table's keys.

        The `COMMAND_NAMES`, `ALL_PARAM_SPECS` and `CONTINUATION_CODES`
        keys are deliberately not scanned: those tables are where a code
        gets its meaning, they cover the whole superset rather than the
        two dozen codes with names, and half-spelling them would read
        worse than either extreme.
        """
        out = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_cmd", "new_event_command")
                    and node.args):
                out.append((node.lineno, node.args[0]))
            target = (node.target if isinstance(node, ast.AnnAssign)
                      else node.targets[0] if isinstance(node, ast.Assign)
                      else None)
            if (getattr(target, "id", "") == "_SUMMARY_RENDERERS"
                    and isinstance(node.value, ast.Dict)):
                out.extend((key.lineno, key) for key in node.value.keys)
        return out

    def test_no_command_is_built_from_a_number_at_all(self):
        """Any literal, not only one that already has a name.

        The first version reported a literal that was already in the
        constants table, which is a rule satisfied by *not* naming the
        code: four codes `compile_commands` emits to close a structure it
        opened -- 403, 404, 411, 412 -- sat two lines from
        `_cmd(CODE_CHOICE_BRANCH, ...)` and passed, in the module whose own
        comment says a module that names some of its codes and not others
        is one where the next one gets a name only if the author happens to
        look. So the rule is now the sentence that comment makes.
        """
        for path in self.readers():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            sites = self.emitted(tree)
            if path == self.module():
                self.assertGreater(len(sites), 25,
                                   "the emit sites were not found")
            spelled = ["%s:%d builds a command from %r"
                       % (path.name, line, node.value)
                       for line, node in sites
                       if isinstance(node, ast.Constant)]
            self.assertEqual(spelled, [],
                             "a command code belongs in the constants block "
                             "of `commands`, where a reader can look it up: "
                             + "; ".join(spelled))


class TestEveryCommandCodeConstantIsRead(unittest.TestCase):
    """The other direction of the rule next door.

    `TestACodeWithAConstantIsSpelledWithIt` holds that a code with a name
    is spelled with it. This holds that a name is read by something --
    otherwise the block grows a constant per round and nothing says which
    of them are load-bearing, which is how `CODE_ERASE_EVENT` came to sit
    under a comment describing the two constants either side of it.

    `CONDITION_KINDS`' own comment records the same defect one table over:
    "eight of the thirteen constants were dead while the function that
    should have used them spelled their numbers out".
    """

    def package(self):
        return (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp")

    def defined(self):
        """Every CODE_* name assigned at the top level of `commands`."""
        source = (self.package() / "commands.py").read_text(encoding="utf-8")
        names = []
        for node in ast.parse(source).body:
            if not isinstance(node, ast.Assign):
                continue
            targets = node.targets[0]
            for name in (targets.elts if isinstance(targets, ast.Tuple)
                         else [targets]):
                if (isinstance(name, ast.Name)
                        and name.id.startswith("CODE_")):
                    names.append(name.id)
        self.assertGreater(len(names), 20, "the constants were not found")
        return names

    def reads(self):
        """Every CODE_* name loaded anywhere in the package.

        A `Name` in load context, so the assignment itself does not count
        as a read and neither does the import that carries it: an
        `ImportFrom` alias is not a `Name` node.
        """
        found = set()
        for path in sorted(self.package().rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Name)
                        and isinstance(node.ctx, ast.Load)
                        and node.id.startswith("CODE_")):
                    found.add(node.id)
        return found

    def test_every_named_code_is_read_somewhere(self):
        idle = sorted(set(self.defined()) - self.reads())
        self.assertEqual(
            idle, [],
            "these code constants are defined and never read, so nothing "
            "would notice if the number were wrong: " + ", ".join(idle))


class TestOneOwnerForTheClipCount(unittest.TestCase):
    """Nothing outside `_Matcher` may write `clipped`.

    It was incremented in two places under opposite conditions: inside
    `__call__` when the pattern is a regex, and from `subject_text`, which
    reached into the object and tested `matcher.whole` itself. That
    arrangement double-counted once already -- the comment in
    `subject_text` recorded it -- and an agent editing either site could
    not see the other.

    A scan rather than a fixed pair of names, because the defect is a
    third writer, which is exactly what the second one was.
    """

    def modules(self):
        root = (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp")
        found = sorted(root.rglob("*.py"))
        self.assertGreater(len(found), 5, "found almost no modules")
        return found

    def test_only_the_matcher_writes_the_clip_count(self):
        writers = []
        for path in self.modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AugAssign)):
                    continue
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target])
                for target in targets:
                    if not (isinstance(target, ast.Attribute)
                            and target.attr == "clipped"):
                        continue
                    owner = target.value
                    # `self.clipped` inside `_Matcher` is the owner; any
                    # other object's `.clipped` is a second writer.
                    if not (isinstance(owner, ast.Name)
                            and owner.id == "self"):
                        writers.append("%s:%d" % (path.name, node.lineno))
        self.assertEqual(
            writers, [],
            "these write another object's clip count, so the rule about "
            "which window is being counted lives in two places: "
            + ", ".join(writers))


class TestEveryTableEntryTakesTheTablesArguments(unittest.TestCase):
    """"Every value takes the same five arguments" was prose.

    `_COMPILERS` and `_SUMMARY_RENDERERS` are both walked without asking
    which shape their entries have -- that is what the uniform signature
    buys -- and nothing held it: a two-argument renderer added to the
    table raises TypeError at the moment a caller renders that one code,
    which is a crafted file's choice and not the suite's.

    Read off each entry rather than from the declared type, because the
    declared type is the other half of the same claim: `Callable[...]` is
    accepted by no checker this project runs.
    """

    # Five for the compilers since the layout forms: the engine is the
    # fifth, because VX and VX Ace write different parameter lists.
    TABLES = {"_COMPILERS": 5, "_SUMMARY_RENDERERS": 3}

    def module(self):
        return (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp" / "commands.py")

    def tree(self):
        return ast.parse(self.module().read_text(encoding="utf-8"))

    def functions(self, tree):
        """Every top-level function, by name."""
        return {node.name: node for node in tree.body
                if isinstance(node, ast.FunctionDef)}

    def takes(self, node, functions):
        """How many positional arguments one table value takes, or None."""
        if isinstance(node, ast.Lambda):
            return len(node.args.args)
        if isinstance(node, ast.Name) and node.id in functions:
            return len(functions[node.id].args.args)
        return None

    def test_every_entry_takes_its_tables_arguments(self):
        tree = self.tree()
        functions = self.functions(tree)
        found = {}
        wrong = []
        for node in ast.walk(tree):
            target = (node.target if isinstance(node, ast.AnnAssign)
                      else node.targets[0] if isinstance(node, ast.Assign)
                      else None)
            name = getattr(target, "id", "")
            if name not in self.TABLES or not isinstance(node.value, ast.Dict):
                continue
            found[name] = len(node.value.values)
            for key, value in zip(node.value.keys, node.value.values):
                arity = self.takes(value, functions)
                if arity is None:
                    wrong.append("%s: an entry this scan cannot read at "
                                 "line %d" % (name, value.lineno))
                elif arity != self.TABLES[name]:
                    wrong.append("%s: the entry at line %d takes %d "
                                 "argument%s, not %d"
                                 % (name, value.lineno, arity,
                                    "" if arity == 1 else "s",
                                    self.TABLES[name]))
        self.assertEqual(sorted(found), sorted(self.TABLES),
                         "a table was not found, so this checked nothing")
        for name, count in found.items():
            self.assertGreater(count, 10, "%s has %d entries" % (name, count))
        self.assertEqual(wrong, [], "; ".join(wrong))


class TestOneVocabularyForAPartialAnswer(ToolTest):
    """Twenty-odd spellings of "this is not the whole story", two colliding.

    `notes` was a list of layout notes in `command_reference` and a sentence
    about clipped strings in `search`; `events_truncated` was a paragraph
    while `references_truncated` and `incoming_transfers_truncated` two
    modules over were booleans. One key, two types, in both cases -- so a
    caller reading either had to know which tool it was talking to, which
    is the opposite of what a shared suffix is for.

    Three suffixes now, one type each, and `registry.partial` builds them.
    """

    # The type each suffix promises. Held to `PARTIAL_SUFFIXES` by
    # `test_every_declared_suffix_has_a_type_here` below, which is what
    # makes that constant load-bearing: it was referenced nowhere in src or
    # tests, and this suite kept a second independent copy -- so adding a
    # fourth suffix to the source of truth changed nothing anywhere -- and
    # a fourth is exactly what arrived: `_stopped` was in use in four
    # places while the tuple said there were three.
    TYPES = {"_truncated": bool, "_note": str, "_not_read": dict,
             "_stopped": str}

    def replies(self):
        """A broad sample of real replies, read-only where possible."""
        slot = self.call("scripts", op="list")["scripts"][0]["index"]
        return [
            ("project_info", dict()),
            ("db_list", dict(table="items")),
            ("db_get", dict(table="items", id=1)),
            ("map_list", dict(include_details=True)),
            ("map_get", dict(map_id=1, tiles="counts")),
            ("map_tiles", dict(op="read", map_id=1, x=0, y=0, width=2,
                               height=2, z=0)),
            ("event_get", dict(map_id=1)),
            ("event_get", dict(common_event_id=1)),
            ("search", dict(query="Potion")),
            ("search", dict(query=".", regex=True)),
            ("search", dict(command_code=101)),
            ("validate", dict()),
            # Every scope: five of its replies are built in `analysis`, the
            # layer that may not call `partial` (see HAND_BUILT_IS_FINE), so
            # this read is the only thing holding them to the vocabulary.
            ("map_critique", dict()),
            ("command_reference", dict()),
            ("scripts", dict(op="get", index=slot)),
            ("backups", dict(op="list")),
            ("raw_data", dict(op="get", table="system", path="game_title")),
            ("db_edit", dict(table="items", dry_run=True,
                             ops=[{"op": "delete", "id": 1}])),
            ("map_edit", dict(op="delete", map_id=2, dry_run=True)),
            ("map_paint", dict(op="dungeon", map_id=2, floor="A2:0",
                               wall="A4:8", room_min=2, room_max=3,
                               dry_run=True)),
        ]

    def keys_of(self, value, prefix=""):
        """Every key in a reply, including the ones inside its rows."""
        if isinstance(value, dict):
            for key, item in value.items():
                yield key, item
                yield from self.keys_of(item)
        elif isinstance(value, list):
            for item in value:
                yield from self.keys_of(item)

    def test_every_partial_key_has_the_type_its_suffix_promises(self):
        wrong = []
        for name, arguments in self.replies():
            reply = self.call(name, **arguments)
            for key, value in self.keys_of(reply):
                for suffix, kind in self.TYPES.items():
                    if key.endswith(suffix) and not isinstance(value, kind):
                        wrong.append("%s: %s is %s, not %s"
                                     % (name, key, type(value).__name__,
                                        kind.__name__))
        self.assertEqual(wrong, [],
                         "these keys share a suffix and not a type: "
                         + "; ".join(wrong))

    # The two functions whose job *is* building these keys. Every other
    # place that makes a key out of data is the defect: `"%s_note" % scope`
    # produced one of four names depending on which scope came back empty,
    # so a caller had to guess which one to read.
    OWNERS = ("partial", "as_reply")

    # The modules that may still spell a suffix by hand, and why. All sit
    # *below* the tools layer where `partial` lives, so calling it would
    # invert the layering `TestTheAnalysisLayeringIsAcyclic` holds -- and a
    # rule with no exceptions and a layering with one is worse than saying
    # so. Eight other sites had no reason at all: `search_tools` built
    # `strings_clipped_note` and `text_scan_stopped` ten lines from
    # `partial("command_walk", ...)`, under a comment reading "all three
    # through `partial`, which owns the suffixes".
    HAND_BUILT_IS_FINE = {
        "convert.py": "sits below `tools`, and builds `cells_truncated` and "
                      "`cells_note` for a table it is converting",
        "report.py": "the analysis layer, which sits below `tools`; it "
                     "builds `command_walk_stopped`",
        # The `map_critique` scopes, for the same reason as `report.py`.
        # Each builds its own scope's keys and nothing else, and every one
        # of them is read back through `replies()` above, which is what the
        # registry's own comment says holds analysis-built keys to the
        # vocabulary -- that read is what found `script_blocks_truncated`
        # serving a count under the boolean suffix.
        "dead_ends.py": "the analysis layer; builds `dead_ends_stopped` and "
                        "`dead_ends_note`",
        # The lore scan, for the same reason: it builds its own answer's
        # keys (`walk_stopped`, `proper_nouns_truncated`, `files_not_read`
        # and the review's `findings_truncated`) and nothing else.
        "lore_baseline.py": "the analysis layer; builds `walk_stopped`, "
                            "`files_not_read` and the `_truncated` keys of "
                            "the baseline and the review",
        "map_shape.py": "the analysis layer; builds `dead_space_stopped`",
        "reachability.py": "the analysis layer; builds `reachability_stopped` "
                           "and `reachability_note`",
        "switch_usage.py": "the analysis layer; builds "
                           "`switch_usage_stopped`",
        "tile_variety.py": "the analysis layer; builds "
                           "`tile_variety_stopped`",
    }

    def source_files(self):
        root = (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp")
        found = sorted(root.rglob("*.py"))
        self.assertGreater(len(found), 5, "found almost no modules")
        return found

    def test_no_module_builds_a_suffixed_key_by_hand(self):
        """A key assigned under one of these suffixes, outside `partial`.

        The type test above reads the *replies*, so it sees a hand-built
        key as long as the value happens to have the right type -- and the
        "no key built out of data" test only looks for a format expression.
        Neither notices `out["strings_clipped_note"] = ...`, which is how
        eleven of them accumulated beside the function that owns them.
        """
        built = []
        for path in self.source_files():
            if path.name in self.HAND_BUILT_IS_FINE:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if not (isinstance(target, ast.Subscript)
                            and isinstance(target.slice, ast.Constant)
                            and isinstance(target.slice.value, str)):
                        continue
                    key = target.slice.value
                    if any(key.endswith(suffix)
                           for suffix in PARTIAL_SUFFIXES):
                        built.append("%s:%d %s"
                                     % (path.name, node.lineno, key))
        self.assertEqual(
            built, [],
            "these build a key of the partial-answer vocabulary by hand, so "
            "the function that owns the suffixes is not the only thing that "
            "spells them: " + "; ".join(built))

    def test_no_exception_outlives_its_module(self):
        names = {path.name for path in self.source_files()}
        stale = sorted(set(self.HAND_BUILT_IS_FINE) - names)
        self.assertEqual(stale, [], "excused and gone: " + ", ".join(stale))

    def test_every_declared_suffix_has_a_type_here(self):
        """The other direction: a suffix the module declares is checked.

        Two tables for one vocabulary is how the source-of-truth one went
        inert. This asserts they name the same set, so a fourth suffix
        cannot be added to `PARTIAL_SUFFIXES` without a type to hold it.
        """
        self.assertEqual(sorted(self.TYPES), sorted(PARTIAL_SUFFIXES),
                         "the type table and the module's tuple disagree, so "
                         "one of them is not the vocabulary")

    def test_partial_can_build_every_suffix_it_declares(self):
        """A function that owns the suffixes has to be able to build them."""
        from rpgvxace_mcp.tools.registry import partial
        built = partial("x", truncated=True, notes=("why",),
                        not_read={"scope": "reason"}, stopped="where")
        self.assertEqual(sorted(built),
                         sorted("x" + suffix for suffix in PARTIAL_SUFFIXES))

    def test_no_reply_key_is_built_out_of_data(self):
        """A key made from a value cannot be looked up in any schema.

        The scan looks for a format expression in a subscript position,
        which is the shape that produced it -- skipping the two functions
        that own the suffixes, because a rule that forbids its own
        implementation is not a rule.
        """
        root = (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp")
        dynamic = []
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # By node identity, because `ast.walk` yields a function's whole
            # body whatever the loop does with the FunctionDef itself -- so
            # skipping the definition does not skip what is inside it.
            allowed = {id(inner)
                       for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef)
                       and node.name in self.OWNERS
                       for inner in ast.walk(node)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Subscript):
                    continue
                if id(node) in allowed:
                    continue
                index = node.slice
                if (isinstance(index, ast.BinOp)
                        and isinstance(index.op, ast.Mod)
                        and isinstance(index.left, ast.Constant)
                        and isinstance(index.left.value, str)):
                    dynamic.append("%s: %s" % (path.name,
                                               ast.unparse(node)))
        self.assertEqual(dynamic, [],
                         "these build a reply key out of data: "
                         + "; ".join(dynamic))

    def test_the_owner_list_names_functions_that_exist(self):
        """So an exemption cannot outlive the function it was written for."""
        from rpgvxace_mcp.analysis import ReferencePreview
        from rpgvxace_mcp.tools import registry
        homes = {"partial": registry, "as_reply": ReferencePreview}
        # `OWNERS` itself, which this used to leave unread: renaming one of
        # its entries left the test green and the exemption covering any
        # function of the old name.
        self.assertEqual(sorted(homes), sorted(self.OWNERS))
        for name, home in homes.items():
            self.assertTrue(callable(getattr(home, name, None)),
                            "%s is exempt as an owner and does not exist"
                            % name)

    def test_a_reported_omission_forces_a_partial_key(self):
        """The rule the whole vocabulary exists for.

        A reply that says it left something out must also carry one of the
        three suffixes, or the count is the only sign and a caller reading
        the list rather than the count sees a complete answer.
        """
        counters = ("cells_omitted", "tile_ids_not_shown")
        silent = []
        for name, arguments in self.replies():
            reply = self.call(name, **arguments)
            omitted = [(key, value) for key, value in self.keys_of(reply)
                       if key in counters and isinstance(value, int)
                       and value > 0]
            if not omitted:
                continue
            said = [key for key, _ in self.keys_of(reply)
                    if any(key.endswith(s) for s in self.TYPES)]
            if not said:
                silent.append("%s reports %s and says nothing"
                              % (name, omitted))
        self.assertEqual(silent, [], "; ".join(silent))

    def test_a_reply_that_omits_tile_ids_says_so_in_a_partial_key(self):
        """The case the sampled replies never reach.

        Every sample above reports `tile_ids_not_shown: 0`, so the rule that
        an omission forces a partial key was checked against nothing. A map
        with more distinct tile ids than are listed makes the counter
        positive.
        """
        from rpgvxace_mcp.tools import map_read_tools
        table = self.ctx.project.load_map(1).get("data")
        for number in range(map_read_tools.TOP_TILE_IDS + 5):
            table.set(number % table.xsize, number // table.xsize, 0,
                      2816 + number)
        reply = self.call("map_get", map_id=1, tiles="counts")
        hidden = [value for key, value in self.keys_of(reply)
                  if key == "tile_ids_not_shown" and value > 0]
        self.assertTrue(hidden, "the crafted map hid no tile ids: %s"
                        % self.keys_of(reply))
        said = [key for key, _ in self.keys_of(reply)
                if any(key.endswith(s) for s in self.TYPES)]
        self.assertTrue(said, "tile ids were left out and no partial key "
                              "says so: %s" % hidden)

    def test_every_op_taking_tool_says_which_op_answered(self):
        """`op` selects the branch, so a reply cannot be read without it."""
        missing = []
        for name, tool in REGISTRY.items():
            spec = (tool.schema.get("properties") or {}).get("op") or {}
            if not spec.get("enum"):
                continue
            for label, arguments in self.replies():
                if label != name or "op" not in arguments:
                    continue
                reply = self.call(name, **arguments)
                if reply.get("op") != arguments["op"]:
                    missing.append("%s op=%s" % (name, arguments["op"]))
        self.assertEqual(missing, [],
                         "these replies do not say which op answered: "
                         + ", ".join(missing))

    def test_the_op_comes_first_or_second(self):
        """A reply is read in order, and `dry_run` is the only thing before it."""
        for name, arguments in self.replies():
            if "op" not in arguments:
                continue
            with self.subTest(tool=name, op=arguments["op"]):
                reply = self.call(name, **arguments)
                keys = list(reply)[:2]
                self.assertIn("op", keys,
                              "%s answers %s before it says which op"
                              % (name, keys))


class TestTwoClaimsAreChecked(ToolTest):
    """`printable_text` and `brief` each state a fact. These check them.

    A comment asserting something no test holds is the defect this project
    treats as equal to a bug -- and both of these were written that way in
    the round that added them.
    """

    def test_no_unencodable_character_is_printable(self):
        """`printable_text`'s comment says "checked over the whole
        code-point range, not assumed" -- and nothing checked it.

        The claim is what lets one filter do two jobs: replacing every
        non-printable character also replaces every character that cannot
        be encoded as UTF-8. If it were false, a lone surrogate that
        `isprintable()` accepted would reach `json.dumps` and end the
        session on the transport, which is what the surrogate branch used
        to exist for.
        """
        wrong = []
        for code_point in range(0x110000):
            char = chr(code_point)
            try:
                char.encode("utf-8")
            except UnicodeEncodeError:
                if char.isprintable():
                    wrong.append(hex(code_point))
        self.assertEqual(wrong, [],
                         "these characters are printable and cannot be "
                         "encoded, so one filter is not enough: %s"
                         % wrong[:8])

    def test_brief_bounds_a_container_before_it_builds_the_text(self):
        """`brief` says the bound comes first. It did, for strings only.

        A MapInfos key can legally be an Array, and `_brief`'s one caller
        passes a MapInfos key. Measured before this: `repr` of a
        200,000-entry list built 1.3 MB to return 224 characters -- a cap
        on the result and not on the work.

        Asserted by *work done*, which is the property and not a race: the
        entries count how often they are rendered, so a bound that only cuts
        the answer renders all of them and one that stops the building does
        not. (This timed the call, with the list's own construction inside the
        timed region and a 0.5 s ceiling a slow machine could cross -- and a
        repr of the whole list takes less than that on a fast one.)
        """
        from rpgvxace_mcp.convert import brief

        class Entry:
            rendered = 0

            def __repr__(self):
                Entry.rendered += 1
                return "e"

        entries = [Entry() for _ in range(200_000)]
        small = brief(entries[:100])
        Entry.rendered = 0
        large = brief(entries)
        self.assertLess(Entry.rendered, 1000,
                        "%d of 200,000 entries were rendered to describe a "
                        "list, so the whole thing was built first"
                        % Entry.rendered)
        self.assertGreater(Entry.rendered, 0, "nothing was rendered")
        self.assertLess(len(large), 1000)
        self.assertLess(abs(len(large) - len(small)), 200,
                        "the description grew with the input")

    def test_brief_still_says_what_a_short_value_is(self):
        from rpgvxace_mcp.convert import brief
        self.assertEqual(brief(5), "5")
        self.assertEqual(brief([1, 2, 3]), "[1, 2, 3]")
        self.assertEqual(brief("ab"), "'ab'")

    def test_brief_says_how_long_a_long_string_really_was(self):
        from rpgvxace_mcp.convert import brief
        out = brief("A" * 5000)
        self.assertIn("5000", out)
        self.assertLess(len(out), 500)


class TestAPageFieldCanBeReadBackAfterWriting(ToolTest):
    """`event_edit` may patch a page field, so `event_get` shows it.

    `move_route` was the exception, and the only one of the thirteen
    `RPG::Event::Page` fields it applied to: `event_edit` accepts a patch to
    it because `refuse_unknown_fields` is keyed off the documented field
    list, and `_page_view` named eleven fields by hand and did not name that
    one. So a model could set a custom move route, be told the edit
    succeeded, read the page back, and see no move route -- which reads
    exactly like a write that silently did nothing.

    Derived from the schema rather than listed, because a list here is how
    the other one went wrong. `list` and `id` are excluded: `list` is the
    command list, which the reply carries as `script` and `command_count`
    and which `_check_page_patch` refuses to patch wholesale, and `id` is
    not a page field.
    """

    NOT_A_VIEW_FIELD = {
        # Answered as `script` plus `command_count`, and refused as a patch.
        "list",
    }

    def page_fields(self):
        from rpgvxace_mcp.schema import SHARED_CLASS_FIELDS
        return (set(SHARED_CLASS_FIELDS["RPG::Event::Page"])
                - self.NOT_A_VIEW_FIELD)

    def test_every_documented_page_field_is_in_the_view(self):
        out = self.call("event_get", map_id=1)
        page = out["events"][0]["pages"][0]
        missing = sorted(self.page_fields() - set(page))
        self.assertEqual(
            missing, [],
            "`event_edit` accepts a patch to these and `event_get` does not "
            "answer them, so a write reads back as if it had not happened: "
            + ", ".join(missing))

    def test_the_view_invents_no_page_field(self):
        """The other direction, so this cannot pass by answering everything."""
        out = self.call("event_get", map_id=1)
        page = out["events"][0]["pages"][0]
        # The keys a reply adds that are not fields of the object: the page
        # index, the rendered script, the counts, and the raw echo.
        REPLY_ONLY = {"page", "script", "command_count", "commands",
                      "trigger_code", "commands_not_read"}
        invented = sorted(set(page) - self.page_fields() - REPLY_ONLY)
        self.assertEqual(invented, [],
                         "these are neither page fields nor named reply "
                         "keys: " + ", ".join(invented))

    def test_a_move_route_written_is_a_move_route_read(self):
        """End to end, on the field that was missing."""
        self.call("event_edit", map_id=1,
                  ops=[{"op": "set_page", "event_id": 1, "page": 0,
                        "patch": {"move_type": 3}}])
        out = self.call("event_get", map_id=1, event_ids=[1], page=0)
        page = out["events"][0]["pages"][0]
        self.assertIn("move_route", page)
        self.assertEqual(page["move_type"], 3)
