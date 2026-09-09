"""Tile data: reading a rectangle of cells, filling one, and writing a grid.

Split from map_tools. One tool, one rectangle type, three ops -- the cleanest
of the three subjects that file held.
"""

from __future__ import annotations

from typing import Any
from ..project import MAX_MAP_ID
from ..rubytypes import Table
from .map_common import MAX_CELLS_PER_READ, _check_budget, _check_tile
from .registry import (
    ARR,
    Context,
    DRY_RUN,
    INT,
    STR,
    ToolError,
    as_int,
    check_op_args,
    need_int,
    one_of,
    opt_bool,
    opt_int,
    opt_list,
    prop,
    schema,
    tool,
    write_reply,
)

# What each map_tiles op reads. "read" writes nothing, so it takes no
# dry_run; "fill" paints one id everywhere and "write" takes a grid, and each
# used to accept the other's argument and ignore it -- a fill carrying rows
# reported "filled ..." and never looked at them.
TILE_OP_ARGS = {
    "read": {"op", "map_id", "x", "y", "width", "height", "z"},
    "fill": {"op", "map_id", "x", "y", "width", "height", "z", "tile_id",
             "dry_run"},
    "write": {"op", "map_id", "x", "y", "width", "height", "z", "rows",
              "dry_run"},
}

class _Rect:
    """The rectangle of one map_tiles call, and the table it sits in.

    Every op resolves the same five numbers from the same arguments, and each
    of those has a check that must not be skipped, so they are resolved once
    here rather than in a body that then branches three ways.
    """

    __slots__ = ("data", "map_id", "x", "y", "width", "height", "z")

    def __init__(self, ctx: Context, args: dict[str, Any], map_id: int,
                 data: Table):
        self.data = data
        self.map_id = map_id
        if data.xsize < 1 or data.ysize < 1:
            # Named here, because every bound below is derived from these two:
            # an empty table used to be reported as "the default for x must be
            # at most -1, got 0", which is about an argument never sent.
            raise ToolError(
                "map %d has a tile table of %dx%d, so it has no cells to read "
                "or write. Give it a size with map_edit op=\"resize\" first."
                % (map_id, data.xsize, data.ysize))

        # Each axis is bounded before it is used. A negative x used to slip
        # past the fit check below, because the default width was computed
        # from it, and then failed deep in the Table with an error the model
        # could not act on.
        self.x = opt_int(args, "x", 0, minimum=0, maximum=data.xsize - 1) or 0
        self.y = opt_int(args, "y", 0, minimum=0, maximum=data.ysize - 1) or 0
        self.z = opt_int(args, "z", 0, minimum=0) or 0
        if self.z >= data.zsize:
            # Named rather than bounded generically, so the message says which
            # layers this map actually has.
            raise ToolError("layer %d does not exist; this map has layers 0..%d"
                            % (self.z, data.zsize - 1))

        # Both defaults are at least 1, because x and y are capped one inside
        # the table's extents and an empty table is refused above; an explicit
        # value below 1 is refused by the bound. So there is no zero-size
        # rectangle left to test for.
        self.width = opt_int(args, "width", data.xsize - self.x, minimum=1)
        self.height = opt_int(args, "height", data.ysize - self.y, minimum=1)
        if self.x + self.width > data.xsize or self.y + self.height > data.ysize:
            raise ToolError(
                "the rectangle (%d, %d) %dx%d does not fit inside this %dx%d map"
                % (self.x, self.y, self.width, self.height,
                   data.xsize, data.ysize))

    @property
    def cells(self) -> int:
        return self.width * self.height

    def describe(self, ctx: Context) -> dict[str, Any]:
        return {"map_id": self.map_id, "x": self.x, "y": self.y,
                "width": self.width, "height": self.height, "z": self.z,
                "layer": ctx.project.profile.map_layers.get(
                    self.z, "layer %d (not used by this engine)" % self.z)}

    def read(self) -> list[list[int]]:
        return [[self.data.get(self.x + dx, self.y + dy, self.z)
                 for dx in range(self.width)]
                for dy in range(self.height)]

    def paint(self, cells: "list[int]") -> None:
        """Write one value per cell, left to right and top to bottom."""
        for index, value in enumerate(cells):
            self.data.set(self.x + index % self.width,
                          self.y + index // self.width, self.z, value)

def _cells_to_fill(ctx: Context, args: dict[str, Any],
                   rect: _Rect) -> "tuple[list[int], str]":
    """One tile id for every cell of the rectangle."""
    tile_id = opt_int(args, "tile_id")
    if tile_id is None:
        raise ToolError("fill needs a tile_id")
    _check_tile(ctx, tile_id, rect.z, "tile_id")
    _check_budget(rect.cells, "fill")
    return ([tile_id] * rect.cells,
            "filled %dx%d at (%d, %d) layer %d with tile %d"
            % (rect.width, rect.height, rect.x, rect.y, rect.z, tile_id))

def _cells_from_rows(ctx: Context, args: dict[str, Any],
                     rect: _Rect) -> "tuple[list[int], str]":
    """The caller's grid, flattened, with every value checked first.

    Every value before the first cell is written: `Table.set` masks what it
    stores, so an out-of-range id used to be written as some other tile
    entirely -- and validating inside the write loop left the rows before the
    bad one in the cached map.
    """
    rows = opt_list(args, "rows")
    if len(rows) != rect.height:
        raise ToolError("rows has %d rows but the rectangle is %d tall"
                        % (len(rows), rect.height))
    _check_budget(rect.cells, "write")
    cells: list[int] = []
    for dy, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != rect.width:
            raise ToolError(
                "row %d has %s cells but the rectangle is %d wide"
                % (dy, len(row) if isinstance(row, list) else "?", rect.width))
        for dx, value in enumerate(row):
            where = "rows[%d][%d]" % (dy, dx)
            tile_id = as_int(value, where)
            _check_tile(ctx, tile_id, rect.z, where)
            cells.append(tile_id)
    return (cells, "wrote %dx%d tiles at (%d, %d) layer %d"
            % (rect.width, rect.height, rect.x, rect.y, rect.z))

@tool(
    "map_tiles",
    "Read or write map tile data",
    "Read a rectangle of tile ids, write one back, or flood a rectangle with a "
    "single tile. Tiles are addressed x, y (from the top-left) on layer z: "
    "0 is the ground, 1 sits above it, 2 is the shadow layer, and on VX Ace 3 "
    "holds region ids. map_get reports the layers a given map actually has. "
    "Rows come back as arrays of tile ids, top row first.",
    schema({
        "map_id": prop(INT, "Which map.", minimum=1, maximum=MAX_MAP_ID),
        "op": prop(STR, "read (default), write, or fill.",
                   enum=list(TILE_OP_ARGS)),
        "x": prop(INT, "Left edge of the rectangle (default 0).", minimum=0),
        "y": prop(INT, "Top edge of the rectangle (default 0).", minimum=0),
        "width": prop(INT, "Rectangle width (default: to the map's right "
                           "edge). Width times height is at most %d cells per "
                           "call." % MAX_CELLS_PER_READ, minimum=1),
        "height": prop(INT, "Rectangle height (default: to the map's bottom edge).",
                       minimum=1),
        "z": prop(INT, "Layer: 0 ground, 1 above ground, 2 shadow mask (values "
                       "0-15), and on VX Ace 3 region ids (0-63). Default 0. "
                       "map_get reports the layers a given map has.",
                  minimum=0),
        "rows": prop(ARR, "For write: rows of tile ids, top row first. Must match "
                          "the rectangle.", items={"type": ARR, "items": {"type": INT}}),
        "tile_id": prop(INT, "For fill: the tile id to write into every cell. "
                             "Layers 0 and 1 take 0-65535, layer 2 takes 0-15, "
                             "and layer 3 takes 0-63.", minimum=0),
        "dry_run": DRY_RUN,
    }, ["map_id"]),
    writes=True,
)
def map_tiles(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Read, write or flood a rectangle of tile ids on one layer.

    Every coordinate and every value is checked before the first cell is
    written, because Table.set masks what it stores: an out-of-range id
    would otherwise be saved as a different tile.
    """
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    op = one_of(args, "op", list(TILE_OP_ARGS), "read")
    check_op_args(op, args, TILE_OP_ARGS, "map_tiles")
    game_map = ctx.project.load_map(map_id)
    data = game_map.get("data")
    if not isinstance(data, Table):
        raise ToolError("map %d has no tile table" % map_id)

    rect = _Rect(ctx, args, map_id, data)
    region = rect.describe(ctx)
    if op == "read":
        _check_budget(rect.cells, "read")
        region["rows"] = rect.read()
        return region

    cells, summary = (_cells_to_fill(ctx, args, rect) if op == "fill"
                      else _cells_from_rows(ctx, args, rect))
    dry_run = opt_bool(args, "dry_run")
    filename = ctx.project.map_file(map_id)
    with ctx.staged(filename) as write_landed:
        rect.paint(cells)
        write = write_landed.save(filename, dry_run=dry_run,
                                  tool="map_tiles", summary=summary)
    region["changed"] = summary
    region["write"] = write
    # `changed` becomes `would_change`: "filled 5x5 at (0, 0) with tile 12"
    # is a sentence about something that happened, and on a dry run it did
    # not.
    return write_reply(region, dry_run)
