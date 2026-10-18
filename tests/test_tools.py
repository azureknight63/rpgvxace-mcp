"""Per-tool tests: what each tool answers on an ordinary project.

The conventional half of the suite. Every tool has a class here, run
against the demo project and -- for the tools whose answers differ between
engines -- again against a VX one.

The regression suites that pin a specific past defect live in
`test_guards_*.py`, one file per subject. They are a different population:
this file answers "does the tool work", those answer "is this particular
defect still impossible", and `scripts/prove_guards.py` reverts the code
each of those names and requires it to fail.
"""

import os
import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from build_project import build  # noqa: E402

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.project import (  # noqa: E402
    Project,
    ReadOnlyError,
    StaleFileError,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyObject,
    RubyString,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
)

from tool_case import (  # noqa: E402
    ToolTest,
)


class TestProjectInfo(ToolTest):
    def test_reports_engine_encoding_and_counts(self):
        info = self.call("project_info")
        self.assertEqual(info["engine"], self.engine)
        self.assertEqual(info["extension"], self.extension)
        self.assertEqual(info["encoding"], "utf-8")
        self.assertEqual(info["entry_counts"]["actors"], 2)
        self.assertEqual(info["entry_counts"]["items"], 2)
        self.assertEqual(info["maps"], [1, 2])
        self.assertTrue(any("last page" in note.lower()
                            for note in info["engine_notes"]))

    def test_schema_is_observed_from_the_files(self):
        info = self.call("project_info", include_schema=True)
        actor = info["schema"]["classes"]["RPG::Actor"]
        self.assertIn("class_id", actor["fields"])
        # RPG::Map is documented for both engines, so every field it carries in
        # this project should have a description.
        game_map = info["schema"]["classes"]["RPG::Map"]
        self.assertTrue(game_map["fields"]["width"])
        self.assertEqual(game_map["fields_without_a_description"], [])
        # And a database class has no reference table, so all of its fields
        # are listed without one -- which does not make them any less real.
        self.assertTrue(actor["fields_without_a_description"])
        self.assertIn("class_id", actor["fields_without_a_description"])
        self.assertEqual(len(info["schema"]["map_layers"]), 4 if self.ace else 3)

    def test_table_list_matches_the_engine(self):
        tables = {f["table"] for f in self.call("project_info")["data_files"]}
        if self.ace:
            # Ace has a tileset database; VX's encounter areas are gone.
            self.assertIn("tilesets", tables)
            self.assertNotIn("areas", tables)
        else:
            self.assertIn("areas", tables)
            self.assertNotIn("tilesets", tables)

    def test_absent_table_explains_the_engine_difference(self):
        missing = "areas" if self.ace else "tilesets"
        with self.assertRaises(ToolError) as caught:
            self.call("db_list", table=missing)
        message = str(caught.exception)
        self.assertIn("has no %s table" % missing, message)
        self.assertIn("region" if self.ace else "Graphics/System", message)


class TestDatabase(ToolTest):
    def test_list_summarises_entries(self):
        result = self.call("db_list", table="items")
        self.assertEqual(result["matching_entries"], 2)
        first = result["entries"][0]
        self.assertEqual((first["id"], first["name"], first["price"]), (1, "Potion", 50))

    def test_list_filters_by_name(self):
        result = self.call("db_list", table="items", name_contains="hi-")
        self.assertEqual([e["name"] for e in result["entries"]], ["Hi-Potion"])

    def curve(self):
        """Where this engine keeps its parameter curve Table."""
        if self.ace:
            return "classes", "params", 800
        return "actors", "parameters", 600

    def test_get_returns_all_fields_and_summarises_tables(self):
        entry = self.call("db_get", table="actors", id=1)["entries"][0]["entry"]
        self.assertEqual(entry["name"], "Ralph")
        self.assertEqual(entry["__class__"], "RPG::Actor")
        table, field, cells = self.curve()
        curve = self.call("db_get", table=table, id=1)["entries"][0]["entry"][field]
        # A parameter curve of hundreds of cells is described, not dumped.
        self.assertEqual(curve["cells_omitted"], cells)

    def test_get_full_tables_includes_cells(self):
        table, field, cells = self.curve()
        entry = self.call("db_get", table=table, id=1,
                          tables="full")["entries"][0]["entry"]
        self.assertEqual(len(entry[field]["cells"]), cells)

    def test_get_reports_missing_ids(self):
        result = self.call("db_get", table="items", ids=[1, 99])
        self.assertEqual(result["missing_ids"], [99])

    def test_update_patches_only_named_fields(self):
        before = self.call("db_get", table="items", id=1)["entries"][0]["entry"]
        result = self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"name": "Herb", "price": 25}}])
        self.assertTrue(result["applied"][0]["changes"])
        after = self.reopen()
        entry = REGISTRY["db_get"].handler(after, {"table": "items", "id": 1})
        entry = entry["entries"][0]["entry"]
        self.assertEqual((entry["name"], entry["price"]), ("Herb", 25))
        self.assertEqual(entry["icon_index"], before["icon_index"])

    def test_nested_patch_merges(self):
        self.call("map_edit", op="update", map_id=1, patch={"bgm": {"name": "Theme1"}})
        game_map = self.reopen().project.load_map(1)
        bgm = game_map.get("bgm")
        self.assertEqual(bgm.get("name").data, b"Theme1")
        self.assertEqual(bgm.get("volume"), 100)  # untouched

    def test_create_by_cloning_keeps_the_field_set(self):
        result = self.call("db_edit", table="items", ops=[
            {"op": "create", "clone_from": 1, "patch": {"name": "Elixir", "price": 900}}])
        new_id = result["applied"][0]["id"]
        self.assertEqual(new_id, 3)
        entry = self.call("db_get", table="items", id=new_id)["entries"][0]["entry"]
        self.assertEqual((entry["name"], entry["price"], entry["id"]), ("Elixir", 900, 3))
        original = self.call("db_get", table="items", id=1)["entries"][0]["entry"]
        self.assertEqual(sorted(entry), sorted(original))

    def test_clone_is_a_deep_copy(self):
        # A list field, so writing to the clone would show up in the original if
        # the copy were shallow. Which list an item has depends on the engine.
        field = "features" if self.ace else "plus_state_set"
        self.call("db_edit", table="items", ops=[
            {"op": "create", "clone_from": 1, "patch": {"name": "Elixir"}}])
        self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 3,
             "patch": {field: [{"__class__": "RPG::BaseItem::Feature",
                                "code": 21, "data_id": 2, "value": 1.5}]
                       if self.ace else [2]}}])
        first = self.call("db_get", table="items", id=1)["entries"][0]["entry"]
        self.assertEqual(first[field], [])

    def test_create_without_clone_warns(self):
        result = self.call("db_edit", table="states", ops=[
            {"op": "create", "patch": {"name": "Silence"}}])
        self.assertTrue(any("cloned" in w for w in result["warnings"]))

    def test_a_blank_entry_takes_its_siblings_field_order(self):
        # The patch's key order is whatever the caller typed, and the id is
        # set last; Marshal keeps that order. A sibling holds the editor's.
        sibling = self.ctx.project.get_entry("items", 1).fields()
        given = [field for field in reversed(sibling)
                 if field in ("name", "price", "icon_index")]
        self.call("db_edit", table="items", ops=[
            {"op": "create", "patch": {"price": 7, "icon_index": 3,
                                       "name": "Twig"}}])
        entry = self.reopen().project.get_entry("items", 3)
        self.assertEqual(entry.fields(),
                         [field for field in sibling
                          if field in set(given) | {"id"}])

    def test_update_of_missing_entry_explains_create(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items", ops=[{"op": "update", "id": 42, "patch": {}}])
        self.assertIn("create", str(caught.exception))

    def test_delete_keeps_the_slot_and_reports_references(self):
        result = self.call("db_edit", table="items", ops=[{"op": "delete", "id": 1}])
        applied = result["applied"][0]
        # The chest on map 2 hands out item 1.
        self.assertGreaterEqual(applied["references_found"], 1)
        self.assertIn("map 2 event 1", applied["references"][0]["where"])
        self.assertTrue(result["warnings"])
        # Slot preserved, so item 2 keeps its id.
        entry = self.reopen().project.get_entry("items", 2)
        self.assertEqual(entry.get("name").data, b"Hi-Potion")

    def test_dry_run_leaves_the_file_alone(self):
        before = self.data_bytes("Items")
        result = self.call("db_edit", table="items", dry_run=True, ops=[
            {"op": "update", "id": 1, "patch": {"name": "Nope"}}])
        self.assertTrue(result["write"]["dry_run"])
        self.assertEqual(self.data_bytes("Items"), before)

    def test_write_creates_a_backup(self):
        result = self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"price": 60}}])
        backup = Path(result["write"]["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(marshal_rb.load(backup.read_bytes())[1].get("price"), 50)

    def test_unknown_table_lists_the_options(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_list", table="monsters")
        self.assertIn("actors", str(caught.exception))


class TestMaps(ToolTest):
    def test_list_shows_the_tree_and_start_position(self):
        result = self.call("map_list", include_details=True)
        rows = {row["id"]: row for row in result["maps"]}
        self.assertEqual(rows[1]["name"], "Riverbend")
        self.assertEqual(rows[2]["parent_id"], 1)
        self.assertEqual((rows[1]["width"], rows[1]["height"]), (20, 15))
        self.assertEqual(rows[1]["events"], 2)
        self.assertEqual(result["start_position"], {"map_id": 1, "x": 5, "y": 6})

    def test_get_reports_properties_events_and_layers(self):
        result = self.call("map_get", map_id=1, tiles="counts")
        self.assertEqual(result["properties"]["encounter_step"], 30)
        self.assertEqual(result["tile_table"]["zsize"], 4 if self.ace else 3)
        # Layer 2 is a tile layer on both engines: RGSS3's `layered_tiles`
        # and RGSS2's `passable?` both read it as a tile id, and on a real
        # Ace project it holds B-E ids. Ace's layer 3 packs the shadow bits
        # and the region id into one word.
        self.assertIn("tile layer", result["tile_table"]["layers"]["2"])
        self.assertNotIn("shadow", result["tile_table"]["layers"]["2"])
        if self.ace:
            self.assertIn("region", result["tile_table"]["layers"]["3"])
            self.assertIn("shadow", result["tile_table"]["layers"]["3"])
        elder = result["events"][0]
        self.assertEqual((elder["name"], elder["x"], elder["y"]), ("Elder", 5, 4))
        self.assertEqual(elder["pages"][1]["conditions"], ["switch 3 is ON"])
        ground = result["tile_counts"]["0"]["most_common"][0]
        self.assertEqual((ground["tile_id"], ground["count"], ground["sheet"]),
                         (2816, 300, "A2"))

    def test_tiles_read_and_write(self):
        rows = self.call("map_tiles", map_id=1, x=0, y=0, width=3, height=2)["rows"]
        self.assertEqual(rows, [[2816] * 3, [2816] * 3])
        self.call("map_tiles", map_id=1, op="write", x=1, y=1, width=2, height=1,
                  rows=[[4352, 4353]])
        after = REGISTRY["map_tiles"].handler(
            self.reopen(), {"map_id": 1, "x": 0, "y": 1, "width": 3, "height": 1})
        self.assertEqual(after["rows"], [[2816, 4352, 4353]])

    def test_tiles_fill(self):
        self.call("map_tiles", map_id=1, op="fill", x=0, y=0, width=2, height=2,
                  z=1, tile_id=48)
        rows = self.call("map_tiles", map_id=1, x=0, y=0, width=2, height=2, z=1)["rows"]
        self.assertEqual(rows, [[48, 48], [48, 48]])

    def test_layer_2_takes_an_upper_tile_id(self):
        # A B-E id, which a real Ace project's layer 2 is full of; the old
        # shadow-mask ceiling of 15 refused it.
        self.call("map_tiles", map_id=1, op="fill", x=0, y=0, width=2,
                  height=1, z=2, tile_id=200)
        rows = self.call("map_tiles", map_id=1, x=0, y=0, width=2, height=1,
                         z=2)["rows"]
        self.assertEqual(rows, [[200, 200]])

    def test_region_and_shadow_views_need_the_meta_layer(self):
        for view in ("region", "shadow"):
            with self.subTest(view=view):
                if self.ace:
                    out = self.call("map_tiles", map_id=1, view=view, width=1,
                                    height=1)
                    self.assertEqual((out["z"], out["view"]), (3, view))
                    with self.assertRaises(ToolError) as caught:
                        self.call("map_tiles", map_id=1, view=view, z=2)
                    self.assertIn("layer 3", str(caught.exception))
                else:
                    with self.assertRaises(ToolError) as caught:
                        self.call("map_tiles", map_id=1, view=view)
                    self.assertIn("VX Ace", str(caught.exception))

    def test_tiles_rejects_out_of_bounds_rectangle(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, x=18, y=0, width=5, height=1)
        self.assertIn("does not fit", str(caught.exception))

    def test_tiles_rejects_mismatched_rows(self):
        with self.assertRaises(ToolError):
            self.call("map_tiles", map_id=1, op="write", x=0, y=0, width=2, height=2,
                      rows=[[1, 2]])

    def test_tiles_rejects_missing_layer(self):
        beyond = 4 if self.ace else 3
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, z=beyond)
        self.assertIn("layers 0..%d" % (beyond - 1), str(caught.exception))

    def test_create_writes_both_the_map_and_the_tree_entry(self):
        result = self.call("map_edit", op="create", name="Forest Path", parent_id=1,
                           width=25, height=20)
        map_id = result["map_id"]
        self.assertEqual(map_id, 3)
        fresh = self.reopen()
        game_map = fresh.project.load_map(3)
        self.assertEqual((game_map.get("width"), game_map.get("height")), (25, 20))
        self.assertEqual(game_map.get("data").xsize, 25)
        info = fresh.project.get_entry("map_infos", 3)
        self.assertEqual(info.get("name").data, b"Forest Path")
        self.assertEqual(info.get("parent_id"), 1)

    def test_created_map_is_loadable_marshal(self):
        self.call("map_edit", op="create", name="Cellar")
        raw = self.data_bytes("Map003")
        self.assertEqual(marshal_rb.dump(marshal_rb.load(raw)), raw)
        game_map = self.reopen().project.load_map(3)
        self.assertEqual(game_map.get("data").zsize, 4 if self.ace else 3)
        self.assertEqual(game_map.has("tileset_id"), self.ace)

    def test_resize_keeps_overlapping_tiles(self):
        self.call("map_tiles", map_id=2, op="fill", x=0, y=0, width=12, height=10,
                  tile_id=2049)
        self.call("map_edit", op="resize", map_id=2, width=6, height=5)
        game_map = self.reopen().project.load_map(2)
        self.assertEqual((game_map.get("width"), game_map.get("height")), (6, 5))
        self.assertEqual(game_map.get("data").get(5, 4, 0), 2049)

    def test_resize_flags_events_left_outside(self):
        result = self.call("map_edit", op="resize", map_id=1, width=4, height=4)
        self.assertTrue(any("outside" in change for change in result["changes"]))

    def test_rename(self):
        self.call("map_edit", op="update", map_id=1, name="Riverbend Village")
        info = self.reopen().project.get_entry("map_infos", 1)
        self.assertEqual(info.get("name").data, b"Riverbend Village")

    def test_delete_refuses_while_children_exist(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="delete", map_id=1)
        self.assertIn("parent", str(caught.exception))

    def test_delete_removes_file_and_tree_entry_and_warns(self):
        result = self.call("map_edit", op="delete", map_id=2)
        self.assertFalse((self.tmp / "project" / "Data" / "Map002.rvdata").exists())
        self.assertIsNone(self.reopen().project.get_entry("map_infos", 2))
        # The door on map 1 still transfers to map 2.
        # `warnings`, plural and a list: db_edit spells it that way and
        # a caller reading one tool's replies should not have to learn
        # two words for one thing.
        self.assertIn("Transfer Player", " ".join(result["warnings"]))


class TestEvents(ToolTest):
    def test_get_renders_a_readable_script(self):
        result = self.call("event_get", map_id=1, event_ids=[1])
        page = result["events"][0]["pages"][0]
        self.assertEqual(page["trigger"], "action button")
        script = page["script"]
        self.assertIn("Show Text", script[0])
        self.assertIn("Welcome to Riverbend", script[1])
        self.assertIn("switch 1 is ON", script[3])
        # The branch body is indented one level.
        self.assertTrue(script[4].startswith("  4|   "))

    def test_get_includes_raw_commands_on_request(self):
        page = self.call("event_get", map_id=1, event_ids=[1],
                         include_raw=True)["events"][0]["pages"][0]
        first = page["commands"][0]
        self.assertEqual(first["code"], 101)
        self.assertEqual(first["parameters"], ["Actor1", 0, 0, 2])

    def test_get_common_event(self):
        result = self.call("event_get", common_event_id=1)
        self.assertEqual(result["name"], "Heal Party")
        self.assertIn("Recover All", result["script"][0])

    def test_create_event_from_the_dsl(self):
        result = self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 9, "y": 7, "name": "Signpost",
            "patch": {"graphic": {"character_name": "People1", "character_index": 3},
                      "trigger": 0},
            "commands": [
                {"text": "The road east leads to Riverbend."},
                {"if": {"switch": 1, "is": "on"},
                 "then": [{"text": "The gate stands open."},
                          {"switch": 2, "value": "on"}],
                 "else": [{"text": "The gate is shut."}]},
            ],
        }])
        event_id = result["applied"][0]["event_id"]
        page = REGISTRY["event_get"].handler(
            self.reopen(), {"map_id": 1, "event_ids": [event_id], "include_raw": True}
        )["events"][0]["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        self.assertEqual(codes, [101, 401, 111, 101, 401, 121, 411, 101, 401, 412, 0])
        indents = [c["indent"] for c in page["commands"]]
        self.assertEqual(indents, [0, 0, 0, 1, 1, 1, 0, 1, 1, 0, 0])
        self.assertEqual(page["graphic"]["character_name"], "People1")

    def test_long_text_splits_into_message_boxes(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"text": "one\ntwo\nthree\nfour\nfive"}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        self.assertEqual(codes, [101, 401, 401, 401, 401, 101, 401, 0])

    def test_choices_compile_with_branches(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 2, "y": 2,
            "commands": [{
                "choices": ["Yes", "No"],
                "branches": [[{"switch": 1, "value": "on"}], [{"text": "Maybe later."}]],
                "cancel_branch": [{"text": "..."}],
            }],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        self.assertEqual(codes, [102, 402, 121, 402, 101, 401, 403, 101, 401, 404, 0])
        self.assertEqual(page["commands"][0]["parameters"], [["Yes", "No"], 5])

    def test_loop_compiles(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 3, "y": 3,
            "commands": [{"loop": [{"wait": 60}, {"break_loop": True}]}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        self.assertEqual([c["code"] for c in page["commands"]], [112, 230, 113, 413, 0])

    def test_script_command_uses_continuation_lines(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 4, "y": 4,
            "commands": [{"script": "a = 1\nb = 2"}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        self.assertEqual([c["code"] for c in page["commands"]], [355, 655, 0])
        # No trailing newline: all 65 script commands in a real editor-written
        # project store their line without one. Adding one would show up as a
        # blank line in every script box in the editor.
        self.assertEqual(page["commands"][0]["parameters"], ["a = 1"])
        self.assertEqual(page["commands"][1]["parameters"], ["b = 2"])

    def test_raw_commands_pass_through(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 5, "y": 5,
            "commands": [{"raw": {"code": 236, "parameters": [1, 5, 60]}}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        self.assertEqual(page["commands"][0]["parameters"], [1, 5, 60])

    def test_move_route_gains_its_editor_step_lines(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 6, "y": 6,
            "commands": [{"raw": {"code": 205, "parameters": [
                0,
                {"__class__": "RPG::MoveRoute", "wait": True,
                 "list": [{"code": 1}, {"code": 4}]},
            ]}}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        self.assertEqual([c["code"] for c in page["commands"]], [205, 505, 505, 0])
        self.assertIn("Move Down", page["script"][1])

    def test_unknown_dsl_key_is_explained(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, ops=[
                {"op": "create", "x": 1, "y": 1, "commands": [{"teleport": 3}]}])
        message = str(caught.exception)
        self.assertIn("raw", message)
        self.assertIn("transfer", message)

    def test_append_keeps_the_terminator_last(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "append_commands", "event_id": 1, "page": 0,
            "commands": [{"self_switch": "A", "value": "on"}],
        }])
        page = self.call("event_get", map_id=1, event_ids=[1],
                         include_raw=True)["events"][0]["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        self.assertEqual(codes[-2:], [123, 0])
        self.assertEqual(codes.count(0), 1)

    def test_set_page_condition_and_trigger(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_page", "event_id": 2, "page": 0,
            "patch": {"trigger": 3, "condition": {"switch1_valid": True,
                                                  "switch1_id": 2}},
        }])
        page = self.call("event_get", map_id=1, event_ids=[2])["events"][0]["pages"][0]
        self.assertEqual(page["trigger"], "autorun")
        self.assertTrue(page["condition"]["switch1_valid"])
        self.assertEqual(page["condition"]["switch1_id"], 2)

    def test_move_and_delete(self):
        self.call("event_edit", map_id=1, ops=[
            {"op": "move", "event_id": 1, "x": 12, "y": 9},
            {"op": "add_page", "event_id": 1, "commands": [{"text": "hi"}]},
        ])
        result = self.call("event_get", map_id=1, event_ids=[1])["events"][0]
        self.assertEqual((result["x"], result["y"]), (12, 9))
        self.assertEqual(result["page_count"], 3)
        self.call("event_edit", map_id=1, ops=[{"op": "delete", "event_id": 1}])
        self.assertEqual(self.call("event_get", map_id=1, event_ids=[1])["events"], [])

    def test_move_outside_the_map_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, ops=[
                {"op": "move", "event_id": 1, "x": 30, "y": 2}])
        self.assertIn("20x15", str(caught.exception))

    def test_common_event_commands_can_be_replaced(self):
        self.call("event_edit", common_event_id=1, ops=[{
            "op": "set_commands",
            "commands": [{"text": "Your wounds close."}, {"raw": {"code": 314,
                                                                  "parameters": [0, 1]}}],
        }])
        result = self.call("event_get", common_event_id=1)
        self.assertIn("Your wounds close", result["script"][1])

    def test_edited_map_stays_byte_identical_on_reload(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 7, "y": 7, "commands": [{"text": "Hello."}]}])
        raw = self.data_bytes("Map001")
        self.assertEqual(marshal_rb.dump(marshal_rb.load(raw)), raw)


class TestCommandReference(ToolTest):
    def test_lookup_by_code(self):
        result = self.call("command_reference", code=121)
        self.assertEqual(result["name"], "Control Switches")
        self.assertEqual(result["parameters"][0], "switch_id_start")

    def test_unknown_layout_says_so(self):
        # Rotate Picture: this used to ask about Move Picture (232), whose
        # VX Ace layout is now confirmed against editor-written data.
        result = self.call("command_reference", code=233)
        self.assertIsNone(result["parameters"])
        # A list, because a code can be both undispatched and unrecorded.
        # `layout_notes`: `notes` was also a *sentence* in search's
        # reply, so one key had two types.
        self.assertTrue(any("include_raw" in note
                            for note in result["layout_notes"]))

    def test_search_by_name(self):
        result = self.call("command_reference", search="picture")
        self.assertIn("Show Picture", [row["name"] for row in result["commands"]])

    def test_code_list_matches_the_engine(self):
        codes = {row["code"] for row in self.call("command_reference")["commands"]}
        self.assertIn(136, codes)  # Change Encounter exists in both
        # Change Formation Access and Get Location Info are VX Ace additions.
        self.assertEqual(137 in codes, self.ace)
        self.assertEqual(285 in codes, self.ace)

    def test_shop_processing_layout_differs_by_engine(self):
        """Both engines have "purchase only"; only VX Ace has a price.

        This asserted that VX had no purchase-only flag, which was the
        table's belief and not VX's: its command_302 reads
        `$game_temp.shop_purchase_only = @params[2]`. What the two really
        differ in is the price, and where the flag sits.
        """
        spec = self.call("command_reference", code=302)["parameters"]
        goods = self.call("command_reference", code=605)["parameters"]
        self.assertEqual("price" in spec, self.ace)
        self.assertEqual("price" in goods, self.ace)
        self.assertEqual(spec.index("purchase_only"), 4 if self.ace else 2)
        self.assertNotIn("purchase_only", goods)


class TestSearch(ToolTest):
    def test_finds_dialogue_with_its_location(self):
        result = self.call("search", query="bridge east")
        self.assertEqual(result["count"], 1)
        hit = result["hits"][0]
        self.assertEqual(hit["location"]["map_id"], 1)
        self.assertEqual(hit["location"]["event_id"], 1)
        self.assertEqual(hit["command"], "Text Line")

    def test_finds_database_names(self):
        hits = self.call("search", query="potion", scopes=["database"])["hits"]
        self.assertEqual({h["location"]["table"] for h in hits}, {"items"})

    def test_finds_script_lines(self):
        hits = self.call("search", query="SceneManager", scopes=["scripts"])["hits"]
        self.assertEqual(hits[0]["script_name"], "Main")

    def test_regex(self):
        hits = self.call("search", query=r"Riverbend\s+Cave", regex=True,
                         scopes=["maps"])["hits"]
        self.assertEqual(hits[0]["location"]["map_id"], 2)

    def test_by_command_code(self):
        hits = self.call("search", command_code=201)["hits"]
        self.assertEqual(len(hits), 1)
        self.assertIn("map 2", hits[0]["text"])

    def test_bad_regex_is_reported(self):
        with self.assertRaises(ToolError):
            self.call("search", query="([", regex=True)


class TestValidate(ToolTest):
    def rules(self, **kwargs):
        return {f["rule"] for f in self.call("validate", **kwargs)["findings"]}

    def test_clean_project_has_no_errors(self):
        result = self.call("validate")
        errors = [f for f in result["findings"] if f["severity"] == "error"]
        self.assertEqual(errors, [], errors)

    def test_detects_transfer_to_missing_map(self):
        self.project.load_map(1)
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_commands", "event_id": 2, "page": 0,
            "commands": [{"transfer": {"map_id": 77, "x": 1, "y": 1}}],
        }])
        self.assertIn("transfer_to_missing_map", self.rules())

    def test_detects_transfer_outside_the_target_map(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_commands", "event_id": 2, "page": 0,
            "commands": [{"transfer": {"map_id": 2, "x": 40, "y": 40}}],
        }])
        findings = self.call("validate")["findings"]
        outside = [f for f in findings if f["rule"] == "transfer_outside_map"]
        self.assertIn("only 12x10", outside[0]["message"])

    def test_detects_missing_graphic(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_page", "event_id": 1, "page": 0,
            "patch": {"graphic": {"character_name": "NoSuchSheet"}},
        }])
        self.assertIn("missing_graphic", self.rules(scopes=["assets"]))

    def test_detects_missing_audio(self):
        self.call("map_edit", op="update", map_id=1,
                  patch={"bgm": {"name": "MissingTrack"}})
        self.assertIn("missing_audio", self.rules(scopes=["assets"]))

    def test_detects_shadowed_pages(self):
        self.call("event_edit", map_id=1, ops=[
            {"op": "add_page", "event_id": 1, "commands": [{"text": "unreachable"}]}])
        findings = self.call("validate", scopes=["events"])["findings"]
        shadowed = [f for f in findings if f["rule"] == "shadowed_pages"]
        self.assertIn("can never run", shadowed[0]["message"])

    def test_detects_unstoppable_autorun(self):
        # A single-page Autorun with no conditions and no way out: the page wins
        # every time, so it restarts the moment it ends.
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 9, "y": 9, "name": "Trap",
            "patch": {"trigger": 3}, "commands": [{"text": "You cannot leave."}]}])
        findings = self.call("validate", scopes=["events"])["findings"]
        trap = [f for f in findings if f["rule"] == "unstoppable_autorun"]
        self.assertEqual(len(trap), 1)
        self.assertIn("never regains control", trap[0]["message"])

    def test_autorun_cutscene_with_a_later_page_is_not_flagged(self):
        # The standard cutscene shape: page 1 autoruns and sets a self switch,
        # page 2 is conditioned on it and takes over. Because the last matching
        # page wins, this does stop -- flagging it was a false positive.
        self.call("event_edit", map_id=1, ops=[
            {"op": "set_page", "event_id": 1, "page": 0,
             "patch": {"trigger": 3},
             "commands": [{"text": "A hooded figure passes."},
                          {"self_switch": "A", "value": "on"}]},
            {"op": "set_page", "event_id": 1, "page": 1,
             "patch": {"condition": {"self_switch_valid": True}}},
        ])
        self.assertNotIn("unstoppable_autorun", self.rules(scopes=["events"]))

    def test_autorun_that_transfers_the_player_is_not_flagged(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 10, "y": 10, "patch": {"trigger": 3},
            "commands": [{"transfer": {"map_id": 2, "x": 1, "y": 1}}]}])
        self.assertNotIn("unstoppable_autorun", self.rules(scopes=["events"]))

    def test_detects_asset_case_mismatch(self):
        # Works on Windows, fails on a case-sensitive filesystem.
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_page", "event_id": 1, "page": 0,
            "patch": {"graphic": {"character_name": "people1"}}}])
        findings = self.call("validate", scopes=["assets"])["findings"]
        mismatch = [f for f in findings if f["rule"] == "asset_case_mismatch"]
        self.assertTrue(mismatch)
        message = mismatch[0]["message"]
        self.assertIn('spelled "People1"', message)
        # The folder is named, so the fix does not need a second lookup.
        self.assertIn("Graphics/Characters", message)
        self.assertNotIn("missing_graphic", {f["rule"] for f in findings})

    def test_detects_dangling_database_reference(self):
        self.call("db_edit", table="items", ops=[{"op": "delete", "id": 1}])
        self.assertIn("missing_item", self.rules(scopes=["events"]))

    def test_detects_missing_troop_in_encounters(self):
        self.call("map_edit", op="update", map_id=2, patch={"encounter_list": [1, 9]})
        self.assertIn("missing_troop", self.rules(scopes=["maps"]))

    def test_detects_orphan_map_file(self):
        infos = self.project.load_data("map_infos")
        infos.delete(2)
        self.project.save_file(self.project.filename_for("map_infos"))
        self.assertIn("orphan_map_file", self.rules(scopes=["maps"]))

    def test_detects_missing_label(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_commands", "event_id": 2, "page": 0,
            "commands": [{"jump": "nowhere"}],
        }])
        self.assertIn("missing_label", self.rules(scopes=["events"]))

    def test_detects_broken_database_links(self):
        self.call("db_edit", table="actors", ops=[
            {"op": "update", "id": 1, "patch": {"class_id": 9}}])
        self.assertIn("missing_class", self.rules(scopes=["database"]))


class TestMapCritique(ToolTest):
    def test_an_ordinary_project_is_fully_reachable(self):
        out = self.call("map_critique")
        self.assertIn("reachability", out["checks_run"])
        reach = out["reachability"]
        self.assertEqual(reach["start_map"], 1)
        self.assertEqual(reach["reachable"], [1, 2])
        self.assertEqual(reach["unreachable"], [])
        self.assertEqual(reach["unresolved_transfers"], 0)

    def test_a_map_nothing_transfers_to_is_reported_unreachable(self):
        self.call("map_edit", op="create", name="Isolated")
        reach = self.call("map_critique",
                          scopes=["reachability"])["reachability"]
        self.assertEqual(reach["reachable"], [1, 2])
        self.assertEqual(reach["unreachable"], [3])

    def test_a_variable_indirect_transfer_is_counted_not_guessed_at(self):
        # The destination is read from variable 1 at runtime, so it names no
        # map this server can follow -- it must not invent an edge from it,
        # and it must not be silently dropped either.
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"transfer": {"designation": 1, "map_id": 1,
                                       "x": 0, "y": 0}}]}])
        reach = self.call("map_critique")["reachability"]
        self.assertEqual(reach["reachable"], [1, 2])
        self.assertEqual(reach["unreachable"], [])
        self.assertGreaterEqual(reach["unresolved_transfers"], 1)

    def test_a_common_event_transfer_reaches_from_anywhere(self):
        self.call("map_edit", op="create", name="Isolated")
        self.call("event_edit", common_event_id=1, ops=[{
            "op": "append_commands",
            "commands": [{"transfer": {"map_id": 3, "x": 0, "y": 0}}]}])
        reach = self.call("map_critique")["reachability"]
        self.assertIn(3, reach["reachable"])
        self.assertNotIn(3, reach["unreachable"])

    def test_unknown_scope_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("map_critique", scopes=["no_such_scope"])


class TestMapCritiqueDeadSpace(ToolTest):
    """The `dead_space` scope: ground-layer emptiness and occupied shape."""

    def shape(self, map_id):
        out = self.call("map_critique", scopes=["dead_space"])["dead_space"]
        return {row["map_id"]: row for row in out["maps"]}[map_id]

    def new_map(self):
        self.call("map_edit", op="create", name="Shape", width=6, height=4)
        return 3

    def test_a_painted_map_reports_its_size_and_a_full_box(self):
        row = self.shape(1)
        self.assertEqual((row["width"], row["height"]), (20, 15))
        self.assertEqual(row["empty_ground_fraction"], 0.0)
        self.assertEqual(row["bounding_box"],
                         {"x0": 0, "y0": 0, "x1": 19, "y1": 14})
        self.assertEqual(row["fill_of_bounding_box"], 1.0)

    def test_a_new_map_is_all_empty_with_no_box(self):
        row = self.shape(self.new_map())
        self.assertEqual(row["empty_ground_fraction"], 1.0)
        self.assertIsNone(row["bounding_box"])
        self.assertIsNone(row["fill_of_bounding_box"])
        self.assertTrue(row["blank"])

    def test_the_box_spans_scattered_tiles_and_fill_is_honest(self):
        map_id = self.new_map()
        self.call("map_tiles", map_id=map_id, op="fill", x=2, y=1,
                  width=2, height=2, tile_id=2816)
        self.call("map_tiles", map_id=map_id, op="fill", x=5, y=3,
                  width=1, height=1, tile_id=2816)
        row = self.shape(map_id)
        self.assertEqual(row["bounding_box"],
                         {"x0": 2, "y0": 1, "x1": 5, "y1": 3})
        # 5 occupied of 24 cells; the box is 4 x 3 = 12 cells.
        self.assertEqual(row["empty_ground_fraction"], round(19 / 24, 4))
        self.assertEqual(row["fill_of_bounding_box"], round(5 / 12, 4))
        self.assertFalse(row["blank"])

    def test_a_tile_whose_id_has_a_zero_byte_is_still_occupied(self):
        # 256 is 0x0100: its low byte is zero. The row scan works on bytes.
        map_id = self.new_map()
        self.call("map_tiles", map_id=map_id, op="fill", x=4, y=0,
                  width=1, height=1, tile_id=256)
        row = self.shape(map_id)
        self.assertEqual(row["bounding_box"],
                         {"x0": 4, "y0": 0, "x1": 4, "y1": 0})

    def test_an_upper_layer_tile_is_content_but_not_ground(self):
        map_id = self.new_map()
        self.call("map_tiles", map_id=map_id, op="fill", z=1, x=0, y=0,
                  width=1, height=1, tile_id=2816)
        row = self.shape(map_id)
        self.assertEqual(row["empty_ground_fraction"], 1.0)
        self.assertIsNone(row["bounding_box"])
        self.assertFalse(row["blank"], "an upper-layer tile is content")

    def test_an_event_alone_makes_a_map_not_blank(self):
        map_id = self.new_map()
        self.call("event_edit", map_id=map_id, ops=[{
            "op": "create", "x": 1, "y": 1, "commands": [{"wait": 1}]}])
        row = self.shape(map_id)
        self.assertEqual(row["empty_ground_fraction"], 1.0)
        self.assertFalse(row["blank"])

    def test_reachability_marks_blank_unreachable_maps(self):
        blank = self.new_map()
        self.call("map_edit", op="create", name="Built", width=5, height=5)
        self.call("map_tiles", map_id=4, op="fill", x=0, y=0,
                  width=2, height=2, tile_id=2816)
        reach = self.call("map_critique",
                          scopes=["reachability"])["reachability"]
        self.assertEqual(reach["unreachable"], [blank, 4])
        detail = {d["map_id"]: d for d in reach["unreachable_detail"]}
        self.assertTrue(detail[blank]["blank"])
        self.assertFalse(detail[4]["blank"])
        self.assertEqual(detail[4]["nonzero_cells"], 4)

    def set_meta_layer(self, map_id, words):
        """Write `words` into the first cells of VX Ace's layer 3."""
        table = self.project.load_map(map_id).get("data")
        plane = table.xsize * table.ysize
        for at, word in enumerate(words):
            table.data[3 * plane + at] = word

    def test_regions_and_shadows_alone_are_not_content(self):
        # Layer 3 packs region << 8 | shadow; neither is a tile, so a map
        # holding only them is still blank, and they are counted apart.
        map_id = self.new_map()
        self.set_meta_layer(map_id, [256, 256 | 5, 3, 15, 512])
        row = self.shape(map_id)
        self.assertTrue(row["blank"], "regions and shadows are not tiles")
        self.assertEqual(row["nonzero_cells"], 0)
        self.assertEqual(row["region_cells"], 3)
        self.assertEqual(row["shadow_cells"], 3)

    def test_nonzero_cells_counts_tiles_on_the_three_tile_layers_only(self):
        map_id = self.new_map()
        self.call("map_tiles", map_id=map_id, op="fill", x=0, y=0,
                  width=2, height=1, tile_id=2816)
        self.call("map_tiles", map_id=map_id, op="fill", z=2, x=0, y=1,
                  width=1, height=1, tile_id=5)
        self.set_meta_layer(map_id, [256] * 6)
        row = self.shape(map_id)
        self.assertEqual(row["nonzero_cells"], 3)
        self.assertEqual(row["region_cells"], 6)
        self.assertEqual(row["shadow_cells"], 0)
        self.assertFalse(row["blank"])

    def test_the_reply_states_what_empty_means(self):
        out = self.call("map_critique", scopes=["dead_space"])["dead_space"]
        self.assertIn("layer 0", out["definition"])
        self.assertIn("region_cells", out["definition"])

    def test_the_description_says_a_void_cell_may_carry_shadows_and_regions(
            self):
        description = REGISTRY["map_critique"].description
        self.assertIn("a shadow or region id on layer 3", description)

    def test_the_row_walk_is_bounded_and_says_so(self):
        from rpgvxace_mcp.analysis import WalkBudget, map_shapes
        out = map_shapes(self.project, WalkBudget(limit=3))
        self.assertIn("dead_space_stopped", out)
        self.assertNotIn("unreadable_maps", out)


class TestMapCritiqueLayersVX(ToolTest):
    """VX has three tile layers and no region layer: every layer is tiles."""

    engine = "vx"

    def test_every_vx_layer_is_a_tile_layer(self):
        self.call("map_edit", op="create", name="Shape", width=6, height=4)
        self.call("map_tiles", map_id=3, op="fill", z=2, x=0, y=0,
                  width=2, height=1, tile_id=5)
        out = self.call("map_critique", scopes=["dead_space", "tile_variety"])
        row = {r["map_id"]: r for r in out["dead_space"]["maps"]}[3]
        self.assertEqual(row["nonzero_cells"], 2)
        self.assertFalse(row["blank"])
        self.assertNotIn("region_cells", row)
        self.assertNotIn("shadow_cells", row)
        entry = {m["map_id"]: m for m in out["tile_variety"]["maps"]}[3]
        self.assertEqual([l["layer"] for l in entry["layers"]], [0, 1, 2])
        self.assertEqual(entry["layers"][2]["non_empty_cells"], 2)
        self.assertNotIn("regions", entry)
        self.assertNotIn("shadows", entry)


class TestMapCritiqueScriptTransfers(ToolTest):
    """Transfers written as Ruby in a Script command (355 + 655 lines).\n\n    Pattern matching over the script text, not a Ruby parse, so these pin\n    both what is followed and what is deliberately left alone.\n    """

    def _isolated_map(self):
        self.call("map_edit", op="create", name="Isolated")  # map 3

    def _script_on_map(self, script, map_id=1):
        self.call("event_edit", map_id=map_id, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"script": script}]}])

    def _reach(self):
        return self.call("map_critique")["reachability"]

    def test_a_literal_reserve_transfer_is_a_direct_edge(self):
        self._isolated_map()
        self._script_on_map("$game_player.reserve_transfer(3, 4, 5, 2)")
        reach = self._reach()
        self.assertIn(3, reach["reachable"])
        self.assertEqual(reach["unreachable"], [])
        self.assertEqual(reach["script_transfers"], 1)
        self.assertEqual(reach["unresolved_script_transfers"], 0)
        self.assertEqual(reach["unresolved_transfers"], 0)

    def test_the_edge_belongs_to_the_map_that_holds_the_script(self):
        # Written on the unreachable map, so it must not make itself
        # reachable: an edge is followed from where it lives.
        self._isolated_map()
        self._script_on_map("reserve_transfer(1, 0, 0)", map_id=3)
        self.assertEqual(self._reach()["unreachable"], [3])

    def test_a_multi_line_script_is_reassembled(self):
        self._isolated_map()
        self._script_on_map("x = 1\ny = 2\n$game_player.reserve_transfer(3, 0, 0)")
        reach = self._reach()
        self.assertEqual(reach["unreachable"], [])
        self.assertEqual(reach["script_transfers"], 1)

    def test_a_call_split_across_lines_is_still_one_call(self):
        self._isolated_map()
        self._script_on_map("$game_player.reserve_transfer(\n  3,\n  0, 0)")
        self.assertEqual(self._reach()["script_transfers"], 1)

    def test_a_variable_argument_is_counted_apart_from_command_ones(self):
        self._isolated_map()
        self._script_on_map("$game_player.reserve_transfer($game_variables[1], 0, 0)")
        reach = self._reach()
        self.assertEqual(reach["unreachable"], [3])
        self.assertEqual(reach["script_transfers"], 0)
        self.assertEqual(reach["unresolved_script_transfers"], 1)
        self.assertEqual(reach["unresolved_transfers"], 0)

    def test_a_computed_literal_is_not_guessed_at(self):
        self._isolated_map()
        self._script_on_map("$game_player.reserve_transfer(2 + 1, 0, 0)")
        reach = self._reach()
        self.assertEqual(reach["unreachable"], [3])
        self.assertEqual(reach["unresolved_script_transfers"], 1)

    def test_a_commented_out_call_is_not_a_transfer(self):
        self._isolated_map()
        self._script_on_map("# $game_player.reserve_transfer(3, 0, 0)\n"
                            "=begin\n$game_player.reserve_transfer(3, 0, 0)\n=end")
        reach = self._reach()
        self.assertEqual(reach["unreachable"], [3])
        self.assertEqual(reach["script_transfers"], 0)

    def test_a_definition_or_a_string_is_not_a_call(self):
        self._isolated_map()
        self._script_on_map("def reserve_transfer(map_id, x, y, d = 2)\n"
                            "puts 'reserve_transfer(3, 0, 0)'\n"
                            "alias old_rt reserve_transfer\nend")
        reach = self._reach()
        self.assertEqual(reach["unreachable"], [3])
        self.assertEqual(reach["script_transfers"], 0)
        self.assertEqual(reach["unresolved_script_transfers"], 0)

    def test_the_vx_player_new_map_id_assignment_is_followed(self):
        self._isolated_map()
        self._script_on_map("$game_temp.player_new_map_id = 3")
        reach = self._reach()
        self.assertEqual(reach["unreachable"], [])
        self.assertEqual(reach["script_transfers"], 1)

    def test_a_script_transfer_in_a_common_event_reaches_from_anywhere(self):
        self._isolated_map()
        self.call("event_edit", common_event_id=1, ops=[{
            "op": "append_commands",
            "commands": [{"script": "$game_player.reserve_transfer(3, 0, 0)"}]}])
        reach = self._reach()
        self.assertEqual(reach["unreachable"], [])
        self.assertEqual(reach["script_transfers"], 1)

    def test_a_target_nothing_on_disk_holds_adds_no_reachable_map(self):
        self._script_on_map("$game_player.reserve_transfer(99, 0, 0)")
        reach = self._reach()
        self.assertEqual(reach["reachable"], [1, 2])
        self.assertEqual(reach["script_transfers"], 1)

    def test_an_ordinary_project_reports_no_script_transfers(self):
        reach = self._reach()
        self.assertEqual(reach["script_transfers"], 0)
        self.assertEqual(reach["unresolved_script_transfers"], 0)
        self.assertEqual(reach["script_blocks_clipped"], 0)

    def test_an_enormous_script_is_scanned_to_a_bound_and_says_so(self):
        # Nothing past the cap may be scanned, and silence about that would
        # read as "no transfer there".
        self._isolated_map()
        filler = "x = 1;" * 40000
        self._script_on_map(filler + "$game_player.reserve_transfer(3, 0, 0)")
        reach = self._reach()
        self.assertEqual(reach["script_blocks_clipped"], 1)
        self.assertEqual(reach["script_transfers"], 0)
        self.assertEqual(reach["unreachable"], [3])


class TestMapCritiqueDeadEnds(ToolTest):
    """`dead_ends`: reachable maps the party enters and cannot leave."""

    def dead_ends(self):
        return self.call("map_critique", scopes=["dead_ends"])["dead_ends"]

    def transfer(self, source, target):
        self.call("event_edit", map_id=source, ops=[{
            "op": "create", "x": 2, "y": 2,
            "commands": [{"transfer": {"map_id": target, "x": 0, "y": 0}}]}])

    def make_map(self, name):
        self.call("map_edit", op="create", name=name)
        return max(self.call("map_critique", scopes=["reachability"])
                   ["reachability"]["unreachable"])

    def test_the_fixture_end_of_the_line_is_a_no_exit_map(self):
        # Map 1 transfers to map 2 and map 2 transfers nowhere.
        out = self.dead_ends()
        self.assertEqual([e["map_id"] for e in out["no_exit"]], [2])
        self.assertEqual(out["no_exit"][0]["entered_from"], [1])
        self.assertFalse(out["no_exit"][0]["uncertain"])
        self.assertEqual(out["closed_regions"], [])
        self.assertEqual(out["one_way"], [])

    def test_a_return_door_removes_the_finding(self):
        self.transfer(2, 1)
        out = self.dead_ends()
        self.assertEqual(out["no_exit"], [])
        self.assertEqual(out["one_way"], [])

    def test_a_self_transfer_is_not_an_exit(self):
        self.transfer(2, 2)
        out = self.dead_ends()
        self.assertEqual([e["map_id"] for e in out["no_exit"]], [2])

    def test_maps_that_only_lead_to_each_other_are_a_closed_region(self):
        third = self.make_map("Third")
        self.transfer(1, third)
        self.transfer(2, third)
        self.transfer(third, 2)
        # 1 -> 2 <-> third: both have an exit, the party cannot leave them.
        out = self.dead_ends()
        self.assertEqual(out["no_exit"], [])
        self.assertEqual([r["maps"] for r in out["closed_regions"]],
                         [[2, third]])
        self.assertEqual(out["closed_regions"][0]["entered_from"], [1])

    def test_a_route_that_continues_is_one_way_not_a_dead_end(self):
        third = self.make_map("Third")
        self.transfer(2, third)
        out = self.dead_ends()
        self.assertEqual([e["map_id"] for e in out["no_exit"]], [third])
        self.assertEqual(out["one_way"], [2])

    def test_a_door_to_a_missing_map_is_not_an_exit(self):
        self.transfer(2, 99)
        out = self.dead_ends()
        self.assertEqual([e["map_id"] for e in out["no_exit"]], [2])
        self.assertEqual(out["no_exit"][0]["transfers_to_missing_maps"], [99])

    def test_a_variable_transfer_downgrades_the_finding(self):
        self.call("event_edit", map_id=2, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"transfer": {"designation": 1, "map_id": 1,
                                       "x": 0, "y": 0}}]}])
        entry = self.dead_ends()["no_exit"][0]
        self.assertEqual(entry["map_id"], 2)
        self.assertTrue(entry["uncertain"])
        self.assertIn("unresolved_transfers", entry["reasons"])

    def test_a_script_that_may_transfer_on_the_map_downgrades_the_finding(self):
        # A transfer whose map id is not a literal: no edge, so it may be
        # the exit the graph cannot see. (This read `$game_variables[1] = 2`
        # when any script at all downgraded a finding; that script cannot
        # transfer, and now has a test of its own below.)
        self.call("event_edit", map_id=2, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"script": "$game_player.reserve_transfer("
                                    "$game_variables[1], 0, 0)"}]}])
        entry = self.dead_ends()["no_exit"][0]
        self.assertTrue(entry["uncertain"])
        self.assertIn("map_script", entry["reasons"])

    # -- which scripts can make a finding uncertain ------------------------
    # Only one that might transfer the player and that can run while the
    # party is on the finding's maps: their own events, the common events
    # those events call (followed through further calls), and autorun or
    # parallel common events and troop pages, which can run anywhere.

    def script_on_map(self, map_id, script, x=1, y=1):
        self.call("event_edit", map_id=map_id, ops=[{
            "op": "create", "x": x, "y": y,
            "commands": [{"script": script}]}])

    def common_event(self, commands, trigger=0):
        """A new common event holding `commands`; its id."""
        out = self.call("db_edit", table="common_events", ops=[
            {"op": "create", "clone_from": 1, "patch": {"trigger": trigger}}])
        new_id = out["applied"][0]["id"]
        self.call("event_edit", common_event_id=new_id, ops=[
            {"op": "set_commands", "commands": commands}])
        return new_id

    def map_two(self):
        entries = self.dead_ends()["no_exit"]
        self.assertEqual([e["map_id"] for e in entries], [2])
        return entries[0]

    VARIABLE_TRANSFER = "$game_player.reserve_transfer($game_variables[3], 1, 1)"

    def test_a_script_that_cannot_transfer_on_the_map_is_not_an_exit(self):
        self.script_on_map(2, "$game_variables[1] = 2")
        entry = self.map_two()
        self.assertFalse(entry["uncertain"], entry.get("reasons"))

    def test_a_harmless_script_in_a_parallel_common_event_is_not_an_exit(self):
        self.common_event([{"script": "$game_system.set_autodash(true)"}],
                          trigger=2)
        entry = self.map_two()
        self.assertFalse(entry["uncertain"], entry.get("reasons"))

    def test_a_transfer_script_in_a_parallel_common_event_downgrades(self):
        self.common_event([{"script": self.VARIABLE_TRANSFER}], trigger=2)
        entry = self.map_two()
        self.assertTrue(entry["uncertain"])
        self.assertIn("common_event_script", entry["reasons"])

    def test_a_transfer_script_in_an_autorun_common_event_downgrades(self):
        self.common_event([{"script": self.VARIABLE_TRANSFER}], trigger=1)
        self.assertIn("common_event_script", self.map_two()["reasons"])

    def test_a_transfer_script_in_a_common_event_nothing_calls_is_not_an_exit(self):
        self.common_event([{"script": self.VARIABLE_TRANSFER}], trigger=0)
        entry = self.map_two()
        self.assertFalse(entry["uncertain"], entry.get("reasons"))

    def test_a_common_event_called_from_the_map_brings_its_script(self):
        called = self.common_event([{"script": self.VARIABLE_TRANSFER}])
        self.call("event_edit", map_id=2, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"common_event": called}]}])
        entry = self.map_two()
        self.assertTrue(entry["uncertain"])
        self.assertIn("common_event_script", entry["reasons"])

    def test_a_common_event_called_only_from_another_map_does_not(self):
        called = self.common_event([{"script": self.VARIABLE_TRANSFER}])
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"common_event": called}]}])
        entry = self.map_two()
        self.assertFalse(entry["uncertain"], entry.get("reasons"))

    def test_a_chain_of_common_event_calls_is_followed(self):
        inner = self.common_event([{"script": self.VARIABLE_TRANSFER}])
        outer = self.common_event([{"common_event": inner}])
        self.call("event_edit", map_id=2, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"common_event": outer}]}])
        self.assertIn("common_event_script", self.map_two()["reasons"])

    def scripts_file_with_a_transfer_method(self):
        self.call("scripts", op="set", index=0, code=(
            "class Game_Interpreter\n"
            "  def say_hello\n"
            "    p 1\n"
            "  end\n"
            "  def go_home\n"
            "    $game_player.reserve_transfer(1, 0, 0)\n"
            "  end\n"
            "end\n"))

    def test_calling_a_transfer_method_from_the_scripts_file_downgrades(self):
        self.scripts_file_with_a_transfer_method()
        self.script_on_map(2, "go_home")
        entry = self.map_two()
        self.assertTrue(entry["uncertain"])
        self.assertIn("map_script", entry["reasons"])

    def test_calling_a_method_that_makes_no_transfer_is_not_an_exit(self):
        self.scripts_file_with_a_transfer_method()
        self.script_on_map(2, "say_hello")
        entry = self.map_two()
        self.assertFalse(entry["uncertain"], entry.get("reasons"))

    def test_eval_is_treated_as_a_possible_transfer(self):
        self.script_on_map(2, 'eval("go_" + "home")')
        self.assertIn("map_script", self.map_two()["reasons"])

    def test_an_uncertain_finding_says_where_the_script_is(self):
        self.script_on_map(2, self.VARIABLE_TRANSFER)
        where = self.map_two()["may_exit_via"]
        self.assertTrue(where)
        self.assertIn("map 2", where[0])

    def test_a_literal_script_transfer_is_an_exit_not_an_uncertainty(self):
        self.script_on_map(2, "$game_player.reserve_transfer(1, 0, 0)")
        self.assertEqual(self.dead_ends()["no_exit"], [])

    def test_a_common_event_transfer_downgrades_the_finding(self):
        self.call("event_edit", common_event_id=1, ops=[{
            "op": "append_commands",
            "commands": [{"transfer": {"map_id": 1, "x": 0, "y": 0}}]}])
        entry = self.dead_ends()["no_exit"][0]
        self.assertTrue(entry["uncertain"])
        self.assertIn("common_event_transfers", entry["reasons"])

    def test_an_unreachable_map_is_not_a_dead_end(self):
        self.make_map("Island")
        out = self.dead_ends()
        self.assertEqual([e["map_id"] for e in out["no_exit"]], [2])

    def test_the_scope_runs_by_default(self):
        out = self.call("map_critique")
        self.assertIn("dead_ends", out["checks_run"])
        self.assertIn("dead_ends", out)


class TestMapCritiqueSummary(ToolTest):
    """A default call answers with a ranked summary, not every table.

    The full tables of tile_variety, stat_outliers and dead_space are what
    made a default reply 64 KB on a real project; they are still there when
    named. The summary draws on them all and lists findings only.
    """

    def summary(self, **args):
        return self.call("map_critique", **args)["summary"]

    def findings(self, **args):
        return self.summary(**args)["findings"]

    def test_a_default_call_runs_the_finding_scopes_and_the_summary(self):
        out = self.call("map_critique")
        self.assertEqual(out["checks_run"], ["reachability", "dead_ends",
                                             "switch_usage", "summary"])
        for scope in ("summary", "reachability", "dead_ends", "switch_usage"):
            self.assertIn(scope, out)
        for table in ("tile_variety", "stat_outliers", "dead_space"):
            self.assertNotIn(table, out, "%s is opt-in now" % table)

    def test_the_tables_still_run_when_named(self):
        out = self.call("map_critique",
                        scopes=["tile_variety", "stat_outliers", "dead_space"])
        for table in ("tile_variety", "stat_outliers", "dead_space"):
            self.assertIn(table, out)
        self.assertNotIn("summary", out)

    def test_the_summary_can_be_asked_for_alone(self):
        out = self.call("map_critique", scopes=["summary"])
        self.assertEqual(out["checks_run"], ["summary"])
        self.assertNotIn("reachability", out)
        self.assertTrue(out["summary"]["findings"])

    def test_findings_are_ranked_by_priority(self):
        self.call("map_edit", op="create", name="Folder")      # blank, 3
        rows = self.findings()
        self.assertGreater(len(rows), 1)
        self.assertEqual([r["priority"] for r in rows],
                         list(range(1, len(rows) + 1)))
        order = {"high": 0, "medium": 1, "low": 2}
        ranks = [order[r["severity"]] for r in rows]
        self.assertEqual(ranks, sorted(ranks))

    def test_a_certain_dead_end_comes_first_and_is_high(self):
        # Map 1 leads to map 2 and map 2 leads nowhere.
        first = self.findings()[0]
        self.assertEqual((first["scope"], first["finding"], first["map_id"]),
                         ("dead_ends", "no_exit", 2))
        self.assertEqual(first["severity"], "high")

    def test_every_finding_is_a_prompt_to_look_not_a_verdict(self):
        summary = self.summary()
        self.assertIn("not a verdict", summary["note"])
        for row in summary["findings"]:
            self.assertIsInstance(row["prompt_to_look"], str)
            self.assertTrue(row["prompt_to_look"])

    def test_unreachable_maps_mark_the_blank_ones(self):
        self.call("map_edit", op="create", name="Folder")      # 3, blank
        self.call("map_edit", op="create", name="Built", width=5, height=5)
        self.call("map_tiles", map_id=4, op="fill", x=0, y=0,
                  width=2, height=2, tile_id=2816)
        rows = {r["finding"]: r for r in self.findings()
                if r["scope"] == "reachability"}
        self.assertEqual(rows["unreachable"]["maps"], [4])
        self.assertEqual(rows["unreachable"]["severity"], "medium")
        self.assertEqual(rows["unreachable_blank"]["maps"], [3])
        self.assertEqual(rows["unreachable_blank"]["severity"], "low")

    def test_a_gate_that_can_never_open_is_listed(self):
        self.call("event_edit", map_id=1, ops=[
            {"op": "create", "x": 7, "y": 7,
             "commands": [{"comment": "placeholder"}]}])
        event_id = max(e["id"] for e in self.call(
            "event_get", map_id=1)["events"])
        self.call("event_edit", map_id=1, ops=[
            {"op": "set_page", "event_id": event_id, "page": 0,
             "patch": {"condition": {"switch1_valid": True,
                                     "switch1_id": 88}}}])
        # The demo project reads switches 1 and 3 and never sets them
        # either; 88 is the one this test wrote.
        gates = {r["id"]: r for r in self.findings()
                 if r["scope"] == "switch_usage"}
        self.assertEqual(gates[88]["finding"], "switch_never_set")
        self.assertEqual(gates[88]["severity"], "high")
        self.assertIn("event %d" % event_id, gates[88]["first_read"])

    def test_the_finding_list_is_capped_and_says_so(self):
        for i in range(30):
            self.call("event_edit", map_id=1, ops=[
                {"op": "create", "x": i % 10, "y": 5 + i // 10,
                 "commands": [{"if": {"switch": 300 + i}, "then": []}]}])
        summary = self.summary()
        self.assertGreater(summary["findings_total"], len(summary["findings"]))
        self.assertTrue(summary["findings_truncated"])

    def test_an_ordinary_project_says_nothing_is_cut_short(self):
        summary = self.summary()
        self.assertNotIn("findings_truncated", summary)
        self.assertNotIn("findings_stopped", summary)
        self.assertEqual(summary["findings_total"], len(summary["findings"]))


class TestCritiqueSummaryFences(unittest.TestCase):
    """Dead space and tile variety are judged against this project's maps.

    The fences are Tukey's, computed over the project's own non-blank maps
    exactly as `stat_outliers` computes them over a table -- so the same
    number is unusual in one project and ordinary in another.
    """

    def summary(self, empties=(), entropies=(), blank=()):
        from rpgvxace_mcp.analysis import critique_summary
        shapes = {"maps": [{"map_id": i, "blank": False,
                            "empty_ground_fraction": v}
                           for i, v in enumerate(empties, start=1)]
                  + [{"map_id": 100 + i, "blank": True,
                      "empty_ground_fraction": 1.0}
                     for i in range(len(blank))]}
        variety = {"maps": [{"map_id": i, "layers": [
            {"layer": 0, "entropy_bits": v}]}
            for i, v in enumerate(entropies, start=1)]}
        reach = {"start_map": 1, "reachable": [], "unreachable": [],
                 "unreachable_detail": [], "unresolved_transfers": 0,
                 "unresolved_script_transfers": 0}
        dead = {"no_exit": [], "closed_regions": [], "one_way": []}
        usage = {kind: {"read_but_never_set": {"shown": []}}
                 for kind in ("switches", "variables")}
        outliers = {"fields": []}
        return critique_summary(reach, dead, usage, shapes, variety, outliers)

    def flagged(self, out, finding):
        rows = [r for r in out["findings"] if r["finding"] == finding]
        # Map ids, as every other finding lists them; each metric is a
        # list beside `maps`, in the same order.
        return [m for r in rows for m in r["maps"]]

    SPREAD = (0.0, 0.02, 0.05, 0.05, 0.1, 0.1, 0.12, 0.15, 0.2)

    def test_a_map_far_emptier_than_the_rest_is_flagged(self):
        out = self.summary(empties=self.SPREAD + (0.9,))
        self.assertEqual(self.flagged(out, "unusual_dead_space"), [10])

    def test_the_same_number_is_ordinary_where_every_map_is_like_it(self):
        out = self.summary(empties=(0.8, 0.82, 0.84, 0.85, 0.86, 0.87,
                                    0.88, 0.89, 0.9, 0.9))
        self.assertEqual(self.flagged(out, "unusual_dead_space"), [])
        self.assertIn("upper_fence", out["map_fences"]["dead_space"])

    def test_blank_maps_are_not_part_of_the_population(self):
        out = self.summary(empties=self.SPREAD + (0.18,), blank=range(6))
        self.assertEqual(self.flagged(out, "unusual_dead_space"), [])
        self.assertEqual(out["map_fences"]["dead_space"]["n"], 10)

    def test_a_map_far_plainer_than_the_rest_is_flagged(self):
        out = self.summary(entropies=(3.0, 3.2, 3.4, 3.5, 3.6, 3.8, 4.0,
                                      4.1, 4.2, 0.1))
        self.assertEqual(self.flagged(out, "low_tile_variety"), [10])
        row = [r for r in out["findings"]
               if r["finding"] == "low_tile_variety"][0]
        self.assertEqual(row["ground_entropy_bits"], [0.1])

    def test_no_spread_means_no_fence_and_says_why(self):
        out = self.summary(empties=(0.0,) * 9 + (0.5,))
        self.assertEqual(self.flagged(out, "unusual_dead_space"), [])
        self.assertIn("interquartile",
                      out["map_fences"]["dead_space"]["skipped"])

    def test_an_outlier_field_whose_rounded_spread_is_zero_still_ranks(self):
        # `stat_outliers` rounds iqr to four places for its reply, so a
        # real but tiny spread arrives as 0.
        from rpgvxace_mcp.analysis import critique_summary
        field = {"table": "items", "field": "price", "iqr": 0,
                 "upper_fence": 1.0, "lower_fence": 1.0, "outliers_total": 1,
                 "outliers": [{"id": 3, "name": "Gem", "value": 9,
                               "direction": "high"}]}
        out = critique_summary(
            {"start_map": None}, {}, {}, {"maps": []}, {"maps": []},
            {"fields": [field]})
        rows = [r for r in out["findings"] if r["scope"] == "stat_outliers"]
        self.assertEqual(rows[0]["entries"][0]["id"], 3)

    def test_too_few_maps_means_no_fence(self):
        out = self.summary(empties=(0.0, 0.1, 0.9))
        self.assertIn("skipped", out["map_fences"]["dead_space"])

    # -- the absolute floors under the project-relative fences ------------

    def with_layers(self, rows, blank=()):
        """A summary over maps given as (ground distinct, entropy, upper)."""
        from rpgvxace_mcp.analysis import critique_summary
        shapes = {"maps": [{"map_id": i, "blank": i in blank,
                            "empty_ground_fraction": 0.0}
                           for i in range(1, len(rows) + 1)]}
        variety = {"maps": [{"map_id": i, "layers": [
            {"layer": 0, "distinct_tiles": distinct, "entropy_bits": bits},
            {"layer": 1, "non_empty_cells": upper},
            {"layer": 2, "non_empty_cells": 0}]}
            for i, (distinct, bits, upper) in enumerate(rows, start=1)]}
        reach = {"start_map": None}
        return critique_summary(reach, {}, {}, shapes, variety,
                                {"fields": []})

    # Ten maps whose ground entropy spreads so wide the lower fence is
    # below zero: the shape of the real game, where nothing was flagged.
    WIDE = [(40, b, 10) for b in (0.8, 1.5, 2.1, 2.7, 3.0, 3.6, 4.2, 4.6,
                                  5.4, 6.1)]

    def test_a_single_ground_tile_is_flagged_whatever_the_fence_says(self):
        out = self.with_layers(self.WIDE + [(1, 0.0, 0)])
        self.assertLess(out["map_fences"]["tile_variety"]["lower_fence"], 0)
        rows = [r for r in out["findings"] if r["finding"] == "uniform_ground"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["severity"], "low")
        self.assertEqual(rows[0]["scope"], "tile_variety")
        self.assertEqual(rows[0]["maps"], [11])
        self.assertEqual(rows[0]["upper_layers_empty"], [11])
        self.assertIn("?", rows[0]["prompt_to_look"])
        self.assertIn("rule", out["map_fences"]["uniform_ground"])

    def test_a_single_ground_tile_under_decoration_is_still_flagged(self):
        out = self.with_layers(self.WIDE + [(1, 0.0, 30)])
        self.assertEqual(self.flagged(out, "uniform_ground"), [11])
        row = [r for r in out["findings"] if r["finding"] == "uniform_ground"]
        self.assertEqual(row[0]["upper_layers_empty"], [])

    def test_a_map_with_no_tile_anywhere_is_not_uniform_ground(self):
        # Events on a map with no tile drawn anywhere: 100% empty ground,
        # which `unusual_dead_space` already says. Listing it here as well
        # would say one thing twice.
        out = self.with_layers(self.WIDE + [(0, None, 0)])
        self.assertEqual(self.flagged(out, "uniform_ground"), [])

    def test_no_ground_under_a_drawn_upper_layer_is_not_uniform(self):
        out = self.with_layers(self.WIDE + [(0, None, 12)])
        self.assertEqual(self.flagged(out, "uniform_ground"), [])

    def test_two_ground_tiles_are_not_uniform(self):
        out = self.with_layers(self.WIDE + [(2, 0.1, 0)])
        self.assertEqual(self.flagged(out, "uniform_ground"), [])

    def test_a_blank_map_is_not_uniform_ground(self):
        out = self.with_layers(self.WIDE + [(0, None, 0)], blank={11})
        self.assertEqual(self.flagged(out, "uniform_ground"), [])

    def test_a_uniform_map_is_not_listed_twice(self):
        # Past the Tukey fence and at the floor: one finding, the floor's.
        out = self.summary(entropies=(3.0, 3.2, 3.4, 3.5, 3.6, 3.8, 4.0,
                                      4.1, 4.2, 0.0))
        variety_rows = self.with_layers(
            [(30, b, 5) for b in (3.0, 3.2, 3.4, 3.5, 3.6, 3.8, 4.0, 4.1,
                                  4.2)] + [(1, 0.0, 0)])
        self.assertEqual(self.flagged(out, "low_tile_variety"), [10])
        self.assertEqual(self.flagged(variety_rows, "uniform_ground"), [10])
        self.assertEqual(self.flagged(variety_rows, "low_tile_variety"), [])

    def test_zero_spread_dead_space_falls_back_to_the_non_zero_maps(self):
        # Most maps have no empty ground, so the IQR is 0; the eight that
        # do have some are fenced among themselves.
        empties = ((0.0,) * 30 + (0.1, 0.12, 0.15, 0.15, 0.2, 0.22, 0.25,
                                  0.3, 0.95))
        out = self.summary(empties=empties)
        fence = out["map_fences"]["dead_space"]
        self.assertIn("interquartile", fence["skipped"])
        self.assertEqual(fence["fallback"]["n"], 9)
        self.assertEqual(fence["fallback"]["upper_fence"], 0.4)
        self.assertIn("rule", fence["fallback"])
        self.assertEqual(self.flagged(out, "unusual_dead_space"), [39])

    def test_zero_spread_with_few_non_zero_maps_flags_over_half_empty(self):
        out = self.summary(empties=(0.0,) * 10 + (0.3, 0.6, 1.0))
        fence = out["map_fences"]["dead_space"]
        self.assertEqual(fence["fallback"]["upper_fence"], 0.5)
        self.assertIn("50%", fence["fallback"]["rule"])
        self.assertEqual(self.flagged(out, "unusual_dead_space"), [12, 13])
        row = [r for r in out["findings"]
               if r["finding"] == "unusual_dead_space"][0]
        self.assertEqual(row["upper_fence"], 0.5)
        self.assertEqual(row["empty_ground_fraction"], [0.6, 1])


class TestMapCritiqueTileVariety(ToolTest):
    """The `tile_variety` scope: Shannon entropy of tile ids per layer.

    Known values, not snapshots: a layer holding four ids in equal numbers
    is 2.0 bits and a layer holding one is 0.0, whatever the map's size.
    """

    def set_layer(self, map_id, z, cells):
        """Overwrite one layer of a loaded map with `cells`, row-major."""
        table = self.project.load_map(map_id).get("data")
        plane = table.xsize * table.ysize
        self.assertEqual(len(cells), plane)
        table.data[z * plane:(z + 1) * plane] = type(table.data)("H", cells)
        return table

    def layer(self, map_id=1, z=0):
        out = self.call("map_critique", scopes=["tile_variety"])
        self.assertEqual(out["checks_run"], ["tile_variety"])
        entry = next(m for m in out["tile_variety"]["maps"]
                     if m["map_id"] == map_id)
        return entry["layers"][z]

    def test_uniform_over_four_tiles_is_two_bits(self):
        self.set_layer(1, 0, [10, 20, 30, 40] * 75)
        layer = self.layer(1, 0)
        self.assertAlmostEqual(layer["entropy_bits"], 2.0)
        self.assertEqual(layer["distinct_tiles"], 4)
        self.assertEqual(layer["cells"], 300)
        self.assertEqual(layer["non_empty_cells"], 300)
        self.assertAlmostEqual(layer["normalised"], 1.0)

    def test_a_single_tile_is_zero_bits_not_null(self):
        self.set_layer(1, 0, [7] * 300)
        layer = self.layer(1, 0)
        self.assertEqual(layer["entropy_bits"], 0.0)
        self.assertEqual(layer["distinct_tiles"], 1)

    def test_empty_cells_are_excluded_and_counted(self):
        # 150 empty, 75 of one tile, 75 of another: entropy over the 150
        # non-empty cells is 1 bit, not the 1.5 bits it would be with 0
        # counted as a third symbol.
        self.set_layer(1, 0, [0, 0, 5, 6] * 75)
        layer = self.layer(1, 0)
        self.assertAlmostEqual(layer["entropy_bits"], 1.0)
        self.assertEqual(layer["distinct_tiles"], 2)
        self.assertEqual(layer["non_empty_cells"], 150)
        self.assertEqual(layer["cells"], 300)

    def test_a_fully_empty_layer_is_null_not_zero(self):
        self.set_layer(1, 1, [0] * 300)
        layer = self.layer(1, 1)
        self.assertIsNone(layer["entropy_bits"])
        self.assertIsNone(layer["normalised"])
        self.assertEqual(layer["distinct_tiles"], 0)
        self.assertEqual(layer["non_empty_cells"], 0)

    def test_the_reply_says_what_the_number_is(self):
        out = self.call("map_critique", scopes=["tile_variety"])
        method = out["tile_variety"]["method"]
        self.assertIn("non-empty", method)
        self.assertIn("log2", method)

    def test_every_tile_layer_of_every_map_is_reported(self):
        # Ace's layer 3 is region << 8 | shadow, not tile ids: it is not a
        # tile layer and is reported under its own keys instead.
        out = self.call("map_critique", scopes=["tile_variety"])
        maps = {m["map_id"]: m for m in out["tile_variety"]["maps"]}
        self.assertEqual(sorted(maps), [1, 2])
        self.assertEqual(len(maps[1]["layers"]), 3)
        self.assertEqual([l["layer"] for l in maps[1]["layers"]], [0, 1, 2])
        self.assertIn("regions", maps[1])
        self.assertIn("shadows", maps[1])

    def test_layer_3_is_reported_as_regions_and_shadows(self):
        # Region 1 on three cells (one also shadowed), region 2 on one,
        # shadow mask 5 on two cells and 15 on one.
        words = [256, 256, 256 | 5, 512, 5, 15] + [0] * 294
        self.set_layer(1, 3, words)
        entry = next(m for m in self.call(
            "map_critique", scopes=["tile_variety"])["tile_variety"]["maps"]
            if m["map_id"] == 1)
        self.assertEqual(entry["regions"],
                         {"cells": 4, "by_id": [{"region": 1, "cells": 3},
                                                {"region": 2, "cells": 1}]})
        self.assertEqual(entry["shadows"],
                         {"cells": 3, "by_mask": [{"mask": 5, "cells": 2},
                                                  {"mask": 15, "cells": 1}]})
        self.assertNotIn(3, [l["layer"] for l in entry["layers"]])

    def test_the_method_says_layer_3_is_not_a_tile_layer(self):
        method = self.call("map_critique",
                           scopes=["tile_variety"])["tile_variety"]["method"]
        self.assertIn("region", method)
        self.assertIn("shadow", method)

    def test_it_runs_when_named_and_not_by_default(self):
        # Opt-in since the default reply became a summary: this table was
        # half of a 64 KB default reply on a real project. Named, it runs
        # exactly as it did.
        out = self.call("map_critique", scopes=["tile_variety"])
        self.assertIn("tile_variety", out["checks_run"])
        self.assertIn("tile_variety", out)
        out = self.call("map_critique")
        self.assertNotIn("tile_variety", out["checks_run"])
        self.assertNotIn("tile_variety", out)

    def test_a_cell_budget_skips_maps_and_says_so(self):
        from rpgvxace_mcp.analysis import map_tile_variety
        # Map 1 is 20x15x4 = 1200 cells, map 2 is 12x10x4 = 480.
        out = map_tile_variety(self.project, max_cells=500)
        self.assertEqual([m["map_id"] for m in out["maps"]], [2])
        self.assertEqual([s["map_id"] for s in out["skipped"]], [1])
        self.assertIn("budget", out["skipped"][0]["reason"])
        self.assertIn("tile_variety_stopped", out)


class TestShannonEntropy(unittest.TestCase):
    def test_known_values(self):
        from rpgvxace_mcp.analysis import shannon_entropy
        self.assertAlmostEqual(shannon_entropy([1, 1, 1, 1]), 2.0)
        self.assertAlmostEqual(shannon_entropy([5, 5]), 1.0)
        self.assertEqual(shannon_entropy([9]), 0.0)
        # Scale-free: the same distribution at ten times the cells.
        self.assertAlmostEqual(shannon_entropy([30, 10]),
                               shannon_entropy([3, 1]))

    def test_nothing_to_measure_is_none(self):
        from rpgvxace_mcp.analysis import shannon_entropy
        self.assertIsNone(shannon_entropy([]))
        self.assertIsNone(shannon_entropy([0, 0]))


class TestMapCritiqueSwitchUsage(ToolTest):
    """switch_usage: switches and variables read but never set, or the reverse.

    Ids are picked well clear of the demo project's own (1..3) so each test
    asserts about the ids it wrote and not about the fixture.
    """

    def add_event(self, commands, condition=None, x=7, y=7, map_id=1):
        commands = commands or [{"comment": "placeholder"}]
        out = self.call("event_edit", map_id=map_id, ops=[
            {"op": "create", "x": x, "y": y, "commands": commands}])
        event_id = out["applied"][0]["event_id"]
        if condition:
            self.call("event_edit", map_id=map_id, ops=[
                {"op": "set_page", "event_id": event_id, "page": 0,
                 "patch": {"condition": condition}}])
        return event_id

    def usage(self, **args):
        return self.call("map_critique", scopes=["switch_usage"],
                         **args)["switch_usage"]

    def ids(self, report, kind, which):
        return [row["id"] for row in report[kind][which]["shown"]]

    def row(self, report, kind, which, row_id):
        return [r for r in report[kind][which]["shown"]
                if r["id"] == row_id][0]

    def test_scope_is_listed_and_runs_by_default(self):
        out = self.call("map_critique")
        self.assertIn("switch_usage", out["checks_run"])
        self.assertIn("switch_usage", out)

    def test_a_page_condition_switch_nothing_sets_is_read_but_never_set(self):
        event_id = self.add_event([], {"switch1_valid": True,
                                       "switch1_id": 88})
        report = self.usage()
        self.assertIn(88, self.ids(report, "switches", "read_but_never_set"))
        row = self.row(report, "switches", "read_but_never_set", 88)
        self.assertEqual(row["reads"], 1)
        self.assertIn("event %d" % event_id, row["read_at"][0]["where"])
        self.assertFalse(row["read_at"][0]["holds_while_unset"])
        self.assertEqual(row["confidence"], "high")

    def test_a_switch_written_by_a_range_is_not_read_but_never_set(self):
        self.add_event([{"switch": 90, "switch_end": 93, "value": "on"}])
        self.add_event([{"if": {"switch": 92}, "then": []}], x=8)
        report = self.usage()
        self.assertNotIn(92, self.ids(report, "switches",
                                      "read_but_never_set"))

    def test_range_expands_for_set_but_never_read(self):
        self.add_event([{"switch": 100, "switch_end": 105, "value": "on"}])
        self.add_event([{"if": {"switch": 103}, "then": []}], x=8)
        report = self.usage()
        unread = self.ids(report, "switches", "set_but_never_read")
        for switch_id in (100, 101, 102, 104, 105):
            self.assertIn(switch_id, unread)
        self.assertNotIn(103, unread)
        row = self.row(report, "switches", "set_but_never_read", 101)
        self.assertEqual(row["writes"], 1)
        self.assertIn("100..105", row["set_at"][0]["how"])

    def test_a_huge_range_is_counted_not_enumerated(self):
        self.add_event([{"switch": 1000, "switch_end": 2000000000,
                         "value": "on"}])
        block = self.usage()["switches"]["set_but_never_read"]
        # Clamped to the engine's 5000 ids, so counted without expanding.
        self.assertGreaterEqual(block["total"], 5000 - 1000 + 1)
        self.assertLess(block["total"], 5000 - 1000 + 1 + 10)
        self.assertLessEqual(len(block["shown"]), 25)
        self.assertTrue(block["truncated"])

    def test_variables_are_tracked_including_the_operand_form(self):
        # variable 120 is only read as the *operand* of another variable's
        # assignment; variable 121 is the target of that assignment.
        self.add_event([{"variable": 121, "from_variable": 120, "op": "set"}])
        report = self.usage()
        self.assertIn(120, self.ids(report, "variables", "read_but_never_set"))
        self.assertIn(121, self.ids(report, "variables", "set_but_never_read"))

    def test_a_variable_page_condition_reports_what_zero_does(self):
        self.add_event([], {"variable_valid": True, "variable_id": 130,
                            "variable_value": 5})
        report = self.usage()
        row = self.row(report, "variables", "read_but_never_set", 130)
        self.assertFalse(row["read_at"][0]["holds_while_unset"])

    def test_a_literal_script_write_counts_as_a_write(self):
        self.add_event([{"script": "$game_switches[140] = true"}])
        self.add_event([{"if": {"switch": 140}, "then": []}], x=8)
        self.add_event([{"if": {"switch": 141}, "then": []}], x=9)
        report = self.usage()
        never_set = self.ids(report, "switches", "read_but_never_set")
        self.assertNotIn(140, never_set)
        self.assertIn(141, never_set)

    def test_script_comparison_is_a_read_not_a_write(self):
        self.add_event([{"script": "x = 1 if $game_switches[150] == true"}])
        report = self.usage()
        self.assertIn(150, self.ids(report, "switches", "read_but_never_set"))

    def test_a_non_literal_script_write_downgrades_confidence(self):
        self.add_event([{"if": {"switch": 160}, "then": []}])
        self.add_event([{"script": "$game_switches[Config::DOOR] = true"}],
                       x=8)
        report = self.usage()
        row = self.row(report, "switches", "read_but_never_set", 160)
        self.assertEqual(row["confidence"], "low")
        unknown = report["switches"]["script_access_unknown"]
        self.assertGreaterEqual(unknown["non_literal_writes"], 1)
        # variables were not touched by that script
        var_unknown = report["variables"]["script_access_unknown"]
        self.assertEqual(var_unknown["non_literal_writes"], 0)

    def test_an_aliased_game_switches_is_opaque(self):
        self.add_event([{"if": {"switch": 170}, "then": []}])
        self.add_event([{"script": "s = $game_switches"}], x=8)
        report = self.usage()
        unknown = report["switches"]["script_access_unknown"]
        self.assertGreaterEqual(unknown["opaque_references"], 1)
        row = self.row(report, "switches", "read_but_never_set", 170)
        self.assertEqual(row["confidence"], "low")

    def test_self_switches_are_a_separate_namespace_and_excluded(self):
        self.add_event([{"self_switch": "C", "value": "on"}],
                       {"self_switch_valid": True, "self_switch_ch": "D"})
        report = self.usage()
        self.assertTrue(report["self_switches"]["excluded"])
        self.assertGreaterEqual(report["self_switches"]["writes"], 1)
        self.assertGreaterEqual(report["self_switches"]["reads"], 1)

    def test_a_move_route_switch_command_is_a_write(self):
        self.add_event([
            {"raw": {"code": 205, "parameters": [0, {
                "__class__": "RPG::MoveRoute",
                "list": [{"__class__": "RPG::MoveCommand", "code": 27,
                          "parameters": [180]}]}]}}])
        self.add_event([{"if": {"switch": 180}, "then": []}], x=8)
        report = self.usage()
        self.assertNotIn(180, self.ids(report, "switches",
                                       "read_but_never_set"))
        self.assertNotIn(180, self.ids(report, "switches",
                                       "set_but_never_read"))

    def test_text_escape_reads_a_variable(self):
        self.add_event([{"variable": 190, "value": 3},
                        {"text": "You have \\V[190] herbs."}])
        report = self.usage()
        self.assertNotIn(190, self.ids(report, "variables",
                                       "set_but_never_read"))

    def test_common_event_trigger_switch_is_a_read(self):
        common = self.project.get_entry("common_events", 1)
        common.set("trigger", 2)
        common.set("switch_id", 200)
        self.project.save_file(self.project.filename_for("common_events"),
                               tool="test", summary="trigger")
        self.ctx = self.reopen()
        report = self.usage()
        self.assertIn(200, self.ids(report, "switches", "read_but_never_set"))

    def test_locations_are_bounded_and_counted(self):
        for i in range(6):
            self.add_event([], {"switch1_valid": True, "switch1_id": 210},
                           x=i)
        report = self.usage()
        row = self.row(report, "switches", "read_but_never_set", 210)
        self.assertEqual(row["reads"], 6)
        self.assertLess(len(row["read_at"]), 6)
        self.assertTrue(row["locations_truncated"])

    def test_findings_are_capped_with_a_total(self):
        for i in range(40):
            self.add_event([], {"switch1_valid": True,
                                "switch1_id": 300 + i}, x=i % 10, y=i // 10)
        block = self.usage()["switches"]["read_but_never_set"]
        self.assertGreaterEqual(block["total"], 40)
        self.assertLessEqual(len(block["shown"]), 25)
        self.assertTrue(block["truncated"])

    def test_the_other_scope_does_not_run_it(self):
        out = self.call("map_critique", scopes=["reachability"])
        self.assertNotIn("switch_usage", out)


class TestScripts(ToolTest):
    def test_list_and_get(self):
        listing = self.call("scripts", op="list")
        self.assertEqual(listing["total"], 3)
        self.assertEqual(listing["scripts"][2]["name"], "Main")
        script = self.call("scripts", op="get", index=2)
        self.assertIn("SceneManager", script["code"])

    def test_set_recompresses(self):
        self.call("scripts", op="set", index=2, code="# replaced\n")
        raw = marshal_rb.load(self.data_bytes("Scripts"))
        self.assertEqual(zlib.decompress(raw[2][2].data), b"# replaced\n")

    def test_set_rejects_bad_index(self):
        with self.assertRaises(ToolError):
            self.call("scripts", op="set", index=9, code="x")


class TestBackups(ToolTest):
    def test_snapshot_and_restore(self):
        snapshot = self.call("backups", op="create", label="before-edits")
        self.assertIn("Items" + self.extension, snapshot["files"])
        self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"name": "Wrong"}}])
        result = self.call("backups", op="restore", backup=snapshot["backup"])
        self.assertIn("Items" + self.extension, result["restored"])
        self.assertTrue(result["safety_backup"])
        entry = self.reopen().project.get_entry("items", 1)
        self.assertEqual(entry.get("name").data, b"Potion")

    def test_list_reports_snapshots(self):
        self.call("backups", op="create", label="one")
        listing = self.call("backups", op="list")
        self.assertTrue(listing["backups"])

    def test_restore_of_unknown_backup_lists_options(self):
        self.call("backups", op="create")
        with self.assertRaises(Exception) as caught:
            self.call("backups", op="restore", backup="nope")
        self.assertIn("available", str(caught.exception))


class TestRawData(ToolTest):
    def test_get_by_path(self):
        result = self.call("raw_data", table="system", path="party_members")
        self.assertEqual(result["value"], [1, 2])

    def test_get_deep_into_a_map(self):
        result = self.call("raw_data", map_id=1,
                           path="events/1/pages/0/list/1/parameters/0")
        self.assertIn("Welcome", result["value"])

    def test_set_by_path(self):
        self.call("raw_data", op="set", table="system", path="start_x",
                  value={"value": 9})
        self.assertEqual(self.reopen().project.load_data("system").get("start_x"), 9)

    def test_set_refuses_unknown_field(self):
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="set", table="system", path="nonsense",
                      value={"value": 1})
        self.assertIn("no field", str(caught.exception))

    def test_table_cells_point_at_map_tiles(self):
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", map_id=1, path="data/0")
        self.assertIn("map_tiles", str(caught.exception))

    def test_path_errors_list_available_fields(self):
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", map_id=1, path="widht")
        self.assertIn("width", str(caught.exception))


class TestReadOnly(ToolTest):
    read_only = True

    def test_reads_work(self):
        self.assertEqual(self.call("db_list", table="items")["matching_entries"], 2)

    def test_writes_are_refused(self):
        with self.assertRaises(ReadOnlyError):
            self.call("db_edit", table="items", ops=[
                {"op": "update", "id": 1, "patch": {"price": 1}}])

    def test_dry_run_still_works(self):
        result = self.call("db_edit", table="items", dry_run=True, ops=[
            {"op": "update", "id": 1, "patch": {"price": 1}}])
        self.assertTrue(result["write"]["dry_run"])


class TestConcurrentEditorProtection(ToolTest):
    def test_write_is_refused_after_the_file_changes_underneath(self):
        self.project.load_data("items")  # cache it
        path = self.tmp / "project" / "Data" / ("Items" + self.extension)
        # Stand in for the editor saving the project while we work.
        path.write_bytes(marshal_rb.dump([None, RubyObject("RPG::Item",
                                                           {"@id": 1,
                                                            "@name": RubyString(b"Edited")})]))
        with self.assertRaises(StaleFileError) as caught:
            self.call("db_edit", table="items", ops=[
                {"op": "update", "id": 1, "patch": {"price": 5}}])
        self.assertIn("editor", str(caught.exception))
        # The file on disk is untouched by the refused write.
        self.assertEqual(marshal_rb.load(path.read_bytes())[1].get("name").data, b"Edited")

    def test_refresh_recovers(self):
        self.project.load_data("items")
        path = self.tmp / "project" / "Data" / ("Items" + self.extension)
        raw = marshal_rb.load(path.read_bytes())
        raw[1].set("price", 7)
        path.write_bytes(marshal_rb.dump(raw))
        result = self.call("db_list", table="items", refresh=True)
        self.assertEqual(result["entries"][0]["price"], 7)
        self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"price": 8}}])
        self.assertEqual(self.reopen().project.get_entry("items", 1).get("price"), 8)


class TestEngineDetection(unittest.TestCase):
    def stub(self, project_file: str, data_file: str) -> Path:
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-engine-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "Data").mkdir(parents=True)
        (tmp / project_file).write_text("x", encoding="ascii")
        (tmp / "Data" / data_file).write_bytes(marshal_rb.dump([None]))
        return tmp

    def test_ace_and_vx_open_without_a_flag(self):
        ace = Project(self.stub("Game.rvproj2", "Actors.rvdata2"))
        self.assertEqual((ace.engine, ace.extension), ("vx_ace", ".rvdata2"))
        self.assertIn("tilesets", ace.data_files)
        vx = Project(self.stub("Game.rvproj", "Actors.rvdata"))
        self.assertEqual((vx.engine, vx.extension), ("vx", ".rvdata"))
        self.assertIn("areas", vx.data_files)

    def test_xp_opens_without_a_flag_and_is_modelled_as_xp(self):
        # This needed --allow-other-engines and was described with the VX
        # model until XP had a profile of its own.
        for root in (self.stub("Game.rxproj", "Actors.rxdata"),
                     self.stub("Unrelated.txt", "Actors.rxdata")):
            with self.subTest(root=root.name):
                project = Project(root)
                self.assertEqual((project.engine, project.extension),
                                 ("xp", ".rxdata"))
                self.assertEqual(project.profile.key, "xp")
                self.assertIn("tilesets", project.data_files)
                self.assertNotIn("areas", project.data_files)
                self.assertEqual(project.profile.label, "RPG Maker XP")

    def test_mv_is_refused_outright(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-mv-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "Data").mkdir(parents=True)
        (tmp / "Data" / "Actors.json").write_text("[]", encoding="utf-8")
        for allow in (False, True):
            with self.assertRaises(Exception) as caught:
                Project(tmp, allow_other_engines=allow)
            self.assertIn("JSON", str(caught.exception))

    def test_engine_guessed_from_data_when_no_project_file(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-bare-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "Data").mkdir(parents=True)
        (tmp / "Data" / "Actors.rvdata2").write_bytes(marshal_rb.dump([None]))
        project = Project(tmp)
        self.assertEqual(project.engine, "vx_ace")
        self.assertIn("guessed", project.engine_note)

    def test_directory_without_data_is_reported(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-empty-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(Exception) as caught:
            Project(tmp)
        self.assertIn("Game.rvproj2", str(caught.exception))


class TestJapaneseEncoding(unittest.TestCase):
    """A Shift_JIS VX project must survive a read/write round trip.

    This is a VX case by nature: RGSS2 strings are bare bytes in the editor's
    code page, whereas Ace's Ruby 1.9 strings carry an encoding and are UTF-8.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-cp932-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build(self.tmp / "project", "vx")
        path = self.tmp / "project" / "Data" / "Items.rvdata"
        items = marshal_rb.load(path.read_bytes())
        items[1].set("name", RubyString("ポーション".encode("cp932")))
        items[1].set("description", RubyString("HPを100かいふくする。".encode("cp932")))
        path.write_bytes(marshal_rb.dump(items))

    def test_detects_cp932_and_reads_the_text(self):
        project = Project(self.tmp / "project")
        self.assertEqual(project.codec.name, "cp932")
        ctx = Context(project)
        entry = REGISTRY["db_list"].handler(ctx, {"table": "items"})["entries"][0]
        self.assertEqual(entry["name"], "ポーション")

    def test_writes_stay_in_cp932(self):
        ctx = Context(Project(self.tmp / "project"))
        REGISTRY["db_edit"].handler(ctx, {"table": "items", "ops": [
            {"op": "update", "id": 1, "patch": {"name": "エリクサー"}}]})
        raw = marshal_rb.load((self.tmp / "project" / "Data" / "Items.rvdata").read_bytes())
        self.assertEqual(raw[1].get("name").data, "エリクサー".encode("cp932"))

    def test_untranslatable_character_is_refused_clearly(self):
        ctx = Context(Project(self.tmp / "project"))
        with self.assertRaises(Exception) as caught:
            REGISTRY["db_edit"].handler(ctx, {"table": "items", "ops": [
                {"op": "update", "id": 1, "patch": {"name": "Ω≈ç√"}}]})
        self.assertIn("--encoding", str(caught.exception))


class TestProjectInfoVX(TestProjectInfo):
    engine = "vx"


class TestDatabaseVX(TestDatabase):
    engine = "vx"


class TestMapsVX(TestMaps):
    engine = "vx"


class TestEventsVX(TestEvents):
    engine = "vx"


class TestCommandReferenceVX(TestCommandReference):
    engine = "vx"


class TestSearchVX(TestSearch):
    engine = "vx"


class TestValidateVX(TestValidate):
    engine = "vx"


class TestRawDataVX(TestRawData):
    engine = "vx"


# XP re-runs the suites whose subject it shares: its database, maps, events
# and raw data are read and written by the same tools. Validate and search
# run too, on XP's own fixture -- their command rules are the VX family's,
# and the XP runs show what they do with XP's commands.
#
# Where a shared test asserts something the VX family's data holds and XP's
# does not -- a face in Show Text, a 2816 in the tile layers, a 230 for
# Wait -- the XP class overrides it with XP's version of the same check.
class TestProjectInfoXP(TestProjectInfo):
    engine = "xp"

    def test_schema_is_observed_from_the_files(self):
        info = self.call("project_info", include_schema=True)
        actor = info["schema"]["classes"]["RPG::Actor"]
        self.assertIn("class_id", actor["fields"])
        # Every class is documented for XP, the database ones included,
        # because the RGSS1 reference documents them all.
        for cls in ("RPG::Map", "RPG::Actor", "RPG::Tileset", "RPG::System"):
            with self.subTest(cls=cls):
                self.assertEqual(info["schema"]["classes"][cls]
                                 ["fields_without_a_description"], [])
        self.assertEqual(len(info["schema"]["map_layers"]), 3)
        self.assertEqual(info["schema"]["tile_id_ranges"]["tileset"][0], 384)
        self.assertIn("0x40", info["schema"]["tileset_passage_bits"])

    def test_table_list_matches_the_engine(self):
        tables = {f["table"] for f in self.call("project_info")["data_files"]}
        self.assertIn("tilesets", tables)
        self.assertNotIn("areas", tables)

    def test_absent_table_explains_the_engine_difference(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_list", table="areas")
        message = str(caught.exception)
        self.assertIn("has no areas table", message)
        self.assertIn("encounter_list", message)


class TestDatabaseXP(TestDatabase):
    engine = "xp"

    def test_update_patches_only_named_fields(self):
        before = self.call("db_get", table="items", id=1)["entries"][0]["entry"]
        self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"name": "Herb", "price": 25}}])
        entry = REGISTRY["db_get"].handler(
            self.reopen(), {"table": "items", "id": 1})["entries"][0]["entry"]
        self.assertEqual((entry["name"], entry["price"]), ("Herb", 25))
        # XP names an icon by file; the VX family indexes a sheet.
        self.assertEqual(entry["icon_name"], before["icon_name"])

    def test_a_blank_entry_takes_its_siblings_field_order(self):
        sibling = self.ctx.project.get_entry("items", 1).fields()
        self.call("db_edit", table="items", ops=[
            {"op": "create", "patch": {"price": 7, "icon_name": "x",
                                       "name": "Twig"}}])
        entry = self.reopen().project.get_entry("items", 3)
        self.assertEqual(entry.fields(),
                         [field for field in sibling
                          if field in {"name", "price", "icon_name", "id"}])


class TestMapsXP(TestMaps):
    engine = "xp"

    def test_get_reports_properties_events_and_layers(self):
        result = self.call("map_get", map_id=1, tiles="counts")
        self.assertEqual(result["properties"]["encounter_step"], 30)
        self.assertEqual(result["tile_table"]["zsize"], 3)
        for z in range(3):
            self.assertIn("tile layer", result["tile_table"]["layers"][str(z)])
        elder = result["events"][0]
        self.assertEqual((elder["name"], elder["x"], elder["y"]),
                         ("Elder", 5, 4))
        self.assertEqual(elder["pages"][1]["conditions"], ["switch 3 is ON"])
        ground = result["tile_counts"]["0"]["most_common"][0]
        self.assertEqual((ground["tile_id"], ground["count"], ground["sheet"]),
                         (384, 300, "tileset"))
        top = result["tile_counts"]["1"]["most_common"]
        self.assertIn({"tile_id": 48, "count": 20, "sheet": "autotile 1"}, top)

    def test_tiles_read_and_write(self):
        rows = self.call("map_tiles", map_id=1, x=0, y=0, width=3,
                         height=2)["rows"]
        self.assertEqual(rows, [[384] * 3, [384] * 3])
        self.call("map_tiles", map_id=1, op="write", x=1, y=1, width=2,
                  height=1, rows=[[385, 386]])
        after = REGISTRY["map_tiles"].handler(
            self.reopen(), {"map_id": 1, "x": 0, "y": 1, "width": 3,
                            "height": 1})
        self.assertEqual(after["rows"], [[384, 385, 386]])

    def test_created_map_is_loadable_marshal(self):
        self.call("map_edit", op="create", name="Cellar")
        raw = self.data_bytes("Map003")
        self.assertEqual(marshal_rb.dump(marshal_rb.load(raw)), raw)
        game_map = self.reopen().project.load_map(3)
        self.assertEqual(game_map.get("data").zsize, 3)
        # XP's maps name a tileset, as VX Ace's do and VX's do not.
        self.assertTrue(game_map.has("tileset_id"))
        self.assertFalse(game_map.has("parallax_name"))


class TestEventsXP(TestEvents):
    engine = "xp"

    def test_get_renders_a_readable_script(self):
        result = self.call("event_get", map_id=1, event_ids=[1])
        page = result["events"][0]["pages"][0]
        self.assertEqual(page["trigger"], "action button")
        script = page["script"]
        # XP's Show Text holds its first line itself.
        self.assertIn("Show Text: Welcome to Riverbend", script[0])
        self.assertIn("bridge east", script[1])
        self.assertIn("switch 1 is ON", script[2])
        self.assertTrue(script[3].startswith("  3|   "))

    def test_get_includes_raw_commands_on_request(self):
        page = self.call("event_get", map_id=1, event_ids=[1],
                         include_raw=True)["events"][0]["pages"][0]
        first = page["commands"][0]
        self.assertEqual(first["code"], 101)
        self.assertEqual(first["parameters"],
                         ["Welcome to Riverbend, traveller."])

    def test_create_event_from_the_dsl(self):
        result = self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 9, "y": 7, "name": "Signpost",
            "patch": {"graphic": {"character_name": "People1",
                                  "character_hue": 30},
                      "trigger": 0},
            "commands": [
                {"text": "The road east leads to Riverbend."},
                {"if": {"switch": 1, "is": "on"},
                 "then": [{"text": "The gate stands open."},
                          {"switch": 2, "value": "on"}],
                 "else": [{"text": "The gate is shut."}]},
            ],
        }])
        event_id = result["applied"][0]["event_id"]
        page = REGISTRY["event_get"].handler(
            self.reopen(), {"map_id": 1, "event_ids": [event_id],
                            "include_raw": True})["events"][0]["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        self.assertEqual(codes, [101, 111, 101, 121, 411, 101, 412, 0])
        indents = [c["indent"] for c in page["commands"]]
        self.assertEqual(indents, [0, 0, 1, 1, 0, 1, 0, 0])
        self.assertEqual(page["graphic"]["character_hue"], 30)

    def test_long_text_splits_into_message_boxes(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 1, "y": 1,
            "commands": [{"text": "one\ntwo\nthree\nfour\nfive"}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        self.assertEqual(codes, [101, 401, 401, 401, 101, 0])

    def test_choices_compile_with_branches(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 2, "y": 2,
            "commands": [{
                "choices": ["Yes", "No"],
                "branches": [[{"switch": 1, "value": "on"}],
                             [{"text": "Maybe later."}]],
                "cancel_branch": [{"text": "..."}],
            }],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        self.assertEqual(codes, [102, 402, 121, 402, 101, 403, 101, 404, 0])
        self.assertEqual(page["commands"][0]["parameters"], [["Yes", "No"], 5])

    def test_loop_compiles(self):
        # XP's Wait is 106, so it is written raw; the loop form is shared.
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 3, "y": 3,
            "commands": [{"loop": [{"raw": {"code": 106, "parameters": [30]}},
                                   {"break_loop": True}]}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        self.assertEqual([c["code"] for c in page["commands"]],
                         [112, 106, 113, 413, 0])

    def test_move_route_gains_its_editor_step_lines(self):
        # XP's Set Move Route is 209 and its step lines 509; its 205 is a
        # fog tone, and gains nothing.
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 6, "y": 6,
            "commands": [
                {"raw": {"code": 209, "parameters": [
                    0, {"__class__": "RPG::MoveRoute", "repeat": False,
                        "skippable": False,
                        "list": [{"code": 1}, {"code": 39}]}]}},
                {"raw": {"code": 205, "parameters": [
                    {"__class__": "Tone", "red": 0, "green": 0, "blue": 0,
                     "gray": 0}, 20]}}],
        }])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        self.assertEqual([c["code"] for c in page["commands"]],
                         [209, 509, 509, 205, 0])
        self.assertIn("Move Down", page["script"][1])
        self.assertIn("Always on Top ON", page["script"][2])

    def test_common_event_commands_can_be_replaced(self):
        self.call("event_edit", common_event_id=1, ops=[{
            "op": "set_commands",
            "commands": [{"text": "Your wounds close."},
                         {"raw": {"code": 314, "parameters": [0]}}],
        }])
        result = self.call("event_get", common_event_id=1)
        self.assertIn("Your wounds close", result["script"][0])
        self.assertIn("Recover All", result["script"][1])


class TestCommandReferenceXP(TestCommandReference):
    engine = "xp"

    def test_shop_processing_layout_differs_by_engine(self):
        # XP's command_302 reads [kind, id] and nothing else: no price and
        # no purchase-only flag.
        spec = self.call("command_reference", code=302)["parameters"]
        goods = self.call("command_reference", code=605)["parameters"]
        self.assertEqual(spec, ["goods_type", "goods_id"])
        self.assertEqual(goods, ["goods_type", "goods_id"])

    def test_the_layouts_are_xp_s_own(self):
        result = self.call("command_reference", code=209)
        self.assertEqual(result["name"], "Set Move Route")
        self.assertTrue(result["layouts_are_this_engines"])
        self.assertNotIn("layout_notes", result)
        listing = self.call("command_reference")
        self.assertIn("raw", listing["authoring_note"])
        moves = self.call("command_reference", move_commands=True)
        self.assertIn({"code": 39, "name": "Always on Top ON"},
                      moves["move_commands"])


class TestSearchXP(TestSearch):
    engine = "xp"


class TestValidateXP(TestValidate):
    engine = "xp"


class TestRawDataXP(TestRawData):
    engine = "xp"

    def test_get_deep_into_a_map(self):
        # The second line of the greeting: XP's first is in the 101.
        result = self.call("raw_data", map_id=1,
                           path="events/1/pages/0/list/1/parameters/0")
        self.assertIn("bridge east", result["value"])


class TestXPProject(ToolTest):
    """What XP has of its own, and what it is refused."""

    engine = "xp"

    def usage(self, commands):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 9, "y": 9, "commands": commands}])
        return self.call("map_critique",
                         scopes=["switch_usage"])["switch_usage"]

    def test_a_battle_names_its_troop_in_xp_s_place(self):
        # XP's command_301 takes the troop from parameters[0]; VX's rule
        # reads parameters[1] when parameters[0] is 0, which on XP is the
        # can-escape flag.
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 9, "y": 9, "commands": [
                {"raw": {"code": 301, "parameters": [2, False, False]}},
                {"raw": {"code": 301, "parameters": [7, True, False]}}]}])
        missing = [f for f in self.call("validate", scopes=["events"])
                   ["findings"] if f["rule"] == "missing_troop"]
        self.assertEqual(len(missing), 1)
        self.assertIn("troop id 7", missing[0]["message"])
        result = self.call("db_edit", table="troops",
                           ops=[{"op": "delete", "id": 2}])
        self.assertEqual(result["applied"][0]["references_found"], 1)

    def test_xp_s_show_text_is_not_read_as_a_face(self):
        # XP's 101 holds a line of text where VX's holds a face graphic, so
        # VX's asset rule would report every message as a missing face.
        findings = self.call("validate")["findings"]
        self.assertEqual([f for f in findings
                          if "face" in f["message"].lower()], [])

    def test_an_autorun_that_erases_itself_is_not_reported(self):
        # XP's Erase Event is 116; VX's 214 is not a command on XP.
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_commands", "event_id": 2, "page": 0,
            "commands": [{"text": "Once."},
                         {"raw": {"code": 116, "parameters": []}}]}, {
            "op": "set_page", "event_id": 2, "page": 0,
            "patch": {"trigger": 3}}])
        rules = {f["rule"] for f in
                 self.call("validate", scopes=["events"])["findings"]}
        self.assertNotIn("unstoppable_autorun", rules)
        # And without it, the same page is reported.
        self.call("event_edit", map_id=1, ops=[{
            "op": "set_commands", "event_id": 2, "page": 0,
            "commands": [{"text": "Forever."}]}])
        rules = {f["rule"] for f in
                 self.call("validate", scopes=["events"])["findings"]}
        self.assertIn("unstoppable_autorun", rules)

    def test_switch_usage_reads_xp_s_commands(self):
        report = self.usage([
            # XP's route is 209, and its step 27 turns switch 3 on.
            {"raw": {"code": 209, "parameters": [0, {
                "__class__": "RPG::MoveRoute", "repeat": False,
                "skippable": False,
                "list": [{"code": 27, "parameters": [3]}, {"code": 0}]}]}},
            # Change Text Options: VX Ace's 104 writes a variable; XP's
            # sets the window's position and frame.
            {"raw": {"code": 104, "parameters": [2, 0]}},
            # Change HP from variable 1: XP's operate_value reads
            # parameters 1-3, one place earlier than VX's command_311.
            {"raw": {"code": 311, "parameters": [0, 0, 1, 1, False]}},
            # XP's Show Text holds its first line, \V[2] and all.
            {"text": "You carry \\V[2] herbs."},
        ])
        never_set = [row["id"] for row in
                     report["switches"]["read_but_never_set"]["shown"]]
        self.assertNotIn(3, never_set)
        variables = report["variables"]
        self.assertEqual(variables["set_but_never_read"]["shown"], [])
        self.assertEqual(sorted(row["id"] for row in
                                variables["read_but_never_set"]["shown"]),
                         [1, 2])

    def test_the_tileset_is_xp_s(self):
        entry = self.call("db_get", table="tilesets",
                          id=1)["entries"][0]["entry"]
        self.assertEqual(entry["__class__"], "RPG::Tileset")
        self.assertEqual(entry["tileset_name"], "001-Grassland01")
        self.assertEqual(len(entry["autotile_names"]), 7)
        for field in ("passages", "priorities", "terrain_tags"):
            self.assertEqual(entry[field]["xsize"], 384 + 8)

    def test_a_created_map_is_an_xp_map(self):
        result = self.call("map_edit", op="create", name="Field",
                           width=10, height=8, tileset_id=1)
        game_map = self.reopen().project.load_map(result["map_id"])
        self.assertEqual(sorted(game_map.fields()), sorted(
            self.project.profile.class_fields["RPG::Map"]))
        self.assertEqual(game_map.get("data").zsize, 3)
        self.assertEqual(game_map.get("bgm").cls, "RPG::AudioFile")
        with self.assertRaises(ToolError):
            self.call("map_edit", op="create", name="Nowhere", tileset_id=9)

    def test_a_created_event_has_xp_s_page(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 9, "y": 9,
            "commands": [{"text": "Hello"}, {"switch": 2}]}])
        events = self.reopen().project.load_map(1).get("events")
        event = max((e for _, e in events.items()), key=lambda e: e.get("id"))
        page = event.get("pages")[0]
        self.assertTrue(page.has("always_on_top"))
        self.assertFalse(page.has("priority_type"))
        self.assertTrue(page.get("graphic").has("opacity"))
        self.assertEqual([c.get("code") for c in page.get("list")],
                         [101, 121, 0])
        script = self.call("event_get", map_id=1, event_ids=[
            event.get("id")])["events"][0]["pages"][0]
        self.assertIn("Show Text: Hello", "\n".join(script["script"]))

    def test_event_get_names_xp_commands(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 8, "y": 9, "commands": [
                {"raw": {"code": 106, "parameters": [20]}},
                {"raw": {"code": 209, "parameters": [-1, {
                    "__class__": "RPG::MoveRoute", "repeat": False,
                    "skippable": False, "list": [{"code": 0}]}]}}]}])
        page = self.call("event_get", map_id=1, name_contains="EV",
                         include_raw=True)["events"][0]["pages"][0]
        script = "\n".join(page["script"])
        self.assertIn("Wait: 20 frames", script)
        self.assertIn("Set Move Route: player", script)
        self.assertNotIn("Set Vehicle Location", script)

    def test_ace_only_tools_refuse_xp_and_say_why(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_render", map_id=1)
        self.assertIn("RPG Maker XP", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, view="region")
        self.assertIn("RPG Maker XP", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, ops=[{
                "op": "create", "x": 7, "y": 9,
                "commands": [{"gold": {"op": "add", "value": 1}}]}])
        self.assertIn("RPG Maker XP", str(caught.exception))
        # Autotile painting and live play-testing are VX Ace's too.
        with self.assertRaises(ToolError) as caught:
            self.call("map_paint", op="fill", map_id=1, kind="A2:0", x=0,
                      y=0, width=2, height=2)
        self.assertIn("RPG Maker XP", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.call("live", op="start")
        self.assertIn("RPG Maker XP", str(caught.exception))

    def test_validate_says_which_rules_it_skipped(self):
        report = self.call("validate")
        self.assertIn("XP", report["engine_warning"])
        self.assertIn("skipped", report["engine_warning"])

    def test_the_documents_describe_xp(self):
        from rpgvxace_mcp.docs import (
            command_reference_document,
            data_format_reference,
        )
        data = data_format_reference(self.project)
        self.assertIn("RPG Maker XP", data)
        self.assertIn("autotile_names", data)
        self.assertIn("48 to 95", data)
        self.assertIn("priorities", data)
        self.assertNotIn("described with", data)
        commands = command_reference_document(self.project)
        self.assertIn("| 106 | Wait |", commands)
        self.assertIn("| 209 | Set Move Route |", commands)
        self.assertNotIn("| 230 |", commands)
        # map_paint refuses XP, so the workflow does not offer it.
        from rpgvxace_mcp.docs import workflow_document
        workflow = workflow_document(self.project)
        self.assertNotIn("`map_paint` paints", workflow)
        self.assertIn("On XP only the basic forms compile", workflow)


class TestAceTilesets(ToolTest):
    def test_tileset_table_is_readable(self):
        rows = self.call("db_list", table="tilesets")["entries"]
        self.assertEqual([r["name"] for r in rows], ["Field", "Dungeon"])
        self.assertEqual(rows[0]["mode"], 0)

    def test_tileset_sheets_and_flags(self):
        entry = self.call("db_get", table="tilesets", id=1)["entries"][0]["entry"]
        self.assertEqual(entry["tileset_names"][0], "World_A1")
        self.assertEqual(len(entry["tileset_names"]), 9)
        # 8192 per-tile flag words, summarised rather than dumped.
        self.assertEqual(entry["flags"]["size"], 8192)
        self.assertEqual(entry["flags"]["dim"], 1)

    def test_map_names_its_tileset(self):
        properties = self.call("map_get", map_id=2)
        self.assertEqual(properties["other_fields"].count("tileset_id"), 1)
        raw = self.call("raw_data", map_id=2, path="tileset_id")
        self.assertEqual(raw["value"], 2)

    def test_new_map_gets_a_tileset(self):
        self.call("map_edit", op="create", name="Vault", tileset_id=2)
        self.assertEqual(
            self.call("raw_data", map_id=3, path="tileset_id")["value"], 2)

    def test_validate_flags_a_missing_tileset(self):
        self.call("raw_data", op="set", map_id=2, path="tileset_id",
                  value={"value": 9})
        findings = self.call("validate", scopes=["maps"])["findings"]
        rules = {f["rule"] for f in findings}
        self.assertIn("missing_tileset", rules)

    def test_validate_flags_a_missing_tileset_graphic(self):
        self.call("db_edit", table="tilesets", ops=[
            {"op": "update", "id": 1, "patch": {"tileset_names": [
                "NoSuchSheet", "", "", "", "", "", "", "", ""]}}])
        findings = self.call("validate", scopes=["maps"])["findings"]
        graphic = [f for f in findings if f["rule"] == "missing_graphic"]
        self.assertTrue(graphic)
        self.assertIn("sheet A1", graphic[0]["message"])

    def test_areas_table_is_absent_with_a_pointer_to_regions(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_list", table="areas")
        self.assertIn("region", str(caught.exception))


class TestAceRegionsAndEncounters(ToolTest):
    def test_region_layer_is_readable(self):
        # The fixture paints region 1 the way the editor stores it: 256 on
        # layer 3, as 337 cells of the author's game do. The region view decodes
        # it; the raw word is still there for a caller that wants it.
        window = dict(map_id=2, x=0, y=5, width=3, height=1)
        self.assertEqual(self.call("map_tiles", view="region", **window)["rows"],
                         [[1, 1, 1]])
        self.assertEqual(self.call("map_tiles", view="shadow", **window)["rows"],
                         [[0, 0, 0]])
        self.assertEqual(self.call("map_tiles", z=3, **window)["rows"],
                         [[256, 256, 256]])

    def test_region_layer_is_writable(self):
        self.call("map_tiles", map_id=2, op="fill", x=0, y=0, width=12, height=2,
                  view="region", tile_id=2)
        window = dict(map_id=2, x=0, y=0, width=2, height=1)
        self.assertEqual(self.call("map_tiles", view="region", **window)["rows"],
                         [[2, 2]])
        self.assertEqual(self.call("map_tiles", z=3, **window)["rows"],
                         [[512, 512]])

    def test_writing_a_region_keeps_the_shadow_and_back(self):
        window = dict(map_id=2, x=0, y=0, width=2, height=1)
        self.call("map_tiles", op="write", z=3, rows=[[5, 15]], **window)
        self.call("map_tiles", op="fill", view="region", tile_id=3, **window)
        self.assertEqual(self.call("map_tiles", z=3, **window)["rows"],
                         [[0x305, 0x30F]])
        self.call("map_tiles", op="write", view="shadow", rows=[[0, 4]],
                  **window)
        self.assertEqual(self.call("map_tiles", z=3, **window)["rows"],
                         [[0x300, 0x304]])
        self.assertEqual(self.call("map_tiles", view="region", **window)["rows"],
                         [[3, 3]])

    def test_the_tile_tally_decodes_layer_3_instead_of_naming_a_sheet(self):
        # 256 is region 1, not a tile from sheet C.
        counts = self.call("map_get", map_id=2, tiles="counts")["tile_counts"]
        top = {e["tile_id"]: e for e in counts["3"]["most_common"]}
        self.assertEqual((top[256]["region"], top[256]["shadow"]), (1, 0))
        self.assertNotIn("sheet", top[256])
        self.assertIn("sheet", counts["2"]["most_common"][0])

    def test_a_region_written_raw_round_trips(self):
        # Region 11 with no shadow is 2816: a legal word on layer 3, not an
        # overflow of a 0-63 field.
        window = dict(map_id=2, x=0, y=0, width=1, height=1)
        self.call("map_tiles", op="fill", z=3, tile_id=11 << 8, **window)
        self.assertEqual(self.call("map_tiles", view="region", **window)["rows"],
                         [[11]])

    def test_encounter_entries_are_objects_not_ids(self):
        # Ace wraps each encounter in RPG::Map::Encounter; reading one as a bare
        # troop id used to make validate report every encounter as missing.
        encounters = self.call("map_get", map_id=2)["properties"]["encounter_list"]
        self.assertEqual(encounters[0]["__class__"], "RPG::Map::Encounter")
        self.assertEqual(encounters[0]["troop_id"], 1)
        findings = self.call("validate", scopes=["maps"])["findings"]
        self.assertNotIn("missing_troop", {f["rule"] for f in findings})

    def test_validate_still_finds_a_bad_encounter_troop(self):
        self.call("raw_data", op="set", map_id=2, path="encounter_list/0/troop_id",
                  value={"value": 77})
        findings = self.call("validate", scopes=["maps"])["findings"]
        self.assertIn("missing_troop", {f["rule"] for f in findings})


class TestAceStringStyle(ToolTest):
    def test_strings_read_as_plain_text(self):
        # Every Ace string carries an encoding ivar; that is bookkeeping and must
        # not clutter the value an agent reads.
        entry = self.call("db_get", table="items", id=1)["entries"][0]["entry"]
        self.assertEqual(entry["name"], "Potion")
        self.assertIsInstance(entry["description"], str)

    def test_written_strings_keep_the_engine_style(self):
        self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"name": "Tonic"}}])
        entry = self.reopen().project.get_entry("items", 1)
        self.assertEqual(entry.get("name").data, b"Tonic")
        self.assertEqual(entry.get("name").ivars, {"E": True})

    def test_authored_event_strings_keep_the_engine_style(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 2, "y": 2, "name": "Sign",
            "commands": [{"text": "Beware the cave."}]}])
        game_map = self.reopen().project.load_map(1)
        event = game_map.get("events").get(3)
        self.assertEqual(event.get("name").ivars, {"E": True})
        line = event.get("pages")[0].get("list")[1]
        self.assertEqual(line.get("parameters")[0].ivars, {"E": True})

    def test_non_ascii_survives_a_round_trip(self):
        self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"name": "Élixir d'Or"}}])
        listing = self.call("db_list", table="items", refresh=True)
        self.assertEqual(listing["entries"][0]["name"], "Élixir d'Or")
        entry = self.reopen().project.get_entry("items", 1)
        self.assertEqual(entry.get("name").data, "Élixir d'Or".encode("utf-8"))


class TestAceCommands(ToolTest):
    def test_ace_only_command_round_trips(self):
        # 285 Get Location Info does not exist in VX.
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 4, "y": 4,
            "commands": [{"raw": {"code": 285, "parameters": [1, 0, 0, 5, 6]}}]}])
        page = self.call("event_get", map_id=1, event_ids=[3],
                         include_raw=True)["events"][0]["pages"][0]
        self.assertEqual(page["commands"][0]["code"], 285)
        self.assertIn("Get Location Info", page["script"][0])

    def test_scrolling_text_lines_are_named(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 5, "y": 5,
            "commands": [{"raw": {"code": 105, "parameters": [2, False]}},
                         {"raw": {"code": 405, "parameters": ["A long time ago"]}}]}])
        script = self.call("event_get", map_id=1, event_ids=[3],
                           include_raw=True)["events"][0]["pages"][0]["script"]
        self.assertIn("Show Scrolling Text", script[0])
        self.assertIn("A long time ago", script[1])


class TestRunTimePackage(ToolTest):
    """Assets can live in the RTP, outside the project folder.

    Reporting those as missing produced hundreds of false warnings on a real
    project, so an unlocatable RTP has to change what a finding claims.
    """

    def declare_rtp(self, name: str = "RPGVXAce") -> None:
        ini = self.tmp / "project" / "Game.ini"
        ini.write_text("[Game]\nRTP=%s\nTitle=Demo Quest\n" % name, encoding="ascii")

    def test_no_rtp_declared_means_the_asset_pool_is_complete(self):
        info = self.call("project_info")
        self.assertEqual(info["rtp"]["declared"], [])
        self.assertTrue(self.project.assets_complete)
        self.assertIn("no RTP", info["rtp"]["note"])

    def test_unlocatable_rtp_softens_missing_asset_findings(self):
        self.declare_rtp("NoSuchRTP-ForTesting")
        project = Project(self.tmp / "project")
        self.assertEqual(project.rtp_names, ["NoSuchRTP-ForTesting"])
        self.assertEqual(project.rtp_unresolved, ["NoSuchRTP-ForTesting"])
        self.assertFalse(project.assets_complete)

        ctx = Context(project)
        REGISTRY["map_edit"].handler(ctx, {
            "op": "update", "map_id": 1, "patch": {"bgm": {"name": "FromTheRTP"}}})
        findings = REGISTRY["validate"].handler(ctx, {"scopes": ["assets"]})["findings"]
        audio = [f for f in findings if f["rule"] == "missing_audio"]
        self.assertTrue(audio)
        # Reported as a possibility, not as a fact.
        self.assertEqual(audio[0]["severity"], "info")
        self.assertIn("could not be located", audio[0]["message"])

    def test_explicit_rtp_path_widens_the_asset_pool(self):
        rtp = self.tmp / "fake-rtp"
        (rtp / "Audio" / "BGM").mkdir(parents=True)
        (rtp / "Audio" / "BGM" / "FromTheRTP.ogg").write_bytes(b"")
        self.declare_rtp()
        project = Project(self.tmp / "project", rtp_paths=[str(rtp)])
        self.assertTrue(project.assets_complete)
        self.assertIn("FromTheRTP", project.asset_names("bgm"))
        # The project's own files are still visible alongside the RTP's.
        self.assertIn("Town1", project.asset_names("bgm"))

        ctx = Context(project)
        REGISTRY["map_edit"].handler(ctx, {
            "op": "update", "map_id": 1, "patch": {"bgm": {"name": "FromTheRTP"}}})
        findings = REGISTRY["validate"].handler(ctx, {"scopes": ["assets"]})["findings"]
        self.assertNotIn("missing_audio", {f["rule"] for f in findings})

    def test_bad_rtp_path_is_rejected(self):
        with self.assertRaises(Exception) as caught:
            Project(self.tmp / "project", rtp_paths=[str(self.tmp / "nope")])
        self.assertIn("--rtp path does not exist", str(caught.exception))


class TestRealProject(unittest.TestCase):
    """Read-only checks against a real project, when one is provided.

    Set RPGVXACE_TEST_PROJECT to a project folder to run these. Skipped otherwise
    so the suite stays self-contained.
    """

    def setUp(self):
        path = os.environ.get("RPGVXACE_TEST_PROJECT")
        if not path:
            self.skipTest("set RPGVXACE_TEST_PROJECT to a project folder")
        self.project = Project(path, read_only=True)
        self.ctx = Context(self.project)

    def call(self, tool_name, /, **arguments):
        return REGISTRY[tool_name].handler(self.ctx, arguments)

    def test_every_data_file_round_trips_byte_for_byte(self):
        mismatched = []
        for path in sorted(self.project.data_dir.glob("*" + self.project.extension)):
            raw = path.read_bytes()
            if marshal_rb.dump(marshal_rb.load(raw)) != raw:
                mismatched.append(path.name)
        self.assertEqual(mismatched, [])

    def test_dead_ends_reads_and_is_internally_consistent(self):
        out = self.call("map_critique", scopes=["dead_ends"])["dead_ends"]
        reach = self.call("map_critique", scopes=["reachability"]
                          )["reachability"]
        stuck = [e["map_id"] for e in out["no_exit"]]
        stuck += [m for r in out["closed_regions"] for m in r["maps"]]
        self.assertTrue(set(stuck + out["one_way"])
                        <= set(reach["reachable"]))
        self.assertFalse(set(stuck) & set(out["one_way"]))

    def test_project_reads(self):
        info = self.call("project_info")
        self.assertIn(info["engine"], ("vx_ace", "vx"))
        self.assertTrue(info["entry_counts"])
        self.assertTrue(info["maps"])

    def test_every_map_and_event_reads(self):
        for map_id in self.project.map_ids_on_disk():
            result = self.call("map_get", map_id=map_id)
            self.assertIn("tile_table", result)
            events = self.call("event_get", map_id=map_id)
            for event in events["events"]:
                for page in event["pages"]:
                    self.assertIsInstance(page["script"], list)

    def test_validate_runs(self):
        result = self.call("validate")
        self.assertIn("counts", result)

    def skip_unless_the_fixture_game(self):
        """Skip unless this is the project the facts below were read from.

        Asked of the project's own title, in System and in Game.ini: this
        used to skip only when map 39 was missing, and any project with
        thirty-nine maps then failed on figures from somebody else's game.
        """
        from rpgvxace_mcp.analysis import _text
        system = self.project.load_data("system")
        title = _text(self.project, system.get("game_title"))
        ini = self.project.root / "Game.ini"
        ini_text = (ini.read_text(encoding="utf-8", errors="replace")
                    if ini.is_file() else "")
        expected = os.environ.get("RPGVXACE_TEST_PROJECT_TITLE")
        if not expected:
            self.skipTest("set RPGVXACE_TEST_PROJECT_TITLE to the title of "
                          "the game these facts were read from")
        if title != expected or "Title=%s" % expected not in ini_text:
            self.skipTest("not the project these facts were read from "
                          "(title %r)" % title)

    def test_map_critique_on_the_fixture_game(self):
        # Facts read from the author's project; skipped for any other.
        self.skip_unless_the_fixture_game()
        out = self.call("map_critique", scopes=["reachability"])
        reach = out["reachability"]
        self.assertEqual(reach["start_map"], 14)
        self.assertEqual(reach["unresolved_transfers"], 0)
        detail = {d["map_id"]: d for d in reach["unreachable_detail"]}
        for blank in (16, 26, 35, 38):
            self.assertTrue(detail[blank]["blank"], blank)
            self.assertEqual((detail[blank]["width"], detail[blank]["height"]),
                             (17, 13))
            self.assertEqual(detail[blank]["nonzero_cells"], 0)
            self.assertEqual(detail[blank]["event_count"], 0)
        thorny = detail[39]
        self.assertFalse(thorny["blank"])
        self.assertEqual((thorny["width"], thorny["height"]), (38, 30))
        self.assertEqual(thorny["nonzero_cells"], 1359)
        self.assertEqual(thorny["event_count"], 0)

    def test_the_default_critique_of_the_fixture_game_is_a_short_summary(self):
        """64 KB before the summary; the target was 4 to 8 KB."""
        self.skip_unless_the_fixture_game()
        import json
        out = self.call("map_critique")
        size = len(json.dumps(out, ensure_ascii=False).encode("utf-8"))
        self.assertLess(size, 8 * 1024)
        self.assertGreater(size, 4 * 1024)
        rows = out["summary"]["findings"]
        blank = [r for r in rows if r["finding"] == "unreachable_blank"]
        self.assertEqual(blank[0]["maps"], [16, 26, 35, 38])

    def test_the_fixture_game_layer_3_is_counted_apart_from_tiles(self):
        """337 region cells and 556 shadow-only cells, none of them tiles.

        The same facts `test_the_layer_model_on_the_fixture_game` reads straight
        from the tables, seen through `dead_space` and `tile_variety`.
        """
        self.skip_unless_the_fixture_game()
        out = self.call("map_critique", scopes=["dead_space", "tile_variety"])
        rows = out["dead_space"]["maps"]
        self.assertEqual(sum(r["region_cells"] for r in rows), 337)
        self.assertEqual(sum(r["shadow_cells"] for r in rows), 556)
        variety = out["tile_variety"]["maps"]
        self.assertEqual({len(m["layers"]) for m in variety}, {3})
        self.assertEqual(sum(m["regions"]["cells"] for m in variety), 337)
        self.assertEqual(sum(m["shadows"]["cells"] for m in variety), 556)

    def test_the_default_summary_of_the_fixture_game_flags_degenerate_maps(self):
        """The fences alone flagged no map here; the floors catch these.

        Map 9 is one ground tile and nothing above it; maps 4 and 5 hold
        events and no tile at all (all void, so dead space and nothing
        else); 12 and 15 are more than half void. The
        dead-space IQR is 0 (most maps have no empty ground) and only seven
        maps have any, so the 50% rule applies.
        """
        self.skip_unless_the_fixture_game()
        summary = self.call("map_critique")["summary"]
        flagged = {r["finding"]: r["maps"]
                   for r in summary["findings"]
                   if r["finding"] in ("uniform_ground",
                                       "unusual_dead_space")}
        # Maps 4 and 5 hold no tile at all: said once, as dead space.
        self.assertEqual(flagged["uniform_ground"], [9])
        self.assertEqual(flagged["unusual_dead_space"], [4, 5, 12, 15])
        self.assertEqual(
            summary["map_fences"]["dead_space"]["fallback"]["upper_fence"],
            0.5)

    def test_harmless_common_event_scripts_do_not_cloud_the_fixture_game(self):
        """Its only common-event scripts set autodash and grant classes.

        Every dead end here was `uncertain` because a common event held a
        script, any script. None of these can transfer the party, so the
        dead end at map 36 stands on its own.
        """
        self.skip_unless_the_fixture_game()
        dead = self.call("map_critique", scopes=["dead_ends"])["dead_ends"]
        entry = {e["map_id"]: e for e in dead["no_exit"]}[36]
        self.assertFalse(entry["uncertain"], entry.get("reasons"))

    def test_the_layer_model_on_the_fixture_game(self):
        """Layer 2 holds tile ids and layer 3 packs region << 8 | shadow.

        Read from the author's project on 2026-10-17 and skipped for any
        other: 39 maps, every table four deep; 4,932 non-zero cells on layer
        2, all of them B-E ids; on layer 3, 337 cells of exactly 256 (region
        1, no shadow) and 556 shadow-only cells, none with a bit outside the
        two fields.
        """
        self.skip_unless_the_fixture_game()
        upper = ids_on_3 = shadow_only = 0
        regions: dict[int, int] = {}
        first_region = None
        for map_id in self.project.map_ids_on_disk():
            data = self.project.load_map(map_id).get("data")
            self.assertEqual(data.zsize, 4, map_id)
            plane = data.xsize * data.ysize
            layer_2 = [v for v in data.data[2 * plane:3 * plane] if v]
            upper += len(layer_2)
            self.assertTrue(all(v < 1024 for v in layer_2),
                            "map %d holds a non B-E id on layer 2" % map_id)
            for at, word in enumerate(data.data[3 * plane:4 * plane]):
                if not word:
                    continue
                ids_on_3 += 1
                self.assertEqual(word & ~0x3F0F, 0, (map_id, word))
                region = word >> 8      # RGSS3's Game_Map#region_id
                if not region:
                    shadow_only += 1
                    continue
                regions[region] = regions.get(region, 0) + 1
                if first_region is None:
                    first_region = (map_id, at % data.xsize, at // data.xsize)
        self.assertEqual(upper, 4932)
        self.assertEqual((ids_on_3, regions, shadow_only),
                         (893, {1: 337}, 556))
        # And through the tool, which is what a caller sees: no longer
        # refused as a region id above 63, and decoded to region 1.
        map_id, x, y = first_region
        window = dict(map_id=map_id, x=x, y=y, width=1, height=1)
        self.assertEqual(self.call("map_tiles", z=3, **window)["rows"], [[256]])
        self.assertEqual(self.call("map_tiles", view="region", **window)["rows"],
                         [[1]])


class TestTheCompilerTableAndItsHelpAgree(unittest.TestCase):
    """Every key the DSL compiles is a key the DSL documents, and vice versa.

    `_compile_entry` dispatched seventeen keys as a chain of `if "key" in
    entry` tests, and `SUPPORTED_KEYS` was the same seventeen string
    literals written again with nothing holding them equal -- so a branch
    added without the set under-reported what works, and a set entry added
    without a branch advertised a key that raises "unrecognised". The set
    is `frozenset(_COMPILERS)` now, so that pair cannot disagree.

    What can still disagree is `DSL_HELP`, which is the text two tools serve
    to the model as the answer to "how do I write a command list". A key
    that compiles and is not in it is a feature no caller is told about; a
    form described in it that does not compile is worse, because a caller
    writes it and is refused.
    """

    def keys(self):
        from rpgvxace_mcp.commands import SUPPORTED_KEYS, _COMPILERS
        self.assertEqual(set(SUPPORTED_KEYS), set(_COMPILERS),
                         "the advertised set is no longer derived")
        self.assertGreater(len(_COMPILERS), 10, "read almost nothing")
        return sorted(_COMPILERS)

    def test_every_compiled_key_is_documented(self):
        from rpgvxace_mcp.commands import DSL_HELP
        missing = [key for key in self.keys()
                   if '"%s"' % key not in DSL_HELP]
        self.assertEqual(
            missing, [],
            "these compile and `DSL_HELP` does not mention them, so no "
            "caller is told they work: " + ", ".join(missing))

    def test_every_compiler_actually_compiles_its_key(self):
        """So a table entry cannot be a name with nothing behind it.

        The minimal entry for each key, compiled. What matters is that
        every value in the table is reached and returns commands -- a
        `None` or a misspelled function name in there would raise, and the
        chain this replaced could not have such an entry at all.
        """
        from rpgvxace_mcp.commands import compile_commands
        from rpgvxace_mcp.convert import TextCodec
        codec = TextCodec("utf-8")
        smallest = {
            "raw": {"raw": {"code": 108, "parameters": []}},
            "text": {"text": "hello"},
            "comment": {"comment": "note"},
            "script": {"script": "p 1"},
            "switch": {"switch": 1, "value": "on"},
            "self_switch": {"self_switch": "A", "value": "on"},
            "variable": {"variable": 1, "op": "set", "value": 1},
            "wait": {"wait": 30},
            "common_event": {"common_event": 1},
            "label": {"label": "top"},
            "jump": {"jump": "top"},
            "transfer": {"transfer": {"map_id": 1, "x": 1, "y": 1}},
            "exit": {"exit": True},
            "if": {"if": {"switch": 1, "is": "on"}, "then": []},
            "loop": {"loop": []},
            "break_loop": {"break_loop": True},
            "choices": {"choices": ["a", "b"], "branches": [[], []]},
            "move_route": {"move_route": {"target": "player",
                                          "steps": ["move_up"]}},
            "shop": {"shop": {"goods": [{"item": 1}]}},
            "show_picture": {"show_picture": {"number": 1, "name": "a"}},
            "move_picture": {"move_picture": {"number": 1}},
            "erase_picture": {"erase_picture": 1},
            "tint_screen": {"tint_screen": {"tone": [0, 0, 0, 0]}},
            "flash_screen": {"flash_screen": {"color": [0, 0, 0, 0]}},
            "shake_screen": {"shake_screen": {}},
            "gold": {"gold": {"value": 1}},
            "item": {"item": {"id": 1, "value": 1}},
            "weapon": {"weapon": {"id": 1, "value": 1}},
            "armor": {"armor": {"id": 1, "value": 1}},
            "party": {"party": {"actor": 1, "op": "add"}},
            "battle": {"battle": {"troop": 1}},
            "name_input": {"name_input": {"actor": 1, "max_characters": 8}},
            "bgm": {"bgm": {"name": "a"}},
            "bgs": {"bgs": {"name": "a"}},
            "me": {"me": {"name": "a"}},
            "se": {"se": {"name": "a"}},
            "fadeout_screen": {"fadeout_screen": True},
            "fadein_screen": {"fadein_screen": True},
            "event_location": {"event_location": {"event": 1, "x": 0,
                                                  "y": 0}},
            "balloon": {"balloon": {"target": 0, "balloon": 1}},
            "animation": {"animation": {"target": 0, "animation": 1}},
        }
        self.assertEqual(sorted(smallest), self.keys(),
                         "this table and the compiler table disagree, so "
                         "one of the two is not the vocabulary")
        for key in self.keys():
            with self.subTest(key=key):
                out = compile_commands([smallest[key]], codec)
                self.assertTrue(out, "%s compiled to nothing" % key)

    def test_an_unknown_key_names_what_is_supported(self):
        from rpgvxace_mcp.commands import CommandError, compile_commands
        from rpgvxace_mcp.convert import TextCodec
        with self.assertRaises(CommandError) as caught:
            compile_commands([{"nosuchform": 1}], TextCodec("utf-8"))
        message = str(caught.exception)
        for key in ("text", "choices", "transfer"):
            self.assertIn(key, message)
