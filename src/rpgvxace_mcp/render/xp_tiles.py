"""Where an RPG Maker XP tile id lives on its graphics, and how an autotile is cut.

An XP map cell holds 0, an autotile id from 48 to 383, or a tileset id from 384
up. `docs/engine_parity.md` records what each fact below rests on, because they
do not rest on the same thing:

* **Recorded in this repo from XP's own scripts and data** (`schema`): a tileset
  id ``n`` draws cell ``n - 384`` of the tileset graphic, eight tiles to a row;
  autotile ``k`` (1 to 7) owns ids ``48k .. 48k + 47`` and names its graphic in
  ``RPG::Tileset#autotile_names``. `xp_sheet_needed`, `plain_cell` and the
  tileset half of `xp_compose_tile` use only these.
* **Remembered, not verified** (nothing XP-format exists on the machine this was
  written on): that an autotile graphic is 96 x 128 pixels (six 16-pixel pieces
  across, eight down, numbered 1 to 48 row by row, an animated one repeating
  the 96 pixels sideways per frame), that the graphic is a 9-slice around an
  interior tile, and that the 48 patterns are numbered the way VX Ace's 48
  floor shapes are. The table is therefore *built* here from
  `autotile.FLOOR_PATTERNS` (read off a real Ace game) and the 9-slice, so the
  two cannot drift, and `tests/test_engine_parity.py` holds the build to the
  remembered table typed out entry by entry. `xp_compose_tile` draws an
  autotile only when its caller says the remembered facts are wanted.

Pure functions over decoded images, like `tiles`.
"""

from __future__ import annotations

from ..autotile import FLOOR_PATTERNS
from ..schema import XP_AUTOTILE_PATTERNS, XP_FIRST_TILESET_ID
from .canvas import copy_rect, new_surface
from .png import Image
from .tiles import HALF, TILE


# A cell is 16 bits in a Table, so ids stop here.
XP_TILE_ID_MAX = 0x10000

# The tileset graphic is this many tiles wide, by the engine and not by the
# file: a graphic of another width is read as if it were this wide.
TILESET_COLUMNS = 8

# An autotile graphic frame is three tiles wide and four tall: 6 x 8 pieces.
FRAME_WIDTH = 3 * TILE
FRAME_HEIGHT = 4 * TILE
PIECES_ACROSS = FRAME_WIDTH // HALF

# The whole-tile picture in the graphic's top-left corner, which is pattern 47.
WHOLE_TILE = (1, 2, 7, 8)


def _piece(tile_column: int, tile_row: int, quarter_x: int, quarter_y: int) -> int:
    """The 1-based piece number of a quarter of a tile of the graphic."""
    return ((tile_row * 2 + quarter_y) * PIECES_ACROSS
            + tile_column * 2 + quarter_x + 1)


# Where the nine slices and the inner-corner tile sit, as (column, row) of the
# graphic's twelve tiles: the 3 x 3 block of rows 1-3 is the interior ringed by
# its edges and outer corners, and the tile at (2, 0) holds the four notches
# that show where a corner neighbour is missing.
_NOTCHES = (2, 0)
_FIRST_SLICE_ROW = 1


def _slice_index(low_open: bool, high_open: bool, high_quarter: bool) -> int:
    """Which of a 9-slice's three columns (or rows) a quarter is cut from.

    ``low_open`` is the left (or up) side being open, ``high_open`` the right
    (or down) one. With only one open the whole cell is that edge's tile, so
    both quarters across take it; with both or neither open the cell is cut
    from two tiles (the two edges, or the interior twice) and each quarter
    takes the one on its own side.
    """
    if low_open != high_open:
        return 0 if low_open else 2
    if low_open:
        return 2 if high_quarter else 0
    return 1


def _floor_pieces(absent: frozenset, notches: frozenset
                  ) -> tuple[int, int, int, int]:
    """The four pieces (TL, TR, BL, BR) for one pattern.

    ``absent`` is the set of sides that border something else and ``notches``
    the diagonals that do while both neighbours beside them do not, as
    `autotile.FLOOR_PATTERNS` states every shape. A cell with one side open is
    that edge's whole tile; with two adjacent open, the corner tile; a quarter
    whose two sides are both joined and whose diagonal is not shows the notch
    piece instead of its interior one.
    """
    out = []
    for corner, qx, qy in (("TL", 0, 0), ("TR", 1, 0),
                           ("BL", 0, 1), ("BR", 1, 1)):
        column = _slice_index("L" in absent, "R" in absent, bool(qx))
        row = _slice_index("U" in absent, "D" in absent, bool(qy))
        # The quarter's own two sides: joined, so nothing shows but a notch.
        own = ("U" if qy == 0 else "D", "L" if qx == 0 else "R")
        if corner in notches and not (set(own) & absent):
            out.append(_piece(*_NOTCHES, qx, qy))
        else:
            out.append(_piece(column, row + _FIRST_SLICE_ROW, qx, qy))
    return tuple(out)


# Pattern n is `autotile.FLOOR_PATTERNS[n]`, and the last is the whole tile.
XP_FLOOR_TABLE = tuple(
    [_floor_pieces(*pattern) for pattern in FLOOR_PATTERNS] + [WHOLE_TILE])


def xp_sheet_needed(tile_id: int) -> str | None:
    """The graphic a tile id is cut from, or None if it draws nothing.

    ``"tileset"`` for ids from 384, ``"autotile k"`` for 48 to 383, and None
    for 0 and for 1 to 47, which no XP data holds.
    """
    if tile_id < XP_AUTOTILE_PATTERNS or tile_id >= XP_TILE_ID_MAX:
        return None
    if tile_id >= XP_FIRST_TILESET_ID:
        return "tileset"
    return "autotile %d" % (tile_id // XP_AUTOTILE_PATTERNS)


def plain_cell(tile_id: int) -> tuple[int, int]:
    """``(x, y)`` in pixels of a tileset id's cell."""
    n = tile_id - XP_FIRST_TILESET_ID
    return n % TILESET_COLUMNS * TILE, n // TILESET_COLUMNS * TILE


def xp_compose_tile(tile_id: int, sheets: "dict[str, Image | None]",
                    autotiles: bool) -> Image | None:
    """One 32 x 32 tile, or None if there is nothing to draw.

    None covers the empty tile, an unused id, a graphic that is not loaded, a
    cell outside its graphic, an autotile graphic that is not a whole number of
    96 x 128 frames, and any autotile at all while ``autotiles`` is False --
    the caller counts which, since a picture that quietly drew fewer tiles than
    the map holds is the thing to avoid.
    """
    name = xp_sheet_needed(tile_id)
    if name is None:
        return None
    sheet = sheets.get(name)
    if sheet is None:
        return None
    out = new_surface(TILE, TILE)
    if name == "tileset":
        x, y = plain_cell(tile_id)
        if x + TILE > sheet.width or y + TILE > sheet.height:
            return None
        copy_rect(out, sheet, x, y, TILE, TILE, 0, 0)
        return out
    if not autotiles:
        return None
    if sheet.height != FRAME_HEIGHT or sheet.width % FRAME_WIDTH:
        return None
    # Frame 0 of an animated autotile: the engine steps through the frames,
    # a still picture shows the first.
    for index, piece in enumerate(XP_FLOOR_TABLE[tile_id % XP_AUTOTILE_PATTERNS]):
        px = (piece - 1) % PIECES_ACROSS * HALF
        py = (piece - 1) // PIECES_ACROSS * HALF
        copy_rect(out, sheet, px, py, HALF, HALF,
                  index % 2 * HALF, index // 2 * HALF)
    return out


def xp_sprite_rect(sheet: Image, name: str, index: int, direction: int,
                   pattern: int) -> tuple[int, int, int, int, bool]:
    """``(sx, sy, w, h, lifted)`` for a character frame.

    An XP character graphic holds one character in 4 columns (the walking
    pattern) by 4 rows (down, left, right, up). It has no ``$`` or ``!``
    prefixes and no index, and nothing lifts the sprite off its tile. Not
    verified: see the module docstring.
    """
    width, height = sheet.width // 4, sheet.height // 4
    row = {2: 0, 4: 1, 6: 2, 8: 3}.get(direction, 0)
    column = min(max(pattern, 0), 3)
    return column * width, row * height, width, height, False

