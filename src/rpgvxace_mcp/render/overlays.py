"""Marks drawn over a rendered map: blocked cells, terrain tags, event ids.

This module only draws and describes. What is blocked is decided by
`passability` (the engine's rules over tile ids and flags) and handed in by
`scene`, which owns the grid; nothing here reads a map. Every mark is drawn
on the 32-pixel-a-tile canvas before the picture is scaled to the caller's
``tile_size``, so a digit that is clear at 32 pixels is a smear at 8: the
digit overlays are skipped below ``MIN_DIGIT_TILE_SIZE`` and the reply says
so, rather than drawing something that cannot be read.

The marks, and the legend a reply carries for them:

* a red tint over the whole cell: no direction can be walked;
* a yellow bar on an edge: that edge cannot be crossed, in or out;
* a cyan frame: an event that is solid to the player stands here;
* a white digit at the bottom right: the cell's terrain tag, 1 to 7;
* a small number at the top right: the id of the event at the cell.
"""

from __future__ import annotations

from .canvas import blend_rect, draw_number
from .passability import DOWN, LEFT, RIGHT, UP
from .png import Image
from .tiles import TILE


PASSABILITY = "passability"
TERRAIN_TAGS = "terrain_tags"
EVENT_IDS = "event_ids"
OVERLAY_NAMES = (PASSABILITY, TERRAIN_TAGS, EVENT_IDS)


# The smallest tile_size at which a digit is drawn. The canvas is drawn at 32
# pixels a tile and resampled by nearest pixel; a digit's strokes are one or
# two canvas pixels thick, and below half size they fall between samples.
MIN_DIGIT_TILE_SIZE = 16

# A label is drawn only when its number fits inside a cell: seven digits at
# the smaller scale. Anything longer (a damaged event id) is counted and
# skipped, which also keeps `str()` off an enormous integer.
MAX_LABEL_DIGITS = 7


BLOCKED_TINT = (220, 40, 40)
BLOCKED_ALPHA = 105
EDGE_BAR = (255, 215, 0)
EDGE_BAR_ALPHA = 235
EDGE_BAR_THICKNESS = 4
EVENT_FRAME = (0, 225, 255)
EVENT_FRAME_ALPHA = 240
TAG_COLOUR = (255, 255, 255)
LABEL_COLOUR = (255, 240, 120)


def digit_scale(tile_size: int) -> int | None:
    """The font scale for digits at a picture size, or None to skip them."""
    if tile_size < MIN_DIGIT_TILE_SIZE:
        return None
    return 1 if tile_size >= TILE else 2


def draw_blocked_cell(canvas: Image, px: int, py: int) -> None:
    blend_rect(canvas, px, py, TILE, TILE, BLOCKED_TINT, BLOCKED_ALPHA)


def draw_blocked_edges(canvas: Image, px: int, py: int, blocked: int) -> None:
    """A bar on each edge whose bit is set (down is the bottom edge)."""
    t = EDGE_BAR_THICKNESS
    if blocked & UP:
        blend_rect(canvas, px, py, TILE, t, EDGE_BAR, EDGE_BAR_ALPHA)
    if blocked & DOWN:
        blend_rect(canvas, px, py + TILE - t, TILE, t, EDGE_BAR, EDGE_BAR_ALPHA)
    if blocked & LEFT:
        blend_rect(canvas, px, py, t, TILE, EDGE_BAR, EDGE_BAR_ALPHA)
    if blocked & RIGHT:
        blend_rect(canvas, px + TILE - t, py, t, TILE, EDGE_BAR, EDGE_BAR_ALPHA)


def draw_event_frame(canvas: Image, px: int, py: int) -> None:
    """A two-pixel frame, inset by six, over a cell a solid event stands on."""
    inset, width = 6, 2
    side = TILE - 2 * inset
    for x, y, w, h in ((inset, inset, side, width),
                       (inset, TILE - inset - width, side, width),
                       (inset, inset, width, side),
                       (TILE - inset - width, inset, width, side)):
        blend_rect(canvas, px + x, py + y, w, h, EVENT_FRAME, EVENT_FRAME_ALPHA)


def _fits(number: int) -> bool:
    return 0 <= number < 10 ** MAX_LABEL_DIGITS


def _plate_width(number: int, scale: int) -> int:
    return (len(str(number)) * 4 + 1) * scale


def draw_terrain_tag(canvas: Image, px: int, py: int, tag: int,
                     scale: int) -> bool:
    """The tag at the bottom right of a cell; False if it cannot be drawn."""
    if not _fits(tag):
        return False
    width = _plate_width(tag, scale)
    draw_number(canvas, px + TILE - width - 2, py + TILE - 7 * scale - 2,
                tag, scale, TAG_COLOUR)
    return True


def draw_event_label(canvas: Image, px: int, py: int, event_id: int,
                     scale: int) -> bool:
    """The event id at the top right of a cell; False if it cannot be drawn."""
    if not _fits(event_id):
        return False
    width = _plate_width(event_id, scale)
    draw_number(canvas, px + TILE - width - 2, py + 2, event_id, scale,
                LABEL_COLOUR)
    return True


def legend(names) -> dict:
    """What each mark of the chosen overlays means, for the reply."""
    out: dict = {}
    if PASSABILITY in names:
        out[PASSABILITY] = {
            "red_tint": "the player cannot enter or leave this cell in any "
                        "direction (the tileset's passage flags)",
            "yellow_bar": "the player cannot cross this edge of the cell, "
                          "in or out",
            "cyan_frame": "an event that is solid to the player stands "
                          "here: page 1 is not Through and its priority is "
                          "Same as Characters",
        }
    if TERRAIN_TAGS in names:
        out[TERRAIN_TAGS] = {
            "white_digit": "the cell's terrain tag, 1 to 7, bottom right; "
                           "cells with tag 0 carry no mark",
        }
    if EVENT_IDS in names:
        out[EVENT_IDS] = {
            "yellow_number": "the id of the event standing on the cell, "
                             "top right",
        }
    return out

