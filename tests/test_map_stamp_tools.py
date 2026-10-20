"""`map_stamp`: a piece of a map kept by name and pasted back anywhere.

The geometry is held to hand-written literals (a 3x2 grid whose every cell is
different, so a swapped axis cannot hide), the autotile behaviour to an
independent derivation (`map_paint` painting the mirrored layout directly on
the destination), and the untrusted-file behaviour to a table of hand edits,
each of which must be refused by name with the map file left byte-identical.
"""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp import autotile  # noqa: E402
from rpgvxace_mcp.analysis import WalkBudget  # noqa: E402
from rpgvxace_mcp.convert import to_json  # noqa: E402
from rpgvxace_mcp.tools import map_stamp_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import REGISTRY, ToolError  # noqa: E402

from tool_case import ToolTest  # noqa: E402


def label(value):
    """A subtest name for a hostile value that cannot itself be `repr`ed."""
    try:
        return repr(value)[:30]
    except ValueError:  # an integer past 4300 digits, or a list holding one
        return "something huge"


GRID = [1, 2, 3, 4, 5, 6]  # 3 wide, 2 tall; every cell different


def turned(mirror="none", rotate=0, cells=GRID, width=3, height=2, meta=False):
    """The pure transform, as rows, so a failure prints a grid."""
    turn = map_stamp_tools.Orientation(mirror, rotate)
    out = map_stamp_tools.turn_cells(cells, width, height, turn, meta)
    new_width, _ = turn.size(width, height)
    return [out[i:i + new_width] for i in range(0, len(out), new_width)]


class TestTheGeometry(unittest.TestCase):
    """Mirror and rotate, as literals. Rotation is clockwise."""

    def test_no_turn_is_the_grid(self):
        self.assertEqual(turned(), [[1, 2, 3], [4, 5, 6]])

    def test_mirror_horizontal_swaps_the_columns(self):
        self.assertEqual(turned("horizontal"), [[3, 2, 1], [6, 5, 4]])

    def test_mirror_vertical_swaps_the_rows(self):
        self.assertEqual(turned("vertical"), [[4, 5, 6], [1, 2, 3]])

    def test_mirror_both_is_a_half_turn(self):
        self.assertEqual(turned("both"), [[6, 5, 4], [3, 2, 1]])
        self.assertEqual(turned(rotate=180), turned("both"))

    def test_rotate_90_turns_clockwise(self):
        self.assertEqual(turned(rotate=90), [[4, 1], [5, 2], [6, 3]])

    def test_rotate_270_turns_anticlockwise(self):
        self.assertEqual(turned(rotate=270), [[3, 6], [2, 5], [1, 4]])

    def test_mirror_is_applied_before_the_rotation(self):
        self.assertEqual(turned("horizontal", 90), [[6, 3], [5, 2], [4, 1]])

    def test_the_size_swaps_for_a_quarter_turn_only(self):
        self.assertEqual(map_stamp_tools.Orientation("none", 90).size(3, 2),
                         (2, 3))
        self.assertEqual(map_stamp_tools.Orientation("none", 180).size(3, 2),
                         (3, 2))

    def test_mirroring_twice_is_the_identity(self):
        for mirror in ("horizontal", "vertical", "both"):
            with self.subTest(mirror=mirror):
                once = map_stamp_tools.turn_cells(
                    GRID, 3, 2, map_stamp_tools.Orientation(mirror, 0), False)
                twice = map_stamp_tools.turn_cells(
                    once, 3, 2, map_stamp_tools.Orientation(mirror, 0), False)
                self.assertEqual(twice, GRID)

    def test_rotating_four_quarter_turns_is_the_identity(self):
        quarter = map_stamp_tools.Orientation("none", 90)
        cells, width, height = GRID, 3, 2
        for _ in range(4):
            cells = map_stamp_tools.turn_cells(cells, width, height, quarter,
                                               False)
            width, height = quarter.size(width, height)
        self.assertEqual((cells, width, height), (GRID, 3, 2))

    def test_a_shadow_word_moves_its_quarters_and_keeps_its_region(self):
        """Layer 3: bits 1 TL, 2 TR, 4 BL, 8 BR in the low nibble."""
        region = 5 << 8
        word = lambda bits: [region | bits]  # noqa: E731
        cases = [
            ("horizontal", 0, {1: 2, 2: 1, 4: 8, 8: 4, 5: 10, 3: 3}),
            ("vertical", 0, {1: 4, 4: 1, 2: 8, 8: 2, 5: 5, 3: 12}),
            ("none", 90, {1: 2, 2: 8, 8: 4, 4: 1, 5: 3, 3: 10}),
            ("none", 180, {1: 8, 8: 1, 2: 4, 4: 2, 5: 10}),
            ("none", 270, {1: 4, 4: 8, 8: 2, 2: 1, 5: 12}),
        ]
        for mirror, rotate, table in cases:
            for before, after in table.items():
                with self.subTest(mirror=mirror, rotate=rotate, bits=before):
                    self.assertEqual(
                        turned(mirror, rotate, word(before), 1, 1, True),
                        [word(after)[0:1]])

    def test_bits_the_editor_never_reads_are_kept(self):
        self.assertEqual(turned("horizontal", 0, [0x30 | 1], 1, 1, True),
                         [[0x30 | 2]])


class StampTest(ToolTest):
    """`map_stamp` against the demo project.

    Map 1 is 20 x 15 of A2 kind 16 with the Elder (5, 4) and the Cave
    Entrance (8, 2); map 2 is 12 x 10 with the Chest at (3, 3).
    """

    def stamp(self, **arguments):
        return self.call("map_stamp", **arguments)

    def rows(self, map_id, x, y, width, height, z=0):
        return self.call("map_tiles", map_id=map_id, x=x, y=y, width=width,
                         height=height, z=z)["rows"]

    def put(self, map_id, x, y, rows, z=0):
        self.call("map_tiles", op="write", map_id=map_id, x=x, y=y,
                  width=len(rows[0]), height=len(rows), z=z, rows=rows)

    def map_bytes(self, map_id):
        return self.data_bytes("Map%03d" % map_id)

    def events_of(self, map_id):
        events = self.reopen().project.load_map(map_id).get("events")
        return {key: value for key, value in events.pairs}

    def event_json(self, map_id, event_id):
        ctx = self.reopen()
        event = ctx.project.load_map(map_id).get("events").get(event_id)
        return to_json(event, ctx.codec)

    def stamp_dir(self):
        return self.tmp / "project" / ".rpgvxace-mcp" / "stamps"

    def stamp_file(self, name):
        return self.stamp_dir() / (name + ".json")

    def edit_file(self, name, change):
        """Hand-edit a stored stamp, the way an untrusted file arrives."""
        path = self.stamp_file(name)
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def make_pattern(self):
        """A 3x2 block of distinct plain tiles on layers 0-2 at map 1 (10, 8)."""
        self.put(1, 10, 8, [[1540, 1541, 1542], [1543, 1544, 1545]], z=0)
        self.put(1, 10, 8, [[3, 4, 5], [6, 7, 8]], z=1)
        self.put(1, 10, 8, [[10, 11, 12], [13, 14, 15]], z=2)
        self.put(1, 10, 8, [[5 << 8 | 1, 5 << 8 | 2, 7 << 8],
                            [5 << 8 | 4, 5 << 8 | 8, 0]], z=3)


class TestCopyListDelete(StampTest):
    def test_a_copy_is_kept_by_name_and_listed(self):
        self.make_pattern()
        out = self.stamp(op="copy", name="block", map_id=1, x=10, y=8,
                         width=3, height=2)
        self.assertEqual(out["stamp"]["width"], 3)
        self.assertEqual(out["stamp"]["height"], 2)
        self.assertEqual(out["stamp"]["layers"], [0, 1, 2, 3])
        self.assertTrue(self.stamp_file("block").is_file())
        listed = self.stamp(op="list")["stamps"]
        self.assertEqual([row["name"] for row in listed], ["block"])
        self.assertEqual((listed[0]["width"], listed[0]["height"]), (3, 2))
        self.assertEqual(listed[0]["source_map_id"], 1)

    def test_list_with_no_stamps_is_empty_and_makes_nothing(self):
        self.assertEqual(self.stamp(op="list")["stamps"], [])
        self.assertFalse((self.tmp / "project" / ".rpgvxace-mcp").exists())

    def test_delete_removes_the_file(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        self.stamp(op="delete", name="block")
        self.assertFalse(self.stamp_file("block").exists())
        self.assertEqual(self.stamp(op="list")["stamps"], [])

    def test_delete_of_a_stamp_that_is_not_there_says_so(self):
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="delete", name="ghost")
        self.assertIn("no stamp named 'ghost'", str(caught.exception))

    def test_a_dry_run_copy_stores_nothing_and_makes_no_folder(self):
        out = self.stamp(op="copy", name="block", map_id=1, x=0, y=0,
                         width=2, height=2, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertIn("would_create", out)
        self.assertFalse((self.tmp / "project" / ".rpgvxace-mcp").exists())

    def test_a_dry_run_delete_keeps_the_file(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        out = self.stamp(op="delete", name="block", dry_run=True)
        self.assertIn("would_delete", out)
        self.assertTrue(self.stamp_file("block").is_file())

    def test_copying_over_a_name_is_refused(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        before = self.stamp_file("block").read_bytes()
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="block", map_id=1, x=3, y=3, width=2,
                       height=2)
        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(self.stamp_file("block").read_bytes(), before)

    def test_layers_narrow_what_is_kept(self):
        self.make_pattern()
        out = self.stamp(op="copy", name="ground", map_id=1, x=10, y=8,
                         width=3, height=2, layers=[0])
        self.assertEqual(out["stamp"]["layers"], [0])
        data = json.loads(self.stamp_file("ground").read_text("utf-8"))
        self.assertEqual(list(data["cells"]), ["0"])

    def test_a_layer_the_map_lacks_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="x", map_id=1, x=0, y=0, width=2,
                       height=2, layers=[4])
        self.assertIn("layer", str(caught.exception))

    def test_a_rectangle_off_the_map_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="x", map_id=1, x=19, y=14, width=2,
                       height=2)
        self.assertIn("does not fit", str(caught.exception))


class TestNames(StampTest):
    def test_only_plain_names_are_taken(self):
        bad = ["", "../escape", "a/b", "a" + chr(92) + "b", "x.json",
               "-leading", "_leading", "with space", "caf" + chr(0xe9),
               "a" * 49, "x" + chr(10), "C:evil", "..", ".", "a:b"]
        for name in bad:
            with self.subTest(name=name):
                with self.assertRaises(ToolError) as caught:
                    self.stamp(op="copy", name=name, map_id=1, x=0, y=0,
                               width=2, height=2)
                self.assertIn("name", str(caught.exception))
        self.assertFalse((self.tmp / "project" / ".rpgvxace-mcp").exists())

    def test_a_windows_device_name_is_refused(self):
        """`CON.json` is the console on Windows; the name is refused outright."""
        with mock.patch.object(map_stamp_tools, "_write_stamp",
                               side_effect=AssertionError("written")):
            for name in ("CON", "nul", "Aux", "PRN", "COM1", "lpt9"):
                with self.subTest(name=name):
                    with self.assertRaises(ToolError) as caught:
                        self.stamp(op="copy", name=name, map_id=1, x=0, y=0,
                                   width=2, height=2)
                    self.assertIn("device", str(caught.exception))

    def test_the_longest_name_is_taken(self):
        name = "a" * map_stamp_tools.MAX_STAMP_NAME
        self.stamp(op="copy", name=name, map_id=1, x=0, y=0, width=2,
                   height=2)
        self.assertEqual(self.stamp(op="list")["stamps"][0]["name"], name)

    def test_a_non_string_name_is_refused(self):
        for value in (5, True, 1.5, ["a"], {"a": 1}):
            with self.subTest(value=value):
                with self.assertRaises(ToolError):
                    self.stamp(op="copy", name=value, map_id=1, x=0, y=0,
                               width=2, height=2)

    def link(self, target, name):
        """A directory link from the project to `target`, or skip."""
        path = self.tmp / "project" / name
        try:
            os.symlink(target, path, target_is_directory=True)
            self.addCleanup(self.drop_link, path)
            return path
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt":
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(path),
                                     str(target)], capture_output=True)
            if result.returncode == 0:
                self.addCleanup(self.drop_link, path)
                return path
        self.skipTest("this machine will not make a directory link")

    @staticmethod
    def drop_link(path):
        """Remove the link itself, never what it leads to."""
        for remove in (os.unlink, os.rmdir):
            try:
                remove(path)
                return
            except OSError:
                continue

    def test_a_work_folder_that_leads_outside_is_refused_before_anything_is_made(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        self.link(outside, ".rpgvxace-mcp")
        with self.assertRaises(Exception) as caught:
            self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                       height=2)
        self.assertIn("outside", str(caught.exception))
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_stamps_folder_that_leads_outside_is_refused_before_anything_is_made(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.tmp / "project" / ".rpgvxace-mcp").mkdir()
        self.link(outside, ".rpgvxace-mcp/stamps")
        with self.assertRaises(Exception) as caught:
            self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                       height=2)
        self.assertIn("outside", str(caught.exception))
        self.assertEqual(list(outside.iterdir()), [])

    def test_a_stamp_file_that_is_a_link_is_not_followed(self):
        self.stamp(op="copy", name="real", map_id=1, x=0, y=0, width=2,
                   height=2)
        outside = self.tmp / "outside.json"
        outside.write_bytes(self.stamp_file("real").read_bytes())
        planted = self.stamp_file("planted")
        try:
            os.symlink(outside, planted)
        except (OSError, NotImplementedError):
            self.skipTest("this machine will not make a file link")
        with self.assertRaises(Exception) as caught:
            self.stamp(op="paste", name="planted", map_id=1, x=5, y=5)
        self.assertIn("link", str(caught.exception))


    def test_a_stamp_file_that_reads_as_a_link_is_refused_on_paste(self):
        """The same check, reached without needing a link this machine allows."""
        self.stamp(op="copy", name="real", map_id=1, x=0, y=0, width=2,
                   height=2)
        real = map_stamp_tools._is_reparse_point
        with mock.patch.object(
                map_stamp_tools, "_is_reparse_point",
                side_effect=lambda path: path.name == "real.json" or real(path)):
            with self.assertRaises(ToolError) as caught:
                self.stamp(op="paste", name="real", map_id=2, x=0, y=0)
        self.assertIn("is a link", str(caught.exception))
        self.assertEqual(self.rows(2, 0, 0, 2, 2)[0], [2048, 2048])

    def test_a_listed_stamp_that_reads_as_a_link_is_not_opened(self):
        self.stamp(op="copy", name="real", map_id=1, x=0, y=0, width=2,
                   height=2)
        real = map_stamp_tools._is_reparse_point
        with mock.patch.object(
                map_stamp_tools, "_is_reparse_point",
                side_effect=lambda path: path.name == "real.json" or real(path)):
            out = self.stamp(op="list")
        self.assertEqual(list(out["stamps_not_read"]), ["real"])
        self.assertIn("link", out["stamps_not_read"]["real"])
        self.assertNotIn("width", out["stamps"][0])


class TestPasteTiles(StampTest):
    def test_a_paste_reproduces_every_layer_on_another_map(self):
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=10, y=8, width=3,
                   height=2)
        out = self.stamp(op="paste", name="block", map_id=2, x=4, y=5)
        self.assertEqual(out["painted"], 24)
        for z, expected in (
                (0, [[1540, 1541, 1542], [1543, 1544, 1545]]),
                (1, [[3, 4, 5], [6, 7, 8]]),
                (2, [[10, 11, 12], [13, 14, 15]]),
                (3, [[5 << 8 | 1, 5 << 8 | 2, 7 << 8],
                     [5 << 8 | 4, 5 << 8 | 8, 0]])):
            with self.subTest(layer=z):
                self.assertEqual(self.rows(2, 4, 5, 3, 2, z), expected)

    def test_the_paste_leaves_the_cells_around_it_alone(self):
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=10, y=8, width=3,
                   height=2)
        before = self.rows(2, 0, 0, 12, 10, 0)
        self.stamp(op="paste", name="block", map_id=2, x=4, y=5,
                   reshape_edges=False)
        after = self.rows(2, 0, 0, 12, 10, 0)
        changed = {(x, y) for y in range(10) for x in range(12)
                   if before[y][x] != after[y][x]}
        self.assertEqual(changed, {(x, y) for y in (5, 6) for x in (4, 5, 6)})

    def test_with_reshape_edges_only_the_ring_around_the_paste_can_change(self):
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=10, y=8, width=3,
                   height=2)
        before = self.rows(2, 0, 0, 12, 10, 0)
        self.stamp(op="paste", name="block", map_id=2, x=4, y=5)
        after = self.rows(2, 0, 0, 12, 10, 0)
        changed = {(x, y) for y in range(10) for x in range(12)
                   if before[y][x] != after[y][x]}
        ring = {(x, y) for y in range(4, 8) for x in range(3, 8)}
        self.assertLessEqual(changed, ring)
        # And the ring does change: floor beside a different tile is no
        # longer floor with all four sides joined.
        self.assertTrue(changed - {(x, y) for y in (5, 6) for x in (4, 5, 6)})

    def test_mirror_horizontal_pastes_the_columns_reversed(self):
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=10, y=8, width=3,
                   height=2)
        self.stamp(op="paste", name="block", map_id=2, x=0, y=0,
                   mirror="horizontal")
        self.assertEqual(self.rows(2, 0, 0, 3, 2, 0),
                         [[1542, 1541, 1540], [1545, 1544, 1543]])
        # The cells swap columns and each cell's shadow quarters swap sides
        # with them, so a top-left quarter that moved to the right edge of
        # the block is a top-right quarter; the region stays with its cell.
        self.assertEqual(self.rows(2, 0, 0, 3, 2, 3),
                         [[7 << 8, 5 << 8 | 1, 5 << 8 | 2],
                          [0, 5 << 8 | 4, 5 << 8 | 8]])

    def test_rotate_90_pastes_a_taller_block(self):
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=10, y=8, width=3,
                   height=2, layers=[0])
        out = self.stamp(op="paste", name="block", map_id=2, x=1, y=1,
                         rotate=90)
        self.assertEqual((out["placed"]["width"], out["placed"]["height"]),
                         (2, 3))
        self.assertEqual(self.rows(2, 1, 1, 2, 3, 0),
                         [[1543, 1540], [1544, 1541], [1545, 1542]])

    def test_a_paste_that_runs_past_the_edge_is_clipped_and_says_so(self):
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=10, y=8, width=3,
                   height=2, layers=[0])
        out = self.stamp(op="paste", name="block", map_id=2, x=10, y=9)
        # Columns 10 and 11 of row 9 fit; everything else is outside.
        self.assertEqual(out["painted"], 2)
        self.assertEqual(out["clipped_cells"], 4)
        self.assertTrue(out["cells_truncated"])
        self.assertIn("outside the map", out["cells_note"])
        self.assertEqual(self.rows(2, 10, 9, 2, 1, 0), [[1540, 1541]])

    def test_a_paste_that_clips_nothing_says_nothing_of_clipping(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        out = self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
        self.assertNotIn("cells_truncated", out)
        self.assertEqual(out["clipped_cells"], 0)

    def test_an_origin_off_the_map_is_refused(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name="block", map_id=2, x=12, y=0)
        self.assertIn("at most 11", str(caught.exception))

    def test_a_dry_run_changes_nothing_and_reads_as_a_preview(self):
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=10, y=8, width=3,
                   height=2)
        before = self.map_bytes(2)
        out = self.stamp(op="paste", name="block", map_id=2, x=4, y=5,
                         dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_paint"], 24)
        self.assertNotIn("painted", out)
        self.assertEqual(self.map_bytes(2), before)
        self.assertEqual(self.rows(2, 4, 5, 3, 2, 0)[0], [2048] * 3)

    def test_the_write_is_one_change_in_the_log(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
        entries = [e for e in self.reopen().project.recent_changes(50)
                   if e.get("tool") == "map_stamp"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["file"], "Map002" + self.extension)

    def test_a_missing_stamp_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name="ghost", map_id=2, x=0, y=0)
        self.assertIn("no stamp named 'ghost'", str(caught.exception))


def landed(cells, rect, at, mirror="none", rotate=0):
    """Where absolute `cells` of `rect` land when the rect is pasted at `at`.

    Written out again here, not borrowed from the module, so the autotile
    tests below are not checking `Orientation` against itself.
    """
    x0, y0, width, height = rect
    out = []
    for ax, ay in cells:
        x, y, w, h = ax - x0, ay - y0, width, height
        if mirror in ("horizontal", "both"):
            x = w - 1 - x
        if mirror in ("vertical", "both"):
            y = h - 1 - y
        for _ in range(rotate // 90):
            x, y, w, h = h - 1 - y, x, h, w
        out.append((at[0] + x, at[1] + y))
    return out


class TestAutotilesFollowTheTurn(StampTest):
    """The expected cells come from `map_paint`, not from the code under test.

    A layout is painted with `map_paint` on one map, copied, pasted turned, and
    compared with the same layout *painted directly* in its turned position on
    a fresh copy of the project: two routes to one set of autotile ids.
    """

    # An L of kind-17 ground on the kind-16 field: three down, one across.
    SHAPE = [(2, 2), (2, 3), (2, 4), (3, 4)]
    RECT = (1, 1, 4, 5)  # the L and a ring of one cell around it

    def paint(self, cells):
        self.call("map_paint", op="paint", map_id=1, kind="A2:1",
                  cells=[list(cell) for cell in cells])

    def window(self, x, y, width, height):
        return self.rows(1, x, y, width, height, 0)

    def copy_ell(self, rect=None, name="ell"):
        x, y, width, height = rect or self.RECT
        self.stamp(op="copy", name=name, map_id=1, x=x, y=y, width=width,
                   height=height)

    def test_every_turn_matches_the_layout_painted_in_that_turn(self):
        cases = [("horizontal", 0), ("vertical", 0), ("both", 0), ("none", 90),
                 ("none", 180), ("none", 270), ("horizontal", 90),
                 ("vertical", 270)]
        for mirror, rotate in cases:
            with self.subTest(mirror=mirror, rotate=rotate):
                self.setUp()
                self.paint(self.SHAPE)
                self.copy_ell()
                at = (10, 3)
                self.stamp(op="paste", name="ell", map_id=1, x=at[0], y=at[1],
                           mirror=mirror, rotate=rotate)
                width, height = (5, 4) if rotate in (90, 270) else (4, 5)
                pasted = self.window(at[0] - 1, at[1] - 1, width + 2,
                                     height + 2)
                self.setUp()
                self.paint(landed(self.SHAPE, self.RECT, at, mirror, rotate))
                direct = self.window(at[0] - 1, at[1] - 1, width + 2,
                                     height + 2)
                self.assertEqual(pasted, direct)

    def test_the_comparison_can_fail(self):
        """A paste that skipped the re-shape differs from the direct paint."""
        self.paint(self.SHAPE)
        self.copy_ell()
        self.stamp(op="paste", name="ell", map_id=1, x=10, y=3,
                   mirror="horizontal")
        pasted = self.window(9, 2, 6, 7)
        self.setUp()
        self.paint(self.SHAPE)
        self.copy_ell()
        self.stamp(op="paste", name="ell", map_id=1, x=10, y=3)
        unturned = self.window(9, 2, 6, 7)
        self.assertNotEqual(pasted, unturned)

    def test_a_symmetric_layout_reshapes_to_the_same_cells_mirrored(self):
        self.call("map_paint", op="fill", map_id=1, kind="A2:1", x=3, y=3,
                  width=5, height=4)
        self.stamp(op="copy", name="slab", map_id=1, x=2, y=2, width=7,
                   height=6)
        self.stamp(op="paste", name="slab", map_id=1, x=11, y=7)
        plain = self.window(10, 6, 9, 8)
        for mirror in ("horizontal", "vertical", "both"):
            with self.subTest(mirror=mirror):
                self.stamp(op="paste", name="slab", map_id=1, x=11, y=7,
                           mirror=mirror)
                self.assertEqual(self.window(10, 6, 9, 8), plain)

    def test_a_symmetric_layout_stays_itself_when_turned_a_half(self):
        self.call("map_paint", op="fill", map_id=1, kind="A2:1", x=3, y=3,
                  width=5, height=4)
        self.stamp(op="copy", name="slab", map_id=1, x=2, y=2, width=7,
                   height=6)
        self.stamp(op="paste", name="slab", map_id=1, x=11, y=7)
        plain = self.window(10, 6, 9, 8)
        self.stamp(op="paste", name="slab", map_id=1, x=11, y=7, rotate=180)
        self.assertEqual(self.window(10, 6, 9, 8), plain)

    def test_a_round_trip_through_two_mirrors_restores_the_patch(self):
        self.paint(self.SHAPE)
        original = self.window(1, 1, 4, 5)
        self.copy_ell()
        self.stamp(op="paste", name="ell", map_id=1, x=12, y=2,
                   mirror="horizontal")
        self.stamp(op="copy", name="back", map_id=1, x=12, y=2, width=4,
                   height=5)
        self.stamp(op="paste", name="back", map_id=1, x=12, y=9,
                   mirror="horizontal")
        self.assertEqual(self.window(12, 9, 4, 5), original)

    def test_four_quarter_turns_restore_the_patch(self):
        self.paint(self.SHAPE)
        original = self.window(1, 1, 4, 5)
        self.copy_ell(name="s0")
        size = (4, 5)
        spots = [(8, 1), (14, 1), (8, 8), (14, 8)]
        for step, at in enumerate(spots):
            self.stamp(op="paste", name="s%d" % step, map_id=1, x=at[0],
                       y=at[1], rotate=90)
            size = (size[1], size[0])
            self.stamp(op="copy", name="s%d" % (step + 1), map_id=1, x=at[0],
                       y=at[1], width=size[0], height=size[1])
        self.assertEqual(self.window(spots[-1][0], spots[-1][1], *size),
                         original)

    def test_the_ring_around_a_paste_joins_it(self):
        """reshape_edges re-shapes the neighbours; off, they keep their shape."""
        self.put(1, 0, 0, [[autotile.first_id(17)]])  # a lone kind-17 cell
        self.stamp(op="copy", name="lone", map_id=1, x=0, y=0, width=1,
                   height=1)
        self.stamp(op="paste", name="lone", map_id=1, x=8, y=8,
                   reshape_edges=False)
        self.assertEqual(self.rows(1, 7, 8, 3, 1)[0][0], 2816)
        out = self.stamp(op="paste", name="lone", map_id=1, x=8, y=8)
        self.assertNotEqual(self.rows(1, 7, 8, 3, 1)[0][0], 2816)
        self.assertGreater(out["reshaped"], 0)

    def test_without_reshape_edges_a_turned_stamp_still_reshapes_its_own_cells(self):
        self.paint(self.SHAPE)
        rect = (2, 2, 2, 3)  # just the patch, no ring
        self.copy_ell(rect)
        self.stamp(op="paste", name="ell", map_id=1, x=12, y=3,
                   mirror="horizontal", reshape_edges=False)
        pasted = self.window(12, 3, 2, 3)
        beside = self.window(14, 3, 1, 3)
        self.setUp()
        self.paint(landed(self.SHAPE, rect, (12, 3), "horizontal"))
        self.assertEqual(pasted, self.window(12, 3, 2, 3))
        # The column beside the patch is outside the rectangle: left as it
        # was, where painting the layout would have joined it.
        self.assertEqual(beside, [[2816]] * 3)
        self.assertNotEqual(self.window(14, 3, 1, 3), [[2816]] * 3)

    def test_a_stamp_pasted_unturned_keeps_its_cells(self):
        self.paint(self.SHAPE)
        original = self.window(1, 1, 4, 5)
        self.copy_ell()
        out = self.stamp(op="paste", name="ell", map_id=1, x=12, y=2)
        self.assertEqual(self.window(12, 2, 4, 5), original)
        self.assertEqual(out["reshaped"], 0)


class TestEvents(StampTest):
    def test_an_event_is_pasted_with_a_new_id_a_moved_position_and_its_name(self):
        self.stamp(op="copy", name="elder", map_id=1, x=4, y=3, width=3,
                   height=3)
        out = self.stamp(op="paste", name="elder", map_id=2, x=6, y=6)
        self.assertEqual([(e["id"], e["name"], e["x"], e["y"])
                          for e in out["created"]], [(2, "Elder", 7, 7)])
        events = self.events_of(2)
        self.assertEqual(sorted(events), [1, 2])
        pasted = self.event_json(2, 2)
        original = self.event_json(1, 1)
        self.assertEqual((pasted["id"], pasted["x"], pasted["y"]), (2, 7, 7))
        self.assertEqual(pasted["pages"], original["pages"])
        self.assertEqual(len(pasted["pages"]), 2)

    def test_an_event_with_no_name_stays_without_one(self):
        self.call("event_edit", map_id=1, ops=[
            {"op": "update", "event_id": 1, "name": ""}])
        self.stamp(op="copy", name="nameless", map_id=1, x=5, y=4, width=1,
                   height=1)
        out = self.stamp(op="paste", name="nameless", map_id=2, x=6, y=6)
        self.assertEqual(out["created"][0]["name"], "")
        self.assertEqual(self.event_json(2, 2)["name"], "")

    def test_the_chest_in_the_other_map_is_untouched(self):
        self.stamp(op="copy", name="elder", map_id=1, x=4, y=3, width=3,
                   height=3)
        before = self.event_json(2, 1)
        self.stamp(op="paste", name="elder", map_id=2, x=6, y=6)
        self.assertEqual(self.event_json(2, 1), before)

    def test_new_ids_fill_the_lowest_free_slots_in_order(self):
        self.call("event_edit", map_id=2, ops=[
            {"op": "create", "event_id": 3, "x": 0, "y": 0},
            {"op": "create", "event_id": 5, "x": 1, "y": 0}])
        self.call("event_edit", map_id=1, ops=[
            {"op": "create", "x": 6, "y": 4, "name": "Third"}])
        self.stamp(op="copy", name="trio", map_id=1, x=4, y=3, width=4,
                   height=3)
        out = self.stamp(op="paste", name="trio", map_id=2, x=4, y=5)
        self.assertEqual([e["id"] for e in out["created"]], [2, 4])
        self.assertEqual([e["name"] for e in out["created"]],
                         ["Elder", "Third"])

    def test_include_events_false_leaves_them_behind_on_copy_and_paste(self):
        out = self.stamp(op="copy", name="bare", map_id=1, x=4, y=3, width=3,
                         height=3, include_events=False)
        self.assertEqual(out["stamp"]["events"], 0)
        self.stamp(op="copy", name="full", map_id=1, x=4, y=3, width=3,
                   height=3)
        out = self.stamp(op="paste", name="full", map_id=2, x=6, y=6,
                         include_events=False)
        self.assertEqual(out["created"], [])
        self.assertEqual(sorted(self.events_of(2)), [1])
        self.assertIn("include_events=false", out["events_note"])

    def test_an_event_landing_on_another_is_skipped_and_never_overwrites(self):
        self.stamp(op="copy", name="elder", map_id=1, x=4, y=3, width=3,
                   height=3)
        before = self.event_json(2, 1)
        out = self.stamp(op="paste", name="elder", map_id=2, x=2, y=2)
        # (5, 4) -> (3, 3): the Chest is there.
        self.assertEqual(out["created"], [])
        self.assertEqual(len(out["events_skipped"]), 1)
        skipped = out["events_skipped"][0]
        self.assertEqual((skipped["name"], skipped["x"], skipped["y"]),
                         ("Elder", 3, 3))
        self.assertIn("already holds event 1", skipped["reason"])
        self.assertTrue(out["events_truncated"])
        self.assertEqual(self.event_json(2, 1), before)
        self.assertEqual(sorted(self.events_of(2)), [1])

    def test_an_event_past_the_edge_is_skipped_with_the_reason(self):
        # The Elder is the stamp's left cell; mirrored it is the right one.
        self.stamp(op="copy", name="wide", map_id=1, x=5, y=4, width=3,
                   height=1)
        out = self.stamp(op="paste", name="wide", map_id=2, x=10, y=9,
                         mirror="horizontal")
        self.assertEqual(out["created"], [])
        skipped = out["events_skipped"][0]
        self.assertEqual((skipped["x"], skipped["y"]), (12, 9))
        self.assertIn("outside the map", skipped["reason"])
        self.assertEqual(sorted(self.events_of(2)), [1])
        # Unmirrored it is on the map, at the left of the two cells that fit.
        out = self.stamp(op="paste", name="wide", map_id=2, x=10, y=9)
        self.assertEqual([(e["x"], e["y"]) for e in out["created"]],
                         [(10, 9)])

    def test_events_follow_a_mirror(self):
        self.stamp(op="copy", name="left", map_id=1, x=5, y=4, width=3,
                   height=2)
        out = self.stamp(op="paste", name="left", map_id=2, x=3, y=6,
                         mirror="horizontal")
        self.assertEqual((out["created"][0]["x"], out["created"][0]["y"]),
                         (5, 6))
        out = self.stamp(op="paste", name="left", map_id=2, x=3, y=1,
                         mirror="vertical")
        self.assertEqual((out["created"][0]["x"], out["created"][0]["y"]),
                         (3, 2))

    def test_events_follow_a_rotation(self):
        self.stamp(op="copy", name="left", map_id=1, x=5, y=4, width=3,
                   height=2)
        # Elder at stamp (0, 0); a quarter turn puts it at (height-1, 0).
        out = self.stamp(op="paste", name="left", map_id=2, x=2, y=5,
                         rotate=90)
        self.assertEqual((out["created"][0]["x"], out["created"][0]["y"]),
                         (3, 5))

    def test_a_mirror_turns_the_way_the_event_faces(self):
        self.call("event_edit", map_id=1, ops=[
            {"op": "set_page", "event_id": 1, "page": 0,
             "patch": {"graphic": {"__class__": "RPG::Event::Page::Graphic",
                                   "tile_id": 0,
                                   "character_name": "People1",
                                   "character_index": 2, "direction": 4,
                                   "pattern": 0}}}])
        self.stamp(op="copy", name="elder", map_id=1, x=5, y=4, width=1,
                   height=1)
        self.stamp(op="paste", name="elder", map_id=2, x=6, y=6,
                   mirror="horizontal")
        self.assertEqual(
            self.event_json(2, 2)["pages"][0]["graphic"]["direction"], 6)
        self.stamp(op="paste", name="elder", map_id=2, x=7, y=7, rotate=90)
        # Facing left, turned a quarter clockwise, faces up.
        self.assertEqual(
            self.event_json(2, 3)["pages"][0]["graphic"]["direction"], 8)

    def test_move_route_commands_survive_with_their_step_lines(self):
        route = {"__class__": "RPG::MoveRoute", "repeat": False,
                 "skippable": False, "wait": True,
                 "list": [{"code": 1, "parameters": []},
                          {"code": 0, "parameters": []}]}
        self.call("event_edit", map_id=1, ops=[
            {"op": "append_commands", "event_id": 1, "page": 0,
             "commands": [{"raw": {"code": 205, "parameters": [0, route]}}]}])
        self.stamp(op="copy", name="elder", map_id=1, x=5, y=4, width=1,
                   height=1)
        self.stamp(op="paste", name="elder", map_id=2, x=6, y=6)
        self.assertEqual(self.event_json(2, 2)["pages"][0]["list"],
                         self.event_json(1, 1)["pages"][0]["list"])

    def test_a_stamp_with_more_events_than_the_ceiling_is_refused(self):
        original = map_stamp_tools.MAX_STAMP_EVENTS
        map_stamp_tools.MAX_STAMP_EVENTS = 1
        self.addCleanup(setattr, map_stamp_tools, "MAX_STAMP_EVENTS", original)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="two", map_id=1, x=0, y=0, width=20,
                       height=15)
        self.assertIn("2 events", str(caught.exception))
        self.assertIn("include_events", str(caught.exception))
        self.assertFalse(self.stamp_file("two").exists())


class TestPositionDependentCommands(StampTest):
    def add(self, map_id, x, y, name, commands):
        out = self.call("event_edit", map_id=map_id, ops=[
            {"op": "create", "x": x, "y": y, "name": name,
             "commands": commands}])
        return out["applied"][0]["event_id"]

    def raw(self, code, *parameters):
        return {"raw": {"code": code, "parameters": list(parameters)}}

    def flagged(self, out):
        return {(row["name"], row["code"]) for row in
                out.get("position_dependent", [])}

    def test_a_plain_event_is_not_flagged(self):
        self.stamp(op="copy", name="elder", map_id=1, x=4, y=3, width=3,
                   height=3)
        out = self.stamp(op="paste", name="elder", map_id=2, x=6, y=6)
        self.assertEqual(out.get("position_dependent", []), [])

    def test_a_transfer_to_another_map_is_not_flagged_but_to_the_same_one_is(self):
        # The door sends the player to map 2.
        out = self.stamp(op="copy", name="door", map_id=1, x=8, y=2, width=1,
                         height=1)
        self.assertEqual(out.get("position_dependent", []), [])
        # Pasted into map 2 it targets the map it now stands on.
        out = self.stamp(op="paste", name="door", map_id=2, x=9, y=9)
        self.assertEqual(self.flagged(out), {("Cave Entrance", 201)})
        row = out["position_dependent"][0]
        self.assertEqual(row["event"], out["created"][0]["id"])
        self.assertIn("map 2", row["why"])

    def test_a_transfer_to_the_map_the_stamp_came_from_is_flagged_on_copy(self):
        self.add(1, 12, 12, "Loop", [self.raw(201, 0, 1, 3, 3, 2, 0)])
        out = self.stamp(op="copy", name="loop", map_id=1, x=12, y=12,
                         width=1, height=1)
        self.assertEqual(self.flagged(out), {("Loop", 201)})

    def test_set_event_location_is_always_flagged(self):
        self.add(1, 12, 12, "Mover", [self.raw(203, 0, 0, 7, 7, 0)])
        out = self.stamp(op="copy", name="mover", map_id=1, x=12, y=12,
                         width=1, height=1)
        self.assertEqual(self.flagged(out), {("Mover", 203)})
        self.assertIn("absolute", out["position_dependent"][0]["why"])
        out = self.stamp(op="paste", name="mover", map_id=2, x=1, y=1)
        self.assertEqual(self.flagged(out), {("Mover", 203)})

    def test_a_reference_to_an_event_by_number_is_flagged(self):
        self.add(1, 12, 12, "Caller", [self.raw(212, 2, 5),
                                       self.raw(213, 0, 1)])
        out = self.stamp(op="copy", name="caller", map_id=1, x=12, y=12,
                         width=1, height=1)
        # 212 names event 2; 213 names "this event" (0), which is safe.
        self.assertEqual(self.flagged(out), {("Caller", 212)})
        self.assertIn("event 2", out["position_dependent"][0]["why"])

    def test_a_directional_move_route_is_flagged_only_when_the_stamp_is_turned(self):
        route = {"__class__": "RPG::MoveRoute", "repeat": False,
                 "skippable": False, "wait": False,
                 "list": [{"code": 4, "parameters": []},
                          {"code": 0, "parameters": []}]}
        self.add(1, 12, 12, "Walker", [self.raw(205, 0, route)])
        self.stamp(op="copy", name="walker", map_id=1, x=12, y=12, width=1,
                   height=1)
        out = self.stamp(op="paste", name="walker", map_id=2, x=1, y=1)
        self.assertEqual(out.get("position_dependent", []), [])
        out = self.stamp(op="paste", name="walker", map_id=2, x=2, y=1,
                         mirror="horizontal")
        self.assertEqual(self.flagged(out), {("Walker", 205)})
        self.assertIn("not mirrored", out["position_dependent"][0]["why"])

    def test_the_flag_list_is_bounded_and_says_so(self):
        original = map_stamp_tools.MAX_STAMP_FLAGS
        map_stamp_tools.MAX_STAMP_FLAGS = 2
        self.addCleanup(setattr, map_stamp_tools, "MAX_STAMP_FLAGS", original)
        self.add(1, 12, 12, "Many", [self.raw(203, 0, 0, 1, 1, 0)] * 5)
        out = self.stamp(op="copy", name="many", map_id=1, x=12, y=12,
                         width=1, height=1)
        self.assertEqual(len(out["position_dependent"]), 2)
        self.assertTrue(out["position_dependent_truncated"])


class TestAnotherEngine(StampTest):
    def test_a_stamp_from_another_engine_is_refused(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        self.edit_file("block", lambda data: data.update(engine="vx"))
        before = self.map_bytes(2)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
        message = str(caught.exception)
        self.assertIn("vx_ace", message)
        self.assertIn("'vx'", message)
        self.assertEqual(self.map_bytes(2), before)


class TestOnVX(StampTest):
    engine = "vx"

    def test_a_plain_stamp_works_and_a_turn_is_refused(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        data = json.loads(self.stamp_file("block").read_text("utf-8"))
        self.assertEqual(data["engine"], "vx")
        self.assertEqual(data["layers"], [0, 1, 2])
        self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name="block", map_id=2, x=0, y=0,
                       mirror="horizontal")
        self.assertIn("autotile", str(caught.exception))
        with self.assertRaises(ToolError):
            self.stamp(op="paste", name="block", map_id=2, x=0, y=0,
                       rotate=90)

    def test_layer_three_does_not_exist(self):
        with self.assertRaises(ToolError):
            self.stamp(op="copy", name="x", map_id=1, x=0, y=0, width=2,
                       height=2, layers=[3])


class TestOnXP(StampTest):
    engine = "xp"

    def test_a_stamp_round_trips_with_its_event(self):
        self.stamp(op="copy", name="block", map_id=2, x=2, y=2, width=3,
                   height=3)
        out = self.stamp(op="paste", name="block", map_id=1, x=10, y=10)
        self.assertEqual(len(out["created"]), 1)


class TestAReadOnlyServer(StampTest):
    read_only = True

    def test_nothing_is_written_but_a_preview_and_a_list_work(self):
        out = self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                         height=2, dry_run=True)
        self.assertTrue(out["dry_run"])
        with self.assertRaises(Exception) as caught:
            self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                       height=2)
        self.assertIn("read-only", str(caught.exception))
        self.assertEqual(self.stamp(op="list")["stamps"], [])
        with self.assertRaises(Exception) as caught:
            self.stamp(op="delete", name="block")
        self.assertIn("block", str(caught.exception))


class TestAStampIsUntrustedInput(StampTest):
    """Every hand edit is refused by name and leaves the map byte-identical."""

    def setUp(self):
        super().setUp()
        self.make_pattern()
        self.stamp(op="copy", name="block", map_id=1, x=4, y=3, width=7,
                   height=6)
        self.before = self.map_bytes(2)
        self.pristine = self.stamp_file("block").read_bytes()

    def refuse(self, change, expect, name="block"):
        # Each edit starts from the stamp as copied, not from the last edit.
        self.stamp_file("block").write_bytes(self.pristine)
        self.edit_file(name, change)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name=name, map_id=2, x=0, y=0)
        self.assertIn(expect, str(caught.exception))
        self.assertIn("'%s'" % name, str(caught.exception))
        self.assertEqual(self.map_bytes(2), self.before)

    def test_the_format_and_version_are_checked(self):
        self.refuse(lambda d: d.update(format="something"), "format")
        self.refuse(lambda d: d.update(version=2), "version")
        self.refuse(lambda d: d.pop("version"), "version")

    def test_a_key_nobody_wrote_is_refused(self):
        self.refuse(lambda d: d.update(extra=1), "extra")

    def test_the_dimensions_must_agree_with_the_cells(self):
        self.refuse(lambda d: d["cells"]["0"].pop(), "cells")
        self.refuse(lambda d: d.update(width=8), "cells")

    def test_dimensions_are_bounded(self):
        for value in (0, -1, True, 1.5, "7", None, 10 ** 30):
            with self.subTest(value=value):
                self.refuse(lambda d, v=value: d.update(width=v), "width")
        limit = map_stamp_tools.MAX_STAMP_CELLS

        def too_many_cells(d):
            """Each side is in range and every array matches: only the
            product is over the ceiling."""
            d.update(width=limit, height=2)
            d["cells"] = {z: [0] * (2 * limit) for z in d["cells"]}
        self.refuse(too_many_cells, "above the %d" % limit)

    def test_every_cell_is_checked_against_the_tile_ceiling(self):
        self.refuse(lambda d: d["cells"]["0"].__setitem__(3, 65536), "65535")
        self.refuse(lambda d: d["cells"]["0"].__setitem__(3, -1), "-1")
        self.refuse(lambda d: d["cells"]["0"].__setitem__(3, True), "whole")
        self.refuse(lambda d: d["cells"]["0"].__setitem__(3, 1.5), "whole")
        self.refuse(lambda d: d["cells"]["0"].__setitem__(3, "5"), "whole")
        self.refuse(lambda d: d["cells"]["0"].__setitem__(3, 10 ** 400),
                    "65535")

    def test_a_layer_three_word_may_set_only_region_and_shadow_bits(self):
        self.refuse(lambda d: d["cells"]["3"].__setitem__(2, 0x4000),
                    "region")

    def test_the_cell_message_names_the_place(self):
        self.edit_file("block", lambda d: d["cells"]["1"].__setitem__(9, 99999))
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
        # Index 9 of a 7-wide stamp is column 2 of row 1.
        self.assertIn("layer 1", str(caught.exception))
        self.assertIn("(2, 1)", str(caught.exception))

    def test_the_layers_list_is_checked(self):
        self.refuse(lambda d: d.update(layers=[]), "layers")
        self.refuse(lambda d: d.update(layers=[0, 0, 1, 2, 3]), "layers")
        self.refuse(lambda d: d.update(layers=[0, 9]), "layers")
        self.refuse(lambda d: d.update(layers="0"), "layers")
        self.refuse(lambda d: d["cells"].pop("0"), "layer 0")
        self.refuse(lambda d: d["cells"].update({"7": [0] * 42}), "layers")

    def test_events_are_bounded_by_count_and_position(self):
        self.refuse(lambda d: d.update(
            events=d["events"] * (map_stamp_tools.MAX_STAMP_EVENTS + 1)),
            str(map_stamp_tools.MAX_STAMP_EVENTS))
        self.refuse(lambda d: d["events"][0].update(x=99), "inside")
        self.refuse(lambda d: d["events"][0].update(y=-1), "inside")
        self.refuse(lambda d: d["events"][0].update(name=5), "name")
        self.refuse(lambda d: d["events"][0].update(pages=[]), "pages")
        self.refuse(lambda d: d["events"][0].update(
            pages=d["events"][0]["pages"] * map_stamp_tools.MAX_STAMP_PAGES),
            "pages")

    def test_an_event_payload_goes_through_the_path_event_edit_uses(self):
        def bad_command(d):
            d["events"][0]["pages"][0]["commands"] = [{"nonsense": 1}]
        self.refuse(bad_command, "unrecognised")

        def list_patch(d):
            d["events"][0]["pages"][0]["patch"]["list"] = []
        self.refuse(list_patch, "list")

        def unknown_field(d):
            d["events"][0]["pages"][0]["patch"]["trigggger"] = 3
        self.refuse(unknown_field, "trigggger")

        def too_long(d):
            d["events"][0]["pages"][0]["commands"] = (
                [{"wait": 1}] * (map_stamp_tools.MAX_STAMP_PAGE_COMMANDS + 1))
        self.refuse(too_long, str(map_stamp_tools.MAX_STAMP_PAGE_COMMANDS))

        def bad_raw(d):
            d["events"][0]["pages"][0]["commands"] = [
                {"raw": {"code": 10 ** 6, "parameters": []}}]
        self.refuse(bad_raw, "raw.code")

    def test_a_file_over_the_byte_ceiling_is_refused_before_it_is_parsed(self):
        path = self.stamp_file("block")
        path.write_bytes(b"[" * (map_stamp_tools.MAX_STAMP_FILE_BYTES + 1))
        with mock.patch.object(map_stamp_tools, "_parse_json",
                               side_effect=AssertionError("parsed")):
            with self.assertRaises(ToolError) as caught:
                self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
        self.assertIn("bytes", str(caught.exception))
        self.assertIn(str(map_stamp_tools.MAX_STAMP_FILE_BYTES),
                      str(caught.exception))
        self.assertEqual(self.map_bytes(2), self.before)

    def test_text_that_is_not_json_is_refused(self):
        for raw in (b"", b"{", b"\xff\xfe", b"[1,2", b"NaN", b'{"a": Infinity}',
                    b"[" * 100000, b'"' + b"x" * 100 + b'"', b"[]", b"null",
                    b"5"):
            with self.subTest(raw=raw[:20]):
                self.stamp_file("block").write_bytes(raw)
                with self.assertRaises(ToolError) as caught:
                    self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
                self.assertIn("'block'", str(caught.exception))
                self.assertEqual(self.map_bytes(2), self.before)

    def test_a_number_of_a_thousand_digits_is_a_refusal_not_a_crash(self):
        self.stamp_file("block").write_bytes(
            b'{"format": "x", "width": ' + b"9" * 6000 + b"}")
        with self.assertRaises(ToolError):
            self.stamp(op="paste", name="block", map_id=2, x=0, y=0)

    def test_a_paste_that_fails_after_writing_tiles_leaves_the_map_clean(self):
        """The cached map is rolled back, not just the file left unwritten."""
        def explode(*_args):
            raise RuntimeError("fell over after the tiles were written")
        original = map_stamp_tools.reshape
        map_stamp_tools.reshape = explode
        self.addCleanup(setattr, map_stamp_tools, "reshape", original)
        with self.assertRaises(RuntimeError):
            self.stamp(op="paste", name="block", map_id=2, x=0, y=0)
        map_stamp_tools.reshape = original
        # Same project, same cache: nothing of the attempt is left in it.
        self.assertEqual(self.rows(2, 0, 0, 7, 6, 0), [[2048] * 7] * 6)
        events = self.project.load_map(2).get("events")
        self.assertEqual(sorted(key for key, _ in events.pairs), [1])
        self.assertEqual(self.map_bytes(2), self.before)


class TestTheWalksAreBudgeted(StampTest):
    def short_budget(self, steps):
        return mock.patch.object(map_stamp_tools, "WalkBudget",
                                 lambda: WalkBudget(steps))

    def test_a_copy_that_cannot_see_every_event_is_refused_not_short(self):
        with self.short_budget(1):
            with self.assertRaises(ToolError) as caught:
                self.stamp(op="copy", name="all", map_id=1, x=0, y=0,
                           width=20, height=15)
        self.assertIn("incomplete", str(caught.exception))
        self.assertFalse(self.stamp_file("all").exists())

    def test_a_paste_that_cannot_see_every_event_is_refused_and_writes_nothing(self):
        self.stamp(op="copy", name="all", map_id=1, x=0, y=0, width=12,
                   height=10)
        before = self.map_bytes(1)
        with self.short_budget(1):
            with self.assertRaises(ToolError) as caught:
                self.stamp(op="paste", name="all", map_id=1, x=6, y=5)
        self.assertIn("incomplete", str(caught.exception))
        self.assertEqual(self.rows(1, 6, 5, 2, 1)[0], [2816, 2816])
        self.assertEqual(self.map_bytes(1), before)

    def test_a_paste_that_would_reshape_too_much_is_refused_whole(self):
        self.call("map_paint", op="fill", map_id=1, kind="A2:1", x=3, y=3,
                  width=5, height=4)
        self.stamp(op="copy", name="slab", map_id=1, x=2, y=2, width=7,
                   height=6)
        original = map_stamp_tools.MAX_STAMP_RESHAPE_CELLS
        map_stamp_tools.MAX_STAMP_RESHAPE_CELLS = 10
        self.addCleanup(setattr, map_stamp_tools, "MAX_STAMP_RESHAPE_CELLS",
                        original)
        before = self.map_bytes(1)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name="slab", map_id=1, x=11, y=7)
        self.assertIn("re-shape", str(caught.exception))
        self.assertIn("10", str(caught.exception))
        # Tiles went onto the cached map before the refusal; none is left.
        self.assertEqual(self.rows(1, 11, 7, 1, 1)[0], [2816])
        self.assertEqual(self.map_bytes(1), before)


class TestTheCeilingsAreEnforcedAndAdvertised(StampTest):
    def lower(self, name, value):
        original = getattr(map_stamp_tools, name)
        setattr(map_stamp_tools, name, value)
        self.addCleanup(setattr, map_stamp_tools, name, original)

    def test_every_ceiling_is_in_the_description(self):
        text = REGISTRY["map_stamp"].description.replace(",", "")
        names = [name for name in dir(map_stamp_tools)
                 if name.startswith("MAX_STAMP")]
        self.assertGreaterEqual(len(names), 8)
        for name in names:
            with self.subTest(ceiling=name):
                value = str(getattr(map_stamp_tools, name))
                self.assertRegex(text, r"(?<![0-9])%s(?![0-9])" % value)

    def test_too_many_cells_is_refused_on_copy(self):
        self.lower("MAX_STAMP_CELLS", 11)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="big", map_id=1, x=0, y=0, width=4,
                       height=3)
        self.assertIn("12 cells", str(caught.exception))
        self.assertIn("11", str(caught.exception))
        self.stamp(op="copy", name="ok", map_id=1, x=0, y=0, width=11,
                   height=1)

    def test_too_many_stamps_is_refused_on_copy(self):
        self.lower("MAX_STAMPS", 2)
        for name in ("a", "b"):
            self.stamp(op="copy", name=name, map_id=1, x=0, y=0, width=1,
                       height=1)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="c", map_id=1, x=0, y=0, width=1,
                       height=1)
        self.assertIn("2 stamps", str(caught.exception))
        self.assertFalse(self.stamp_file("c").exists())
        self.stamp(op="delete", name="a")
        self.stamp(op="copy", name="c", map_id=1, x=0, y=0, width=1, height=1)

    def test_a_stamp_too_big_for_its_file_is_refused_on_copy(self):
        self.lower("MAX_STAMP_FILE_BYTES", 300)
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="big", map_id=1, x=0, y=0, width=10,
                       height=10)
        self.assertIn("300", str(caught.exception))
        self.assertFalse(self.stamp_dir().exists()
                         and any(self.stamp_dir().iterdir()))

    def test_the_listing_is_bounded_and_says_so(self):
        for name in ("a", "b", "c"):
            self.stamp(op="copy", name=name, map_id=1, x=0, y=0, width=1,
                       height=1)
        self.lower("MAX_STAMPS", 2)
        out = self.stamp(op="list")
        self.assertEqual([row["name"] for row in out["stamps"]], ["a", "b"])
        self.assertTrue(out["stamps_truncated"])
        self.assertIn("2", out["stamps_note"])

    def test_a_file_beyond_the_listing_budget_is_named_not_opened(self):
        self.stamp(op="copy", name="a", map_id=1, x=0, y=0, width=1, height=1)
        self.stamp(op="copy", name="b", map_id=1, x=0, y=0, width=1, height=1)
        self.lower("MAX_STAMP_LIST_BYTES", 1)
        out = self.stamp(op="list")
        self.assertEqual([row["name"] for row in out["stamps"]], ["a", "b"])
        self.assertEqual(set(out["stamps_not_read"]), {"a", "b"})

    def test_a_broken_file_is_listed_as_unread_not_dropped(self):
        self.stamp(op="copy", name="a", map_id=1, x=0, y=0, width=1, height=1)
        self.stamp_file("broken").write_bytes(b"{")
        out = self.stamp(op="list")
        self.assertEqual({row["name"] for row in out["stamps"]}, {"a", "broken"})
        self.assertEqual(list(out["stamps_not_read"]), ["broken"])

    def test_files_that_are_not_stamps_are_ignored(self):
        self.stamp(op="copy", name="a", map_id=1, x=0, y=0, width=1, height=1)
        (self.stamp_dir() / "notes.txt").write_text("hi")
        (self.stamp_dir() / "bad name.json").write_text("{}")
        self.assertEqual([r["name"] for r in self.stamp(op="list")["stamps"]],
                         ["a"])


class TestArgumentsAreStrict(StampTest):
    def test_hostile_values_are_refused_not_crashed(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        hostile = [True, 1.5, "3", [3], {"a": 1}, -1, 10 ** 5000,
                   float("inf")]
        for key in ("x", "y"):
            for value in hostile:
                with self.subTest(key=key, value=label(value)):
                    with self.assertRaises(ToolError):
                        self.stamp(op="paste", name="block", map_id=2,
                                   **{"x": 0, "y": 0, key: value})
        for key in ("width", "height"):
            for value in hostile + [0]:
                with self.subTest(key=key, value=label(value)):
                    with self.assertRaises(ToolError):
                        self.stamp(op="copy", name="again", map_id=1, x=0,
                                   y=0, **{"width": 2, "height": 2,
                                           key: value})
        for value in hostile + [0, 10000]:
            with self.subTest(map_id=label(value)):
                with self.assertRaises(ToolError):
                    self.stamp(op="paste", name="block", map_id=value, x=0,
                               y=0)

    def test_mirror_and_rotate_take_only_their_words(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        for value in ("diagonal", "", 1, True, ["none"], "Horizontal"):
            with self.subTest(mirror=value):
                with self.assertRaises(ToolError) as caught:
                    self.stamp(op="paste", name="block", map_id=2, x=0, y=0,
                               mirror=value)
                self.assertIn("mirror", str(caught.exception))
        for value in (45, 360, -90, 1, "90", True, 90.5, [90], 10 ** 5000):
            with self.subTest(rotate=value if value != 10 ** 5000 else "big"):
                with self.assertRaises(ToolError) as caught:
                    self.stamp(op="paste", name="block", map_id=2, x=0, y=0,
                               rotate=value)
                self.assertIn("rotate", str(caught.exception))

    def test_layers_must_be_a_list_of_distinct_layer_numbers(self):
        for value in ("0", 0, [True], [1.5], ["0"], [0, 0], [-1], [4],
                      [10 ** 5000], [[0]], [0, 1, 2, 3, 3]):
            with self.subTest(layers=label(value)):
                with self.assertRaises(ToolError):
                    self.stamp(op="copy", name="x", map_id=1, x=0, y=0,
                               width=2, height=2, layers=value)

    def test_flags_must_be_booleans(self):
        self.stamp(op="copy", name="block", map_id=1, x=0, y=0, width=2,
                   height=2)
        for key in ("include_events", "reshape_edges", "dry_run"):
            for value in ("yes", 1, [True]):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(ToolError):
                        self.stamp(op="paste", name="block", map_id=2, x=0,
                                   y=0, **{key: value})

    def test_an_argument_the_op_does_not_read_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="list", name="x")
        self.assertIn("name", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="delete", name="x", map_id=1)
        self.assertIn("map_id", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="copy", name="x", map_id=1, mirror="both")
        self.assertIn("mirror", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.stamp(op="paste", name="x", map_id=1, x=0, y=0, width=3)
        self.assertIn("width", str(caught.exception))

    def test_required_arguments_are_named(self):
        for arguments, word in (
                ({"op": "copy", "map_id": 1}, "name"),
                ({"op": "copy", "name": "x"}, "map_id"),
                ({"op": "paste", "name": "x", "map_id": 1, "y": 0}, "x"),
                ({"op": "paste", "name": "x", "map_id": 1, "x": 0}, "y"),
                ({"op": "paste", "map_id": 1, "x": 0, "y": 0}, "name"),
                ({"op": "delete"}, "name"),
                ({}, "op")):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ToolError) as caught:
                    self.stamp(**arguments)
                self.assertIn(word, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
