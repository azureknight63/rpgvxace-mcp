"""How much of each map's ground is used, and what shape the used part takes.

`reachability` asks whether the player can get to a map; this asks what a
map looks like once there. It reports numbers about the tile table and
nothing else -- no verdict that a map is "too empty", because a cave map
that is mostly void is a design, not a defect, and this reader cannot tell
the two apart.

What "empty" means here, exactly: a cell whose tile id on **layer 0** is 0.
Layer 0 is the ground layer on both engines (see `schema` for the layer
names). Consequences a caller should know before reading a fraction:

* An autotile or a tile from any sheet has a non-zero id and counts as
  occupied, whatever it looks like. A cell filled with a "blank" tile that
  the tileset draws as nothing is occupied here.
* A cell that is 0 on layer 0 may still carry a tile on layer 1 or 2, a
  shadow or region id on VX Ace's layer 3, or an event. Those are *not*
  counted: this measures the ground layer, so `empty_ground_fraction` is an
  upper bound on how much of the map the player sees as void.
* `blank` is the stricter notion: no tile on any of the three tile layers
  and no event at all. It exists so a folder-like map nobody built on is not
  mistaken for a finding. `nonzero_cells` counts those tiles. VX Ace's
  layer 3 is not tiles -- each word is ``region << 8 | shadow`` -- so it
  makes no map un-blank and is counted apart: `region_cells` (region id not
  0) and `shadow_cells` (any shadow bit set); one cell can be both. A VX
  project has no such layer and neither key.

The bounding box is the smallest rectangle (inclusive x0,y0,x1,y1) holding
every occupied ground cell, and `fill_of_bounding_box` is occupied ground
cells over its area -- a thin L-shaped map has a large box and a low fill.

All of it is C-speed array work plus one Python iteration per row, charged
to the caller's `WalkBudget` per row so a crafted table declaring 65535
rows across many maps stops and says so.
"""

from __future__ import annotations

import sys
from typing import Any

from ..project import Project
from ..rubytypes import RubyHash, Table
from ..schema import MAX_SHADOW, META_LAYER, TILE_LAYER_COUNT
from .walk import WalkBudget, iter_maps


_DEFINITION = (
    "empty = tile id 0 on layer 0 (the ground layer). Cells empty on layer "
    "0 may still hold an upper-layer tile, a shadow or region id on layer 3 "
    "(VX Ace), or an event; those are not counted. blank = no tile on any "
    "tile layer (0-2) and no events; nonzero_cells counts those tiles. "
    "Layer 3 holds region << 8 | shadow, not tiles: region_cells and "
    "shadow_cells count its cells with a region id or a shadow bit.")


# Each byte's shadow bits alone, for counting shadowed cells at C speed.
_SHADOW_BITS = bytes(b & MAX_SHADOW for b in range(256))


def _event_count(game_map: Any) -> "int | None":
    events = game_map.get("events")
    if isinstance(events, RubyHash):
        return len(events.items())
    return None


def _meta_cells(words: Any) -> dict[str, int]:
    """Cells of a layer-3 slice holding a region id, and a shadow bit.

    Split into each word's high byte (the region) and low byte (the shadow
    nibble) so both counts are C-speed `bytes.count`, never a Python loop
    over cells.
    """
    raw = words.tobytes()
    low, high = raw[0::2], raw[1::2]
    if sys.byteorder == "big":
        low, high = high, low
    return {"region_cells": len(high) - high.count(0),
            "shadow_cells": len(low) - low.translate(_SHADOW_BITS).count(0)}


def _ground_shape(data: Table, budget: WalkBudget) -> dict[str, Any]:
    """Empty fraction and bounding box of layer 0, charged one step per row."""
    width, height = data.xsize, data.ysize
    cells = width * height
    if cells == 0 or data.zsize == 0:
        return {"empty_ground_fraction": None, "bounding_box": None,
                "fill_of_bounding_box": None}
    ground = data.data[:cells]
    zeros = ground.count(0)
    occupied = cells - zeros
    out: dict[str, Any] = {"empty_ground_fraction": round(zeros / cells, 4)}
    if occupied == 0:
        out.update(bounding_box=None, fill_of_bounding_box=None)
        return out
    y0 = y1 = None
    x0, x1 = width, -1
    for y in range(height):
        if not budget.spend(1):
            out["bounding_box"] = None
            out["fill_of_bounding_box"] = None
            out["shape_incomplete"] = True
            return out
        row = ground[y * width:(y + 1) * width]
        if row.count(0) == width:
            continue
        raw = row.tobytes()
        # Strip zero bytes from each end: the first surviving byte sits in
        # the cell at (offset // 2), whichever of its two bytes is non-zero.
        first = (len(raw) - len(raw.lstrip(b"\0"))) // 2
        last = (len(raw.rstrip(b"\0")) - 1) // 2
        x0, x1 = min(x0, first), max(x1, last)
        y1 = y
        if y0 is None:
            y0 = y
    area = (x1 - x0 + 1) * (y1 - y0 + 1)
    out["bounding_box"] = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    out["fill_of_bounding_box"] = round(occupied / area, 4)
    return out


def map_shapes(project: Project, budget: "WalkBudget | None" = None
               ) -> dict[str, Any]:
    """Per-map ground emptiness, bounding box and blankness, for every map.

    Blankness and ``nonzero_cells`` read the tile layers only; on a profile
    with a region layer ``region_cells`` and ``shadow_cells`` count layer 3
    (``None`` when a table is too shallow to hold it).

    ``maps`` is sorted by id. A map that will not load is not listed;
    ``unreadable_maps`` counts them so a short list is not mistaken for a
    short project (`validate` says which file and why). A map whose tile
    data is not a Table reports `blank: null` -- unknown, not "not blank".
    """
    budget = WalkBudget() if budget is None else budget
    region_layer = project.profile.has_region_layer
    on_disk = project.map_ids_on_disk()
    rows: list[dict[str, Any]] = []
    for map_id, game_map in iter_maps(project, None):
        if not budget.spend(1):
            break
        data = game_map.get("data")
        events = _event_count(game_map)
        row: dict[str, Any] = {"map_id": map_id, "event_count": events}
        if not isinstance(data, Table):
            row.update(width=None, height=None, blank=None,
                       empty_ground_fraction=None, bounding_box=None,
                       fill_of_bounding_box=None,
                       note="tile data is not a Table; run validate")
        else:
            plane = data.xsize * data.ysize
            tiles = data.data[:min(data.zsize, TILE_LAYER_COUNT) * plane]
            nonzero = len(tiles) - tiles.count(0)
            row.update(width=data.xsize, height=data.ysize,
                       blank=(nonzero == 0 and events == 0),
                       nonzero_cells=nonzero)
            if region_layer:
                row.update(_meta_cells(data.data[
                    META_LAYER * plane:(META_LAYER + 1) * plane])
                    if data.zsize > META_LAYER
                    else {"region_cells": None, "shadow_cells": None})
            row.update(_ground_shape(data, budget))
        rows.append(row)
    result: dict[str, Any] = {"definition": _DEFINITION, "maps": rows}
    stopped = budget.note("dead space")
    if stopped:
        result["dead_space_stopped"] = stopped
    else:
        result["unreadable_maps"] = len(on_disk) - len(rows)
    return result
