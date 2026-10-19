"""The helper that keeps server-written files out of the real-project ground truth."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import real_project  # noqa: E402


class FakeProfile:
    extension = ".rvdata2"


class FakeProject:
    profile = FakeProfile()

    def __init__(self, root, ids):
        self.root = Path(root)
        self._ids = ids

    def map_ids_on_disk(self):
        return list(self._ids)


class TestEditorWrittenFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        (self.root / "Data").mkdir()
        for map_id, mtime in ((1, 1000), (2, 3000)):
            path = self.root / "Data" / ("Map%03d.rvdata2" % map_id)
            path.write_bytes(b"x")
            os.utime(path, (mtime, mtime))
        self.project = FakeProject(self.root, [1, 2])

    def log(self, *times):
        folder = self.root / ".rpgvxace-mcp"
        folder.mkdir()
        (folder / "changes.jsonl").write_text("\n".join(
            json.dumps({"at": "1970-01-01T00:%02d:%02d+00:00" % divmod(t // 1, 60)})
            for t in times), encoding="utf-8")

    def test_a_project_the_server_never_touched_counts_whole(self):
        self.assertEqual(real_project.editor_written_map_ids(self.project),
                         [1, 2])

    def test_a_file_changed_after_the_first_logged_change_is_left_out(self):
        self.log(2000, 2500)
        self.assertEqual(real_project.editor_written_map_ids(self.project),
                         [1])

    def test_the_earliest_entry_sets_the_cutoff_whatever_the_order(self):
        self.log(2500, 500)
        self.assertEqual(real_project.editor_written_map_ids(self.project),
                         [])

    def test_a_line_that_is_not_an_entry_is_ignored(self):
        self.log(2000)
        with open(self.root / ".rpgvxace-mcp" / "changes.jsonl", "a",
                  encoding="utf-8") as handle:
            handle.write("\nnot json\n{}\n")
        self.assertEqual(real_project.first_logged_change(self.root), 2000)

    def test_a_missing_file_is_not_ground_truth_once_there_is_a_log(self):
        self.log(2000)
        self.assertFalse(real_project.editor_written(self.project,
                                                     "Map099.rvdata2"))


if __name__ == "__main__":
    unittest.main()
