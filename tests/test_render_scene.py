"""Drawing a window of a map from sheets painted in a few lines.

No project and no files: `scene.render_map` takes plain data, so every case
here is a handful of cells and a sheet whose pixels say where they came from.
"""

import sys
import unittest
from array import array
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from rpgvxace_mcp.render import canvas, scene, tiles  # noqa: E402
from rpgvxace_mcp.render.scene import Sprite, TileGrid  # noqa: E402


RED = (200, 0, 0, 255)
BLUE = (0, 0, 200, 255)
GREEN = (0, 200, 0, 255)
BACKGROUND = (24, 24, 32, 255)


def solid(width, height, colour):
    return canvas.new_surface(width, height, colour)


def frames(columns, rows, size=32):
    """A sheet of `columns` x `rows` frames, each a distinct flat colour."""
    image = canvas.new_surface(columns * size, rows * size)
    for fy in range(rows):
        for fx in range(columns):
            canvas.blend_rect(image, fx * size, fy * size, size, size,
                              (fx * 10 + 5, fy * 10 + 5, 77), 255)
    for i in range(3, len(image.data), 4):
        image.data[i] = 255
    return image


def frame_colour(fx, fy):
    return (fx * 10 + 5, fy * 10 + 5, 77, 255)


def grid_of(width, height, cells=None, depth=4):
    """A map grid with `cells` given as {(x, y, z): id}."""
    data = array("H", bytes(2 * width * height * depth))
    for (x, y, z), value in (cells or {}).items():
        data[x + y * width + z * width * height] = value
    return TileGrid(width, height, depth, data)


def pixel(image, x, y):
    at = (y * image.width + x) * 4
    return tuple(image.data[at:at + 4])


def draw(grid, window=None, flags=(), sheets=None, **options):
    window = window or (0, 0, grid.width, grid.height)
    return scene.render_map(grid, flags, sheets or {}, window, **options)


class TestTheTileLayers(unittest.TestCase):
    def test_an_empty_map_is_the_background(self):
        result = draw(grid_of(2, 2))
        self.assertEqual((result.image.width, result.image.height), (64, 64))
        self.assertEqual(pixel(result.image, 5, 5), BACKGROUND)
        self.assertEqual(result.tiles_drawn, 0)

    def test_a_tile_lands_in_its_cell(self):
        sheets = {"B": frames(16, 16)}
        # id 9 is column 1, row 1 of sheet B.
        result = draw(grid_of(3, 2, {(2, 1, 0): 9}), sheets=sheets)
        self.assertEqual(pixel(result.image, 2 * 32 + 3, 32 + 3),
                         frame_colour(1, 1))
        self.assertEqual(pixel(result.image, 3, 3), BACKGROUND)
        self.assertEqual(result.tiles_drawn, 1)

    def test_later_layers_draw_over_earlier_ones(self):
        sheets = {"B": frames(16, 16)}
        cells = {(0, 0, 0): 1, (0, 0, 1): 2, (0, 0, 2): 3}
        result = draw(grid_of(1, 1, cells), sheets=sheets)
        self.assertEqual(pixel(result.image, 5, 5), frame_colour(3, 0))

    def test_a_transparent_upper_tile_shows_the_one_beneath(self):
        upper = canvas.new_surface(512, 512)       # all clear
        sheets = {"B": frames(16, 16), "C": upper}
        cells = {(0, 0, 0): 1, (0, 0, 1): 256 + 1}
        result = draw(grid_of(1, 1, cells), sheets=sheets)
        self.assertEqual(pixel(result.image, 5, 5), frame_colour(1, 0))

    def test_a_window_draws_only_its_cells(self):
        sheets = {"B": frames(16, 16)}
        grid = grid_of(4, 3, {(1, 1, 0): 1, (2, 1, 0): 2, (0, 0, 0): 3})
        result = draw(grid, window=(1, 1, 2, 1), sheets=sheets)
        self.assertEqual((result.image.width, result.image.height), (64, 32))
        self.assertEqual(pixel(result.image, 4, 4), frame_colour(1, 0))
        self.assertEqual(pixel(result.image, 36, 4), frame_colour(2, 0))
        self.assertEqual(result.tiles_drawn, 2)

    def test_tiles_on_a_missing_sheet_are_counted_not_drawn(self):
        grid = grid_of(2, 1, {(0, 0, 0): 1, (1, 0, 0): 2816, (1, 0, 1): 1100})
        result = draw(grid, sheets={"B": None})
        self.assertEqual(result.tiles_not_drawn,
                         {"B": 1, "A2": 1, "unused ids": 1})
        self.assertEqual(pixel(result.image, 5, 5), BACKGROUND)

    def test_a_flagged_tile_goes_over_a_character(self):
        sheets = {"B": solid(512, 512, BLUE), "C": solid(512, 512, RED)}
        sprites = [Sprite(1, 0, 0, "$Hero")]
        grid = grid_of(2, 1, {(0, 0, 0): 1, (1, 0, 0): 1, (0, 0, 1): 256})
        flags = [0] * 1024
        flags[256] = tiles.STAR_FLAG
        hero = solid(96, 128, GREEN)
        result = draw(grid, flags=flags, sheets=sheets, sprites=sprites,
                      load_character=lambda name: hero)
        self.assertEqual(pixel(result.image, 16, 16), RED)
        # The same tile without the flag lies under the character instead.
        flags[256] = 0
        result = draw(grid, flags=flags, sheets=sheets, sprites=sprites,
                      load_character=lambda name: hero)
        self.assertEqual(pixel(result.image, 16, 16), GREEN)

    def test_a_flag_word_shorter_than_the_ids_is_zero(self):
        sheets = {"B": solid(512, 512, BLUE)}
        result = draw(grid_of(1, 1, {(0, 0, 0): 5}), flags=[1], sheets=sheets)
        self.assertEqual(pixel(result.image, 5, 5), BLUE)


class TestShadowsAndRegions(unittest.TestCase):
    def test_each_shadow_bit_darkens_its_quarter(self):
        sheets = {"B": solid(512, 512, (200, 200, 200, 255))}
        for bit, qx, qy in ((1, 0, 0), (2, 1, 0), (4, 0, 1), (8, 1, 1)):
            grid = grid_of(1, 1, {(0, 0, 0): 1, (0, 0, 3): bit})
            image = draw(grid, sheets=sheets).image
            for x, y in ((0, 0), (1, 0), (0, 1), (1, 1)):
                at = pixel(image, x * 16 + 8, y * 16 + 8)
                with self.subTest(bit=bit, quarter=(x, y)):
                    self.assertEqual(at[0], 100 if (x, y) == (qx, qy) else 200)

    def test_shadows_can_be_switched_off(self):
        sheets = {"B": solid(512, 512, (200, 200, 200, 255))}
        grid = grid_of(1, 1, {(0, 0, 0): 1, (0, 0, 3): 15})
        image = draw(grid, sheets=sheets, shadows=False).image
        self.assertEqual(pixel(image, 8, 8)[0], 200)

    def test_the_region_id_is_the_byte_above_the_shadow_bits(self):
        sheets = {"B": solid(512, 512, (200, 200, 200, 255))}
        grid = grid_of(2, 1, {(0, 0, 0): 1, (0, 0, 3): 3 << 8,
                              (1, 0, 3): 3})
        plain = draw(grid, sheets=sheets).image
        marked = draw(grid, sheets=sheets, regions=True).image
        # Region 3 is not a shadow, and with the overlay off it is invisible.
        self.assertEqual(pixel(plain, 28, 28)[0], 200)
        self.assertNotEqual(pixel(marked, 28, 28), pixel(plain, 28, 28))
        # Low bits are shadow, not region, so the second cell has no overlay.
        self.assertEqual(pixel(marked, 32 + 28, 28), pixel(plain, 32 + 28, 28))

    def test_a_map_without_the_extra_layer_draws_without_it(self):
        sheets = {"B": solid(512, 512, BLUE)}
        result = draw(grid_of(1, 1, {(0, 0, 0): 1}, depth=3), sheets=sheets,
                      regions=True)
        self.assertEqual(pixel(result.image, 5, 5), BLUE)


class TestCharacters(unittest.TestCase):
    def sprite_at(self, name, **frame):
        sheet = frames(12, 8) if not name.lstrip("!").startswith("$") \
            else frames(3, 4)
        sprite = Sprite(7, 1, 1, name, **frame)
        return draw(grid_of(3, 3), sprites=[sprite],
                    load_character=lambda n: sheet)

    def test_a_standard_sheet_picks_character_direction_and_pattern(self):
        # character 5 is column 1, row 1 of characters; facing left is the
        # second row of its four; pattern 2 is the third frame.
        result = self.sprite_at("People1", index=5, direction=4, pattern=2)
        self.assertEqual(pixel(result.image, 48, 48), frame_colour(5, 5))
        self.assertEqual(result.sprites_drawn, 1)

    def test_every_direction_and_pattern_is_a_different_frame(self):
        seen = set()
        for direction in (2, 4, 6, 8):
            for pattern in (0, 1, 2):
                result = self.sprite_at("People1", index=0,
                                        direction=direction, pattern=pattern)
                seen.add(pixel(result.image, 48, 48))
        self.assertEqual(len(seen), 12)

    def test_a_dollar_sheet_is_one_character(self):
        result = self.sprite_at("$Boss", direction=6, pattern=1)
        self.assertEqual(pixel(result.image, 48, 48), frame_colour(1, 2))

    def test_the_sprite_stands_on_its_tile_four_pixels_up(self):
        sheet = solid(12 * 32, 8 * 32, GREEN)
        sprite = Sprite(1, 0, 0, "People1")
        image = draw(grid_of(1, 2), sprites=[sprite],
                     load_character=lambda n: sheet).image
        # 32 pixels tall, bottom edge at 28: the top four rows are cut off.
        self.assertEqual(pixel(image, 16, 27), GREEN)
        self.assertEqual(pixel(image, 16, 28), BACKGROUND)

    def test_a_bang_sheet_is_not_lifted(self):
        sheet = solid(12 * 32, 8 * 32, GREEN)
        sprite = Sprite(1, 0, 0, "!Door")
        image = draw(grid_of(1, 2), sprites=[sprite],
                     load_character=lambda n: sheet).image
        self.assertEqual(pixel(image, 16, 31), GREEN)

    def test_a_tall_character_overhangs_the_tile_above(self):
        sheet = solid(12 * 32, 8 * 48, GREEN)       # 32 x 48 frames
        sprite = Sprite(1, 0, 1, "Tall")
        image = draw(grid_of(1, 2), sprites=[sprite],
                     load_character=lambda n: sheet).image
        self.assertEqual(pixel(image, 16, 20), GREEN)   # in the tile above

    def test_the_sprite_lower_on_the_map_is_drawn_over_the_one_above(self):
        sheets = {"Upper": solid(12 * 32, 8 * 32, GREEN),
                  "Lower": solid(12 * 32, 8 * 64, RED)}
        # Listed lowest first, so only sorting by row puts it on top.
        sprites = [Sprite(2, 0, 1, "Lower"), Sprite(1, 0, 0, "Upper")]
        image = draw(grid_of(1, 2), sprites=sprites,
                     load_character=lambda name: sheets[name]).image
        # "Lower" is 64 pixels tall, so it reaches up over "Upper".
        self.assertEqual(pixel(image, 16, 10), RED)
        self.assertEqual(pixel(image, 16, 40), RED)

    def test_a_missing_graphic_becomes_a_marker_with_its_id(self):
        sprite = Sprite(12, 1, 0, "Nobody")
        result = draw(grid_of(3, 1), sprites=[sprite],
                      load_character=lambda n: None)
        self.assertEqual(result.sprites_drawn, 0)
        self.assertEqual([i for i, _ in result.sprites_marked], [12])
        self.assertIn("Nobody", result.sprites_marked[0][1])
        self.assertNotEqual(pixel(result.image, 32 + 16, 16), BACKGROUND)
        self.assertEqual(pixel(result.image, 3, 3), BACKGROUND)

    def test_an_event_with_no_graphic_is_invisible(self):
        result = draw(grid_of(2, 1), sprites=[Sprite(1, 0, 0)],
                      load_character=lambda n: None)
        self.assertEqual(pixel(result.image, 16, 16), BACKGROUND)
        self.assertEqual(result.sprites_marked, [])

    def test_a_tile_graphic_is_drawn_as_that_tile(self):
        sheets = {"B": frames(16, 16)}
        result = draw(grid_of(2, 1), sheets=sheets,
                      sprites=[Sprite(1, 1, 0, tile_id=9)])
        self.assertEqual(pixel(result.image, 32 + 5, 5), frame_colour(1, 1))

    def test_markers_mode_boxes_every_event_and_none_draws_nothing(self):
        sprites = [Sprite(1, 0, 0, "People1"), Sprite(2, 1, 0)]
        sheet = solid(12 * 32, 8 * 32, GREEN)
        boxed = draw(grid_of(2, 1), sprites=sprites, sprite_mode="markers",
                     load_character=lambda n: sheet)
        self.assertEqual(boxed.sprites_drawn, 0)
        self.assertNotEqual(pixel(boxed.image, 16, 16), GREEN)
        self.assertNotEqual(pixel(boxed.image, 48, 16), BACKGROUND)
        nothing = draw(grid_of(2, 1), sprites=sprites, sprite_mode="none",
                       load_character=lambda n: sheet)
        self.assertEqual(pixel(nothing.image, 16, 16), BACKGROUND)

    def test_events_outside_the_window_are_left_alone(self):
        sheet = solid(12 * 32, 8 * 32, GREEN)
        result = draw(grid_of(30, 1), window=(0, 0, 2, 1),
                      sprites=[Sprite(1, 20, 0, "People1")],
                      load_character=lambda n: sheet)
        self.assertEqual(result.sprites_drawn, 0)


class TestTheGrid(unittest.TestCase):
    def test_lines_and_coordinates_are_drawn_when_asked(self):
        plain = draw(grid_of(6, 6)).image
        lined = draw(grid_of(6, 6), show_grid=True).image
        self.assertEqual(pixel(plain, 32, 10), BACKGROUND)
        self.assertNotEqual(pixel(lined, 32, 10), BACKGROUND)
        self.assertNotEqual(pixel(lined, 10, 32), BACKGROUND)
        # the "5" label for column 5 sits in the top-left of that cell
        label = [pixel(lined, 5 * 32 + dx, dy) for dx in range(2, 14)
                 for dy in range(2, 14)]
        self.assertIn((255, 240, 120, 255), label)


if __name__ == "__main__":
    unittest.main()
