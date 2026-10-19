"""VX and XP in `map_render`: what is drawn, what is refused, and that Ace is unchanged.

`docs/engine_parity.md` is the written spec this file holds the code to: for
each engine, which facts are established (and may be drawn by default), which
are only remembered (and are drawn only when the call says
``unverified_formats: true``, with an ``engine_warning``), and which are not
known at all (and are refused with a message saying what is missing).

Every expected pixel is computed here by hand from a painted sheet, not by
calling the code under test: `quarter(x, y)` colours each 16-pixel square of a
sheet by where it is, so a pixel's colour names the place it was cut from.
"""

import array
import hashlib
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))

from rpgvxace_mcp import autotile  # noqa: E402
from rpgvxace_mcp.render import canvas, layouts, png, xp_tiles  # noqa: E402
from rpgvxace_mcp.render.scene import Sprite, TileGrid, render_map  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyString  # noqa: E402
from rpgvxace_mcp.tools import map_render_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import ToolError  # noqa: E402

import test_map_render as base  # noqa: E402
from tool_case import ToolTest  # noqa: E402


def quarter(x, y):
    """What `test_map_render.quarter` paints at pixel (x, y) of a sheet."""
    return base.quarter(x, y)


def painted_sheet(width, height):
    """The in-memory twin of `base.paint`: each 16 px square coloured by place."""
    image = canvas.new_surface(width, height)
    for y in range(height):
        for x in range(width):
            at = (y * width + x) * 4
            image.data[at:at + 4] = bytes(quarter(x, y)) + b"\xff"
    return image


def pixel(image, x, y):
    at = (y * image.width + x) * 4
    return tuple(image.data[at:at + 3])


# ---------------------------------------------------------------------------
# XP's autotile table
# ---------------------------------------------------------------------------


# The table as remembered from XP community Tilemap rewrites, typed out entry
# by entry, 8 to a row: entry n is row n // 8, column n % 8, and each entry is
# the four 16-pixel pieces (top-left, top-right, bottom-left, bottom-right) of
# the 96 x 128 autotile graphic, numbered 1 to 48 row by row. REMEMBERED, not
# verified: see docs/engine_parity.md. It is here so that the table the code
# *builds* (from the Ace shape numbering and the graphic's 9-slice) is held to
# an independent statement of the same table.
REMEMBERED_XP_TABLE = (
    (27, 28, 33, 34), (5, 28, 33, 34), (27, 6, 33, 34), (5, 6, 33, 34),
    (27, 28, 33, 12), (5, 28, 33, 12), (27, 6, 33, 12), (5, 6, 33, 12),
    (27, 28, 11, 34), (5, 28, 11, 34), (27, 6, 11, 34), (5, 6, 11, 34),
    (27, 28, 11, 12), (5, 28, 11, 12), (27, 6, 11, 12), (5, 6, 11, 12),
    (25, 26, 31, 32), (25, 6, 31, 32), (25, 26, 31, 12), (25, 6, 31, 12),
    (15, 16, 21, 22), (15, 16, 21, 12), (15, 16, 11, 22), (15, 16, 11, 12),
    (29, 30, 35, 36), (29, 30, 11, 36), (5, 30, 35, 36), (5, 30, 11, 36),
    (39, 40, 45, 46), (5, 40, 45, 46), (39, 6, 45, 46), (5, 6, 45, 46),
    (25, 30, 31, 36), (15, 16, 45, 46), (13, 14, 19, 20), (13, 14, 19, 12),
    (17, 18, 23, 24), (17, 18, 11, 24), (41, 42, 47, 48), (5, 42, 47, 48),
    (37, 38, 43, 44), (37, 6, 43, 44), (13, 18, 19, 24), (13, 14, 43, 44),
    (37, 42, 43, 48), (17, 18, 47, 48), (13, 18, 43, 48), (1, 2, 7, 8),
)


class TestTheXpAutotileTable(unittest.TestCase):
    def test_it_is_the_remembered_table_entry_for_entry(self):
        self.assertEqual(len(xp_tiles.XP_FLOOR_TABLE), 48)
        for shape, wanted in enumerate(REMEMBERED_XP_TABLE):
            with self.subTest(shape=shape):
                self.assertEqual(xp_tiles.XP_FLOOR_TABLE[shape], wanted)

    # The graphic is a 9-slice, read from the picture rather than the table:
    # a piece's tile (column, row) says what it can be used for.
    ROLE = {(0, 1): "TL", (1, 1): "T", (2, 1): "TR", (0, 2): "L",
            (1, 2): "C", (2, 2): "R", (0, 3): "BL", (1, 3): "B",
            (2, 3): "BR", (2, 0): "notch", (0, 0): "whole"}
    # Which sides a role says are open, as seen from the quadrant it fills.
    OPEN = {"TL": "UL", "T": "U", "TR": "UR", "L": "L", "C": "", "R": "R",
            "BL": "DL", "B": "D", "BR": "DR", "notch": ""}

    @classmethod
    def role(cls, piece):
        index = piece - 1
        column, row = index % 6, index // 6
        return cls.ROLE[(column // 2, row // 2)], column % 2, row % 2

    def test_each_entry_agrees_with_itself_about_which_sides_are_open(self):
        # Quadrant order is TL, TR, BL, BR. A piece in the TL slot decides
        # whether up and left are open; the TR slot, up and right; and so on.
        # Two slots that share a side must say the same thing about it.
        for shape, pieces in enumerate(xp_tiles.XP_FLOOR_TABLE[:47]):
            with self.subTest(shape=shape):
                slots = {}
                for slot, piece in zip(("TL", "TR", "BL", "BR"), pieces):
                    role, qx, qy = self.role(piece)
                    # The piece must sit in the quarter of its tile that
                    # belongs in this slot, as the slot's own corner of it.
                    self.assertEqual((qx, qy),
                                     (0 if slot[1] == "L" else 1,
                                      0 if slot[0] == "T" else 1),
                                     "piece %d is a %s piece in slot %s"
                                     % (piece, role, slot))
                    slots[slot] = role
                vertical = {
                    "TL": "U" in self.OPEN[slots["TL"]],
                    "TR": "U" in self.OPEN[slots["TR"]],
                    "BL": "D" in self.OPEN[slots["BL"]],
                    "BR": "D" in self.OPEN[slots["BR"]]}
                left = ("L" in self.OPEN[slots["TL"]],
                        "L" in self.OPEN[slots["BL"]])
                right = ("R" in self.OPEN[slots["TR"]],
                         "R" in self.OPEN[slots["BR"]])
                self.assertEqual(vertical["TL"], vertical["TR"])
                self.assertEqual(vertical["BL"], vertical["BR"])
                self.assertEqual(left[0], left[1])
                self.assertEqual(right[0], right[1])

    def test_the_47_neighbourhoods_each_have_one_entry_and_the_lone_tile_last(self):
        # Independent count: 16 interiors by notch, 4 sides x 4 notch sets,
        # 2 strips, 4 corners x 2, 4 caps, 1 island = 47; the 48th is the
        # whole-tile picture. No two entries are alike.
        self.assertEqual(len(set(xp_tiles.XP_FLOOR_TABLE)), 48)
        self.assertEqual(16 + 4 * 4 + 2 + 4 * 2 + 4 + 1, 47)
        self.assertEqual(xp_tiles.XP_FLOOR_TABLE[47], (1, 2, 7, 8))

    def test_it_follows_the_ace_numbering_group_for_group(self):
        # The corroboration the spec speaks of, as a test: shape n of XP has
        # the open sides and notches `autotile.FLOOR_PATTERNS[n]` says, read
        # back from the pieces.
        for shape, (absent, notches) in enumerate(autotile.FLOOR_PATTERNS):
            with self.subTest(shape=shape):
                pieces = xp_tiles.XP_FLOOR_TABLE[shape]
                seen_open = set()
                seen_notch = set()
                for slot, piece in zip(("TL", "TR", "BL", "BR"), pieces):
                    role, _, _ = self.role(piece)
                    if role == "notch":
                        seen_notch.add(slot)
                    seen_open.update(self.OPEN[role])
                self.assertEqual(seen_open, set(absent))
                self.assertEqual(seen_notch, set(notches))


# ---------------------------------------------------------------------------
# Cutting XP tiles
# ---------------------------------------------------------------------------


class TestXpTileIds(unittest.TestCase):
    def test_which_sheet_an_id_comes_from(self):
        needed = xp_tiles.xp_sheet_needed
        self.assertIsNone(needed(0))
        self.assertIsNone(needed(-1))
        # 1-47 are not ids XP data holds.
        self.assertIsNone(needed(1))
        self.assertIsNone(needed(47))
        self.assertEqual(needed(48), "autotile 1")
        self.assertEqual(needed(95), "autotile 1")
        self.assertEqual(needed(96), "autotile 2")
        self.assertEqual(needed(383), "autotile 7")
        self.assertEqual(needed(384), "tileset")
        self.assertEqual(needed(0xFFFF), "tileset")
        self.assertIsNone(needed(0x10000))

    def test_a_tileset_id_is_cell_id_minus_384_eight_to_a_row(self):
        sheet = painted_sheet(256, 128)
        compose = xp_tiles.xp_compose_tile
        # id 384 is the top-left cell: its top-left pixel is quarter (0, 0).
        first = compose(384, {"tileset": sheet}, True)
        self.assertEqual(pixel(first, 0, 0), quarter(0, 0))
        self.assertEqual(pixel(first, 31, 31), quarter(31, 31))
        # id 391 ends row 0: column 7 -> x = 224.
        self.assertEqual(pixel(compose(391, {"tileset": sheet}, True), 0, 0),
                         quarter(224, 0))
        # id 392 starts row 1: y = 32.
        self.assertEqual(pixel(compose(392, {"tileset": sheet}, True), 0, 0),
                         quarter(0, 32))
        # 384 + 8 * 4 + 5 would be row 4, column 5 -- past this 128 px sheet,
        # whose rows are 0 to 3.
        self.assertIsNone(compose(384 + 8 * 4 + 5, {"tileset": sheet}, True))
        self.assertIsNotNone(compose(384 + 8 * 3 + 5, {"tileset": sheet}, True))
        # and row 2, column 5 is x = 160, y = 64.
        self.assertEqual(
            pixel(compose(384 + 8 * 2 + 5, {"tileset": sheet}, True), 3, 4),
            quarter(163, 68))

    def test_a_missing_or_narrow_sheet_draws_nothing_rather_than_failing(self):
        compose = xp_tiles.xp_compose_tile
        self.assertIsNone(compose(384, {}, True))
        self.assertIsNone(compose(384, {"tileset": None}, True))
        # Column 7 of a 224 px sheet is outside it.
        self.assertIsNone(compose(391, {"tileset": painted_sheet(224, 32)},
                                  True))
        self.assertIsNone(compose(0xFFFF, {"tileset": painted_sheet(256, 64)},
                                  True))

    def test_an_autotile_is_cut_in_sixteen_pixel_pieces_by_its_pattern(self):
        sheet = painted_sheet(96, 128)
        sheets = {"autotile 1": sheet}
        compose = xp_tiles.xp_compose_tile
        # Pattern 0 is pieces 27, 28, 33, 34. Piece p is at column (p-1) % 6,
        # row (p-1) // 6 of the 6 x 8 grid: 27 -> (2, 4), 28 -> (3, 4),
        # 33 -> (2, 5), 34 -> (3, 5), i.e. pixels (32, 64), (48, 64),
        # (32, 80), (48, 80).
        tile = compose(48, sheets, True)
        self.assertEqual(pixel(tile, 0, 0), quarter(32, 64))
        self.assertEqual(pixel(tile, 16, 0), quarter(48, 64))
        self.assertEqual(pixel(tile, 0, 16), quarter(32, 80))
        self.assertEqual(pixel(tile, 31, 31), quarter(63, 95))
        # Pattern 1 swaps the top-left for notch piece 5 at (4, 0) -> (64, 0).
        self.assertEqual(pixel(compose(49, sheets, True), 2, 3),
                         quarter(66, 3))
        # Pattern 47 is the whole top-left tile: pieces 1, 2, 7, 8.
        whole = compose(95, sheets, True)
        self.assertEqual(pixel(whole, 0, 0), quarter(0, 0))
        self.assertEqual(pixel(whole, 16, 0), quarter(16, 0))
        self.assertEqual(pixel(whole, 0, 16), quarter(0, 16))
        self.assertEqual(pixel(whole, 16, 16), quarter(16, 16))

    def test_the_second_autotile_reads_its_own_sheet(self):
        sheets = {"autotile 1": None, "autotile 2": painted_sheet(96, 128)}
        self.assertIsNone(xp_tiles.xp_compose_tile(48, sheets, True))
        self.assertEqual(
            pixel(xp_tiles.xp_compose_tile(96 + 47, sheets, True), 0, 0),
            quarter(0, 0))

    def test_an_animated_autotile_draws_its_first_frame(self):
        # 288 = three 96 px frames side by side; frame 0 is the left one.
        sheet = painted_sheet(288, 128)
        tile = xp_tiles.xp_compose_tile(48, {"autotile 1": sheet}, True)
        self.assertEqual(pixel(tile, 0, 0), quarter(32, 64))

    def test_an_autotile_graphic_of_the_wrong_size_is_not_drawn(self):
        for width, height in ((96, 96), (95, 128), (100, 128), (64, 128)):
            with self.subTest(size=(width, height)):
                sheet = painted_sheet(width, height)
                self.assertIsNone(
                    xp_tiles.xp_compose_tile(48, {"autotile 1": sheet}, True))

    def test_autotiles_are_not_drawn_unless_asked_for(self):
        sheets = {"autotile 1": painted_sheet(96, 128),
                  "tileset": painted_sheet(256, 32)}
        self.assertIsNone(xp_tiles.xp_compose_tile(48, sheets, False))
        self.assertIsNotNone(xp_tiles.xp_compose_tile(384, sheets, False))

    def test_a_character_frame_is_a_quarter_of_the_width_and_height(self):
        sheet = canvas.new_surface(128, 192)
        rect = xp_tiles.xp_sprite_rect
        # 4 x 4 frames of 32 x 48: direction 2 down is row 0, 4 left row 1,
        # 6 right row 2, 8 up row 3; pattern is the column.
        self.assertEqual(rect(sheet, "a", 0, 2, 0), (0, 0, 32, 48, False))
        self.assertEqual(rect(sheet, "a", 0, 4, 1), (32, 48, 32, 48, False))
        self.assertEqual(rect(sheet, "a", 0, 6, 2), (64, 96, 32, 48, False))
        self.assertEqual(rect(sheet, "a", 0, 8, 3), (96, 144, 32, 48, False))
        # A pattern or direction out of range is held to the sheet.
        self.assertEqual(rect(sheet, "a", 0, 2, 99)[0], 96)
        self.assertEqual(rect(sheet, "a", 0, 99, -5)[:2], (0, 0))


# ---------------------------------------------------------------------------
# The scene, with a layout
# ---------------------------------------------------------------------------


def grid_of(width, height, *layers):
    cells = array.array("H")
    for layer in layers:
        cells.extend(layer)
    return TileGrid(width, height, len(layers), cells)


class TestTheSceneWithAnXpLayout(unittest.TestCase):
    def test_a_tile_with_a_priority_is_drawn_over_a_tile_event_not_under_it(self):
        # Two cells. Layer 0: id 384 (priority 0) then 385 (priority 1). A
        # tile event shows id 386 in each cell. The priority-1 tile must
        # cover the event; the priority-0 one must not.
        sheet = painted_sheet(256, 32)
        priorities = [0] * 384 + [0, 1, 0]
        grid = grid_of(2, 1, [384, 385], [0, 0], [0, 0])
        sprites = [Sprite(1, 0, 0, tile_id=386), Sprite(2, 1, 0, tile_id=386)]
        # The same id under the priority-0 tile and above it is told apart by
        # which of the two ends up on top: paint the layer tile as 385 in both
        # cells for the second run.
        result = render_map(grid, priorities, {"tileset": sheet},
                            (0, 0, 2, 1), sprites=sprites,
                            layout=layouts.xp_layout(False, False))
        # Cell 0: ground 384 (below), event tile 386 drawn after it -> 386.
        self.assertEqual(pixel(result.image, 4, 4), quarter(2 * 32 + 4, 4))
        # Cell 1: ground 385 has priority 1 -> drawn after the event -> 385.
        self.assertEqual(pixel(result.image, 32 + 4, 4),
                         quarter(1 * 32 + 4, 4))

    def test_an_id_with_no_priority_entry_is_below_characters(self):
        sheet = painted_sheet(256, 32)
        grid = grid_of(2, 1, [384, 385], [0, 0], [0, 0])
        sprites = [Sprite(1, 1, 0, tile_id=386)]
        result = render_map(grid, [], {"tileset": sheet}, (0, 0, 2, 1),
                            sprites=sprites,
                            layout=layouts.xp_layout(False, False))
        self.assertEqual(pixel(result.image, 32 + 4, 4),
                         quarter(2 * 32 + 4, 4))

    def test_autotile_cells_are_counted_by_sheet_when_not_drawn(self):
        grid = grid_of(3, 1, [48, 96, 384], [0, 0, 0], [0, 0, 0])
        result = render_map(grid, [], {"tileset": painted_sheet(256, 32)},
                            (0, 0, 3, 1),
                            layout=layouts.xp_layout(False, False))
        self.assertEqual(result.tiles_drawn, 1)
        self.assertEqual(result.tiles_not_drawn,
                         {"autotile 1": 1, "autotile 2": 1})

    def test_characters_are_markers_unless_the_layout_draws_them(self):
        grid = grid_of(1, 1, [0], [0], [0])
        sprites = [Sprite(7, 0, 0, name="Hero")]
        sheet = canvas.new_surface(128, 192, (40, 200, 40, 255))

        def load(name):
            return sheet

        plain = render_map(grid, [], {}, (0, 0, 1, 1), sprites=sprites,
                           load_character=load,
                           layout=layouts.xp_layout(False, False))
        self.assertEqual(plain.sprites_drawn, 0)
        self.assertEqual([event for event, _ in plain.sprites_marked], [7])
        self.assertIn("unverified_formats", plain.sprites_marked[0][1])
        drawn = render_map(grid, [], {}, (0, 0, 1, 1), sprites=sprites,
                           load_character=load,
                           layout=layouts.xp_layout(False, True))
        self.assertEqual(drawn.sprites_drawn, 1)
        self.assertEqual(drawn.sprites_marked, [])

    def test_a_layout_that_cannot_judge_passability_refuses_the_overlay(self):
        grid = grid_of(1, 1, [0], [0], [0])
        for name in ("passability", "terrain_tags"):
            with self.subTest(overlay=name):
                with self.assertRaises(ValueError) as caught:
                    render_map(grid, [], {}, (0, 0, 1, 1), overlays=[name],
                               layout=layouts.xp_layout(False, False))
                self.assertIn(name, str(caught.exception))

    def test_event_ids_are_drawn_on_an_xp_layout(self):
        grid = grid_of(1, 1, [0], [0], [0])
        result = render_map(grid, [], {}, (0, 0, 1, 1),
                            sprites=[Sprite(3, 0, 0)],
                            overlays=["event_ids"], sprite_mode="none",
                            layout=layouts.xp_layout(False, False))
        self.assertEqual(result.overlay_cells["event_ids"],
                         {"event_labels": 1})


# ---------------------------------------------------------------------------
# The tool: Ace unchanged
# ---------------------------------------------------------------------------


# SHA-256 of the PNG bytes `map_render` wrote for the demo Ace project before
# any engine-parity change (commit f516fcc), rendered by `AceRender` below
# with its painted sheets. The picture is byte-identical on both sides of the
# change, which is the claim; if an intended change to Ace drawing moves
# these, this is where to say so.
ACE_PLAIN_SHA256 = "d57d278e95840e0c3fbbdc22e6361c965710ed0dc128649debeac5e2042830e0"
ACE_OVERLAYS_SHA256 = "acaec5ece102352e36c7a2eb44da0a205713ff7094a4652af1257247c14ba423"


class AceRender(base.MapRenderTest):
    def png_of(self, **arguments):
        reply = self.render(**arguments)
        return Path(reply["image"]["path"]).read_bytes()


class TestAceIsByteIdentical(AceRender):
    def test_the_plain_picture_is_pinned(self):
        self.assertEqual(hashlib.sha256(self.png_of()).hexdigest(),
                         ACE_PLAIN_SHA256)

    def test_the_picture_with_every_overlay_is_pinned(self):
        data = self.png_of(overlays=["passability", "terrain_tags",
                                     "event_ids"], regions=True, grid=True)
        self.assertEqual(hashlib.sha256(data).hexdigest(),
                         ACE_OVERLAYS_SHA256)

    def test_the_flag_changes_nothing_for_ace(self):
        self.assertEqual(self.png_of(),
                         self.png_of(unverified_formats=True))

    def test_the_reply_for_ace_has_no_engine_warning(self):
        reply = self.render()
        self.assertNotIn("engine_warning", reply)
        self.assertNotIn("unverified_formats", reply)


# ---------------------------------------------------------------------------
# The tool: XP
# ---------------------------------------------------------------------------


class XpRender(ToolTest):
    engine = "xp"

    def setUp(self):
        super().setUp()
        graphics = self.tmp / "project" / "Graphics"
        # 256 x 64: eight tiles wide and two rows, as the fixture's tileset
        # (ids 384 to 391) needs.
        base.painted(graphics / "Tilesets" / "001-Grassland01.png", 256, 64,
                     quarter)
        base.painted(graphics / "Tilesets" / "028-Cave01.png", 256, 64,
                     quarter)
        base.painted(graphics / "Autotiles" / "001-G_Water01.png", 96, 128,
                     quarter)
        base.painted(graphics / "Characters" / "People1.png", 128, 192,
                     lambda x, y: (40, 200, 40))
        map_render_tools._SHEETS.clear()

    def render(self, **arguments):
        arguments.setdefault("map_id", 1)
        return base.unwrap(self, self.call("map_render", **arguments))

    def picture(self, reply):
        return png.decode(Path(reply["image"]["path"]).read_bytes())

    def at(self, image, x, y):
        i = (y * image.width + x) * 4
        return tuple(image.data[i:i + 3])


class TestAnXpRenderByDefault(XpRender):
    def test_tileset_tiles_are_cut_from_the_tileset_graphic(self):
        image = self.picture(self.render())
        # Ground id 384 is cell (0, 0) of the graphic everywhere.
        self.assertEqual(self.at(image, 3 * 32 + 4, 5 * 32 + 4),
                         quarter(4, 4))
        self.assertEqual(self.at(image, 3 * 32 + 20, 5 * 32 + 20),
                         quarter(20, 20))

    def test_the_second_map_uses_its_own_tileset_and_cell(self):
        reply = self.render(map_id=2)
        self.assertEqual(reply["tileset"]["id"], 2)
        image = self.picture(reply)
        # Cave floor id 391 is column 7 of the Cave graphic: x = 224.
        self.assertEqual(self.at(image, 4, 4), quarter(224 + 4, 4))

    def test_autotile_cells_are_not_drawn_and_say_so(self):
        reply = self.render()
        # 20 cells of autotile 1 on row 0, and the ground below each.
        self.assertEqual(reply["drawn"]["tiles"], 20 * 15)
        self.assertIn("autotile 1 20", reply["tiles_note"])
        # Row 0 shows the ground where the autotile would be.
        image = self.picture(reply)
        self.assertEqual(self.at(image, 3 * 32 + 4, 4), quarter(4, 4))

    def test_the_reply_carries_an_engine_warning_that_names_the_gaps(self):
        reply = self.render()
        warning = reply["engine_warning"]
        self.assertIn("RPG Maker XP", warning)
        self.assertIn("autotile", warning)
        self.assertIn("unverified_formats", warning)
        self.assertIn("docs/engine_parity.md", warning)
        self.assertIs(reply["unverified_formats"], False)

    def test_characters_are_markers_with_the_reason(self):
        reply = self.render()
        # The Elder (event 1) stands at (5, 4) and has a character graphic.
        self.assertEqual(reply["drawn"]["event_graphics"], 0)
        self.assertEqual(reply["drawn"]["event_markers"], 1)
        self.assertIn("1 (", reply["events_note"])
        self.assertIn("unverified_formats", reply["events_note"])
        image = self.picture(reply)
        # A marker's outline is black, 3 pixels in from the cell corner.
        self.assertEqual(self.at(image, 5 * 32 + 3, 4 * 32 + 3), (0, 0, 0))

    def test_an_event_that_is_a_tile_is_drawn_as_that_tile(self):
        game_map = self.ctx.project.load_map(1, False)
        graphic = game_map.get("events").get(2).get("pages")[0].get("graphic")
        graphic.set("tile_id", 386)
        image = self.picture(self.render())
        # The door stands at (8, 2); id 386 is column 2 of the tileset.
        self.assertEqual(self.at(image, 8 * 32 + 4, 2 * 32 + 4),
                         quarter(2 * 32 + 4, 4))

    def test_event_ids_can_be_drawn(self):
        reply = self.render(overlays=["event_ids"])
        self.assertEqual(reply["overlay_cells"], {"event_labels": 2})

    def test_passability_is_refused_with_what_is_missing(self):
        with self.assertRaises(ToolError) as caught:
            self.render(overlays=["passability"])
        message = str(caught.exception)
        self.assertIn("passability", message)
        self.assertIn("XP", message)
        self.assertIn("passages", message)
        self.assertIn("priorities", message)
        self.assertIn("docs/engine_parity.md", message)

    def test_terrain_tags_are_refused_too(self):
        with self.assertRaises(ToolError) as caught:
            self.render(overlays=["event_ids", "terrain_tags"])
        self.assertIn("terrain_tags", str(caught.exception))

    def test_regions_are_refused_because_xp_has_none(self):
        with self.assertRaises(ToolError) as caught:
            self.render(regions=True)
        self.assertIn("XP has no region", str(caught.exception))

    def test_a_tileset_with_no_graphic_is_named_in_sheets_not_read(self):
        entry = self.ctx.project.get_entry("tilesets", 1, False)
        entry.set("tileset_name", RubyString(b""))
        reply = self.render()
        self.assertIn("tileset", reply["sheets_not_read"])
        self.assertEqual(reply["drawn"]["tiles"], 0)

    def test_ids_the_data_cannot_hold_are_counted_not_crashed_on(self):
        data = self.ctx.project.load_map(1, False).get("data")
        data.set(0, 1, 0, 65535)      # past the end of a 64 px sheet
        data.set(1, 1, 0, 7)          # 1-47 are not tile ids in XP
        data.set(2, 1, 0, 383)        # autotile 7: no graphic named
        reply = self.render()
        self.assertEqual(reply["drawn"]["tiles"], 20 * 15 - 3)
        self.assertIn("unused ids 1", reply["tiles_note"])
        self.assertIn("tileset 1", reply["tiles_note"])
        self.assertIn("autotile 7 1", reply["tiles_note"])

    def test_the_flag_must_be_a_boolean(self):
        for value in ("yes", 1, 0, [True], "true"):
            with self.subTest(value=value):
                with self.assertRaises(ToolError):
                    self.render(unverified_formats=value)

    def test_nothing_in_the_project_changes(self):
        before = {p: p.read_bytes() for p in
                  (self.tmp / "project" / "Data").iterdir()}
        self.render(unverified_formats=True)
        self.assertEqual({p: p.read_bytes() for p in
                          (self.tmp / "project" / "Data").iterdir()}, before)


class TestAnXpRenderThatTrustsRememberedFormats(XpRender):
    def test_autotiles_are_cut_by_the_remembered_table(self):
        image = self.picture(self.render(unverified_formats=True))
        # Row 0 is autotile 1 pattern 0: pieces 27, 28, 33, 34 at sheet
        # pixels (32, 64), (48, 64), (32, 80), (48, 80).
        self.assertEqual(self.at(image, 3 * 32 + 4, 4), quarter(36, 68))
        self.assertEqual(self.at(image, 3 * 32 + 20, 4), quarter(52, 68))
        self.assertEqual(self.at(image, 3 * 32 + 4, 20), quarter(36, 84))
        self.assertEqual(self.at(image, 3 * 32 + 20, 20), quarter(52, 84))

    def test_nothing_is_left_undrawn_and_the_warning_says_what_was_assumed(self):
        reply = self.render(unverified_formats=True)
        self.assertEqual(reply["drawn"]["tiles"], 20 * 15 + 20)
        self.assertNotIn("tiles_note", reply)
        self.assertIs(reply["unverified_formats"], True)
        warning = reply["engine_warning"]
        self.assertIn("may be wrong", warning)
        self.assertIn("autotile", warning)
        self.assertIn("character", warning)

    def test_a_character_is_drawn_from_a_four_by_four_sheet(self):
        reply = self.render(unverified_formats=True)
        self.assertEqual(reply["drawn"]["event_graphics"], 1)
        self.assertEqual(reply["drawn"]["event_markers"], 0)
        image = self.picture(reply)
        # 128 x 192 sheet: frames are 32 x 48, bottom-aligned on the cell
        # with no lift, so the Elder at (5, 4) covers y from 4 * 32 - 16.
        self.assertEqual(self.at(image, 5 * 32 + 16, 4 * 32 + 20),
                         (40, 200, 40))
        self.assertEqual(self.at(image, 5 * 32 + 16, 4 * 32 - 14),
                         (40, 200, 40))
        self.assertNotEqual(self.at(image, 5 * 32 + 16, 4 * 32 - 18),
                            (40, 200, 40))

    def test_passability_stays_refused_even_so(self):
        with self.assertRaises(ToolError) as caught:
            self.render(unverified_formats=True, overlays=["passability"])
        self.assertIn("passages", str(caught.exception))


# ---------------------------------------------------------------------------
# The tool: VX
# ---------------------------------------------------------------------------


class VxRender(ToolTest):
    engine = "vx"

    def setUp(self):
        super().setUp()
        graphics = self.tmp / "project" / "Graphics"
        base.painted(graphics / "System" / "TileA2.png", 512, 384, quarter)
        base.painted(graphics / "System" / "TileB.png", 512, 512, quarter)
        base.painted(graphics / "Characters" / "People1.png", 384, 256,
                     lambda x, y: (40, 200, 40))
        map_render_tools._SHEETS.clear()

    def render(self, **arguments):
        arguments.setdefault("map_id", 1)
        return base.unwrap(self, self.call("map_render", **arguments))

    def picture(self, reply):
        return png.decode(Path(reply["image"]["path"]).read_bytes())

    def at(self, image, x, y):
        i = (y * image.width + x) * 4
        return tuple(image.data[i:i + 3])


class TestAVxRenderByDefault(VxRender):
    def test_it_is_refused_and_the_message_says_what_is_missing(self):
        with self.assertRaises(ToolError) as caught:
            self.render()
        message = str(caught.exception)
        self.assertIn("RPG Maker VX", message)
        # What is known, and what is not.
        self.assertIn("Graphics/System", message)
        self.assertIn("TileA1", message)
        self.assertIn("tile id", message)
        self.assertIn("shape", message)
        self.assertIn("unverified_formats", message)
        self.assertIn("map_tiles", message)
        self.assertIn("docs/engine_parity.md", message)

    def test_nothing_is_written_when_it_is_refused(self):
        renders = self.tmp / "project" / ".rpgvxace-mcp" / "renders"
        with self.assertRaises(ToolError):
            self.render()
        self.assertFalse(renders.exists())


class TestAVxRenderThatTrustsRememberedFormats(VxRender):
    def test_it_draws_with_the_ace_bands_from_the_system_sheets(self):
        reply = self.render(unverified_formats=True)
        image = self.picture(reply)
        # Layer 0 is 2816: A2 kind 16 shape 0, quarters (2,4) (1,4) (2,3)
        # (1,3) of the block at the sheet's origin, as for Ace.
        self.assertEqual(self.at(image, 8 * 32 + 8, 8 * 32 + 8),
                         quarter(2 * 16, 4 * 16))
        self.assertEqual(self.at(image, 8 * 32 + 24, 8 * 32 + 24),
                         quarter(1 * 16, 3 * 16))
        # Layer 1 puts B tile 20 along row 0: column 4, row 2 of TileB.
        self.assertEqual(self.at(image, 3 * 32 + 4, 4), quarter(4 * 32, 2 * 32))

    def test_the_warning_says_the_picture_may_be_wrong(self):
        reply = self.render(unverified_formats=True)
        warning = reply["engine_warning"]
        self.assertIn("RPG Maker VX", warning)
        self.assertIn("may be wrong", warning)
        self.assertIn("flag", warning)
        self.assertIs(reply["unverified_formats"], True)
        self.assertIsNone(reply["tileset"]["id"])

    def test_the_overlays_that_need_vxs_flag_table_are_refused(self):
        for name in ("passability", "terrain_tags"):
            with self.subTest(overlay=name):
                with self.assertRaises(ToolError) as caught:
                    self.render(unverified_formats=True, overlays=[name])
                self.assertIn(name, str(caught.exception))
                self.assertIn("VX", str(caught.exception))

    def test_regions_are_refused_because_vx_has_none(self):
        with self.assertRaises(ToolError) as caught:
            self.render(unverified_formats=True, regions=True)
        self.assertIn("VX has no region", str(caught.exception))

    def test_a_missing_sheet_is_named_by_its_file(self):
        (self.tmp / "project" / "Graphics" / "System" / "TileB.png").unlink()
        map_render_tools._SHEETS.clear()
        self.ctx.project.forget_assets()
        reply = self.render(unverified_formats=True)
        self.assertIn("B", reply["sheets_not_read"])
        self.assertIn("TileB", reply["sheets_not_read"]["B"])


# ---------------------------------------------------------------------------
# The tools that stay refused
# ---------------------------------------------------------------------------


class StaysRefused(ToolTest):
    """The two other tools, on VX and XP, with a reason that is specific."""

    def refusal(self, tool, **arguments):
        with self.assertRaises(ToolError) as caught:
            self.call(tool, **arguments)
        return str(caught.exception)

    PAINT = dict(op="fill", map_id=1, kind="A2:0", x=0, y=0, width=1,
                 height=1)


class TestVxStaysRefused(StaysRefused):
    engine = "vx"

    def test_map_paint_names_the_unknown_numbering(self):
        message = self.refusal("map_paint", **self.PAINT)
        self.assertIn("VX", message)
        self.assertIn("shape", message)
        self.assertIn("map_tiles", message)
        self.assertIn("docs/engine_parity.md", message)

    def test_live_says_why_it_is_ace_only(self):
        message = self.refusal("live", op="start")
        self.assertIn("RPG Maker VX", message)
        self.assertIn("VX Ace", message)
        self.assertIn("bridge", message)
        self.assertIn("RGSS3", message)
        self.assertIn("docs/engine_parity.md", message)


class TestXpStaysRefused(StaysRefused):
    engine = "xp"

    def test_map_paint_names_what_it_would_have_to_write(self):
        message = self.refusal("map_paint", **self.PAINT)
        self.assertIn("XP", message)
        self.assertIn("48", message)
        self.assertIn("map_tiles", message)
        self.assertIn("docs/engine_parity.md", message)

    def test_live_says_why_it_is_ace_only(self):
        message = self.refusal("live", op="start")
        self.assertIn("RPG Maker XP", message)
        self.assertIn("VX Ace", message)
        self.assertIn("bridge", message)
        self.assertIn("RGSS3", message)
        self.assertIn("docs/engine_parity.md", message)


if __name__ == "__main__":
    unittest.main()
