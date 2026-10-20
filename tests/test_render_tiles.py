"""Tile geometry, the three autotile tables, and the pixel operations.

The table values asserted below are the ones RGSS3 itself uses (they are what
the editor's stored shape numbers index), written out here as literals rather
than recomputed, so the generator in `tiles.py` is held to the engine and not
to itself. `TestAgainstARealProject` then checks the shape *numbering* against
the neighbours of every autotile in the user's game, when that is available.
"""

import itertools
import os
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

import real_project  # noqa: E402
from rpgvxace_mcp import autotile  # noqa: E402
from rpgvxace_mcp.render import canvas, tiles  # noqa: E402
from rpgvxace_mcp.render.png import Image  # noqa: E402


def quarter_colour(qx, qy):
    return (qx * 8, qy * 6, 99, 255)


def painted_sheet(width, height, cell=16):
    """A sheet where each `cell` square is one colour naming its position."""
    image = canvas.new_surface(width, height)
    for qy in range(height // cell):
        for qx in range(width // cell):
            canvas.blend_rect(image, qx * cell, qy * cell, cell, cell,
                              quarter_colour(qx, qy)[:3], 255)
    for i in range(3, len(image.data), 4):
        image.data[i] = 255
    return image


def pixel(image, x, y):
    at = (y * image.width + x) * 4
    return tuple(image.data[at:at + 4])


class TestTheFloorTable(unittest.TestCase):
    # Entries of RGSS3's floor table, by shape number: the four quarters
    # (top-left, top-right, bottom-left, bottom-right) as (x, y) in 16-pixel
    # steps inside the 4 x 6 block.
    KNOWN = {
        0: ((2, 4), (1, 4), (2, 3), (1, 3)),
        1: ((2, 0), (1, 4), (2, 3), (1, 3)),
        2: ((2, 4), (3, 0), (2, 3), (1, 3)),
        4: ((2, 4), (1, 4), (2, 3), (3, 1)),
        8: ((2, 4), (1, 4), (2, 1), (1, 3)),
        15: ((2, 0), (3, 0), (2, 1), (3, 1)),
        16: ((0, 4), (1, 4), (0, 3), (1, 3)),
        17: ((0, 4), (3, 0), (0, 3), (1, 3)),
        20: ((2, 2), (1, 2), (2, 3), (1, 3)),
        24: ((2, 4), (3, 4), (2, 3), (3, 3)),
        28: ((2, 4), (1, 4), (2, 5), (1, 5)),
        32: ((0, 4), (3, 4), (0, 3), (3, 3)),
        33: ((2, 2), (1, 2), (2, 5), (1, 5)),
        34: ((0, 2), (1, 2), (0, 3), (1, 3)),
        36: ((2, 2), (3, 2), (2, 3), (3, 3)),
        38: ((2, 4), (3, 4), (2, 5), (3, 5)),
        40: ((0, 4), (1, 4), (0, 5), (1, 5)),
        46: ((0, 2), (3, 2), (0, 5), (3, 5)),
        47: ((0, 0), (1, 0), (0, 1), (1, 1)),
    }

    def test_there_are_48_shapes(self):
        self.assertEqual(len(tiles.FLOOR_TABLE), 48)

    def test_it_matches_the_engines_own_entries(self):
        for shape, quarters in self.KNOWN.items():
            with self.subTest(shape=shape):
                self.assertEqual(tiles.FLOOR_TABLE[shape], quarters)

    def test_no_quarter_leaves_the_block(self):
        for shape, quarters in enumerate(tiles.FLOOR_TABLE):
            for qx, qy in quarters:
                self.assertTrue(0 <= qx < 4 and 0 <= qy < 6, (shape, qx, qy))

    def test_every_shape_but_the_lone_tile_looks_different(self):
        seen = {}
        for shape, quarters in enumerate(tiles.FLOOR_TABLE[:47]):
            self.assertNotIn(quarters, seen,
                             "shapes %s and %d draw the same tile"
                             % (seen.get(quarters), shape))
            seen[quarters] = shape

    def test_the_patterns_are_every_arrangement_exactly_once(self):
        """47 = the sides open or joined, times the inner corners that show."""
        corners = {"TL": "LU", "TR": "RU", "BL": "LD", "BR": "RD"}
        universe = set()
        for count in range(5):
            for open_sides in itertools.combinations("LURD", count):
                absent = frozenset(open_sides)
                showing = [c for c, sides in corners.items()
                           if not absent & set(sides)]
                for n in range(len(showing) + 1):
                    for notches in itertools.combinations(showing, n):
                        universe.add((absent, frozenset(notches)))
        self.assertEqual(len(universe), 47)
        self.assertEqual(set(autotile.FLOOR_PATTERNS), universe)
        self.assertEqual(len(autotile.FLOOR_PATTERNS), 47)

    def test_an_edge_piece_is_used_exactly_where_that_side_is_open(self):
        """The 4 x 4 grid's outer column and rows are the borders.

        Pieces from the inner-corner tile (rows 0 and 1) are not part of that
        grid, and the test after this one says when they are used.
        """
        for shape, (absent, notches) in enumerate(autotile.FLOOR_PATTERNS):
            tl, tr, bl, br = tiles.FLOOR_TABLE[shape]
            with self.subTest(shape=shape):
                if tl[1] >= 2:
                    self.assertEqual(tl[0] == 0, "L" in absent)
                    self.assertEqual(tl[1] == 2, "U" in absent)
                if tr[1] >= 2:
                    self.assertEqual(tr[0] == 3, "R" in absent)
                    self.assertEqual(tr[1] == 2, "U" in absent)
                if bl[1] >= 2:
                    self.assertEqual(bl[0] == 0, "L" in absent)
                    self.assertEqual(bl[1] == 5, "D" in absent)
                if br[1] >= 2:
                    self.assertEqual(br[0] == 3, "R" in absent)
                    self.assertEqual(br[1] == 5, "D" in absent)

    def test_an_inner_corner_is_used_exactly_for_the_corners_that_show(self):
        for shape, (absent, notches) in enumerate(autotile.FLOOR_PATTERNS):
            drawn = {name for name, quarter in
                     zip(("TL", "TR", "BL", "BR"), tiles.FLOOR_TABLE[shape])
                     if quarter[1] < 2 and quarter[0] >= 2}
            with self.subTest(shape=shape):
                self.assertEqual(drawn, set(notches))


class TestTheWallAndWaterfallTables(unittest.TestCase):
    def test_wall_entries_the_engine_uses(self):
        known = {
            0: ((2, 2), (1, 2), (2, 1), (1, 1)),
            1: ((0, 2), (1, 2), (0, 1), (1, 1)),
            2: ((2, 0), (1, 0), (2, 1), (1, 1)),
            3: ((0, 0), (1, 0), (0, 1), (1, 1)),
            4: ((2, 2), (3, 2), (2, 1), (3, 1)),
            8: ((2, 2), (1, 2), (2, 3), (1, 3)),
            15: ((0, 0), (3, 0), (0, 3), (3, 3)),
        }
        self.assertEqual(len(tiles.WALL_TABLE), 16)
        for shape, quarters in known.items():
            self.assertEqual(tiles.WALL_TABLE[shape], quarters, shape)
        for quarters in tiles.WALL_TABLE:
            for qx, qy in quarters:
                self.assertTrue(0 <= qx < 4 and 0 <= qy < 4)
        self.assertEqual(len(set(tiles.WALL_TABLE)), 16)

    def test_waterfall_entries_the_engine_uses(self):
        self.assertEqual(tiles.WATERFALL_TABLE, (
            ((2, 0), (1, 0), (2, 1), (1, 1)),
            ((0, 0), (1, 0), (0, 1), (1, 1)),
            ((2, 0), (3, 0), (2, 1), (3, 1)),
            ((0, 0), (3, 0), (0, 1), (3, 1)),
        ))


class TestWhereATileIsOnItsSheet(unittest.TestCase):
    def test_which_sheet_each_id_band_names(self):
        wanted = {0: None, 1: "B", 255: "B", 256: "C", 511: "C", 512: "D",
                  767: "D", 768: "E", 1023: "E", 1024: None, 1535: None,
                  1536: "A5", 1663: "A5", 1664: None, 2047: None,
                  2048: "A1", 2815: "A1", 2816: "A2", 4351: "A2",
                  4352: "A3", 5887: "A3", 5888: "A4", 8191: "A4",
                  8192: None, -1: None}
        for tile_id, sheet in wanted.items():
            with self.subTest(tile_id=tile_id):
                self.assertEqual(tiles.sheet_needed(tile_id), sheet)

    def test_plain_tiles_use_the_split_halves_of_b_to_e(self):
        # ids 0-127 fill the left 8 columns, 128-255 the right 8.
        self.assertEqual(tiles.plain_source(1), ("B", 32, 0))
        self.assertEqual(tiles.plain_source(8), ("B", 0, 32))
        self.assertEqual(tiles.plain_source(127), ("B", 7 * 32, 15 * 32))
        self.assertEqual(tiles.plain_source(128), ("B", 8 * 32, 0))
        self.assertEqual(tiles.plain_source(255), ("B", 15 * 32, 15 * 32))
        self.assertEqual(tiles.plain_source(256), ("C", 0, 0))
        self.assertEqual(tiles.plain_source(768), ("E", 0, 0))
        self.assertEqual(tiles.plain_source(1536), ("A5", 0, 0))
        self.assertEqual(tiles.plain_source(1663), ("A5", 7 * 32, 15 * 32))

    def test_where_each_autotile_block_starts(self):
        a1 = lambda kind: tiles.autotile_block(2048 + kind * 48)  # noqa: E731
        self.assertEqual(a1(0)[:4], ("A1", 0, 0, "floor"))
        self.assertEqual(a1(1)[:4], ("A1", 0, 3, "floor"))
        self.assertEqual(a1(2)[:4], ("A1", 6, 0, "floor"))
        self.assertEqual(a1(3)[:4], ("A1", 6, 3, "floor"))
        self.assertEqual(a1(4)[:4], ("A1", 8, 0, "floor"))
        self.assertEqual(a1(5)[:4], ("A1", 14, 0, "waterfall"))
        self.assertEqual(a1(6)[:4], ("A1", 8, 3, "floor"))
        self.assertEqual(a1(7)[:4], ("A1", 14, 3, "waterfall"))
        self.assertEqual(a1(8)[:4], ("A1", 0, 6, "floor"))
        self.assertEqual(a1(15)[:4], ("A1", 14, 9, "waterfall"))
        a2 = lambda kind: tiles.autotile_block(2048 + kind * 48)  # noqa: E731
        self.assertEqual(a2(16)[:4], ("A2", 0, 0, "floor"))
        self.assertEqual(a2(17)[:4], ("A2", 2, 0, "floor"))
        self.assertEqual(a2(24)[:4], ("A2", 0, 3, "floor"))
        self.assertEqual(a2(47)[:4], ("A2", 14, 9, "floor"))
        self.assertEqual(a2(48)[:4], ("A3", 0, 0, "wall"))
        self.assertEqual(a2(55)[:4], ("A3", 14, 0, "wall"))
        self.assertEqual(a2(56)[:4], ("A3", 0, 2, "wall"))
        self.assertEqual(a2(79)[:4], ("A3", 14, 6, "wall"))
        self.assertEqual(a2(80)[:4], ("A4", 0, 0, "floor"))
        self.assertEqual(a2(88)[:4], ("A4", 0, 3, "wall"))
        self.assertEqual(a2(96)[:4], ("A4", 0, 5, "floor"))
        self.assertEqual(a2(104)[:4], ("A4", 0, 8, "wall"))
        self.assertEqual(a2(112)[:4], ("A4", 0, 10, "floor"))
        self.assertEqual(a2(127)[:4], ("A4", 14, 13, "wall"))

    def test_the_shape_is_the_low_part_of_the_id(self):
        self.assertEqual(tiles.autotile_block(2816 + 47)[4], 47)
        self.assertEqual(tiles.autotile_block(2816 + 48 + 5)[4], 5)

    def test_every_autotile_block_fits_its_sheet(self):
        """A1 is 24 x 18 tiles, A2 16 x 18, A3 16 x 12, A4 16 x 15."""
        size = {"A1": (24, 18), "A2": (16, 18), "A3": (16, 12), "A4": (16, 15)}
        width_of = {"floor": 2, "wall": 2, "waterfall": 2}
        height_of = {"floor": 3, "wall": 2, "waterfall": 3}
        for tile_id in range(tiles.TILE_ID_A1, tiles.TILE_ID_MAX, 48):
            sheet, bx, by, table, _ = tiles.autotile_block(tile_id)
            w, h = size[sheet]
            with self.subTest(tile_id=tile_id):
                self.assertLessEqual(bx + width_of[table], w)
                self.assertLessEqual(by + height_of[table], h)


class TestComposingATile(unittest.TestCase):
    def test_a_plain_tile_is_cut_from_its_place(self):
        sheet = painted_sheet(512, 512)
        tile = tiles.compose_tile(9, {"B": sheet})
        # id 9 = column 1, row 1 of the sheet => pixel (32, 32), quarters 2,2.
        self.assertEqual(pixel(tile, 0, 0), quarter_colour(2, 2))
        self.assertEqual(pixel(tile, 31, 31), quarter_colour(3, 3))

    def test_an_autotile_is_assembled_from_four_quarters(self):
        sheet = painted_sheet(512, 576)
        for shape in (0, 15, 33, 46, 47):
            tile = tiles.compose_tile(2816 + shape, {"A2": sheet})
            for index, (qx, qy) in enumerate(tiles.FLOOR_TABLE[shape]):
                at_x, at_y = index % 2 * 16 + 8, index // 2 * 16 + 8
                with self.subTest(shape=shape, quarter=index):
                    # block (0, 0) of A2: the quarter's own colour.
                    self.assertEqual(pixel(tile, at_x, at_y),
                                     quarter_colour(qx, qy))

    def test_a_second_kind_reads_from_its_own_block(self):
        sheet = painted_sheet(512, 576)
        tile = tiles.compose_tile(2048 + 17 * 48 + 46, {"A2": sheet})
        # kind 17: block (2, 0) tiles => quarter offset (4, 0).
        self.assertEqual(pixel(tile, 8, 8), quarter_colour(4 + 0, 2))

    def test_a_missing_sheet_or_empty_tile_draws_nothing(self):
        self.assertIsNone(tiles.compose_tile(0, {}))
        self.assertIsNone(tiles.compose_tile(5, {"B": None}))
        self.assertIsNone(tiles.compose_tile(5, {}))
        self.assertIsNone(tiles.compose_tile(1100, {"B": painted_sheet(512, 512)}))

    def test_a_sheet_too_small_for_the_tile_draws_nothing(self):
        self.assertIsNone(tiles.compose_tile(255, {"B": painted_sheet(64, 64)}))
        self.assertIsNone(tiles.compose_tile(2816, {"A2": painted_sheet(16, 16)}))


class TestPixelOperations(unittest.TestCase):
    def test_an_opaque_blit_copies(self):
        dst = canvas.new_surface(4, 4, (0, 0, 0, 255))
        src = canvas.new_surface(2, 2, (10, 20, 30, 255))
        canvas.blit(dst, src, 0, 0, 2, 2, 1, 1)
        self.assertEqual(pixel(dst, 1, 1), (10, 20, 30, 255))
        self.assertEqual(pixel(dst, 2, 2), (10, 20, 30, 255))
        self.assertEqual(pixel(dst, 0, 0), (0, 0, 0, 255))
        self.assertEqual(pixel(dst, 3, 3), (0, 0, 0, 255))

    def test_a_clear_blit_changes_nothing(self):
        dst = canvas.new_surface(2, 2, (7, 7, 7, 255))
        canvas.blit(dst, canvas.new_surface(2, 2), 0, 0, 2, 2, 0, 0)
        self.assertEqual(bytes(dst.data), bytes([7, 7, 7, 255]) * 4)

    def test_a_half_clear_pixel_is_mixed(self):
        dst = canvas.new_surface(1, 1, (0, 0, 0, 255))
        src = canvas.new_surface(1, 1, (200, 100, 50, 128))
        canvas.blit(dst, src, 0, 0, 1, 1, 0, 0)
        r, g, b, a = pixel(dst, 0, 0)
        self.assertEqual((r, g, b, a), (100, 50, 25, 255))

    def test_a_blit_is_clipped_to_both_images(self):
        dst = canvas.new_surface(3, 3, (0, 0, 0, 255))
        src = canvas.new_surface(4, 4, (9, 9, 9, 255))
        canvas.blit(dst, src, 0, 0, 4, 4, -2, -2)
        self.assertEqual(pixel(dst, 1, 1), (9, 9, 9, 255))
        self.assertEqual(pixel(dst, 2, 2), (0, 0, 0, 255))
        canvas.blit(dst, src, 0, 0, 4, 4, 2, 2)
        self.assertEqual(pixel(dst, 2, 2), (9, 9, 9, 255))
        canvas.blit(dst, src, 0, 0, 4, 4, 50, 50)      # nowhere: no error
        canvas.blit(dst, src, 9, 9, 4, 4, 0, 0)        # outside the source

    def test_blend_rect_tints_and_clips(self):
        dst = canvas.new_surface(4, 1, (100, 100, 100, 255))
        canvas.blend_rect(dst, 2, -5, 10, 10, (0, 0, 0), 128)
        self.assertEqual(pixel(dst, 1, 0), (100, 100, 100, 255))
        self.assertEqual(pixel(dst, 2, 0), (50, 50, 50, 255))
        self.assertEqual(pixel(dst, 3, 0), (50, 50, 50, 255))

    def test_resize_nearest(self):
        src = canvas.new_surface(2, 2)
        for i, colour in enumerate(((1, 0, 0, 255), (2, 0, 0, 255),
                                    (3, 0, 0, 255), (4, 0, 0, 255))):
            src.data[i * 4:i * 4 + 4] = bytes(colour)
        big = canvas.resize_nearest(src, 4, 4)
        self.assertEqual([pixel(big, x, 0)[0] for x in range(4)], [1, 1, 2, 2])
        self.assertEqual([pixel(big, 0, y)[0] for y in range(4)], [1, 1, 3, 3])
        small = canvas.resize_nearest(big, 1, 1)
        self.assertEqual(pixel(small, 0, 0)[0], 1)
        self.assertIs(canvas.resize_nearest(src, 2, 2), src)
        thin = canvas.resize_nearest(src, 1, 3)
        self.assertEqual((thin.width, thin.height), (1, 3))

    def test_to_rgb_drops_alpha(self):
        image = canvas.new_surface(2, 1, (1, 2, 3, 4))
        self.assertEqual(canvas.to_rgb(image), bytes([1, 2, 3, 1, 2, 3]))

    def test_numbers_draw_pixels_and_report_their_size(self):
        dst = canvas.new_surface(40, 20, (0, 0, 0, 255))
        width, height = canvas.draw_number(dst, 1, 1, 12, 2)
        self.assertEqual((width, height), ((2 * 4 + 1) * 2, 14))
        lit = sum(1 for i in range(0, len(dst.data), 4)
                  if dst.data[i] > 200)
        self.assertGreater(lit, 10)


@unittest.skipUnless(os.environ.get("RPGVXACE_TEST_PROJECT"),
                     "set RPGVXACE_TEST_PROJECT to a VX Ace project")
class TestAgainstARealProject(unittest.TestCase):
    """The shape numbers the editor wrote, against the neighbours they imply.

    Every floor autotile on the project's maps: work out which of its eight
    neighbours is the same kind, find the shape the table says that pattern
    needs, and require the editor stored the same number. Two cells in a
    thousand differ -- the editor also joins some different-but-related
    kinds -- so the bar is a share, not equality.
    """

    def test_the_floor_shapes_follow_their_neighbours(self):
        from rpgvxace_mcp.project import Project
        project = Project(os.environ["RPGVXACE_TEST_PROJECT"])
        agree = differ = 0
        for map_id in real_project.editor_written_map_ids(project):
            data = project.load_map(map_id).get("data")
            w, h = data.xsize, data.ysize
            for z in range(3):
                def at(x, y):
                    if 0 <= x < w and 0 <= y < h:
                        return data.data[x + y * w + z * w * h]
                    return None
                for y in range(h):
                    for x in range(w):
                        tile = at(x, y)
                        if tile is None or tile < tiles.TILE_ID_A1:
                            continue
                        if tiles.autotile_block(tile)[3] != "floor":
                            continue
                        kind = (tile - tiles.TILE_ID_A1) // 48

                        def same(nx, ny, kind=kind):
                            other = at(nx, ny)
                            return other is None or (
                                other >= tiles.TILE_ID_A1
                                and (other - tiles.TILE_ID_A1) // 48 == kind)
                        sides = {"L": same(x - 1, y), "U": same(x, y - 1),
                                 "R": same(x + 1, y), "D": same(x, y + 1)}
                        diagonals = {"TL": same(x - 1, y - 1),
                                     "TR": same(x + 1, y - 1),
                                     "BL": same(x - 1, y + 1),
                                     "BR": same(x + 1, y + 1)}
                        across = {"TL": "LU", "TR": "RU", "BL": "LD",
                                  "BR": "RD"}
                        absent = frozenset(s for s, ok in sides.items()
                                           if not ok)
                        notches = frozenset(
                            c for c, ok in diagonals.items()
                            if not ok and all(sides[s] for s in across[c]))
                        want = autotile.FLOOR_PATTERNS.index((absent, notches))
                        if want == (tile - tiles.TILE_ID_A1) % 48:
                            agree += 1
                        else:
                            differ += 1
        self.assertGreater(agree + differ, 1000, "found almost no autotiles")
        self.assertLess(differ / (agree + differ), 0.01,
                        "%d of %d cells disagree with the table"
                        % (differ, agree + differ))


if __name__ == "__main__":
    unittest.main()
