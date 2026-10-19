"""Guards on the arguments a call may carry.

An argument that is silently dropped reports the same success as one that
was applied, so the caller will not retry what it believes already
happened. That is the whole subject of this file: every gate that turns a
dropped argument into a refusal, and every id that is refused rather than
coerced -- `int(2.9)` is 2, and an id addresses a different thing.
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

from rpgvxace_mcp.commands import CommandError  # noqa: E402
from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.schema import (  # noqa: E402
    PROFILES,
    new_event_command,
)
from rpgvxace_mcp.rubytypes import RubyHash, RubyObject, Table  # noqa: E402
from rpgvxace_mcp.project import (  # noqa: E402
    Project,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
    check_op_args,
    led_by_op,
)

# The tool modules this file names directly. Registration -- the
# side effect of importing them -- is `tool_case`'s job; these are
# here because the tests below read their op tables.
from rpgvxace_mcp.tools import (  # noqa: E402
    db_tools,
    event_tools,
    live_tools,
    map_edit_tools,
    map_paint_tools,
    map_tile_tools,
    misc_tools,
    raw_tools,
    reference_tools,
    undo_tools,
)

from tool_case import (  # noqa: E402
    ToolTest,
)


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
                ("map_edit", None, map_edit_tools.MAP_OP_ARGS)):
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
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="set", table="scripts", path="0/2",
                      value={"value": "puts 1"})
        # This refusal and not another (a bad path, a bad value).
        self.assertIn("scripts tool", str(caught.exception))


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
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "add_page", "event_id": 1, "commands": []}])
        # The empty list, and not some other rule the op might trip.
        self.assertIn("empty commands list", str(caught.exception))

    def test_create_refuses_it(self):
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "create", "name": "New", "x": 1, "y": 1,
                 "commands": []}])
        self.assertIn("empty commands list", str(caught.exception))

    def test_the_page_still_holds_what_it_did(self):
        before = len(self.project.load_map(1).get("events").get(1)
                     .get("pages")[0].get("list"))
        with self.assertRaises(ToolError):
            self.call("event_edit", map_id=1, ops=[
                {"op": "set_page", "event_id": 1, "page": 0, "commands": []}])
        after = len(self.reopen().project.load_map(1).get("events").get(1)
                    .get("pages")[0].get("list"))
        self.assertEqual(before, after)


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
        ("map_edit", None): map_edit_tools.MAP_OP_ARGS,
        ("map_tiles", None): map_tile_tools.TILE_OP_ARGS,
        ("map_paint", None): map_paint_tools.PAINT_OP_ARGS,
        ("refactor", None): reference_tools.REFACTOR_OP_ARGS,
        ("db_edit", "ops"): db_tools.DB_OP_ARGS,
        ("event_edit", "ops"): event_tools.EVENT_OP_ARGS,
        ("raw_data", None): raw_tools.RAW_OP_ARGS,
        ("scripts", None): misc_tools.SCRIPT_OP_ARGS,
        ("backups", None): misc_tools.BACKUP_OP_ARGS,
        ("live", None): live_tools.LIVE_OP_ARGS,
        ("undo", None): undo_tools.UNDO_OP_ARGS,
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
                 event_tools.EVENT_OP_ARGS),
                ("map_paint", map_paint_tools._PLANNERS,
                 map_paint_tools.PAINT_OP_ARGS)):
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
        ("map_edit", None): map_edit_tools.MAP_OP_ARGS,
        ("map_tiles", None): map_tile_tools.TILE_OP_ARGS,
        ("map_paint", None): map_paint_tools.PAINT_OP_ARGS,
        ("refactor", None): reference_tools.REFACTOR_OP_ARGS,
        ("db_edit", "ops"): db_tools.DB_OP_ARGS,
        ("event_edit", "ops"): event_tools.EVENT_OP_ARGS,
        ("raw_data", None): raw_tools.RAW_OP_ARGS,
        ("scripts", None): misc_tools.SCRIPT_OP_ARGS,
        ("backups", None): misc_tools.BACKUP_OP_ARGS,
        ("live", None): live_tools.LIVE_OP_ARGS,
        ("undo", None): undo_tools.UNDO_OP_ARGS,
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

    def test_the_remedy_fits_the_call_site(self):
        """Three call sites shared one sentence, and it fitted one of them.

        "Ask for one or the other" is the answer when a caller gave two
        alternatives and can only have one -- `command_reference code=101
        search="text"`. It is not the answer for `event_get
        common_event_id=1 page=0`, where `event_ids`, `name_contains` and
        `page` are not alternatives to anything: there is no other thing to
        ask for instead, and the caller simply has to drop them.

        A refusal whose remedy is wrong is worse than one with no remedy.
        The model does what it says, is refused again, and has been told
        twice that the fix is a choice it does not have.
        """
        with self.assertRaises(ToolError) as caught:
            self.call("event_get", common_event_id=1, page=0)
        message = str(caught.exception)
        self.assertNotIn("one or the other", message)
        self.assertIn("Drop", message)

        with self.assertRaises(ToolError) as caught:
            self.call("command_reference", code=101, search="text")
        self.assertIn("one or the other", str(caught.exception),
                      "the site the sentence did fit lost it")

    def test_every_inert_refusal_names_its_own_remedy(self):
        """Structural, so a fourth call site cannot inherit a wrong one.

        Over the whole tools layer, not one module. It read `event_tools.py`
        by name, and when `command_reference` moved to
        `command_docs_tools.py` two of the three call sites went with it --
        which the count below caught, and which is why the count is there.
        """
        tools = (Path(__file__).resolve().parent.parent / "src"
                 / "rpgvxace_mcp" / "tools")
        calls = []
        for path in sorted(tools.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            calls += [(path.name, node) for node in ast.walk(tree)
                      if isinstance(node, ast.Call)
                      and getattr(node.func, "id", "") == "refuse_inert"]
        self.assertGreaterEqual(len(calls), 3,
                                "found %d call sites, so this checked "
                                "almost nothing" % len(calls))
        for where, call in calls:
            given = {kw.arg for kw in call.keywords}
            with self.subTest(module=where, line=call.lineno):
                self.assertIn(
                    "remedy", given,
                    "%s:%d takes the default remedy, and there is no default "
                    "that is right for every branch" % (where, call.lineno))

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

    def test_branches_that_are_not_a_list_are_refused(self):
        """The refusal was written after the coercion that made it dead.

        `branches = as_list(entry.get("branches"))` answers `[]` for anything
        that is not a list, so the `isinstance` test below it could never be
        true and `{"choices": ["a"], "branches": "nope"}` compiled as though
        no branches had been asked for -- reporting the same success as a
        page that got the branches it named. Same defect as this class's own,
        one argument over.
        """
        for given in ("nope", {"0": [{"comment": "y"}]}, 3):
            with self.subTest(branches=given):
                with self.assertRaises((ToolError, CommandError)) as caught:
                    self.compile_choices(["Yes", "No"], given)
                self.assertIn("branches", str(caught.exception))

    def test_a_coercible_cancel_is_refused(self):
        """The one bare `int()` left in the compilers.

        `int(2.9)` is 2 and `int(True)` is 1, so a wrong-typed cancel
        selected a different choice than the caller named and the reply
        said the page had been rewritten -- the defect `_compile_variable`
        and `_compile_transfer` both carry a paragraph about. `{"cancel":
        "x"}` escaped as a bare ValueError rather than a CommandError, so
        the caller was told an argument "was not the shape the tool
        expected" without being told which.
        """
        for given in (2.9, True, "1", None):
            with self.subTest(cancel=given):
                with self.assertRaises((ToolError, CommandError)) as caught:
                    self.compile_choices(["Yes", "No"], [[], []],
                                         cancel=given)
                self.assertIn("cancel", str(caught.exception))

    def test_a_cancel_outside_the_engines_range_is_refused(self):
        """0 disallows cancelling, 1..4 picks a choice, 5 is a branch.

        The comment above the parameter has said so all along; nothing
        checked it, so `{"cancel": 99}` wrote a value the interpreter has
        no branch for and reported success.
        """
        for given in (-1, 6, 99):
            with self.subTest(cancel=given):
                with self.assertRaises((ToolError, CommandError)):
                    self.compile_choices(["Yes", "No"], [[], []],
                                         cancel=given)

    def test_every_cancel_the_engine_takes_still_compiles(self):
        for given in (0, 1, 2, 3, 4, 5):
            with self.subTest(cancel=given):
                self.assertTrue(self.compile_choices(
                    ["Yes", "No"], [[], []], cancel=given))

    def test_no_branches_at_all_is_still_allowed(self):
        """Absent is not malformed: Show Choices with no bodies is legal."""
        out = self.call("event_edit", map_id=1, dry_run=True, ops=[{
            "op": "set_commands", "event_id": 1, "page": 0,
            "commands": [{"choices": ["Yes", "No"]}]}])
        self.assertTrue(out)


class TestTwoSpellingsOfOneArgumentAgree(ToolTest):
    """`db_get id=N` and `db_get ids=[N]` are the same request.

    `id` declares and enforces `minimum=0, maximum=MAX_ENTRY_ID`; `ids`
    declared `items: {"type": "integer"}` with no bounds and enforced only a
    minimum. So `db_get ids=[999999999]` was accepted and `db_get
    id=999999999` refused -- one request, two spellings, two answers about
    what is a legal id. `event_get`'s `event_ids` already shows the correct
    pattern: declared item bounds matching the `int_list` call.
    """

    HUGE = 999999999

    def test_both_spellings_answer_a_high_id_the_same_way(self):
        """Agreement is the property; which verdict is a separate question.

        They now agree on answering rather than refusing, because reading an
        id that is not there is a legitimate question and `missing_ids` plus a
        note on whether db_edit could create it is more use than an error.
        MAX_ENTRY_ID is the ceiling on what this server will *create*.
        """
        one = self.call("db_get", table="items", id=self.HUGE)
        many = self.call("db_get", table="items", ids=[self.HUGE])
        self.assertEqual(one["missing_ids"], [self.HUGE])
        self.assertEqual(one["missing_ids"], many["missing_ids"])

    def test_both_spellings_accept_a_legal_id(self):
        one = self.call("db_get", table="items", id=1)
        many = self.call("db_get", table="items", ids=[1])
        self.assertEqual(one["entries"], many["entries"])

    def test_the_schema_declares_the_same_bound_on_both(self):
        """A caller reads the schema before calling, not the source."""
        properties = REGISTRY["db_get"].schema["properties"]
        self.assertEqual(properties["ids"]["items"].get("minimum"),
                         properties["id"].get("minimum"))
        self.assertEqual(properties["ids"]["items"].get("maximum"),
                         properties["id"].get("maximum"),
                         "one spelling declares a ceiling the other does not, "
                         "so the same request is legal or not depending on "
                         "how it is written")

    def test_a_high_id_still_gets_advice_about_creating_it(self):
        """The reason the looser bound is the right agreement.

        db_get tells a caller which missing ids db_edit could fill in. With a
        maximum on both spellings that advice was unreachable, because the
        request never got past the argument check.
        """
        out = self.call("db_get", table="items", ids=[1, 500000])
        self.assertIn("500000", str(out["missing_ids"]))
        self.assertIn("500000", out["note"])


class TestTheNegativeIndexConventionIsDeclared(ToolTest):
    """`-1` means "the last one" in raw_data and is refused by both event tools.

    `event_get`'s own source says so -- "`event_edit` refuses the same value
    while `raw_data` gives -1 the opposite meaning, so silence here was the
    one reading a caller could not check" -- and then neither event tool's
    description mentions it. A model that learned `-1` from `raw_data` tries
    it on `event_edit` and gets "event 3 has pages 0, 1, not -1", a refusal
    that never says the convention differs by tool.
    """

    def descriptions_of(self, tool_name, argument):
        return REGISTRY[tool_name].schema["properties"][argument]["description"]

    def test_raw_data_still_counts_from_the_end(self):
        out = self.call("raw_data", table="system", path="party_members/-1")
        self.assertIn("value", out)

    def test_both_event_tools_still_refuse_it(self):
        with self.assertRaises(ToolError):
            self.call("event_get", map_id=1, page=-1)
        with self.assertRaises(ToolError):
            self.call("event_edit", map_id=1, dry_run=True, ops=[
                {"op": "set_page", "event_id": 1, "page": -1, "patch": {}}])

    def test_event_get_says_the_convention_does_not_apply(self):
        text = self.descriptions_of("event_get", "page")
        self.assertIn("-1", text,
                      "the description does not mention the value a caller "
                      "will try after reading raw_data's: %r" % text)

    def test_event_edit_says_it_too(self):
        page = REGISTRY["event_edit"].schema["properties"]["ops"]["items"][
            "properties"]["page"]["description"]
        self.assertIn("-1", page,
                      "the description does not mention it: %r" % page)

    def test_raw_data_still_documents_its_own(self):
        """The tool that does support it must keep saying so."""
        self.assertIn("-1", self.descriptions_of("raw_data", "path"))


class TestAMapEditResolvesEachArgumentOnce(ToolTest):
    """The value that was checked is the value that gets written.

    Counted rather than reasoned about: two `opt_dict(args, "patch")` calls
    in one request means two objects, and they agree only because the readers
    happen to be pure. That is a property of the readers, not of this code,
    and the refusal that runs on one of them is what guards a file the editor
    would refuse to open.
    """

    def resolutions_of(self, argument, **arguments):
        """How many times one argument is read while a request is served."""
        from rpgvxace_mcp.tools import map_edit_tools, registry
        seen = []

        def spy(args, key, *rest, **kwargs):
            if key == argument:
                seen.append(key)
            return original(args, key, *rest, **kwargs)

        target = "opt_dict" if argument == "patch" else "opt_str"
        original = getattr(registry, target)
        self.addCleanup(setattr, map_edit_tools, target,
                        getattr(map_edit_tools, target))
        setattr(map_edit_tools, target, spy)
        self.call("map_edit", **arguments)
        return len(seen)

    def test_a_patch_is_read_once(self):
        count = self.resolutions_of(
            "patch", op="update", map_id=1, dry_run=True,
            patch={"encounter_step": 9})
        self.assertEqual(count, 1,
                         "the patch was read %d times, so the object "
                         "_refuse_structural checked is not necessarily the "
                         "object apply_patch writes" % count)

    def test_a_name_is_read_once(self):
        count = self.resolutions_of("name", op="update", map_id=1,
                                    dry_run=True, name="Renamed")
        self.assertEqual(count, 1,
                         "the name was read %d times, so the value that "
                         "proved a tree entry exists is not necessarily the "
                         "value written into it" % count)

    def test_a_resize_still_reports_what_it_changed(self):
        out = self.call("map_edit", op="resize", map_id=1, width=25,
                        height=20, dry_run=True)
        self.assertIn("resized from 20x15 to 25x20", out["would_change"])

    def test_a_rename_still_needs_a_tree_entry(self):
        """The check that has to happen before anything is written."""
        infos = self.project.load_data("map_infos")
        infos.delete(1)
        self.project.save_file(self.project.filename_for("map_infos"),
                               tool="test", summary="drop the entry")
        self.ctx = self.reopen()
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="update", map_id=1, name="Renamed")
        self.assertIn("MapInfos", str(caught.exception))

    def test_a_structural_patch_is_still_refused(self):
        with self.assertRaises(ToolError):
            self.call("map_edit", op="update", map_id=1,
                      patch={"width": 99}, dry_run=True)

    def test_a_rename_and_a_patch_together_still_work(self):
        out = self.call("map_edit", op="update", map_id=1, name="Both",
                        patch={"encounter_step": 7}, dry_run=True)
        text = str(out)
        self.assertIn("Both", text)
        self.assertIn("encounter_step", text)


class TestAnOpDispatcherIsNamedForWhatItDoes(unittest.TestCase):
    """`_op_set_commands` also appended, and branched on `op` to decide."""

    def test_the_write_dispatcher_says_write(self):
        from rpgvxace_mcp.tools import event_tools
        self.assertTrue(hasattr(event_tools, "_op_write_commands"),
                        "the function handling both set and append is still "
                        "named for only one of them")
        self.assertFalse(hasattr(event_tools, "_op_set_commands"))

    def test_both_ops_still_dispatch_to_it(self):
        """The two ops whose shared handler the old name described as one."""
        from rpgvxace_mcp.tools import event_tools
        for op in ("set_commands", "append_commands"):
            self.assertIs(event_tools._EVENT_OPS[op],
                          event_tools._op_write_commands,
                          "%s no longer reaches the writer" % op)


class TestARefusalComesBeforeTheWork(ToolTest):
    """raw_data refused the scripts table after unmarshalling the whole file.

    The refusal is unconditional for that table, so the read could never be
    used -- and `map_edit_tools` fixes this same ordering, with a comment
    saying the check "used to run after the tree entry had been added to the
    cache".
    """

    def test_the_scripts_table_is_refused_without_being_read(self):
        loaded = []
        original = self.project.load_data

        def spy(key, refresh=False):
            loaded.append(key)
            return original(key, refresh)

        self.project.load_data = spy
        self.addCleanup(setattr, self.project, "load_data", original)

        with self.assertRaises(ToolError):
            self.call("raw_data", table="scripts", path="0")
        self.assertNotIn("scripts", loaded,
                         "the file was unmarshalled before the refusal that "
                         "always follows it")
        # The control: the same spy sees an ordinary table being read, so its
        # silence about `scripts` is not a spy that watches nothing.
        self.call("raw_data", table="system", path="game_title")
        self.assertIn("system", loaded)

    def test_an_ordinary_table_is_still_read(self):
        out = self.call("raw_data", table="system", path="game_title")
        self.assertIn("value", out)


class TestADeclaredTypeIsChecked(ToolTest):
    """`check_args` says it holds a call to the schema its tool declares.

    It enforced unknown keys, maxItems and integer bounds, and not `type`, so
    a string sent for a declared integer fell through to the handler's own
    accessor -- which reports it, but as a different problem, from a different
    layer, in different words depending on which accessor got there first.
    """

    def test_a_string_for_an_integer_is_refused_by_the_gate(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_get", map_id="1")
        message = str(caught.exception)
        self.assertIn("map_id", message)
        self.assertIn("integer", message.lower())

    def test_an_integer_for_a_string_is_refused_too(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_list", table=7)
        self.assertIn("table", str(caught.exception))

    def test_a_boolean_is_not_an_integer(self):
        """`True` is an int in Python and is not one in a schema."""
        with self.assertRaises(ToolError):
            self.call("map_get", map_id=True)

    def test_an_integral_float_is_still_accepted(self):
        """The house rule: 2.0 is a whole number a caller may send.

        `as_int` exists for this, and every tool already accepts it, so the
        type check must not start refusing what the readers accept.
        """
        out = self.call("map_get", map_id=1.0)
        self.assertEqual(out["map_id"], 1)

    def test_the_right_types_still_pass(self):
        self.assertIn("maps", self.call("map_list"))
        self.assertIn("entries", self.call("db_list", table="items"))


class TestEveryIdInTheDslRefusesACoercibleValue(ToolTest):
    """`_compile_variable`'s docstring states the rule for one function only.

    It says "Every id here goes through `_needs_int`, not `int()`. `int(True)`
    is 1 and `int(2.9)` is 2, so a wrong-typed id addressed a different game
    variable" -- and four ids that select *what to edit* still went through
    bare `int()`. One of them sits in the same return statement as a
    `_needs_int` call, so `{"variable": 3, "from_variable": 2.9}` compared
    against variable 2 and answered "rewrote page 0 of event 1".
    """

    def compile_refused(self, entry, field):
        """A DSL entry the compiler must refuse rather than coerce.

        Through the real `ops` shape. An earlier draft of this suite passed
        `op=` and `commands=` at the top level, which `check_args` refuses as
        unknown arguments -- so every case in it was satisfied by a spelling
        mistake rather than by the rule, which is the vacuity these suites
        exist to prevent.
        """
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, ops=[{
                "op": "set_commands", "event_id": 2, "page": 0,
                "commands": [entry]}])
        message = str(caught.exception)
        self.assertNotIn("unknown argument", message,
                         "refused for the wrong reason: %s" % message)
        # And for the field under test: a missing event or page, or an empty
        # `then`, would also be a refusal.
        self.assertIn(field, message,
                      "refused, but not over %r: %s" % (field, message))
        return message

    def test_a_call_common_event_id_is_not_coerced(self):
        self.compile_refused({"common_event": 2.9}, "common_event")

    def test_a_call_common_event_id_is_not_a_boolean(self):
        self.compile_refused({"common_event": True}, "common_event")

    def test_a_transfer_destination_map_is_not_coerced(self):
        self.compile_refused({"transfer": {"map_id": 2.9, "x": 1, "y": 1}},
                             "map_id")

    def test_a_transfer_destination_x_is_not_coerced(self):
        self.compile_refused({"transfer": {"map_id": 1, "x": 2.9, "y": 1}},
                             "x")

    def test_a_transfer_destination_y_is_not_coerced(self):
        self.compile_refused({"transfer": {"map_id": 1, "x": 1, "y": 2.9}},
                             "y")

    def test_a_condition_operand_variable_is_not_coerced(self):
        """The one in the same statement as a `_needs_int` call."""
        self.compile_refused(
            {"if": {"variable": 3, "from_variable": 2.9}, "then": []},
            "from_variable")

    def test_an_ordinary_transfer_still_compiles(self):
        out = self.call("event_edit", map_id=1, ops=[{
            "op": "set_commands", "event_id": 2, "page": 0,
            "commands": [{"transfer": {"map_id": 1, "x": 2, "y": 3}}]}])
        self.assertIn("applied", out)

    def test_every_id_in_the_compiler_goes_through_the_checker(self):
        """The rule, not one case of it: no bare `int(` on a subscript.

        `int(x)` on a value that came out of the caller's request is the
        defect; `int()` on something this module computed is not. The scan
        looks for the shape the four defects had -- a subscript or a `.get`
        of the entry dict passed straight to `int` -- so a new one written
        the same way is caught without anybody remembering the rule.
        """
        source = (Path(__file__).resolve().parent.parent / "src"
                  / "rpgvxace_mcp" / "commands.py").read_text(encoding="utf-8")
        bad = []
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "int" and node.args):
                continue
            argument = node.args[0]
            reads_request = isinstance(argument, ast.Subscript) or (
                isinstance(argument, ast.Call)
                and isinstance(argument.func, ast.Attribute)
                and argument.func.attr == "get")
            if reads_request:
                bad.append(ast.unparse(node))
        self.assertEqual(bad, [],
                         "these coerce a caller's value instead of refusing "
                         "it, so a wrong-typed id addresses a different "
                         "thing and the reply says it worked")


class TestAnInertScopeIsSaidRatherThanRefused(ToolTest):
    """Two arguments no path reads, answered two different ways.

    A review proposed refusing a scope named beside `events` on a
    `command_code` search, the way `regex` is refused. The two are not the
    same case: `scopes_searched` names exactly which scopes were read, so a
    caller passing the full list is *told* database was skipped, while a
    dropped `regex` left no trace in the reply at all. This suite holds the
    distinction, so the next review reads the answer instead of proposing it
    a third time.
    """

    def test_a_scope_that_holds_no_commands_is_reported_not_refused(self):
        out = self.call("search", command_code=101,
                        scopes=["events", "database"])
        self.assertEqual(out["scopes_searched"], ["events"])

    def test_a_dropped_regex_has_no_such_trace_so_it_is_refused(self):
        """The asymmetry, asserted rather than described."""
        with self.assertRaises(ToolError) as caught:
            self.call("search", command_code=101, regex=True)
        self.assertIn("regex", str(caught.exception))
        out = self.call("search", command_code=101)
        self.assertNotIn("regex", out,
                         "if the reply named the flag, refusing it would be "
                         "the second answer to an answered question")

    def test_a_command_code_with_no_events_scope_is_still_refused(self):
        """Nothing would be searched at all, which the reply cannot fix."""
        with self.assertRaises(ToolError) as caught:
            self.call("search", command_code=101, scopes=["database"])
        self.assertIn("command_code", str(caught.exception))

    def test_the_default_scopes_are_left_alone(self):
        out = self.call("search", command_code=101)
        self.assertEqual(out["scopes_searched"], ["events"])


class TestACraftedCommonEventIsReportedNotCrashed(ToolTest):
    """`event_get` read the slot straight; `event_edit` refuses it by name.

    A CommonEvents file holding an Array or a String where an
    RPG::CommonEvent belongs made the read path raise AttributeError, which
    `server._tool_error` reports with the hint "an argument was not the shape
    the tool expected" -- a diagnosis about the caller for a fact about the
    file.
    """

    def craft_common_event(self, value):
        commons = self.project.load_data("common_events")
        commons[1] = value
        self.project.save_file(self.project.filename_for("common_events"),
                               tool="test", summary="a crafted slot")
        self.ctx = self.reopen()

    def test_a_string_in_a_common_event_slot_is_reported(self):
        self.craft_common_event(self.project.codec.make_string("not an event"))
        with self.assertRaises(ToolError) as caught:
            self.call("event_get", common_event_id=1)
        self.assertIn("common event 1", str(caught.exception).lower())
        self.assertNotIn("argument", str(caught.exception).lower())

    def test_an_array_in_a_common_event_slot_is_reported(self):
        self.craft_common_event([1, 2, 3])
        with self.assertRaises(ToolError) as caught:
            self.call("event_get", common_event_id=1)
        self.assertIn("common event 1", str(caught.exception).lower())

    def test_a_real_common_event_still_reads(self):
        out = self.call("event_get", common_event_id=1)
        self.assertIn("script", out)


class TestADeclaredCharacterCeilingIsEnforced(ToolTest):
    """`_check_declared` held maxItems and ignored maxLength.

    So a declared string ceiling was documentation rather than a gate, and
    the one property that declares one enforced it a second time by hand.
    """

    def test_a_string_past_its_declared_ceiling_is_refused(self):
        from rpgvxace_mcp.tools.search_tools import MAX_PATTERN_CHARS
        with self.assertRaises(ToolError) as caught:
            self.call("search", query="a" * (MAX_PATTERN_CHARS + 1),
                      regex=True)
        self.assertIn("characters", str(caught.exception))

    def test_the_gate_itself_enforces_it(self):
        """Asked of `check_args`, not of the tool that also checks by hand."""
        from rpgvxace_mcp.tools.registry import ToolError as TE, check_args
        schema_def = {"properties": {"q": {"type": "string", "maxLength": 4}}}
        with self.assertRaises(TE) as caught:
            check_args(schema_def, {"q": "abcde"}, "this tool")
        self.assertIn("q takes at most 4 characters", str(caught.exception))

    def test_a_string_within_it_is_accepted(self):
        from rpgvxace_mcp.tools.registry import check_args
        check_args({"properties": {"q": {"type": "string", "maxLength": 4}}},
                   {"q": "abcd"}, "this tool")


class TestAnArrayFieldThatIsNotAnArrayIsReadSafely(ToolTest):
    """`or []` looks like `_seq` and is a different rule.

    `RubyHash` defines `__len__` and neither `__iter__` nor `__getitem__`,
    so an *empty* hash is falsy and becomes `[]` while a *non-empty* one is
    truthy and reaches the loop. Measured before this: a Hash of one pair
    where a command's @parameters belongs made `event_get` answer
    "'RubyHash' object is not subscriptable" and `search` "not iterable",
    which `server._tool_error` reports as "an argument was not the shape
    the tool expected" -- a diagnosis about the caller for a fact about the
    file, which is exactly what `as_list`'s docstring says it prevents.

    `validate` answered correctly throughout, because it read the same
    field through `_seq`. One rule, two spellings, and only one of them
    was the rule.
    """

    def craft(self, value):
        """Put `value` where a command's @parameters belongs."""
        game_map = self.project.load_map(1)
        page = game_map.get("events").get(1).get("pages")[0]
        # By hand, not through `new_event_command`: that constructor does
        # `list(parameters or [])`, which coerces the very thing this test
        # crafts. A RubyHash where an Array belongs is the hostile shape
        # under test, so it has to reach the file uncoerced.
        # By hand, not through `new_event_command`: that constructor does
        # `list(parameters or [])`, which coerces the very thing this test
        # crafts. A RubyHash where an Array belongs is the hostile shape
        # under test, so it has to reach the file uncoerced.
        command = RubyObject("RPG::EventCommand")
        command.set("code", 401)
        command.set("indent", 0)
        command.set("parameters", value)
        page.set("list", [command] + list(page.get("list")))
        self.project.path_for(self.project.map_file(1)).write_bytes(
            marshal_rb.dump(game_map))
        self.ctx = self.reopen()

    def non_empty_hash(self):
        """Non-empty, which is what makes it truthy and reach the loop."""
        pairs = RubyHash()
        pairs.set(0, self.project.codec.make_string("hello"))
        return pairs

    def test_event_get_reads_a_hash_where_an_array_belongs(self):
        self.craft(self.non_empty_hash())
        out = self.call("event_get", map_id=1)
        self.assertIn("events", out)

    def test_search_reads_it_too(self):
        self.craft(self.non_empty_hash())
        out = self.call("search", query="hello", scopes=["events"])
        self.assertIn("hits", out)

    def test_validate_still_reports_the_map(self):
        self.craft(self.non_empty_hash())
        out = self.call("validate", scopes=["events"])
        self.assertIn("findings", out)

    def test_a_scalar_where_an_array_belongs_is_read_too(self):
        """The shape `or []` does handle, so it must keep working."""
        self.craft(7)
        self.assertIn("events", self.call("event_get", map_id=1))

    def test_no_module_spells_the_rule_as_or_empty_list(self):
        """The rule, not the three cases: no `or []` on a file read.

        `x.get(...) or []` is the shape; `required or []` on a schema
        argument is not, because that value came from this server.
        """
        root = (Path(__file__).resolve().parent.parent / "src"
                / "rpgvxace_mcp")
        spelled = []
        for path in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.BoolOp)
                        and isinstance(node.op, ast.Or)
                        and len(node.values) == 2):
                    continue
                left, right = node.values
                if not (isinstance(right, ast.List) and not right.elts):
                    continue
                # A `.get(...)` on the left is a read of a file's field.
                if (isinstance(left, ast.Call)
                        and isinstance(left.func, ast.Attribute)
                        and left.func.attr == "get"):
                    spelled.append("%s: %s" % (path.name,
                                               ast.unparse(node)))
        self.assertEqual(spelled, [],
                         "these read a file's field with `or []`, which is "
                         "not the same rule as `as_list`: "
                         + "; ".join(spelled))


class TestEveryDeclaredOpLeadsItsReply(unittest.TestCase):
    """`led_by_op` over every op in every schema, and both directions.

    The reply shaping was four lines inside `Tool.run`, so the only evidence
    it worked was `TestOneVocabularyForAPartialAnswer`, which calls the real
    handlers -- and that reaches an op only if it can be sampled read-only
    or as a dry run. Of the ops the tools declare, the sample covers eight;
    the ones it cannot reach are the ones that write, whose replies are
    exactly what a caller most needs to read.

    So the shaping is its own function and it is fed every declared op.
    Nothing here calls a handler, so nothing here writes.
    """

    def declared_ops(self):
        """Each (tool, op) the schemas advertise, top level and per batch.

        `db_edit` and `event_edit` declare theirs inside `ops[].op` rather
        than at the top level -- twelve of the twenty-eight, and the twelve
        that write. A version of this that read only the top level found
        sixteen and would have called that complete.
        """
        out = []
        for name, tool in sorted(REGISTRY.items()):
            properties = tool.schema.get("properties") or {}
            item = (properties.get("ops") or {}).get("items") or {}
            for spec in (properties.get("op") or {},
                         (item.get("properties") or {}).get("op") or {}):
                for op in spec.get("enum") or ():
                    out.append((name, op))
        return out

    def test_the_schemas_declare_ops_at_all(self):
        """So the tests below cannot pass by finding nothing."""
        found = self.declared_ops()
        self.assertGreater(len(found), 25,
                           "only %d declared ops found" % len(found))
        self.assertIn(("event_edit", "set_page"), found,
                      "the batch tools' ops were not read")

    def test_every_declared_op_leads_its_reply(self):
        for name, op in self.declared_ops():
            with self.subTest(tool=name, op=op):
                shaped = led_by_op({"result": 1}, op)
                self.assertEqual(list(shaped)[0], "op")
                self.assertEqual(shaped["op"], op)
                self.assertEqual(shaped["result"], 1)

    def test_a_dry_run_comes_before_the_op_and_keeps_its_value(self):
        for name, op in self.declared_ops():
            for given in (True, False):
                with self.subTest(tool=name, op=op, dry_run=given):
                    shaped = led_by_op({"dry_run": given, "result": 1}, op)
                    self.assertEqual(list(shaped)[:2], ["dry_run", "op"])
                    self.assertIs(shaped["dry_run"], given)

    def test_a_handler_that_set_the_op_itself_is_overridden(self):
        """Five of `map_edit`'s reply sites did, and put it second or third."""
        shaped = led_by_op({"result": 1, "op": "whatever"}, "create")
        self.assertEqual(list(shaped)[0], "op")
        self.assertEqual(shaped["op"], "create")

    def test_a_reply_that_is_not_a_dict_is_left_alone(self):
        self.assertEqual(led_by_op([1, 2], "create"), [1, 2])
        self.assertEqual(led_by_op("text", "create"), "text")

    def test_no_op_means_no_key(self):
        """A tool that does not select with an op says nothing about one."""
        self.assertEqual(led_by_op({"result": 1}, None), {"result": 1})

    def test_the_sampled_replies_name_only_declared_ops(self):
        """The other direction: the sample cannot drift off the schemas.

        A `("map_edit", {"op": "resze"})` in that list would exercise the
        refusal path and read as covering the op it misspells -- which is
        the mistake that made six coercion cases pass on a typo in an
        earlier round.
        """
        source = (Path(__file__).resolve().parent
                  / "test_guards_replies.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        sampler = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "replies")
        declared = set(self.declared_ops())
        sampled = []
        for node in ast.walk(sampler):
            if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
                continue
            tool, call = node.elts
            if not (isinstance(tool, ast.Constant)
                    and isinstance(tool.value, str)
                    and isinstance(call, ast.Call)):
                continue
            for keyword in call.keywords:
                if (keyword.arg == "op"
                        and isinstance(keyword.value, ast.Constant)):
                    sampled.append((tool.value, keyword.value.value))
        self.assertTrue(sampled, "read no sampled ops, so this checked none")
        self.assertEqual(sorted(set(sampled) - declared), [],
                         "these are sampled and no schema declares them")


class TestEveryDeclaredTypeIsInTheTable(unittest.TestCase):
    """`_check_declared` enforces every type the schemas actually declare.

    It was four `elif declared == X and not isinstance(...)` branches, and
    the comment above the table that replaced them said a fifth declared
    type "was enforced only if someone remembered to add a fifth branch".
    That is still true of the table -- a table is not self-completing --
    so this is the direction that matters: a type a schema declares and the
    table does not hold is a type nothing checks, and the argument would
    read as closed while the hole stayed open.

    `INT` is deliberately outside the table: it delegates to `as_int`,
    which refuses a bool and a non-integral float in words of its own.
    """

    def declared(self):
        """Every `"type"` any registered tool's schema names, at any depth."""
        found = set()

        def walk(node):
            if isinstance(node, dict):
                kind = node.get("type")
                if isinstance(kind, str):
                    found.add(kind)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for tool in REGISTRY.values():
            walk(tool.schema)
        self.assertGreater(len(found), 3, "read almost no declared types")
        return found

    def test_every_declared_type_is_enforced(self):
        from rpgvxace_mcp.tools.registry import INT, _DECLARED_TYPES
        held = set(_DECLARED_TYPES) | {INT}
        # "object" appears as the schema envelope's own type rather than as
        # an argument's, and an argument declared OBJ is in the table.
        missing = sorted(self.declared() - held - {"object"})
        self.assertEqual(
            missing, [],
            "these types are declared by a tool schema and "
            "`_check_declared` holds none of them, so nothing checks "
            "them: " + ", ".join(missing))

    def test_nothing_in_the_table_is_undeclared(self):
        """The other direction: an entry for a type nothing uses is dead."""
        from rpgvxace_mcp.tools.registry import _DECLARED_TYPES
        idle = sorted(set(_DECLARED_TYPES) - self.declared())
        self.assertEqual(idle, [],
                         "the table holds these and no schema declares "
                         "them: " + ", ".join(idle))

    def test_each_entry_refuses_the_wrong_shape(self):
        """So an entry cannot be a name with no enforcement behind it."""
        from rpgvxace_mcp.tools.registry import (
            STR, ToolError, _check_declared,
        )
        wrong = {str: 1, bool: "yes", list: {}, dict: []}
        from rpgvxace_mcp.tools.registry import _DECLARED_TYPES
        for declared, (kind, said) in _DECLARED_TYPES.items():
            with self.subTest(declared=declared):
                with self.assertRaises(ToolError) as caught:
                    _check_declared("k", wrong[kind], {"type": declared})
                self.assertIn(said, str(caught.exception))
        self.assertIn(STR, _DECLARED_TYPES)
