"""Where a VX Ace tile id lives on its sheet, and how an autotile is cut.

A tile id names one of nine sheets (``A1`` to ``A5``, then ``B`` to ``E``):

====================  ==================================================
ids                   what they are
====================  ==================================================
0 - 1023              B, C, D, E: 256 plain tiles each (0 is empty)
1536 - 1663           A5: 128 plain tiles
2048 - 8191           A1 to A4: autotiles, 48 shapes per kind
====================  ==================================================

An autotile id is ``2048 + kind * 48 + shape``. The sheet holds one block per
*kind*, and a block is cut into 16 x 16 quarters: each of a tile's four
quarters is taken from a place in the block chosen by the shape. The engine
does not recompute the shape from the neighbours -- the editor stored it in the
map -- so drawing is a table lookup, and the three tables below (floor, wall,
waterfall) are the whole of autotile behaviour.

The tables are *built* here from the rule they encode rather than typed in as
96 pairs of digits: each shape is a set of absent sides plus the inner corners
that still show, and a quarter's source follows from its two adjacent sides.
``tests/test_render_tiles.py`` holds the build to the shape numbering the
editor writes, and the real-project check compares it with the neighbours of
every autotile in a game.

What a shape number *means*, the id bands and where each kind's block sits
are facts of the map format rather than of drawing, so they live in
`..autotile`, which `map_paint` reads to write shapes; this module keeps only
the quarter tables, which say where to cut pixels.
"""

from __future__ import annotations

from ..autotile import (
    FLOOR_PATTERNS,
    TILE_ID_A1,
    TILE_ID_A5,
    TILE_ID_MAX,
    autotile_block,
    is_autotile,
)
from .canvas import blit, copy_rect, new_surface
from .png import Image


TILE = 32
HALF = TILE // 2


# A tileset's sheets, in the order its `tileset_names` lists them.
SHEET_NAMES = ("A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E")


# Which `RPG::Tileset#flags` bit lifts a tile above the characters.
STAR_FLAG = 0x10


def _floor_quarters(absent: frozenset, notches: frozenset
                    ) -> tuple[tuple[int, int], ...]:
    """The four quarter sources for one floor shape, as ``(x, y)`` in quarters.

    A floor block is 4 quarters wide and 6 tall. Rows 0 and 1 of its left
    tile hold the lone tile, and rows 0 and 1 of its right tile hold the four
    inner corners; rows 2 to 5 are a 4 x 4 grid of outer corners (the outside
    columns and rows) around an interior (the middle two).

    ``absent`` is the set of sides ("L", "U", "R", "D") that border something
    else, and ``notches`` the diagonals ("TL", "TR", "BL", "BR") that do while
    both neighbours beside them do not.
    """
    def pick(vertical: str, horizontal: str, corner: str,
             outer: tuple[int, int], vertical_edge: tuple[int, int],
             horizontal_edge: tuple[int, int], notch: tuple[int, int],
             inside: tuple[int, int]) -> tuple[int, int]:
        v, h = vertical in absent, horizontal in absent
        if v and h:
            return outer
        if v:
            return vertical_edge
        if h:
            return horizontal_edge
        return notch if corner in notches else inside

    return (
        pick("U", "L", "TL", (0, 2), (2, 2), (0, 4), (2, 0), (2, 4)),
        pick("U", "R", "TR", (3, 2), (1, 2), (3, 4), (3, 0), (1, 4)),
        pick("D", "L", "BL", (0, 5), (2, 5), (0, 3), (2, 1), (2, 3)),
        pick("D", "R", "BR", (3, 5), (1, 5), (3, 3), (3, 1), (1, 3)),
    )


# Shape 47 is the lone tile, cut whole from the block's top-left tile.
FLOOR_TABLE = tuple(
    [_floor_quarters(*pattern) for pattern in FLOOR_PATTERNS]
    + [((0, 0), (1, 0), (0, 1), (1, 1))])


def _wall_table() -> tuple:
    """A wall block is 2 x 2 tiles (4 x 4 quarters); bits are L, U, R, D open."""
    rows = []
    for shape in range(16):
        left, up, right, down = (bool(shape >> i & 1) for i in range(4))
        rows.append((
            (0 if left else 2, 0 if up else 2),
            (3 if right else 1, 0 if up else 2),
            (0 if left else 2, 3 if down else 1),
            (3 if right else 1, 3 if down else 1),
        ))
    return tuple(rows)


def _waterfall_table() -> tuple:
    """A waterfall frame is 2 x 1 tiles; bits are L and R open."""
    rows = []
    for shape in range(4):
        left, right = bool(shape & 1), bool(shape & 2)
        rows.append((
            (0 if left else 2, 0), (3 if right else 1, 0),
            (0 if left else 2, 1), (3 if right else 1, 1),
        ))
    return tuple(rows)


WALL_TABLE = _wall_table()
WATERFALL_TABLE = _waterfall_table()
TABLES = {"floor": FLOOR_TABLE, "wall": WALL_TABLE,
          "waterfall": WATERFALL_TABLE}


def plain_source(tile_id: int) -> tuple[str, int, int]:
    """``(sheet, x, y)`` in pixels for a non-autotile id."""
    if tile_id >= TILE_ID_A5:
        n = tile_id - TILE_ID_A5
        return "A5", n % 8 * TILE, n // 8 % 16 * TILE
    return ("BCDE"[tile_id // 256],
            (tile_id // 128 % 2 * 8 + tile_id % 8) * TILE,
            tile_id % 256 // 8 % 16 * TILE)


def sheet_needed(tile_id: int) -> str | None:
    """The sheet a tile id will be cut from, or None if it draws nothing."""
    if tile_id <= 0 or tile_id >= TILE_ID_MAX:
        return None
    if tile_id < 1024 or TILE_ID_A5 <= tile_id < TILE_ID_A5 + 128:
        return plain_source(tile_id)[0]
    if tile_id >= TILE_ID_A1:
        return autotile_block(tile_id)[0]
    return None


def compose_tile(tile_id: int, sheets: "dict[str, Image | None]"
                 ) -> Image | None:
    """One 32 x 32 tile, alpha intact, or None if there is nothing to draw.

    None covers the empty tile, an unused id, a sheet that is not loaded and a
    tile whose rectangle falls outside the sheet it names -- the caller counts
    which, since a map that quietly drew fewer tiles than it holds is the
    thing to avoid.
    """
    name = sheet_needed(tile_id)
    if name is None:
        return None
    sheet = sheets.get(name)
    if sheet is None:
        return None
    out = new_surface(TILE, TILE)
    if not is_autotile(tile_id):
        _, x, y = plain_source(tile_id)
        if x + TILE > sheet.width or y + TILE > sheet.height:
            return None
        copy_rect(out, sheet, x, y, TILE, TILE, 0, 0)
        return out
    _, bx, by, table, shape = autotile_block(tile_id)
    rows = TABLES[table]
    if shape >= len(rows):
        # `autotile_block` names a shape 0-47 for every kind, and a wall has
        # sixteen and a waterfall four: a hand-edited or damaged map can hold
        # an id the table has no row for. Undrawable, not a crash -- the
        # caller counts it with the other ids that drew nothing.
        return None
    quarters = rows[shape]
    for index, (qx, qy) in enumerate(quarters):
        sx = (bx * 2 + qx) * HALF
        sy = (by * 2 + qy) * HALF
        if sx + HALF > sheet.width or sy + HALF > sheet.height:
            return None
        copy_rect(out, sheet, sx, sy, HALF, HALF,
                  index % 2 * HALF, index // 2 * HALF)
    return out


def draw_tile(dst: Image, tile: Image, x: int, y: int) -> None:
    """Put a composed tile on the canvas at pixel ``(x, y)``."""
    blit(dst, tile, 0, 0, TILE, TILE, x, y)
