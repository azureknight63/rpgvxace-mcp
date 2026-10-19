"""`references` and `refactor`: where something is used, and moving it safely.

`references` answers "where is item 12 / switch 40 / map 4 used?" from the
same scan `refactor` rewrites from, so what one reports is exactly what the
other would change. The expectations in the first half are counted by hand
from the demo project's contents (`tests/fixtures/build_project.py`), not
read back from the tool.
"""

import sys
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.analysis import (  # noqa: E402
    REFERENCE_RULES,
    WalkBudget,
)
from rpgvxace_mcp.rubytypes import RubyHash, RubyObject  # noqa: E402
from rpgvxace_mcp.schema import (  # noqa: E402
    new_event,
    new_event_command,
)
from rpgvxace_mcp.tools import reference_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import REGISTRY, ToolError  # noqa: E402

from tool_case import ToolTest  # noqa: E402


class ReferenceCase(ToolTest):
    """Helpers that put known references into the demo project."""

    def command(self, code, *parameters, indent=0):
        # Text goes in as the project writes it: a RubyString carrying the
        # engine's encoding record, which Marshal will not guess for a str.
        return new_event_command(code, indent, [
            self.project.codec.make_string(p) if isinstance(p, str) else p
            for p in parameters])

    def write_map(self, map_id, game_map):
        path = self.project.path_for(self.project.map_file(map_id))
        path.write_bytes(marshal_rb.dump(game_map))

    def add_event(self, commands, map_id=1, name="Probe", pages=1):
        """Add an event of ``pages`` pages that all run ``commands``.

        Returns the event id. The commands are shared between the pages as
        one list object, which is how the editor's own copy-paste of a page
        reads back after a Marshal round trip.
        """
        game_map = self.project.load_map(map_id)
        events = game_map.get("events")
        event_id = max([k for k in events.keys()] or [0]) + 1
        event = new_event(event_id, name)
        shared = list(commands) + [self.command(0)]
        first = event.get("pages")[0]
        first.set("list", shared)
        more = []
        for _ in range(pages - 1):
            extra = marshal_rb.load(marshal_rb.dump(first))
            extra.set("list", shared)
            more.append(extra)
        event.set("pages", [first] + more)
        events.set(event_id, event)
        self.write_map(map_id, game_map)
        self.ctx = self.reopen()
        self.project = self.ctx.project
        return event_id

    def edit_data(self, stem, change):
        """Load a data file, let ``change`` mutate it, write it back."""
        name = stem + self.extension
        value = marshal_rb.load(self.project.path_for(name).read_bytes())
        change(value)
        self.project.path_for(name).write_bytes(marshal_rb.dump(value))
        self.ctx = self.reopen()
        self.project = self.ctx.project

    def refs(self, kind, id, **more):
        return self.call("references", kind=kind, id=id, **more)

    def where(self, reply):
        return [site["where"] for site in reply["sites"]]


class TestTheToolIsAdvertisedHonestly(ReferenceCase):
    def test_the_kinds_are_the_rule_index_plus_the_rest(self):
        enum = REGISTRY["references"].schema["properties"]["kind"]["enum"]
        # Everything the command rule index supports ...
        self.assertTrue(set(REFERENCE_RULES) <= set(enum),
                        sorted(set(REFERENCE_RULES) - set(enum)))
        # ... plus the numbered namespaces and the tables the rule index
        # does not key (a skill is named by five commands it does not list).
        self.assertTrue({"switches", "variables", "skills", "maps",
                         "common_events"} <= set(enum))
        from rpgvxace_mcp.analysis import REFERENCE_KINDS
        self.assertEqual(sorted(enum), sorted(REFERENCE_KINDS))

    def test_every_ceiling_is_a_constant_and_is_advertised(self):
        description = REGISTRY["references"].description
        schema = REGISTRY["references"].schema["properties"]
        self.assertEqual(schema["limit"]["maximum"], reference_tools.MAX_SITES)
        self.assertEqual(schema["offset"]["maximum"],
                         reference_tools.MAX_OFFSET)
        self.assertIn(str(reference_tools.MAX_SITES), description)
        self.assertIn(str(reference_tools.MAX_OFFSET), description)
        self.assertIn("read-only", description.lower())

    def test_it_is_a_read_only_tool(self):
        self.assertFalse(REGISTRY["references"].writes)
        self.assertTrue(REGISTRY["refactor"].writes)


class TestWhereAnEntryIsUsed(ReferenceCase):
    """Counted by hand from the demo project."""

    def test_item_1_is_a_command_and_two_enemy_drops(self):
        reply = self.refs("items", 1)
        self.assertEqual(reply["total"], 3)
        self.assertEqual(sorted(self.where(reply)), sorted([
            "map 2 event 1 (Chest) page 0 command 0",
            "enemies 1 field drop_items[0].data_id",
            "enemies 2 field drop_items[0].data_id",
        ]))
        by_where = {site["where"]: site["what"] for site in reply["sites"]}
        self.assertEqual(by_where["map 2 event 1 (Chest) page 0 command 0"],
                         "Change Items")
        self.assertEqual(by_where["enemies 1 field drop_items[0].data_id"],
                         "drop_items[0].data_id")

    def test_an_unused_item_has_no_sites_and_still_says_what_was_read(self):
        reply = self.refs("items", 2)
        self.assertEqual(reply["total"], 0)
        self.assertEqual(reply["sites"], [])
        self.assertIn("enemy drop items", reply["coverage"]["scanned"])

    def test_switches_are_read_from_conditions_branches_and_writes(self):
        # Switch 1: the Elder's Conditional Branch (command 3 of page 0).
        one = self.refs("switches", 1)
        self.assertEqual(self.where(one),
                         ["map 1 event 1 (Elder) page 0 command 3"])
        self.assertEqual(one["sites"][0]["what"],
                         "Conditional Branch (switch)")
        # Switch 2: the Control Switches that follows it (command 6).
        two = self.refs("switches", 2)
        self.assertEqual(self.where(two),
                         ["map 1 event 1 (Elder) page 0 command 6"])
        self.assertEqual(two["sites"][0]["what"], "Control Switches")
        # Switch 3: the Elder's second page only runs while it is on.
        three = self.refs("switches", 3)
        self.assertEqual(self.where(three),
                         ["map 1 event 1 (Elder) page 1 condition"])
        self.assertEqual(three["sites"][0]["what"],
                         "map event page conditions: switch1_id")

    def test_an_inactive_page_condition_is_not_a_reference(self):
        # Every new page carries switch 1 in its switch1_id with the flag
        # off. Counting those would make every switch 1 look used everywhere.
        reply = self.refs("switches", 1)
        self.assertEqual(reply["total"], 1)

    def test_a_range_write_covers_the_switches_inside_it(self):
        self.add_event([self.command(121, 2, 5, 0)])
        reply = self.refs("switches", 4)
        self.assertEqual(reply["total"], 1)
        self.assertEqual(reply["sites"][0]["what"], "Control Switches 2..5")
        self.assertEqual(reply["sites"][0]["range"], [2, 5])

    def test_variables_in_every_place_a_command_can_name_one(self):
        self.add_event([
            self.command(122, 3, 3, 0, 0, 5),          # variable 3 = 5
            self.command(111, 1, 3, 0, 5, 1),          # if variable 3 >= 5
            self.command(122, 4, 4, 0, 1, 3),          # variable 4 = variable 3
            self.command(401, "Gold: \\V[3]"),           # message text
            self.command(125, 0, 1, 3),                # gold: from variable 3
        ])
        reply = self.refs("variables", 3)
        whats = sorted(site["what"] for site in reply["sites"])
        self.assertEqual(whats, sorted([
            "Control Variables",
            "Conditional Branch (variable)",
            "Control Variables (operand variable)",
            "message text \\V[n]",
            "Change Gold (variable operand)",
        ]))
        self.assertEqual(reply["total"], 5)

    def test_a_common_event_is_called_and_used_as_an_item_effect(self):
        self.add_event([self.command(117, 1)])

        def effect(items):
            items[2].get("effects").append(RubyObject(
                "RPG::UsableItem::Effect",
                {"@code": 44, "@data_id": 1, "@value1": 0.0, "@value2": 0.0}))
        self.edit_data("Items", effect)
        reply = self.refs("common_events", 1)
        self.assertEqual(reply["total"], 2)
        self.assertEqual(sorted(site["what"] for site in reply["sites"]),
                         ["Call Common Event", "effects[1].data_id"])

    def test_a_map_is_named_by_transfers_vehicles_and_the_system(self):
        self.add_event([self.command(202, 0, 0, 2, 3, 3)], map_id=2)
        reply = self.refs("maps", 2)
        self.assertEqual(sorted(self.where(reply)), sorted([
            "map 1 event 2 (Cave Entrance) page 0 command 1",
            "map 2 event 2 (Probe) page 0 command 0",
        ]))
        # Map 1 is the start map, the last-edited map and map 2's parent.
        start = self.refs("maps", 1)
        self.assertEqual(sorted(self.where(start)), sorted([
            "system 0 field start_map_id",
            "system 0 field edit_map_id",
            "map_infos 2 field parent_id",
        ]))

    def test_a_skill_is_a_learning_an_enemy_action_and_a_command(self):
        self.add_event([self.command(318, 0, 1, 0, 2)])
        two = self.refs("skills", 2)
        self.assertEqual(sorted(self.where(two)), sorted([
            "classes 2 field learnings[0].skill_id",
            "map 1 event 3 (Probe) page 0 command 0",
        ]))
        one = self.refs("skills", 1)
        self.assertEqual(sorted(self.where(one)), sorted([
            "classes 1 field learnings[0].skill_id",
            "enemies 1 field actions[0].skill_id",
            "enemies 2 field actions[0].skill_id",
        ]))

    def test_a_troop_is_an_encounter_a_battle_and_the_test_troop(self):
        self.add_event([self.command(301, 0, 2, 1, 0)])
        reply = self.refs("troops", 2)
        self.assertEqual(sorted(self.where(reply)), sorted([
            "map 1 event 3 (Probe) page 0 command 0",
            "map 2 field encounter_list[1].troop_id",
        ]))
        self.assertEqual(sorted(self.where(self.refs("troops", 1))), sorted([
            "map 2 field encounter_list[0].troop_id",
            "system 0 field test_troop_id",
        ]))


class TestScriptsAreMentionsNotReferences(ReferenceCase):
    def test_a_script_naming_the_number_is_counted_apart(self):
        self.add_event([self.command(355, "$game_switches[40] = true")])
        reply = self.refs("switches", 40)
        self.assertEqual(reply["total"], 0)
        self.assertEqual(reply["sites"], [])
        self.assertEqual(reply["script_mentions"]["count"], 1)
        self.assertEqual(reply["script_mentions"]["listed"][0]["where"],
                         "map 1 event 3 (Probe) page 0 command 0")
        self.assertIn("text matches", reply["script_mentions"]["note"])
        self.assertIn("not references", reply["script_mentions"]["note"])

    def test_a_longer_number_is_not_a_mention_of_a_shorter_one(self):
        self.add_event([self.command(355, "$game_switches[400] = true")])
        self.assertEqual(self.refs("switches", 40)["script_mentions"]["count"],
                         0)

    def test_the_coverage_object_says_scripts_cannot_be_seen(self):
        coverage = self.refs("items", 2)["coverage"]
        self.assertTrue(any("Ruby scripts" in line
                            for line in coverage["not_scanned"]))
        self.assertTrue(any("plug-in" in line
                            for line in coverage["not_scanned"]))

    def test_weapons_are_not_complete_and_the_coverage_says_why(self):
        coverage = self.refs("weapons", 1)["coverage"]
        self.assertTrue(any("starting equipment" in line
                            for line in coverage["not_scanned"]))


class TestTheArgumentsAreStrictlyRead(ReferenceCase):
    def test_each_wrong_id_is_a_clean_refusal(self):
        for bad, message in (
                (10 ** 5000, "id must be at most"),
                (-10 ** 5000, "id must be at least"),
                (-5, "id must be at least 1, got -5"),
                (0, "id must be at least 1, got 0"),
                (True, "id must be a whole number, not true or false"),
                (1.5, "id must be a whole number, got 1.5"),
                ([1], "id must be an integer, got list"),
                ("7", "id must be an integer, got str"),
                ({"a": 1}, "id must be an integer, got dict")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ToolError, message):
                    self.call("references", kind="items", id=bad)

    def test_a_missing_id_and_a_missing_kind_are_refused(self):
        with self.assertRaisesRegex(ToolError, "id is required"):
            self.call("references", kind="items")
        with self.assertRaisesRegex(ToolError, "kind is required"):
            self.call("references", id=1)

    def test_an_unknown_kind_lists_the_ones_that_exist(self):
        with self.assertRaisesRegex(ToolError, "kind must be one of .*items"):
            self.call("references", kind="elements", id=1)

    def test_a_switch_id_above_the_engines_ceiling_is_refused(self):
        with self.assertRaisesRegex(ToolError, "switches are numbered 1 to "
                                               "5000"):
            self.call("references", kind="switches", id=5001)

    def test_a_map_id_above_the_ceiling_is_refused(self):
        with self.assertRaisesRegex(ToolError, "id must be at most"):
            self.call("references", kind="maps", id=10000)

    def test_limit_and_offset_are_bounded(self):
        for name, value, message in (
                ("limit", 0, "limit must be at least 1"),
                ("limit", reference_tools.MAX_SITES + 1, "limit must be at most"),
                ("offset", -1, "offset must be at least 0"),
                ("offset", reference_tools.MAX_OFFSET + 1,
                 "offset must be at most"),
                ("limit", True, "limit must be a whole number"),
                ("limit", 2.5, "limit must be a whole number, got 2.5")):
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(ToolError, message):
                    self.call("references", kind="items", id=1,
                              **{name: value})


class TestTheAnswerIsBoundedAndSaysSo(ReferenceCase):
    def test_a_window_of_the_sites_is_marked_truncated(self):
        reply = self.refs("items", 1, limit=1)
        self.assertEqual(reply["total"], 3)
        self.assertEqual(len(reply["sites"]), 1)
        self.assertIs(reply["sites_truncated"], True)

    def test_an_offset_pages_through_the_rest(self):
        everything = self.where(self.refs("items", 1))
        second = self.refs("items", 1, limit=2, offset=1)
        self.assertEqual(self.where(second), everything[1:3])
        self.assertNotIn("sites_truncated", second)

    def test_a_full_page_that_is_all_of_them_is_not_truncated(self):
        reply = self.refs("items", 1, limit=3)
        self.assertEqual(len(reply["sites"]), 3)
        self.assertNotIn("sites_truncated", reply)

    def test_a_walk_that_runs_out_says_it_is_incomplete(self):
        with mock.patch.object(reference_tools, "WalkBudget",
                               lambda: WalkBudget(2)):
            reply = self.refs("items", 1)
        self.assertIn("references_stopped", reply)
        self.assertIn("incomplete", reply["references_stopped"])

    def test_a_list_shared_by_two_pages_is_two_places_it_is_used(self):
        self.add_event([self.command(117, 1)], pages=2)
        reply = self.refs("common_events", 1)
        self.assertEqual(sorted(self.where(reply)), [
            "map 1 event 3 (Probe) page 0 command 0",
            "map 1 event 3 (Probe) page 1 command 0"])

    def test_an_unreadable_map_is_reported_not_skipped(self):
        path = self.project.path_for(self.project.map_file(2))
        path.write_bytes(b"\x04\x08 not marshal")
        self.ctx = self.reopen()
        reply = self.refs("items", 1)
        self.assertIn("references_not_read", reply)
        self.assertIn("map 2", reply["references_not_read"])


class TestAnUnreadableFileIsNotAnEmptyAnswerOnTheDeletePaths(ReferenceCase):
    """A defect in the existing scan, found while building this one.

    `iter_command_lists` skips a map or table it cannot read, silently, and
    the two delete previews are built on it: with one map unparseable, "no
    event points at this" was reported as a finished scan, and the delete it
    gates went ahead on the strength of a map nobody had looked in.
    """

    def corrupt(self, map_id):
        path = self.project.path_for(self.project.map_file(map_id))
        path.write_bytes(b"\x04\x08 not marshal")
        self.ctx = self.reopen()
        self.project = self.ctx.project

    def test_a_db_delete_preview_says_a_map_was_not_read(self):
        self.corrupt(1)
        reply = self.call("db_edit", table="items", dry_run=True,
                          ops=[{"op": "delete", "id": 2}])
        row = reply["would_apply"][0]
        self.assertIs(row["references_checked"], False)
        self.assertIn("map 1", row["references_scan_stopped"])
        self.assertIn("could not be read", row["references_scan_stopped"])

    def test_a_map_delete_preview_says_so_too(self):
        self.corrupt(1)
        reply = self.call("map_edit", op="delete", map_id=2, dry_run=True)
        self.assertIn("map 1", reply["incoming_transfers_scan_stopped"])

    def test_a_readable_project_still_reports_a_finished_scan(self):
        reply = self.call("db_edit", table="items", dry_run=True,
                          ops=[{"op": "delete", "id": 2}])
        row = reply["would_apply"][0]
        self.assertIs(row["references_checked"], True)
        self.assertNotIn("references_scan_stopped", row)


class TestAgainstSwitchUsage(ReferenceCase):
    """`switch_usage` and `references` read the same project two ways."""

    def test_every_switch_it_says_is_read_or_written_is_found(self):
        from rpgvxace_mcp.analysis import map_switch_usage
        usage = map_switch_usage(self.project)
        for kind in ("switches", "variables"):
            seen = {row["id"] for row in usage[kind]["read_but_never_set"]["shown"]}
            seen |= {row["id"] for row in usage[kind]["set_but_never_read"]["shown"]}
            for ident in seen:
                with self.subTest(kind=kind, ident=ident):
                    self.assertGreaterEqual(self.refs(kind, ident)["total"], 1)


class RefactorCase(ReferenceCase):
    """Helpers for the tests that move things."""

    def data_files(self):
        """Every file under Data/, by name, as bytes."""
        folder = self.project.data_dir
        return {path.name: path.read_bytes() for path in sorted(folder.iterdir())
                if path.is_file()}

    def renumber(self, table, from_id, to_id, **more):
        return self.call("refactor", op="renumber", table=table,
                         from_id=from_id, to_id=to_id, **more)

    def fresh(self):
        """A new project object on the same folder, to read what is on disk."""
        self.ctx = self.reopen()
        self.project = self.ctx.project

    def findings(self):
        return self.call("validate")["findings"]

    def changed_files(self, before):
        after = self.data_files()
        return sorted(name for name in set(before) | set(after)
                      if before.get(name) != after.get(name))


class TestRenumberingAnEntry(RefactorCase):
    def test_a_preview_lists_every_site_and_writes_nothing(self):
        before = self.data_files()
        reply = self.renumber("items", 1, 7, dry_run=True)
        self.assertIs(reply["dry_run"], True)
        self.assertEqual(reply["sites_total"], 3)
        self.assertEqual(sorted(site["where"] for site in reply["sites"]),
                         sorted(self.where(self.refs("items", 1))))
        for site in reply["sites"]:
            self.assertEqual((site["before"], site["would_become"]), (1, 7))
        self.assertEqual(reply["would_write"],
                         ["Enemies.rvdata2", "Items.rvdata2", "Map002.rvdata2"])
        self.assertEqual(reply["blocked"], [])
        self.assertEqual(self.data_files(), before)
        self.assertEqual(self.refs("items", 1)["total"], 3)

    def test_the_entry_its_own_id_and_every_reference_move(self):
        before = self.data_files()
        reply = self.renumber("items", 1, 7)
        self.assertNotIn("dry_run", reply)
        self.assertEqual(reply["sites_total"], 3)
        self.fresh()
        items = self.project.load_data("items")
        self.assertEqual(len(items), 8)
        self.assertIsNone(items[1])
        self.assertEqual(items[7].get("id"), 7)
        self.assertEqual(items[7].get("name").data, b"Potion")
        # Everything that named item 1 now names item 7, and nothing is left
        # naming 1.
        moved = self.refs("items", 7)
        self.assertEqual(moved["total"], 3)
        self.assertEqual(self.refs("items", 1)["total"], 0)
        self.assertEqual(sorted(self.where(moved)), sorted([
            "map 2 event 1 (Chest) page 0 command 0",
            "enemies 1 field drop_items[0].data_id",
            "enemies 2 field drop_items[0].data_id"]))
        # Only the three files that held a reference, plus Items itself, were
        # written. Every other file is the same bytes it was.
        self.assertEqual(self.changed_files(before),
                         ["Enemies.rvdata2", "Items.rvdata2", "Map002.rvdata2"])

    def test_validate_reports_nothing_new_afterwards(self):
        self.assertEqual(self.findings(), [])
        self.renumber("items", 1, 7)
        self.fresh()
        self.assertEqual(self.findings(), [])

    def test_an_occupied_target_is_refused_and_nothing_changes(self):
        before = self.data_files()
        with self.assertRaisesRegex(ToolError, "items 2 is already taken"):
            self.renumber("items", 1, 2)
        self.assertEqual(self.data_files(), before)

    def test_a_missing_source_is_refused(self):
        with self.assertRaisesRegex(ToolError, "items has no entry 9 to move"):
            self.renumber("items", 9, 10)

    def test_the_same_id_is_refused(self):
        with self.assertRaisesRegex(ToolError, "nothing to move"):
            self.renumber("items", 1, 1)

    def test_a_target_far_past_the_end_is_refused(self):
        with self.assertRaisesRegex(ToolError, "to_id 5000 is too high"):
            self.renumber("items", 1, 5000)

    def test_an_entry_that_is_not_where_its_own_id_says_is_refused(self):
        def damage(items):
            items[1].set("id", 5)
        self.edit_data("Items", damage)
        with self.assertRaisesRegex(ToolError, "carries id 5"):
            self.renumber("items", 1, 7)

    def test_each_hostile_id_is_a_clean_refusal(self):
        for field in ("from_id", "to_id"):
            for bad, message in (
                    (10 ** 5000, "must be at most"),
                    (-3, "must be at least 1, got -3"),
                    (0, "must be at least 1, got 0"),
                    (True, "must be a whole number, not true or false"),
                    (2.5, "must be a whole number, got 2.5"),
                    ([1], "must be an integer, got list")):
                arguments = {"from_id": 1, "to_id": 7, field: bad}
                with self.subTest(field=field, message=message):
                    with self.assertRaisesRegex(ToolError,
                                                "%s %s" % (field, message)):
                        self.call("refactor", op="renumber", table="items",
                                  **arguments)

    def test_required_arguments_are_named(self):
        with self.assertRaisesRegex(ToolError, "from_id is required"):
            self.call("refactor", op="renumber", table="items", to_id=3)
        with self.assertRaisesRegex(ToolError, "table is required"):
            self.call("refactor", op="renumber", from_id=1, to_id=3)

    def test_an_argument_the_op_does_not_read_is_refused(self):
        with self.assertRaisesRegex(ToolError, "does not read 'name'"):
            self.call("refactor", op="renumber", table="items", from_id=1,
                      to_id=3, name="Potion")

    def test_a_long_preview_is_windowed_and_says_so(self):
        self.add_event([self.command(126, 1, 0, 0, n) for n in range(0, 60)])
        reply = self.renumber("items", 1, 7, dry_run=True, limit=10)
        self.assertEqual(reply["sites_total"], 63)
        self.assertEqual(len(reply["sites"]), 10)
        self.assertIs(reply["sites_truncated"], True)
        rest = self.renumber("items", 1, 7, dry_run=True, limit=200, offset=10)
        self.assertEqual(len(rest["sites"]), 53)
        self.assertNotIn("sites_truncated", rest)


class TestASwap(RefactorCase):
    def test_a_second_swap_puts_every_byte_back(self):
        before = self.data_files()
        self.call("refactor", op="renumber", table="items", from_id=1,
                  to_id=2, swap=True)
        self.fresh()
        self.assertEqual(self.project.load_data("items")[1].get("name").data,
                         b"Hi-Potion")
        self.assertEqual(self.project.load_data("items")[2].get("id"), 2)
        # The chest paid out item 1, which is now the other potion's slot.
        swapped = self.refs("items", 2)
        self.assertIn("map 2 event 1 (Chest) page 0 command 0",
                      self.where(swapped))
        self.call("refactor", op="renumber", table="items", from_id=1,
                  to_id=2, swap=True)
        self.assertEqual(self.data_files(), before)

    def test_swap_needs_both_entries(self):
        with self.assertRaisesRegex(ToolError, "items has no entry 9 to swap"):
            self.call("refactor", op="renumber", table="items", from_id=1,
                      to_id=9, swap=True)

    def test_a_list_shared_by_two_pages_is_rewritten_once_not_twice(self):
        # One command list on two pages. Visiting it twice and rewriting each
        # time would map item 1 to 2 and then 2 back to 1 -- a swap that does
        # nothing -- so the number is changed once per object, not per visit.
        self.add_event([self.command(126, 1, 0, 0, 1)], pages=2)
        self.call("refactor", op="renumber", table="items", from_id=1,
                  to_id=2, swap=True)
        self.fresh()
        game_map = self.project.load_map(1)
        event = game_map.get("events").get(3)
        lists = [page.get("list") for page in event.get("pages")]
        self.assertIs(lists[0], lists[1], "the fixture lost its sharing")
        self.assertEqual(lists[0][0].get("parameters")[0], 2)

    def test_the_preview_counts_a_shared_number_once_and_says_it_is_shared(self):
        self.add_event([self.command(126, 1, 0, 0, 1)], pages=2)
        reply = self.renumber("items", 1, 7, dry_run=True)
        # The chest, two drops and ONE shared parameter reached twice.
        self.assertEqual(reply["sites_total"], 4)
        self.assertEqual(reply["shared_locations"], 1)
        shared = [site for site in reply["sites"]
                  if site.get("also_reached_at")]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["also_reached_at"], 1)
        self.assertEqual(self.refs("items", 1)["total"], 5)


class TestSomeTablesAreRefusedBecauseTheyAreNotDeclaredComplete(RefactorCase):
    def test_every_other_table_names_what_would_be_missed(self):
        from rpgvxace_mcp.analysis import RENUMBER_COMPLETE
        declared = set(RENUMBER_COMPLETE["vx_ace"])
        refused = [t for t in reference_tools.RENUMBER_TABLES
                   if t not in declared]
        self.assertIn("weapons", refused)
        self.assertIn("actors", refused)
        before = self.data_files()
        for table in refused:
            with self.subTest(table=table):
                with self.assertRaises(ToolError) as caught:
                    self.renumber(table, 1, 5)
                message = str(caught.exception)
                self.assertTrue(
                    "renumber is not offered" in message
                    or "renumber table \"maps\"" in message, message)
        self.assertEqual(self.data_files(), before)

    def test_weapons_have_rules_and_are_refused_all_the_same(self):
        # `references` can answer for weapons; that is not a declaration that
        # the answer is complete.
        self.assertEqual(self.refs("weapons", 1)["total"], 0)
        with self.assertRaisesRegex(ToolError, "starting equipment"):
            self.renumber("weapons", 1, 5)

    def test_the_refusal_is_by_declaration_not_by_having_rules(self):
        from rpgvxace_mcp.analysis import ref_sites
        with mock.patch.dict(ref_sites.RENUMBER_COMPLETE["vx_ace"],
                             {"weapons": frozenset(
                                 ref_sites.scanned_labels("weapons",
                                                          "vx_ace"))}):
            # Declared, so it is no longer refused for that reason.
            self.assertTrue(ref_sites.renumber_support("vx_ace", "weapons")[0])
        self.assertFalse(ref_sites.renumber_support("vx_ace", "weapons")[0])

    def test_the_declaration_is_exactly_what_the_scanner_reads(self):
        from rpgvxace_mcp.analysis import RENUMBER_COMPLETE, ref_sites
        for table in RENUMBER_COMPLETE["vx_ace"]:
            with self.subTest(table=table):
                self.assertTrue(ref_sites.declaration_matches_scanner(
                    "vx_ace", table))
        self.assertEqual(set(RENUMBER_COMPLETE["vx_ace"]),
                         set(reference_tools.OFFERED_TABLES))

    def test_a_rule_added_without_review_breaks_the_declaration(self):
        from rpgvxace_mcp.analysis import ref_sites
        extra = ref_sites.FieldRule("a new place", ("actors",), ("x",),
                                    "items")
        with mock.patch.object(ref_sites, "FIELD_RULES",
                               ref_sites.FIELD_RULES + (extra,)):
            self.assertFalse(ref_sites.declaration_matches_scanner(
                "vx_ace", "items"))
        self.assertTrue(ref_sites.declaration_matches_scanner(
            "vx_ace", "items"))

    def test_every_table_with_a_rule_is_declared_or_has_its_gaps_written(self):
        from rpgvxace_mcp.analysis import (RENUMBER_COMPLETE, RENUMBER_UNCOVERED,
                                           command_rule_tables)
        ruled = set(command_rule_tables("vx_ace"))
        known = set(RENUMBER_COMPLETE["vx_ace"]) | set(RENUMBER_UNCOVERED)
        self.assertEqual(sorted(ruled - known), [],
                         "a table with reference rules is neither declared "
                         "complete nor listed with what it misses")

    def test_another_engine_is_refused_whole(self):
        from rpgvxace_mcp.analysis import renumber_support
        supported, why = renumber_support("xp", "items")
        self.assertFalse(supported)
        self.assertIn("only offered on VX Ace", why)

    def test_skills_one_and_two_cannot_move(self):
        with self.assertRaisesRegex(ToolError, "skill 1 is Attack"):
            self.renumber("skills", 1, 5)
        with self.assertRaisesRegex(ToolError, "skill 1 is Attack"):
            self.call("refactor", op="renumber", table="skills", from_id=2,
                      to_id=3, swap=True)


class TestOtherTables(RefactorCase):
    def test_a_common_event_follows_its_calls_and_effects(self):
        self.add_event([self.command(117, 1)])

        def effect(items):
            items[2].get("effects").append(RubyObject(
                "RPG::UsableItem::Effect",
                {"@code": 44, "@data_id": 1, "@value1": 0.0, "@value2": 0.0}))
        self.edit_data("Items", effect)
        before = self.data_files()
        self.renumber("common_events", 1, 4)
        self.fresh()
        events = self.project.load_data("common_events")
        self.assertEqual(events[4].get("id"), 4)
        self.assertEqual(events[4].get("name").data, b"Heal Party")
        self.assertIsNone(events[1])
        self.assertEqual(self.refs("common_events", 4)["total"], 2)
        self.assertEqual(self.refs("common_events", 1)["total"], 0)
        self.assertEqual(self.findings(), [])
        self.assertEqual(self.changed_files(before),
                         ["CommonEvents.rvdata2", "Items.rvdata2",
                          "Map001.rvdata2"])

    def test_a_skill_follows_learnings_actions_effects_and_commands(self):
        self.call("db_edit", table="skills",
                  ops=[{"op": "create", "id": 3, "clone_from": 2}])

        def point(classes):
            classes[1].get("learnings")[0].set("skill_id", 3)
        self.edit_data("Classes", point)

        def act(enemies):
            enemies[2].get("actions")[0].set("skill_id", 3)
        self.edit_data("Enemies", act)
        self.add_event([self.command(318, 0, 1, 0, 3)])
        total = self.refs("skills", 3)["total"]
        self.assertEqual(total, 3)
        self.renumber("skills", 3, 6)
        self.fresh()
        self.assertEqual(self.refs("skills", 6)["total"], 3)
        self.assertEqual(self.refs("skills", 3)["total"], 0)
        self.assertEqual(self.project.load_data("skills")[6].get("id"), 6)
        self.assertEqual(self.findings(), [])

    def test_a_troop_follows_encounters_the_test_troop_and_battles(self):
        self.add_event([self.command(301, 0, 2, 1, 0)])
        before = self.data_files()
        self.renumber("troops", 2, 5)
        self.fresh()
        self.assertEqual(sorted(self.where(self.refs("troops", 5))), sorted([
            "map 1 event 3 (Probe) page 0 command 0",
            "map 2 field encounter_list[1].troop_id"]))
        self.assertEqual(self.project.load_data("troops")[5].get("id"), 5)
        self.assertEqual(self.findings(), [])
        self.assertEqual(self.changed_files(before),
                         ["Map001.rvdata2", "Map002.rvdata2",
                          "Troops.rvdata2"])
        # Troop 1 is the System's test troop: moving it moves that too.
        self.renumber("troops", 1, 7)
        self.fresh()
        self.assertEqual(self.project.load_data("system").get("test_troop_id"),
                         7)


class TestRenumberingAMap(RefactorCase):
    def test_the_file_the_tree_key_and_every_transfer_follow(self):
        before = self.data_files()
        moved_bytes = before["Map002.rvdata2"]
        reply = self.renumber("maps", 2, 5)
        self.assertEqual(reply["blocked"], [])
        self.fresh()
        files = self.data_files()
        self.assertNotIn("Map002.rvdata2", files)
        self.assertIn("Map005.rvdata2", files)
        # The map itself is the same bytes under a new name: nothing in it
        # named its own number.
        self.assertEqual(files["Map005.rvdata2"], moved_bytes)
        infos = self.project.load_data("map_infos")
        self.assertIsNone(infos.get(2))
        self.assertEqual(infos.get(5).get("name").data, b"Riverbend Cave")
        self.assertEqual(infos.get(5).get("parent_id"), 1)
        # The door on map 1 now goes to map 5.
        self.assertEqual(self.refs("maps", 5)["total"], 1)
        self.assertEqual(self.refs("maps", 2)["total"], 0)
        self.assertEqual(self.findings(), [])
        self.assertEqual(self.changed_files(before),
                         ["Map001.rvdata2", "Map002.rvdata2", "Map005.rvdata2",
                          "MapInfos.rvdata2"])

    def test_the_parent_the_system_and_the_vehicles_follow_map_1(self):
        def boats(system):
            system.get("boat").set("start_map_id", 1)
        self.edit_data("System", boats)
        self.renumber("maps", 1, 4)
        self.fresh()
        infos = self.project.load_data("map_infos")
        self.assertEqual(infos.get(2).get("parent_id"), 4)
        system = self.project.load_data("system")
        self.assertEqual(system.get("start_map_id"), 4)
        self.assertEqual(system.get("edit_map_id"), 4)
        # The fixture's three vehicles are one shared object: it is moved
        # once, and all three see map 4.
        for vehicle in ("boat", "ship", "airship"):
            self.assertEqual(system.get(vehicle).get("start_map_id"), 4)
        self.assertEqual(self.findings(), [])

    def test_a_transfer_inside_the_moved_map_is_carried_with_it(self):
        self.add_event([self.command(201, 0, 2, 1, 1, 2, 0)], map_id=2)
        self.renumber("maps", 2, 6)
        self.fresh()
        moved = self.project.load_map(6)
        event = moved.get("events").get(2)
        self.assertEqual(
            event.get("pages")[0].get("list")[0].get("parameters")[1], 6)
        self.assertNotIn("Map002.rvdata2", self.data_files())
        self.assertEqual(self.findings(), [])

    def test_two_maps_swap_files_tree_entries_and_transfers(self):
        before = self.data_files()
        self.call("refactor", op="renumber", table="maps", from_id=1,
                  to_id=2, swap=True)
        self.fresh()
        infos = self.project.load_data("map_infos")
        self.assertEqual(infos.get(1).get("name").data, b"Riverbend Cave")
        self.assertEqual(infos.get(2).get("name").data, b"Riverbend")
        # Riverbend (now 2) was the parent of the cave (now 1): the cave's
        # parent_id follows Riverbend to 2.
        self.assertEqual(infos.get(1).get("parent_id"), 2)
        self.assertEqual(self.findings(), [])
        self.call("refactor", op="renumber", table="maps", from_id=1,
                  to_id=2, swap=True)
        self.assertEqual(self.data_files(), before)

    def test_a_taken_map_id_is_refused(self):
        with self.assertRaisesRegex(ToolError, "map 2 is already taken"):
            self.renumber("maps", 1, 2)

    def test_a_map_with_a_file_and_no_tree_entry_is_refused(self):
        def orphan(infos):
            infos.delete(2)
        self.edit_data("MapInfos", orphan)
        with self.assertRaisesRegex(ToolError,
                                    "map 2 has a map file but no tree entry"):
            self.renumber("maps", 2, 5)

    def test_a_free_id_whose_file_exists_is_refused(self):
        path = self.project.path_for(self.project.map_file(5))
        path.write_bytes(self.data_files()["Map001.rvdata2"])
        self.fresh()
        with self.assertRaisesRegex(ToolError, "map 5 is already taken"):
            self.renumber("maps", 2, 5)

    def test_the_tree_is_not_a_table(self):
        with self.assertRaisesRegex(ToolError, "renumber table \"maps\""):
            self.renumber("map_infos", 2, 5)


class TestItIsAtomic(RefactorCase):
    def fail_on(self, filename):
        from rpgvxace_mcp.project import Project, ProjectError
        real = Project.save_file

        def save(project, name, *args, **kwargs):
            if name == filename and not kwargs.get("dry_run"):
                raise ProjectError("the editor has %s open" % name)
            return real(project, name, *args, **kwargs)
        return mock.patch.object(Project, "save_file", save)

    def test_a_failure_on_the_last_file_puts_the_earlier_ones_back(self):
        before = self.data_files()
        with self.fail_on("Map002.rvdata2"):
            with self.assertRaisesRegex(ToolError, "put back as it was"):
                self.renumber("items", 1, 7)
        self.assertEqual(self.data_files(), before)
        # And what the server remembers is what is on disk.
        self.assertEqual(self.refs("items", 1)["total"], 3)
        self.assertEqual(self.refs("items", 7)["total"], 0)

    def test_a_failure_on_the_first_file_writes_nothing(self):
        before = self.data_files()
        with self.fail_on("Enemies.rvdata2"):
            with self.assertRaises(ToolError):
                self.renumber("items", 1, 7)
        self.assertEqual(self.data_files(), before)

    def test_a_map_move_that_fails_at_the_tree_leaves_no_new_file(self):
        before = self.data_files()
        with self.fail_on("MapInfos.rvdata2"):
            with self.assertRaises(ToolError):
                self.renumber("maps", 2, 5)
        self.assertEqual(self.data_files(), before)
        self.assertNotIn("Map005.rvdata2", self.data_files())

    def test_a_map_move_that_fails_removing_the_old_file_restores_everything(self):
        from rpgvxace_mcp.project import Project, ProjectError
        before = self.data_files()

        def refuse(project, name, *args, **kwargs):
            raise ProjectError("could not delete %s" % name)
        with mock.patch.object(Project, "delete_file", refuse):
            with self.assertRaises(ToolError):
                self.renumber("maps", 2, 5)
        self.assertEqual(self.data_files(), before)

    def test_a_file_changed_by_the_editor_is_noticed_before_a_write(self):
        from rpgvxace_mcp.project import StaleFileError
        self.refs("items", 1)               # loads the files into the cache
        path = self.project.path_for("Map002.rvdata2")
        game_map = marshal_rb.load(path.read_bytes())
        game_map.get("events").get(1).set("name", self.project.codec
                                          .make_string("Edited elsewhere"))
        path.write_bytes(marshal_rb.dump(game_map))
        before = self.data_files()
        with self.assertRaises(StaleFileError):
            self.renumber("items", 1, 7)
        self.assertEqual(self.data_files(), before)

    def test_a_refused_request_leaves_nothing_in_the_cache(self):
        with self.assertRaises(ToolError):
            self.renumber("items", 1, 2)
        self.assertEqual(self.refs("items", 1)["total"], 3)


class TestWhatBlocksARenumber(RefactorCase):
    def test_a_script_mention_blocks_it_until_it_is_allowed(self):
        self.add_event([self.command(355, "$data_items[1].name")])
        preview = self.renumber("items", 1, 7, dry_run=True)
        self.assertEqual(len(preview["blocked"]), 1)
        self.assertIn("Ruby script", preview["blocked"][0])
        self.assertEqual(preview["script_mentions"]["count"], 1)
        before = self.data_files()
        with self.assertRaisesRegex(ToolError, "renumber refused.*Ruby script"):
            self.renumber("items", 1, 7)
        self.assertEqual(self.data_files(), before)
        done = self.renumber("items", 1, 7, allow_script_mentions=True)
        self.assertEqual(done["script_mentions"]["count"], 1)
        self.fresh()
        self.assertEqual(self.refs("items", 7)["total"], 3)

    def test_an_unfinished_scan_blocks_it(self):
        with mock.patch.object(reference_tools, "WalkBudget",
                               lambda: WalkBudget(2)):
            preview = self.renumber("items", 1, 7, dry_run=True)
            self.assertTrue(any("did not finish" in line
                                for line in preview["blocked"]))
            before = self.data_files()
            with self.assertRaisesRegex(ToolError, "did not finish"):
                self.renumber("items", 1, 7)
        self.assertEqual(self.data_files(), before)

    def test_a_file_that_cannot_be_read_blocks_it(self):
        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(b"\x04\x08 not marshal")
        self.fresh()
        preview = self.renumber("items", 1, 7, dry_run=True)
        self.assertTrue(any("could not be read" in line and "map 1" in line
                            for line in preview["blocked"]), preview["blocked"])
        with self.assertRaisesRegex(ToolError, "could not be read"):
            self.renumber("items", 1, 7)

    def test_a_reference_already_naming_the_free_id_blocks_it(self):
        # Item 9 does not exist; something still names it. Moving item 1 to 9
        # would quietly make that something point at the potion.
        self.add_event([self.command(126, 9, 0, 0, 1)])
        with self.assertRaisesRegex(ToolError,
                                    "already name items 9.*which has no entry"):
            self.renumber("items", 1, 9)
        # A swap has no free id to be confused about.
        self.call("refactor", op="renumber", table="items", from_id=1,
                  to_id=2, swap=True)


class TestNamingASwitchOrVariable(RefactorCase):
    def test_a_switch_is_named_in_the_system_list(self):
        reply = self.call("refactor", op="rename_switch", id=2, name="Elder Met")
        self.assertEqual((reply["before"], reply["after"]),
                         ("Met Elder", "Elder Met"))
        self.fresh()
        names = self.project.load_data("system").get("switches")
        self.assertEqual(names[2].data, b"Elder Met")
        self.assertEqual(names[1].data, b"Door Opened")
        # In the project's own string style, so the editor reads it.
        self.assertEqual(names[2].ivars, names[1].ivars)

    def test_a_variable_likewise(self):
        self.call("refactor", op="rename_variable", id=1, name="Coins")
        self.fresh()
        self.assertEqual(
            self.project.load_data("system").get("variables")[1].data, b"Coins")

    def test_a_preview_says_what_would_become_of_the_name(self):
        before = self.data_files()
        reply = self.call("refactor", op="rename_switch", id=2, name="X",
                          dry_run=True)
        self.assertIs(reply["dry_run"], True)
        self.assertEqual((reply["before"], reply["would_become"]),
                         ("Met Elder", "X"))
        self.assertEqual(self.data_files(), before)

    def test_a_number_past_the_end_extends_the_list(self):
        self.call("refactor", op="rename_switch", id=7, name="Far")
        self.fresh()
        names = self.project.load_data("system").get("switches")
        self.assertEqual(len(names), 8)
        self.assertEqual(names[7].data, b"Far")
        self.assertEqual(names[5].data, b"")

    def test_the_same_name_is_refused(self):
        with self.assertRaisesRegex(ToolError, "already named"):
            self.call("refactor", op="rename_switch", id=1, name="Door Opened")

    def test_a_name_is_one_line_of_text(self):
        for bad in ("two\nlines", "tab\there", "bell\x07", "del\x7f"):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ToolError, "no control characters"):
                    self.call("refactor", op="rename_switch", id=1, name=bad)

    def test_the_name_length_is_bounded_and_the_type_checked(self):
        with self.assertRaisesRegex(ToolError, "name takes at most %d"
                                    % reference_tools.MAX_NAME_CHARS):
            self.call("refactor", op="rename_switch", id=1,
                      name="x" * (reference_tools.MAX_NAME_CHARS + 1))
        with self.assertRaisesRegex(ToolError, "name must be a string"):
            self.call("refactor", op="rename_switch", id=1, name=["a"])

    def test_the_id_is_strictly_read_and_bounded(self):
        for bad, message in (
                (10 ** 5000, "id must be at most"),
                (0, "id must be at least 1, got 0"),
                (5001, "id must be at most 5000, got 5001"),
                (True, "id must be a whole number, not true or false"),
                (1.5, "id must be a whole number, got 1.5"),
                ([2], "id must be an integer, got list")):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ToolError, message):
                    self.call("refactor", op="rename_switch", id=bad, name="Z")

    def test_a_name_the_code_page_cannot_hold_is_refused_before_a_write(self):
        before = self.data_files()
        project = self.project
        with mock.patch.object(type(project.codec), "encode_strict",
                               side_effect=ToolError("cannot be written")):
            with self.assertRaises(ToolError):
                self.call("refactor", op="rename_switch", id=2, name="e")
        self.assertEqual(self.data_files(), before)


class TestThePreviewAndTheAnswerAgree(RefactorCase):
    def test_what_references_lists_is_what_renumber_would_change(self):
        self.add_event([self.command(117, 1), self.command(301, 0, 2, 1, 0),
                        self.command(201, 0, 2, 1, 1, 2, 0)])
        for table, ident in (("items", 1), ("common_events", 1),
                             ("troops", 2), ("maps", 2)):
            with self.subTest(table=table):
                listed = sorted(self.where(self.refs(table, ident)))
                target = 9 if table != "maps" else 8
                preview = self.renumber(table, ident, target, dry_run=True)
                self.assertEqual(
                    sorted(site["where"] for site in preview["sites"]),
                    listed)


class TestReferencesOnAnotherEngine(ToolTest):
    engine = "xp"

    def test_the_shared_command_rules_still_work_and_the_gap_is_said(self):
        reply = self.call("references", kind="items", id=1)
        self.assertEqual(reply["total"], 1)
        self.assertEqual(reply["sites"][0]["where"],
                         "map 2 event 1 (Chest) page 0 command 0")
        self.assertTrue(any("VX Ace" in line
                            for line in reply["coverage"]["not_scanned"]))


if __name__ == "__main__":
    unittest.main()
