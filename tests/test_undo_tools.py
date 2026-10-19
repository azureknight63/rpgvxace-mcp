"""The `undo` tool and the journal of previous file contents behind it.

Every test here is about a property that would be easy to lose quietly: that
an undo puts back the exact bytes, that it is one step across several files,
that it refuses (and says why) when the editor has saved since, that the
journal cannot be turned against the project, and that a write the journal
cannot record is not made.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tool_case import ToolTest  # noqa: E402

from rpgvxace_mcp import marshal_rb, undo_journal  # noqa: E402
from rpgvxace_mcp.project import (  # noqa: E402
    MAX_DATA_FILE_BYTES,
    Project,
    ProjectError,
    ReadOnlyError,
    StaleFileError,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
)


class UndoCase(ToolTest):
    """A project, a way to make edits, and a way to look at the journal."""

    def setUp(self):
        super().setUp()
        self.data = self.tmp / "project" / "Data"

    @property
    def journal_dir(self):
        return self.tmp / "project" / ".rpgvxace-mcp" / "undo"

    def price(self, value, ctx=None):
        """Edit item 1's price: one write of Items."""
        return REGISTRY["db_edit"].run(
            ctx or self.ctx, {"table": "items", "ops": [
                {"op": "update", "id": 1, "patch": {"price": value}}]})

    def items(self):
        return self.data_bytes("Items")

    def rows(self, **extra):
        return self.call("undo", op="list", **extra)["entries"]

    def entry_path(self, seq):
        return self.journal_dir / ("e%012d.json" % seq)

    def blob_path(self, seq):
        return self.journal_dir / ("e%012d.bin" % seq)

    def editor_saves(self, name, payload=b"edited by the editor"):
        (self.data / name).write_bytes(payload)

    def editor_edits_price(self, value):
        """The editor saving Items with a different price: valid data."""
        path = self.data / ("Items" + self.extension)
        table = marshal_rb.load(path.read_bytes())
        table[1].set("price", value)
        path.write_bytes(marshal_rb.dump(table))


class TestAnEditCanBeTakenBack(UndoCase):
    def test_a_revert_puts_back_the_exact_bytes(self):
        original = self.items()
        self.price(777)
        self.assertNotEqual(self.items(), original)
        result = self.call("undo", op="revert")
        self.assertEqual(self.items(), original)
        self.assertEqual([r["file"] for r in result["restored"]],
                         ["Items" + self.extension])
        self.assertEqual(result["restored"][0]["action"], "restore")

    def test_count_goes_back_that_many_changes_and_no_further(self):
        original = self.items()
        self.price(11)
        after_first = self.items()
        self.price(22)
        self.call("undo", op="revert", count=1)
        self.assertEqual(self.items(), after_first)
        self.call("undo", op="revert", count=1)
        # The second undo undoes the first undo (it is journalled), so the
        # newest change is the redo of price 22 -- not the original.
        self.assertNotEqual(self.items(), original)

    def test_two_changes_back_in_one_call(self):
        original = self.items()
        self.price(11)
        self.price(22)
        self.call("undo", op="revert", count=2)
        self.assertEqual(self.items(), original)

    def test_an_undo_can_itself_be_undone(self):
        self.price(777)
        edited = self.items()
        self.call("undo", op="revert")
        self.assertNotEqual(self.items(), edited)
        self.call("undo", op="revert")
        self.assertEqual(self.items(), edited)

    def test_to_an_entry_id_leaves_the_state_just_after_that_change(self):
        self.price(11)
        keep = self.items()
        first = self.rows()[0]["id"]
        self.price(22)
        self.price(33)
        result = self.call("undo", op="revert", to=first)
        self.assertEqual(self.items(), keep)
        self.assertEqual(len(result["reverts_steps"]), 2)

    def test_to_the_newest_entry_has_nothing_to_undo(self):
        self.price(11)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert", to=self.rows()[0]["id"])
        self.assertIn("already the newest change", str(caught.exception))

    def test_to_an_id_that_is_not_there_says_so(self):
        self.price(11)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert", to=999)
        self.assertIn("no entry 999", str(caught.exception))

    def test_an_empty_journal_says_so(self):
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("journal is empty", str(caught.exception))

    def test_more_changes_than_the_journal_holds_is_refused(self):
        self.price(11)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert", count=5)
        self.assertIn("asked to undo 5 changes but the journal holds 1",
                      str(caught.exception))

    def test_a_revert_that_wrote_nothing_still_leaves_the_state_alone_on_dry_run(self):
        self.price(777)
        edited = self.items()
        entries = sorted(os.listdir(self.journal_dir))
        preview = self.call("undo", op="revert", dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual(list(preview)[0], "dry_run")
        self.assertNotIn("restored", preview)
        self.assertEqual(self.items(), edited)
        self.assertEqual(sorted(os.listdir(self.journal_dir)), entries)
        row = preview["would_restore"][0]
        self.assertEqual(row["file"], "Items" + self.extension)
        self.assertEqual(row["back_to"], "the state just before entry 1")
        self.assertEqual(row["from_entries"], [1])

    def test_the_cache_does_not_serve_the_undone_value(self):
        before = self.call("db_get", table="items", id=1)[
            "entries"][0]["entry"]["price"]
        self.price(777)
        self.call("undo", op="revert")
        after = self.call("db_get", table="items", id=1)[
            "entries"][0]["entry"]["price"]
        self.assertEqual(after, before)


class TestOneToolCallIsOneChange(UndoCase):
    def test_creating_a_map_is_reverted_in_both_files_at_once(self):
        infos = "MapInfos" + self.extension
        before_infos = self.data_bytes("MapInfos")
        self.call("map_edit", op="create", name="Cellar")
        self.assertTrue((self.data / ("Map003" + self.extension)).exists())
        rows = self.rows()
        self.assertEqual({r["file"] for r in rows},
                         {"Map003" + self.extension, infos})
        self.assertEqual(len({r["step"] for r in rows}), 1)
        changes = {r["file"]: r["change"] for r in rows}
        self.assertEqual(changes["Map003" + self.extension], "created")
        self.assertEqual(changes[infos], "modified")
        self.call("undo", op="revert")
        self.assertFalse((self.data / ("Map003" + self.extension)).exists())
        self.assertEqual(self.data_bytes("MapInfos"), before_infos)

    def test_a_deleted_map_comes_back(self):
        name = "Map002" + self.extension
        before = self.data_bytes("Map002")
        self.call("map_edit", op="delete", map_id=2)
        self.assertFalse((self.data / name).exists())
        result = self.call("undo", op="revert")
        self.assertEqual(self.data_bytes("Map002"), before)
        self.assertIn("recreate", [r["action"] for r in result["restored"]])

    def test_the_created_file_is_deleted_by_undo(self):
        self.call("map_edit", op="create", name="Cellar")
        result = self.call("undo", op="revert", dry_run=True)
        actions = {r["file"]: r["action"] for r in result["would_restore"]}
        self.assertEqual(actions["Map003" + self.extension], "delete")

    def test_the_count_is_in_changes_not_files(self):
        self.price(5)
        self.call("map_edit", op="create", name="Cellar")
        # Three journal entries (Items, Map003, MapInfos), two changes.
        self.assertEqual(len(self.rows()), 3)
        self.call("undo", op="revert", count=2)
        self.assertFalse((self.data / ("Map003" + self.extension)).exists())
        self.assertEqual(self.items(), self.items())

    def test_a_multi_file_revert_is_all_or_nothing(self):
        infos = "MapInfos" + self.extension
        self.call("map_edit", op="create", name="Cellar")
        created = self.data_bytes("Map003")
        self.editor_saves(infos)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn(infos, str(caught.exception))
        self.assertIn("RPG Maker editor", str(caught.exception))
        # Nothing was done, including the file that was fine.
        self.assertEqual(self.data_bytes("Map003"), created)
        self.assertEqual(self.data_bytes("MapInfos"),
                         b"edited by the editor")

    def test_a_write_that_fails_midway_puts_the_first_file_back(self):
        self.call("map_edit", op="delete", map_id=2)
        self.assertEqual(len(self.rows()), 2)
        before = {p.name: p.read_bytes() for p in self.data.iterdir()}
        real = undo_journal.write_bytes_atomic
        calls = []

        def second_fails(path, data):
            calls.append(path.name)
            if len(calls) == 2:
                raise OSError("disk went away")
            real(path, data)

        with mock.patch.object(undo_journal, "write_bytes_atomic",
                               second_fails):
            with self.assertRaises(ProjectError) as caught:
                self.call("undo", op="revert")
        self.assertEqual(len(calls), 2)
        self.assertIn("nothing changed", str(caught.exception))
        self.assertEqual({p.name: p.read_bytes() for p in self.data.iterdir()},
                         before)
        # And the failed attempt left no journal entry behind to confuse a
        # later undo: the newest entries are still the rename's.
        self.assertEqual(len(self.rows()), 2)
        self.call("undo", op="revert")
        self.assertTrue((self.data / ("Map002" + self.extension)).exists())


class TestAStaleFileIsNotOverwritten(UndoCase):
    def test_a_file_the_editor_saved_since_is_refused_and_named(self):
        name = "Items" + self.extension
        self.price(777)
        self.editor_saves(name)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        message = str(caught.exception)
        self.assertIn(name, message)
        self.assertIn("entry 1 wrote", message)
        self.assertIn("nothing was changed", message)
        self.assertEqual(self.items(), b"edited by the editor")

    def test_force_overwrites_and_the_overwrite_is_journalled(self):
        self.price(777)
        self.editor_saves("Items" + self.extension)
        self.call("undo", op="revert", force=True)
        self.assertNotEqual(self.items(), b"edited by the editor")
        # The editor's bytes are not lost: undoing the undo brings them back.
        self.call("undo", op="revert")
        self.assertEqual(self.items(), b"edited by the editor")

    def test_the_dry_run_names_the_conflict_without_refusing(self):
        self.price(777)
        self.editor_saves("Items" + self.extension)
        preview = self.call("undo", op="revert", dry_run=True)
        self.assertTrue(preview["would_be_refused"])
        self.assertEqual(preview["conflicts"][0]["file"],
                         "Items" + self.extension)
        self.assertIn("RPG Maker editor", preview["conflicts"][0]["reason"])

    def test_a_file_that_came_back_after_a_delete_is_a_conflict(self):
        name = "Map002" + self.extension
        self.call("map_edit", op="delete", map_id=2)
        self.editor_saves(name)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("exists again", str(caught.exception))

    def test_a_file_that_vanished_after_an_edit_is_a_conflict(self):
        self.price(777)
        (self.data / ("Items" + self.extension)).unlink()
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("gone from disk", str(caught.exception))

    def test_an_outside_change_between_two_journalled_ones_blocks_a_longer_undo(self):
        name = "Items" + self.extension
        self.price(11)
        self.editor_edits_price(4321)
        self.project.load_data("items", refresh=True)
        self.price(22)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert", count=2)
        self.assertIn("do not follow each other", str(caught.exception))
        # One step back is fine: it is just the last write.
        self.call("undo", op="revert", count=1)

    def test_the_normal_writers_still_refuse_a_stale_file_after_an_undo(self):
        self.price(777)
        self.call("undo", op="revert")
        # Read after the undo, so the server holds the restored file ...
        self.project.load_data("items")
        # ... and the editor saves it again behind the server's back.
        self.editor_edits_price(4321)
        with self.assertRaises(StaleFileError):
            self.price(5)

    def test_an_edit_straight_after_an_undo_writes_on_the_restored_state(self):
        original = self.items()
        self.price(777)
        self.call("undo", op="revert")
        self.price(5)
        self.call("undo", op="revert")
        self.assertEqual(self.items(), original)


class TestReadOnly(UndoCase):
    def setUp(self):
        super().setUp()
        self.price(777)
        self.edited = self.items()
        self.locked = Context(Project(self.tmp / "project", read_only=True))

    def test_a_read_only_server_refuses_to_revert(self):
        from rpgvxace_mcp.project import ReadOnlyError
        with self.assertRaises(ReadOnlyError) as caught:
            REGISTRY["undo"].run(self.locked, {"op": "revert"})
        self.assertIn("read-only", str(caught.exception))
        self.assertEqual(self.items(), self.edited)

    def test_it_can_still_list_and_preview(self):
        rows = REGISTRY["undo"].run(self.locked, {"op": "list"})["entries"]
        self.assertEqual(len(rows), 1)
        preview = REGISTRY["undo"].run(self.locked,
                                       {"op": "revert", "dry_run": True})
        self.assertTrue(preview["dry_run"])
        self.assertEqual(self.items(), self.edited)


class TestSequenceNumbersLiveOnDisk(UndoCase):
    def test_a_restarted_server_continues_the_numbering(self):
        self.price(1)
        self.price(2)
        later = self.reopen()
        self.price(3, ctx=later)
        self.assertEqual([r["id"] for r in self.rows()], [3, 2, 1])
        # And the restarted server can undo what the first one did.
        original_state = self.items()
        REGISTRY["undo"].run(later, {"op": "revert", "count": 3})
        self.assertNotEqual(self.items(), original_state)

    def test_two_servers_on_one_folder_never_share_a_number(self):
        other = self.reopen()
        for value in range(10, 16):
            self.price(value, ctx=self.ctx if value % 2 else other)
            self.project.load_data("items", refresh=True)
            other.project.load_data("items", refresh=True)
        ids = [r["id"] for r in self.rows()]
        self.assertEqual(ids, [6, 5, 4, 3, 2, 1])

    def test_two_writes_in_the_same_second_stay_apart_and_in_order(self):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2030, 1, 2, 3, 4, 5, tzinfo=tz or timezone.utc)

        with mock.patch.object(undo_journal, "datetime", Frozen):
            self.price(1)
            self.price(2)
        rows = self.rows()
        self.assertEqual([r["id"] for r in rows], [2, 1])
        self.assertEqual(rows[0]["at"], rows[1]["at"])
        self.call("undo", op="revert", count=1)
        # Newest first: the second write is what went back.
        self.assertEqual(self.call("db_get", table="items", id=1)[
            "entries"][0]["entry"]["price"], 1)

    def test_a_number_taken_since_the_scan_is_not_reused(self):
        """Two servers pick the same next number; the second must move on."""
        self.price(1)
        first_blob = self.blob_path(1).read_bytes()
        real = undo_journal.UndoJournal._scan
        calls = []

        def behind_the_times(journal, folder):
            calls.append(1)
            if len(calls) == 1:
                return {}, {}, None  # the scan another server also saw
            return real(journal, folder)

        with mock.patch.object(undo_journal.UndoJournal, "_scan",
                               behind_the_times):
            self.price(2)
        self.assertEqual([r["id"] for r in self.rows()], [2, 1])
        self.assertEqual(self.blob_path(1).read_bytes(), first_blob)

    def test_a_stray_blob_with_no_entry_is_skipped_not_reused(self):
        self.journal_dir.mkdir(parents=True)
        self.blob_path(5).write_bytes(b"orphan")
        self.price(1)
        self.assertEqual([r["id"] for r in self.rows()], [6])

    def test_numbers_that_have_run_out_refuse_the_write(self):
        self.journal_dir.mkdir(parents=True)
        self.entry_path(999999999999).write_text("{}")
        before = self.items()
        with self.assertRaises(ProjectError) as caught:
            self.price(1)
        self.assertIn("used up", str(caught.exception))
        self.assertEqual(self.items(), before)


class TestBackupsAndTheJournal(UndoCase):
    def test_a_restore_can_be_undone(self):
        self.call("backups", op="create", label="before")
        snapshot = self.call("backups", op="list")["backups"][0]["backup"]
        original = self.items()
        self.price(777)
        self.call("backups", op="restore", backup=snapshot,
                  files=["Items" + self.extension])
        self.assertEqual(self.items(), original)
        self.call("undo", op="revert")
        self.assertNotEqual(self.items(), original)

    def test_a_whole_snapshot_restore_is_one_change(self):
        self.call("backups", op="create", label="before")
        snapshot = self.call("backups", op="list")["backups"][0]["backup"]
        before = {p.name: p.read_bytes() for p in self.data.iterdir()}
        self.price(777)
        self.call("map_edit", op="update", map_id=1, name="Renamed")
        edited = {p.name: p.read_bytes() for p in self.data.iterdir()}
        self.call("backups", op="restore", backup=snapshot)
        restored = self.rows()
        self.assertEqual({r["step"] for r in restored[:2]} & {
            r["step"] for r in restored[2:]}, set())
        self.call("undo", op="revert")
        self.assertEqual({p.name: p.read_bytes() for p in self.data.iterdir()},
                         edited)
        del before

    def test_undo_works_after_the_snapshot_it_came_from_is_pruned(self):
        self.project.backup_keep = 1
        self.call("backups", op="create", label="a")
        snapshot = next(b["backup"] for b in self.call(
            "backups", op="list")["backups"] if b["backup"].endswith("-a"))
        original = self.items()
        self.price(777)
        self.call("backups", op="restore", backup=snapshot,
                  files=["Items" + self.extension])
        self.call("backups", op="create", label="b")
        self.call("backups", op="create", label="c")
        names = [b["backup"] for b in self.call("backups",
                                                op="list")["backups"]]
        self.assertFalse(any(n.endswith("-a") for n in names), names)
        self.call("undo", op="revert", count=1)
        self.assertNotEqual(self.items(), original)

    def test_pruning_never_touches_the_journal(self):
        self.price(1)
        files = sorted(os.listdir(self.journal_dir))
        self.project.backup_keep = 1
        for label in ("a", "b", "c"):
            self.call("backups", op="create", label=label)
        self.call("backups", op="prune")
        self.assertEqual(sorted(os.listdir(self.journal_dir)), files)

    def test_a_snapshot_cannot_collide_with_a_journal_name(self):
        self.price(1)
        # The snapshot labels most likely to be mistaken for a journal name.
        for label in ("undo", "e000000000001", "e000000000001.bin"):
            made = self.call("backups", op="create", label=label)
            self.assertTrue(made["path"].startswith(
                str(self.tmp / "project" / ".rpgvxace-mcp" / "backups")))
            self.assertFalse(Path(made["path"]).resolve().is_relative_to(
                self.journal_dir.resolve()))
        self.assertEqual(sorted(p.name for p in self.journal_dir.iterdir()),
                         ["e000000000001.bin", "e000000000001.json"])
        # And none of the journal reaches a snapshot.
        for backup in self.call("backups", op="list")["backups"]:
            self.assertFalse([f for f in backup["files"]
                              if f.startswith("e0")], backup)

    def test_the_two_folders_are_siblings_under_the_work_dir(self):
        work = self.project.work_dir()
        self.assertEqual(self.project.backup_root().parent, work)
        self.assertEqual(self.project.journal.directory().parent, work)
        self.assertNotEqual(self.project.backup_root(),
                            self.project.journal.directory())


class TestTheJournalIsBounded(UndoCase):
    def test_the_oldest_entries_are_dropped_and_the_drop_is_reported(self):
        with mock.patch.object(undo_journal, "MAX_UNDO_ENTRIES", 5):
            for value in range(1, 9):
                self.price(value)
            listing = self.call("undo", op="list")
        self.assertEqual([r["id"] for r in listing["entries"]],
                         [8, 7, 6, 5, 4])
        self.assertEqual(listing["journal"]["dropped_entries"], 3)
        self.assertIn("3 older entries were dropped",
                      listing["entries_note"])
        self.assertEqual(sorted(p.name for p in self.journal_dir.glob("*.bin")),
                         ["e%012d.bin" % n for n in (4, 5, 6, 7, 8)])

    def test_a_revert_further_back_than_the_journal_says_what_was_dropped(self):
        with mock.patch.object(undo_journal, "MAX_UNDO_ENTRIES", 3):
            for value in range(1, 7):
                self.price(value)
            with self.assertRaises(ProjectError) as caught:
                self.call("undo", op="revert", count=4)
        self.assertIn("holds 3", str(caught.exception))
        self.assertIn("3 older entries were dropped", str(caught.exception))

    def test_the_byte_ceiling_drops_oldest_first_too(self):
        size = len(self.items())
        with mock.patch.object(undo_journal, "MAX_UNDO_BYTES",
                               int(size * 2.5)):
            for value in range(1, 6):
                self.price(value)
            listing = self.call("undo", op="list")
        self.assertLessEqual(len(listing["entries"]), 2)
        self.assertGreaterEqual(len(listing["entries"]), 1)
        self.assertEqual(listing["entries"][0]["id"], 5)
        self.assertGreater(listing["journal"]["dropped_entries"], 0)

    def test_a_steps_entries_are_dropped_together_never_half(self):
        with mock.patch.object(undo_journal, "MAX_UNDO_ENTRIES", 4):
            self.call("map_edit", op="create", name="One")      # 2 entries
            self.call("map_edit", op="create", name="Two")      # 2 entries
            self.price(1)                                       # 1 entry
            rows = self.rows()
        steps = {}
        for row in rows:
            steps.setdefault(row["step"], []).append(row["file"])
        for step, files in steps.items():
            if step in (1, 3):  # the creation steps
                self.assertEqual(len(files), 2, steps)

    def test_a_change_too_big_for_the_journal_is_refused_not_made(self):
        before = self.items()
        with mock.patch.object(undo_journal, "MAX_UNDO_BYTES", 10):
            with self.assertRaises(ProjectError) as caught:
                self.price(1)
        self.assertIn("NOT applied", str(caught.exception))
        self.assertEqual(self.items(), before)

    def test_list_is_bounded_and_says_when_it_stopped(self):
        for value in range(1, 6):
            self.price(value)
        listing = self.call("undo", op="list", limit=2)
        self.assertEqual(len(listing["entries"]), 2)
        self.assertIs(listing["entries_truncated"], True)
        full = self.call("undo", op="list")
        self.assertNotIn("entries_truncated", full)

    def test_a_folder_stuffed_with_junk_is_scanned_boundedly(self):
        self.journal_dir.mkdir(parents=True)
        for number in range(undo_journal._MAX_SCAN + 50):
            (self.journal_dir / ("junk%d" % number)).write_bytes(b"")
        self.price(1)
        listing = self.call("undo", op="list")
        self.assertIn("entries_stopped", listing)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("only the first", str(caught.exception))

    def test_the_blob_ceiling_is_the_ceiling_for_reading_a_data_file(self):
        self.assertEqual(undo_journal.MAX_UNDO_BLOB_BYTES,
                         MAX_DATA_FILE_BYTES)


class TestAWriteTheJournalCannotRecordIsNotMade(UndoCase):
    def block_the_journal(self):
        work = self.tmp / "project" / ".rpgvxace-mcp"
        work.mkdir(exist_ok=True)
        shutil.rmtree(work / "undo", ignore_errors=True)
        (work / "undo").write_bytes(b"a file where the folder should be")

    def test_a_save_is_refused_and_says_why(self):
        self.block_the_journal()
        before = self.items()
        with self.assertRaises(ProjectError) as caught:
            self.price(777)
        message = str(caught.exception)
        self.assertIn("NOT applied", message)
        self.assertIn("undo journal could not be written", message)
        self.assertEqual(self.items(), before)

    def test_the_refused_edit_does_not_linger_for_the_next_write(self):
        self.block_the_journal()
        with self.assertRaises(ProjectError):
            self.price(777)
        (self.tmp / "project" / ".rpgvxace-mcp" / "undo").unlink()
        self.price(5)
        entry = self.call("db_get", table="items", id=1)["entries"][0]["entry"]
        self.assertEqual(entry["price"], 5)

    def test_a_delete_is_refused(self):
        self.block_the_journal()
        name = "Map002" + self.extension
        with self.assertRaises(ProjectError) as caught:
            self.call("map_edit", op="delete", map_id=2)
        self.assertIn("NOT applied", str(caught.exception))
        self.assertTrue((self.data / name).exists())

    def test_a_restore_is_refused_before_it_copies_anything(self):
        self.call("backups", op="create", label="before")
        snapshot = self.call("backups", op="list")["backups"][0]["backup"]
        self.price(777)
        edited = self.items()
        self.block_the_journal()
        with self.assertRaises(ProjectError) as caught:
            self.call("backups", op="restore", backup=snapshot,
                      files=["Items" + self.extension])
        self.assertIn("NOT applied", str(caught.exception))
        self.assertEqual(self.items(), edited)

    def test_an_undo_is_refused_when_its_own_entry_cannot_be_kept(self):
        self.price(777)
        edited = self.items()
        with mock.patch.object(undo_journal.UndoJournal, "_write_entry",
                               side_effect=OSError("disk full")):
            with self.assertRaises(ProjectError) as caught:
                self.call("undo", op="revert")
        self.assertIn("disk full", str(caught.exception))
        self.assertEqual(self.items(), edited)

    def test_a_write_that_changes_nothing_is_not_journalled(self):
        self.price(777)
        self.assertEqual(len(self.rows()), 1)
        self.price(777)
        self.assertEqual(len(self.rows()), 1)

    def test_a_failed_data_write_takes_its_entry_back(self):
        self.price(1)
        real = os.replace

        def locked(source, target):
            if Path(target).name == "Items" + self.extension:
                raise OSError("locked")
            real(source, target)  # the journal's own files go through

        with mock.patch("rpgvxace_mcp.project.os.replace", locked):
            with self.assertRaises(ProjectError) as caught:
                self.price(2)
        self.assertIn("could not write", str(caught.exception))
        self.assertEqual(len(self.rows()), 1)

    def test_the_journal_folder_being_a_link_is_refused(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        work = self.tmp / "project" / ".rpgvxace-mcp"
        work.mkdir(exist_ok=True)
        try:
            os.symlink(outside, work / "undo", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks")
        with self.assertRaises(ProjectError) as caught:
            self.price(1)
        self.assertIn("is a link", str(caught.exception))
        self.assertEqual(list(outside.iterdir()), [])


class TestAHandEditedJournalIsNotTrusted(UndoCase):
    """The journal sits in a folder the threat model calls hostile."""

    def setUp(self):
        super().setUp()
        self.price(777)
        self.edited = self.items()
        self.sentinel = self.tmp / "outside.rvdata2"
        self.sentinel.write_bytes(b"do not touch")
        self.entry = json.loads(self.entry_path(1).read_text())

    def tamper(self, **changes):
        record = dict(self.entry)
        record.update(changes)
        self.entry_path(1).write_text(json.dumps(record))

    def assert_refused_and_untouched(self, expect=None):
        listing = self.call("undo", op="list")
        self.assertEqual(listing["entries"], [])
        self.assertIn("1", listing["entries_not_read"])
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("entry 1", str(caught.exception))
        if expect:
            self.assertIn(expect, listing["entries_not_read"]["1"])
        self.assertEqual(self.items(), self.edited)
        self.assertEqual(self.sentinel.read_bytes(), b"do not touch")
        self.assertFalse((self.tmp / "evil.rvdata2").exists())

    def test_every_hostile_file_name_is_refused(self):
        names = [
            "..\\..\\outside.rvdata2", "../outside.rvdata2",
            str(self.sentinel), "C:\\outside.rvdata2", "/outside.rvdata2",
            "Items" + self.extension + "\n", "nul" + self.extension,
            "Game.ini", "", ".." , "Data/Items" + self.extension,
            "Items" + self.extension + ".rpgvxace-mcp.tmp", None, 5,
            ["Items"], {"a": 1}, "A" * 5000,
        ]
        for name in names:
            with self.subTest(name=name):
                self.tamper(file=name)
                self.assert_refused_and_untouched()

    def test_absurd_and_wrongly_typed_numbers_are_refused(self):
        cases = {
            "before_size": [10 ** 30, -1, True, 1.5, "7", None, 2 ** 63],
            "seq": [2, 0, True, "1"],
            "step": [0, 2, -5, 10 ** 30, True],
            "after_size": [10 ** 30, -3],
        }
        for key, values in cases.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.tamper(**{key: value})
                    self.assert_refused_and_untouched()

    def test_bad_hashes_and_states_are_refused(self):
        for key, value in [("before_hash", "zz"), ("before_hash", None),
                           ("before_hash", "A" * 32), ("after_hash", 5),
                           ("after_hash", "short"), ("before", "maybe"),
                           ("before", None), ("v", 2), ("v", None)]:
            with self.subTest(key=key, value=value):
                self.tamper(**{key: value})
                self.assert_refused_and_untouched()

    def test_an_absent_entry_may_not_carry_bytes(self):
        self.tamper(before="absent", before_size=10, before_hash=None)
        self.assert_refused_and_untouched()

    def test_text_fields_are_bounded(self):
        self.tamper(summary="x" * 100000)
        self.assert_refused_and_untouched()

    def test_junk_that_is_not_json_or_is_too_big_or_too_deep(self):
        for raw in (b"not json", b"[" * 30000, b"{" * 30000 + b"}" * 30000,
                    b"\xff\xfe\x00", b"[]", b"7", b"null",
                    b" " * (undo_journal._MAX_META_BYTES + 5)):
            with self.subTest(size=len(raw), head=raw[:8]):
                self.entry_path(1).write_bytes(raw)
                self.assert_refused_and_untouched()

    def test_a_blob_of_the_wrong_size_or_hash_is_refused(self):
        for payload in (b"short", b"\x00" * (len(self.blob_path(1).read_bytes())
                                            + 1)):
            with self.subTest(size=len(payload)):
                original = self.blob_path(1).read_bytes()
                self.blob_path(1).write_bytes(payload)
                with self.assertRaises(ProjectError) as caught:
                    self.call("undo", op="revert")
                self.assertIn("entry 1", str(caught.exception))
                self.assertEqual(self.items(), self.edited)
                self.blob_path(1).write_bytes(original)
        # Same size, different bytes: the hash catches it.
        flipped = bytearray(self.blob_path(1).read_bytes())
        flipped[-1] ^= 0xFF
        self.blob_path(1).write_bytes(bytes(flipped))
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("do not match the hash", str(caught.exception))
        self.assertEqual(self.items(), self.edited)

    def test_a_missing_blob_is_refused_and_listed_as_not_restorable(self):
        self.blob_path(1).unlink()
        row = self.rows()[0]
        self.assertIs(row["restorable"], False)
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("missing", str(caught.exception))

    def test_a_blob_that_is_a_link_is_not_followed(self):
        self.sentinel.write_bytes(self.blob_path(1).read_bytes())
        self.blob_path(1).unlink()
        try:
            os.symlink(self.sentinel, self.blob_path(1))
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks")
        with self.assertRaises(ProjectError):
            self.call("undo", op="revert")
        self.assertEqual(self.items(), self.edited)

    def test_a_destination_that_is_a_link_is_refused(self):
        target = self.data / ("Items" + self.extension)
        keep = self.tmp / "real_items"
        keep.write_bytes(target.read_bytes())
        target.unlink()
        try:
            os.symlink(keep, target)
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks")
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert")
        self.assertIn("link", str(caught.exception))
        self.assertEqual(keep.read_bytes(), self.edited)

    def pretend_these_are_links(self, *names):
        """Stand in for a link where this account cannot make a real one."""
        real = undo_journal._is_reparse_point
        return mock.patch.object(
            undo_journal, "_is_reparse_point",
            lambda path: Path(path).name in names or real(path))

    def test_a_blob_that_is_a_link_is_refused_even_where_links_cannot_be_made(self):
        with self.pretend_these_are_links("e000000000001.bin"):
            with self.assertRaises(ProjectError) as caught:
                self.call("undo", op="revert")
        self.assertIn("not a plain file", str(caught.exception))
        self.assertEqual(self.items(), self.edited)

    def test_a_data_file_that_is_a_link_is_refused_even_where_links_cannot_be_made(self):
        with self.pretend_these_are_links("Items" + self.extension):
            with self.assertRaises(ProjectError) as caught:
                self.call("undo", op="revert")
        self.assertIn("link", str(caught.exception))
        self.assertEqual(self.items(), self.edited)

    def test_a_journal_folder_that_is_a_link_is_refused_on_read_and_write(self):
        with self.pretend_these_are_links("undo"):
            with self.assertRaises(ProjectError) as caught:
                self.call("undo", op="list")
            self.assertIn("is a link", str(caught.exception))
            with self.assertRaises(ProjectError) as caught:
                self.price(1)
            self.assertIn("NOT applied", str(caught.exception))
        self.assertEqual(self.items(), self.edited)

    def test_a_bad_entry_newer_than_the_target_blocks_the_revert(self):
        self.price(1)
        self.price(2)
        self.entry_path(2).write_text("garbage")
        # Entry 3 is the newest and nothing unreadable lies beyond it.
        self.call("undo", op="revert", count=1)
        # Going back two changes means passing entry 2, which cannot be
        # described, so the journal refuses rather than skip it.
        with self.assertRaises(ProjectError) as caught:
            self.call("undo", op="revert", count=3)
        self.assertIn("entry 2", str(caught.exception))
        self.assertIn("cannot be trusted", str(caught.exception))

    def test_a_file_name_in_a_good_looking_entry_for_another_engine_is_refused(self):
        other = ".rxdata" if self.extension != ".rxdata" else ".rvdata2"
        self.tamper(file="Items" + other)
        self.assert_refused_and_untouched()


class TestArgumentsAreStrict(UndoCase):
    def test_count_and_to_refuse_what_is_not_a_small_whole_number(self):
        self.price(1)
        for key in ("count", "to"):
            for number, value in enumerate(
                    (0, -1, True, 1.5, "1", [1], 10 ** 5000, 10 ** 40,
                     float("nan"))):
                with self.subTest(key=key, case=number):
                    with self.assertRaises((ToolError, ProjectError)):
                        self.call("undo", op="revert", **{key: value})

    def test_a_huge_limit_is_refused(self):
        for number, value in enumerate((0, -1, 101, 10 ** 5000, True, "5")):
            with self.subTest(case=number):
                with self.assertRaises(ToolError):
                    self.call("undo", op="list", limit=value)

    def test_count_and_to_together_are_refused(self):
        self.price(1)
        with self.assertRaises(ToolError) as caught:
            self.call("undo", op="revert", count=1, to=1)
        self.assertIn("not both", str(caught.exception))

    def test_an_argument_the_op_does_not_read_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("undo", op="list", force=True)
        self.assertIn("does not read", str(caught.exception))
        with self.assertRaises(ToolError):
            self.call("undo", op="revert", limit=3)

    def test_an_unknown_op_is_refused(self):
        with self.assertRaises(ToolError):
            self.call("undo", op="redo")

    def test_the_description_advertises_every_ceiling(self):
        text = REGISTRY["undo"].description
        for ceiling in (undo_journal.MAX_UNDO_ENTRIES,
                        undo_journal.MAX_UNDO_BYTES,
                        undo_journal.MAX_UNDO_BLOB_BYTES,
                        undo_journal.MAX_UNDO_LIST, undo_journal.MAX_UNDO_STEPS):
            self.assertIn(str(ceiling), text)
        self.assertIn("REFUSED", text)

    def test_the_list_row_quotes_journal_text_boundedly(self):
        self.price(1)
        record = json.loads(self.entry_path(1).read_text())
        record["summary"] = "s" * 700
        record["tool"] = "t" * 60
        self.entry_path(1).write_text(json.dumps(record))
        row = self.rows()[0]
        self.assertLess(len(row["summary"]), 200)


if __name__ == "__main__":
    unittest.main()
