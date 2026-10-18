"""Shared map vocabulary: the cell budget, and the checks two tools both need.

Registers no tool. It exists because `map_read_tools`, `map_edit_tools` and
`map_tile_tools` all need the same cell ceilings and the same refusal when a
walk is too large, and a leaf that registers tools is the wrong home for
something three of its siblings read -- which is what `map_tools` had become.
"""

from __future__ import annotations

from ..rubytypes import CELL_MASK
from ..schema import MAX_REGION_ID, MAX_SHADOW, META_LAYER, META_LAYER_BITS
from .registry import Context, ToolError

# How many cells one windowed read returns or one write covers. The whole of a
# large map is more than a caller can use and more than the response can carry.
MAX_CELLS_PER_READ = 20000

# What a whole-table walk may cover. Larger, because these are not windowed:
# a caller cannot ask for less of a tile tally or of a resize, so refusing one
# at the windowed ceiling would refuse a legitimate map -- MAX_MAP_SIDE allows
# 1000x1000, and a 100x100x4 map is already 40000 cells. This still refuses the
# extents only a hand-crafted file declares.
MAX_CELLS_PER_WALK = 4_000_000

# How a map_tiles call sees a cell. "raw" is the stored value: a tile id on
# layers 0-2, and on VX Ace's layer 3 the packed region * 256 + shadow word.
# "region" and "shadow" read and write one part of that word (see `schema`
# for why the layers are what they are). This replaced a ceiling table that
# called layer 2 a shadow mask and layer 3 a bare region id, and so refused
# every real tile id on layer 2 and every real region on layer 3.
TILE_VIEWS = ("raw", "region", "shadow")
VIEW_CEILINGS = {"region": MAX_REGION_ID, "shadow": MAX_SHADOW}


def _check_budget(cells: int, what: str, windowed: bool = True,
                  remedy: str | None = None) -> None:
    """Refuse a cell walk larger than this server will spend time on.

    Every one of these is a per-cell loop on a single-threaded server, and a
    map's declared extents come out of a file that need not be one the editor
    wrote.

    ``windowed`` says whether the caller can ask for less. A read or a write
    takes an x/y/width/height, so the ceiling is small and the remedy is to
    narrow it; a tile tally or a resize covers the whole table by definition,
    so it gets the larger ceiling.

    ``remedy`` is passed in by callers whose way out is not the default one.
    The default told every non-windowed caller to "read it in pieces with
    map_tiles", which is no help to a resize -- map_tiles cannot resize -- and
    only half an answer for a tile tally, which it cannot compute either.
    """
    ceiling = MAX_CELLS_PER_READ if windowed else MAX_CELLS_PER_WALK
    if cells <= ceiling:
        return
    if remedy is None:
        remedy = ("Ask for a smaller x/y/width/height window."
                  if windowed else
                  "This map is larger than this server will walk in one call; "
                  "read it in pieces with map_tiles instead.")
    raise ToolError("that would %s %d cells, above the %d this tool handles at "
                    "once. %s" % (what, cells, ceiling, remedy))

# What a caller can do about a map too large to resize. Not "use map_tiles":
# map_tiles reads and paints, and cannot change a map's size at all.
_RESIZE_REMEDY = (
    "Both the current and the requested size are walked, and this map is "
    "larger than this server will walk in one call. A map this size has to be "
    "resized in the RPG Maker editor.")


def _check_tile(ctx: Context, tile_id: int, z: int, what: str,
                view: str = "raw") -> None:
    """Refuse a value the layer, or the part of it being written, cannot hold.

    ``Table.set`` masks to 16 bits, so an out-of-range id was silently stored
    as a different tile and then reported back as the value the caller asked
    for. A raw word on the meta layer is also refused when it sets a bit
    outside the region and shadow fields: nothing reads those bits, so a
    value using them is a tile id sent to the wrong layer, not a cell.
    """
    ceiling = VIEW_CEILINGS.get(view, CELL_MASK)
    if not 0 <= tile_id <= ceiling:
        layer = (view if view != "raw" else
                 ctx.project.profile.map_layers.get(z, "layer %d" % z))
        raise ToolError(
            "%s is %d, which %s cannot hold: its values run 0 to %d."
            % (what, tile_id, layer, ceiling))
    if (view == "raw" and z == META_LAYER
            and ctx.project.profile.has_region_layer
            and tile_id & ~META_LAYER_BITS):
        raise ToolError(
            "%s is %d, which layer %d cannot hold: each cell there is "
            "region * 256 + shadow, a region 0 to %d above shadow bits 0 to "
            "%d, and %d sets bits outside both. To paint a region or a "
            "shadow, use view=region or view=shadow; tile ids go on layers "
            "0 to 2." % (what, tile_id, META_LAYER, MAX_REGION_ID, MAX_SHADOW,
                         tile_id))
