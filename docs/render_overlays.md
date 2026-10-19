# `map_render` overlays: passability, terrain tags, event ids

`map_render` takes an `overlays` argument: any of `passability`,
`terrain_tags`, `event_ids` (at most 3, each once; a repeated name is drawn
once and an unknown one is refused with the list of choices). They mark the
picture for reading rather than looking. Everything is drawn inside the
window and pixel ceilings the render already has, and the PNG stays
deterministic: the same call gives the same bytes and so the same file name.

## What the marks mean

The reply carries a `legend` for the overlays asked for, and `overlay_cells`
counts what was marked in the window.

| Overlay | Mark | Meaning | Count key |
|---|---|---|---|
| `passability` | red tint over the cell | the player cannot enter or leave it in any direction | `blocked` |
| `passability` | yellow bar on an edge | that edge cannot be crossed, in or out | `edge_blocked` (cells with at least one bar) |
| `passability` | cyan frame | a solid event stands here (page 1 is not Through and its priority is Same as Characters) | `event_blocked` |
| `terrain_tags` | white digit, bottom right | the cell's terrain tag, 1 to 7 (tag 0 is unmarked) | `tagged` |
| `event_ids` | yellow number, top right | the id of the event on the cell | `event_labels` |

`overlays_note` says what could not be drawn or only approximately:

* digits are not drawn below `tile_size` 16 (the canvas is drawn at 32 pixels
  a tile and resampled by nearest pixel, so a one- or two-pixel stroke at 4
  pixels a tile is noise). The cells and events are still counted, and the
  note says how many labels were left off. Between 16 and 31 the digits are
  drawn at twice the size so they survive halving;
* a label whose number cannot fit a cell (more than 7 digits, or negative:
  only a damaged file has one) is counted and skipped;
* cells holding a tile id the tileset's flags table has no entry for are
  counted by overlay (see below).

Event ids and passability read the map's events even when `events` is
`none`; `events` still decides only whether graphics or markers are drawn.

## The passability rules

Taken from RGSS3's `Game_Map#check_passage`, for the player:

```
all_tiles(x, y) = ids of events whose graphic is a tile, then layers z = 2, 1, 0
for each direction bit (down 0x1, left 0x2, right 0x4, up 0x8):
  for each tile in all_tiles:
    flag = tileset.flags[tile]
    skip it if flag & 0x10                 # star: no effect on passage
    passable if flag & bit == 0, else impassable      # first tile that decides
  if no tile decided: impassable
```

* The walk is made once per bit, so the tile that decides "down" is not
  necessarily the one that decides "left".
* The flags are indexed by the tile id itself. B-E ids, A5 ids and A1-A4
  autotile ids (`2048 + kind * 48 + shape`) all use `flags[id]`. The engine
  has no passability special case for the water of A1: whether a water tile
  can be walked is only what the editor wrote in its flag word (boats and
  ships read other bits, 0x200 and 0x400, which the player never does).
* Tile 0 is looked up like any other. The editor writes the star bit on it,
  which is why an empty layer decides nothing; a tileset whose tile 0 has no
  star bit makes every cell it tops passable, in the engine too.
* An edge bar means the same thing from either side: entering a cell through
  an edge asks for the same bit as leaving through it.
* `Table` keeps 16-bit words unsigned and RGSS reads them signed; the rules
  convert, which matters only for terrain tags (a word with bit 15 set is
  negative, so `>> 12` is not above zero and the layer below decides).
* The terrain tag is `flags[id] >> 12` of the topmost tile (z = 2, 1, 0)
  with a tag above zero. Unlike passability it does not skip star tiles, and
  tile-graphic events are not part of it.

### Where it is an approximation

* Events are judged by **page 1**, as everywhere in this renderer; page
  conditions are not evaluated. A solid event is one whose page 1 `through`
  is not true and whose `priority_type` is 1. A tile-graphic event (page 1
  graphic is a tile) joins the walk ahead of the layers, in id order.
* A looping map, vehicle passage (boat, ship, airship) and the cells just
  outside the window are not shown.
* A tile id with no entry in the flags table is nil in the engine (a crash).
  Here it counts as passable and untagged, and `overlays_note` reports how
  many cells read such an id, per overlay, with the table's size. A tileset
  with no flags table at all reports every cell.

## Where the code is

* `render/passability.py`: the pure rules (`passage`, `terrain_tag`,
  `event_blocks`) over tile ids and a flags sequence. No pixels.
* `render/overlays.py`: the marks, their colours, the digit floor and the
  legend text. No map access.
* `render/scene.py`: `render_map(..., overlays=(), tile_size=32)` places them;
  `Sprite` gained `through` and `priority_type`.
* `tools/map_render_tools.py`: reads the `overlays` argument (bounded, de-duplicated)
  and reports `legend`, `overlay_cells`, `overlays_note`.
* `tests/test_render_overlays.py`: the rules against hand-computed engine
  results, the marks in exact pixels, the tool end to end, and an opt-in
  real-project class (`RPGVXACE_TEST_PROJECT`) that checks only structure.
