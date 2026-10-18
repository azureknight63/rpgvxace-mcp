"""VX Ace autotiles as the map stores them: kinds, shape numbers, neighbours.

An autotile id is ``2048 + kind * 48 + shape``. The *kind* names one block of
an A1-A4 sheet; the *shape* says which of that block's edge and corner pieces
the cell shows, and the editor chose it from the cell's neighbours when the
tile was painted. The engine never recomputes it. So there are two questions,
and two halves of the codebase asking them:

* drawing: given a shape, which quarters of the block to cut -- that is
  `render.tiles`, and it is only about pixels;
* writing: given the neighbours, which shape to store -- that is here, and it
  is only about integers.

Both need the same facts underneath -- the id bands, which kind is which
sheet and block, and what each of the 48 floor shapes *means* -- so those
facts live here, below both. They were in `render.tiles`, where a tool that
writes tile ids would have had to import the PNG codec and the pixel
operations to learn an integer, and where the renderer's private table was
the only statement of the editor's shape numbering.

The neighbour rules below were read off a real game (the author's game, 39 maps,
made in the editor): see `tests/test_map_paint.py`'s real-project class,
which holds each rule to the share of stored shapes it reproduces.
"""

from __future__ import annotations

from typing import Callable, Iterable

from .schema import with_shadow


# Where each tile-id band starts. These are RGSS3's own `TILE_ID_*` constants.
TILE_ID_B = 0
TILE_ID_C = 256
TILE_ID_D = 512
TILE_ID_E = 768
TILE_ID_A5 = 1536
TILE_ID_A1 = 2048
TILE_ID_A2 = 2816
TILE_ID_A3 = 4352
TILE_ID_A4 = 5888
TILE_ID_MAX = 8192


SHAPES_PER_KIND = 48


# The kinds each autotile sheet holds, as [first, last + 1). A1 has 16, A2
# and A3 32 each, A4 48: the bands above divided by 48.
KIND_RANGES = {
    "A1": (0, (TILE_ID_A2 - TILE_ID_A1) // SHAPES_PER_KIND),
    "A2": ((TILE_ID_A2 - TILE_ID_A1) // SHAPES_PER_KIND,
           (TILE_ID_A3 - TILE_ID_A1) // SHAPES_PER_KIND),
    "A3": ((TILE_ID_A3 - TILE_ID_A1) // SHAPES_PER_KIND,
           (TILE_ID_A4 - TILE_ID_A1) // SHAPES_PER_KIND),
    "A4": ((TILE_ID_A4 - TILE_ID_A1) // SHAPES_PER_KIND,
           (TILE_ID_MAX - TILE_ID_A1) // SHAPES_PER_KIND),
}


# The four sides, and the bit each sets in a wall or waterfall shape when it
# is open. A floor shape is not a bit set (see `FLOOR_PATTERNS`).
SIDES = (("L", -1, 0), ("U", 0, -1), ("R", 1, 0), ("D", 0, 1))
WALL_BITS = {"L": 1, "U": 2, "R": 4, "D": 8}
WATERFALL_BITS = {"L": 1, "R": 2}


# Each inner corner, the offset to its diagonal neighbour, and the two sides
# that must both be joined for the corner to show at all.
CORNERS = (("TL", -1, -1, "LU"), ("TR", 1, -1, "RU"),
           ("BL", -1, 1, "LD"), ("BR", 1, 1, "RD"))


def _floor_patterns() -> list[tuple[frozenset, frozenset]]:
    """What each of the 48 floor shapes means, in the editor's numbering.

    Shapes 0 to 15 have all four sides joined; the 4 bits are the inner
    corners (TL, TR, BR, BL). 16 to 31 have one side open, in the order left,
    up, right, down, and the two corners that still show are numbered
    clockwise. 32 and 33 are a horizontal and a vertical strip. 34 to 41 are
    the four open corners, each with and without the one corner still
    showing. 42 to 45 have a single side joined, 46 none, and 47 is the lone
    tile drawn whole.

    ``absent`` is the set of sides ("L", "U", "R", "D") that border something
    else, and ``notches`` the diagonals ("TL", "TR", "BL", "BR") that do while
    both neighbours beside them do not.
    """
    corners = ("TL", "TR", "BR", "BL")
    patterns: list[tuple[frozenset, frozenset]] = []
    for bits in range(16):
        patterns.append((frozenset(),
                         frozenset(c for i, c in enumerate(corners)
                                   if bits >> i & 1)))
    # (open side, the diagonals that remain, clockwise)
    for side, shown in (("L", ("TR", "BR")), ("U", ("BR", "BL")),
                        ("R", ("BL", "TL")), ("D", ("TL", "TR"))):
        for bits in range(4):
            patterns.append((frozenset(side),
                             frozenset(c for i, c in enumerate(shown)
                                       if bits >> i & 1)))
    patterns.append((frozenset("LR"), frozenset()))
    patterns.append((frozenset("UD"), frozenset()))
    for sides, shown in (("LU", "BR"), ("UR", "BL"), ("RD", "TL"),
                         ("DL", "TR")):
        patterns.append((frozenset(sides), frozenset()))
        patterns.append((frozenset(sides), frozenset([shown])))
    for sides in ("LUR", "LUD", "LRD", "URD"):
        patterns.append((frozenset(sides), frozenset()))
    patterns.append((frozenset("LURD"), frozenset()))
    return patterns


# Shapes 0 to 46, by meaning. Shape 47, the lone tile drawn whole, has no
# pattern: the editor never derives it from neighbours, so nothing here
# writes it, and `render.tiles` appends its quarters by hand.
FLOOR_PATTERNS = _floor_patterns()


# The inverse, which is all a writer needs: a pattern's shape number.
FLOOR_SHAPE_OF = {pattern: shape for shape, pattern in enumerate(FLOOR_PATTERNS)}


def is_autotile(tile_id: int) -> bool:
    return TILE_ID_A1 <= tile_id < TILE_ID_MAX


def kind_of(tile_id: int) -> int | None:
    """The autotile kind an id belongs to, or None for any other id."""
    if not is_autotile(tile_id):
        return None
    return (tile_id - TILE_ID_A1) // SHAPES_PER_KIND


def first_id(kind: int) -> int:
    """The id of a kind's shape 0."""
    return TILE_ID_A1 + kind * SHAPES_PER_KIND


def autotile_block(tile_id: int) -> tuple[str, int, int, str, int]:
    """``(sheet, bx, by, table, shape)`` for an autotile id.

    ``bx`` and ``by`` are the block's top-left corner in whole tiles. The
    water of A1 animates in the engine (three frames); frame 0 is drawn.
    ``table`` is which shape set the kind uses: "floor" (48 shapes from the
    eight neighbours), "wall" (16, from the four sides) or "waterfall" (4,
    from left and right).
    """
    kind, shape = divmod(tile_id - TILE_ID_A1, SHAPES_PER_KIND)
    tx, ty = kind % 8, kind // 8
    if tile_id < TILE_ID_A2:
        if kind == 0:
            return "A1", 0, 0, "floor", shape
        if kind == 1:
            return "A1", 0, 3, "floor", shape
        if kind == 2:
            return "A1", 6, 0, "floor", shape
        if kind == 3:
            return "A1", 6, 3, "floor", shape
        bx = tx // 4 * 8
        by = ty * 6 + tx // 2 % 2 * 3
        if kind % 2 == 0:
            return "A1", bx, by, "floor", shape
        return "A1", bx + 6, by, "waterfall", shape
    if tile_id < TILE_ID_A3:
        return "A2", tx * 2, (ty - 2) * 3, "floor", shape
    if tile_id < TILE_ID_A4:
        return "A3", tx * 2, (ty - 6) * 2, "wall", shape
    wall = ty % 2 == 1
    by = int((ty - 10) * 2.5 + (0.5 if wall else 0))
    return "A4", tx * 2, by, "wall" if wall else "floor", shape


def table_of(kind: int) -> str:
    """Which shape set a kind uses: "floor", "wall" or "waterfall"."""
    return autotile_block(first_id(kind))[3]


def sheet_of(kind: int) -> str:
    """Which autotile sheet a kind is cut from."""
    return autotile_block(first_id(kind))[0]


def is_high(tile_id: int) -> bool:
    """Whether a ground tile is part of a wall: casts the editor's shadow.

    An A3 roof or wall, an A4 wall face, or an A4 wall top. These are the
    tiles the editor shades the cell to the right of (see `auto_shadow`).
    """
    kind = kind_of(tile_id)
    if kind is None:
        return False
    return KIND_RANGES["A3"][0] <= kind


class Layer:
    """One tile layer of a map's flat Table buffer, addressed by (x, y).

    The buffer is the Table's own ``array``, so writes go straight into the
    cached map; everything a caller stages is undone by `Context.staged` if
    the save does not land.
    """

    __slots__ = ("buffer", "width", "height", "base")

    def __init__(self, buffer, width: int, height: int, z: int):
        self.buffer = buffer
        self.width = width
        self.height = height
        self.base = z * width * height

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def get(self, x: int, y: int) -> int:
        return self.buffer[self.base + y * self.width + x]

    def set(self, x: int, y: int, value: int) -> None:
        self.buffer[self.base + y * self.width + x] = value

    def kind(self, x: int, y: int) -> int | None:
        return kind_of(self.get(x, y))


def floor_shape(joined: Callable[[int, int], bool]) -> int:
    """The floor shape for a cell, given which neighbours join it.

    ``joined(dx, dy)`` answers for each of the eight neighbours. A side is
    open where its neighbour does not join; an inner corner shows where both
    sides beside it join and the diagonal between them does not.
    """
    absent = frozenset(name for name, dx, dy in SIDES if not joined(dx, dy))
    notches = frozenset(
        name for name, dx, dy, beside in CORNERS
        if not (set(beside) & absent) and not joined(dx, dy))
    return FLOOR_SHAPE_OF[(absent, notches)]


def _run(layer: Layer, x: int, y: int, kind: int,
         runs: "dict[tuple[int, int], tuple[int, int]]") -> tuple[int, int]:
    """The rows a vertical run of one wall kind covers, through (x, y).

    Memoised per call in ``runs`` for every cell of the run, so re-shaping a
    tall wall walks each column once rather than once per cell.
    """
    cached = runs.get((x, y))
    if cached is not None:
        return cached
    top = y
    while top > 0 and layer.kind(x, top - 1) == kind:
        top -= 1
    bottom = y
    while bottom < layer.height - 1 and layer.kind(x, bottom + 1) == kind:
        bottom += 1
    for row in range(top, bottom + 1):
        runs[(x, row)] = (top, bottom)
    return top, bottom


def wall_shape(layer: Layer, x: int, y: int, kind: int,
               runs: "dict[tuple[int, int], tuple[int, int]] | None" = None
               ) -> int:
    """The wall shape for a cell of ``kind``: one bit per open side.

    Up and down are open wherever the neighbour is not the same kind, the
    map's edge included -- a wall face is drawn with its top and bottom
    border at the map edge. Left and right join at the map's edge, and join a
    neighbour of the same kind only when that neighbour's run covers the
    same rows: a wall stepping up or down beside another shows its edge.

    On A4 a different A4 tile beside a wall face joins it as well: the face
    runs on behind the ceiling of the column next to it, which is what the
    editor stores in the inner corners of a room: of the A4 faces in the
    real game with their own wall top beside them, 275 of 278 join it.
    """
    runs = {} if runs is None else runs
    bits = 0
    for name, dx, dy in SIDES:
        nx, ny = x + dx, y + dy
        if not layer.inside(nx, ny):
            if dy:
                bits |= WALL_BITS[name]
            continue
        other = layer.kind(nx, ny)
        if dy:
            if other != kind:
                bits |= WALL_BITS[name]
            continue
        if other == kind:
            if _run(layer, nx, ny, kind, runs) != _run(layer, x, y, kind,
                                                       runs):
                bits |= WALL_BITS[name]
        elif not (other is not None and sheet_of(kind) == "A4"
                  and sheet_of(other) == "A4"):
            bits |= WALL_BITS[name]
    return bits


def shape_at(layer: Layer, x: int, y: int,
             runs: "dict[tuple[int, int], tuple[int, int]] | None" = None
             ) -> int | None:
    """The shape the cell at (x, y) should store, or None if not an autotile.

    A floor or waterfall joins a neighbour of the same kind, and joins at
    the map's edge. A wall is `wall_shape`.
    """
    kind = layer.kind(x, y)
    if kind is None:
        return None
    table = table_of(kind)
    if table == "wall":
        return wall_shape(layer, x, y, kind, runs)

    def joined(dx: int, dy: int) -> bool:
        nx, ny = x + dx, y + dy
        return not layer.inside(nx, ny) or layer.kind(nx, ny) == kind

    if table == "waterfall":
        return sum(WATERFALL_BITS[name] for name, dx, dy in SIDES
                   if name in WATERFALL_BITS and not joined(dx, dy))
    return floor_shape(joined)


def reshape(layer: Layer, cells: Iterable[tuple[int, int]]) -> int:
    """Give every autotile among ``cells`` the shape its neighbours call for.

    Returns how many cells changed. Plain tiles and empty cells are left
    alone, so a caller can pass a whole neighbourhood without sorting it.
    """
    runs: dict[tuple[int, int], tuple[int, int]] = {}
    changed = 0
    for x, y in cells:
        shape = shape_at(layer, x, y, runs)
        if shape is None:
            continue
        value = first_id(layer.kind(x, y)) + shape
        if layer.get(x, y) != value:
            layer.set(x, y, value)
            changed += 1
    return changed


def neighbourhood(layer: Layer, cells: Iterable[tuple[int, int]]
                  ) -> list[tuple[int, int]]:
    """Every cell whose shape can depend on one of ``cells``, sorted.

    The cells themselves and their eight neighbours; and, for a wall, the
    whole of each vertical run beside it, because a wall's left and right
    join depends on whether the run next to it spans the same rows.
    """
    out: set[tuple[int, int]] = set()
    for x, y in cells:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if layer.inside(x + dx, y + dy):
                    out.add((x + dx, y + dy))
    runs: dict[tuple[int, int], tuple[int, int]] = {}
    for x, y in list(out):
        kind = layer.kind(x, y)
        if kind is None or table_of(kind) != "wall":
            continue
        top, bottom = _run(layer, x, y, kind, runs)
        for column in (x - 1, x, x + 1):
            if 0 <= column < layer.width:
                out.update((column, row) for row in range(top, bottom + 1))
    return sorted(out, key=lambda cell: (cell[1], cell[0]))


# The shadow the editor puts on a cell with a wall to its left: the left two
# quarters, top-left (1) and bottom-left (4).
WALL_SHADOW = 1 | 4


def auto_shadow(ground: Layer, meta: Layer,
                cells: Iterable[tuple[int, int]]) -> int:
    """Set each cell's shadow bits the way the editor does, keeping regions.

    A cell that is not itself part of a wall gets the left-half shadow when
    the cells to its left and up-left both are (`is_high`); every other cell
    gets none. Read off the real game: 474 of the 515 cells with both
    neighbours high carry it, and 2 of the other 19,751 do.

    Returns how many cells changed.
    """
    changed = 0
    for x, y in cells:
        high = (not is_high(ground.get(x, y))
                and x > 0 and y > 0
                and is_high(ground.get(x - 1, y))
                and is_high(ground.get(x - 1, y - 1)))
        word = meta.get(x, y)
        value = with_shadow(word, WALL_SHADOW if high else 0)
        if value != word:
            meta.set(x, y, value)
            changed += 1
    return changed

