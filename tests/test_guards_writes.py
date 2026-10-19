"""Guards on writing: staging, staleness, containment, and previews.

A half-applied edit and a preview that does not match the act are the two
failures here a caller cannot detect. Staging makes a write all-or-nothing
per file; the staleness digest refuses to overwrite a file the editor
changed underneath us; containment keeps every path inside the project; and
`write_reply` makes a preview and its act one sentence.
"""

import ast
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
    ProjectError,
    ReadOnlyError,
    StaleFileError,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyString,
    Table,
)
from rpgvxace_mcp.schema import (  # noqa: E402
    STRUCTURAL_MAP_FIELDS,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
)

# The tool modules this file names directly. Registration -- the
# side effect of importing them -- is `tool_case`'s job; these are
# here because the tests below read their op tables.
from rpgvxace_mcp.tools import (  # noqa: E402
    map_edit_tools,
    raw_tools,
)

from tool_case import (  # noqa: E402
    ToolTest,
)


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
        """The editor numbers regions 0 to 63. Layer 3 stores one as 256
        times its number, so 2816 -- which this used to send raw and expect
        refused -- is region 11 and legal; the region view's ceiling is
        where 63 is enforced now."""
        if not self.ace:
            self.skipTest("layer 3 holds region ids on VX Ace only")
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, op="fill", x=0, y=0, width=1,
                      height=1, view="region", tile_id=64)
        self.assertIn("0 to 63", str(caught.exception))

    def test_a_shadow_mask_beyond_four_bits_is_refused(self):
        if not self.ace:
            self.skipTest("layer 3 holds shadow bits on VX Ace only")
        with self.assertRaises(ToolError) as caught:
            self.call("map_tiles", map_id=1, op="fill", x=0, y=0, width=1,
                      height=1, view="shadow", tile_id=16)
        self.assertIn("0 to 15", str(caught.exception))

    def test_painting_a_region_keeps_the_shadow_and_back(self):
        """A region and the shadow under it share one word of layer 3."""
        if not self.ace:
            self.skipTest("layer 3 is the packed layer on VX Ace only")
        window = dict(map_id=1, x=0, y=0, width=2, height=1)
        self.call("map_tiles", op="write", z=3, rows=[[5, 15]], **window)
        self.call("map_tiles", op="fill", view="region", tile_id=7, **window)
        self.assertEqual(self.call("map_tiles", z=3, **window)["rows"],
                         [[(7 << 8) | 5, (7 << 8) | 15]])
        self.call("map_tiles", op="fill", view="shadow", tile_id=0, **window)
        self.assertEqual(self.call("map_tiles", z=3, **window)["rows"],
                         [[7 << 8, 7 << 8]])

    def test_a_raw_word_with_bits_no_field_holds_is_refused(self):
        """Layer 3 holds region * 256 + shadow and nothing else: bits 4-7
        and 14-15 are read by nothing in RGSS3 and no real map sets them, so
        a raw value using them is a mistake (a tile id sent to the wrong
        layer, say) rather than something to store."""
        if not self.ace:
            self.skipTest("layer 3 is the packed layer on VX Ace only")
        for bad in (16, 0x80, 64 << 8, 0xFFFF):
            with self.subTest(value=bad):
                with self.assertRaises(ToolError) as caught:
                    self.call("map_tiles", map_id=1, op="fill", x=0, y=0,
                              width=1, height=1, z=3, tile_id=bad)
                self.assertIn("region * 256 + shadow", str(caught.exception))

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
        """The two lists had drifted, which is what opened the hole.

        Every guard reads the schema's own tuple: the same object, not an equal
        one, so a module that gained a shorter local copy of its own fails.
        (Comparing `raw_tools`' name with the schema's was a tautology -- it is
        an import of it -- and never looked at `map_edit_tools`, the other
        guard.)
        """
        from rpgvxace_mcp.tools import map_edit_tools
        self.assertIs(raw_tools.STRUCTURAL_MAP_FIELDS, STRUCTURAL_MAP_FIELDS)
        self.assertIs(map_edit_tools.STRUCTURAL_MAP_FIELDS,
                      STRUCTURAL_MAP_FIELDS)
        # And what is in it, from outside the code: the four fields a map's
        # other fields have to agree with.
        self.assertEqual(set(STRUCTURAL_MAP_FIELDS),
                         {"width", "height", "data", "events"})


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
                        map_edit_tools._cells_outside(table, width, height),
                        expected,
                        "%dx%dx%d cropped to %dx%d" % (xs, ys, zs, width, height))


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
        self.assertEqual(preview["would_copy"], real["files"])

    def test_the_preview_matches_an_ordinary_snapshot(self):
        preview = self.call("backups", op="create", dry_run=True)
        real = self.call("backups", op="create")
        self.assertEqual(preview["would_copy"], real["files"])

    def test_a_dry_run_writes_nothing(self):
        before = self.call("backups", op="list")["backups"]
        self.call("backups", op="create", dry_run=True)
        self.assertEqual(self.call("backups", op="list")["backups"], before)


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


class TestEveryPreviewReadsAsAPreview(ToolTest):
    """A dry run says so at the top level, in the conditional, everywhere.

    One call per writing **op**, not per writing tool. The per-tool version
    of the completeness test exercised `map_edit` with `op="update"` alone,
    so `op="create"` went on returning a past-tense `changes` and
    `op="delete"` went on hand-rolling its whole reply -- the two paths that
    `PREVIEW_NAMES`' own comment names as its reason for existing. A rule
    checked per tool cannot see a branch.
    """

    # Ops of a writing tool that write nothing, so they build no preview.
    # Each needs a reason, because this is the escape hatch from the
    # completeness rule below and an escape hatch without a reason is where
    # unchecked branches come back. Every name here is checked against the
    # tool's declared ops, so a renamed op cannot leave a stale excuse.
    READ_ONLY_OPS = {
        "backups op=list": "lists the snapshots that exist",
        "map_tiles op=read": "reads a rectangle of tile ids",
        "raw_data op=get": "reads one value by path",
        "scripts op=list": "lists the script slots",
        "scripts op=get": "reads one slot's code",
        # `live` changes a running scratch copy of the game, never a project
        # file, so there is no write to preview. Each op still needs its own
        # line: an excuse names one op, so a new op is not excused by these.
        "live op=start": "copies the project to a scratch folder and runs it",
        "live op=stop": "ends the scratch game and deletes the copy",
        "live op=status": "reports on the scratch game",
        "live op=state": "reads the running game's state",
        "live op=input": "presses keys in the scratch game",
        "live op=step": "runs the scratch game for some frames",
        "live op=pause": "pauses the scratch game",
        "live op=resume": "lets the scratch game run",
        "live op=set": "changes the running game's switches and variables",
        "live op=teleport": "moves the player in the running game",
        "live op=screenshot": "captures the scratch game's window",
        "live op=eval": "runs Ruby in the scratch game",
        # `event_template` writes only through op=create, which has its own
        # preview below. The other three never reach a file: list and
        # describe read the built-in table, and preview is create with the
        # write switched off.
        "event_template op=list": "lists the built-in templates",
        "event_template op=describe": "describes one built-in template",
        "event_template op=preview": "shows what create would write",
    }

    def setUp(self):
        super().setUp()
        # A slot that exists, for the scripts case, and a snapshot to
        # restore from.
        self.slot = self.call("scripts", op="list")["scripts"][0]["index"]
        self.backup = self.call("backups", op="create",
                                label="fixture")["backup"]

    def previews(self):
        """One dry-run call per writing op, as (label, tool, arguments).

        The label is `tool op=...`, or the tool's name for a tool that takes
        no op, which is what the completeness test compares against the ops
        each tool declares.
        """
        return [
            ("db_edit op=update", "db_edit",
             dict(table="items", dry_run=True,
                  ops=[{"op": "update", "id": 1, "patch": {"name": "Zap"}}])),
            ("db_edit op=create", "db_edit",
             dict(table="items", dry_run=True,
                  ops=[{"op": "create", "clone_from": 1}])),
            ("db_edit op=delete", "db_edit",
             dict(table="items", dry_run=True,
                  ops=[{"op": "delete", "id": 1}])),
            ("event_template op=create", "event_template",
             dict(op="create", template="sign", map_id=1, x=1, y=1,
                  parameters={"lines": ["Hi"]}, dry_run=True)),
            ("event_edit op=update", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "update", "event_id": 1, "name": "Renamed"}])),
            ("event_edit op=create", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "create", "name": "New", "x": 1, "y": 1}])),
            ("event_edit op=delete", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "delete", "event_id": 1}])),
            ("event_edit op=move", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "move", "event_id": 1, "x": 2, "y": 2}])),
            ("event_edit op=set_page", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "set_page", "event_id": 1, "page": 0,
                        "commands": [{"comment": "hi"}]}])),
            ("event_edit op=add_page", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "add_page", "event_id": 1,
                        "commands": [{"comment": "hi"}]}])),
            ("event_edit op=delete_page", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "delete_page", "event_id": 1, "page": 0}])),
            ("event_edit op=set_commands", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "set_commands", "event_id": 1, "page": 0,
                        "commands": [{"comment": "hi"}]}])),
            ("event_edit op=append_commands", "event_edit",
             dict(map_id=1, dry_run=True,
                  ops=[{"op": "append_commands", "event_id": 1, "page": 0,
                        "commands": [{"comment": "hi"}]}])),
            ("map_edit op=update", "map_edit",
             dict(op="update", map_id=1, dry_run=True, name="Renamed")),
            ("map_edit op=resize", "map_edit",
             dict(op="resize", map_id=1, width=25, height=20, dry_run=True)),
            ("map_edit op=create", "map_edit",
             dict(op="create", name="New", width=20, height=15,
                  dry_run=True)),
            ("map_edit op=delete", "map_edit",
             dict(op="delete", map_id=2, dry_run=True)),
            ("map_tiles op=fill", "map_tiles",
             dict(op="fill", map_id=1, x=0, y=0, width=2, height=2, z=0,
                  tile_id=0, dry_run=True)),
            ("map_tiles op=write", "map_tiles",
             dict(op="write", map_id=1, x=0, y=0, z=0, width=2, height=2,
                  rows=[[0, 0], [0, 0]], dry_run=True)),
            ("map_paint op=fill", "map_paint",
             dict(op="fill", map_id=1, kind="A2:1", x=0, y=0, width=2,
                  height=2, dry_run=True)),
            ("map_paint op=paint", "map_paint",
             dict(op="paint", map_id=1, kind="A2:1", cells=[[1, 1]],
                  dry_run=True)),
            ("map_paint op=border", "map_paint",
             dict(op="border", map_id=1, kind="A2:1", x=0, y=0, width=3,
                  height=3, dry_run=True)),
            ("map_paint op=region", "map_paint",
             dict(op="region", map_id=1, region=3, x=0, y=0, width=2,
                  height=2, dry_run=True)),
            ("map_paint op=room", "map_paint",
             dict(op="room", map_id=2, floor="A2:0", wall="A4:8", x=3, y=4,
                  width=4, height=3, dry_run=True)),
            ("map_paint op=corridor", "map_paint",
             dict(op="corridor", map_id=2, floor="A2:0", wall="A4:8", x=1,
                  y=6, to_x=9, to_y=6, dry_run=True)),
            ("map_paint op=dungeon", "map_paint",
             dict(op="dungeon", map_id=2, floor="A2:0", wall="A4:8",
                  room_min=2, room_max=3, rooms=2, dry_run=True)),
            ("scripts op=set", "scripts",
             dict(op="set", index=self.slot, name="Renamed", dry_run=True)),
            ("raw_data op=set", "raw_data",
             dict(op="set", table="system", path="game_title",
                  value={"value": "Renamed"}, dry_run=True)),
            ("backups op=create", "backups",
             dict(op="create", label="preview", dry_run=True)),
            ("backups op=prune", "backups", dict(op="prune", dry_run=True)),
            ("backups op=restore", "backups",
             dict(op="restore", backup=self.backup, dry_run=True)),
        ]

    # Words that mean "this happened". A preview must not use them at the top
    # level of its reply or in the rows it carries.
    PAST_TENSE = ("applied", "changes", "changed", "renamed_to", "deleted",
                  "removed", "restored", "created", "after", "writes",
                  "cloned_from")

    # Ops whose real call cannot be run straight after its own preview, so
    # the key-set comparison skips them. A reason each, for the same reason
    # the read-only list carries one.
    # Keys an act may add that a preview cannot: each names something
    # that exists only because the write happened. A reason each, because
    # this is the escape hatch from the symmetry rule.
    ACT_ONLY_KEYS = {
        # The saved-file record. A preview has no bytes written and no
        # backup path, and `write_reply` renames its preview counterpart.
        "write",
        # The snapshot a restore takes of what it is about to overwrite.
        "safety_backup",
    }

    NOT_REPLAYABLE = {
        # Restoring overwrites the data files the rest of this call reads.
        "backups op=restore",
        # Pruning depends on how many snapshots exist, which the preview of
        # `op="create"` above has already changed.
        "backups op=prune",
    }

    def declared_ops(self):
        """Every `tool op=...` label the advertised schema declares.

        From the schema rather than from a list here, so it cannot drift
        from what the tools accept. `db_edit` and `event_edit` declare their
        ops inside `ops[].op` rather than at the top level, which is exactly
        where the two tools with the most branches keep them.
        """
        labels = set()
        for name, tool in REGISTRY.items():
            if not getattr(tool, "writes", False):
                continue
            props = tool.schema.get("properties") or {}
            spec = props.get("op") or {}
            if not spec.get("enum"):
                items = (props.get("ops") or {}).get("items") or {}
                spec = (items.get("properties") or {}).get("op") or {}
            ops = spec.get("enum")
            if not ops:
                labels.add(name)
                continue
            labels.update("%s op=%s" % (name, op) for op in ops)
        return labels

    def test_every_writing_op_is_covered(self):
        """Per op, from the schema, so a new branch cannot skip the rule."""
        required = self.declared_ops() - set(self.READ_ONLY_OPS)
        covered = {label for label, _, _ in self.previews()}
        self.assertEqual(
            sorted(required - covered), [],
            "these writing ops build a reply that nothing here checks")
        self.assertEqual(
            sorted(covered - required), [],
            "these entries name an op no writing tool declares, or one the "
            "read-only list already excuses")

    def test_no_read_only_excuse_outlives_its_op(self):
        """An op that was renamed away must not leave its excuse behind."""
        unknown = sorted(set(self.READ_ONLY_OPS) - self.declared_ops())
        self.assertEqual(unknown, [],
                         "these ops are excused from the preview rule and no "
                         "longer exist")

    def test_every_preview_says_so_at_the_top_level(self):
        for label, name, arguments in self.previews():
            with self.subTest(op=label):
                out = self.call(name, **arguments)
                self.assertTrue(
                    out.get("dry_run"),
                    "%s's preview does not say at the top level that nothing "
                    "was written: %s" % (label, sorted(out)))

    def test_every_preview_says_so_first(self):
        """`dry_run` last is `dry_run` after everything a model has read.

        `backups op="prune"` put it at the end of its reply. A model reading
        keys in order has taken `removed` for a completed removal by then,
        which is the whole reason `write_reply` builds the dict with
        `dry_run` first.
        """
        for label, name, arguments in self.previews():
            with self.subTest(op=label):
                out = self.call(name, **arguments)
                self.assertEqual(
                    next(iter(out)), "dry_run",
                    "%s's preview leads with %r rather than saying that "
                    "nothing happened" % (label, next(iter(out))))

    def test_no_preview_uses_a_past_tense_key(self):
        for label, name, arguments in self.previews():
            with self.subTest(op=label):
                out = self.call(name, **arguments)
                found = [key for key in out if key in self.PAST_TENSE]
                self.assertEqual(
                    found, [],
                    "%s's preview reports %s, which reads as a completed "
                    "write" % (label, found))

    def test_a_preview_and_the_act_answer_the_same_keys(self):
        """A key set that depends on the branch is a preview you cannot read.

        `_delete_map` emitted `incoming_transfers` and its `_truncated`
        sibling unconditionally on the preview and only `if incoming` on the
        real call, so previewing the deletion of an unreferenced map
        promised two keys the act would omit. Compared after the rename, so
        `would_delete` and `deleted` count as the same key.

        Each pair runs against its own copy of the project. Sharing one
        copy makes the suite order-dependent -- `op="delete"` really does
        delete the event the `move` entry names -- and an order-dependent
        assertion is one that can pass for the wrong reason.
        """
        from rpgvxace_mcp.tools.registry import PREVIEW_NAMES

        def keys(reply):
            """Both replies in the *preview* vocabulary.

            Renaming forward rather than inverting the table: two act-side
            keys can share one preview name -- `changes` and `changed` both
            become `would_change` -- so an inverse map answers whichever of
            them iterated last and compares the wrong pair.
            """
            return {PREVIEW_NAMES.get(k, k) for k in reply if k != "dry_run"}

        for label, name, arguments in self.previews():
            if label in self.NOT_REPLAYABLE:
                continue
            with self.subTest(op=label):
                self.setUp()
                preview = self.call(name, **arguments)
                acted = self.call(name, **dict(arguments, dry_run=False))
                self.assertEqual(
                    sorted(keys(preview) - keys(acted)), [],
                    "%s previews keys the act does not answer" % label)
                # And the other direction, which was unchecked: an act
                # carrying keys the preview never mentioned is the same
                # defect read backwards, and `_delete_map`'s act really did
                # answer `warnings` where its preview said nothing.
                self.assertEqual(
                    sorted(keys(acted) - keys(preview)
                           - self.ACT_ONLY_KEYS), [],
                    "%s answers keys its preview never mentioned" % label)

    def test_no_row_inside_a_preview_uses_one_either(self):
        """Where a model actually reads what happened."""
        for label, name, arguments in self.previews():
            with self.subTest(op=label):
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
        import hashlib

        def disk_state():
            # Every file under the project, and the folders' listing: a dry
            # run that wrote Map001, Scripts, a snapshot or the change log
            # was invisible to a comparison of Items alone.
            root = self.project.root
            return {str(path.relative_to(root)): (
                        hashlib.sha1(path.read_bytes()).hexdigest()
                        if path.is_file() else "dir")
                    for path in sorted(root.rglob("*"))}

        seen = 0
        for label, name, arguments in self.previews():
            with self.subTest(op=label):
                before = disk_state()
                self.call(name, **arguments)
                after = disk_state()
                self.assertEqual(
                    after, before,
                    "%s wrote to the project on a dry run: %s" % (
                        label, sorted(set(after.items()) ^ set(before.items()))))
                seen += 1
        self.assertGreater(seen, 3, "almost no previews were compared")

    def test_a_real_write_still_reads_as_done(self):
        out = self.call("db_edit", table="items",
                        ops=[{"op": "update", "id": 1,
                              "patch": {"name": "Zap"}}])
        self.assertNotIn("dry_run", out)
        self.assertIn("applied", out)
        self.assertIn("changes", out["applied"][0])


class TestAnAssetAddedMidSessionIsSeen(ToolTest):
    """validate says a graphic is missing; the developer adds it; validate agrees.

    The asset listing is cached per folder for the life of the process, and
    nothing invalidated it, so the second validate reported the same missing
    graphic as the first -- and would have gone on doing so until the server
    was restarted. `Project.forget_assets` fixes it and had no caller; every
    other read tool already takes `refresh` for this exact reason.
    """

    def point_an_enemy_at(self, name):
        enemy = self.project.get_entry("enemies", 1)
        enemy.set("battler_name", self.project.codec.make_string(name))
        self.project.save_file(self.project.filename_for("enemies"),
                               tool="test", summary="battler")

    def rules(self, **arguments):
        out = self.call("validate", scopes=["assets"], **arguments)
        return sorted({f["rule"] for f in out["findings"]})

    def add_battler(self, name):
        folder = self.tmp / "project" / "Graphics" / "Battlers"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ("%s.png" % name)).write_bytes(b"\x89PNG\r\n\x1a\n")

    def test_validate_takes_refresh(self):
        """Every other read tool does, and this is the tool that needs it."""
        self.assertIn("refresh", REGISTRY["validate"].schema["properties"])

    def test_an_added_graphic_stops_being_reported(self):
        self.point_an_enemy_at("BrandNew")
        self.assertIn("missing_graphic", self.rules())

        self.add_battler("BrandNew")
        self.assertEqual(
            self.rules(refresh=True), [],
            "the graphic is on disk and validate still reports it missing, "
            "so the developer has no way to confirm the fix short of "
            "restarting the server")

    def test_without_refresh_the_cached_answer_is_kept(self):
        """The cache is deliberate: refresh is the way to say "look again"."""
        self.point_an_enemy_at("BrandNew")
        self.assertIn("missing_graphic", self.rules())
        self.add_battler("BrandNew")
        self.assertIn("missing_graphic", self.rules())

    def test_refresh_does_not_invent_findings(self):
        out = self.call("validate", refresh=True)
        plain = self.call("validate")
        self.assertEqual(out["counts"], plain["counts"])


class TestAForeignBackupDirectoryIsSkipped(ToolTest):
    """Pruning steps over what it did not create, whatever kind of link it is.

    Windows-only, because a directory junction is the case `is_symlink()`
    misses and there is no portable way to make one.
    """

    def setUp(self):
        super().setUp()
        if sys.platform != "win32":
            self.skipTest("a directory junction is a Windows construct")
        self.project = Project(self.tmp / "project", backup_keep=1)
        self.ctx = Context(self.project)

    def plant_junction(self, name="20200101-000000"):
        """A junction in the backups folder, named like one of our snapshots."""
        import subprocess

        target = self.tmp / "precious"
        target.mkdir(exist_ok=True)
        (target / "important.txt").write_text("keep me", encoding="utf-8")
        root = self.project.backup_root()
        root.mkdir(parents=True, exist_ok=True)
        link = root / name
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link),
                               str(target)], capture_output=True, text=True)
        if done.returncode != 0:
            self.skipTest("could not create a junction: %s"
                          % (done.stderr or done.stdout).strip())
        return link, target

    def test_pruning_does_not_fail_because_of_it(self):
        self.project.snapshot(label="one")
        self.project.snapshot(label="two")
        link, _ = self.plant_junction()
        removed = self.project.prune_backups()
        self.assertNotIn(link.name, removed,
                         "a directory this server did not create was listed "
                         "as removed")

    def test_the_junction_is_left_alone(self):
        self.project.snapshot(label="one")
        link, target = self.plant_junction()
        self.project.prune_backups()
        self.assertTrue(link.exists(), "the junction itself was removed")
        self.assertTrue((target / "important.txt").exists(),
                        "the junction's target lost a file")

    def stale_snapshot(self, stamp):
        """A snapshot directory as a previous run would have left it.

        Pruning is for earlier runs: this session's own directory is spared,
        and two `snapshot()` calls in one session share it -- the module backs
        a file up once per run.
        """
        folder = self.project.backup_root() / stamp
        (folder / "Data").mkdir(parents=True, exist_ok=True)
        (folder / "Data" / "Items.rvdata2").write_bytes(b"old")
        return folder

    def test_the_real_snapshots_are_still_pruned(self):
        """Skipping the foreign one must not stop the work."""
        self.project.snapshot(label="current")
        older = self.stale_snapshot("20250101-000000-one")
        self.stale_snapshot("20250102-000000-two")
        self.plant_junction()

        removed = self.project.prune_backups()
        self.assertIn(older.name, removed,
                      "a stale snapshot was not pruned, so one foreign "
                      "directory stopped the whole operation: %s" % removed)
        self.assertFalse(older.exists())

    def test_the_tool_reports_it_plainly(self):
        self.project.snapshot(label="one")
        self.project.snapshot(label="two")
        self.plant_junction()
        out = self.call("backups", op="prune")
        self.assertIn("removed", out)

    def test_a_dry_run_says_the_same_thing(self):
        self.project.snapshot(label="one")
        self.project.snapshot(label="two")
        link, _ = self.plant_junction()
        stale = self.stale_snapshot("20250101-000000-old")
        would = self.project.prune_backups(dry_run=True)
        self.assertNotIn(link.name, would)
        # What it would remove is listed, and nothing was: "not the foreign
        # one" is also what a dry run that lists nothing says.
        self.assertIn(stale.name, would)
        self.assertTrue(stale.exists())


class TestOneRuleForBeingOnTheMap(ToolTest):
    """Four copies of "is this coordinate inside the map", two of them wrong.

    `analysis` had it right twice; `event_tools` carried two `and` clauses
    that its own docstring says were rejected, dead because both sizes are
    refused below 1 three lines above; and `map_edit_tools` had no lower
    bound at all, so an event at x = -1 survived a resize unreported and a
    non-integer x from a crafted file reached `>= width` as a TypeError.
    """

    def test_a_negative_coordinate_is_off_the_map(self):
        from rpgvxace_mcp.schema import on_map
        self.assertFalse(on_map(-1, 0, 20, 15))
        self.assertFalse(on_map(0, -1, 20, 15))

    def test_the_far_edge_is_off_the_map(self):
        from rpgvxace_mcp.schema import on_map
        self.assertFalse(on_map(20, 0, 20, 15))
        self.assertFalse(on_map(0, 15, 20, 15))

    def test_the_corners_are_on_it(self):
        from rpgvxace_mcp.schema import on_map
        self.assertTrue(on_map(0, 0, 20, 15))
        self.assertTrue(on_map(19, 14, 20, 15))

    def event_at(self, x, y):
        """Put event 1 at a position, bypassing the tool's own bounds check."""
        game_map = self.project.load_map(1)
        event = game_map.get("events").get(1)
        event.set("x", x)
        event.set("y", y)
        self.project.save_file(self.project.map_file(1), tool="test",
                               summary="moved off the map")
        self.ctx = self.reopen()

    def test_a_resize_reports_an_event_at_a_negative_position(self):
        """The copy with no lower bound, through the tool that reads it."""
        self.event_at(-1, 0)
        out = self.call("map_edit", op="resize", map_id=1, width=20,
                        height=15, dry_run=True)
        self.assertIn("outside the map", str(out),
                      "an event at x = -1 survived a resize unreported: %s"
                      % out)

    def test_a_resize_reports_an_event_whose_position_is_not_a_number(self):
        """`or 0` turned an unreadable position into a valid one."""
        self.event_at(self.project.codec.make_string("west"), 0)
        out = self.call("map_edit", op="resize", map_id=1, width=20,
                        height=15, dry_run=True)
        self.assertIn("outside the map", str(out))

    def test_an_ordinary_event_is_not_reported(self):
        out = self.call("map_edit", op="resize", map_id=1, width=20,
                        height=15, dry_run=True)
        self.assertNotIn("outside the map", str(out))

    @staticmethod
    def by_hand(source):
        """The comparisons in `source` that write the rule out by hand.

        A bare coordinate (`x`, `y`, or an attribute of that name) ordered
        against a bare size (`width`, `height`) in any position, so the usual
        `0 <= x < width` is seen as well as `x < width`. Precise on purpose:
        three expressions in this tree mention a size and are not this rule --
        the unusable-size refusal (`width < 1 or height < 1`), a resize
        shortcut (`width >= data.xsize`), and a rectangle against a table
        (`self.x + self.width > data.xsize`) -- and an approximate scan
        flagged all three. None of them has a bare coordinate among its
        operands.
        """
        found = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE))
                       for op in node.ops):
                continue
            operands = [node.left, *node.comparators]
            coordinate = any(
                (isinstance(o, ast.Name) and o.id in ("x", "y"))
                or (isinstance(o, ast.Attribute) and o.attr in ("x", "y"))
                for o in operands)
            size = any(isinstance(o, ast.Name)
                       and o.id in ("width", "height") for o in operands)
            if coordinate and size:
                found.append(ast.unparse(node))
        return found

    def test_the_by_hand_scan_can_tell_the_forms_apart(self):
        """The instrument, fed what it must flag and what it must not."""
        for source in ("x < width", "0 <= x < width", "y >= height",
                       "0 <= y < height", "event.x >= width"):
            with self.subTest(flagged=source):
                self.assertTrue(self.by_hand(source), source)
        for source in ("width < 1 or height < 1", "width >= data.xsize",
                       "self.x + self.width > data.xsize", "x == 3"):
            with self.subTest(allowed=source):
                self.assertEqual(self.by_hand(source), [], source)

    def test_no_module_writes_the_rule_out_by_hand(self):
        """The rule, not one copy of it: no bare coordinate comparison left."""
        root = Path(__file__).resolve().parent.parent / "src" / "rpgvxace_mcp"
        offenders = []
        scanned = users = 0
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if path.name == "schema.py":
                continue        # where `on_map` lives
            scanned += 1
            users += "on_map(" in source
            offenders += ["%s: %s" % (path.name, text)
                          for text in self.by_hand(source)]
        # The scan read the tree and the tree uses the rule it points to: a
        # wrong `root`, or a rename of the variables, left this empty and green.
        self.assertGreater(scanned, 20, "the scan read almost nothing")
        self.assertGreater(users, 1, "no module outside schema.py asks on_map")
        self.assertEqual(offenders, [],
                         "these compare a coordinate against a map size "
                         "instead of asking on_map")
