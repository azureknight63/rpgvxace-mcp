"""Tool-level tests, run against a freshly built demo project each time."""

import json
import re
import shutil
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))

from build_project import build  # noqa: E402

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.commands import CommandError  # noqa: E402
from rpgvxace_mcp.analysis import (  # noqa: E402
    MAX_FINDINGS,
    REFERENCE_PREVIEW,
    validate as run_validate,
)
from rpgvxace_mcp.project import (  # noqa: E402
    Project,
    ProjectError,
    ReadOnlyError,
    StaleFileError,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
    RubyString,
    Table,
)
from rpgvxace_mcp import convert  # noqa: E402
from rpgvxace_mcp.convert import ConversionError  # noqa: E402
from rpgvxace_mcp.schema import PROFILES  # noqa: E402
from rpgvxace_mcp.tools.search_tools import MAX_SUBJECT_CHARS  # noqa: E402
from rpgvxace_mcp.tools import pattern_safety  # noqa: E402
from rpgvxace_mcp.tools.pattern_safety import (  # noqa: E402
    PatternTooCostly,
    check_pattern,
    check_shape,
)
from rpgvxace_mcp.tools.search_tools import _matcher  # noqa: E402
from rpgvxace_mcp.tools import (  # noqa: E402,F401
    db_tools,
    event_tools,
    map_tools,
    misc_tools,
    raw_tools,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
    check_op_args,
)


class ToolTest(unittest.TestCase):
    """Base class giving each test its own copy of the demo project.

    Subclasses set ``engine`` to run the same checks against the other engine
    in the family.
    """

    read_only = False
    engine = "vx_ace"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build(self.tmp / "project", self.engine)
        self.project = Project(self.tmp / "project", read_only=self.read_only)
        self.ctx = Context(self.project)

    @property
    def ace(self) -> bool:
        return self.engine == "vx_ace"

    @property
    def extension(self) -> str:
        return ".rvdata2" if self.ace else ".rvdata"

    def call(self, tool_name, /, **arguments):
        # run(), not handler(): a real call goes through the argument check,
        # so the tests should too.
        return REGISTRY[tool_name].run(self.ctx, arguments)

    def reopen(self):
        """A fresh Project, to prove a change actually reached the disk."""
        return Context(Project(self.tmp / "project"))

    def data_bytes(self, stem):
        """Bytes of one data file, named without its extension."""
        name = stem if stem.endswith(self.extension) else stem + self.extension
        return (self.tmp / "project" / "Data" / name).read_bytes()


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
        self.assertIn("shadow", result["tile_table"]["layers"]["2"])
        if self.ace:
            self.assertIn("region", result["tile_table"]["layers"]["3"])
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
        self.assertIn("Transfer Player", result["warning"])


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
        result = self.call("command_reference", code=232)
        self.assertIsNone(result["parameters"])
        # A list, because a code can be both undispatched and unrecorded.
        self.assertTrue(any("include_raw" in note for note in result["notes"]))

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
        spec = self.call("command_reference", code=302)["parameters"]
        self.assertEqual("purchase_only" in spec, self.ace)


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

    def test_xp_needs_the_flag_and_says_so(self):
        root = self.stub("Game.rxproj", "Actors.rxdata")
        with self.assertRaises(Exception) as caught:
            Project(root)
        message = str(caught.exception)
        self.assertIn("XP", message)
        self.assertIn("--allow-other-engines", message)
        project = Project(root, allow_other_engines=True)
        self.assertEqual(project.extension, ".rxdata")
        # Described with the closest model available, and it says which.
        self.assertEqual(project.profile.key, "vx")

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


# ---------------------------------------------------------------------------
# The same suites again against VX. Every assertion above is engine-aware, so
# re-running them proves both profiles work rather than just the default one.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# VX Ace specifics
# ---------------------------------------------------------------------------

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
        rows = self.call("map_tiles", map_id=2, x=0, y=5, width=3, height=1,
                         z=3)["rows"]
        self.assertEqual(rows, [[1, 1, 1]])

    def test_region_layer_is_writable(self):
        self.call("map_tiles", map_id=2, op="fill", x=0, y=0, width=12, height=2,
                  z=3, tile_id=2)
        rows = self.call("map_tiles", map_id=2, x=0, y=0, width=2, height=1,
                         z=3)["rows"]
        self.assertEqual(rows, [[2, 2]])

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
        import os
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


class TestPathContainment(ToolTest):
    """The backup name and file names in a restore are caller-supplied.

    Every test here was a working exploit before the containment helpers went
    in: an unvalidated `stamp` was joined to the backup root, and an absolute
    path replaces a join outright, so a restore both listed and read from
    anywhere on the host and wrote the result into Data/ where the read tools
    could then hand it to the model.
    """

    def setUp(self):
        super().setUp()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        (self.outside / ("Items" + self.extension)).write_bytes(
            b"PRETEND-CREDENTIALS\n")

    def test_restore_refuses_an_absolute_backup_name(self):
        with self.assertRaises(Exception) as caught:
            self.call("backups", op="restore", backup=str(self.outside))
        self.assertIn("single name", str(caught.exception))
        # The point of the guard: nothing was staged into the project.
        self.assertNotIn(b"PRETEND-CREDENTIALS",
                         self.data_bytes("Items"))

    def test_restore_refuses_parent_traversal(self):
        for name in ("..", "../..", r"..\..", "../outside", "sub/dir"):
            with self.subTest(backup=name):
                with self.assertRaises(Exception) as caught:
                    self.call("backups", op="restore", backup=name)
                self.assertIn("single name", str(caught.exception))

    def test_restore_refuses_a_traversing_file_name(self):
        snapshot = self.call("backups", op="create", label="safe")
        with self.assertRaises(Exception) as caught:
            self.call("backups", op="restore", backup=snapshot["backup"],
                      files=["../../Game.ini"])
        self.assertIn("single name", str(caught.exception))

    def test_restore_still_works_with_a_real_snapshot(self):
        """The guard must not reject the names this server generates itself."""
        snapshot = self.call("backups", op="create", label="pre-restore")
        result = self.call("backups", op="restore", backup=snapshot["backup"])
        self.assertIn("Items" + self.extension, result["restored"])

    def test_path_for_refuses_a_name_that_is_not_one_component(self):
        for name in ("../Game.ini", r"..\Game.ini", "sub/Items.rvdata2",
                     "C:/Windows/win.ini", ".hidden", "",
                     "Items.rvdata2:hidden", "PROGRA~1",
                     # Windows resolves these to devices, not files.
                     "NUL", "con", "COM1.rvdata2", "Items."):
            with self.subTest(name=name):
                with self.assertRaises(ProjectError):
                    self.project.path_for(name)

    def test_path_for_accepts_every_name_this_project_uses(self):
        for key in self.project.data_files:
            self.project.path_for(self.project.filename_for(key))
        for map_id in self.project.map_ids_on_disk():
            self.project.path_for(self.project.map_file(map_id))


class TestPathContainmentReadOnly(TestPathContainment):
    """A read-only server must not even enumerate a directory it was pointed at.

    The original gate was `read_only and not dry_run`, so a dry run reported
    `would_restore` for any directory on the host. That made a server the
    operator had deliberately started read-only into a filesystem oracle.
    """

    read_only = True

    def test_restore_still_works_with_a_real_snapshot(self):
        with self.assertRaises(ReadOnlyError):
            self.call("backups", op="create", label="pre-restore")

    def test_restore_refuses_a_traversing_file_name(self):
        # A read-only server cannot take a snapshot, so stage one directly.
        stamp = "20240101-000000-staged"
        staged = self.project.backup_root() / stamp
        staged.mkdir(parents=True)
        (staged / ("Items" + self.extension)).write_bytes(
            self.data_bytes("Items"))
        with self.assertRaises(Exception) as caught:
            self.call("backups", op="restore", backup=stamp,
                      files=["../../Game.ini"], dry_run=True)
        self.assertIn("single name", str(caught.exception))

    def test_dry_run_restore_lists_nothing_outside_the_project(self):
        for name in (str(self.outside), "../..", ".."):
            with self.subTest(backup=name):
                with self.assertRaises(Exception) as caught:
                    self.call("backups", op="restore", backup=name,
                              dry_run=True)
                message = str(caught.exception)
                self.assertIn("single name", message)
                # Nothing about what is actually in that directory leaked.
                self.assertNotIn("Items", message)

    def test_a_bad_name_is_refused_as_a_bad_name(self):
        """The containment check has to run before the read-only gate.

        Behind the gate it would stop being exercised in exactly the mode an
        operator chooses when they are being careful, and the caller would be
        told the wrong reason for the refusal.
        """
        with self.assertRaises(Exception) as caught:
            self.call("backups", op="restore", backup=str(self.outside))
        self.assertIn("single name", str(caught.exception))

    def test_snapshot_and_prune_are_refused(self):
        with self.assertRaises(ReadOnlyError):
            self.project.snapshot("nope")
        with self.assertRaises(ReadOnlyError):
            self.project.prune_backups()


class TestBackupPruning(ToolTest):
    def test_restore_survives_a_full_backup_root(self):
        """Restoring takes a safety snapshot, which prunes -- and the pruning
        used to be free to delete the very snapshot being restored from.

        The snapshot has to be older than the safety snapshot for this to
        bite, because pruning keeps the newest: restoring last week's backup
        is precisely when it matters.
        """
        stamp = "20200101-000000-wanted"
        staged = self.project.backup_root() / stamp
        staged.mkdir(parents=True)
        (staged / ("Items" + self.extension)).write_bytes(
            self.data_bytes("Items"))
        # Two, so the edit below does not legitimately prune the staged
        # snapshot before the restore starts: the guard under test is the
        # pruning that happens *inside* the restore.
        self.project.backup_keep = 2
        self.call("db_edit", table="items", ops=[
            {"op": "update", "id": 1, "patch": {"name": "Wrong"}}])
        result = self.call("backups", op="restore", backup=stamp)
        self.assertIn("Items" + self.extension, result["restored"])
        entry = self.reopen().project.get_entry("items", 1)
        self.assertEqual(entry.get("name").data, b"Potion")


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


class TestStalenessDetection(ToolTest):
    def test_a_same_size_rewrite_is_still_detected(self):
        """Modification times are coarse on FAT and on network shares, so a
        time-and-size check alone would let an editor save be overwritten."""
        filename = self.project.filename_for("items")
        path = self.project.path_for(filename)
        self.project.load_data("items")  # populate the cache
        original = path.read_bytes()
        stat = path.stat()
        edited = bytearray(original)
        edited[-1] = (edited[-1] + 1) % 256
        path.write_bytes(bytes(edited))
        # Put the timestamp back exactly, as a coarse-resolution filesystem
        # effectively would.
        import os as _os
        _os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(path.stat().st_size, len(original))
        with self.assertRaises(StaleFileError) as caught:
            self.project.save_file(filename, tool="test", summary="clobber")
        self.assertIn("contents differ", str(caught.exception))

    def test_a_touch_that_changes_nothing_is_not_stale(self):
        filename = self.project.filename_for("items")
        path = self.project.path_for(filename)
        self.project.load_data("items")
        import os as _os
        stat = path.stat()
        _os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
        self.project.save_file(filename, tool="test", summary="untouched")


class TestDeletionIsLogged(ToolTest):
    def test_deleting_a_map_reaches_the_change_log(self):
        """Deletion is the most destructive thing here, and was the one
        mutation that left no trace in the log."""
        self.call("map_edit", op="create", name="Doomed", width=20, height=15)
        created = [m for m in self.project.map_ids_on_disk(refresh=True)
                   if m != 1]
        self.call("map_edit", op="delete", map_id=created[-1])
        summaries = [row.get("summary") for row
                     in self.project.recent_changes(20)]
        self.assertIn("deleted", summaries)


class TestScriptNameStringForm(ToolTest):
    def test_a_written_script_name_keeps_this_engine_string_form(self):
        """VX Ace strings carry a Ruby 1.9 encoding record; a name written
        without one is a string the editor did not produce."""
        self.project.set_script(0, name="Renamed")
        filename = self.project.filename_for("scripts")
        self.project.save_file(filename, tool="test", summary="rename")
        raw = self.data_bytes("Scripts")
        self.assertIn(b"Renamed", raw)
        rows = marshal_rb.load(raw)
        name = rows[0][1]
        self.assertEqual(name.data, b"Renamed")
        if self.ace:
            self.assertTrue(name.ivars, "Ace strings carry an encoding record")
        else:
            self.assertFalse(name.ivars, "VX strings are bare bytes")


class TestPathContainmentVX(TestPathContainment):
    engine = "vx"


class TestScriptNameStringFormVX(TestScriptNameStringForm):
    engine = "vx"

class TestHostileDataFolder(ToolTest):
    """A project folder is not necessarily one the editor made."""

    def test_an_unaddressable_map_file_does_not_stop_the_server(self):
        """Map0 and Map10000 are ids no Map%03d name can express, so letting
        them out of the directory listing crashed construction."""
        for name in ("Map0", "Map000", "Map10000"):
            (self.tmp / "project" / "Data" / (name + self.extension)).write_bytes(
                self.data_bytes("Map001"))
        project = Project(self.tmp / "project")
        ids = project.map_ids_on_disk()
        self.assertIn(1, ids)
        for unusable in (0, 10000):
            self.assertNotIn(unusable, ids)
        # And the tools still work on the maps that are addressable.
        self.assertIn("tile_table", self.call("map_get", map_id=1))

    def test_a_truncated_script_body_is_an_error_not_a_short_script(self):
        """Returning the part that inflated would let a read-edit-write cycle
        drop the tail of a real script without saying so."""
        scripts = self.project.load_data("scripts")
        body = zlib.compress(("# a real script\n" * 400).encode("utf-8"))
        scripts[0] = [1, self.project.codec.make_string("truncated"),
                      RubyString(body[:len(body) // 2])]
        self.project.save_file(self.project.filename_for("scripts"),
                               tool="test", summary="truncated")
        entry = self.reopen().project.load_scripts()[0]
        self.assertNotIn("code", entry)
        self.assertIn("truncated", entry["error"])


class TestRestoreValidatesBeforeActing(ToolTest):
    def test_a_bad_name_in_the_snapshot_stops_before_anything_is_copied(self):
        """Validation used to happen inside the copy loop, so a rejection
        landed after the safety snapshot and after some files were copied."""
        stamp = "20200101-000000-staged"
        staged = self.project.backup_root() / stamp
        staged.mkdir(parents=True)
        (staged / ("Items" + self.extension)).write_bytes(
            self.data_bytes("Items"))
        (staged / ".hidden").write_bytes(b"x")
        before = {p.name for p in self.project.backup_root().iterdir()}
        with self.assertRaises(ProjectError):
            self.call("backups", op="restore", backup=stamp)
        after = {p.name for p in self.project.backup_root().iterdir()}
        self.assertEqual(before, after,
                         "a safety snapshot was taken before the name was checked")


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

class ValidateTest(ToolTest):
    """Helpers for the rules that must not cry wolf."""

    def findings(self, rule=None, **arguments):
        rows = self.call("validate", **arguments)["findings"]
        return [f for f in rows if rule is None or f["rule"] == rule]

    def corrupt(self, stem):
        """Leave a data file that exists but cannot be parsed."""
        name = stem if stem.endswith(self.extension) else stem + self.extension
        (self.tmp / "project" / "Data" / name).write_bytes(b"\x04\x08 not marshal")

    def declare_missing_rtp(self):
        """Point Game.ini at an RTP that is not installed anywhere."""
        (self.tmp / "project" / "Game.ini").write_text(
            "[Game]\nRTP1=NoSuchRtpAnywhere\nLibrary=System\\RGSS300.dll\n"
            "Scripts=Data\\Scripts%s\nTitle=Demo\n" % self.extension,
            encoding="utf-8")
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)
        self.assertFalse(self.project.assets_complete)

    def first_event(self, map_id=1):
        game_map = self.project.load_map(map_id)
        event_id, event = game_map.get("events").sorted_items()[0]
        return game_map, event_id, event


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

class TestNothingIsStaged(ToolTest):
    """A change that did not land must not be waiting for the next write.

    Every editing tool mutates the value in the project's cache and then asks
    for it to be written. Nothing used to undo that, so a dry run, a refused
    write and an op batch that failed halfway all left their changes in memory
    -- invisible until some later, unrelated write of the same file put them on
    disk. That is a silent edit to someone's game that no tool call asked for.
    """

    def item_name(self, project=None):
        return (project or self.project).get_entry("items", 1).get("name").data

    def test_a_dry_run_leaves_nothing_behind(self):
        self.call("db_edit", table="items", dry_run=True, ops=[
            {"op": "update", "id": 1, "patch": {"name": "STAGED"}}])
        self.assertEqual(self.item_name(), b"Potion", "the cache was mutated")
        # And a later real write of the same table must not carry it.
        self.call("db_edit", table="items",
                  ops=[{"op": "update", "id": 2, "patch": {"price": 99}}])
        self.assertEqual(self.item_name(self.reopen().project), b"Potion")

    def test_a_failed_op_batch_leaves_nothing_behind(self):
        with self.assertRaises(ToolError):
            self.call("db_edit", table="items", ops=[
                {"op": "update", "id": 1, "patch": {"name": "HALF"}},
                {"op": "delete"},
            ])
        self.assertEqual(self.item_name(), b"Potion")
        self.call("db_edit", table="items",
                  ops=[{"op": "update", "id": 2, "patch": {"price": 99}}])
        self.assertEqual(self.item_name(self.reopen().project), b"Potion")

    def test_a_resize_dry_run_does_not_discard_cached_tiles(self):
        before = self.project.load_map(1).get("data")
        size = (before.xsize, before.ysize)
        self.call("map_edit", op="resize", map_id=1, width=5, height=5,
                  dry_run=True)
        after = self.project.load_map(1).get("data")
        self.assertEqual((after.xsize, after.ysize), size)

    def test_a_tile_write_dry_run_does_not_paint_cached_tiles(self):
        before = self.project.load_map(1).get("data").get(2, 2, 0)
        self.call("map_tiles", map_id=1, op="fill", x=2, y=2, width=2,
                  height=2, tile_id=100, dry_run=True)
        self.assertEqual(self.project.load_map(1).get("data").get(2, 2, 0),
                         before)

    def test_an_event_edit_dry_run_does_not_delete_a_cached_event(self):
        events = self.project.load_map(1).get("events")
        event_id = events.sorted_items()[0][0]
        self.call("event_edit", map_id=1, dry_run=True,
                  ops=[{"op": "delete", "event_id": event_id}])
        self.assertIn(event_id,
                      [k for k, _ in self.project.load_map(1)
                       .get("events").sorted_items()])


class TestDatabaseIdsAreChecked(ToolTest):
    def test_a_negative_create_id_is_refused(self):
        """set_entry indexes the array directly, so a negative id used to
        overwrite the LAST entry and report it as a create."""
        before = [(i, e.get("name").data) for i, e in
                  self.project.entries("actors")]
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="actors",
                      ops=[{"op": "create", "id": -1}])
        self.assertIn("negative", str(caught.exception))
        after = [(i, e.get("name").data) for i, e in
                 self.reopen().project.entries("actors")]
        self.assertEqual(before, after)

    def test_a_huge_create_id_is_refused(self):
        """The id is the array length, so a big one pads the table with
        hundreds of thousands of empty slots and writes them all."""
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="actors",
                      ops=[{"op": "create", "id": 300000}])
        self.assertIn("too high", str(caught.exception))

    def test_slot_zero_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="actors",
                      ops=[{"op": "create", "id": 0}])
        self.assertIn("slot 0", str(caught.exception))

    def test_a_created_entry_carries_its_own_id(self):
        """Every RGSS entry in an id-indexed array repeats its id in @id; the
        engine reads it, and a blank create used to leave it unset."""
        result = self.call("db_edit", table="actors", ops=[{"op": "create"}])
        new_id = result["applied"][0]["id"]
        entry = self.reopen().project.get_entry("actors", new_id)
        self.assertEqual(entry.get("id"), new_id)

    def test_a_patch_cannot_move_an_entry_id(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="actors",
                      ops=[{"op": "create", "patch": {"id": 77}}])
        self.assertIn("its position", str(caught.exception))

    def test_map_infos_is_not_editable_here(self):
        """A tree entry and its Map### file have to change together, and only
        map_edit knows how."""
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="map_infos",
                      ops=[{"op": "delete", "id": 1}])
        self.assertIn("map_edit", str(caught.exception))

    def test_deleting_an_unscanned_table_says_it_was_not_scanned(self):
        """A zero here used to read as "nothing points at this", when in fact
        nothing had looked: only some tables have reference rules."""
        result = self.call("db_edit", table="skills",
                           ops=[{"op": "delete", "id": 1}])
        entry = result["applied"][0]
        self.assertFalse(entry["references_checked"])
        self.assertTrue(any("not checked" in w for w in result["warnings"]))


class TestArgumentsAreNotCoerced(ToolTest):
    def test_a_fractional_id_is_refused(self):
        """int(2.9) is 2, so a wrong-typed id used to address a different
        entry than the caller named -- and report success against it."""
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items",
                      ops=[{"op": "update", "id": 1.9, "patch": {"price": 7}}])
        self.assertIn("whole number", str(caught.exception))

    def test_a_boolean_id_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_get", map_id=True)
        self.assertIn("true or false", str(caught.exception))

    def test_an_integral_float_is_accepted(self):
        """JSON has no integer type of its own, so a client may send 1.0."""
        self.assertEqual(self.call("map_get", map_id=1.0)["map_id"], 1)

    def test_an_explicit_falsy_choice_is_not_replaced_by_the_default(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, op="")
        self.assertIn("must be one of", str(caught.exception))


class TestMapWritesStayConsistent(ToolTest):
    def test_a_patch_cannot_change_the_map_size(self):
        """Setting @width alone leaves the map claiming a size its tile table
        does not have, which is what the editor refuses to open."""
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="update", map_id=1,
                      patch={"width": 100})
        self.assertIn("resize", str(caught.exception))

    def test_width_is_refused_on_update(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="update", map_id=1, width=30)
        self.assertIn("resize", str(caught.exception))

    def test_a_rename_of_a_map_with_no_tree_entry_writes_nothing(self):
        """The map file used to be saved before the tree half was checked, so
        the caller was told the call failed with half of it on disk."""
        infos = self.project.load_data("map_infos")
        infos.delete(1)
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="drop entry")
        ctx = Context(Project(self.tmp / "project"))
        before = self.data_bytes("Map001")
        with self.assertRaises(ToolError):
            REGISTRY["map_edit"].handler(ctx, {
                "op": "resize", "map_id": 1, "width": 30, "height": 25,
                "name": "Renamed"})
        self.assertEqual(self.data_bytes("Map001"), before,
                         "the map was resized before the rename was checked")

    def test_a_shrink_reports_the_tiles_it_discards(self):
        result = self.call("map_edit", op="resize", map_id=1, width=3,
                           height=3)
        self.assertTrue(any("discarded" in c for c in result["changes"]),
                        result["changes"])

    def test_a_tile_value_the_layer_cannot_hold_is_refused(self):
        """Table.set masks to 16 bits, so an out-of-range id was silently
        stored as some other tile and reported back as the one asked for."""
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, op="fill", x=0, y=0, width=1,
                      height=1, tile_id=70000)
        self.assertIn("values run 0 to", str(caught.exception))

    def test_a_region_id_beyond_the_layer_is_refused(self):
        if not self.ace:
            self.skipTest("layer 3 holds region ids on VX Ace only")
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, op="fill", x=0, y=0, width=1,
                      height=1, z=3, tile_id=2816)
        self.assertIn("0 to 63", str(caught.exception))

    def test_a_bad_row_writes_none_of_the_rows(self):
        before = [self.project.load_map(1).get("data").get(x, 0, 0)
                  for x in range(3)]
        with self.assertRaises(ToolError):
            self.call("map_tiles", map_id=1, op="write", x=0, y=0, width=3,
                      height=2, rows=[[1, 1, 1], [1, 1]])
        after = [self.project.load_map(1).get("data").get(x, 0, 0)
                 for x in range(3)]
        self.assertEqual(before, after, "the first row was written anyway")

    def test_a_negative_coordinate_is_refused_with_a_usable_message(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, x=-2)
        self.assertIn("at least 0", str(caught.exception))


class TestRawDataRefusesBreakage(ToolTest):
    """raw_data is the unvalidated escape hatch, but reach is not breakage."""

    def test_a_structure_cannot_be_replaced_by_a_scalar(self):
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="set", table="system",
                      path="party_members", value={"value": 0})
        self.assertIn("cannot open", str(caught.exception))

    def test_a_map_s_structural_fields_are_refused(self):
        for field in ("data", "width", "events"):
            with self.subTest(field=field):
                with self.assertRaises(ToolError) as caught:
                    self.call("raw_data", op="set", map_id=1, path=field,
                              value={"value": 0})
                self.assertIn("agree with it", str(caught.exception))

    def test_a_refused_write_leaves_nothing_staged(self):
        before = self.project.load_data("system").get("start_x")
        with self.assertRaises(ToolError):
            self.call("raw_data", op="set", table="system",
                      path="party_members", value={"value": 0})
        self.assertEqual(self.project.load_data("system").get("start_x"),
                         before)


class TestSearchReportsWhatItSearched(ToolTest):
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
        self.assertIn("troops", result["could_not_read"])
        # The scope still counts as searched, because the other tables read.
        self.assertIn("database", result["scopes_searched"])

    def test_a_scope_whose_only_file_fails_is_not_called_searched(self):
        self.corrupt("MapInfos")
        # Reopened, because setUp already read MapInfos into the cache and a
        # cached copy is still perfectly readable.
        self.ctx = self.reopen()
        result = self.call("search", query="zzz", scopes=["maps"])
        self.assertNotIn("maps", result["scopes_searched"])
        self.assertIn("map_infos", result["could_not_read"])


class TestMapCreateIsStaged(unittest.TestCase):
    def test_a_refused_create_leaves_no_tree_entry(self):
        """_create_map hand-rolled its own undo, which covered the dry run but
        not a refusal -- so a read-only server was left with a tree entry
        pointing at a map file that was never written."""
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-ro-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build(tmp / "project", "vx_ace")
        project = Project(tmp / "project", read_only=True)
        ctx = Context(project)
        before = [k for k, _ in project.load_data("map_infos").sorted_items()]
        with self.assertRaises(ReadOnlyError):
            REGISTRY["map_edit"].handler(ctx, {
                "op": "create", "name": "Ghost", "width": 20, "height": 15})
        after = [k for k, _ in project.load_data("map_infos").sorted_items()]
        self.assertEqual(before, after, "a phantom tree entry was left behind")

    def test_a_dry_run_create_leaves_no_tree_entry(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-dry-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        build(tmp / "project", "vx_ace")
        project = Project(tmp / "project")
        ctx = Context(project)
        before = [k for k, _ in project.load_data("map_infos").sorted_items()]
        result = REGISTRY["map_edit"].handler(ctx, {
            "op": "create", "name": "Ghost", "width": 20, "height": 15,
            "dry_run": True})
        self.assertTrue(result["dry_run"])
        after = [k for k, _ in project.load_data("map_infos").sorted_items()]
        self.assertEqual(before, after)


class TestScriptHitsSayWhere(ToolTest):
    def test_a_script_hit_names_its_slot_and_line(self):
        """Location had no "script" branch, so every script hit's `where` was
        the bare word "script"."""
        result = self.call("search", query="def ", scopes=["scripts"])
        self.assertTrue(result["hits"])
        where = result["hits"][0]["where"]
        self.assertIn("script ", where)
        self.assertIn("line ", where)

class TestArgumentShapeIsEnforced(ToolTest):
    """The declared schema has to be the enforced one.

    Every tool's schema says additionalProperties: False and most declare
    numeric bounds, but nothing checked either -- the transport handed
    arguments straight to the handler. So a misspelled dry_run performed a
    real write while the caller believed it had asked for a preview, which is
    the worst shape of failure available here: the caller is told what it
    expected to hear.
    """

    def test_a_misspelled_dry_run_does_not_write(self):
        before = self.data_bytes("Items")
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items", dryrun=True,
                      ops=[{"op": "update", "id": 1,
                            "patch": {"name": "Written"}}])
        self.assertIn("unknown argument", str(caught.exception))
        self.assertEqual(self.data_bytes("Items"), before)

    def test_the_message_lists_what_the_tool_takes(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_get", map_id=1, tilez="counts")
        message = str(caught.exception)
        self.assertIn("tiles", message)
        self.assertIn("include_events", message)

    def test_a_misspelled_key_inside_an_op_is_refused(self):
        """An op's own keys are arguments too. Checking only the top level
        meant a misspelled one was dropped -- and for set_commands that left
        the page it was meant to rewrite holding one blank command."""
        before = self.data_bytes("Items")
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items",
                      ops=[{"op": "update", "id": 1, "pach": {"price": 9}}])
        self.assertIn("ops[0]", str(caught.exception))
        self.assertEqual(self.data_bytes("Items"), before)

    def test_a_declared_bound_inside_an_op_is_enforced(self):
        """The part of the recursion nothing else covers.

        An unknown key inside an op is refused twice over now -- by this
        recursion and by the per-op applicability check -- but a key that is
        real and out of range is only caught here. So the bounds each handler
        enforces are declared in the ops item too, and this is the test that
        walks them.
        """
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "move", "event_id": 10 ** 9, "x": 1, "y": 1}])
        self.assertIn("at most", str(caught.exception))

    def test_an_empty_command_list_is_refused(self):
        game_map = self.project.load_map(1)
        event_id = game_map.get("events").sorted_items()[0][0]
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, ops=[
                {"op": "set_commands", "event_id": event_id, "page": 0,
                 "commands": []}])
        self.assertIn("at least one command", str(caught.exception))

    def test_a_declared_bound_is_enforced(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_list", table="items", limit=10_000_000)
        self.assertIn("at most", str(caught.exception))

    def test_a_declared_maxitems_is_enforced(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_get", table="items", ids=list(range(1, 500)))
        self.assertIn("at most", str(caught.exception))


class TestAtPrefixedFieldsAreRefused(ToolTest):
    """Ruby ivars are written @width, and these patches accept either
    spelling, so a refusal list checked against the raw key let the @ form
    through every one of the three guards that shared that shape."""

    def test_a_map_patch_cannot_reach_a_structural_field(self):
        for key in ("width", "@width", "data", "@data"):
            with self.subTest(key=key):
                with self.assertRaises(ToolError) as caught:
                    self.call("map_edit", op="update", map_id=1,
                              patch={key: 99})
                self.assertIn("rest of the map", str(caught.exception))

    def test_a_page_patch_cannot_reach_the_command_list(self):
        game_map = self.project.load_map(1)
        event_id = game_map.get("events").sorted_items()[0][0]
        for key in ("list", "@list"):
            with self.subTest(key=key):
                with self.assertRaises(ToolError) as caught:
                    self.call("event_edit", map_id=1, ops=[
                        {"op": "set_page", "event_id": event_id, "page": 0,
                         "patch": {key: []}}])
                self.assertIn("set_commands", str(caught.exception))

    def test_raw_data_cannot_reach_a_structural_field(self):
        for path in ("width", "@width", "events", "@events"):
            with self.subTest(path=path):
                with self.assertRaises(ToolError) as caught:
                    self.call("raw_data", op="set", map_id=1, path=path,
                              value={"value": 0})
                self.assertIn("agree with it", str(caught.exception))


class TestGuardsRunOnEveryPath(ToolTest):
    def test_creating_a_map_refuses_a_structural_patch(self):
        """The create path returned before the update path's check ran."""
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="create", name="Bad", width=20, height=15,
                      patch={"width": 500})
        self.assertIn("rest of the map", str(caught.exception))

    def test_updating_an_entry_cannot_move_its_id(self):
        """The refusal existed only on the create path, though the tool's
        docstring claimed it applied to a patch."""
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items",
                      ops=[{"op": "update", "id": 1, "patch": {"id": 7}}])
        self.assertIn("its position", str(caught.exception))
        self.assertEqual(self.reopen().project.get_entry("items", 1).get("id"), 1)


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


class TestSearchRefusesTheHangingPattern(ToolTest):
    def test_a_nested_quantifier_is_refused(self):
        """Python's re backtracks exponentially on these and cannot be
        interrupted, so one would wedge this single-threaded server."""
        for pattern in ("(a+)+$", "(a*)*b", "(x|y+)+z"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ToolError) as caught:
                    self.call("search", query=pattern, regex=True)
                message = str(caught.exception)
                # The shape it found, and the reason a refusal is the answer:
                # the message names one or the other of these depending on
                # whether the cost is in the nesting or in a chain.
                self.assertIn("repeats a group", message)
                self.assertIn("cannot be interrupted", message)

    def test_an_ordinary_pattern_still_works(self):
        result = self.call("search", query="Welcome|Potion", regex=True)
        self.assertTrue(result["hits"])

    def test_a_command_code_search_needs_the_events_scope(self):
        with self.assertRaises(ToolError) as caught:
            self.call("search", command_code=101, scopes=["database"])
        self.assertIn("events", str(caught.exception))


class TestDeletingRespectsStaleness(ToolTest):
    def test_a_map_file_that_changed_is_not_deleted(self):
        """Deleting is the most destructive thing here, and was the one write
        exempt from "refuse to act on a file that changed underneath us"."""
        import os
        self.project.load_map(1)  # cache it
        path = self.project.path_for(self.project.map_file(1))
        stat = path.stat()
        edited = bytearray(path.read_bytes())
        edited[-1] = (edited[-1] + 1) % 256
        path.write_bytes(bytes(edited))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaises(StaleFileError):
            self.project.delete_file(self.project.map_file(1), tool="test")
        self.assertTrue(path.is_file(), "the file was deleted anyway")


class TestMapIdsAreCanonical(ToolTest):
    def test_an_id_always_names_a_file_that_exists(self):
        """Map7 parses as id 7, but the name an id maps back to is Map007 --
        so accepting the padded spelling put an id in the list whose file is
        not there, and every caller that turned it back into a filename got a
        file it could not open."""
        data = self.data_bytes("Map001")
        (self.tmp / "project" / "Data" /
         ("Map7" + self.extension)).write_bytes(data)
        project = Project(self.tmp / "project")
        for map_id in project.map_ids_on_disk():
            self.assertTrue(
                project.exists(project.map_file(map_id)),
                "id %d is listed but %s is not on disk"
                % (map_id, project.map_file(map_id)))

    def test_a_padded_filename_is_not_a_second_map(self):
        data = self.data_bytes("Map001")
        for name in ("Map1", "Map01", "Map0001"):
            (self.tmp / "project" / "Data" /
             (name + self.extension)).write_bytes(data)
        ids = Project(self.tmp / "project").map_ids_on_disk()
        self.assertEqual(ids.count(1), 1, ids)

class TestAPatchCannotInventAField(ToolTest):
    """apply_patch only checks that a name is a valid Ruby ivar, so a
    plausible typo used to add an instance variable and be reported back as a
    successful change -- while raw_data, the deliberately unvalidated escape
    hatch, refused exactly that on the grounds that the editor rejects an
    object carrying ivars it does not expect."""

    def test_a_misspelled_field_is_refused_and_the_real_one_listed(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items",
                      ops=[{"op": "update", "id": 1, "patch": {"pirce": 900}}])
        message = str(caught.exception)
        self.assertIn("no field", message)
        self.assertIn("price", message, "the message should list the real one")

    def test_the_real_field_still_works_in_either_spelling(self):
        for key in ("price", "@price"):
            with self.subTest(key=key):
                self.call("db_edit", table="items",
                          ops=[{"op": "update", "id": 1, "patch": {key: 900}}])
        self.assertEqual(self.reopen().project.get_entry("items", 1).get("price"),
                         900)

    def test_a_blank_create_is_judged_against_a_sibling(self):
        """A blank entry has no fields of its own, so what the table's other
        entries carry is the only authority for this class."""
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items",
                      ops=[{"op": "create", "patch": {"nmae": "X"}}])
        self.assertIn("no field", str(caught.exception))
        self.call("db_edit", table="items",
                  ops=[{"op": "create", "patch": {"name": "Fine"}}])

    def test_a_map_patch_is_judged_too(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="update", map_id=1,
                      patch={"disable_dashng": True})
        self.assertIn("no field", str(caught.exception))
        self.call("map_edit", op="update", map_id=1,
                  patch={"disable_dashing": True})

class TestStagingIsPerFile(ToolTest):
    """A two-file write must not keep the file whose save was refused.

    One commit flag for the whole block meant the first save to land committed
    every staged file, so a rename whose MapInfos save was refused stayed in
    the cache -- while the caller was told the whole call had failed -- for the
    next write of that file to persist.
    """

    def make_stale(self, filename):
        """Leave a file valid, different, and wearing its old timestamp."""
        import os
        path = self.project.path_for(filename)
        self.project.load_file(filename)          # cache the current value
        stat = path.stat()
        edited = marshal_rb.load(path.read_bytes())
        entry = edited.sorted_items()[0][1] if hasattr(edited, "sorted_items")             else edited[1]
        entry.set("name", self.project.codec.make_string("ChangedOnDisk"))
        path.write_bytes(marshal_rb.dump(edited))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def test_a_refused_second_save_discards_only_its_own_file(self):
        infos_file = self.project.filename_for("map_infos")
        before = self.project.get_entry("map_infos", 1).get("name").data
        self.make_stale(infos_file)

        with self.assertRaises((ToolError, StaleFileError)):
            self.call("map_edit", op="resize", map_id=1, width=25, height=20,
                      name="Renamed")

        # The rename must be gone from the cache, not waiting there. Re-read
        # from disk to see what a later write of this file would marshal.
        after = self.project.get_entry("map_infos", 1).get("name").data
        self.assertNotEqual(after, b"Renamed",
                            "the refused rename is still staged")
        self.assertNotEqual(after, before,
                            "the cache should have been re-read from disk")

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

if __name__ == "__main__":
    unittest.main()


class TestInapplicableArgumentsAreRefused(ToolTest):
    """An argument the chosen op does not read must be refused, not dropped.

    A dropped argument reports the same success as one that was applied, so a
    caller cannot tell that half its request vanished -- and will not retry
    what it believes already happened. `event_edit` op "update" reading a name
    and ignoring `commands` was the worst of these: the rename landed, the
    command list did not, and the reply said "updated event 1".
    """

    def test_event_update_will_not_quietly_drop_commands(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "update", "event_id": 1, "name": "Quiet",
                 "commands": [{"text": "hello"}]}])
        message = str(caught.exception)
        self.assertIn("commands", message)
        # And it says which ops do read it, so the caller can split the request.
        self.assertIn("set_commands", message)

    def test_db_delete_will_not_quietly_drop_a_patch(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="actors", dry_run=True, ops=[
                {"op": "delete", "id": 1, "patch": {"name": "Gone"}}])
        self.assertIn("patch", str(caught.exception))

    def test_map_delete_will_not_quietly_drop_anything(self):
        for extra in ({"patch": {"encounter_step": 9}}, {"name": "Renamed"},
                      {"width": 30}, {"parent_id": 1}):
            with self.subTest(extra=sorted(extra)):
                with self.assertRaises(ToolError) as caught:
                    self.call("map_edit", op="delete", map_id=3, dry_run=True,
                              **extra)
                self.assertIn(sorted(extra)[0], str(caught.exception))

    def test_map_update_will_not_quietly_drop_a_reparent(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="update", map_id=1, dry_run=True,
                      patch={"encounter_step": 25}, parent_id=2)
        self.assertIn("parent_id", str(caught.exception))

    def test_an_op_that_does_read_it_still_works(self):
        # The gate must not refuse the combinations that are real: a resize
        # may carry a patch and a rename, because it is one file write either
        # way.
        out = self.call("map_edit", op="resize", map_id=1, width=21,
                        patch={"encounter_step": 9}, name="Renamed",
                        dry_run=True)
        # `would_change` on a dry run -- see TestEveryPreviewReadsAsAPreview.
        self.assertIn("resized from 20x15 to 21x15", out["would_change"])

    def test_every_op_in_the_schema_has_a_table_entry(self):
        """A new op must not be able to land without declaring what it reads.

        The op names and the table are the same dict now, but the argument
        names in each set are written by hand, so this also holds them to the
        schema: a set naming an argument the schema does not declare would
        refuse nothing, and check_args would have refused it first.
        """
        for tool_name, key, table in (
                ("event_edit", "ops", event_tools.EVENT_OP_ARGS),
                ("db_edit", "ops", db_tools.DB_OP_ARGS),
                ("map_edit", None, map_tools.MAP_OP_ARGS)):
            with self.subTest(tool=tool_name):
                properties = REGISTRY[tool_name].schema["properties"]
                if key:
                    properties = properties[key]["items"]["properties"]
                declared = set(properties)
                self.assertEqual(set(properties["op"]["enum"]), set(table))
                for op, reads in table.items():
                    self.assertLessEqual(reads, declared,
                                         "%s op %s names an argument the "
                                         "schema does not declare" % (tool_name, op))
                # And the other direction, which is this round's whole defect:
                # an argument the schema advertises that no op reads would be
                # accepted by check_args, refused by nothing, and dropped.
                never_read = declared - set().union(*table.values())
                self.assertEqual(
                    never_read, set(),
                    "%s declares %s, which no op reads -- it would be "
                    "accepted and ignored"
                    % (tool_name, ", ".join(sorted(never_read))))


class TestANestedPatchCannotInventAField(ToolTest):
    """A typo one level down must be refused like a typo at the top.

    `refuse_unknown_fields` compares the patch's top-level keys, and
    `apply_patch` merges nested dicts field by field, so
    {"bgm": {"nmae": "Town"}} added @nmae to the RPG::BGM and reported
    `bgm.nmae: <unset> -> "Town"` as the change asked for. The editor rejects
    an object carrying instance variables it does not expect, so that write
    produces a project that will not open.
    """

    def test_a_nested_typo_is_refused(self):
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("map_edit", op="update", map_id=1,
                      patch={"bgm": {"nmae": "Town1"}})
        message = str(caught.exception)
        self.assertIn("nmae", message)
        self.assertIn("RPG::BGM", message)

    def test_the_field_is_not_left_behind(self):
        with self.assertRaises((ToolError, ConversionError)):
            self.call("map_edit", op="update", map_id=1,
                      patch={"bgm": {"nmae": "Town1"}})
        bgm = self.reopen().project.load_map(1).get("bgm")
        self.assertFalse(bgm.has("nmae"))

    def test_a_real_nested_field_still_merges(self):
        out = self.call("map_edit", op="update", map_id=1,
                        patch={"bgm": {"volume": 55}})
        self.assertIn("bgm.volume: 100 -> 55", out["changes"])
        self.assertEqual(self.reopen().project.load_map(1).get("bgm").get("volume"), 55)


class TestAddPageRefusesInsteadOfCrashing(ToolTest):
    """add_page with a misspelled patch field must refuse, not raise.

    The refusal message named a page index that is only assigned further down,
    for the ops that act on an existing page, so this raised UnboundLocalError
    -- which server.py does not catch, so the model was told the server is
    broken rather than that it made a typo.
    """

    def test_a_misspelled_field_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "add_page", "event_id": 1,
                 "patch": {"trigggger": 3}}])
        self.assertIn("trigggger", str(caught.exception))

    def test_add_page_with_a_real_patch_works(self):
        out = self.call("event_edit", map_id=1, ops=[
            {"op": "add_page", "event_id": 1, "patch": {"trigger": 2}}])
        self.assertEqual(out["applied"][0]["op"], "add_page")


class TestSearchPatternsStayFast(ToolTest):
    r"""The corpus pattern_safety's docstring points at.

    A cost model cannot be checked by reading it, so this measures. Every
    pattern the module allows must finish quickly against the worst subject it
    will ever see, and every pattern that can be made to backtrack must be
    refused. Python's `re` cannot be interrupted, so a pattern that is merely
    slow here is a server the user has to kill.

    **The subject is part of the case.** An earlier version of this test held
    one fixed tuple of subjects, and that is why it passed while five bypasses
    were live: `\d?\s?` repeated is 0.001s against letters and does not
    finish against digits. Each attack below therefore names the text that
    makes it fatal, measured.
    """

    # A long run of one kind of character is what makes competing atoms
    # backtrack, so the subjects are homogeneous by construction.
    LETTERS = "a" * MAX_SUBJECT_CHARS
    DIGITS = "1" * MAX_SUBJECT_CHARS
    SPACES = " " * MAX_SUBJECT_CHARS
    MIXED = ("a1 " * MAX_SUBJECT_CHARS)[:MAX_SUBJECT_CHARS]
    ALTERNATING = "ab" * (MAX_SUBJECT_CHARS // 2)

    SUBJECTS = (LETTERS, DIGITS, SPACES, MIXED, ALTERNATING)

    # (pattern, the subject it is fatal against, how it evades a cost model).
    # Every time here was measured in a killable child process; ">15s" means
    # the child was still running when it was killed.
    ATTACKS = (
        # Classic nested repeats: a quantifier over a body that can itself
        # match more than one way.
        ("(a+)+b", LETTERS, "nested repeat"),
        ("((a+))+b", LETTERS, "nested repeat, one group deeper"),
        ("(?:a+)+b", LETTERS, "nested repeat, non-capturing"),
        ("(a|a)+b", LETTERS, "repeat over an ambiguous branch"),
        ("(a{1,2})+b", LETTERS, "repeat over a bounded repeat"),
        ("(a*)*b", LETTERS, "repeat over a nullable repeat"),
        ("(a|b|ab)*c", ALTERNATING, "repeat over an ambiguous branch"),
        (r"(\w+\s?)*$", LETTERS, "repeat over a nullable body"),
        ("(x+x+)+y", "x" * MAX_SUBJECT_CHARS, "repeat over two quantifiers"),

        # Homogeneous chains: many atoms of one kind, all competing.
        ("a?" * 24 + "b", LETTERS, "optional chain"),
        ("a?" * 40 + "b", LETTERS, "optional chain, longer"),
        ("(a|ab)" * 20 + "z", LETTERS, "alternation chain"),
        ("a{0,500}a{0,500}a{0,500}b", LETTERS, "bounded repeat chain"),
        ("[a-z]+[a-z]+[a-z]+[a-z]+!", LETTERS, "four quantifiers, one class"),

        # Nullable atoms that a disjointness test reads as breaking the chain,
        # while at match time each matches empty and they all stay adjacent.
        # >15s each, measured.
        (r"\w?\W?" * 24 + "\x00", LETTERS, "nullable word/non-word chain"),
        ("a?b?" * 24 + "c", LETTERS, "nullable literal chain"),
        (r"\d?\s?" * 24 + "\x00", DIGITS, "nullable digit/space chain"),

        # Case: the match runs with IGNORECASE, so these compete even though
        # their literals differ. >15s each, measured.
        ("a+A+a+z", LETTERS, "three quantifiers, one letter in two cases"),
        ("a+A+a+A+z", LETTERS, "four quantifiers, one letter in two cases"),
        ("a?A?" * 24 + "z", LETTERS, "nullable chain, one letter in two cases"),

        # Two blocks, each on its own inside a budget, multiplying together.
        (r"\d?" * 14 + r"\s?" * 14 + "z", DIGITS, "two independent blocks"),

        # Two quantifiers over one class, with nothing required between them.
        # 14.3s and >15s, measured -- and both score the same as
        # `Gold\s+\d+`, which is instant, so no score can separate them.
        ("a*a*c", LETTERS, "two stars, one character"),
        ("[a-z]+[a-z]+!", LETTERS, "two quantifiers, one class"),
        (r"\w+\d+!", DIGITS, "two quantifiers, overlapping classes"),

        # A nullable atom does not separate its neighbours: it can match
        # empty, so the atoms either side of it stay adjacent in the subject.
        # Scoring 2 before the fix, and over thirty seconds against a run of
        # "=" -- which is what a stock VX Ace script separator line is. The
        # first shape also names eight cheap characters so that the dangerous
        # one was pushed out of every measured subject. Both measured.
        ("[]?"
         + "=?~?" * 24 + "!", "=" * MAX_SUBJECT_CHARS,
         "nullable filler, dangerous character hidden from the subjects"),
        ("=?~?" * 24 + "!", "=" * MAX_SUBJECT_CHARS,
         "nullable filler between competing atoms"),

        # Lookaround: refused outright, because each runs its own match at
        # every position and there is no way to bound that here.
        (r"Fire(?=Sword)", LETTERS, "lookahead"),
        (r"Gold(?!\d)", LETTERS, "negative lookahead"),
        (r"(?<=Name: )\w+", LETTERS, "lookbehind"),
    )

    # Patterns a caller would really write. Each must be allowed, and fast
    # against every subject above.
    ORDINARY = (
        r"\bHP\b", "Sword|Blade|Axe", r"^\s*#", "[Ff]ire.*damage",
        r"Gold\s+\d+", r"(fire|ice)\s*bolt", "Fire.*Sword.*Shield",
        r"\d{1,3} damage", "Potion", r"actor_\d+", r"[A-Z][a-z]+",
        "self_switch", r"item\s*=\s*\d+", ".*",
        "(?:Elixir|Potion)s?", r"[0-9]+\.[0-9]+", r"^\$game_",
        r"Show Text.*\\v\[\d+\]",
    )

    # Generous enough not to be flaky on a loaded machine, and far below the
    # ten-plus seconds every refused pattern above was measured at.
    BUDGET_SECONDS = 2.0

    def test_every_costly_pattern_is_refused(self):
        for pattern, _subject, why in self.ATTACKS:
            with self.subTest(shape=why, pattern=pattern[:28]):
                with self.assertRaises((PatternTooCostly, ToolError)):
                    check_pattern(pattern)

    def test_the_tool_refuses_them_too(self):
        """Through `search`, so the refusal reaches the caller as a ToolError."""
        for pattern, _subject, why in self.ATTACKS[:6]:
            with self.subTest(shape=why):
                with self.assertRaises(ToolError):
                    self.call("search", query=pattern, regex=True)

    def test_every_allowed_pattern_finishes_quickly(self):
        import re
        import time
        for pattern in self.ORDINARY:
            with self.subTest(pattern=pattern):
                check_pattern(pattern)         # must not refuse a real pattern
                compiled = re.compile(pattern, re.IGNORECASE)
                start = time.perf_counter()
                for subject in self.SUBJECTS:
                    compiled.search(subject[:MAX_SUBJECT_CHARS])
                elapsed = time.perf_counter() - start
                self.assertLess(
                    elapsed, self.BUDGET_SECONDS,
                    "%r took %.2fs against %d subjects"
                    % (pattern, elapsed, len(self.SUBJECTS)))


class TestSearchSaysWhatItDidNotRead(ToolTest):
    """A regex is matched against a bounded prefix; a caller must be told.

    A plain substring search reads every string in full and a regex search
    does not, and the two reported the same shape -- so "no hits" from a regex
    over a project with long script lines was indistinguishable from a search
    that had genuinely looked everywhere.
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
        self.assertIn("notes", out)
        self.assertIn(str(MAX_SUBJECT_CHARS), out["notes"])

    def test_a_substring_search_reads_the_whole_string(self):
        self.long_note()
        out = self.call("search", query="NEEDLE", scopes=["database"])
        self.assertEqual(out["count"], 1)
        # Nothing was clipped, so nothing is claimed to have been.
        self.assertNotIn("notes", out)


class TestAMapPatchCannotMoveAnEvent(ToolTest):
    """@events must be refused in a map patch, as the message already said.

    A patch merges field by field, so patch={"events": {"1": {"x": 99999}}}
    reached into the event object and moved it off the map -- past event_edit's
    bounds check, and past a refusal whose own text says to use event_edit for
    events. raw_data refused this and map_edit did not.
    """

    def test_patching_events_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="update", map_id=1,
                      patch={"events": {"1": {"x": 99999, "y": 99999}}})
        self.assertIn("events", str(caught.exception))

    def test_the_event_did_not_move(self):
        with self.assertRaises(ToolError):
            self.call("map_edit", op="update", map_id=1,
                      patch={"events": {"1": {"x": 99999, "y": 99999}}})
        event = self.reopen().project.load_map(1).get("events").get(1)
        self.assertEqual(event.get("x"), 5)

    def test_both_guards_read_the_same_list(self):
        """The two lists had drifted, which is what opened the hole."""
        self.assertEqual(tuple(raw_tools.STRUCTURAL_MAP_PATHS),
                         tuple(map_tools.STRUCTURAL_MAP_FIELDS))


class TestCommandReferenceSaysWhoseLayoutsTheseAre(ToolTest):
    """An unmodelled engine's layouts are Ace's, and must not read as its own.

    `command_table` and `param_specs` fall back to Ace for an engine this
    server does not model. Reading is still useful; presenting those layouts as
    confirmed is how a caller writes a raw command with the wrong parameter
    order, which the interpreter then misreads.
    """

    def test_a_modelled_engine_says_yes(self):
        out = self.call("command_reference", search="text")
        self.assertTrue(out["layouts_are_this_engines"])
        self.assertNotIn("engine_warning", out)

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


class TestRawDataWillNotHandBackCompressedScripts(ToolTest):
    """The scripts refusal was claimed for both directions, done for one.

    Reading returned every slot body as base64 of compressed Ruby: bytes no
    caller can use, in a response large enough to crowd out everything else,
    from the one table whose whole point is that another tool decompresses it.
    db_get was changed to refuse this and raw_data was not.
    """

    def test_reading_the_scripts_table_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", table="scripts", path="")
        self.assertIn("scripts tool", str(caught.exception))

    def test_writing_it_is_still_refused(self):
        with self.assertRaises(ToolError):
            self.call("raw_data", op="set", table="scripts", path="0/2",
                      value={"value": "puts 1"})


class TestAnEmptyCommandListIsRefusedEverywhere(ToolTest):
    """commands: [] is a deletion wearing the name of an edit.

    Two of the five sites that take `commands` refused it; the three that take
    it alongside a patch did not, so set_page with an empty list compiled
    nothing, wrote the lone blank terminator, and reported "replaced the
    command list".
    """

    def test_set_page_refuses_it(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "set_page", "event_id": 1, "page": 0, "commands": []}])
        self.assertIn("delete_page", str(caught.exception))

    def test_add_page_refuses_it(self):
        with self.assertRaises(ToolError):
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "add_page", "event_id": 1, "commands": []}])

    def test_create_refuses_it(self):
        with self.assertRaises(ToolError):
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "create", "name": "New", "x": 1, "y": 1,
                 "commands": []}])

    def test_the_page_still_holds_what_it_did(self):
        before = len(self.project.load_map(1).get("events").get(1)
                     .get("pages")[0].get("list"))
        with self.assertRaises(ToolError):
            self.call("event_edit", map_id=1, ops=[
                {"op": "set_page", "event_id": 1, "page": 0, "commands": []}])
        after = len(self.reopen().project.load_map(1).get("events").get(1)
                    .get("pages")[0].get("list"))
        self.assertEqual(before, after)


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


class TestDbGetArgumentsAreHonoured(ToolTest):
    """An argument db_get cannot honour must be refused, not ignored."""

    def test_ids_on_a_single_object_table_are_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_get", table="system", ids=[1, 2])
        self.assertIn("system", str(caught.exception))

    def test_reading_the_single_object_still_works(self):
        out = self.call("db_get", table="system")
        self.assertIn("entry", out)

    def test_a_non_string_field_name_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("db_list", table="items", fields=[["price"]])

    def test_the_empty_slot_note_only_offers_what_create_accepts(self):
        """Both ids are named, and only the creatable one is offered."""
        out = self.call("db_get", table="items", ids=[0, 998])
        note = out["note"]
        self.assertIn("fills in 998", note)
        # Id 0 is named as refused rather than silently dropped from the
        # advice: "we did not mention it" and "you can create it" read the
        # same to a caller scanning for its id.
        self.assertIn("will not create 0", note)
        self.assertIn("slot 0 empty", note)

    def test_scripts_is_not_advertised(self):
        self.assertNotIn("scripts",
                         REGISTRY["db_get"].schema["properties"]["table"]["enum"])


class TestMapTilesNamesAnEmptyTable(ToolTest):
    """A map with no cells must be named, not reported as a bad argument.

    Every bound in map_tiles is derived from the table's extents, so an empty
    one came back as "the default for x must be at most -1, got 0" -- a message
    about an argument the caller never sent.
    """

    def test_an_empty_tile_table_says_so(self):
        game_map = self.project.load_map(1)
        game_map.set("data", Table(0, 0, 1))
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="emptied")
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1)
        message = str(caught.exception)
        self.assertIn("no cells", message)
        self.assertNotIn("default for x", message)


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


class TestDiscardedTileCountIsRight(ToolTest):
    """The slice-based count must agree with counting cell by cell.

    _cells_outside was rewritten to work over rows of the flat buffer rather
    than two Python calls per cell; a count of someone's discarded work is not
    a place to be approximately right.
    """

    def test_it_matches_a_per_cell_count(self):
        import random
        random.seed(11)
        for _ in range(40):
            xs, ys, zs = (random.randint(1, 7), random.randint(1, 7),
                          random.randint(1, 3))
            table = Table(xs, ys, zs)
            for _ in range(random.randint(0, xs * ys * zs)):
                table.set(random.randrange(xs), random.randrange(ys),
                          random.randrange(zs), random.randint(1, 400))
            for width in range(xs + 2):
                for height in range(ys + 2):
                    expected = 0
                    if width < xs or height < ys:
                        expected = sum(
                            1
                            for z in range(zs)
                            for y in range(ys)
                            for x in range(xs)
                            if (x >= width or y >= height)
                            and table.get(x, y, z))
                    self.assertEqual(
                        map_tools._cells_outside(table, width, height),
                        expected,
                        "%dx%dx%d cropped to %dx%d" % (xs, ys, zs, width, height))


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


class TestSnapshotPreviewMatchesTheSnapshot(ToolTest):
    """A dry run must report what the real call would do.

    The preview globbed the data folder itself while `snapshot` skips symlinked
    files, so it listed files no backup would ever hold -- the same mistake the
    prune branch's comment says it fixed by asking the real function.
    """

    def test_the_preview_leaves_out_what_the_snapshot_skips(self):
        """The difference the two could not agree on was a skipped file.

        `snapshot` refuses to copy a symlinked data file, because copying one
        would put whatever it points at into the backup tree. The preview
        globbed the folder itself, so it listed that file anyway. Symlinks
        cannot be created without privilege on this platform, so one is
        simulated -- the property under test is that the preview asks the
        function that copies, not that this platform has symlinks.
        """
        import pathlib as _pathlib
        skipped = self.project.filename_for("items")
        real_is_symlink = _pathlib.Path.is_symlink

        def pretend(path):
            return path.name == skipped or real_is_symlink(path)

        _pathlib.Path.is_symlink = pretend
        self.addCleanup(setattr, _pathlib.Path, "is_symlink", real_is_symlink)

        preview = self.call("backups", op="create", dry_run=True)
        real = self.call("backups", op="create")
        self.assertNotIn(skipped, real["files"])
        self.assertEqual(preview["would_snapshot"], real["files"])

    def test_the_preview_matches_an_ordinary_snapshot(self):
        preview = self.call("backups", op="create", dry_run=True)
        real = self.call("backups", op="create")
        self.assertEqual(preview["would_snapshot"], real["files"])

    def test_a_dry_run_writes_nothing(self):
        before = self.call("backups", op="list")["backups"]
        self.call("backups", op="create", dry_run=True)
        self.assertEqual(self.call("backups", op="list")["backups"], before)


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


class TestHashTablesThatCarryTheirOwnId(unittest.TestCase):
    """VX's `areas` is Hash-keyed and RPG::Area does carry @id.

    `_carries_id` decided from ARRAY-versus-HASH, in a docstring that asserted
    a Hash-keyed table never carries one -- so an area created here was written
    with no @id at all, and a patch moving @id to another number was accepted.
    MapInfos, the case that reasoning was built for, genuinely has no id field
    and must stay exempt.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-areas-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        build(self.tmp / "project", "vx")
        self.project = Project(self.tmp / "project")
        self.ctx = Context(self.project)

    def call(self, tool_name, /, **arguments):
        return REGISTRY[tool_name].run(self.ctx, arguments)

    def test_a_created_area_carries_its_own_id(self):
        self.call("db_edit", table="areas", ops=[
            {"op": "create", "id": 4, "patch": {"name": "Cave"}}])
        entry = Project(self.tmp / "project").get_entry("areas", 4)
        self.assertEqual(entry.get("id"), 4)

    def test_a_clone_gets_its_own_id_not_the_originals(self):
        self.call("db_edit", table="areas", ops=[
            {"op": "create", "id": 1, "patch": {"name": "First"}}])
        self.call("db_edit", table="areas", ops=[
            {"op": "create", "id": 7, "clone_from": 1}])
        self.assertEqual(self.project.get_entry("areas", 7).get("id"), 7)

    def test_a_patch_cannot_move_an_areas_id(self):
        self.call("db_edit", table="areas", ops=[
            {"op": "create", "id": 2, "patch": {"name": "Second"}}])
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="areas", ops=[
                {"op": "update", "id": 2, "patch": {"id": 99}}])
        self.assertIn("its position", str(caught.exception))

    def test_map_infos_is_still_exempt(self):
        """RPG::MapInfo has no id field, which is why the rule was written."""
        self.assertNotIn("id", PROFILES["vx"].class_fields["RPG::MapInfo"])


class TestUnknownCharacterClassesCountAsCompeting(unittest.TestCase):
    """An unrecognised pair of character categories must be assumed to overlap.

    The budget only multiplies backtrack points that can compete for the same
    characters, so "these cannot meet" is the answer that makes a chain score
    as free. The first version of this listed the overlapping pairs and
    defaulted to disjoint, so a pair it had not thought of -- a newline
    against a whitespace class, which do overlap -- read as unable to compete.
    """

    def charset(self, *categories):
        return pattern_safety._Charset(categories=set(categories))

    def test_a_category_and_its_complement_are_disjoint(self):
        for name in ("SPACE", "DIGIT", "WORD", "LINEBREAK"):
            with self.subTest(category=name):
                self.assertFalse(
                    self.charset("CATEGORY_" + name).intersects(
                        self.charset("CATEGORY_NOT_" + name)))

    def test_overlapping_categories_are_reported_as_such(self):
        for left, right in (("CATEGORY_DIGIT", "CATEGORY_WORD"),
                            ("CATEGORY_LINEBREAK", "CATEGORY_SPACE"),
                            ("CATEGORY_WORD", "CATEGORY_NOT_SPACE")):
            with self.subTest(pair=(left, right)):
                self.assertTrue(self.charset(left).intersects(self.charset(right)))

    def test_an_unlisted_pair_counts_as_overlapping(self):
        """The safe direction: refuse more, never score a real chain as free."""
        self.assertTrue(
            self.charset("CATEGORY_SOMETHING_NEW").intersects(
                self.charset("CATEGORY_ANOTHER_ONE")))

    def test_the_order_a_pair_is_written_in_does_not_matter(self):
        """Two entries were written the opposite way round from the lookup."""
        for left, right in (("CATEGORY_SPACE", "CATEGORY_NOT_SPACE"),
                            ("CATEGORY_WORD", "CATEGORY_NOT_WORD")):
            with self.subTest(pair=(left, right)):
                self.assertFalse(self.charset(left).intersects(self.charset(right)))
                self.assertFalse(self.charset(right).intersects(self.charset(left)))


class TestEveryOpSelectingToolHasATable(unittest.TestCase):
    """No tool may take an `op` without declaring what each op reads.

    Fixing the tools that had no per-op table was not the fix; this is. The
    gate was added to the three editing tools, its docstring said "nothing is
    ignored silently here", and four op-selecting tools -- map_tiles, raw_data,
    scripts, backups -- had no table at all, so each accepted the other ops'
    arguments and reported success. A reviewer found them, twice. This walks
    the registry so the next one cannot land unnoticed.
    """

    # Every op table in the tools layer, by the tool and argument path it
    # governs. `ops` means the op sits inside each entry of an `ops` array.
    TABLES = {
        ("map_edit", None): map_tools.MAP_OP_ARGS,
        ("map_tiles", None): map_tools.TILE_OP_ARGS,
        ("db_edit", "ops"): db_tools.DB_OP_ARGS,
        ("event_edit", "ops"): event_tools.EVENT_OP_ARGS,
        ("raw_data", None): raw_tools.RAW_OP_ARGS,
        ("scripts", None): misc_tools.SCRIPT_OP_ARGS,
        ("backups", None): misc_tools.BACKUP_OP_ARGS,
    }

    def op_selecting_tools(self):
        """Every (tool, path) in the registry whose schema declares an `op`."""
        found = []
        for name, tool in REGISTRY.items():
            properties = tool.schema.get("properties") or {}
            if "op" in properties:
                found.append((name, None))
            inside = (properties.get("ops", {}).get("items", {})
                      .get("properties", {}))
            if "op" in inside:
                found.append((name, "ops"))
        return sorted(found)

    def test_every_op_selecting_tool_is_in_the_table_list(self):
        self.assertEqual(self.op_selecting_tools(), sorted(self.TABLES),
                         "a tool takes an op with no per-op argument table, "
                         "so it accepts the other ops' arguments and ignores "
                         "them")

    def test_every_table_agrees_with_its_schema(self):
        for (name, key), table in sorted(self.TABLES.items()):
            with self.subTest(tool=name, path=key):
                properties = REGISTRY[name].schema["properties"]
                if key:
                    properties = properties[key]["items"]["properties"]
                self.assertEqual(set(properties["op"]["enum"]), set(table))
                declared = set(properties)
                for op, reads in table.items():
                    self.assertLessEqual(
                        reads, declared,
                        "%s op %s reads an argument the schema does not "
                        "declare" % (name, op))
                never_read = declared - set().union(*table.values())
                self.assertEqual(
                    never_read, set(),
                    "%s declares %s, which no op reads -- it would be "
                    "accepted and ignored" % (name, ", ".join(sorted(never_read))))

    def test_every_dispatcher_covers_exactly_its_table(self):
        """A dispatch table and an argument table must name the same ops.

        db_tools carried three separate lists of the same three op names while
        the comment above one of them said they were "named once, so they
        cannot drift apart". An op added to two of the three raises KeyError at
        dispatch, which is the shape of failure a comment cannot prevent.
        """
        for label, dispatch, declared in (
                ("db_edit", db_tools._DB_OPS, db_tools.DB_OP_ARGS),
                ("event_edit", event_tools._EVENT_OPS,
                 event_tools.EVENT_OP_ARGS)):
            with self.subTest(tool=label):
                self.assertEqual(set(dispatch), set(declared))

    def test_the_common_event_path_has_one_too(self):
        """event_edit runs a second loop that the schema cannot distinguish.

        A common event has no pages and no position, so its ops read far less
        -- and that loop had no table, so `commands` on an "update" were
        dropped and an `event_id` that means nothing there was accepted.
        """
        table = event_tools.COMMON_EVENT_OP_ARGS
        declared = set(REGISTRY["event_edit"]
                       .schema["properties"]["ops"]["items"]["properties"])
        self.assertLessEqual(set(table), set(event_tools.EVENT_OP_ARGS))
        for op, reads in table.items():
            self.assertLessEqual(reads, declared)


class TestOpArgumentsAreGatedEverywhere(ToolTest):
    """The gate must reach every tool that takes an op, not the editing three.

    `check_op_args` was added to map_edit, db_edit and event_edit, and its
    docstring said "nothing is ignored silently here" while map_tiles,
    raw_data, scripts and backups had no table at all -- so each accepted the
    other ops' arguments, ignored them, and reported success.
    """

    def test_map_tiles_refuses_the_other_ops_arguments(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, op="fill", tile_id=5,
                      rows=[[1]], dry_run=True)
        message = str(caught.exception)
        self.assertIn("rows", message)
        self.assertIn("write", message)      # the op that does read it

    def test_map_tiles_still_fills(self):
        out = self.call("map_tiles", map_id=1, op="fill", tile_id=5, x=0, y=0,
                        width=2, height=2, dry_run=True)
        self.assertIn("filled", out["would_change"])

    def test_raw_data_refuses_a_value_it_would_not_write(self):
        """`op` defaults to "get", which is the dangerous half.

        A caller that meant to write and forgot the op used to get back the
        value already there, under a "value" key that reads exactly like
        confirmation of the write it thought it had made.
        """
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", table="system", path="party_members",
                      value={"value": [1]})
        self.assertIn("value", str(caught.exception))
        self.assertIn("set", str(caught.exception))

    def test_raw_data_still_reads_and_writes(self):
        self.assertEqual(
            self.call("raw_data", table="system", path="party_members")["value"],
            [1, 2])
        out = self.call("raw_data", op="set", table="system",
                        path="party_members", value={"value": [1]},
                        dry_run=True)
        self.assertEqual(out["would_become"], [1])

    def test_scripts_refuses_code_on_a_read(self):
        with self.assertRaises(ToolError) as caught:
            self.call("scripts", op="get", index=0, code="puts 1")
        self.assertIn("code", str(caught.exception))

    def test_backups_refuses_files_on_a_create(self):
        """"create" snapshots the whole folder; `files` belongs to "restore"."""
        with self.assertRaises(ToolError) as caught:
            self.call("backups", op="create", files=["Items.rvdata2"])
        self.assertIn("files", str(caught.exception))


class TestCommonEventOpsAreGatedToo(ToolTest):
    """event_edit runs a second loop, and it had no table.

    A common event has no pages and no position, so its ops read far less --
    and this is the sibling path I fixed the map path without touching.
    """

    def test_commands_are_not_dropped_on_an_update(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", common_event_id=1, dry_run=True, ops=[
                {"op": "update", "name": "Renamed",
                 "commands": [{"text": "dropped"}]}])
        message = str(caught.exception)
        self.assertIn("commands", message)
        self.assertIn("set_commands", message)

    def test_an_event_id_means_nothing_here(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", common_event_id=1, dry_run=True, ops=[
                {"op": "set_commands", "event_id": 3,
                 "commands": [{"text": "hi"}]}])
        self.assertIn("event_id", str(caught.exception))

    def test_a_real_common_event_edit_still_works(self):
        out = self.call("event_edit", common_event_id=1, dry_run=True, ops=[
            {"op": "set_commands", "commands": [{"text": "hi"}]}])
        self.assertEqual(out["would_apply"][0]["op"], "set_commands")


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
        self.assertGreater(layer["distinct_tile_ids"], map_tools.TOP_TILE_IDS)
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


class TestLookaroundIsRefusedByName(ToolTest):
    """A refusal has to name the shape it found.

    A lookaround was charged past the budget, so it came back described as a
    repeated group or a chain of competing parts -- neither of which the
    pattern has. `Gold(?!\\d)` was told to "anchor the pattern with the
    literal text you are looking for", which it already was, so a caller
    cannot find the cause and rephrases forever.
    """

    def test_a_lookaround_names_itself(self):
        for pattern in (r"Fire(?=Sword)", r"Gold(?!\d)", r"(?<=Name: )\w+"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ToolError) as caught:
                    self.call("search", query=pattern, regex=True)
                message = str(caught.exception)
                self.assertIn("lookahead", message)
                self.assertNotIn("repeats a group", message)


class TestNoOpSlipsPastTheGate(ToolTest):
    """The op names a tool accepts must be exactly the ops it gates.

    `check_op_args` returns without checking when an op has no table entry,
    reasoning that `one_of` has already said which ops exist. That holds only
    while the two lists agree -- and map_edit spelled its op names three
    times, in the schema enum, in `one_of`, and as the table's keys, 230 lines
    apart. An op present in the first two and missing from the third switches
    the argument gate off for exactly that op, silently, which is the failure
    the gate was built to end.

    So: no silent return for an unknown op, and the list `one_of` enforces is
    read back out of its own refusal rather than trusted.
    """

    # Every op-selecting tool, and the table that must cover it. `ops` means
    # the op sits inside each entry of an `ops` array.
    TABLES = {
        ("map_edit", None): map_tools.MAP_OP_ARGS,
        ("map_tiles", None): map_tools.TILE_OP_ARGS,
        ("db_edit", "ops"): db_tools.DB_OP_ARGS,
        ("event_edit", "ops"): event_tools.EVENT_OP_ARGS,
        ("raw_data", None): raw_tools.RAW_OP_ARGS,
        ("scripts", None): misc_tools.SCRIPT_OP_ARGS,
        ("backups", None): misc_tools.BACKUP_OP_ARGS,
    }

    def ops_one_of_accepts(self, tool_name, key):
        """The op names the handler's own `one_of` call enforces.

        Read out of the refusal it raises for an unknown op, because that is
        the list the running code uses -- a structural comparison against the
        schema would miss a third hardcoded copy, which is the bug here.
        """
        arguments = {"op": "__no_such_op__"}
        if key:
            arguments = {"ops": [arguments]}
        # Enough to get past any required argument that is read first.
        arguments.update({"map_id": 1, "table": "items",
                          "common_event_id": None})
        arguments = {k: v for k, v in arguments.items()
                     if v is not None
                     and k in (REGISTRY[tool_name].schema.get("properties") or {})}
        if key:
            arguments["ops"] = [{"op": "__no_such_op__"}]
        with self.assertRaises(ToolError) as caught:
            self.call(tool_name, **arguments)
        message = str(caught.exception)
        self.assertIn("must be one of", message,
                      "%s did not refuse an unknown op through one_of" % tool_name)
        listed = message.split("must be one of", 1)[1].split(", got")[0]
        return {name.strip() for name in listed.split(",")}

    def test_one_of_and_the_table_name_the_same_ops(self):
        for (tool_name, key), table in sorted(self.TABLES.items()):
            with self.subTest(tool=tool_name):
                self.assertEqual(self.ops_one_of_accepts(tool_name, key),
                                 set(table))

    def test_an_op_with_no_table_entry_is_refused_not_ignored(self):
        """The silent return is the hole, whatever the lists say.

        Two agreeing lists is the good case; this is what has to happen in the
        bad one. Returning quietly means the caller's inapplicable arguments
        are dropped and the call reports success -- the exact failure this
        function exists to prevent, reached through the function itself.
        """
        with self.assertRaises(ToolError) as caught:
            check_op_args("not_a_real_op", {"op": "not_a_real_op", "patch": {}},
                          {"update": {"op", "patch"}}, "a tool")
        self.assertIn("not_a_real_op", str(caught.exception))


class TestAWholesaleObjectCannotInventFields(ToolTest):
    """A value carrying __class__ replaces an object, and its own field names
    were never checked.

    `refuse_unknown_fields` inspects the patch's key *names* against the
    entry's fields, so the top-level key passes; `apply_patch` then sees a
    `__class__` and takes the replacement path, which never reaches
    `_refuse_invented_fields`; `from_json` builds the object with nothing but
    `check_ivar_name` on each key. So a typo inside a replacement object is
    written into the file and reported as the change the caller asked for --
    and the editor then refuses to open the project.

    Reachable three ways, all of them through tools that advertise validation:
    a field's value, an element of an array, and raw_data's whole value.
    """

    def bgm_fields(self):
        return set(self.reopen().project.load_map(1).get("bgm").fields())

    def test_a_typo_in_a_replacement_object_is_refused(self):
        before = self.bgm_fields()
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("map_edit", op="update", map_id=1, patch={
                "bgm": {"__class__": "RPG::BGM", "name": "Town1",
                        "volume": 100, "pitch": 100, "pich": 100}})
        self.assertIn("pich", str(caught.exception))
        self.assertEqual(self.bgm_fields(), before)

    def test_a_typo_inside_an_array_element_is_refused(self):
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("db_edit", table="classes", dry_run=True, ops=[
                {"op": "update", "id": 1, "patch": {"learnings": [
                    {"__class__": "RPG::Class::Learning", "skill_id": 1,
                     "levl": 5}]}}])
        self.assertIn("levl", str(caught.exception))

    def test_raw_data_refuses_one_too(self):
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("raw_data", op="set", map_id=1, path="bgm", value={
                "value": {"__class__": "RPG::BGM", "name": "Town1",
                          "volume": 100, "pitch": 100, "naem": "x"}})
        self.assertIn("naem", str(caught.exception))

    def test_a_correct_replacement_still_works(self):
        out = self.call("map_edit", op="update", map_id=1, patch={
            "bgm": {"__class__": "RPG::BGM", "name": "Cave", "volume": 70,
                    "pitch": 100}})
        self.assertTrue(out["changes"])
        self.assertEqual(self.reopen().project.load_map(1).get("bgm").get("name").data,
                         b"Cave")


class TestReadToolsDropNoArgumentsEither(ToolTest):
    """The gate covers ops; these two tools branch on something else.

    `event_get` chooses between a map and a common event by which id it was
    given, and `command_reference` between three answers by `code`,
    `search` and `move_commands`. Neither branch is an `op`, so
    `check_op_args` does not reach them -- and each silently dropped the
    arguments its branch does not read, which is the failure the gate exists
    to end, reached around it.
    """

    def test_a_common_event_read_refuses_page_arguments(self):
        """A common event has no pages and is addressed by id.

        `event_get common_event_id=3 name_contains="Fire"` returned the event
        in full whether or not its name matched, and the reply reads exactly
        like a positive match.
        """
        for extra in ({"name_contains": "zzz"}, {"page": 0},
                      {"event_ids": [1]}):
            with self.subTest(extra=sorted(extra)):
                with self.assertRaises(ToolError) as caught:
                    self.call("event_get", common_event_id=1, **extra)
                self.assertIn(sorted(extra)[0], str(caught.exception))

    def test_a_common_event_read_still_works(self):
        out = self.call("event_get", common_event_id=1)
        self.assertIn("script", out)

    def test_a_map_read_still_takes_them(self):
        out = self.call("event_get", map_id=1, name_contains="Elder")
        self.assertTrue(out["events"])

    def test_a_code_lookup_refuses_a_search_it_would_not_run(self):
        """`code` returned before `search` was ever read."""
        with self.assertRaises(ToolError) as caught:
            self.call("command_reference", code=101, search="text")
        self.assertIn("search", str(caught.exception))

    def test_each_command_reference_answer_still_works(self):
        self.assertEqual(self.call("command_reference", code=101)["code"], 101)
        self.assertTrue(self.call("command_reference", search="text")["commands"])
        self.assertTrue(self.call("command_reference",
                                  move_commands=True)["move_commands"])


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


class TestMapGetHonoursTilesWithoutATable(ToolTest):
    """A request that cannot be answered must be answered as such.

    `tiles` was resolved inside `if isinstance(data, Table)`, so on a map
    whose @data is not a Table the argument was dropped: no tile_counts, no
    tile_table, no explanation -- and an invalid `tiles` value was not even
    refused on that path.
    """

    def break_data(self):
        game_map = self.project.load_map(1)
        game_map.set("data", self.project.codec.make_string("not a table"))
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="broken data")
        self.ctx = self.reopen()

    def test_asking_for_counts_says_there_is_no_table(self):
        self.break_data()
        out = self.call("map_get", map_id=1, tiles="counts")
        self.assertIn("rather than a Table",
                      " ".join(str(v) for v in out.values()))

    def test_an_invalid_tiles_value_is_still_refused(self):
        self.break_data()
        with self.assertRaises(ToolError):
            self.call("map_get", map_id=1, tiles="every-single-one")


class TestEventGetChecksThePageItWasGiven(ToolTest):
    """A page index no event has must be refused, not answered emptily.

    `page` was used only as `index != only_page`, so `page=9` returned every
    event with an empty `pages` list and a non-zero `page_count`. `event_edit`
    refuses the same value and names the range that exists, and `raw_data`
    gives -1 the opposite meaning (the last page), so silence here is the one
    reading a caller cannot check.
    """

    def test_a_negative_page_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("event_get", map_id=1, page=-1)

    def test_a_page_no_event_has_is_reported(self):
        out = self.call("event_get", map_id=1, page=9)
        self.assertIn("no event", " ".join(str(v) for v in out.values()).lower())

    def test_a_page_that_exists_still_works(self):
        out = self.call("event_get", map_id=1, page=0)
        self.assertTrue(out["events"][0]["pages"])


class TestADeletePreviewMatchesTheDelete(ToolTest):
    """The preview promised a file deletion the act would not perform.

    `_create_map`'s comment and `Project.snapshot`'s docstring both say a
    preview that does not match the act is worse than no preview; this one
    reported `would_delete: <filename>` unconditionally while the real call
    checks whether the file is there and says so when it is not.
    """

    def test_the_preview_says_there_is_no_file_to_delete(self):
        # A tree entry whose map file was never written.
        self.call("map_edit", op="create", name="Ghost", map_id=7)
        (self.tmp / "project" / "Data" / ("Map007" + self.extension)).unlink()
        self.ctx = self.reopen()
        preview = self.call("map_edit", op="delete", map_id=7, dry_run=True)
        real = self.call("map_edit", op="delete", map_id=7)
        self.assertIsNone(preview["would_delete"])
        self.assertIn("no ", preview["note"])
        self.assertIn("no ", real["note"])


class TestRawDataCanFillANilField(ToolTest):
    """A remedy a caller cannot follow is not a remedy.

    `_check_same_kind` treated a nil as "not a structure", so writing a
    properly classed object into a field that currently holds nil was refused
    with "set a field inside it instead" -- and there is no field inside nil.
    Filling one in is exactly what the escape hatch is for.
    """

    def test_an_object_can_replace_a_nil(self):
        # Set directly, because raw_data still refuses object -> nil: that
        # removes a structure rather than filling one in.
        game_map = self.project.load_map(1)
        game_map.set("bgs", None)
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="nil bgs")
        self.ctx = self.reopen()
        out = self.call("raw_data", op="set", map_id=1, path="bgs", value={
            "value": {"__class__": "RPG::BGS", "name": "Wind",
                      "volume": 80, "pitch": 100}})
        self.assertIn("after", out)
        self.assertEqual(
            self.reopen().project.load_map(1).get("bgs").get("name").data,
            b"Wind")


class TestTheShapeGateFailsClosed(unittest.TestCase):
    """A gate that cannot run must refuse, not wave the pattern through.

    `check_shape` swallowed every parser exception and returned, so anything
    the standard library's parser raised that this module had not anticipated
    silently disabled the cheap gate. It is near-unreachable today -- the same
    parser backs `re.compile`, and `_matcher` catches `re.error` -- but the one
    thing standing between a caller and an uninterruptible match should not
    fail open on a surprise.
    """

    def test_a_syntax_error_is_left_to_re_compile(self):
        """A bad pattern gets `re`'s own message, which is better than ours."""
        check_shape("(unclosed")           # must not raise here
        with self.assertRaises(ToolError):
            _matcher({"regex": True}, "(unclosed")

    def test_an_unexpected_parser_failure_is_refused(self):
        import rpgvxace_mcp.tools.pattern_safety as safety

        class Exploding:
            def parse(self, pattern):
                raise MemoryError("parser ran out of room")
            MAXREPEAT = safety._sre_parse.MAXREPEAT

        original = safety._sre_parse
        safety._sre_parse = Exploding()
        self.addCleanup(setattr, safety, "_sre_parse", original)
        with self.assertRaises(PatternTooCostly):
            check_shape("anything")


class TestSearchRefusesAnInertRegexFlag(ToolTest):
    """`regex` says how to read `query`, and a code search reads no query.

    `_matcher` returns on the falsy query before `regex` is looked at, so
    `regex: true` alongside `command_code` was silently dropped -- the one
    place in the tools layer where an argument no path reads was ignored
    rather than refused.
    """

    def test_regex_alongside_a_command_code_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("search", command_code=201, scopes=["events"], regex=True)
        self.assertIn("regex", str(caught.exception))

    def test_a_command_code_search_still_works(self):
        out = self.call("search", command_code=201, scopes=["events"])
        self.assertIn("scopes_searched", out)


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


class SharedObjectMap:
    """Builds a map whose objects share children, for the walk-bound tests.

    A mixin rather than a base class: two suites need this map and only this
    map, and a second copy of the builder would be a second thing to keep in
    step with the RPG::Event::Page field list.
    """

    # Small enough to build quickly, large enough that an unbounded walk is
    # unmistakable: 2000 x 50 x 50 is five million command visits.
    LIST_LEN, PAGES, EVENTS = 2000, 50, 50

    # Far below the 13-36 seconds measured without a bound, and far above what
    # any of the bounded walks costs.
    BUDGET_SECONDS = 10.0

    def craft_shared_map(self):
        """Rewrite map 1 so every command, page and event is one shared object."""
        game_map = self.project.load_map(1)
        command = RubyObject("RPG::EventCommand")
        command.set("code", 108)
        command.set("indent", 0)
        command.set("parameters", [self.project.codec.make_string("x")])

        page = RubyObject("RPG::Event::Page")
        page.set("condition", RubyObject("RPG::Event::Page::Condition"))
        page.set("graphic", RubyObject("RPG::Event::Page::Graphic"))
        for name, value in (("trigger", 0), ("priority_type", 0),
                            ("move_type", 0), ("move_speed", 3),
                            ("move_frequency", 3), ("walk_anime", True),
                            ("step_anime", False), ("direction_fix", False),
                            ("through", False)):
            page.set(name, value)
        page.set("list", [command] * self.LIST_LEN)

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

        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(marshal_rb.dump(game_map))
        # Small on disk is the whole point of the finding.
        self.assertLess(path.stat().st_size, 64 * 1024)
        self.ctx = self.reopen()

    def assert_bounded(self, tool_name, **arguments):
        import time
        start = time.perf_counter()
        out = self.call(tool_name, **arguments)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, self.BUDGET_SECONDS,
                        "%s took %.1fs on a 7 KB crafted map"
                        % (tool_name, elapsed))
        return out


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
        self.assertTrue(
            any("stopped" in str(v) or "not read" in str(v)
                for v in out.values()),
            "the reply does not say the walk was cut short: %s" % sorted(out))

    def test_search_stops_and_says_so(self):
        self.craft_shared_map()
        out = self.assert_bounded("search", query="zzz-nothing-matches",
                                  scopes=["events"])
        # A search that stopped early must not read as "there are none".
        self.assertTrue(out.get("may_have_more") or "stopped" in str(out),
                        "an empty result from a cut-short walk reads as "
                        "nothing found: %s" % sorted(out))

    def test_validate_stops_and_says_so(self):
        self.craft_shared_map()
        out = self.assert_bounded("validate", map_ids=[1], scopes=["events"])
        self.assertTrue(out.get("truncated") or "stopped" in str(out),
                        "validate reports a clean bill from a walk that never "
                        "finished: %s" % sorted(out))


class TestAdviceNamesOnlyWhatWillWork(ToolTest):
    """A remedy the sibling tool refuses is not a remedy.

    db_get's note offered `db_edit op="create"` for every missing id above
    zero, and db_edit refuses two whole classes of those: an id far past the
    end of the table (it would pad the table with empty slots), and any id at
    all in a table db_edit will not touch. The comment above the filter
    claimed it named "only the ids db_edit will actually accept".
    """

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


class TestTheMeasurementFailsClosed(unittest.TestCase):
    """The bound must refuse when it could not run, not pass.

    `measure_cost`'s docstring promises a child that cannot produce an answer
    is treated as a refusal. The branch for a child that died for a
    non-pattern reason assigned `verdict = None` -- a no-op on a variable
    already None -- under a comment ending "anything else is [refused]". So a
    child killed by a signal, by the OOM killer, or a `sys.executable` that is
    not an interpreter was recorded as a measured pass and cached, and the
    pattern then ran in-process where `re` cannot be interrupted.
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety
        self.addCleanup(self.safety._PASSED.clear)

    def run_with_child(self, pattern, **fields):
        import subprocess

        class Fake:
            returncode = fields.get("returncode", 0)
            stdout = fields.get("stdout", "ok")
            stderr = fields.get("stderr", "")

        original = subprocess.run
        self.addCleanup(setattr, subprocess, "run", original)
        subprocess.run = lambda *a, **k: Fake()
        self.safety.measure_cost(pattern)

    def test_a_child_killed_by_a_signal_is_refused(self):
        with self.assertRaises(PatternTooCostly) as caught:
            self.run_with_child("killed-by-signal", returncode=-9, stderr="")
        self.assertIn("safety check", str(caught.exception))

    def test_a_child_that_exits_nonzero_silently_is_refused(self):
        with self.assertRaises(PatternTooCostly):
            self.run_with_child("silent-failure", returncode=1, stderr="")

    def test_a_compile_error_is_still_left_to_re_compile(self):
        """`re` reports a syntax error better than this could.

        Signalled by the exit code the child chooses, not by its stderr. This
        test used to pass `returncode=1, stderr="re.error: ..."`, which was
        the old contract -- and that contract is what let MemoryError,
        RecursionError, ImportError and a non-interpreter `sys.executable`
        through as measured passes, because all four put "error" in stderr
        too. See TestTheMeasurementFailsClosedOnEveryAbnormalExit.
        """
        self.run_with_child("(unclosed",
                            returncode=pattern_safety.COMPILE_FAILED_EXIT,
                            stderr="")

    def test_a_child_that_merely_mentions_an_error_is_refused(self):
        """The old contract, now the case it was hiding.

        Exiting 1 with "re.error" in stderr is no longer evidence of anything:
        our child reports a compile failure by its exit code, so a 1 means
        something else went wrong and the pattern was never measured.
        """
        with self.assertRaises(PatternTooCostly):
            self.run_with_child("Potion", returncode=1,
                                stderr="re.error: something surprising")

    def test_a_clean_child_still_passes(self):
        self.run_with_child("Potion", returncode=0)


class TestEveryNamedCharacterIsMeasured(unittest.TestCase):
    """No character a pattern names may be missing from its measured subjects.

    `_subjects` selected the first eight of the alphabet by code point, so a
    pattern could name eight cheap characters and hide the expensive one
    behind them: eight control characters in a class pushed "=" out of every
    subject, the child returned in milliseconds, and the pass was cached for a
    pattern that takes over thirty seconds on a line of "=".
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety

    def measured_text(self, pattern):
        return "".join(self.safety._subjects(pattern))

    def test_a_hidden_character_still_reaches_a_subject(self):
        hide = "".join(chr(c) for c in range(1, 9))
        pattern = "[" + hide + "]?=+=+!"
        text = self.measured_text(pattern)
        self.assertIn("=", text,
                      "the character that makes this pattern expensive was "
                      "not in any measured subject")

    def test_every_character_of_a_wide_alphabet_reaches_one(self):
        letters = "".join(chr(ord("a") + i) for i in range(20))
        text = self.measured_text("[" + letters + "]+!")
        for char in letters:
            self.assertIn(char, text)

    def test_a_run_long_enough_to_backtrack_is_given_to_each(self):
        """One occurrence is not enough: competing atoms need a run."""
        text = self.measured_text("[abc]+!")
        for char in "abc":
            self.assertIn(char * 8, text)


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

        out = self.assert_bounded("event_get", common_event_id=1,
                                  include_raw=True)
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
        self.analysis = analysis
        for module in (analysis,):
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


class TestEveryNameFromJsonIsChecked(ToolTest):
    """A class or field name reaching the file must be one Ruby would accept.

    `check_ivar_name` and `check_class_name` say a name outside their shape
    "produces a file the editor cannot open". Both were called on exactly one
    of the fourteen forms `_dict_from_json` accepts -- the plain object -- so
    every other form wrote whatever it was given: a struct name, a wrapped
    class name, an extended module list, and the __ivars__ dicts on the string
    forms.
    """

    # A name Ruby cannot resolve, and one Ruby cannot parse as an ivar.
    BAD_CLASS = "not a class!"
    BAD_IVAR = "not an ivar!"

    def convert(self, value):
        return convert.from_json(value, self.project.codec)

    def refuses(self, value, what):
        with self.assertRaises(ConversionError, msg=what) as caught:
            self.convert(value)
        return str(caught.exception)

    def test_a_plain_object_is_still_checked(self):
        """The one form that was already checked, so a fix cannot lose it."""
        self.refuses({"__class__": self.BAD_CLASS}, "plain object class")
        self.refuses({"__class__": "RPG::Item", self.BAD_IVAR: 1},
                     "plain object field")

    def test_a_struct_name_is_checked(self):
        self.refuses({"__struct__": self.BAD_CLASS, "members": {}},
                     "struct name")

    def test_a_struct_member_name_is_checked(self):
        self.refuses({"__struct__": "Foo", "members": {self.BAD_IVAR: 1}},
                     "struct member")

    def test_a_class_ref_is_checked(self):
        self.refuses({"__class_ref__": self.BAD_CLASS}, "class ref")

    def test_a_uclass_name_is_checked(self):
        self.refuses({"__uclass__": self.BAD_CLASS, "inner": []},
                     "uclass name")

    def test_an_extended_module_name_is_checked(self):
        self.refuses({"__extended__": [self.BAD_CLASS], "inner": 1},
                     "extended module")

    def test_a_data_class_name_is_checked(self):
        self.refuses({"__data__": self.BAD_CLASS, "value": 1}, "data class")

    def test_a_strings_ivars_are_checked(self):
        self.refuses({"__string__": "hi", "__ivars__": {self.BAD_IVAR: 1}},
                     "string ivars")

    def test_a_byte_strings_ivars_are_checked(self):
        self.refuses({"__bytes_base64__": "aGk=",
                      "__ivars__": {self.BAD_IVAR: 1}},
                     "byte string ivars")

    def test_an_ivar_wrappers_names_are_checked(self):
        self.refuses({"__ivar_wrapper__": 1, "__ivars__": {self.BAD_IVAR: 1}},
                     "ivar wrapper")

    def test_every_good_form_still_converts(self):
        """The check must not refuse what the reader itself produces.

        Each of these is a shape `to_json` emits, so refusing one would break
        reading a project and writing it back unchanged.
        """
        for value in (
                {"__struct__": "Foo", "members": {"bar": 1}},
                {"__class_ref__": "RPG::Item"},
                {"__class_ref__": "Comparable", "kind": "module"},
                {"__uclass__": "MyArray", "inner": []},
                {"__extended__": ["Comparable"], "inner": 1},
                {"__data__": "MyData", "value": 1},
                {"__string__": "hi", "__ivars__": {"@encoding": 1}},
                {"__string__": "hi", "__ivars__": {"plain": 1}},
                {"__bytes_base64__": "aGk=", "__ivars__": {"@e": 1}},
                {"__ivar_wrapper__": 1, "__ivars__": {"@a": 2}},
                {"__class__": "RPG::Item", "name": "x"},
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(self.convert(value))

    def test_the_real_project_still_round_trips_through_json(self):
        """The strongest form of the previous test: real editor output."""
        raw = self.project.load_data("items")
        rendered = convert.to_json(raw, self.project.codec, "full",
                                   convert.new_walk())
        self.assertIsNotNone(self.convert(rendered))


class TestAnInventedFieldCannotHideInAContainer(ToolTest):
    """The check reaches objects and lists. The format has hashes too.

    `refuse_invented_in_replacement`'s docstring says a wholesale replacement
    is "compared against the value being replaced", with the only stated
    escape being that there is nothing to compare against. A RubyHash was a
    second, unstated escape: the walk recursed into RubyObject fields and into
    list elements that were RubyObjects, and returned silently for everything
    else. A map's `events` is a RubyHash keyed by event id, so
    `{"1": {"__class__": "RPG::Event", "naem": ...}}` went to disk unexamined
    -- and a list holding lists was skipped the same way.
    """

    def refuse(self, existing, replacement):
        return convert.refuse_invented_in_replacement(
            existing, replacement, "the value")

    def event_like(self, **fields):
        """An RPG::Event with the fields given, as the editor writes them."""
        obj = RubyObject("RPG::Event")
        for name, value in fields.items():
            obj.set(name, value)
        return obj

    def test_a_typo_inside_a_hash_value_is_refused(self):
        existing = RubyHash([(1, self.event_like(id=1, name="A", x=0, y=0))])
        replacement = RubyHash([(1, self.event_like(id=1, naem="A", x=0, y=0))])
        with self.assertRaises(ConversionError) as caught:
            self.refuse(existing, replacement)
        self.assertIn("naem", str(caught.exception))

    def test_a_correct_hash_value_is_allowed(self):
        existing = RubyHash([(1, self.event_like(id=1, name="A", x=0, y=0))])
        replacement = RubyHash([(1, self.event_like(id=1, name="B", x=1, y=0))])
        self.assertIsNone(self.refuse(existing, replacement))

    def test_a_hash_key_with_no_twin_is_not_judged(self):
        """The stated escape still applies: no authority, no refusal."""
        existing = RubyHash([(1, self.event_like(id=1, name="A"))])
        replacement = RubyHash([(7, self.event_like(id=7, naem="A"))])
        self.assertIsNone(self.refuse(existing, replacement))

    def test_a_typo_inside_a_nested_list_is_refused(self):
        existing = [[self.event_like(id=1, name="A")]]
        replacement = [[self.event_like(id=1, naem="A")]]
        with self.assertRaises(ConversionError) as caught:
            self.refuse(existing, replacement)
        self.assertIn("naem", str(caught.exception))

    def test_a_typo_inside_a_hash_inside_a_list_is_refused(self):
        existing = [RubyHash([(1, self.event_like(id=1, name="A"))])]
        replacement = [RubyHash([(1, self.event_like(id=1, naem="A"))])]
        with self.assertRaises(ConversionError) as caught:
            self.refuse(existing, replacement)
        self.assertIn("naem", str(caught.exception))

    def test_the_one_reachable_hash_is_closed_by_a_narrower_guard(self):
        """Why this is a latent gap and not a live hole, recorded as a test.

        The format has two hash locations: a map's `events` and the MapInfos
        file. raw_data refuses to replace `events` at all, for a reason that
        has nothing to do with invented fields -- the rest of the map has to
        agree with it -- and no patch path reaches MapInfos whole. So the hash
        branch above was covered by coincidence rather than by the rule its
        docstring states. If that narrower refusal is ever relaxed, this test
        fails and says which other layer has to hold.
        """
        broken = {"__hash__": [[1, {"__class__": "RPG::Event", "naem": "x"}]]}
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="set", map_id=1, path="events",
                      value={"value": broken}, dry_run=True)
        self.assertIn("cannot be replaced through raw_data",
                      str(caught.exception))

    def test_the_depth_limit_still_holds(self):
        """A deep structure must stop, not recurse until the stack goes."""
        existing = replacement = None
        for _ in range(convert.MAX_JSON_DEPTH + 50):
            existing = RubyHash([(1, existing)])
            replacement = RubyHash([(1, replacement)])
        self.assertIsNone(self.refuse(existing, replacement))


class TestTheMeasurementReachesItsChild(unittest.TestCase):
    """The pattern and its subjects travelled as command-line arguments.

    Windows caps a command line at 32767 characters. Base64 of ten 2000-
    character subjects is about 27000 for ASCII -- within three thousand of
    the limit -- and three times that for Japanese, where each character is
    three UTF-8 bytes. Measured: a 40-character kana class produced an 83101
    character command line.

    The consequence is not a crash. `measure_cost` fails closed, so the
    OSError became "this server could not run its safety check", and every
    Japanese pattern with a moderate character class was refused as unsafe
    when it was nothing of the kind. RPG Maker is Japanese software; a
    Japanese project is the ordinary case, not the edge one.
    """

    # Windows CreateProcess. The real cap on the whole command line.
    ARGV_LIMIT = 32767

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety
        pattern_safety._PASSED.clear()

    def command_for(self, pattern):
        """What would be spawned for this pattern, without spawning it."""
        seen = {}
        real_run = self.safety.subprocess.run

        def spy(command, **kwargs):
            seen["command"] = command
            seen["input"] = kwargs.get("input")
            return real_run(command, **kwargs)

        self.safety.subprocess.run = spy
        self.addCleanup(setattr, self.safety.subprocess, "run", real_run)
        try:
            self.safety.measure_cost(pattern)
        except self.safety.PatternTooCostly:
            pass
        return seen

    def test_a_wide_kana_class_is_measured_not_refused(self):
        kana = "".join(chr(0x3041 + i) for i in range(40))
        pattern = "[" + kana + "]+"
        # One unbounded repeat of one class: nothing competes with it, so this
        # is a cheap pattern and must be allowed.
        self.safety.check_pattern(pattern)

    def test_a_line_of_kanji_is_measured_not_refused(self):
        pattern = "".join(chr(0x4e00 + i) for i in range(170))
        self.safety.check_pattern(pattern)

    def test_the_command_line_stays_far_below_the_limit(self):
        """Whatever the pattern, the payload must not ride on argv."""
        for pattern in ("[" + "".join(chr(0x3041 + i) for i in range(40)) + "]+",
                        "[" + "".join(chr(ord("a") + i) for i in range(26)) + "]+!",
                        "".join(chr(0x4e00 + i) for i in range(170))):
            with self.subTest(pattern=pattern[:20]):
                seen = self.command_for(pattern)
                total = sum(len(str(part)) + 1 for part in seen["command"])
                self.assertLess(
                    total, self.ARGV_LIMIT // 4,
                    "the command line is %d characters, which is within reach "
                    "of the %d Windows allows" % (total, self.ARGV_LIMIT))

    def test_the_child_cannot_import_from_the_working_directory(self):
        """`python -c` puts the process's CWD first on sys.path.

        So a `re.py` or `base64.py` in the directory the server was started
        in would be imported and executed by the first regex search of the
        session -- and that directory is routinely the game project, which is
        the one thing this server's threat model treats as untrusted. `-I`
        drops it, and also ignores PYTHONPATH, sitecustomize and
        usercustomize.
        """
        seen = self.command_for("Gold[0-9]+")
        self.assertIn("-I", seen["command"],
                      "the child is spawned without import isolation, so the "
                      "server's working directory is on its sys.path")

    def test_the_child_runs_somewhere_neutral(self):
        """So the answer does not depend on how the client launched us."""
        import subprocess as sp
        import sys as _sys
        import tempfile
        done = sp.run([_sys.executable, "-I", "-c",
                       "import sys; print(sys.path[0])"],
                      capture_output=True, text=True,
                      cwd=tempfile.gettempdir())
        self.assertNotEqual(done.stdout.strip(), tempfile.gettempdir(),
                            "the working directory is still on sys.path")

    def test_the_subjects_travel_on_stdin(self):
        seen = self.command_for("Gold[0-9]+")
        self.assertIsNotNone(seen["input"],
                             "nothing was written to the child's stdin")


class TestWhatTheMeasurementRemembers(unittest.TestCase):
    """A cached verdict is a cached *timing* observation.

    Caching a pass is sound: a pattern that finished in milliseconds against
    subjects built from its own characters will do so again, and the search
    deadline bounds it in use either way. Caching a refusal is not: the only
    refusals this can produce are a timeout and a failure to spawn, both of
    which a loaded machine can cause once and not again. Cached, a single
    transient hiccup refused a legitimate pattern for the life of the server.

    And the cache had no ceiling, so a caller with many distinct patterns grew
    it without bound.
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety
        pattern_safety._PASSED.clear()
        self.addCleanup(pattern_safety._PASSED.clear)

    def spawns_for(self, run):
        """Count children spawned while `run` executes."""
        count = []
        real_run = self.safety.subprocess.run

        def spy(command, **kwargs):
            count.append(1)
            return real_run(command, **kwargs)

        self.safety.subprocess.run = spy
        self.addCleanup(setattr, self.safety.subprocess, "run", real_run)
        run()
        self.safety.subprocess.run = real_run
        return len(count)

    def test_a_pass_is_measured_once(self):
        def twice():
            self.safety.measure_cost("Gold[0-9]+")
            self.safety.measure_cost("Gold[0-9]+")
        self.assertEqual(self.spawns_for(twice), 1)

    def test_a_refusal_is_not_remembered(self):
        """So one transient timeout does not refuse a pattern for good."""
        original = self.safety.PREFLIGHT_SECONDS
        self.addCleanup(setattr, self.safety, "PREFLIGHT_SECONDS", original)
        # No time at all, so every child times out: a refusal for a reason
        # that has nothing to do with the pattern.
        self.safety.PREFLIGHT_SECONDS = 0.001
        with self.assertRaises(self.safety.PatternTooCostly):
            self.safety.measure_cost("Gold[0-9]+")

        self.safety.PREFLIGHT_SECONDS = original

        # The hiccup is over, and the pattern must get another chance -- which
        # means a child actually runs. Asserting only that the call succeeds
        # cannot tell that apart from the failure it is guarding against: a
        # remembered refusal is served by `measure_cost`'s cache hit, which
        # returns as though the pattern had passed.
        def again():
            self.safety.measure_cost("Gold[0-9]+")
        self.assertEqual(self.spawns_for(again), 1,
                         "the refused pattern was served from the cache "
                         "instead of being measured again")

    def test_the_cache_has_a_ceiling(self):
        for index in range(self.safety.MAX_MEASURED_CACHED * 3):
            self.safety._remember("pattern-%d" % index, None)
        self.assertLessEqual(len(self.safety._PASSED),
                             self.safety.MAX_MEASURED_CACHED)


class TestTheShapeGateFailsClosedThroughout(unittest.TestCase):
    """`check_shape` promises a refusal for anything it cannot analyse.

    Its own docstring says failing open "would quietly remove the cheap half
    of the protection the moment the standard library raised something
    unanticipated, and the caller would never know it had". The guard covered
    the parse and stopped there: `_flatten` and `_worst_run` ran outside it,
    and both walk a caller-shaped tree recursively.
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety

    def test_a_failure_flattening_the_tree_is_a_refusal(self):
        def boom(_parsed):
            raise ValueError("something unanticipated")
        self.addCleanup(setattr, self.safety, "_flatten", self.safety._flatten)
        self.safety._flatten = boom
        with self.assertRaises(self.safety.PatternTooCostly) as caught:
            self.safety.check_shape("Gold[0-9]+")
        self.assertIn("could not analyse", str(caught.exception))

    def test_a_recursion_error_is_a_refusal(self):
        def deep(_parsed):
            raise RecursionError("too deep")
        self.addCleanup(setattr, self.safety, "_flatten", self.safety._flatten)
        self.safety._flatten = deep
        with self.assertRaises(self.safety.PatternTooCostly):
            self.safety.check_shape("Gold[0-9]+")

    def test_a_refusal_from_the_analysis_keeps_its_own_message(self):
        """The outright refusals must not be reworded as "could not analyse"."""
        with self.assertRaises(self.safety.PatternTooCostly) as caught:
            self.safety.check_shape("(?=foo)bar")
        self.assertNotIn("could not analyse", str(caught.exception))


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


class TestADryRunDoesNotReadAsDone(ToolTest):
    """A preview must not be phrased as a confirmation.

    `db_edit dry_run=true` answered with `applied: [{"op": "update", "id": 1,
    "changes": ["name: \"Potion\" -> \"Zap\""]}]`. Every word of that is
    past tense, and the one field that says otherwise -- `write.dry_run` -- is
    nested a level down. A model reading the op rows concluded the rename had
    landed. The `would_*` names this file already uses for `would_write` and
    `would_delete` are the fix.
    """

    def dry_update(self, **extra):
        return self.call("db_edit", table="items", dry_run=True,
                         ops=[{"op": "update", "id": 1,
                               "patch": {"name": "Zap"}}], **extra)

    def test_a_preview_does_not_claim_to_have_applied_anything(self):
        out = self.dry_update()
        self.assertNotIn("applied", out,
                         "a dry run answered with `applied`: %s" % sorted(out))
        self.assertIn("would_apply", out)

    def test_a_previewed_change_is_named_in_the_conditional(self):
        row = self.dry_update()["would_apply"][0]
        self.assertNotIn("changes", row,
                         "a dry run's op row answered with `changes`: %s" % row)
        self.assertIn("would_change", row)
        self.assertIn('"Potion" -> "Zap"', str(row["would_change"]))

    def test_a_preview_says_at_the_top_that_nothing_was_written(self):
        out = self.dry_update()
        self.assertTrue(out.get("dry_run"),
                        "nothing at the top level of the reply says this was "
                        "a preview: %s" % sorted(out))

    def test_a_real_edit_still_reads_as_done(self):
        out = self.call("db_edit", table="items",
                        ops=[{"op": "update", "id": 1,
                              "patch": {"name": "Zap"}}])
        self.assertIn("applied", out)
        self.assertIn("changes", out["applied"][0])
        self.assertNotIn("would_apply", out)
        self.assertNotIn("dry_run", out)
        # And it really landed.
        self.assertEqual(
            self.reopen().project.get_entry("items", 1).get("name").data,
            b"Zap")

    def test_the_preview_and_the_act_describe_the_same_change(self):
        """Two key names, one description -- they must not drift apart."""
        preview = self.dry_update()["would_apply"][0]["would_change"]
        real = self.call("db_edit", table="items",
                         ops=[{"op": "update", "id": 1,
                               "patch": {"name": "Zap"}}])["applied"][0]["changes"]
        self.assertEqual(preview, real)


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
        self.break_every_events_file()
        out = self.call("search", query="anything", scopes=["events"])
        self.assertTrue(out.get("events_note"),
                        "nothing in the reply explains the empty result: %s"
                        % sorted(out))

    def test_a_readable_project_still_claims_it(self):
        out = self.call("search", query="Potion", scopes=["events"])
        self.assertIn("events", out["scopes_searched"])
        self.assertNotIn("events_note", out)


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
        self.assertLessEqual(rows, map_tools.MAX_EVENT_SUMMARY_ROWS)

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
        found = {}
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
                    found[match.group(1)] = str(eval(expression, {}, {}))
                except (SyntaxError, TypeError, ValueError):
                    continue
        return found

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
        self.assertGreater(len(self.ceilings()), 30)

    def test_every_ceiling_is_declared_or_named_internal(self):
        surface = self.advertised()
        undeclared = []
        for name, value in sorted(self.ceilings().items()):
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
        names = set(self.ceilings())
        gone = sorted(set(self.INTERNAL) - names)
        self.assertEqual(gone, [],
                         "INTERNAL names ceilings that no longer exist, so it "
                         "is excusing nothing and hiding the next one")


class TestBothDatabaseToolsRefuseATableTheSameWay(ToolTest):
    """One gate, so the two tools cannot answer the same question differently.

    `_table_arg` knows two things db_get's copy did not: that a table missing
    because *this engine* does not have it deserves the engine's own
    explanation, and where that explanation comes from. db_get is the odd one
    out only in accepting a one-object table like System, which is what the
    `allow_single` flag is for -- and that one difference is why the copy
    existed.
    """

    ENGINE_ONLY = "areas"          # a VX table, absent on VX Ace

    def refusal(self, tool_name, **extra):
        with self.assertRaises(ToolError) as caught:
            self.call(tool_name, **extra)
        return str(caught.exception)

    def test_a_table_this_engine_lacks_is_explained_by_both(self):
        if self.ENGINE_ONLY in self.project.data_files:
            self.skipTest("this engine has %s" % self.ENGINE_ONLY)
        hint = self.project.profile.absence_hint(self.ENGINE_ONLY)
        self.assertTrue(hint, "the profile has no hint to compare against")
        for tool_name in ("db_list", "db_get"):
            with self.subTest(tool=tool_name):
                message = self.refusal(tool_name, table=self.ENGINE_ONLY)
                self.assertIn(hint, message,
                              "%s does not explain why the table is absent, "
                              "so a caller has nothing to do next" % tool_name)

    def test_a_table_no_engine_has_names_what_this_project_has(self):
        for tool_name in ("db_list", "db_get"):
            with self.subTest(tool=tool_name):
                message = self.refusal(tool_name, table="nonesuch")
                self.assertIn("actors", message)

    def test_the_scripts_table_sends_both_to_the_scripts_tool(self):
        for tool_name in ("db_list", "db_get"):
            with self.subTest(tool=tool_name):
                self.assertIn("scripts tool",
                              self.refusal(tool_name, table="scripts"))

    def test_db_get_still_reads_a_one_object_table(self):
        """The one real difference between the two, and the reason for the flag."""
        out = self.call("db_get", table="system")
        self.assertIn("entry", out)

    def test_db_list_still_refuses_one(self):
        self.assertIn("not an id-indexed table",
                      self.refusal("db_list", table="system"))

    def test_db_get_still_refuses_ids_on_a_one_object_table(self):
        """Allowing the table must not start ignoring the ids."""
        message = self.refusal("db_get", table="system", ids=[1, 2])
        self.assertIn("not a table of entries", message)


class TestTheMeasurementFailsClosedOnEveryAbnormalExit(unittest.TestCase):
    """"error" in stderr is not "the pattern would not compile".

    `measure_cost`'s docstring promises that every way the check can fail to
    produce an answer is a refusal, and names three: a child that cannot be
    started, one that times out, and one killed by a signal or the OOM killer.
    The code decided "was this a compile error?" with

        if done.returncode != 0 and "error" not in (done.stderr or "").lower():

    -- a substring test that matches MemoryError, RecursionError, OSError,
    ImportError, and `error: unknown option -c` from a `sys.executable` that
    is not an interpreter. Every one of those skipped the refusal AND reached
    `_remember(pattern, None)`, so an unmeasured pattern was cached as a
    measured pass and then run in-process for the life of the server. That is
    the entire ReDoS bound, bypassed by a child that happens to print "error".

    Measured before the fix: 4 of 6 abnormal exits allowed and cached.
    """

    def setUp(self):
        self.safety = pattern_safety
        self.real_run = pattern_safety.subprocess.run
        self.addCleanup(setattr, pattern_safety.subprocess, "run", self.real_run)
        pattern_safety._PASSED.clear()
        self.addCleanup(pattern_safety._PASSED.clear)

    def child(self, returncode, stderr=""):
        """Make the measurement's child exit however this test needs."""
        class Done:
            def __init__(self):
                self.returncode, self.stderr, self.stdout = returncode, stderr, ""
        self.safety.subprocess.run = lambda *a, **k: Done()

    # Every abnormal exit the docstring names, and the stderr each really
    # produces. None of these is a pattern this server could measure.
    ABNORMAL = (
        ("killed by a signal", -9, ""),
        ("out of memory", 1, "MemoryError: cannot allocate"),
        ("recursion blowout", 1, "RecursionError: maximum recursion depth"),
        ("not an interpreter", 2, "error: unknown option -c"),
        ("import failure", 1, "ImportError: no module named re"),
        ("stderr in another language", 1, "Fehler: unbekannte Option"),
    )

    def test_every_abnormal_exit_is_refused(self):
        for label, returncode, stderr in self.ABNORMAL:
            with self.subTest(label):
                self.safety._PASSED.clear()
                self.child(returncode, stderr)
                with self.assertRaises(self.safety.PatternTooCostly,
                                       msg="%s was allowed" % label):
                    self.safety.measure_cost("some-pattern")

    def test_an_abnormal_exit_is_never_cached_as_a_pass(self):
        """The half that outlives the call: a cached pass skips the gate."""
        for label, returncode, stderr in self.ABNORMAL:
            with self.subTest(label):
                self.safety._PASSED.clear()
                self.child(returncode, stderr)
                try:
                    self.safety.measure_cost("some-pattern")
                except self.safety.PatternTooCostly:
                    pass
                self.assertNotIn(
                    "some-pattern", self.safety._PASSED,
                    "%s was recorded as a measured pass, so the next use of "
                    "this pattern runs unmeasured in-process" % label)

    def test_a_pattern_that_will_not_compile_is_left_to_re(self):
        """The one deliberate carve-out, decided exactly rather than guessed.

        `re.compile` reports a syntax error far better than this can, and
        `_matcher` turns that into a ToolError. So a compile failure must not
        be refused here -- but it must not be cached as a pass either.
        """
        self.child(self.safety.COMPILE_FAILED_EXIT, "re.error: bad range")
        self.safety.measure_cost("[z-a]")
        self.assertNotIn("[z-a]", self.safety._PASSED,
                         "a pattern that never ran was cached as measured")

    def test_the_carve_out_is_a_code_the_child_chooses(self):
        """Not a substring of stderr, which is not evidence of anything."""
        self.assertIsInstance(self.safety.COMPILE_FAILED_EXIT, int)
        self.assertNotIn(self.safety.COMPILE_FAILED_EXIT, (0, 1, 2),
                         "the carve-out code collides with an ordinary "
                         "interpreter exit code")

    def test_a_clean_child_still_passes(self):
        self.child(0, "")
        self.safety.measure_cost("Gold[0-9]+")
        self.assertIn("Gold[0-9]+", self.safety._PASSED)

    def test_a_real_child_still_measures_a_real_pattern(self):
        """End to end, with no fake: the fix must not break the gate."""
        self.safety.subprocess.run = self.real_run
        self.safety.check_pattern("Gold[0-9]+")
        with self.assertRaises(self.safety.PatternTooCostly):
            self.safety.check_pattern("(a+)+$")


class TestEveryDeclaredLengthLimitIsEnforced(ToolTest):
    """`query` is declared "at most 512 characters" and enforced for one mode.

    `_matcher` returns from the substring branch before the length check, and
    no maxLength is declared, so `check_args` does not catch it either: a
    megabyte-long substring query is accepted while the same string as a
    regex is refused. Two spellings of one argument, one limit, one of them
    enforced.

    `TestEveryCallerFacingCeilingIsDeclared` passes on this because 512
    appears in the description -- it checks that a ceiling is *declared*, not
    that it is *applied*, which is the gap this closes.
    """

    def test_a_long_substring_query_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("search", query="x" * 5000)
        self.assertIn("512", str(caught.exception))

    def test_a_long_regex_query_is_still_refused(self):
        with self.assertRaises(ToolError):
            self.call("search", query="x" * 5000, regex=True)

    def test_both_modes_refuse_at_the_same_length(self):
        from rpgvxace_mcp.tools.search_tools import MAX_PATTERN_CHARS
        at_limit = "x" * MAX_PATTERN_CHARS
        over = "x" * (MAX_PATTERN_CHARS + 1)
        for regex in (False, True):
            with self.subTest(regex=regex):
                self.call("search", query=at_limit, regex=regex)
                with self.assertRaises(ToolError):
                    self.call("search", query=over, regex=regex)

    def test_the_schema_declares_it_so_a_caller_can_see_it_first(self):
        """A ceiling in prose only is one the caller learns by being refused."""
        from rpgvxace_mcp.tools.search_tools import MAX_PATTERN_CHARS
        query = REGISTRY["search"].schema["properties"]["query"]
        self.assertEqual(query.get("maxLength"), MAX_PATTERN_CHARS)


class TestTheDslRefusesACoercibleId(ToolTest):
    """`{"variable": 2.9}` wrote game variable 2 and reported success.

    `registry.as_int` exists for exactly this and says so: "int(True) is 1 and
    int(2.9) is 2, so a wrong-typed id used to address a different entry than
    the caller named -- and quietly, because the tool then reported success
    against the id it had coerced to." The compiler applies `_needs_int` to
    switch, self_switch, face_index and indent, and bare `int()` to every
    variable id -- the field that selects which game variable a command
    writes.
    """

    def refuse(self, entry):
        with self.assertRaises((ToolError, CommandError)) as caught:
            self.call("event_edit", map_id=1, ops=[{
                "op": "set_commands", "event_id": 1, "page": 0,
                "commands": [entry]}], dry_run=True)
        return str(caught.exception)

    def test_a_float_variable_id_is_refused(self):
        self.refuse({"variable": 2.9, "op": "add", "value": 1})

    def test_a_boolean_variable_id_is_refused(self):
        self.refuse({"variable": True, "op": "add", "value": 1})

    def test_a_string_variable_id_is_refused(self):
        self.refuse({"variable": "3", "op": "add", "value": 1})

    def test_a_float_variable_end_is_refused(self):
        self.refuse({"variable": 1, "variable_end": 3.7, "op": "add",
                     "value": 1})

    def test_a_float_from_variable_is_refused(self):
        self.refuse({"variable": 1, "op": "add", "from_variable": 2.9})

    def test_a_malformed_random_range_is_refused(self):
        """`low, high = entry["random"]` unpacked whatever it was given."""
        self.refuse({"variable": 1, "op": "set", "random": [1]})
        self.refuse({"variable": 1, "op": "set", "random": 5})

    def test_an_honest_variable_command_still_compiles(self):
        out = self.call("event_edit", map_id=1, dry_run=True, ops=[{
            "op": "set_commands", "event_id": 1, "page": 0,
            "commands": [{"variable": 2, "op": "add", "value": 1}]}])
        self.assertTrue(out)


class TestAChoiceBranchIsNeverDropped(ToolTest):
    """Extra `branches` were compiled away and reported as a rewrite.

    DSL_HELP says only that "branches" pairs with the choices by position, so
    a caller with three branches and two choices was told "rewrote page 0 of
    event 1" and lost the third. That is the rule the whole `check_op_args`
    gate exists to enforce -- a dropped argument reports the same success as
    an applied one -- one level below where that gate reaches.
    """

    def compile_choices(self, choices, branches, **extra):
        entry = {"choices": choices, "branches": branches}
        entry.update(extra)
        return self.call("event_edit", map_id=1, dry_run=True, ops=[{
            "op": "set_commands", "event_id": 1, "page": 0,
            "commands": [entry]}])

    def test_more_branches_than_choices_is_refused(self):
        with self.assertRaises((ToolError, CommandError)) as caught:
            self.compile_choices(["Yes", "No"], [[], [], []])
        message = str(caught.exception)
        self.assertIn("3", message)
        self.assertIn("2", message)

    def test_matching_branches_still_compile(self):
        out = self.compile_choices(
            ["Yes", "No"],
            [[{"comment": "y"}], [{"comment": "n"}]])
        self.assertTrue(out)

    def test_fewer_branches_than_choices_is_allowed(self):
        """A choice with an empty body is a real thing to author."""
        out = self.compile_choices(["Yes", "No"], [[{"comment": "y"}]])
        self.assertTrue(out)


class EmptyPageMap:
    """A map whose amplification is entirely in (events x pages).

    Every page holds an empty @list, so every charge of the form
    `spend(len(commands))` is free. One page condition flag is set so the page
    is not "unconditional", which is what keeps `_check_shadowing` walking and
    producing nothing -- a finding ceiling cannot bound a walk that finds
    nothing. The graphic is left empty for the same reason in the asset walk:
    `_AssetChecker.check` returns before it counts anything.
    """

    # 4,000,000 pairs from ~18 KB. Large enough that an unbounded walk is
    # unmistakable, small enough to build in a test.
    PAGES, EVENTS = 2000, 2000

    BUDGET_SECONDS = 10.0

    def craft_empty_page_map(self):
        page = RubyObject("RPG::Event::Page")
        condition = RubyObject("RPG::Event::Page::Condition")
        # Truthy, so the page is not unconditional and the shadowing rule
        # keeps looking without ever collecting.
        condition.set("switch1_valid", True)
        condition.set("switch1_id", 1)
        page.set("condition", condition)
        # Empty, so the asset checker returns before it collects.
        page.set("graphic", RubyObject("RPG::Event::Page::Graphic"))
        for name, value in (("trigger", 0), ("priority_type", 0),
                            ("move_type", 0), ("move_speed", 3),
                            ("move_frequency", 3), ("walk_anime", True),
                            ("step_anime", False), ("direction_fix", False),
                            ("through", False)):
            page.set(name, value)
        page.set("list", [])          # the bypass

        event = RubyObject("RPG::Event")
        event.set("id", 1)
        event.set("name", self.project.codec.make_string("E"))
        event.set("x", 0)
        event.set("y", 0)
        event.set("pages", [page] * self.PAGES)

        events = RubyHash()
        for event_id in range(1, self.EVENTS + 1):
            events.set(event_id, event)
        game_map = self.project.load_map(1)
        game_map.set("events", events)
        path = self.project.path_for(self.project.map_file(1))
        path.write_bytes(marshal_rb.dump(game_map))
        self.assertLess(path.stat().st_size, 128 * 1024,
                        "the point of the finding is that it is small")
        self.ctx = self.reopen()

    def assert_bounded(self, tool_name, **arguments):
        import time
        start = time.perf_counter()
        out = self.call(tool_name, **arguments)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, self.BUDGET_SECONDS,
                        "%s took %.1fs on a map of empty pages"
                        % (tool_name, elapsed))
        return out


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
        self.craft_empty_page_map()
        out = self.assert_bounded("event_get", map_id=1)
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
                             map_tools.MAX_EVENT_SUMMARY_ROWS)
        self.assertIn("events_truncated", out)

    def test_every_row_counts_toward_the_summary_ceiling(self):
        """Events as well as pages, against one ceiling."""
        self.craft_empty_page_map()
        out = self.assert_bounded("map_get", map_id=1, include_events=True)
        rows = len(out["events"]) + sum(len(e["pages"]) for e in out["events"])
        self.assertLessEqual(rows, map_tools.MAX_EVENT_SUMMARY_ROWS + 1)

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
    """`reference_preview` made a fresh budget per op, so MAX_OPS multiplied it.

    `validate` says why this is wrong in its own comment -- "One allowance for
    every command walk this call makes" -- and `db_get` says it again for its
    conversion budget. A db_edit carrying 200 deletes got 200 separate
    200,000-step allowances.
    """

    def test_a_batch_shares_one_allowance(self):
        from rpgvxace_mcp import analysis
        seen = []
        original = analysis.WalkBudget

        class Counted(original):
            def __init__(self, *a, **k):
                seen.append(1)
                super().__init__(*a, **k)

        analysis.WalkBudget = Counted
        self.addCleanup(setattr, analysis, "WalkBudget", original)
        ids = [entry_id for entry_id, _ in self.project.entries("items")][:4]
        self.assertGreater(len(ids), 1, "the fixture needs several items")
        self.call("db_edit", table="items", dry_run=True,
                  ops=[{"op": "delete", "id": i} for i in ids])
        self.assertLessEqual(len(seen), 1,
                             "a %d-delete batch allocated %d separate walk "
                             "allowances" % (len(ids), len(seen)))


class TestEveryConversionBranchIsCallable(unittest.TestCase):
    """A branch that raises TypeError when reached is not a handled case.

    `_to_json_value` handles a bare `bytes` by calling
    `_string_to_json(RubyString(bytes(value)), codec)` -- two arguments to a
    function that takes four. Nothing in the reader produces a bare `bytes`
    today, so the line has never run; it reads as a handled case and is a
    crash waiting for the first caller that makes one.
    """

    def test_a_bare_bytes_value_converts(self):
        codec = convert.TextCodec("utf-8")
        rendered = convert.to_json(b"hello", codec, "full", convert.new_walk())
        self.assertIn("hello", str(rendered))

    def test_a_bytearray_value_converts(self):
        codec = convert.TextCodec("utf-8")
        rendered = convert.to_json(bytearray(b"hi"), codec, "full",
                                   convert.new_walk())
        self.assertIn("hi", str(rendered))

    def test_it_agrees_with_the_ruby_string_branch(self):
        """A bare bytes and the RubyString the reader would have built."""
        codec = convert.TextCodec("utf-8")
        from rpgvxace_mcp.rubytypes import RubyString
        bare = convert.to_json(b"hello", codec, "full", convert.new_walk())
        wrapped = convert.to_json(RubyString(b"hello"), codec, "full",
                                  convert.new_walk())
        self.assertEqual(bare, wrapped)


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
        for event_id in range(1, map_tools.MAX_EVENT_SUMMARY_ROWS * 3):
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
                             map_tools.MAX_EVENT_SUMMARY_ROWS,
                             "%d event rows came back against a ceiling of %d"
                             % (len(out["events"]),
                                map_tools.MAX_EVENT_SUMMARY_ROWS))

    def test_and_says_that_it_stopped(self):
        self.craft()
        out = self.call("map_get", map_id=1, include_events=True)
        self.assertIn("events_truncated", out)

    def test_event_get_is_bounded_the_same_way(self):
        self.craft()
        out = self.call("event_get", map_id=1)
        self.assertLess(len(out["events"]),
                        map_tools.MAX_EVENT_SUMMARY_ROWS * 3)


class TestEveryPreviewReadsAsAPreview(ToolTest):
    """A dry run says so at the top level, in the conditional, everywhere.

    One call per writing tool, chosen to reach the branch that actually
    builds a reply -- not the argument checks in front of it.
    """

    def setUp(self):
        super().setUp()
        # A slot that exists, for the scripts case.
        self.slot = self.call("scripts", op="list")["scripts"][0]["index"]

    def previews(self):
        """One dry-run call per tool that writes, as (tool name, reply)."""
        return [
            ("db_edit", dict(table="items", dry_run=True,
                             ops=[{"op": "update", "id": 1,
                                   "patch": {"name": "Zap"}}])),
            ("event_edit", dict(map_id=1, dry_run=True,
                                ops=[{"op": "update", "event_id": 1,
                                      "name": "Renamed"}])),
            ("map_edit", dict(op="update", map_id=1, dry_run=True,
                              name="Renamed")),
            ("map_tiles", dict(op="fill", map_id=1, x=0, y=0, width=2,
                               height=2, z=0, tile_id=0, dry_run=True)),
            ("scripts", dict(op="set", index=self.slot, name="Renamed",
                             dry_run=True)),
            ("raw_data", dict(op="set", table="system", path="game_title",
                              value={"value": "Renamed"}, dry_run=True)),
            ("backups", dict(op="create", label="preview", dry_run=True)),
        ]

    # Words that mean "this happened". A preview must not use them at the top
    # level of its reply or in the rows it carries.
    PAST_TENSE = ("applied", "changes", "changed", "renamed_to", "deleted",
                  "removed", "restored", "created", "after")

    def test_every_writing_tool_is_covered(self):
        """So a tool added later cannot quietly skip this rule."""
        writing = {name for name, tool in REGISTRY.items()
                   if getattr(tool, "writes", False)}
        covered = {name for name, _ in self.previews()}
        self.assertEqual(sorted(writing - covered), [],
                         "these tools write and are not checked here")

    def test_every_preview_says_so_at_the_top_level(self):
        for name, arguments in self.previews():
            with self.subTest(tool=name):
                out = self.call(name, **arguments)
                self.assertTrue(
                    out.get("dry_run"),
                    "%s's preview does not say at the top level that nothing "
                    "was written: %s" % (name, sorted(out)))

    def test_no_preview_uses_a_past_tense_key(self):
        for name, arguments in self.previews():
            with self.subTest(tool=name):
                out = self.call(name, **arguments)
                found = [key for key in out if key in self.PAST_TENSE]
                self.assertEqual(
                    found, [],
                    "%s's preview reports %s, which reads as a completed "
                    "write" % (name, found))

    def test_no_row_inside_a_preview_uses_one_either(self):
        """Where a model actually reads what happened."""
        for name, arguments in self.previews():
            with self.subTest(tool=name):
                out = self.call(name, **arguments)
                for key, value in out.items():
                    if not isinstance(value, list):
                        continue
                    for row in value:
                        if not isinstance(row, dict):
                            continue
                        found = [k for k in row if k in self.PAST_TENSE]
                        self.assertEqual(
                            found, [],
                            "a row under %s[%r] reports %s"
                            % (name, key, found))

    def test_a_preview_still_says_what_would_change(self):
        """Renaming the keys must not remove the information."""
        out = self.call("db_edit", table="items", dry_run=True,
                        ops=[{"op": "update", "id": 1,
                              "patch": {"name": "Zap"}}])
        self.assertIn('"Potion" -> "Zap"', str(out))

    def test_nothing_reached_the_disk(self):
        """The claim the whole shape exists to make."""
        for name, arguments in self.previews():
            with self.subTest(tool=name):
                before = self.data_bytes("Items")
                self.call(name, **arguments)
                self.assertEqual(self.data_bytes("Items"), before)

    def test_a_real_write_still_reads_as_done(self):
        out = self.call("db_edit", table="items",
                        ops=[{"op": "update", "id": 1,
                              "patch": {"name": "Zap"}}])
        self.assertNotIn("dry_run", out)
        self.assertIn("applied", out)
        self.assertIn("changes", out["applied"][0])


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
        counts = map_tools._tile_counts(data)
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
        counts = map_tools._tile_counts(data)
        top = {row["tile_id"]: row["count"]
               for row in counts["0"]["most_common"]}
        self.assertEqual(top.get(4242), 2)

    def test_every_layer_is_reported(self):
        data = self.project.load_map(1).get("data")
        counts = map_tools._tile_counts(data)
        self.assertEqual(sorted(counts), sorted(str(z) for z in range(data.zsize)))
