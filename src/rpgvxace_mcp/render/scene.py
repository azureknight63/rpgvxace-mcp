"""Drawing a window of a map: tile layers, shadows, event graphics and overlays.

This module knows nothing about projects or Marshal. It is given plain data --
the tile cells, the tileset's flag words, the decoded sheets, and a list of
event sprites -- and returns a picture, which is what lets it be tested with
sheets painted in a dozen lines. The tool in ``map_render_tools`` is the half
that reads the project and decides what to load.

The stacking follows RGSS3: the three tile layers, then the shadow layer, then
the characters, then the tiles whose flag word carries the star bit (the ones
drawn above characters), then -- as overlays of this tool's own -- regions,
the grid and event markers.

Ace's table has four layers: layers 0, 1 and 2 are all tile layers (the
third takes B-E tiles only), and layer 3 packs two things into one word --
the low four bits are the shadow quarters and the byte above them is the
region id. A real project's third layer is full of tile ids, which is how
that showed; `schema` holds the layer model, and this reads it from there
rather than keeping a second copy of the numbers.
"""

from __future__ import annotations

from array import array
from typing import Callable, Sequence

from ..schema import META_LAYER, TILE_LAYER_COUNT, region_of, shadow_of
from .canvas import (
    blend_rect,
    blit,
    draw_number,
    new_surface,
)
from .png import Image
from .tiles import (
    STAR_FLAG,
    TILE,
    compose_tile,
    draw_tile,
    sheet_needed,
)


# The shadow bits of the meta layer, in the order RGSS3 numbers the quarters.
SHADOW_QUARTERS = ((1, 0, 0), (2, 1, 0), (4, 0, 1), (8, 1, 1))
SHADOW_ALPHA = 128


# Region and marker colours: distinct enough to tell neighbours apart.
PALETTE = ((230, 60, 60), (60, 160, 230), (80, 200, 90), (240, 190, 50),
           (180, 90, 220), (50, 210, 200), (240, 130, 60), (230, 90, 170),
           (120, 120, 240), (170, 210, 60), (200, 120, 90), (90, 90, 90))


# A cache of composed tiles, per render. Distinct ids on a real map number in
# the tens to low hundreds; this is the ceiling for a crafted one.
MAX_COMPOSED_TILES = 4096


class TileGrid:
    """A map's tile table, addressed ``[x, y, z]``."""

    __slots__ = ("width", "height", "depth", "cells")

    def __init__(self, width: int, height: int, depth: int, cells: array):
        self.width = width
        self.height = height
        self.depth = depth
        self.cells = cells

    def at(self, x: int, y: int, z: int) -> int:
        return self.cells[x + y * self.width + z * self.width * self.height]


class Sprite:
    """One event as it looks on page 1."""

    __slots__ = ("event_id", "x", "y", "name", "index", "direction",
                 "pattern", "tile_id")

    def __init__(self, event_id: int, x: int, y: int, name: str = "",
                 index: int = 0, direction: int = 2, pattern: int = 0,
                 tile_id: int = 0):
        self.event_id = event_id
        self.x = x
        self.y = y
        self.name = name
        self.index = index
        self.direction = direction
        self.pattern = pattern
        self.tile_id = tile_id


class Rendered:
    """What a render produced, and what it could not draw."""

    __slots__ = ("image", "tiles_drawn", "tiles_not_drawn", "sprites_drawn",
                 "sprites_marked", "tiles_distinct")

    def __init__(self, image: Image):
        self.image = image
        self.tiles_drawn = 0
        self.tiles_distinct = 0
        # sheet name (or "unused ids") -> how many cells could not be drawn
        self.tiles_not_drawn: dict[str, int] = {}
        self.sprites_drawn = 0
        # (event id, why a marker was drawn instead of a graphic)
        self.sprites_marked: list[tuple[int, str]] = []


class _Painter:
    """The state one render shares between its passes."""

    def __init__(self, grid: TileGrid, flags: Sequence[int],
                 sheets: "dict[str, Image | None]", window: tuple[int, int, int, int],
                 result: Rendered):
        self.grid = grid
        self.flags = flags
        self.sheets = sheets
        self.wx, self.wy, self.ww, self.wh = window
        self.result = result
        self.composed: dict[int, Image | None] = {}

    def tile(self, tile_id: int) -> Image | None:
        if tile_id in self.composed:
            return self.composed[tile_id]
        image = compose_tile(tile_id, self.sheets)
        if len(self.composed) < MAX_COMPOSED_TILES:
            self.composed[tile_id] = image
        return image

    def flag(self, tile_id: int) -> int:
        return self.flags[tile_id] if tile_id < len(self.flags) else 0

    def cells(self):
        for y in range(self.wy, self.wy + self.wh):
            for x in range(self.wx, self.wx + self.ww):
                yield x, y, (x - self.wx) * TILE, (y - self.wy) * TILE

    def paint_tiles(self, canvas: Image, above: bool) -> None:
        """One pass of the tile layers: the tiles under characters, or over."""
        grid = self.grid
        for z in range(min(TILE_LAYER_COUNT, grid.depth)):
            for x, y, px, py in self.cells():
                tile_id = grid.at(x, y, z)
                if tile_id == 0 or (self.flag(tile_id) & STAR_FLAG != 0) != above:
                    continue
                image = self.tile(tile_id)
                if image is None:
                    name = sheet_needed(tile_id) or "unused ids"
                    not_drawn = self.result.tiles_not_drawn
                    not_drawn[name] = not_drawn.get(name, 0) + 1
                    continue
                draw_tile(canvas, image, px, py)
                self.result.tiles_drawn += 1

    def paint_shadows(self, canvas: Image) -> None:
        grid = self.grid
        if grid.depth <= META_LAYER:
            return
        half = TILE // 2
        for x, y, px, py in self.cells():
            bits = shadow_of(grid.at(x, y, META_LAYER))
            if not bits:
                continue
            for bit, qx, qy in SHADOW_QUARTERS:
                if bits & bit:
                    blend_rect(canvas, px + qx * half, py + qy * half,
                               half, half, (0, 0, 0), SHADOW_ALPHA)

    def paint_regions(self, canvas: Image) -> None:
        grid = self.grid
        if grid.depth <= META_LAYER:
            return
        for x, y, px, py in self.cells():
            region = region_of(grid.at(x, y, META_LAYER))
            if not region:
                continue
            blend_rect(canvas, px, py, TILE, TILE,
                       PALETTE[region % len(PALETTE)], 110)
            draw_number(canvas, px + 1, py + 1, region, 2)

    def paint_grid(self, canvas: Image) -> None:
        for column in range(self.ww + 1):
            blend_rect(canvas, column * TILE, 0, 1, self.wh * TILE,
                       (255, 255, 255), 90)
        for row in range(self.wh + 1):
            blend_rect(canvas, 0, row * TILE, self.ww * TILE, 1,
                       (255, 255, 255), 90)
        # Coordinates along the top and left edges, every fifth cell, so a
        # reader can say "the door at 12, 7" without counting squares.
        for column in range(self.ww):
            if (self.wx + column) % 5 == 0:
                draw_number(canvas, column * TILE + 2, 2, self.wx + column, 2,
                            (255, 240, 120))
        for row in range(self.wh):
            if (self.wy + row) % 5 == 0 and row:
                draw_number(canvas, 2, row * TILE + 2, self.wy + row, 2,
                            (255, 240, 120))


def _sprite_rect(sheet: Image, name: str, index: int, direction: int,
                 pattern: int) -> tuple[int, int, int, int, bool]:
    """``(sx, sy, w, h, lifted)`` for a character frame.

    ``$`` marks a sheet holding one character (3 x 4 frames) and ``!`` one
    drawn without the 4-pixel lift RGSS3 gives a character standing on its
    tile.
    """
    big = name.startswith("$") or name[:2] == "!$"
    lifted = not name.startswith("!")
    frames_x, frames_y = (3, 4) if big else (12, 8)
    width, height = sheet.width // frames_x, sheet.height // frames_y
    row = {2: 0, 4: 1, 6: 2, 8: 3}.get(direction, 0)
    pattern = min(max(pattern, 0), 2)
    if big:
        column, line = pattern, row
    else:
        column, line = index % 4 * 3 + pattern, index // 4 * 4 + row
    return column * width, line * height, width, height, lifted


def _marker(canvas: Image, sprite: Sprite, px: int, py: int) -> None:
    """A coloured box with the event's id, for events with nothing to show."""
    colour = PALETTE[sprite.event_id % len(PALETTE)]
    blend_rect(canvas, px + 3, py + 3, TILE - 6, TILE - 6, colour, 190)
    for edge in ((px + 3, py + 3, TILE - 6, 1), (px + 3, py + TILE - 4, TILE - 6, 1),
                 (px + 3, py + 3, 1, TILE - 6), (px + TILE - 4, py + 3, 1, TILE - 6)):
        blend_rect(canvas, *edge, (0, 0, 0), 255)
    draw_number(canvas, px + 4, py + 9, sprite.event_id, 2)


def render_map(grid: TileGrid, flags: Sequence[int],
               sheets: "dict[str, Image | None]",
               window: tuple[int, int, int, int], *,
               sprites: Sequence[Sprite] = (), sprite_mode: str = "graphics",
               load_character: "Callable[[str], Image | None] | None" = None,
               shadows: bool = True, regions: bool = False,
               show_grid: bool = False, background=(24, 24, 32, 255)
               ) -> Rendered:
    """A native-size (32 pixels a tile) picture of one window of a map.

    ``window`` is ``(x, y, width, height)`` in tiles and must lie inside the
    grid; the tool validates that before it asks for a canvas this big.
    ``sprite_mode`` is "graphics" (each event as its page-1 picture, with a
    marker where the picture is missing), "markers" (every event as a marker)
    or "none".
    """
    wx, wy, ww, wh = window
    canvas = new_surface(ww * TILE, wh * TILE, background)
    result = Rendered(canvas)
    painter = _Painter(grid, flags, sheets, window, result)
    painter.paint_tiles(canvas, above=False)
    if shadows:
        painter.paint_shadows(canvas)
    markers: list[Sprite] = []
    if sprite_mode != "none":
        for sprite in sorted(sprites, key=lambda s: (s.y, s.event_id)):
            if not (wx - 3 <= sprite.x < wx + ww + 3
                    and wy - 1 <= sprite.y < wy + wh + 4):
                continue
            if sprite_mode == "markers":
                markers.append(sprite)
            elif not _draw_sprite(canvas, painter, sprite, load_character,
                                  result):
                markers.append(sprite)
    painter.paint_tiles(canvas, above=True)
    if regions:
        painter.paint_regions(canvas)
    if show_grid:
        painter.paint_grid(canvas)
    for sprite in markers:
        _marker(canvas, sprite, (sprite.x - wx) * TILE, (sprite.y - wy) * TILE)
    result.tiles_distinct = len(painter.composed)
    return result


def _draw_sprite(canvas: Image, painter: _Painter, sprite: Sprite,
                 load_character: "Callable[[str], Image | None] | None",
                 result: Rendered) -> bool:
    """Draw a sprite's graphic; False means the caller owes it a marker."""
    px = (sprite.x - painter.wx) * TILE
    py = (sprite.y - painter.wy) * TILE
    if sprite.tile_id:
        image = painter.tile(sprite.tile_id)
        if image is None:
            result.sprites_marked.append(
                (sprite.event_id, "its tile graphic is not drawable"))
            return False
        draw_tile(canvas, image, px, py)
        result.sprites_drawn += 1
        return True
    if not sprite.name:
        return True      # an event with no graphic is invisible, as in the game
    sheet = load_character(sprite.name) if load_character else None
    if sheet is None:
        result.sprites_marked.append(
            (sprite.event_id, "graphic \"%s\" could not be loaded" % sprite.name))
        return False
    sx, sy, w, h, lifted = _sprite_rect(sheet, sprite.name, sprite.index,
                                        sprite.direction, sprite.pattern)
    if w <= 0 or h <= 0 or sx + w > sheet.width or sy + h > sheet.height:
        result.sprites_marked.append(
            (sprite.event_id, "graphic \"%s\" is the wrong size" % sprite.name))
        return False
    blit(canvas, sheet, sx, sy, w, h, px + TILE // 2 - w // 2,
         py + TILE - (4 if lifted else 0) - h)
    result.sprites_drawn += 1
    return True
