"""How varied each map's tile layers are, as Shannon entropy in bits.

A descriptive statistic, not a verdict. Entropy here answers one narrow
question -- *if you picked a non-empty cell of this layer at random, how
surprising would its tile id be?* -- and nothing else:

* **Low entropy is not a bad map.** A flat grass field, a bare dungeon
  floor or a lake is correctly one or two ids. A layer that is 95% one tile
  with a handful of props is low-entropy and exactly what a well-made
  overworld looks like.
* **High entropy is not a good map.** Noise scattered at random has the
  highest entropy there is and looks worse than a deliberate pattern.
  Entropy sees only *how many* of each id there are, never *where* they are:
  a checkerboard and a shuffled checkerboard score the same.
* **Autotiles inflate it.** One autotile kind owns 48 ids, one per edge
  shape, so a pond with a ragged border is "many tiles" to this count while
  being one terrain to a designer. Compare like with like -- the same
  layer, similar maps -- rather than reading an absolute number.
* **Layers are not one population.** Layer 0 is ground, layer 1 overlays
  autotiles on it and layer 2 holds the B-E upper tiles; each is reported
  on its own and never pooled.
* **Only tile layers have tile variety.** VX Ace's layer 3 is not tile ids:
  each word packs a region id above four shadow bits (`schema.META_LAYER`).
  Its entropy would be a number about nothing, so it is not a ``layers``
  row; a map's ``regions`` and ``shadows`` count it instead, by region id
  and by shadow mask. VX has no such layer and neither key.

Its use is as a *triage sort key*: "which maps are dull to walk through?"
is a question this can order, for a person to go and look.

**Tile id 0 is excluded, and counted.** 0 is "nothing drawn in this cell",
which on layer 1 is the ordinary state of most of the map. Counted as a
symbol it would dominate every upper layer and report them as near-zero
variety merely for being sparsely decorated, which says how much was left
unpainted, not how varied what was painted is. So entropy is taken over the
``non_empty_cells`` only, and ``cells`` and ``non_empty_cells`` are both
reported so a caller can see the layer's coverage separately. A layer with no
non-empty cell has no distribution at all: its entropy is ``None``, not 0.0
(0.0 is the honest answer for "one tile, repeated" and must stay
distinguishable from "nothing there").

**Normalisation is by the layer's own alphabet.** ``normalised`` is
entropy / log2(``distinct_tiles``): Pielou's evenness, 0.0 to 1.0, 1.0 being
every present id used equally often. It is chosen over dividing by
log2(cells) because that denominator is a property of the map's *size*, so a
large map would always look more varied than a small one for no reason but
its area. Evenness is independent of size but is undefined for a single
distinct id (log2(1) = 0), where it is ``None``. It says how evenly the ids
present are used, not how many there are -- read it beside ``distinct_tiles``.

Stdlib only; reads the Table's flat buffer the way `map_get`'s tile tally
does, one slice per layer.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

from ..project import Project
from ..rubytypes import Table
from ..schema import META_LAYER, TILE_LAYER_COUNT, region_of, shadow_of
from .walk import iter_maps


# Cells one call may count, across every map. Matches the whole-table walk
# ceiling `map_common.MAX_CELLS_PER_WALK` gives a single tally. Duplicated
# rather than imported because `tools/` is above this package; a Counter over
# an `array` slice runs a few million cells a second, so this is about a
# second of work at worst.
MAX_TILE_VARIETY_CELLS = 4_000_000


TILE_VARIETY_METHOD = (
    "Shannon entropy in bits of the tile ids on each tile layer (0-2), "
    "taken over non-empty cells only (tile id 0 means nothing is drawn there, so "
    "'cells' and 'non_empty_cells' are reported separately and empty cells "
    "are not a symbol). 'normalised' is entropy / log2(distinct_tiles), "
    "0 to 1: how evenly the ids present are used, independent of map size. "
    "entropy_bits and normalised are null for a layer with no non-empty "
    "cell, and normalised is null when only one id is present. Descriptive, "
    "not a verdict: low entropy is not a bad map, and autotile shapes count "
    "as distinct ids. VX Ace's layer 3 is not tile ids but region << 8 | "
    "shadow, so it has no row under 'layers': 'regions' counts its cells by "
    "region id and 'shadows' by shadow mask (0 is neither).")


def shannon_entropy(counts: Iterable[int]) -> "float | None":
    """Entropy in bits of a distribution given as occurrence counts.

    ``None`` when there are no occurrences at all -- an empty distribution
    has no entropy to report -- and ``0.0`` for a single symbol. Zero counts
    are ignored.
    """
    positive = [c for c in counts if c > 0]
    total = sum(positive)
    if total == 0:
        return None
    entropy = 0.0
    for count in positive:
        p = count / total
        entropy -= p * math.log2(p)
    # `-0.0` for a single symbol; report a plain zero.
    return entropy + 0.0


def layer_variety(cells: Any) -> dict[str, Any]:
    """Variety figures for one layer's cells (any sequence of tile ids)."""
    counter = Counter(cells)
    total = sum(counter.values())
    empty = counter.pop(0, 0)
    distinct = len(counter)
    entropy = shannon_entropy(counter.values())
    normalised = None
    if entropy is not None and distinct > 1:
        normalised = entropy / math.log2(distinct)
    return {
        "cells": total,
        "non_empty_cells": total - empty,
        "distinct_tiles": distinct,
        "entropy_bits": entropy,
        "normalised": normalised,
    }


def meta_layer_counts(words: Any) -> dict[str, Any]:
    """Region and shadow distributions of VX Ace layer 3's words.

    Region 0 and shadow mask 0 are "none" and not counted. At most 256
    region ids and 16 masks, so the reply is bounded whatever the map's size.
    """
    regions: Counter = Counter()
    shadows: Counter = Counter()
    for word, count in Counter(words).items():
        if region_of(word):
            regions[region_of(word)] += count
        if shadow_of(word):
            shadows[shadow_of(word)] += count
    return {
        "regions": {"cells": sum(regions.values()),
                    "by_id": [{"region": r, "cells": regions[r]}
                              for r in sorted(regions)]},
        "shadows": {"cells": sum(shadows.values()),
                    "by_mask": [{"mask": m, "cells": shadows[m]}
                                for m in sorted(shadows)]},
    }


def map_tile_variety(project: Project, max_cells: int = MAX_TILE_VARIETY_CELLS
                     ) -> dict[str, Any]:
    """Per-layer tile entropy for every map on disk that loads.

    Tile layers only (``z < TILE_LAYER_COUNT``). On a profile with a region
    layer, layer 3 is counted by `meta_layer_counts` under ``regions`` and
    ``shadows`` instead -- ``None`` each when a table is too shallow to hold
    it. Any deeper layer of a crafted table is read by nothing and skipped.

    ``max_cells`` bounds the total cells counted across the call. A map that
    would take the call past it is listed under ``skipped`` with the reason
    and the walk carries on, so a later, smaller map is not lost to an
    earlier large one; ``tile_variety_stopped`` says the answer is partial.
    A map whose tile data is not a Table is skipped with its own reason.
    Maps that will not load at all are left to `validate`, as every walk
    here does.
    """
    maps: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    remaining = max_cells
    over_budget = False
    layer_names = project.profile.map_layers
    region_layer = project.profile.has_region_layer
    for map_id, game_map in iter_maps(project):
        data = game_map.get("data")
        if not isinstance(data, Table):
            skipped.append({"map_id": map_id,
                            "reason": "tile data is %s, not a Table"
                                      % type(data).__name__})
            continue
        size = len(data.data)
        if size > remaining:
            over_budget = True
            skipped.append({
                "map_id": map_id,
                "reason": "%d cells would pass the %d-cell budget for one "
                          "call (%d left)" % (size, max_cells, remaining)})
            continue
        remaining -= size
        plane = data.xsize * data.ysize
        layers = []
        for z in range(min(data.zsize, TILE_LAYER_COUNT)):
            row = layer_variety(data.data[z * plane:(z + 1) * plane])
            row["layer"] = z
            row["label"] = layer_names.get(z, "layer %d" % z)
            layers.append(row)
        entry: dict[str, Any] = {"map_id": map_id, "width": data.xsize,
                                 "height": data.ysize, "layers": layers}
        if region_layer:
            if data.zsize > META_LAYER:
                entry.update(meta_layer_counts(
                    data.data[META_LAYER * plane:(META_LAYER + 1) * plane]))
            else:
                entry.update(regions=None, shadows=None)
        maps.append(entry)
    result: dict[str, Any] = {"method": TILE_VARIETY_METHOD, "maps": maps,
                              "skipped": skipped}
    if over_budget:
        result["tile_variety_stopped"] = (
            "stopped counting at %d cells in total; the maps under "
            "'skipped' were not measured, so this is not every map."
            % max_cells)
    return result
