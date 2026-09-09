"""Guards on how many findings `validate` may collect, and from where.

A ceiling on findings *collected*, not returned: a project crafted
to produce a finding per event command would otherwise build
millions of them in order to report two hundred. Every rule that
appends inside a loop is in scope, and the scanning tests here are
what catch a rule nobody thought to bound.
"""

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))


from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.analysis import MAX_FINDINGS  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
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
        import ast
        for path in self.modules():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    yield path.stem, node

    def looping_functions(self):
        """Every top-level function in the linter with a loop in it."""
        import ast
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
        import ast
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
        jump = RubyObject("RPG::EventCommand")
        jump.set("code", 119)                 # Jump to Label, with no Label
        jump.set("indent", 0)
        jump.set("parameters", [self.project.codec.make_string("nowhere")])
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
        self.assertLessEqual(len(found), self.CEILING)


# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so running this file directly
# called `unittest.main()` (which exits) before the classes below it
# existed, and reported success having skipped them. pytest collects
# either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()
