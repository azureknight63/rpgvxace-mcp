# VX and XP parity for `map_render`: what is established, and what is not

`map_render` draws VX Ace maps. This note records, for VX (RGSS2) and XP
(RGSS1), which facts a renderer needs, what each is known from, and how far
that can be trusted. It was written before the feature code, and the code
follows it: a fact that is not established here is not drawn by default.

## Method and the evidence that exists

No VX or XP engine, runtime, RTP or real project is on the machine this was
written on, and the network was not used. What *was* available:

* **Repo** (`schema.py`, `README.md`, `autotile.py`, `render/`): the XP profile
  and its comments, which say they were built from XP's default scripts and
  from copies of XP editor data (a new project and a small game in the ARC
  repository). Those sources are not in the repo, so a claim made only there is
  "recorded", not re-checked.
* **The VX Ace install on this machine** (`RPGVXAce/rtp`, its `SampleMap` and
  its `dlc` packs). Its RTP is Ace-only. But two third-party tile packs in
  `dlc` are shipped "compatible with RPG Maker VX, XP and VX Ace", and one of
  them, *Futuristic Tiles*, is written up for **VX**: its read-me tells you to
  import `TileA1.png` .. `TileE.png` into VX's `Graphics/System` and then to
  set each tile's passability with VX's Passage Settings. Its nine sheets are
  real VX-format tile sheets, and were measured and looked at in this session.
  Nothing from the pack is copied into the repo; only sizes and what the
  pictures show are recorded here.
* **Memory** of the RGSS1/RGSS2 scripts and the community's documentation. This
  is the weakest basis and is labelled as such wherever it is used.

Confidence levels: **READ** (in the repo, from the source it names),
**OBSERVED** (measured on a file on this machine), **DERIVED** (follows from
READ/OBSERVED facts by a rule that can be checked), **REMEMBERED** (not
checkable here; may be wrong).

## VX (RGSS2)

### (a) Tile id to graphic and cell

| Fact | Basis | Confidence |
| --- | --- | --- |
| One global tileset, no `Tilesets` database, no `RPG::Map#tileset_id`. | README and `schema.py` profile notes (READ from the earlier work); consistent with the pack's instructions. | READ |
| The sheets are `Graphics/System/TileA1`..`TileA5`, `TileB`..`TileE`. | The pack ships exactly these nine names in a `System` folder and its read-me says to import them into VX's `Graphics/System`. | OBSERVED |
| Sheet sizes: A1 512x384, A2 512x384, A3 512x256, A4 512x480, A5 256x512, B to E 512x512. | Measured on the pack. They are *identical* to the Ace RTP's `Outside_A1`, `_A2`, `_A3`, `_A4`, `_A5`, `_B` (and `World_*`), which is what `render/tiles.py` assumes. | OBSERVED |
| Block geometry of A1 to A4 (A2 and A1 floors as 2 x 3 tile blocks, A1 animated water as 3 frames of 2 x 3, A4 alternating wall tops and wall faces, A1 waterfalls at tile column 14). | Looked at in the pack's pictures and compared with `autotile.autotile_block`, which is the Ace layout; every block in view sits where that function puts it. This is a visual comparison, not a pixel proof. | DERIVED (visual) |
| The tile id bands (B 0, C 256, D 512, E 768, A5 1536, A1 2048, A2 2816, A3 4352, A4 5888) are the same in VX. | `schema.TILE_ID_RANGES` says "VX introduced the layout, Ace kept it" and marks it advisory. The sheet sizes above make it very likely (a 256x512 A5 is 128 tiles = 1536..1663) but ids are in map data, and there is no VX map here. | REMEMBERED |

### (b) The autotile shape table

The 48-shape floor table, the 16-shape wall table and the 4-shape waterfall
table of `render/tiles.py` were derived from a real *Ace* game. The **block
layout** they cut from is shown above to be the same in VX's sheets. What
number the VX editor writes for a given neighbourhood (the *numbering*) is
stored in map data, so the pack cannot say. RGSS3 inherited its Tilemap from
RGSS2 and a VX map opened in Ace converts, which makes "the same 48" the likely
answer, but that is **REMEMBERED**. No VX map exists here to test it on.

### (c) Passability and terrain flags

Not established. VX keeps one per-tile word table for the whole game (as far as
memory serves in `System`'s `passages`), with the star, bush, counter and
terrain-tag bits Ace's flag word has. `schema.VX_CLASS_FIELDS` documents no
such field, the synthetic VX fixture has none, and the pack only shows that
VX has a Passage Settings dialog. **REMEMBERED**, and the overlays stay off.

### (d) What in the renderer is hard-coded to Ace

* `tools/map_render_tools.py`: the refusal on `tileset_layout != "ace"`; the
  tileset is read from a `Tilesets` database by `tileset_id`; the nine sheet
  names come from `tileset_names` in `Graphics/Tilesets`; flags are
  `RPG::Tileset#flags`.
* `render/tiles.py`: `plain_source`, `sheet_needed` and `compose_tile` are the
  id bands and the autotile tables; `STAR_FLAG` is Ace's bit.
* `render/scene.py`: the star-bit test for "above characters"; shadows and
  regions read layer 3; `_sprite_rect` uses the `$`/`!` rules and an 8-cell
  `character_index` sheet; `render/passability.py` is RGSS3's `Game_Map`.

## XP (RGSS1)

### (a) Tile id to graphic and cell

| Fact | Basis | Confidence |
| --- | --- | --- |
| A cell holds 0, an autotile id 48..383, or a tileset id from 384. | `schema.XP_TILE_ID_RANGES`, `XP_LAYER_NOTE`: the only values in copies of XP editor data. | READ (recorded) |
| Tileset id `n` draws cell `n - 384` of the tileset graphic, eight tiles to a row, 32 px. | `XP_LAYER_NOTE`: "Sprite_Character draws tile id - 384 from the tileset graphic, eight tiles to a row", from XP's default scripts. | READ (recorded) |
| The tileset graphic is `RPG::Tileset#tileset_name` in `Graphics/Tilesets`; the seven autotiles are `autotile_names` in `Graphics/Autotiles`, autotile `k` (1 to 7) owning ids `48k .. 48k + 47`. | `schema` field docs and `XP_TILE_ID_RANGES`. | READ (recorded) |
| An autotile graphic is 96 x 128 (3 x 4 tiles = 6 x 8 quarters of 16 px), numbered 1..48 row by row; an animated one is a multiple of 96 wide with one frame after another, frame `(count / 16) % frames`. | Not in the repo. Nothing XP-format is on this machine (the pack's sheets are VX-format). | REMEMBERED |

### (b) The autotile shape table

The repo says outright that "which of an autotile's 48 patterns an id selects
is the built-in Tilemap's and is not documented". Two things are known about
the table, and one is not.

* **The graphic is a 9-slice (REMEMBERED).** With the 96 x 128 sheet numbered
  1..48 in 16 px pieces, row by row, the tile at tile-column 1, tile-row 2 is
  the interior, the eight tiles around it are the edges and outer corners, the
  tile at tile-column 2, tile-row 0 holds the four inner-corner notches
  (pieces 5, 6, 11, 12) and the tile at 0, 0 is the whole-tile picture.
* **The numbering of the 48 matches Ace's (DERIVED, as a corroboration).** I
  wrote the XP table out from memory of XP community Tilemap rewrites (6 x 8
  entries of four piece numbers) *before* comparing it with anything, then
  compared it with `autotile.FLOOR_PATTERNS`, which was read off a real Ace
  game. Group for group they are the same order: 16 interior shapes by notch
  bit (TL, TR, BR, BL), four one-side-open groups in the order left, up,
  right, down with the same two notches each, the two strips, the four outer
  corners with and without a notch, the four three-sided caps, the island, and
  the whole tile last. Two recollections that agree entry for entry are
  evidence against a misremembered table; they are not a proof that XP's
  editor writes these numbers, because Ace may have inherited XP's convention
  and my memory may be of that convention in a rewrite, not the built-in
  Tilemap itself. So `render/xp_tiles.py` *builds* XP's table from
  `FLOOR_PATTERNS` and the 9-slice (so it cannot drift from the Ace table) and
  `tests/test_engine_parity.py` holds the build to the remembered table typed
  out entry by entry, and to a geometric check (each entry's four pieces agree
  about which sides are open).
* **Not known:** that XP's built-in Tilemap uses this numbering and this
  piece layout, since XP data and engine are absent. REMEMBERED, corroborated,
  not verified. This is why autotiles stay behind `unverified_formats`.

### (c) Passability, priority and terrain tags

* The tables are `passages`, `priorities` and `terrain_tags`, all indexed by
  tile id. The passage bits (1 down, 2 left, 4 right, 8 up, 0x40 bush, 0x80
  counter) are listed in the RGSS1 reference (`XP_PASSAGE_BITS`: READ).
* A tile with priority 0 is under characters; 1 to 5 are over them
  (`XP_LAYER_NOTE`, READ). *How* far over (a priority-`p` tile covers
  characters standing up to `p` cells above it) is not recorded; this
  renderer treats every non-zero priority as "above every character".
* The `passable?` algorithm (which layer decides, and the role of
  priority 0 as "decides: passable") and `terrain_tag`'s skip rule are only
  partly in the repo (`schema.py` records the layer order `[2, 1, 0]`). The
  rest is REMEMBERED. XP also stores no star bit and no combined word, so
  `render/passability.py`, written for Ace's one word, does **not** carry over.

### (d) What is hard-coded to Ace

As for VX, plus: XP characters are a 4 x 4 sheet of one character each (no
`character_index`, no `$` prefix; REMEMBERED), events are solid by `through`
(XP pages have no `priority_type`), and a map has three layers and no shadow or
region layer.

## What this means for the feature

| | verified | not verified |
| --- | --- | --- |
| XP | tileset tiles (ids 384 up) and the priority-0 split | autotile pieces and their numbering, character sheets, passability, terrain tags |
| VX | sheet names, sheet sizes, block geometry | id bands, shape numbering, the flag table |

So the default behaviour, decided before any code was written:

* **XP renders what is READ**: tileset tiles, each drawn above or below
  characters by its priority; tile-graphic events; every other event as a
  numbered marker. Autotile cells are *not drawn* and the reply names how many.
  `event_ids` overlay works (it needs no tile fact). `passability` and
  `terrain_tags` are refused.
* **VX is refused** by default, naming what is missing.
* `unverified_formats: true` turns on what is REMEMBERED: XP autotiles (the
  table above) and XP character sheets; VX drawing with Ace's id bands and
  tables on `Graphics/System/Tile*` sheets. Every such reply carries
  `engine_warning`.
* `map_paint` and `live` keep refusing (below).

## What was built (Phase 2)

`map_render` takes `unverified_formats` (boolean, default false; no effect on
VX Ace). The drawing differences live in `render/layouts.py` (one `Layout` value
per engine: which graphic an id is cut from, how, which tiles go over
characters, how a character frame is found, which overlays the engine's rules
allow) and `render/xp_tiles.py` (XP's id decoding, autotile table and character
frame). `scene.render_map` takes `layout=` and defaults to the Ace one, whose
functions are the ones it called before, so **VX Ace pictures are
byte-identical to those of the commit this work was built on**: the tests pin
the SHA-256 of the PNG for the demo project, plain and with every overlay, and
the hashes were taken before any of the code changed.

| Call | XP | VX |
| --- | --- | --- |
| default | Tileset tiles, above or below characters by `priority != 0`; tile-graphic events; all other events as numbered markers whose reason is given; autotile cells **not drawn**, counted by graphic in `tiles_note`; `engine_warning` says what is missing. `event_ids` overlay works. | **Refused**, naming what is established (sheet names and sizes) and what is not (tile id and shape numbering, the flag table). |
| `unverified_formats: true` | Adds autotiles (frame 0, the built table) and characters (4 x 4 sheet). The reply says so and says the picture may be wrong. | Drawn with Ace's id bands, shape tables and block layout from `Graphics/System/TileA1..TileE`; tiles above characters only if the System file has a `passages` table. Reply carries `engine_warning`. |
| `passability`, `terrain_tags` | Refused (also with the flag): the rules are not established. | Refused (also with the flag): VX's flag table is not modelled. |
| `regions` | Refused: XP has no region layer. | Refused: VX has none. |

Every XP or VX reply also carries `engine` and `unverified_formats`.
`autotiles` is a new asset kind (`Graphics/Autotiles`) so the XP autotile
graphics resolve through the same project-then-RTP lookup as the rest.

## Why `map_paint` and `live` stay refused

* **`map_paint` writes the shape number into the map.** A renderer that is wrong
  draws a wrong picture, once, and says so; a painter that is wrong stores a
  wrong tile id in the user's map for the editor to display, and the user has no
  way to tell which cells. XP's 48-pattern numbering and VX's numbering are both
  only remembered, so writing them is exactly what must not be done on a
  corroboration. VX and XP are therefore refused, and the refusal now says which
  fact is missing. The way to unlock painting is the same as for rendering: one
  real XP or VX map, from which `tests/test_map_paint.py`'s real-project check
  (the share of stored shapes a rule reproduces) can be run.
* **`live` runs a bridge written in RGSS3's Ruby 1.9.2** against VX Ace's
  window and `Input`, and was tested on a real VX Ace engine. RGSS1 and RGSS2 run
  Ruby 1.8 with different libraries and a different script set, and there is no
  VX or XP engine on this machine to write or test one against. Nothing about
  rendering evidence changes that.

## What would make the rest verifiable

* One real **XP** project (any), and its RTP: settles the autotile table and
  graphic layout (both are checkable by rendering a map with known autotiles
  and comparing it with the editor's own screenshot), the character sheet
  layout, and the `passable?` rule, after which autotiles and characters could
  leave the flag.
* One real **VX** project: its map data settles the id bands and the shape
  numbering, and its `System.rvdata` settles the flag table; then VX could
  leave the flag and `passability` could be written for it.
* Neither needs more code than is here: the flag is a switch in `layouts.py`
  and the tool's `_plan`.

## Defects and stale statements found on the way

* The README (`Scope` and the engine table) still says `map_render` refuses XP
  and VX and that XP has no render; `docs.py`'s served text said so too and was
  updated. The README lines were left for the owner (this change was told to
  touch only a tool row there).
* `test_tools.py::test_ace_only_tools_refuse_xp_and_say_why` asserted the old
  `map_render` refusal on XP; it now asserts the `engine_warning`.
* `schema.XP_TILE_ID_RANGES_NOTE` and the README call the autotile pattern
  numbering undocumented; this note records what it matches and why that is
  corroboration rather than proof.
