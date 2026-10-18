"""`map_paint`: autotiles painted by kind, and the generators built on that.

The shape rules are tested twice: as pure functions over a hand-built layer
(`autotile`), and through the tool against the demo project. The opt-in
class at the end holds the rules to the shapes the editor stored in a real
game, which is the only evidence that they are the editor's rules and not
merely self-consistent ones.
"""

import os
import shutil
import sys
import tempfile
import unittest
from array import array
from collections import deque
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp import autotile, marshal_rb  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyHash  # noqa: E402
from rpgvxace_mcp.schema import region_of, shadow_of  # noqa: E402
from rpgvxace_mcp.tools import map_paint_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import REGISTRY, Context, ToolError  # noqa: E402

from tool_case import ToolTest  # noqa: E402


# Kinds used below, by number. A2 kind 16 is the fixture town's ground and
# A1 kind 0 the fixture cave's; A4 kind 80 is a wall top and 88 the face
# under it; A3 48 is a roof and 56 the wall under it; A1 5 is a waterfall.
GROUND, OTHER_GROUND, CAVE = 16, 17, 0
TOP, FACE = 80, 88
ROOF, BUILDING = 48, 56
WATERFALL = 5


def layer_of(rows):
    """A one-layer `autotile.Layer` from rows of kinds (None for empty)."""
    height, width = len(rows), len(rows[0])
    buffer = array("H", [0] * (width * height))
    layer = autotile.Layer(buffer, width, height, 0)
    for y, row in enumerate(rows):
        for x, kind in enumerate(row):
            if kind is not None:
                layer.set(x, y, autotile.first_id(kind))
    return layer


def shape(layer, x, y):
    return layer.get(x, y) - autotile.first_id(layer.kind(x, y))


def shaped(rows):
    """`layer_of`, with every cell given its shape."""
    layer = layer_of(rows)
    autotile.reshape(layer, [(x, y) for y in range(layer.height)
                             for x in range(layer.width)])
    return layer


class TestTheFloorShapeFromNeighbours(unittest.TestCase):
    """The inverse of the floor table: every meaning gets its number back."""

    def test_every_pattern_round_trips(self):
        sides = {name: (dx, dy) for name, dx, dy in autotile.SIDES}
        corners = {name: (dx, dy, beside)
                   for name, dx, dy, beside in autotile.CORNERS}
        for number, (absent, notches) in enumerate(autotile.FLOOR_PATTERNS):
            rows = [[OTHER_GROUND] * 3 for _ in range(3)]
            rows[1][1] = GROUND
            for name, (dx, dy) in sides.items():
                if name not in absent:
                    rows[1 + dy][1 + dx] = GROUND
            for name, (dx, dy, beside) in corners.items():
                # A corner beside an open side does not matter, so it is
                # left as the other kind; a notch is the other kind too.
                if not set(beside) & absent and name not in notches:
                    rows[1 + dy][1 + dx] = GROUND
            layer = layer_of(rows)
            with self.subTest(shape=number):
                self.assertEqual(autotile.shape_at(layer, 1, 1), number)

    def test_a_diagonal_beside_an_open_side_is_ignored(self):
        # Open on the left; the top-left diagonal joined or not, same shape.
        joined = layer_of([[GROUND, GROUND, GROUND],
                           [OTHER_GROUND, GROUND, GROUND],
                           [GROUND, GROUND, GROUND]])
        apart = layer_of([[OTHER_GROUND, GROUND, GROUND],
                          [OTHER_GROUND, GROUND, GROUND],
                          [GROUND, GROUND, GROUND]])
        self.assertEqual(autotile.shape_at(joined, 1, 1),
                         autotile.shape_at(apart, 1, 1))

    def test_the_map_edge_joins_a_floor(self):
        """A whole map of one kind is shape 0 everywhere, edges included."""
        layer = shaped([[GROUND] * 4 for _ in range(3)])
        self.assertEqual({shape(layer, x, y) for y in range(3)
                          for x in range(4)}, {0})

    def test_a_lone_cell_is_open_on_every_side(self):
        layer = layer_of([[OTHER_GROUND] * 3, [OTHER_GROUND, GROUND,
                                               OTHER_GROUND],
                          [OTHER_GROUND] * 3])
        self.assertEqual(autotile.shape_at(layer, 1, 1), 46)

    def test_a_cell_that_is_not_an_autotile_has_no_shape(self):
        layer = layer_of([[None, GROUND]])
        self.assertIsNone(autotile.shape_at(layer, 0, 0))
        layer.set(0, 0, 1540)  # an A5 tile
        self.assertIsNone(autotile.shape_at(layer, 0, 0))


class TestTheWallShapeFromNeighbours(unittest.TestCase):
    """One bit per open side: 1 left, 2 up, 4 right, 8 down."""

    def test_a_lone_column_two_tall(self):
        layer = shaped([[GROUND, GROUND, GROUND],
                        [GROUND, BUILDING, GROUND],
                        [GROUND, BUILDING, GROUND],
                        [GROUND, GROUND, GROUND]])
        self.assertEqual(shape(layer, 1, 1), 1 | 2 | 4)
        self.assertEqual(shape(layer, 1, 2), 1 | 4 | 8)

    def test_a_run_of_wall_joins_left_and_right(self):
        layer = shaped([[GROUND] * 5,
                        [GROUND, BUILDING, BUILDING, BUILDING, GROUND],
                        [GROUND] * 5])
        self.assertEqual([shape(layer, x, 1) for x in (1, 2, 3)],
                         [1 | 2 | 8, 2 | 8, 2 | 4 | 8])

    def test_the_map_edge_joins_sideways_and_opens_up_and_down(self):
        layer = shaped([[BUILDING]])
        self.assertEqual(shape(layer, 0, 0), 2 | 8)

    def test_a_wall_stepping_down_beside_another_shows_its_edge(self):
        # Column 0 is two tall, column 1 three: their runs differ, so the
        # two do not join even where both are wall.
        layer = shaped([[GROUND, BUILDING],
                        [BUILDING, BUILDING],
                        [BUILDING, BUILDING]])
        self.assertTrue(shape(layer, 0, 1) & 4)
        self.assertTrue(shape(layer, 1, 1) & 1)

    def test_an_a4_face_joins_the_a4_top_beside_it(self):
        """The inner corner of a room: the face runs on behind the ceiling."""
        layer = shaped([[TOP, TOP, TOP],
                        [TOP, FACE, FACE],
                        [GROUND, GROUND, GROUND]])
        self.assertFalse(shape(layer, 1, 1) & 1)

    def test_an_a3_wall_does_not_join_another_a3_kind(self):
        layer = shaped([[ROOF, BUILDING, GROUND]])
        self.assertTrue(shape(layer, 1, 0) & 1)

    def test_an_a3_roof_uses_the_wall_shapes(self):
        layer = shaped([[GROUND, GROUND, GROUND],
                        [GROUND, ROOF, ROOF],
                        [GROUND, BUILDING, BUILDING]])
        self.assertEqual(shape(layer, 1, 1), 1 | 2 | 8)


class TestTheWaterfallShapeFromNeighbours(unittest.TestCase):
    def test_only_left_and_right_count(self):
        layer = shaped([[GROUND, WATERFALL, WATERFALL, GROUND],
                        [GROUND, WATERFALL, GROUND, GROUND]])
        self.assertEqual(shape(layer, 1, 0), 1)
        self.assertEqual(shape(layer, 2, 0), 2)
        self.assertEqual(shape(layer, 1, 1), 1 | 2)


class TestReshapingAnEdit(unittest.TestCase):
    def test_the_neighbourhood_covers_the_ring_around_an_edit(self):
        layer = layer_of([[GROUND] * 5 for _ in range(5)])
        near = set(autotile.neighbourhood(layer, [(2, 2)]))
        self.assertEqual(near, {(x, y) for x in (1, 2, 3) for y in (1, 2, 3)})

    def test_the_neighbourhood_follows_a_wall_run_beside_it(self):
        layer = layer_of([[GROUND, BUILDING, GROUND]] * 6)
        near = set(autotile.neighbourhood(layer, [(1, 5)]))
        self.assertIn((0, 0), near)
        self.assertIn((2, 0), near)

    def test_reshape_returns_how_many_cells_changed(self):
        layer = shaped([[GROUND] * 5 for _ in range(5)])
        layer.set(2, 2, autotile.first_id(OTHER_GROUND))
        changed = autotile.reshape(layer, autotile.neighbourhood(layer,
                                                                 [(2, 2)]))
        # The cell itself goes from 0 to 46, and all eight around it change.
        self.assertEqual(changed, 9)
        self.assertEqual(shape(layer, 2, 2), 46)
        self.assertEqual(shape(layer, 1, 1), 4)       # inner corner, BR

    def test_reshaping_a_shaped_layer_changes_nothing(self):
        layer = shaped([[TOP, TOP, TOP, TOP],
                        [TOP, FACE, FACE, TOP],
                        [TOP, FACE, FACE, TOP],
                        [TOP, GROUND, GROUND, TOP]])
        everything = [(x, y) for y in range(4) for x in range(4)]
        self.assertEqual(autotile.reshape(layer, everything), 0)


class TestTheEditorsShadow(unittest.TestCase):
    """The left half of a cell, when the cells left and up-left are wall."""

    def meta_for(self, layer, words=0):
        buffer = array("H", [words] * (layer.width * layer.height))
        return autotile.Layer(buffer, layer.width, layer.height, 0)

    def test_the_cell_right_of_a_wall_is_shaded(self):
        ground = shaped([[TOP, GROUND], [FACE, GROUND], [FACE, GROUND]])
        meta = self.meta_for(ground)
        cells = [(x, y) for y in range(3) for x in range(2)]
        autotile.auto_shadow(ground, meta, cells)
        self.assertEqual([shadow_of(meta.get(1, y)) for y in range(3)],
                         [0, autotile.WALL_SHADOW, autotile.WALL_SHADOW])
        self.assertEqual(shadow_of(meta.get(0, 1)), 0)

    def test_a_wall_is_not_shaded_by_the_wall_beside_it(self):
        ground = shaped([[TOP, TOP], [FACE, FACE], [GROUND, GROUND]])
        meta = self.meta_for(ground)
        autotile.auto_shadow(ground, meta, [(1, 1), (1, 2)])
        self.assertEqual(shadow_of(meta.get(1, 1)), 0)
        self.assertEqual(shadow_of(meta.get(1, 2)), 0)

    def test_a_region_under_the_shadow_is_kept(self):
        ground = shaped([[TOP, GROUND], [TOP, GROUND]])
        meta = self.meta_for(ground, words=7 << 8 | 15)
        autotile.auto_shadow(ground, meta, [(1, 1), (0, 0)])
        self.assertEqual(meta.get(1, 1), 7 << 8 | autotile.WALL_SHADOW)
        self.assertEqual(meta.get(0, 0), 7 << 8)


class PaintTest(ToolTest):
    """`map_paint` against the demo project.

    Map 1 is 20 x 15 of A2 kind 16 on the field tileset (A1, A2); map 2 is
    12 x 10 of A1 kind 0 on the dungeon tileset (A1, A2, A4, A5).
    """

    def paint(self, **arguments):
        return self.call("map_paint", **arguments)

    def ground(self, map_id, project=None):
        data = (project or self.project).load_map(map_id).get("data")
        return autotile.Layer(data.data, data.xsize, data.ysize, 0)

    def meta(self, map_id):
        data = self.project.load_map(map_id).get("data")
        return autotile.Layer(data.data, data.xsize, data.ysize, 3)

    def kinds(self, layer, x0, y0, width, height):
        return [[layer.kind(x, y) for x in range(x0, x0 + width)]
                for y in range(y0, y0 + height)]

    def new_map(self, width=40, height=30):
        out = self.call("map_edit", op="create", name="Generated",
                        width=width, height=height, tileset_id=2)
        return out["map_id"]

    def floor_cells(self, layer, kind):
        return {(x, y) for y in range(layer.height) for x in range(layer.width)
                if layer.kind(x, y) == kind}


class TestFillingARectangle(PaintTest):
    def test_the_rectangle_and_its_ring_take_their_shapes(self):
        out = self.paint(op="fill", map_id=1, kind="A2:1", x=5, y=5,
                         width=3, height=3)
        layer = self.ground(1)
        self.assertEqual(self.kinds(layer, 5, 5, 3, 3), [[OTHER_GROUND] * 3] * 3)
        self.assertEqual(shape(layer, 6, 6), 0)
        self.assertEqual(shape(layer, 5, 5), 34)      # open left and up
        self.assertEqual(shape(layer, 7, 7), 38)      # open right and down
        # The ground around it: open toward the patch, inner corner at the
        # diagonals.
        self.assertEqual(shape(layer, 4, 6), 24)      # open right
        self.assertEqual(shape(layer, 4, 4), 4)       # notch bottom-right
        self.assertEqual(out["painted"], 9)
        self.assertEqual(out["reshaped"], 16)

    def test_it_reaches_the_disk(self):
        self.paint(op="fill", map_id=1, kind="A2:1", x=0, y=0, width=2,
                   height=2)
        layer = self.ground(1, self.reopen().project)
        self.assertEqual(layer.kind(1, 1), OTHER_GROUND)
        self.assertEqual(shape(layer, 2, 2), 1)       # notch top-left

    def test_a_dry_run_writes_nothing_and_says_so(self):
        before = self.data_bytes("Map001")
        out = self.paint(op="fill", map_id=1, kind="A2:1", x=0, y=0, width=2,
                         height=2, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertIn("would_change", out)
        self.assertEqual(self.data_bytes("Map001"), before)
        self.assertEqual(self.ground(1).kind(0, 0), GROUND)

    def test_a_plain_a5_tile_is_written_as_it_is(self):
        self.paint(op="fill", map_id=2, kind="A5:3", x=1, y=1, width=2,
                   height=1)
        layer = self.ground(2)
        self.assertEqual(layer.get(1, 1), autotile.TILE_ID_A5 + 3)
        # The cave around it is re-shaped: the A5 tile is not cave.
        self.assertEqual(shape(layer, 0, 1), 24)

    def test_a_tile_id_names_its_kind(self):
        """A value read with map_tiles can be painted back as its kind."""
        self.paint(op="fill", map_id=1, kind=str(autotile.first_id(17) + 30),
                   x=3, y=3, width=1, height=1)
        self.assertEqual(self.ground(1).kind(3, 3), OTHER_GROUND)

    def test_the_middle_layer_takes_autotiles_too(self):
        self.paint(op="fill", map_id=1, kind="A2:1", z=1, x=2, y=2, width=2,
                   height=2)
        data = self.project.load_map(1).get("data")
        middle = autotile.Layer(data.data, data.xsize, data.ysize, 1)
        self.assertEqual(middle.kind(2, 2), OTHER_GROUND)
        self.assertEqual(self.ground(1).kind(2, 2), GROUND)


class TestPaintingCellsAndBorders(PaintTest):
    def test_a_list_of_cells(self):
        out = self.paint(op="paint", map_id=1, kind="A2:1",
                         cells=[[1, 1], [2, 1], [9, 9]])
        layer = self.ground(1)
        self.assertEqual(layer.kind(9, 9), OTHER_GROUND)
        self.assertEqual(shape(layer, 9, 9), 46)
        self.assertEqual(shape(layer, 1, 1), 43)      # joined only right
        self.assertEqual(out["painted"], 3)

    def test_a_border_paints_the_outline_only(self):
        self.paint(op="border", map_id=1, kind="A2:1", x=2, y=2, width=4,
                   height=3)
        layer = self.ground(1)
        self.assertEqual(self.kinds(layer, 2, 2, 4, 3), [
            [OTHER_GROUND] * 4,
            [OTHER_GROUND, GROUND, GROUND, OTHER_GROUND],
            [OTHER_GROUND] * 4])

    def test_a_region_keeps_the_shadow_under_it(self):
        meta = self.meta(2)
        meta.set(3, 3, (1 << 8) | 5)
        self.paint(op="region", map_id=2, region=9, cells=[[3, 3], [4, 3]])
        self.paint(op="region", map_id=2, region=4, x=0, y=0, width=2,
                   height=1)
        meta = self.meta(2)
        self.assertEqual(region_of(meta.get(3, 3)), 9)
        self.assertEqual(shadow_of(meta.get(3, 3)), 5)
        self.assertEqual(region_of(meta.get(1, 0)), 4)
        self.assertEqual(region_of(meta.get(2, 0)), 0)


class TestRefusals(PaintTest):
    def assert_refused(self, fragment, **arguments):
        with self.assertRaises(ToolError) as caught:
            self.paint(**arguments)
        self.assertIn(fragment, str(caught.exception))

    def test_a_sheet_the_tileset_does_not_name(self):
        self.assert_refused("A4", op="fill", map_id=1, kind="A4:0", x=0, y=0,
                            width=1, height=1)

    def test_kind_spellings_it_cannot_read(self):
        for kind in ("A9:0", "A2:32", "A2:-1", "A2", "ground", "9000", "B:1"):
            with self.subTest(kind=kind):
                self.assert_refused("kind", op="fill", map_id=1, kind=kind,
                                    x=0, y=0, width=1, height=1)

    def test_a_block_past_the_end_of_its_sheet_names_the_range(self):
        self.assert_refused("0 to 31", op="fill", map_id=1, kind="A2:32",
                            x=0, y=0, width=1, height=1)

    def test_an_a_tile_on_the_upper_layer(self):
        self.assert_refused("at most 1", op="fill", map_id=1, kind="A2:1", z=2,
                            x=0, y=0, width=1, height=1)

    def test_a_cell_off_the_map(self):
        self.assert_refused("(30, 1)", op="paint", map_id=1, kind="A2:1",
                            cells=[[30, 1]])

    def test_a_rectangle_off_the_map(self):
        self.assert_refused("does not fit", op="fill", map_id=1, kind="A2:1",
                            x=18, y=0, width=5, height=1)

    def test_too_many_cells_in_one_list(self):
        cells = [[0, 0]] * (map_paint_tools.MAX_PAINT_CELLS + 1)
        self.assert_refused(str(map_paint_tools.MAX_PAINT_CELLS), op="paint",
                            map_id=1, kind="A2:1", cells=cells)

    def test_a_floor_that_is_a_wall(self):
        self.assert_refused("floor", op="room", map_id=2, floor="A4:8",
                            wall="A4:8", x=3, y=4, width=4, height=3)

    def test_a_wall_that_is_not_a3_or_a4(self):
        self.assert_refused("wall", op="room", map_id=2, floor="A2:0",
                            wall="A2:1", x=3, y=4, width=4, height=3)

    def test_a_room_with_no_space_above_for_its_wall(self):
        self.assert_refused("above", op="room", map_id=2, floor="A2:0",
                            wall="A4:8", x=3, y=1, width=4, height=3)

    def test_the_other_ops_arguments(self):
        self.assert_refused("cells", op="fill", map_id=1, kind="A2:1",
                            cells=[[0, 0]], x=0, y=0, width=1, height=1)

    def test_a_missing_kind_is_named(self):
        self.assert_refused("kind is required", op="fill", map_id=1, x=0,
                            y=0, width=1, height=1)

    def test_a_rectangle_over_the_cell_ceiling(self):
        map_id = self.new_map(width=200, height=200)
        self.assert_refused("cells", op="fill", map_id=map_id, kind="A2:0")

    def test_a_re_shape_over_its_ceiling_is_refused_and_undone(self):
        """A wall run beside an edit is re-shaped whole, so it is bounded.

        The ceiling is lowered rather than a thousand-row map built: what
        is tested is that the count is checked, and that the refusal leaves
        the cached map as it was.
        """
        self.addCleanup(setattr, map_paint_tools, "MAX_RESHAPE_CELLS",
                        map_paint_tools.MAX_RESHAPE_CELLS)
        map_paint_tools.MAX_RESHAPE_CELLS = 20
        before = bytes(self.project.load_map(1).get("data").data)
        self.assert_refused("re-shape", op="fill", map_id=1, kind="A2:1",
                            x=0, y=0, width=5, height=5)
        self.assertEqual(bytes(self.project.load_map(1).get("data").data),
                         before)


class TestARoom(PaintTest):
    def room(self, **extra):
        arguments = dict(op="room", map_id=2, floor="A2:0", wall="A4:8",
                         x=3, y=4, width=5, height=3)
        arguments.update(extra)
        return self.paint(**arguments)

    def test_floor_faces_and_ceiling(self):
        self.room()
        layer = self.ground(2)
        floor = 16
        self.assertEqual(self.kinds(layer, 3, 4, 5, 3), [[floor] * 5] * 3)
        # Two rows of wall face over the floor, then the ceiling around all.
        self.assertEqual(self.kinds(layer, 3, 2, 5, 2), [[FACE] * 5] * 2)
        self.assertEqual(self.kinds(layer, 2, 1, 7, 1), [[TOP] * 7])
        self.assertEqual([layer.kind(2, y) for y in range(1, 8)], [TOP] * 7)
        self.assertEqual([layer.kind(8, y) for y in range(1, 8)], [TOP] * 7)
        self.assertEqual(self.kinds(layer, 2, 7, 7, 1), [[TOP] * 7])
        # The face: top row open upward, bottom row open downward, and the
        # ends joined to the ceiling column beside them.
        self.assertEqual(shape(layer, 4, 2), 2)
        self.assertEqual(shape(layer, 4, 3), 8)
        self.assertEqual(shape(layer, 3, 2), 2)

    def test_either_half_of_a_wall_pair_names_it(self):
        self.room(wall="A4:0")
        self.assertEqual(self.ground(2).kind(4, 3), FACE)

    def test_the_wall_casts_the_editors_shadow(self):
        self.room()
        meta = self.meta(2)
        # The floor's left column has the ceiling to its left and up-left.
        self.assertEqual(shadow_of(meta.get(3, 5)), autotile.WALL_SHADOW)
        self.assertEqual(shadow_of(meta.get(4, 5)), 0)

    def test_shadows_can_be_left_alone(self):
        self.room(shadows=False)
        self.assertEqual(shadow_of(self.meta(2).get(3, 5)), 0)

    def test_a_door_in_the_top_wall(self):
        self.room(door="top")
        layer = self.ground(2)
        self.assertEqual([layer.kind(5, y) for y in range(1, 4)], [16] * 3)
        # The faces either side of the gap show their edge toward it.
        self.assertTrue(shape(layer, 4, 2) & 4)
        self.assertTrue(shape(layer, 6, 2) & 1)

    def test_a_door_in_a_side(self):
        self.room(door="left", door_at=1)
        self.assertEqual(self.ground(2).kind(2, 5), 16)

    def test_a_door_off_the_side_is_refused(self):
        with self.assertRaises(ToolError):
            self.room(door="left", door_at=3)

    def test_the_reply_is_short_and_says_what_it_did(self):
        out = self.room()
        self.assertEqual(out["room"], {"x": 3, "y": 4, "width": 5,
                                       "height": 3})
        self.assertGreater(out["painted"], 15)


class TestACorridor(PaintTest):
    def test_a_horizontal_run_gets_its_wall(self):
        self.paint(op="corridor", map_id=2, floor="A2:0", wall="A4:8",
                   x=1, y=6, to_x=10, to_y=6)
        layer = self.ground(2)
        self.assertEqual([layer.kind(x, 6) for x in range(1, 11)], [16] * 10)
        self.assertEqual([layer.kind(x, 5) for x in range(1, 11)], [FACE] * 10)
        self.assertEqual([layer.kind(x, 4) for x in range(1, 11)], [FACE] * 10)

    def test_a_bend_reaches_both_ends(self):
        self.paint(op="corridor", map_id=2, floor="A2:0", wall="A4:8",
                   x=1, y=8, to_x=9, to_y=3, corridor_width=2)
        layer = self.ground(2)
        cells = self.floor_cells(layer, 16)
        self.assertIn((1, 8), cells)
        self.assertIn((9, 3), cells)
        self.assertTrue(connected(cells))


def connected(cells):
    """Whether a set of cells is one 4-connected piece."""
    if not cells:
        return False
    start = next(iter(cells))
    seen = {start}
    queue = deque([start])
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if (nx, ny) in cells and (nx, ny) not in seen:
                seen.add((nx, ny))
                queue.append((nx, ny))
    return len(seen) == len(cells)


class TestADungeon(PaintTest):
    def dungeon(self, map_id, **extra):
        arguments = dict(op="dungeon", map_id=map_id, floor="A2:0",
                         wall="A4:8", seed=7)
        arguments.update(extra)
        return self.paint(**arguments)

    def snapshot(self, map_id):
        data = self.project.load_map(map_id).get("data")
        return bytes(data.data)

    def test_the_same_seed_builds_the_same_dungeon(self):
        first, second = self.new_map(), self.new_map()
        a, b = self.dungeon(first), self.dungeon(second)
        self.assertEqual(a["rooms"], b["rooms"])
        self.assertEqual(self.snapshot(first), self.snapshot(second))

    def test_another_seed_builds_another(self):
        first, second = self.new_map(), self.new_map()
        self.dungeon(first)
        self.dungeon(second, seed=8)
        self.assertNotEqual(self.snapshot(first), self.snapshot(second))

    def test_every_floor_cell_can_be_reached(self):
        map_id = self.new_map()
        out = self.dungeon(map_id)
        self.assertGreaterEqual(len(out["rooms"]), 2)
        self.assertTrue(connected(self.floor_cells(self.ground(map_id), 16)))

    def test_every_room_is_floor(self):
        map_id = self.new_map()
        out = self.dungeon(map_id)
        layer = self.ground(map_id)
        for room in out["rooms"]:
            for y in range(room["y"], room["y"] + room["height"]):
                for x in range(room["x"], room["x"] + room["width"]):
                    self.assertEqual(layer.kind(x, y), 16, (room, x, y))

    def test_every_face_stands_on_floor_under_a_ceiling(self):
        map_id = self.new_map()
        self.dungeon(map_id, wall_height=2)
        layer = self.ground(map_id)
        for y in range(layer.height):
            for x in range(layer.width):
                if layer.kind(x, y) != FACE:
                    continue
                below = [layer.kind(x, y + d) for d in (1, 2)
                         if y + d < layer.height]
                self.assertIn(16, below, (x, y))

    def test_the_result_is_already_shaped(self):
        """Re-shaping the finished map from its neighbours changes nothing."""
        map_id = self.new_map()
        self.dungeon(map_id)
        layer = self.ground(map_id)
        everything = [(x, y) for y in range(layer.height)
                      for x in range(layer.width)]
        self.assertEqual(autotile.reshape(layer, everything), 0)

    def test_an_area_inside_the_map(self):
        map_id = self.new_map()
        self.dungeon(map_id, x=20, y=0, width=20, height=30)
        layer = self.ground(map_id)
        self.assertEqual({layer.get(x, y) for y in range(30) for x in range(20)},
                         {0})

    def test_a_dungeon_too_large_is_refused(self):
        map_id = self.new_map(width=300, height=100)
        with self.assertRaises(ToolError) as caught:
            self.dungeon(map_id)
        self.assertIn("cells", str(caught.exception))

    def test_a_room_count_above_the_ceiling_is_refused(self):
        map_id = self.new_map()
        with self.assertRaises(ToolError):
            self.dungeon(map_id, rooms=map_paint_tools.MAX_ROOMS + 1)

    def test_a_dry_run_reports_the_rooms_and_writes_nothing(self):
        map_id = self.new_map()
        before = self.snapshot(map_id)
        out = self.dungeon(map_id, dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertTrue(out["rooms"])
        self.assertEqual(self.snapshot(map_id), before)


class TestOnVX(ToolTest):
    engine = "vx"

    def test_vx_is_refused_with_the_reason(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_paint", op="fill", map_id=1, kind="A2:1", x=0,
                      y=0, width=1, height=1)
        self.assertIn("VX", str(caught.exception))
        self.assertIn("tileset", str(caught.exception))


class TestOnXP(unittest.TestCase):
    def test_xp_is_refused_with_the_reason(self):
        tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-xp-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "Data").mkdir()
        (tmp / "Game.rxproj").write_text("xp", encoding="ascii")
        (tmp / "Data" / "MapInfos.rxdata").write_bytes(
            marshal_rb.dump(RubyHash()))
        ctx = Context(Project(tmp, allow_other_engines=True))
        with self.assertRaises(ToolError) as caught:
            REGISTRY["map_paint"].run(ctx, {"op": "fill", "map_id": 1,
                                            "kind": "A2:1"})
        self.assertIn("XP", str(caught.exception))


@unittest.skipUnless(os.environ.get("RPGVXACE_TEST_PROJECT"),
                     "set RPGVXACE_TEST_PROJECT to a VX Ace project")
class TestAgainstTheRealProject(unittest.TestCase):
    """The shape rules, against every autotile the editor stored in a game.

    Read-only: each layer is copied before it is re-shaped, so nothing is
    written back to the project's cache, let alone its files.
    """

    # Walls are held at what the rule reproduces today (about 92% of the A3
    # and A4 cells in the author's game), so a change that makes it worse fails.
    # The rest are cells the editor shaped when they were painted and did
    # not revisit when the wall beside them changed.
    WALL_SHARE = 0.92

    @classmethod
    def setUpClass(cls):
        cls.project = Project(os.environ["RPGVXACE_TEST_PROJECT"])

    def layers(self):
        """Every tile layer of every map, each as a copy of its own."""
        for map_id in self.project.map_ids_on_disk():
            data = self.project.load_map(map_id).get("data")
            plane = data.xsize * data.ysize
            for z in range(3):
                buffer = array("H", data.data[z * plane:(z + 1) * plane])
                yield autotile.Layer(buffer, data.xsize, data.ysize, 0)

    def render_share(self):
        """The floor share `test_render_tiles` measures, its way, live.

        That check matches each cell's neighbours against the table's own
        patterns without `shape_at`, and found 99.65% in the author's game. It is
        the bar: re-shaping from neighbours has to do at least as well.
        """
        agree = total = 0
        for layer in self.layers():
            for y in range(layer.height):
                for x in range(layer.width):
                    kind = layer.kind(x, y)
                    if kind is None or autotile.table_of(kind) != "floor":
                        continue

                    def same(dx, dy, x=x, y=y, kind=kind, layer=layer):
                        return (not layer.inside(x + dx, y + dy)
                                or layer.kind(x + dx, y + dy) == kind)
                    sides = {name: same(dx, dy)
                             for name, dx, dy in autotile.SIDES}
                    absent = frozenset(n for n, ok in sides.items() if not ok)
                    notches = frozenset(
                        name for name, dx, dy, beside in autotile.CORNERS
                        if not same(dx, dy) and all(sides[s] for s in beside))
                    want = autotile.FLOOR_PATTERNS.index((absent, notches))
                    total += 1
                    agree += want == layer.get(x, y) - autotile.first_id(kind)
        self.assertGreater(total, 1000, "found almost no floor autotiles")
        return agree / total

    def agreement(self, table):
        agree = differ = 0
        for layer in self.layers():
            runs = {}
            for y in range(layer.height):
                for x in range(layer.width):
                    kind = layer.kind(x, y)
                    if kind is None or autotile.table_of(kind) != table:
                        continue
                    stored = layer.get(x, y) - autotile.first_id(kind)
                    if autotile.shape_at(layer, x, y, runs) == stored:
                        agree += 1
                    else:
                        differ += 1
        self.assertGreater(agree + differ, 300, "found almost no autotiles")
        return agree / (agree + differ)

    def test_floor_shapes_follow_their_neighbours(self):
        self.assertGreaterEqual(self.agreement("floor"), self.render_share())

    def test_wall_shapes_follow_their_neighbours(self):
        self.assertGreaterEqual(self.agreement("wall"), self.WALL_SHARE)

    def test_reshaping_a_whole_map_keeps_the_editors_floors(self):
        """Through `reshape`, the path the tool takes, not just `shape_at`."""
        kept = total = 0
        for layer in self.layers():
            before = array("H", layer.buffer)
            autotile.reshape(layer, [(x, y) for y in range(layer.height)
                                     for x in range(layer.width)])
            for old, new in zip(before, layer.buffer):
                kind = autotile.kind_of(old)
                if kind is None or autotile.table_of(kind) != "floor":
                    continue
                total += 1
                kept += old == new
        self.assertGreaterEqual(kept / total, self.render_share())


if __name__ == "__main__":
    unittest.main()
