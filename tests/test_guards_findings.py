"""Guards on how many findings `validate` may collect, and from where.

A ceiling on findings *collected*, not returned: a project crafted
to produce a finding per event command would otherwise build
millions of them in order to report two hundred. Every rule that
appends inside a loop is in scope, and the scanning tests here are
what catch a rule nobody thought to bound.
"""

import ast
import json
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root


from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.analysis import MAX_FINDINGS  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.schema import (  # noqa: E402
    new_event,
    new_event_command,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    ToolError,
)
from tool_case import (  # noqa: E402
    ToolTest,
    ValidateTest,
)


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
        self.analysis = analysis
        self.lower_the_finding_ceiling(self.CEILING)

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

        event = new_event(1, "E")
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
        self.assertEqual(len(found), self.CEILING,
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
        self.assertEqual(len(found), self.CEILING,
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
        self.assertEqual(len(found), self.CEILING,
                         "the tileset walk collected %d findings past a "
                             "ceiling of %d" % (len(found), self.CEILING))

    def test_an_ordinary_project_still_reports_everything(self):
        """The ceiling must not clip a real project's findings.

        Through the helper, and asserting the key `_report` actually sets.
        This assigned `analysis.MAX_FINDINGS = 5000` -- the package, not the
        modules that read it, which is the exact trap
        `lower_the_finding_ceiling`'s own docstring records -- so it ran at
        the ceiling of 4 that `setUp` had installed. And it asserted the
        absence of `findings_ceiling_reached`, a key no code in this
        project writes; the key is `stopped_early`. Two ways of passing
        without checking anything, in one four-line test.
        """
        self.lower_the_finding_ceiling(5000)
        out = self.call("validate")
        self.assertNotIn("stopped_early", out)
        self.assertFalse(out["findings_truncated"])


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
        # And the maps really were read: the absence of one key is also what a
        # run that checked nothing reports.
        loaded = set(self.loaded_maps(scopes=["events"]))
        on_disk = set(self.ctx.project.map_ids_on_disk())
        self.assertTrue(on_disk <= loaded,
                        "a whole run left maps unread: %s"
                        % sorted(on_disk - loaded))

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
        # Well outside the target map, whatever size it is.
        command = new_event_command(
            201, 0, [0, target, width + 50, height + 50, 0, 0])
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
        self.lower_the_finding_ceiling(self.CEILING)

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
        self.assertEqual(
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
        # Exactly the ceiling: a walk that found nothing would also be
        # "at most" it.
        self.assertEqual(len(found), self.CEILING)

    def test_a_real_encounter_list_is_still_checked(self):
        """The ceiling must not stop a project's genuine findings."""
        self.lower_the_finding_ceiling(5000)
        missing = self.craft_bad_encounters()
        tables = self.analysis._Tables(self.project)
        found = self.analysis.check_maps(self.project, [1], tables)
        self.assertTrue(found, "no finding at all for a missing troop")
        self.assertTrue(any(f.rule == "missing_troop" for f in found),
                        [f.rule for f in found])
        self.assertIn(str(missing), " ".join(f.message for f in found))

    def test_the_dimension_check_still_runs(self):
        """`check_maps` calls two sub-checks; stopping must not lose the other."""
        self.lower_the_finding_ceiling(5000)
        game_map = self.project.load_map(1)
        game_map.set("width", 999)          # disagrees with the tile table
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.project = Project(self.tmp / "project")
        tables = self.analysis._Tables(self.project)
        found = self.analysis.check_maps(self.project, [1], tables)
        self.assertTrue(any("999" in f.message for f in found),
                        [f.message for f in found])


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
        # Fifty times the allowance below. The cycle only has to be longer
        # than the budget, but the length is what makes a walk that keeps
        # scanning after the budget is gone show itself: it re-walks the
        # chain from every start the early break left unsettled, so the
        # lookups are counted below rather than timed.
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
        class Counting(dict):
            lookups = 0

            def get(self, key, default=None):
                Counting.lookups += 1
                return dict.get(self, key, default)

        budget = analysis.WalkBudget(limit=100)
        analysis._parent_cycles(Counting(infos.items()), analysis._Findings(),
                                budget)
        self.assertTrue(budget.exhausted,
                        "a %d-entry cycle was walked free" % total)
        # The bound below is on lookups, which a walk that never looked
        # anything up also satisfies.
        self.assertGreater(Counting.lookups, 0,
                           "the walk never asked the map tree for a parent")
        # One walk of the chain is `total` lookups. Once the budget is gone
        # nothing more is worth walking, so a second pass from another start
        # is the quadratic behaviour this bounds.
        self.assertLessEqual(
            Counting.lookups, 2 * total,
            "%d lookups for a %d-entry cycle: the walk kept scanning "
            "starts after its budget was exhausted" % (Counting.lookups, total))

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

    def test_a_parent_chain_fills_the_ceiling_and_no_more(self):
        """The chain loop on its own, with a collector of its own.

        `test_a_parent_chain_cannot_collect_past_the_ceiling` goes through
        `check_map_tree`, where the outer loop can fill the collector first.
        """
        from rpgvxace_mcp import analysis
        infos = {}
        total = MAX_FINDINGS + 200
        for map_id in range(1, total + 1):
            info = RubyObject("RPG::MapInfo")
            info.set("name", self.project.codec.make_string("M%d" % map_id))
            info.set("parent_id", map_id + 1 if map_id < total else 1)
            infos[map_id] = info
        collector = analysis._Findings()
        analysis._parent_cycles(infos, collector,
                                analysis.WalkBudget(limit=10 ** 9))
        self.assertEqual(len(collector.items), MAX_FINDINGS,
                         "a %d-entry cycle left %d findings against a "
                         "ceiling of %d" % (total, len(collector.items),
                                            MAX_FINDINGS))

    def test_an_ordinary_project_still_reports_its_real_findings(self):
        """The charges must not make a real project look crafted."""
        out = self.call("validate", scopes=["maps", "database"])
        self.assertNotIn("command_walk_stopped", out)


class TestEveryLintWalkTakesAnAllowance(unittest.TestCase):
    """Every function in the linter that loops over file data takes a budget.

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
        "map_assets": "loops over two field pairs of one map -- a fixed "
                      "list in this file, not a length out of the project "
                      "-- and is called once per map file on disk, a count "
                      "bounded by the filesystem rather than by a length "
                      "out of a file",
    }

    def modules(self):
        """Every module of the linter, globbed rather than named.

        It was one file and this method returned it. Globbing is the whole
        point of the class: a walk nobody thought to add is what it looks
        for, and a module nobody thought to add is the same defect one
        level up -- `lint/` could have gained an eighth file full of
        unbudgeted loops without this failing.
        """
        package = (Path(__file__).resolve().parent.parent / "src"
                   / "rpgvxace_mcp" / "analysis" / "lint")
        found = sorted(path for path in package.glob("*.py")
                       if path.name != "__init__.py")
        self.assertGreater(len(found), 1,
                           "found no linter modules, so this scanned nothing")
        return found

    def functions(self):
        """Every top-level function of the linter, wherever it lives."""
        for path in self.modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    yield path.stem, node

    def looping_functions(self):
        """Every top-level function in the linter with a loop in it."""
        out = {}
        for _, node in self.functions():
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
        idle = []
        for stem, node in self.functions():
            if "budget" not in [a.arg for a in node.args.args]:
                continue
            body = ast.unparse(node)
            if "budget.spend" not in body and "budget)" not in body:
                idle.append("%s.%s" % (stem, node.name))
        self.assertEqual(idle, [],
                         "these take a budget and neither spend it nor pass "
                         "it on: " + ", ".join(idle))


class TestAWholeRunHasOneCeiling(ToolTest):
    """`MAX_FINDINGS` bounds a run, not each check's own list.

    Its comment says it bounds "findings collected, not just findings
    returned", and `_Findings`' docstring says "two ceilings of
    MAX_FINDINGS are a ceiling of twice that" -- and `validate`
    concatenated seven separately-bounded lists, plus the second one
    `check_tilesets` returned, so a whole-project run could collect about
    eight times it. `_report` then reported `stopped_early` as "collection
    stopped at 5000 findings" when it had not.

    Nothing saw the sum: `TestNoWalkCollectsPastTheCeiling` tests each walk
    alone and `TestCollectionIsBounded` runs one scope. This runs every
    scope at once and asks the collector.
    """

    CEILING = 6

    def setUp(self):
        super().setUp()
        self.lower_the_finding_ceiling(self.CEILING)

    def break_everything(self):
        """Enough wrong, in enough scopes, to pass a small ceiling in each."""
        infos = self.project.load_data("map_infos")
        for map_id in range(50, 90):
            info = RubyObject("RPG::MapInfo")
            info.set("name", self.project.codec.make_string("M%d" % map_id))
            info.set("parent_id", 0)
            info.set("order", map_id)
            infos.set(map_id, info)
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="tree entries with no files")
        system = self.project.load_data("system")
        system.set("party_members", list(range(900, 940)))
        self.project.save_file(self.project.filename_for("system"),
                               tool="test", summary="absent actors")
        self.ctx = self.reopen()

    def test_a_whole_run_collects_no_more_than_the_ceiling(self):
        from rpgvxace_mcp.analysis import lint
        self.break_everything()
        out = self.call("validate", limit=lint.MAX_FINDINGS)
        self.assertLessEqual(
            out["counts"]["error"] + out["counts"]["warning"]
            + out["counts"]["info"], self.CEILING,
            "a run over every scope counted %s against a ceiling of %d"
            % (out["counts"], self.CEILING))

    def test_it_says_that_collection_stopped(self):
        """The claim `stopped_early` makes has to be the one that is true."""
        self.break_everything()
        out = self.call("validate")
        self.assertIn("stopped_early", out)
        self.assertIn(str(self.CEILING), out["stopped_early"])

    def test_an_ordinary_project_does_not_claim_it_stopped(self):
        out = self.call("validate")
        self.assertNotIn("stopped_early", out)


class TestTheCeilingStopsTheWalkingToo(ToolTest):
    """`_Findings.add` caps what is kept; these lines cap what is visited.

    Since `validate` began collecting into one `_Findings`, `add` refuses
    past the ceiling on its own -- which is the property its docstring
    claims ("a caller that forgets `room` still cannot exceed the
    ceiling"). So a test that counts findings passes with a loop's
    `findings.full` check reverted, and two guards on those checks came
    back VACUOUS.

    What the checks still buy is the *iterating*, and in this codebase that
    is the cost: a crafted map's event count comes out of the file, so a
    loop that keeps going after the ceiling keeps charging the walk
    allowance for findings nobody will ever see. The budget is what knows,
    so the budget is what these ask.
    """

    CEILING = 4
    EVENTS = 3000

    def setUp(self):
        super().setUp()
        from rpgvxace_mcp import analysis
        from rpgvxace_mcp.analysis import lint
        self.analysis = analysis
        self.lint = lint
        self.lower_the_finding_ceiling(self.CEILING)

    def craft_shared_events(self, pages):
        """One event object across many slots, each page broken the same way."""
        game_map = self.project.load_map(1)
        event = new_event(1, "E")
        event.set("pages", pages)
        events = RubyHash()
        for event_id in range(1, self.EVENTS + 1):
            events.set(event_id, event)
        game_map.set("events", events)
        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(marshal_rb.dump(game_map))
        self.assertLess(path.stat().st_size, 256 * 1024)
        self.ctx = self.reopen()

    def shadowing_pages(self):
        """Two unconditional pages: the second shadows the first."""
        pages = []
        for _ in range(2):
            page = RubyObject("RPG::Event::Page")
            page.set("condition", RubyObject("RPG::Event::Page::Condition"))
            page.set("graphic", RubyObject("RPG::Event::Page::Graphic"))
            page.set("trigger", 0)
            page.set("list", [])
            pages.append(page)
        return pages

    def test_the_page_walk_stops_visiting_at_the_ceiling(self):
        """`check_event_pages` returns rather than walking on for nothing."""
        self.craft_shared_events(self.shadowing_pages())
        budget = self.analysis.WalkBudget()
        self.lint.check_event_pages(self.project, [1], budget)
        # Four findings' worth of events, not three thousand: the ceiling
        # is reached in the first few and the walk returns.
        self.assertGreater(budget.remaining,
                           self.analysis.MAX_WALK_STEPS - 200,
                           "the page walk kept charging after the ceiling: "
                           "%d of %d steps spent"
                           % (self.analysis.MAX_WALK_STEPS - budget.remaining,
                              self.analysis.MAX_WALK_STEPS))

    def test_the_command_walk_stops_visiting_at_the_ceiling(self):
        """`check_commands` breaks out rather than reading on for nothing.

        Counted by rule calls, not by budget: `iter_command_lists` charges
        per command *list*, so the inner loop's iterations are not in the
        allowance and the break has no effect there. What it does affect is
        how many commands are examined after the ceiling -- which is the
        cost the comment on it describes.
        """
        page = RubyObject("RPG::Event::Page")
        page.set("condition", RubyObject("RPG::Event::Page::Condition"))
        page.set("graphic", RubyObject("RPG::Event::Page::Graphic"))
        page.set("trigger", 0)
        # 119 is Jump to Label, and there is no Label.
        jump = new_event_command(119, 0, [self.project.codec.make_string("nowhere")])
        page.set("list", [jump] * 500)
        self.craft_shared_events([page])
        looked = []
        # The module that owns both, not the package that re-exports them:
        # `check_commands` looks `_check_jump` up as its own global, so
        # patching the re-export leaves the real one running and the count
        # below reads as a pass.
        rules = self.lint.command_rules
        original = rules._check_jump

        def counting(*args, **kwargs):
            looked.append(1)
            return original(*args, **kwargs)

        rules._check_jump = counting
        self.addCleanup(setattr, rules, "_check_jump", original)
        rules.check_commands(self.project, [1],
                             self.lint._Tables(self.project),
                             self.analysis.WalkBudget())
        self.assertTrue(looked, "the counting wrapper never ran, so this "
                                "test measured the unpatched rule")
        # A handful past the ceiling, not 3000 x 500 of them.
        self.assertLess(len(looked), 100,
                        "the command walk examined %d commands to report %d "
                        "findings" % (len(looked), self.CEILING))

    def test_the_ceiling_is_still_what_bounds_the_findings(self):
        """Both halves: what is kept, and what is visited."""
        self.craft_shared_events(self.shadowing_pages())
        found = self.lint.check_event_pages(self.project, [1],
                                            self.analysis.WalkBudget())
        # Exactly the ceiling: 3000 shadowed events fill a ceiling of 4, and
        # "at most" is also true of a walk that found nothing.
        self.assertEqual(len(found), self.CEILING)


class TestAFindingNamesTheRightCommand(ValidateTest):
    """The index a finding reports is the index of the command that is wrong.

    Each rule takes the command list's location and copies it at the index
    where it reports, rather than the loop building one per command. The
    only test of that crafted a one-command page and asserted "command 0",
    which `location.at(0)`, `location.at(anything)` on a one-command list,
    and any index-independent expression all satisfy equally.

    So the bad command sits at index 3 here, with three innocent commands
    before it, and each of the three command rules is checked.
    """

    def craft(self, bad):
        """A page of three comments, then `bad`, then a blank terminator."""
        _, _, event = self.first_event(1)
        page = event.get("pages")[0]
        commands = []
        for _ in range(3):
            commands.append(new_event_command(108, 0, [self.project.codec.make_string("x")]))
        commands.append(bad)
        commands.append(new_event_command())
        page.set("list", commands)
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="crafted")
        self.ctx = self.reopen()

    def where(self, rule):
        rows = [f for f in self.findings(rule=rule)]
        self.assertTrue(rows, "the %s rule reported nothing" % rule)
        return rows[0]

    def command(self, code, parameters, indent=0):
        return new_event_command(code, indent, parameters)

    def test_a_missing_label_names_index_three(self):
        self.craft(self.command(
            119, [self.project.codec.make_string("nowhere")]))
        found = self.where("missing_label")
        self.assertIn("command 3", found["where"])
        self.assertEqual(found["location"]["index"], 3)

    def test_an_indent_jump_names_index_three(self):
        self.craft(self.command(108, [
            self.project.codec.make_string("x")], indent=5))
        found = self.where("indent_jump")
        self.assertIn("command 3", found["where"])
        self.assertEqual(found["location"]["index"], 3)

    def test_a_missing_reference_names_index_three(self):
        """A Transfer Player to a map that is not there."""
        self.craft(self.command(201, [0, 9999, 1, 1, 0]))
        found = self.where("transfer_to_missing_map")
        self.assertIn("command 3", found["where"])
        self.assertEqual(found["location"]["index"], 3)


class TestEveryCollectorIsShared(unittest.TestCase):
    """Every function that collects findings collects into the run's list.

    `MAX_FINDINGS` bounds findings *collected*, and a check that keeps a
    list of its own has a ceiling of its own -- so two of them are a ceiling
    of twice that, which is the sentence `_parent_cycles` uses and the
    defect this package has fixed four times: `_check_encounters`,
    `_parent_cycles`, `check_tilesets` and, until this round,
    `_check_system`.

    `TestEveryLintWalkTakesAnAllowance` is the same idea for the walk
    budget, and it is deliberately a scan rather than a list of names,
    because the older checks enumerate by name and cannot see a walk nobody
    thought to add. This is the ceiling's half of that, and it did not
    exist: `MAX_FINDINGS`' own comment claimed the walk scan covered it,
    which it does not -- that test asks about `budget`.

    A function is in scope if it appends to a list it made itself *and*
    loops. The escape hatch is a reason, as it is next door.
    """

    # Functions that build their own list of findings and legitimately do
    # not take the run's collector. A reason each.
    #
    # Two entries, not nine. The first version listed nine, and seven of
    # them could never have been flagged -- `_check_indent`, `_check_jump`
    # and the rest neither loop nor build a list, so they are not in scope
    # and an excuse for them says nothing. That is the same dilution that
    # made the older by-name lists unreadable, in the test written to
    # replace them.
    OWN_LIST_IS_FINE = {
        "_check_references": "loops over the rules one command code has, "
                             "which is this server's own table, so the "
                             "length is bounded by the source",
        "validate": "the caller that makes the collector",
    }

    def functions(self):
        """Every top-level function of the linter, with its source."""
        package = (Path(__file__).resolve().parent.parent / "src"
                   / "rpgvxace_mcp" / "analysis" / "lint")
        found = sorted(path for path in package.glob("*.py")
                       if path.name != "__init__.py")
        self.assertGreater(len(found), 1, "found no linter modules")
        for path in found:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    yield path.stem, node, ast.unparse(node)

    def collectors(self):
        """Functions that both loop and build a findings list of their own."""
        out = {}
        for stem, node, body in self.functions():
            loops = any(isinstance(inner, (ast.For, ast.While))
                        for inner in ast.walk(node))
            # A collector this function made, rather than one it was
            # handed. `_Findings.given` is the package's one spelling of
            # "the caller's, or a fresh one", so calling it is what counts
            # as sharing -- the two spellings of that idiom are why this
            # test is keyed off the call and not off the assignment.
            owns = (("findings = []" in body or "_Findings()" in body)
                    and "_Findings.given" not in body)
            if loops and owns:
                out[node.name] = stem
        return out

    def test_every_collecting_walk_shares_the_run_list_or_is_excused(self):
        unshared = sorted(
            "%s.%s" % (stem, name)
            for name, stem in self.collectors().items()
            if name not in self.OWN_LIST_IS_FINE)
        self.assertEqual(
            unshared, [],
            "these loop and collect into a list of their own, so each has a "
            "ceiling of its own on top of the run's: " + ", ".join(unshared))

    def test_no_excuse_outlives_its_function(self):
        """A reason for a function that no longer collects is a stale reason.

        Against the collectors, not against every function in the package:
        this compared the table to *all* the names, so an excuse survived
        its function stopping collecting, and seven entries that were never
        in scope at all read as nine checked exemptions.
        """
        collecting = set(self.collectors())
        self.assertTrue(collecting, "found no collectors, so this checked "
                                    "nothing")
        stale = sorted(set(self.OWN_LIST_IS_FINE) - collecting)
        self.assertEqual(stale, [],
                         "excused and no longer collecting: "
                         + ", ".join(stale))

    # Classes that hold a findings list of their own, and why. The scan
    # above walks module-level functions, so a collector that is an
    # *object* is invisible to it -- and that is the shape the history is
    # about: `check_tilesets`' bug was that it guarded `findings` while its
    # loop appended to `checker.findings`.
    COLLECTOR_CLASSES = {
        "_Findings": "the run's collector itself -- the one every rule is "
                     "handed, and the thing the other entries here are "
                     "measured against",
        "_AssetChecker": "four walks share one, which is why the ceiling "
                         "lives on the collector rather than at the call "
                         "sites -- the one site that carried the guard had "
                         "written it against the wrong list. `validate` "
                         "builds one for the whole run and every walk that "
                         "collects assets takes it.",
    }

    def collector_classes(self):
        """Every class in the linter holding a `list[Finding]` attribute."""
        package = (Path(__file__).resolve().parent.parent / "src"
                   / "rpgvxace_mcp" / "analysis" / "lint")
        found = {}
        for path in sorted(package.glob("*.py")):
            if path.name == "__init__.py":
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if not isinstance(node, ast.ClassDef):
                    continue
                body = ast.unparse(node)
                if "list[Finding] = []" in body or ": list[Finding]" in body:
                    found[node.name] = path.stem
        return found

    def test_every_collector_class_is_accounted_for(self):
        """The shape the function scan cannot see, named and reasoned about.

        Not a defect on its own -- an object that owns a ceiling is how
        `_AssetChecker` is *meant* to work. What was wrong is that no test
        could see it, so the one collector still standing outside the run's
        was invisible to the rule written to find exactly that.
        """
        found = self.collector_classes()
        self.assertTrue(found, "found no collector classes, so this "
                               "checked nothing")
        unexplained = sorted(set(found) - set(self.COLLECTOR_CLASSES))
        self.assertEqual(
            unexplained, [],
            "these hold a findings list of their own and nothing says why: "
            + ", ".join("%s (%s)" % (n, found[n]) for n in unexplained))

    def test_no_reason_outlives_its_class(self):
        idle = sorted(set(self.COLLECTOR_CLASSES)
                      - set(self.collector_classes()))
        self.assertEqual(idle, [],
                         "explained and no longer a collector: "
                         + ", ".join(idle))

    def test_the_shared_collector_is_what_bounds_a_whole_run(self):
        """`_Findings.given` is the mechanism, so it has to be reachable."""
        from rpgvxace_mcp.analysis.lint.findings import _Findings
        shared = _Findings()
        self.assertIs(_Findings.given(shared), shared,
                      "given() made a second collector out of one")
        self.assertIsInstance(_Findings.given(None), _Findings)


class TestTheSystemFileIsChecked(ValidateTest):
    """A start map that is not there, and a party member who is not.

    Neither branch had a test. The fixture sets `start_map_id=1` with
    Map001 present and `party_members=[1, 2]` with both actors present, and
    the one suite that crafts forty dangling party members lowers the
    finding ceiling to 6 first -- so the maps scope fills it and
    `check_database` is never reached.

    That is how round 10 shipped `findings.append` on a `_Findings`, which
    has `add`: converting `_check_system` to the shared collector, running
    934 tests, and seeing them all pass. `validate` returned
    `"'_Findings' object has no attribute 'append'"` for any project whose
    System named an absent start map -- and "the game will not boot" is
    that finding's own remedy text, so it is the breakage most worth
    reporting and the one that reported an internal error instead.
    """

    def system(self):
        return self.project.load_data("system")

    def saved(self):
        self.project.save_file(self.project.filename_for("system"),
                               tool="test", summary="crafted system")
        self.ctx = self.reopen()

    def test_a_start_map_that_does_not_exist_is_reported(self):
        absent = max(self.project.map_ids_present()) + 10
        self.system().set("start_map_id", absent)
        self.saved()
        rules = {f["rule"] for f in self.findings()}
        self.assertIn("missing_start_map", rules,
                      "the start map rule reported nothing; rules were %s"
                      % sorted(rules))

    def test_a_party_member_who_is_not_an_actor_is_reported(self):
        actors = {entry_id for entry_id, _ in self.project.entries("actors")}
        self.system().set("party_members", [max(actors) + 5])
        self.saved()
        rules = {f["rule"] for f in self.findings()}
        self.assertIn("missing_party_member", rules,
                      "the party rule reported nothing; rules were %s"
                      % sorted(rules))

    def test_neither_rule_answers_with_an_error(self):
        """The shape of the round-10 regression: a reply, not a traceback.

        `validate` catches nothing, so the failure arrived as an error
        reply carrying an AttributeError -- and `_tool_error` appends
        "Check the arguments, then run validate", pointing the model at the
        tool that had just died.
        """
        absent = max(self.project.map_ids_present()) + 10
        self.system().set("start_map_id", absent)
        actors = {entry_id for entry_id, _ in self.project.entries("actors")}
        self.system().set("party_members", [max(actors) + 5])
        self.saved()
        out = self.call("validate")
        self.assertIn("findings", out)
        rules = {f["rule"] for f in out["findings"]}
        self.assertIn("missing_start_map", rules)
        self.assertIn("missing_party_member", rules)

    def test_both_findings_name_where_they_came_from(self):
        absent = max(self.project.map_ids_present()) + 10
        self.system().set("start_map_id", absent)
        self.saved()
        found = [f for f in self.findings(rule="missing_start_map")]
        self.assertTrue(found)
        self.assertIn("system", found[0]["where"])


class TestAnUnreadableMapInfosIsReported(ValidateTest):
    """A corrupt MapInfos is a finding, not a silence.

    `check_map_tree`'s unreadable branch returned a bare `[Finding(...)]`
    while every other path in it appends to the collector -- and `validate`
    calls it for effect only, reading the collector afterwards. So the one
    file whose loss stops the whole map scope produced nothing at all: no
    finding, no note, and `checks_run` still claiming the map tree was
    checked.

    The standalone return value was right, which is why the direct-call
    tests passed. This is the second time this function's reporting was
    written against a list `validate` does not read, so
    `TestEveryCollectorReturnsWhatItCollected` below is the rule rather
    than a third one-off fix.
    """

    def test_a_corrupt_map_infos_is_a_finding(self):
        self.corrupt("MapInfos")
        rules = {f["rule"] for f in self.findings(scopes=["maps"])}
        self.assertIn("map_infos_unreadable", rules,
                      "a corrupt MapInfos reported nothing; rules were %s"
                      % sorted(rules))

    def test_it_says_what_to_do_about_it(self):
        self.corrupt("MapInfos")
        found = [f for f in self.findings(rule="map_infos_unreadable")]
        self.assertTrue(found)
        self.assertIn("Restore", found[0]["message"])

    def test_a_default_run_reports_it_too(self):
        """Not only the maps scope -- the default run is what a caller makes."""
        self.corrupt("MapInfos")
        rules = {f["rule"] for f in self.call("validate")["findings"]}
        self.assertIn("map_infos_unreadable", rules)


class TestEveryCollectorReturnsWhatItCollected(unittest.TestCase):
    """A rule takes the collector and returns from it, not from a local.

    `check_map_tree` took `collected` and one of its branches returned a
    list literal it had built on the spot -- so the finding existed, the
    direct-call test saw it, and `validate`, which reads the collector,
    did not. `TestEveryCollectorIsShared` next door cannot see this shape:
    it looks for a function that *keeps* its own list, and this one built
    a list only inside a `return`.

    So: a function that takes `collected` returns `findings.items`, or
    nothing at all. Anything else is a finding a caller may or may not be
    shown depending on which of two call styles it used.
    """

    def rules(self):
        """Every function in the linter that takes a `collected` argument."""
        package = (Path(__file__).resolve().parent.parent / "src"
                   / "rpgvxace_mcp" / "analysis" / "lint")
        found = []
        for path in sorted(package.glob("*.py")):
            if path.name == "__init__.py":
                continue
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if not isinstance(node, ast.FunctionDef):
                    continue
                names = [a.arg for a in node.args.args]
                if "collected" in names or "findings" in names:
                    found.append((path.stem, node))
        return found

    def test_the_scan_finds_the_collector_taking_rules(self):
        """So the assertion below cannot pass by reading nothing."""
        names = {node.name for _, node in self.rules()}
        self.assertGreater(len(names), 3, sorted(names))
        self.assertIn("check_map_tree", names)

    def test_no_rule_returns_a_list_it_built_in_the_return(self):
        wrong = []
        for stem, node in self.rules():
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Return)
                        and isinstance(inner.value, ast.List)
                        and inner.value.elts):
                    wrong.append("%s.%s:%d"
                                 % (stem, node.name, inner.lineno))
        self.assertEqual(
            wrong, [],
            "these take a collector and return a list built in the return, "
            "so a caller reading the collector is not shown the finding: "
            + ", ".join(wrong))


class TestOneAssetCheckerForAWholeRun(ToolTest):
    """The count the ceiling comment claims, measured.

    `check_tilesets` built a second `_AssetChecker`, so a whole-scope run
    held two independently-bounded lists of five thousand where the comment
    said one -- and nothing inside its row loop appended to `findings`, so
    `findings.full` never became true there and the sheet walk could build
    a full five thousand for `findings.extend` to drop all but one of.

    Counted by construction, because the count is the claim.
    """

    def test_the_run_builds_one(self):
        """The claim the reason above makes, measured rather than asserted.

        `check_tilesets` built a second one, so a whole-scope run held two
        independently-bounded lists of five thousand where the comment said
        one. Counted by construction, because the count is the claim.
        """
        from rpgvxace_mcp.analysis.lint import assets
        built = []
        original = assets._AssetChecker.__init__

        def counting(inner, project):
            built.append(1)
            return original(inner, project)

        self.addCleanup(setattr, assets._AssetChecker, "__init__", original)
        assets._AssetChecker.__init__ = counting
        self.call("validate")
        self.assertEqual(built, [1],
                         "a whole-scope validate built %d asset checkers, "
                         "so the ceiling is %d times MAX_FINDINGS"
                         % (len(built), len(built)))


class TestAFullAssetCheckerDoesNotStopThePageRules(ToolTest):
    """`check_event_pages` must not return on `checker.full`, and here is why.

    Every other walk that holds an `_AssetChecker` tests `checker.full` and
    returns, and a reviewer twice proposed that this one should too. It must
    not: this walk owns `shadowed_pages` and `unstoppable_autorun`, which
    collect into `findings`, and the checker it carries is a passenger. A
    `return` on a full checker would silently delete two rules' findings for
    any project with more missing graphics than the ceiling holds --
    `_check_map_assets` can test it because there the checker is the only
    thing it collects into.

    Nothing was saved by the proposed test either: the walk is already
    bounded by `findings.full`, by the shared `WalkBudget`, and by
    `_AssetChecker.check` returning on its own first line before it decodes
    a name or builds a `Location`.

    Written as a test rather than a comment because the next reviewer will
    propose it again, and a comment saying "do not" is not a thing that
    fails.
    """

    def test_the_page_rules_still_report_with_a_full_checker(self):
        from rpgvxace_mcp.analysis.lint.assets import _AssetChecker
        from rpgvxace_mcp.analysis.lint.findings import Finding, WARNING
        from rpgvxace_mcp.analysis.lint.pages import check_event_pages
        from rpgvxace_mcp.analysis.walk import WalkBudget
        # An unconditional second page, which hides page 0.
        self.call("event_edit", map_id=1, ops=[
            {"op": "add_page", "event_id": 1,
             "commands": [{"text": "unreachable"}]}])
        project = self.reopen().project
        self.lower_the_finding_ceiling(1)
        checker = _AssetChecker(project)
        checker.findings.append(Finding(
            WARNING, "filler", "one finding, and the ceiling is one"))
        self.assertTrue(checker.full, "the ceiling was not lowered, so this "
                                      "would pass with any behaviour")
        found = check_event_pages(project, project.map_ids_on_disk(),
                                  WalkBudget(), None, checker)
        self.assertIn("shadowed_pages", {f.rule for f in found})


# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so running this file directly
# called `unittest.main()` (which exits) before the classes below it
# existed, and reported success having skipped them. pytest collects
# either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()
