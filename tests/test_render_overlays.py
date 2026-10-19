"""Overlays on `map_render`: passability, terrain tags and event ids.

The first half holds the pure rules in `render.passability` to the engine's
own source, with every expectation worked out by hand from it. The engine
(RGSS3, `Game_Map`) reads, for a move in direction ``d`` (2 down, 4 left,
6 right, 8 up)::

    bit = (1 << (d / 2 - 1)) & 0x0f      # down 0x1, left 0x2, right 0x4, up 0x8
    all_tiles(x, y) = tile-graphic events' tile ids + [z=2, z=1, z=0] tiles
    check_passage(x, y, bit):
      all_tiles.each do |tile_id|
        flag = tileset.flags[tile_id]
        next if flag & 0x10 != 0         # star: no effect on passage
        return true  if flag & bit == 0  # passable
        return false if flag & bit == bit # impassable
      end
      return false                       # nothing decided: impassable
    terrain_tag(x, y):
      [z=2, z=1, z=0].each { |t| tag = flags[t] >> 12; return tag if tag > 0 }
      return 0

The walk is made once PER BIT, so the tile that decides "down" need not be
the tile that decides "left". A tile id with no entry in the flags table
would be nil in the engine (a crash); here it is treated as passable and
reported.

The second half drives the real tool against the demo project.
"""

import hashlib
import os
import sys
import unittest
from array import array
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))

from rpgvxace_mcp.render import overlays, png, scene  # noqa: E402
from rpgvxace_mcp.render.passability import (  # noqa: E402
    DOWN,
    LEFT,
    RIGHT,
    UP,
    event_blocks,
    passage,
    terrain_tag,
)
from rpgvxace_mcp.tools import map_render_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    ImageReply,
    REGISTRY,
    ToolError,
)

from tool_case import ToolTest  # noqa: E402


STAR = 0x10
EVERY_WAY = DOWN | LEFT | RIGHT | UP


def table(size, entries):
    """A flags table of ``size`` zeros with ``{tile_id: flag}`` set."""
    out = [0] * size
    for tile_id, flag in entries.items():
        out[tile_id] = flag
    return out


class TestTheDirectionBits(unittest.TestCase):
    def test_the_four_bits_are_the_engines(self):
        # (1 << (d / 2 - 1)): d=2 -> 1, d=4 -> 2, d=6 -> 4, d=8 -> 8.
        self.assertEqual((DOWN, LEFT, RIGHT, UP), (1, 2, 4, 8))

    def test_each_bit_blocks_only_its_own_edge(self):
        for bit in (DOWN, LEFT, RIGHT, UP):
            flags = table(16, {0: STAR, 10: bit})
            self.assertEqual(passage([0, 0, 10], flags).blocked, bit)

    def test_two_bits_block_two_edges(self):
        flags = table(16, {0: STAR, 10: DOWN | RIGHT})
        self.assertEqual(passage([0, 0, 10], flags).blocked, 0x5)

    def test_all_four_bits_block_the_cell(self):
        flags = table(16, {0: STAR, 10: 0xF})
        self.assertEqual(passage([0, 0, 10], flags).blocked, EVERY_WAY)

    def test_no_bits_leaves_the_cell_open(self):
        flags = table(16, {0: STAR, 10: 0})
        self.assertEqual(passage([0, 0, 10], flags).blocked, 0)


class TestStackedLayers(unittest.TestCase):
    """Stacks are given top first: layer 2, layer 1, layer 0."""

    def test_an_open_upper_tile_decides_over_a_wall_below(self):
        # layer 2 flag 0: every bit is "passable" at the first tile asked.
        flags = table(32, {10: 0xF, 20: 0})
        self.assertEqual(passage([20, 0, 10], flags).blocked, 0)

    def test_a_blocking_upper_tile_decides_over_open_ground_below(self):
        flags = table(32, {10: 0, 20: 0xF})
        self.assertEqual(passage([20, 0, 10], flags).blocked, EVERY_WAY)

    def test_a_star_tile_defers_to_the_one_below(self):
        flags = table(32, {0: STAR, 10: LEFT | UP, 20: STAR})
        self.assertEqual(passage([20, 0, 10], flags).blocked, LEFT | UP)

    def test_a_star_tile_blocks_nothing_whatever_its_direction_bits(self):
        # 0x10 | 0x1: the star bit makes the tile skipped before its down bit
        # is read, so open ground below leaves the cell open.
        flags = table(32, {0: STAR, 10: 0, 20: STAR | DOWN})
        self.assertEqual(passage([20, 0, 10], flags).blocked, 0)

    def test_the_deciding_tile_is_chosen_per_bit(self):
        # Layer 2 blocks only left (0x2); layer 0 blocks only right (0x4).
        # down: layer 2 flag & 1 == 0 -> passable, decided at layer 2.
        # left: layer 2 flag & 2 == 2 -> impassable, decided at layer 2.
        # right: layer 2 flag & 4 == 0 -> passable, decided at layer 2, so the
        #   right-blocking tile below is never read.
        # up: layer 2 flag & 8 == 0 -> passable.
        flags = table(32, {10: RIGHT, 20: LEFT})
        self.assertEqual(passage([20, 0, 10], flags).blocked, LEFT)

    def test_a_middle_layer_decides_when_the_top_is_a_star(self):
        flags = table(32, {5: STAR, 6: RIGHT, 7: 0})
        # layer 2 star (skip); layer 1 = tile 6 decides: right blocked.
        self.assertEqual(passage([5, 6, 7], flags).blocked, RIGHT)

    def test_a_stack_of_nothing_but_stars_is_impassable(self):
        # The engine's final `return false`.
        flags = table(32, {0: STAR, 20: STAR})
        self.assertEqual(passage([0, 20, 0], flags).blocked, EVERY_WAY)

    def test_no_tiles_at_all_is_impassable(self):
        self.assertEqual(passage([], table(8, {})).blocked, EVERY_WAY)

    def test_the_empty_tile_is_read_like_any_other(self):
        # The engine does not skip tile 0: its flags entry decides. The
        # editor writes the star bit there, which is why empty layers are
        # transparent; a tileset that does not would make an empty top layer
        # open every cell.
        starred = table(16, {10: LEFT})
        starred[0] = STAR
        self.assertEqual(passage([0, 0, 10], starred).blocked, LEFT)
        unstarred = table(16, {10: LEFT})
        self.assertEqual(passage([0, 0, 10], unstarred).blocked, 0)
        walled = table(16, {10: 0})
        walled[0] = 0xF
        self.assertEqual(passage([0, 0, 10], walled).blocked, EVERY_WAY)

    def test_tile_graphic_events_come_first(self):
        # `all_tiles` is the events' tile ids, then the layers.
        flags = table(32, {0: STAR, 10: 0, 20: DOWN})
        self.assertEqual(passage([20, 0, 0, 10], flags).blocked, DOWN)
        self.assertEqual(passage([0, 0, 10], flags).blocked, 0)


class TestTileIdsFromEverySheet(unittest.TestCase):
    """A tile's flags entry is `flags[tile_id]`, whichever sheet the id is on."""

    def check(self, tile_id, flag=0xF):
        flags = table(8192, {0: STAR, tile_id: flag})
        self.assertEqual(passage([0, 0, tile_id], flags).blocked, flag & 0xF,
                         "tile id %d" % tile_id)

    def test_b_to_e_ids(self):
        # B 0-255, C 256-511, D 512-767, E 768-1023.
        for tile_id in (1, 255, 256, 511, 512, 767, 768, 1023):
            self.check(tile_id, LEFT)

    def test_a5_ids(self):
        for tile_id in (1536, 1600, 1663):
            self.check(tile_id, RIGHT)

    def test_a1_to_a4_autotile_ids(self):
        # id = 2048 + kind * 48 + shape: kind 0 shape 0; kind 5 shape 3;
        # the first of A2 (2816), A3 (4352), A4 (5888) and the last id (8191).
        for tile_id in (2048, 2048 + 5 * 48 + 3, 2816, 4352, 5888, 8191):
            self.check(tile_id, UP | DOWN)

    def test_neighbouring_autotile_shapes_have_their_own_entries(self):
        flags = table(8192, {0: STAR, 2300: LEFT, 2301: RIGHT})
        self.assertEqual(passage([0, 0, 2300], flags).blocked, LEFT)
        self.assertEqual(passage([0, 0, 2301], flags).blocked, RIGHT)


class TestIdsTheTableDoesNotHold(unittest.TestCase):
    def test_an_id_past_the_table_is_passable_and_reported(self):
        flags = table(100, {0: STAR})
        result = passage([0, 0, 500], flags)
        self.assertEqual(result.blocked, 0)
        self.assertTrue(result.unknown)

    def test_a_known_stack_is_not_reported(self):
        self.assertFalse(passage([0, 0, 10], table(100, {0: STAR})).unknown)

    def test_an_unknown_id_decides_before_a_known_wall_below_it(self):
        # Table of 12 entries; id 50 on layer 2 has none, so it is "passable"
        # at the first tile asked and the wall at layer 0 is never read.
        flags = table(12, {10: 0xF})
        result = passage([50, 0, 10], flags)
        self.assertEqual(result.blocked, 0)
        self.assertTrue(result.unknown)

    def test_an_unknown_id_below_a_decided_tile_is_never_consulted(self):
        flags = table(12, {0: STAR, 10: 0xF})
        result = passage([10, 0, 50], flags)
        self.assertEqual(result.blocked, EVERY_WAY)
        self.assertFalse(result.unknown)

    def test_a_table_of_no_entries_makes_every_id_unknown(self):
        result = passage([0, 0, 7], [])
        self.assertEqual(result.blocked, 0)
        self.assertTrue(result.unknown)

    def test_hostile_ids_do_not_crash(self):
        flags = table(8192, {})
        for tile_id in (65535, 8192, -1, -32768, 2 ** 40):
            result = passage([tile_id, tile_id, tile_id], flags)
            self.assertEqual(result.blocked, 0, tile_id)
            self.assertTrue(result.unknown, tile_id)
            self.assertEqual(terrain_tag([tile_id] * 3, flags), (0, True))

    def test_flag_words_with_the_sign_bit_set_are_read_bitwise(self):
        # A Table of int16 gives negative words. -1 is 0xFFFF: its star bit
        # (0x10) is set, so the tile is skipped and nothing decides.
        flags = table(16, {0: STAR, 10: -1})
        self.assertEqual(passage([0, 0, 10], flags).blocked, EVERY_WAY)
        # -32768 | 3 is 0x8003: no star, left and down set.
        flags = table(16, {0: STAR, 10: -32768 | 0x3})
        self.assertEqual(passage([0, 0, 10], flags).blocked, 0x3)


class TestTerrainTags(unittest.TestCase):
    def test_the_tag_is_the_flag_shifted_down_twelve_bits(self):
        for tag in range(1, 8):
            flags = table(16, {10: tag << 12})
            self.assertEqual(terrain_tag([0, 0, 10], flags), (tag, False))

    def test_the_low_bits_do_not_leak_into_the_tag(self):
        flags = table(16, {10: 0x0FFF})
        self.assertEqual(terrain_tag([0, 0, 10], flags), (0, False))

    def test_the_topmost_nonzero_tag_wins(self):
        flags = table(32, {10: 0x2000, 20: 0x1000, 30: 0})
        self.assertEqual(terrain_tag([20, 0, 10], flags)[0], 1)
        self.assertEqual(terrain_tag([30, 0, 10], flags)[0], 2)

    def test_a_star_tile_still_carries_its_tag(self):
        # `terrain_tag` does not skip 0x10, only `check_passage` does.
        flags = table(32, {10: 0x3000, 20: 0x5000 | STAR})
        self.assertEqual(terrain_tag([20, 0, 10], flags)[0], 5)

    def test_a_negative_word_has_no_tag(self):
        # -28672 is 0x9000 as an int16; shifted down it is -7, which is not
        # `> 0`, so the engine moves on to the layer below.
        flags = table(32, {0: STAR, 10: 0x4000, 20: -28672})
        self.assertEqual(terrain_tag([20, 0, 10], flags)[0], 4)

    def test_a_table_stores_words_unsigned_but_the_engine_reads_them_signed(self):
        # `Table` keeps 16-bit words unsigned, so 0x9000 arrives as 36864.
        # RGSS reads it as -28672; `>> 12` gives -7, not above zero, and the
        # layer below decides. Read unsigned it would be tag 9.
        flags = table(32, {0: STAR, 10: 0x4000, 20: 0x9000})
        self.assertEqual(terrain_tag([20, 0, 10], flags)[0], 4)
        self.assertEqual(terrain_tag([20, 0, 0], flags)[0], 0)

    def test_the_largest_tag_is_seven(self):
        flags = table(32, {10: 0x7FFF})
        self.assertEqual(terrain_tag([10], flags)[0], 7)

    def test_an_untagged_stack_is_zero(self):
        self.assertEqual(terrain_tag([0, 0, 10], table(16, {})), (0, False))


class TestEventsThatBlock(unittest.TestCase):
    def test_only_same_as_characters_and_not_through(self):
        # Game_CharacterBase#collide_with_events?: events_xy_nt (not through)
        # and normal_priority? (priority_type == 1).
        self.assertTrue(event_blocks(through=False, priority_type=1))
        self.assertFalse(event_blocks(through=True, priority_type=1))
        self.assertFalse(event_blocks(through=False, priority_type=0))
        self.assertFalse(event_blocks(through=False, priority_type=2))
        self.assertFalse(event_blocks(through=True, priority_type=0))

    def test_odd_values_do_not_block(self):
        self.assertFalse(event_blocks(through=False, priority_type=True))
        self.assertFalse(event_blocks(through=False, priority_type="1"))

    def test_ruby_truthiness_decides_through(self):
        # Only nil and false are false in Ruby: a damaged page holding 0 or
        # "" in `through` is a page that is through.
        self.assertTrue(event_blocks(through=None, priority_type=1))
        self.assertFalse(event_blocks(through=0, priority_type=1))
        self.assertFalse(event_blocks(through="", priority_type=1))


# ---------------------------------------------------------------------------
# The scene: marks in the right pixels. Expected colours are worked out by
# hand from the blend rule (channel * (255 - alpha) + mark * alpha + 127) // 255
# over the background (24, 24, 32), e.g. the red tint at alpha 105:
#   R (24 * 150 + 220 * 105 + 127) // 255 = 105
#   G (24 * 150 +  40 * 105 + 127) // 255 = 31
#   B (32 * 150 +  40 * 105 + 127) // 255 = 35
# ---------------------------------------------------------------------------


TINTED = (105, 31, 35, 255)
BAR = (237, 200, 3, 255)
FRAME = (1, 213, 242, 255)
BACKGROUND = (24, 24, 32, 255)
PLATE = (8, 8, 11, 255)
WHITE = (255, 255, 255, 255)
LABEL = (255, 240, 120, 255)


def grid_of(width, height, cells=None, depth=4):
    data = array("H", bytes(2 * width * height * depth))
    for (x, y, z), value in (cells or {}).items():
        data[x + y * width + z * width * height] = value
    return scene.TileGrid(width, height, depth, data)


def pixel(image, x, y):
    at = (y * image.width + x) * 4
    return tuple(image.data[at:at + 4])


def draw(grid, flags, window=None, **options):
    window = window or (0, 0, grid.width, grid.height)
    return scene.render_map(grid, flags, {}, window, **options)


def row_of_four():
    """Cells: a wall, a down-blocked, a left+right-blocked, an open one."""
    flags = table(32, {0: STAR, 10: 0xF, 11: DOWN, 12: LEFT | RIGHT, 13: 0})
    grid = grid_of(4, 1, {(0, 0, 0): 10, (1, 0, 0): 11, (2, 0, 0): 12,
                          (3, 0, 0): 13})
    return grid, flags


class TestPassabilityIsDrawn(unittest.TestCase):
    def test_a_wall_is_tinted_and_an_open_cell_is_not(self):
        grid, flags = row_of_four()
        result = draw(grid, flags, overlays=("passability",))
        self.assertEqual(pixel(result.image, 16, 16), TINTED)
        self.assertEqual(pixel(result.image, 3 * 32 + 16, 16), BACKGROUND)

    def test_a_blocked_edge_gets_a_bar_on_that_edge_only(self):
        grid, flags = row_of_four()
        image = draw(grid, flags, overlays=("passability",)).image
        # Cell 1 blocks down: the bottom four rows, not the top or the middle.
        self.assertEqual(pixel(image, 32 + 16, 31), BAR)
        self.assertEqual(pixel(image, 32 + 16, 28), BAR)
        self.assertEqual(pixel(image, 32 + 16, 27), BACKGROUND)
        self.assertEqual(pixel(image, 32 + 16, 0), BACKGROUND)
        self.assertEqual(pixel(image, 32 + 16, 16), BACKGROUND)
        # Cell 2 blocks left and right: a bar at each side, none in the middle.
        self.assertEqual(pixel(image, 64, 16), BAR)
        self.assertEqual(pixel(image, 64 + 3, 16), BAR)
        self.assertEqual(pixel(image, 64 + 4, 16), BACKGROUND)
        self.assertEqual(pixel(image, 64 + 31, 16), BAR)
        self.assertEqual(pixel(image, 64 + 27, 16), BACKGROUND)
        self.assertEqual(pixel(image, 64 + 16, 0), BACKGROUND)

    def test_up_is_the_top_edge(self):
        flags = table(32, {0: STAR, 10: UP})
        image = draw(grid_of(1, 1, {(0, 0, 0): 10}), flags,
                     overlays=("passability",)).image
        self.assertEqual(pixel(image, 16, 0), BAR)
        self.assertEqual(pixel(image, 16, 4), BACKGROUND)
        self.assertEqual(pixel(image, 16, 31), BACKGROUND)

    def test_the_counts(self):
        grid, flags = row_of_four()
        result = draw(grid, flags, overlays=("passability",))
        self.assertEqual(result.overlay_cells["passability"],
                         {"blocked": 1, "edge_blocked": 2, "event_blocked": 0})

    def test_without_the_overlay_nothing_is_marked(self):
        grid, flags = row_of_four()
        result = draw(grid, flags)
        self.assertEqual(pixel(result.image, 16, 16), BACKGROUND)
        self.assertEqual(result.overlay_cells, {})

    def test_only_the_window_is_counted(self):
        grid, flags = row_of_four()
        result = draw(grid, flags, window=(1, 0, 2, 1),
                      overlays=("passability",))
        self.assertEqual(result.overlay_cells["passability"],
                         {"blocked": 0, "edge_blocked": 2, "event_blocked": 0})
        self.assertEqual(pixel(result.image, 16, 31), BAR)

    def test_a_solid_event_is_framed_in_another_colour(self):
        grid, flags = row_of_four()
        sprites = [scene.Sprite(1, 3, 0, through=False, priority_type=1)]
        result = draw(grid, flags, sprites=sprites, sprite_mode="none",
                      overlays=("passability",))
        self.assertEqual(pixel(result.image, 96 + 10, 6), FRAME)
        self.assertEqual(pixel(result.image, 96 + 16, 16), BACKGROUND)
        self.assertEqual(result.overlay_cells["passability"]["event_blocked"], 1)

    def test_events_that_do_not_block_are_not_framed(self):
        grid, flags = row_of_four()
        for through, priority in ((True, 1), (False, 0), (False, 2)):
            with self.subTest(through=through, priority=priority):
                sprites = [scene.Sprite(1, 3, 0, through=through,
                                        priority_type=priority)]
                result = draw(grid, flags, sprites=sprites,
                              overlays=("passability",))
                self.assertEqual(pixel(result.image, 96 + 10, 6), BACKGROUND)
                self.assertEqual(
                    result.overlay_cells["passability"]["event_blocked"], 0)

    def test_a_tile_graphic_event_joins_the_walk_ahead_of_the_layers(self):
        # The open cell (tile 13) becomes a wall once an event whose graphic
        # is tile 10 stands on it: `all_tiles` asks the event's tile first.
        grid, flags = row_of_four()
        sprites = [scene.Sprite(1, 3, 0, tile_id=10)]
        result = draw(grid, flags, sprites=sprites, sprite_mode="none",
                      overlays=("passability",))
        self.assertEqual(pixel(result.image, 96 + 16, 16), TINTED)
        self.assertEqual(result.overlay_cells["passability"]["blocked"], 2)

    def test_an_id_the_table_lacks_is_counted_not_fatal(self):
        flags = table(12, {0: STAR})
        grid = grid_of(2, 1, {(0, 0, 0): 500, (1, 0, 0): 600})
        result = draw(grid, flags, overlays=("passability",))
        self.assertEqual(result.overlay_unknown_cells, {"passability": 2})
        self.assertEqual(result.overlay_cells["passability"]["blocked"], 0)

    def test_an_empty_flags_table_does_not_crash(self):
        result = draw(grid_of(2, 2, {(0, 0, 0): 65535}), [],
                      overlays=("passability", "terrain_tags"))
        self.assertEqual(result.overlay_unknown_cells["passability"], 4)

    def test_a_map_with_one_layer_is_read_from_that_layer(self):
        flags = table(32, {10: DOWN})
        grid = grid_of(1, 1, {(0, 0, 0): 10}, depth=1)
        result = draw(grid, flags, overlays=("passability",))
        self.assertEqual(result.overlay_cells["passability"]["edge_blocked"], 1)

    def test_the_meta_layer_is_not_a_tile_layer(self):
        # Layer 3 holds shadow bits and a region id, not a tile id; its word
        # 10 (shadow quarters 2 and 8) must not be looked up as the wall
        # that tile 10 is, which would make the topmost "tile" a wall.
        flags = table(300, {0: STAR, 10: 0xF, 20: 0})
        grid = grid_of(1, 1, {(0, 0, 0): 20, (0, 0, 3): 10})
        result = draw(grid, flags, overlays=("passability",))
        self.assertEqual(result.overlay_cells["passability"]["blocked"], 0)

    def test_an_unknown_overlay_is_refused(self):
        with self.assertRaisesRegex(ValueError, "unknown overlay walls"):
            draw(grid_of(1, 1), [], overlays=("walls",))


class TestTerrainTagsAreDrawn(unittest.TestCase):
    def tagged(self, **options):
        flags = table(32, {0: STAR, 10: 3 << 12})
        grid = grid_of(2, 1, {(0, 0, 0): 10})
        return draw(grid, flags, overlays=("terrain_tags",), **options)

    def test_a_tag_is_a_digit_at_the_bottom_right(self):
        result = self.tagged()
        # The '3' glyph is 111 / 001 / 111 / 001 / 111 on a plate whose
        # origin is (25, 23): glyph cell (col, row) lands at (26 + col, 24 + row).
        image = result.image
        self.assertEqual(pixel(image, 26, 24), WHITE)
        self.assertEqual(pixel(image, 28, 25), WHITE)
        self.assertEqual(pixel(image, 26, 25), PLATE)
        self.assertEqual(pixel(image, 26, 26), WHITE)
        self.assertEqual(pixel(image, 27, 27), PLATE)
        self.assertEqual(result.overlay_cells["terrain_tags"], {"tagged": 1})

    def test_an_untagged_cell_has_no_mark(self):
        image = self.tagged().image
        self.assertEqual(pixel(image, 32 + 26, 24), BACKGROUND)

    def test_a_half_size_picture_gets_bigger_digits(self):
        # tile_size 16 halves the picture, so the digit is drawn at scale 2:
        # plate origin (20, 16), glyph (col, row) at (22 + 2 * col, 18 + 2 * row)
        # and two pixels square.
        image = self.tagged(tile_size=16).image
        self.assertEqual(pixel(image, 22, 18), WHITE)
        self.assertEqual(pixel(image, 23, 19), WHITE)
        self.assertEqual(pixel(image, 22, 20), PLATE)

    def test_below_the_floor_no_digit_is_drawn_but_the_cell_is_counted(self):
        result = self.tagged(tile_size=8)
        self.assertEqual(pixel(result.image, 26, 24), BACKGROUND)
        self.assertEqual(result.overlay_cells["terrain_tags"], {"tagged": 1})
        self.assertEqual(result.overlay_labels_skipped, {"terrain_tags": 1})

    def test_the_floor_is_sixteen(self):
        self.assertEqual(overlays.digit_scale(15), None)
        self.assertEqual(overlays.digit_scale(16), 2)
        self.assertEqual(overlays.digit_scale(31), 2)
        self.assertEqual(overlays.digit_scale(32), 1)
        self.assertEqual(overlays.digit_scale(64), 1)

    def test_tag_zero_and_unknown_ids_make_no_mark(self):
        flags = table(12, {0: STAR})
        result = draw(grid_of(1, 1, {(0, 0, 0): 999}), flags,
                      overlays=("terrain_tags",))
        self.assertEqual(result.overlay_cells["terrain_tags"], {"tagged": 0})
        self.assertEqual(result.overlay_unknown_cells, {"terrain_tags": 1})


class TestEventIdsAreDrawn(unittest.TestCase):
    def test_the_id_is_at_the_top_right_of_its_cell(self):
        # '7' is 111 / 001 / 010 / 010 / 010; plate origin (57, 2) in cell 1.
        sprites = [scene.Sprite(7, 1, 0)]
        result = draw(grid_of(2, 1), [], sprites=sprites, sprite_mode="none",
                      overlays=("event_ids",))
        self.assertEqual(pixel(result.image, 58, 3), LABEL)
        self.assertEqual(pixel(result.image, 60, 4), LABEL)
        self.assertEqual(pixel(result.image, 58, 4), PLATE)
        self.assertEqual(pixel(result.image, 59, 5), LABEL)
        self.assertEqual(result.overlay_cells["event_ids"], {"event_labels": 1})

    def test_an_event_outside_the_window_has_no_label(self):
        sprites = [scene.Sprite(7, 1, 0), scene.Sprite(8, 5, 5)]
        result = draw(grid_of(2, 1), [], sprites=sprites, sprite_mode="none",
                      overlays=("event_ids",))
        self.assertEqual(result.overlay_cells["event_ids"], {"event_labels": 1})

    def test_a_long_or_negative_id_is_skipped_not_drawn(self):
        sprites = [scene.Sprite(10 ** 40, 0, 0), scene.Sprite(-3, 1, 0)]
        result = draw(grid_of(2, 1), [], sprites=sprites, sprite_mode="none",
                      overlays=("event_ids",))
        self.assertEqual(result.overlay_labels_skipped, {"event_ids": 2})
        self.assertEqual(pixel(result.image, 26, 3), BACKGROUND)

    def test_small_pictures_skip_the_labels(self):
        result = draw(grid_of(2, 1), [], sprites=[scene.Sprite(7, 1, 0)],
                      sprite_mode="none", overlays=("event_ids",), tile_size=4)
        self.assertEqual(result.overlay_labels_skipped, {"event_ids": 1})
        self.assertEqual(pixel(result.image, 58, 3), BACKGROUND)


# ---------------------------------------------------------------------------
# The tool, against the demo project: map 1 is 20 x 15, A2 kind 16 on layer 0
# everywhere and B tile 20 along the top row of layer 1; the Elder (event 1)
# stands at (5, 4) and the cave door (event 2) at (8, 2).
# ---------------------------------------------------------------------------


def paint(path, width, height, colour_at):
    """Write a PNG whose pixel (x, y) is `colour_at(x, y)`."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels += bytes(colour_at(x, y))
    Path(path).write_bytes(png.encode(width, height, bytes(pixels), 3))


def quarter(x, y):
    return ((x // 16) * 8 % 256, (y // 16) * 6 % 256, 99)


def unwrap(case, reply):
    """The JSON half of a render; an inline picture must be the file's bytes."""
    if isinstance(reply, ImageReply):
        case.assertEqual(reply.png,
                         Path(reply.body["image"]["path"]).read_bytes())
        return reply.body
    return reply


class OverlayTest(ToolTest):
    def setUp(self):
        super().setUp()
        graphics = self.tmp / "project" / "Graphics"
        paint(graphics / "Tilesets" / "World_A2.png", 512, 576, quarter)
        paint(graphics / "Tilesets" / "World_B.png", 512, 512, quarter)
        paint(graphics / "Characters" / "People1.png", 384, 256,
              lambda x, y: (40, 200, 40))
        map_render_tools._SHEETS.clear()
        flags = self.project.get_entry("tilesets", 1).get("flags")
        # Tile 0 is a star, as the editor writes it: an empty layer decides
        # nothing. B tile 20 stays a wall (15); the A2 floor (2816) is open.
        flags.set(0, 0, 0, STAR)
        flags.set(2816, 0, 0, 0)
        self.flags = flags

    def render(self, **arguments):
        arguments.setdefault("map_id", 1)
        return unwrap(self, self.call("map_render", **arguments))

    def picture(self, reply):
        return png.decode(Path(reply["image"]["path"]).read_bytes())

    def at(self, image, x, y):
        i = (y * image.width + x) * 4
        return tuple(image.data[i:i + 3])

    def page_of(self, event_id):
        return self.project.load_map(1).get("events").get(event_id).get(
            "pages")[0]


class TestTheToolDrawsOverlays(OverlayTest):
    def test_the_wall_row_is_tinted_and_the_floor_is_not(self):
        plain = self.picture(self.render())
        marked = self.picture(self.render(overlays=["passability"]))
        self.assertNotEqual(self.at(marked, 3 * 32 + 16, 16),
                            self.at(plain, 3 * 32 + 16, 16))
        self.assertEqual(self.at(marked, 3 * 32 + 16, 5 * 32 + 16),
                         self.at(plain, 3 * 32 + 16, 5 * 32 + 16))

    def test_the_reply_counts_and_explains(self):
        reply = self.render(overlays=["passability"])
        self.assertEqual(reply["overlays"], ["passability"])
        self.assertEqual(reply["overlay_cells"],
                         {"blocked": 20, "edge_blocked": 0,
                          "event_blocked": 0})
        self.assertEqual(sorted(reply["legend"]["passability"]),
                         ["cyan_frame", "red_tint", "yellow_bar"])
        self.assertIn("first page", reply["overlays_note"])

    def test_a_solid_event_is_counted(self):
        self.page_of(1).set("priority_type", 1)
        reply = self.render(overlays=["passability"])
        self.assertEqual(reply["overlay_cells"]["event_blocked"], 1)
        marked = self.picture(reply)
        # The frame's top edge over the Elder's green: (40, 200, 40) under
        # cyan at 240/255 is (2, 224, 242) by the blend rule above.
        self.assertEqual(self.at(marked, 5 * 32 + 10, 4 * 32 + 6),
                         (2, 224, 242))

    def test_a_through_event_is_not_solid(self):
        self.page_of(1).set("priority_type", 1)
        self.page_of(1).set("through", True)
        self.assertEqual(
            self.render(overlays=["passability"])["overlay_cells"]
            ["event_blocked"], 0)

    def test_terrain_tags_are_counted_and_drawn(self):
        self.flags.set(2816, 0, 0, 2 << 12)
        plain = self.picture(self.render())
        reply = self.render(overlays=["terrain_tags"])
        self.assertEqual(reply["overlay_cells"], {"tagged": 300})
        self.assertNotEqual(self.at(self.picture(reply), 10 * 32 + 26,
                                    10 * 32 + 24),
                            self.at(plain, 10 * 32 + 26, 10 * 32 + 24))
        self.assertNotIn("overlays_note", reply)

    def test_event_ids_are_drawn_even_when_events_is_none(self):
        reply = self.render(overlays=["event_ids"], events="none")
        self.assertEqual(reply["overlay_cells"], {"event_labels": 2})
        self.assertEqual(reply["drawn"]["event_graphics"], 0)
        self.assertEqual(reply["drawn"]["event_markers"], 0)

    def test_event_ids_follow_the_window(self):
        reply = self.render(overlays=["event_ids"], x=0, y=0, width=6,
                            height=5)
        self.assertEqual(reply["overlay_cells"], {"event_labels": 1})

    def test_all_three_together(self):
        reply = self.render(overlays=["passability", "terrain_tags",
                                      "event_ids"])
        self.assertEqual(sorted(reply["legend"]),
                         ["event_ids", "passability", "terrain_tags"])
        self.assertEqual(reply["overlay_cells"]["event_labels"], 2)

    def test_no_overlay_means_no_overlay_keys(self):
        reply = self.render()
        for key in ("overlays", "legend", "overlay_cells", "overlays_note"):
            self.assertNotIn(key, reply)

    def test_an_empty_list_is_no_overlay(self):
        self.assertNotIn("overlays", self.render(overlays=[]))

    def test_a_name_given_twice_is_drawn_once(self):
        reply = self.render(overlays=["passability", "passability"])
        self.assertEqual(reply["overlays"], ["passability"])
        self.assertEqual(reply["overlay_cells"]["blocked"], 20)

    def test_a_tiny_picture_says_its_digits_were_skipped(self):
        self.flags.set(2816, 0, 0, 2 << 12)
        reply = self.render(overlays=["terrain_tags", "event_ids"],
                            tile_size=4)
        self.assertEqual(reply["overlay_cells"],
                         {"tagged": 300, "event_labels": 2})
        self.assertIn("terrain_tags 300", reply["overlays_note"])
        self.assertIn("event_ids 2", reply["overlays_note"])
        self.assertIn("tile_size 4 is below 16", reply["overlays_note"])

    def test_tile_ids_past_the_table_are_reported_and_do_not_crash(self):
        self.project.load_map(1).get("data").set(7, 7, 2, 9000)
        reply = self.render(overlays=["passability", "terrain_tags"])
        self.assertIn("passability 1", reply["overlays_note"])
        self.assertIn("no entry", reply["overlays_note"])

    def test_a_tileset_without_flags_does_not_crash(self):
        self.project.get_entry("tilesets", 1).set("flags", None)
        reply = self.render(overlays=["passability", "terrain_tags"])
        self.assertEqual(reply["overlay_cells"]["blocked"], 0)
        self.assertIn("passability 300", reply["overlays_note"])

    def test_the_picture_is_deterministic_and_named_by_its_bytes(self):
        first = self.render(overlays=["passability", "event_ids"])
        second = self.render(overlays=["passability", "event_ids"])
        self.assertEqual(first["image"]["path"], second["image"]["path"])
        data = Path(first["image"]["path"]).read_bytes()
        self.assertTrue(first["image"]["path"].endswith(
            "%s.png" % hashlib.sha1(data).hexdigest()[:10]))
        self.assertNotEqual(first["image"]["path"],
                            self.render()["image"]["path"])

    def test_the_order_of_the_names_does_not_change_the_picture(self):
        one = self.render(overlays=["passability", "event_ids"])
        two = self.render(overlays=["event_ids", "passability"])
        self.assertEqual(one["image"]["path"], two["image"]["path"])

    def test_the_window_ceiling_still_applies(self):
        with mock.patch.object(map_render_tools, "MAX_OUTPUT_PIXELS", 1000):
            with self.assertRaises(ToolError):
                self.render(overlays=["passability"])


class TestOverlayArgumentsAreHeld(OverlayTest):
    def refused(self, value, text):
        with self.assertRaises(ToolError) as caught:
            self.render(overlays=value)
        self.assertIn(text, str(caught.exception))

    def test_an_unknown_name_is_refused_and_the_choices_listed(self):
        self.refused(["walls"], "unknown overlays 'walls'")
        self.refused(["walls"], "passability, terrain_tags, event_ids")

    def test_a_name_among_good_ones_is_still_refused(self):
        self.refused(["passability", "walls"], "walls")

    def test_not_a_list_is_refused(self):
        for value in ("passability", 1, {"passability": True}, True):
            with self.subTest(value=value):
                with self.assertRaises(ToolError):
                    self.render(overlays=value)

    def test_more_than_the_ceiling_is_refused(self):
        self.refused(["passability"] * (map_render_tools.MAX_OVERLAYS + 1),
                     "at most %d" % map_render_tools.MAX_OVERLAYS)

    def test_hostile_entries_are_refused_without_a_crash(self):
        for entry in (7, None, True, 1.5, [], {}, "x" * 10_000,
                      10 ** 5000, "passability\x00"):
            with self.subTest(kind=type(entry).__name__):
                with self.assertRaises(ToolError) as caught:
                    self.render(overlays=[entry])
                self.assertLess(len(str(caught.exception)), 2000)

    def test_the_ceiling_and_floor_are_advertised(self):
        text = REGISTRY["map_render"].description
        self.assertIn("at most %d" % map_render_tools.MAX_OVERLAYS, text)
        self.assertIn("below tile_size 16", text)
        spec = REGISTRY["map_render"].schema["properties"]["overlays"]
        self.assertEqual(spec["maxItems"], map_render_tools.MAX_OVERLAYS)
        self.assertEqual(spec["items"]["enum"],
                         ["passability", "terrain_tags", "event_ids"])


@unittest.skipUnless(os.environ.get("RPGVXACE_TEST_PROJECT"),
                     "set RPGVXACE_TEST_PROJECT to a VX Ace project")
class TestRealProject(unittest.TestCase):
    """Structure only: every map takes every overlay, with plausible counts."""

    def test_every_map_takes_every_overlay(self):
        import tempfile
        from rpgvxace_mcp.project import Project
        from rpgvxace_mcp.tools.registry import Context
        with tempfile.TemporaryDirectory() as out:
            project = Project(os.environ["RPGVXACE_TEST_PROJECT"],
                              read_only=True, render_dir=out)
            ctx = Context(project)
            for map_id in project.map_ids_on_disk():
                with self.subTest(map_id=map_id):
                    reply = unwrap(self, REGISTRY["map_render"].run(
                        ctx, {"map_id": map_id, "tile_size": 16,
                              "overlays": ["passability", "terrain_tags",
                                           "event_ids"]}))
                    window = reply["window"]
                    cells = window["width"] * window["height"]
                    counts = reply["overlay_cells"]
                    self.assertLessEqual(counts["blocked"]
                                         + counts["edge_blocked"], cells)
                    self.assertLessEqual(counts["tagged"], cells)
                    self.assertGreaterEqual(counts["event_labels"], 0)
                    self.assertEqual(sorted(reply["legend"]),
                                     ["event_ids", "passability",
                                      "terrain_tags"])
                    image = png.decode(Path(reply["image"]["path"]).read_bytes())
                    self.assertEqual(image.width, reply["image"]["width"])


if __name__ == "__main__":
    unittest.main()
