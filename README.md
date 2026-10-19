# rpgvxace-mcp

<!-- mcp-name: io.github.azureknight63/rpgvxace-mcp -->

An MCP server that lets an AI assistant work on an **RPG Maker VX Ace** project:
read and edit the database, maps, events, tile layers, tilesets and RGSS3
scripts, then check the result for broken references. **RPG Maker VX** and
**RPG Maker XP** projects are supported too — the server detects which engine
made the project and adapts.

It reads and writes `Data/*.rvdata2` directly. **No Ruby installation is
required**, and there are no Python dependencies — only the standard library.

## Why this exists

RPG Maker VX Ace (RGSS3) and VX (RGSS2) store an entire project as Ruby
`Marshal.dump` output. The MCP servers that already cover RPG Maker mostly aim
at MV/MZ, whose data is JSON; the ones that reach the VX family convert
`.rvdata2` by shelling out to a Ruby interpreter, so they need Ruby on `PATH`
before they can open a file at all.

This server implements Marshal format 4.8 in Python instead
([`marshal_rb.py`](src/rpgvxace_mcp/marshal_rb.py)), which removes that dependency
and makes lossless round-tripping a property of the codec: anything the server
does not understand — unknown classes, `_dump` payloads, structs — is carried
through byte for byte, so re-saving a file the server merely read leaves it
identical.

**Validated against a real project.** All 54 data files of a 39-map, 300-skill
VX Ace game (built 2013–2016 in the actual editor) load and re-dump
byte-for-byte identically, and every map, event and command list reads.
What the server *creates* is held to the same standard: a new event command,
page, event, move route, map, map-tree entry or audio object is written with
its instance variables in the order the VX Ace editor writes them (read off
every instance in that game and the editor's RuralFarm sample, which agree),
so it dumps to the editor's bytes. A blank database entry takes the order of
an existing entry in its table. VX and XP have no fixed order to match:
their editors write through Ruby 1.8, whose instance-variable table is a hash
(a copy of XP's new-project data holds its eight actors in three orders), so
there the same order is used, and loads as any order does.

## Install

Python 3.10 or newer (developed and tested on CPython 3.11).

> **Not yet on PyPI.** The `uvx` / `pip install rpgvxace-mcp` / `pipx` forms
> below work once the package is published; until then install from the
> repository (first option).

```bash
# From the repository (works today)
git clone https://github.com/azureknight63/rpgvxace-mcp && cd rpgvxace-mcp && pip install .
# Or straight from git, without cloning
pip install git+https://github.com/azureknight63/rpgvxace-mcp
uvx --from git+https://github.com/azureknight63/rpgvxace-mcp rpgvxace-mcp "C:/Games/MyAceProject"

# From PyPI (once published)
pip install rpgvxace-mcp
pipx install rpgvxace-mcp
uvx rpgvxace-mcp "C:/Games/MyAceProject"      # no install step; uv fetches and runs it
```

For development use `pip install -e .`, or skip installing and run it from the
source tree with `PYTHONPATH=src python -m rpgvxace_mcp`.

## Configure your MCP client

Claude Code (installed copy):

```bash
claude mcp add rpgvxace -- rpgvxace-mcp "C:/Games/MyAceProject"
```

Claude Code with `uvx`, nothing installed:

```bash
claude mcp add rpgvxace -- uvx rpgvxace-mcp "C:/Games/MyAceProject"
```

As a Claude Code plugin (the repository root is the plugin; it asks for the
project folder when enabled and runs the server through `uvx`):

```bash
claude --plugin-dir path/to/rpgvxace-mcp
```

Claude Desktop (`claude_desktop_config.json`), Cursor, or any other client that
launches MCP servers over stdio:

```json
{
  "mcpServers": {
    "rpgvxace": {
      "command": "rpgvxace-mcp",
      "args": ["C:/Games/MyAceProject"]
    }
  }
}
```

Or let `uvx` fetch it (once published to PyPI): use `"command": "uvx"` with
`"args": ["rpgvxace-mcp", "C:/Games/MyAceProject"]`.

Without installing, point the client at the module instead:

```json
{
  "mcpServers": {
    "rpgvxace": {
      "command": "python",
      "args": ["-m", "rpgvxace_mcp", "C:/Games/MyAceProject"],
      "env": { "PYTHONPATH": "C:/path/to/rpgvxace-mcp/src" }
    }
  }
}
```

Point it at the folder holding `Game.rvproj2` (or `Game.rvproj` for VX).

### Options

| Flag | Meaning |
| --- | --- |
| `--read-only` | Refuse every write. Good for exploring a project you don't want touched. |
| `--rtp PATH` | Where this project's RTP lives. Normally found automatically; see [RTP](#the-rtp). Repeatable. |
| `--render-dir PATH` | Where `map_render` writes its PNGs (default `.rpgvxace-mcp/renders` in the project). A render is a cache file, not a project edit, so it is written under `--read-only` too; point this elsewhere to leave the project folder untouched. |
| `--encoding cp932` | Force a text code page. The default auto-detects utf-8, cp932 (Japanese) or cp1252. |
| `--backup-keep N` | How many backup snapshots to keep (default 10). |
| `--allow-other-engines` | Open a Marshal project of an engine the server does not model, described with the closest model. VX Ace, VX and XP need no flag (see [Scope](#scope)). |
| `--allow-live-eval` | Let `live`'s `eval` op run Ruby inside the game. Off by default; a session must also be started with `allow_eval: true`, so the model cannot turn it on alone. `live status` and `project_info` report it. See [docs/live.md](docs/live.md#safety). |
| `--verbose` | Log tracebacks to stderr. |

## Try it without a project of your own

The test fixtures build a small but complete project — two maps, a talking NPC
with a conditional branch, a chest, tilesets, a database, scripts:

```bash
python tests/fixtures/build_project.py ./demo-ace
```

Add `vx` or `xp` as a second argument for a VX or XP project instead. Then point
the server at the folder.

## Tools

Twenty tools, grouped by what they touch. The ones that write are marked ✏️;
each of those takes `dry_run` and backs the file up before changing it.

| Tool | What it does |
| --- | --- |
| `project_info` | Engine, text encoding, RTP status, file inventory, entry counts, and (on request) the fields each Ruby class actually carries in this project. **Call this first.** |
| `db_list` | List a database table with a per-table summary of each entry. |
| `db_get` | Full entries, every field as stored. |
| `db_edit` ✏️ | Batch update / create / delete. Patches merge; `clone_from` copies an existing entry so a new one carries the right fields. Deletes report what still references the entry. |
| `map_list` | The map tree, plus where the party starts. |
| `map_get` | One map's properties, its events in summary, and which tile ids it uses. |
| `map_edit` ✏️ | Change properties, create a map (file + tree entry together), delete one, or resize it keeping the tiles that still fit. |
| `map_tiles` ✏️ | Read, write or flood-fill a rectangle of tile ids on any of the three tile layers, or of Ace's fourth layer — raw, or just its region ids (`view=region`) or shadow bits (`view=shadow`), keeping the other part. |
| `map_paint` ✏️ | Build a VX Ace map without computing tile ids: paint an autotile *kind* (`A2:0`, `A4:8`, or a plain `A5:3`) over a rectangle, a cell list or an outline, and every cell gets the edge/corner shape its neighbours call for while the cells around the edit are re-shaped to join it, as the editor does. `room`, `corridor` and a seeded, repeatable `dungeon` lay out floor, A4/A3 wall faces and wall tops in the VX Ace dungeon style, with the editor's wall shadows; `region` paints region ids. Bounded per call (cells, rooms, re-shaped cells). VX and XP are refused. |
| `map_render` | Draw a VX Ace map as a PNG you can look at: autotiles, the three tile layers, shadows, event graphics from the project and RTP, optional regions and grid, crop and scale. Returned as an image (up to 1,000,000 bytes of PNG) and always written to a file whose path is returned, for clients that show no images. VX maps are refused. |
| `event_get` | Events and their command lists, rendered as readable indented script. Lists at most 2000 events per call; `events_truncated` says there are more and `offset` pages to them. |
| `event_edit` ✏️ | Author events and command lists from a compact DSL (see below). |
| `command_reference` | The event command codes this engine dispatches, and their confirmed parameter layouts. |
| `search` | Find text or a command code anywhere: dialogue, comments, entry names, map names, scripts. |
| `validate` | Lint the project for broken references and event-logic traps. |
| `references` | Where is item 12, skill 5, switch 40, variable 3, common event 7 or map 4 used? Every event command, page condition, troop page, database field and System start position that names it, each with a readable `where` and `what`, a `coverage` object saying which kinds of site were and were not scanned, and Ruby-script text matches counted apart (never as references). Read-only; at most 200 sites a call, paged with `offset`. See [docs/references.md](docs/references.md). |
| `refactor` ✏️ | Renumber a map or a database entry and everything that names it, atomically (`swap` exchanges two), or name a switch or variable. **Fails closed**: only tables whose reference sites are declared complete (maps, common events, items, skills, troops, on VX Ace) can be renumbered; `dry_run` lists every site with before and after. See [docs/references.md](docs/references.md). |
| `map_critique` | Design-quality analysis validate does not run — reachability (which maps nobody can reach, and which of those are just blank), dead_ends (reachable maps the party cannot leave), switch_usage (switches and variables read but never set, or set but never read) and a ranked summary of everything worth a look by default; dead_space (how much of each map's ground layer is empty), tile_variety (Shannon entropy of each tile layer's ids; Ace's region and shadow layer counted apart) and stat_outliers (database entries that are statistical outliers within their own table) return their full tables when named. |
| `scripts` ✏️ | Read and replace script editor slots. |
| `backups` ✏️ | Snapshot, list, restore, prune. |
| `raw_data` ✏️ | Escape hatch: get or set any field by path. |
| `live` | Run a scratch copy of the game and drive it: state, virtual keys, frame stepping, test setup, screenshots, crash capture. See [Live play-testing](#live-play-testing). |

Three reference documents are served as MCP resources — the data format, the
event command table, and a workflow guide — each **generated for the engine of
the project that is open**, so an Ace project's reference documents tilesets and
regions while a VX project's document areas and the global tileset.

### Writing events

`event_edit` takes commands as small objects and handles indentation, message
box splitting and branch terminators:

```json
{
  "map_id": 1,
  "ops": [{
    "op": "create", "x": 8, "y": 6, "name": "Guard",
    "patch": { "graphic": { "character_name": "People1" }, "trigger": 0 },
    "commands": [
      { "text": "Halt! The gate is shut." },
      { "if": { "switch": 1, "is": "on" },
        "then": [ { "text": "...go on through." }, { "switch": 2, "value": "on" } ],
        "else": [ { "text": "Come back with the key." } ] },
      { "choices": ["Fine", "Who are you?"],
        "branches": [ [], [ { "text": "The gate guard." } ] ] }
    ]
  }]
}
```

Supported forms: `text`, `comment`, `script`, `switch`, `self_switch`,
`variable`, `wait`, `common_event`, `label`, `jump`, `transfer`, `exit`, `if`,
`loop`, `break_loop`, `choices`, and these, which write a command in the
parameter layout the open engine's editor writes:

| Form | Command | Notes |
| --- | --- | --- |
| `move_route` | Set Move Route (205) and its 505 step lines | Steps by name (`"move_up"`, `{"jump": [0, -1]}`, `{"wait": 15}` ...), target `"player"`, `"this"` or an event id, `repeat` / `skippable` / `wait` flags. |
| `shop` | Shop Processing (302) and Shop Goods (605) | Items, weapons and armour; `purchase_only`; a `price` override on VX Ace only. |
| `show_picture`, `move_picture`, `erase_picture` | 231, 232, 235 | Pictures 1–100 on VX Ace, 1–20 on VX. `move_picture` is VX Ace only. |
| `tint_screen`, `flash_screen`, `shake_screen` | 223, 224, 225 | Tone and Color are written as RGSS `Tone` / `Color`. |
| `fadeout_screen`, `fadein_screen` | 221, 222 | |
| `gold`, `item`, `weapon`, `armor` | 125, 126, 127, 128 | `op` add/sub, a `value` or `from_variable`; `include_equipped` when removing gear. |
| `party` | Change Party Member (129) | add (optionally `initialize`) or remove. |
| `battle` | Battle Processing (301) with 601–604 | `troop`, `from_variable` or (VX Ace) `random`; `win` / `escape` / `lose` branches exist only when escape or defeat is allowed, as in the editor. |
| `name_input` | Name Input Processing (303) | |
| `bgm`, `bgs`, `me`, `se` | 241, 245, 249, 250 | `RPG::BGM`, `BGS`, `ME`, `SE` on both engines. |
| `event_location` | Set Event Location (203) | Direct or by variables. |
| `balloon`, `animation` | 213, 212 | A wait flag on VX Ace; VX has none. |

Each VX Ace layout was checked against editor-written data: compiling the
matching form for every one of 1,232 such commands in a real VX Ace game, and
362 in the editor's own sample game, reproduces the editor's bytes exactly
(`tests/test_event_dsl.py`, opt-in with `RPGVXACE_TEST_PROJECT`). VX's
layouts come from VX's own `Game_Interpreter`, and a form whose VX layout that
leaves open is refused on VX with the reason. Anything else goes through
`{"raw": {"code": 285, "parameters": [...]}}`, which is written verbatim.

### What `validate` catches

Transfers to maps that don't exist or to coordinates off the edge of the target
map; missing graphics, faces, audio and (on Ace) tileset sheets; **assets whose
name differs only in capitalisation**, which work on Windows and fail on a
case-sensitive filesystem; encounters using troops that were deleted; maps
pointing at a tileset that doesn't exist; command lists referencing deleted
items, weapons, armour, actors or common events; `Jump to Label` with no
matching label; tile data whose size disagrees with the map's; orphan map files
and map-tree cycles; event pages that a later unconditional page permanently
shadows; and autorun pages that trap the player.

That last rule is narrower than it first appears, and deliberately so. An event
runs the **last** page whose conditions match, so the usual cutscene shape —
page 1 autoruns and sets a self switch, page 2 is conditioned on it and takes
over — does terminate. Only an event whose *final* page is an unconditional
autorun that never transfers the player, erases itself or ends the game actually
traps them.

### Results on a real project

Run against the 39-map VX Ace game mentioned above, `validate` reported two
findings, both genuine:

* an `error`: a Transfer Player sending the party to (40, 36) on a map that is
  only 25×20;
* a `warning`: an enemy whose battler is stored as `Crystaloid_large` when the
  file on disk is `Crystaloid_Large`.

Getting there meant fixing two false-positive sources this codebase would
otherwise have shipped: 185 "missing audio" warnings that were really RTP
assets, and 6 "unstoppable autorun" warnings that were really the correct
cutscene pattern.

### What `map_critique` reports

`validate` is about correctness — is a reference sound. `map_critique` is
about design quality, and runs several checks (`scopes` picks).

Called with no `scopes` it runs reachability, dead_ends and switch_usage and
adds a **summary**: every finding worth a look, ranked, without the per-map
and per-field tables (a default call on a 39-map project is about 7 KB; with
every table it was 64 KB). Each finding has a `priority` (1 is first), a
`severity` and a `prompt_to_look` — a prompt, not a verdict:

- **high** — would block play if unintended: a dead end the graph is sure
  of, a switch or variable gate that nothing ever sets.
- **medium** — the same where something a static reader cannot see may
  explain it (an `uncertain` dead end, a `low`-confidence gate), and
  unreachable maps that have content.
- **low** — unusual against this project's own data: maps with unusual dead
  space, uniform ground or low ground-tile variety, the most extreme stat
  outliers (the worst
  entry of each of the five fields furthest out), and unreachable maps that
  are blank (folder maps).

"Unusual" for a map is not a hard-coded number: `empty_ground_fraction` and
ground-layer `entropy_bits` are fenced by Tukey's IQR fences over the
project's own non-blank maps, the same method and the same skip rules as
`stat_outliers` (fewer than 8 maps, or a zero interquartile range, and that
check is skipped with the reason). The fences used are under `map_fences`.

Two absolute floors sit under those fences, because on a real game the
fences alone flagged nothing (most maps had no empty ground, so the
dead-space IQR was 0; the ground entropy spread so wide that its lower
fence was below 0):

- **uniform_ground** — a non-blank map whose ground layer uses exactly one
  tile id is flagged whatever the fence says, and not listed again under
  `low_tile_variety`; `upper_layers_empty` names those with nothing drawn
  above the ground either. A map with no tile at all is not listed here: its
  ground is 100% empty, which `unusual_dead_space` says. The rule is under
  `map_fences.uniform_ground`.
- **dead space with no spread** — when the interquartile range over 8 or
  more maps is zero, the maps with *some* empty ground are fenced among
  themselves if at least 8 have it and they spread; otherwise any map more
  than 50% empty is flagged. `map_fences.dead_space` keeps the reason
  Tukey's fence was skipped and states the rule that ran under `fallback`.

Each map finding lists `maps` as ids, like every other finding, with its
metric (`empty_ground_fraction`, `ground_entropy_bits`) as a list beside it
in the same order.
At most 20 findings are listed, with `findings_total` and
`findings_truncated`; if a walk behind the summary ran out of allowance it
says so under `findings_stopped`. `dead_space`, `tile_variety` and
`stat_outliers` return their full tables only when named in `scopes`, and
`scopes: ["summary"]` returns the summary alone. All the walks of one call
share one walk allowance.

The first check is **reachability**, walked from
the party's start position (`System#start_map_id`) across every direct
Transfer Player command in the project. A map on disk that nothing leads to
comes back in `unreachable`.

Two things it deliberately does not guess at: a transfer whose destination is
read from a game variable at runtime is counted under
`unresolved_transfers` rather than turned into an edge, since no static
reader can know what the variable will hold; and a transfer written inside a
common event is treated as reachable from anywhere, since a common event can
be called from any map or run on its own as a parallel process.

Transfers made from a Script command are followed too, by text pattern rather
than Ruby parsing: `reserve_transfer(map_id, ...)` (usually
`$game_player.reserve_transfer(...)`) or VX's
`$game_temp.player_new_map_id = N` with an integer literal is an edge, counted
in `script_transfers`; any other first argument is counted in
`unresolved_script_transfers` and is not an edge. Not seen: aliases and
wrappers, `eval`, transfers inside scripts in the Scripts file that an event
only calls, and whether a guarded call can ever run (so it can under-report
unreachability, never over-report it). `script_blocks_clipped` counts script
blocks too long to scan in full.

The second check, **dead_ends**, is the converse: maps the party can reach and
cannot leave, over the same direct-transfer graph.

- `no_exit` — a reachable map with no outgoing transfer to another map (a
  transfer back to itself, or to a map id not on disk, is not an exit; the
  latter is listed as `transfers_to_missing_maps`).
- `closed_regions` — two or more maps that transfer only among themselves.
- `one_way` — maps that have an exit but none that leads back to the start
  map; usually story progression, reported only so it can be told apart.

Each `no_exit` / `closed_regions` entry carries `entered_from` and an
`uncertain` flag with `reasons`: a variable-indirect transfer anywhere
(`unresolved_transfers`) or a Transfer Player in a common event / troop page
(`common_event_transfers`) can be an exit the static graph cannot see, and
so can a script that **may transfer the player** and can run on that map —
on its own events (`map_script`), or in a common event those events call
(followed through further calls), an autorun or parallel common event, or a
troop page, which can run anywhere (`common_event_script`). Up to three such
places are named under `may_exit_via`. A script may transfer when it calls
`reserve_transfer` / sets `player_new_map_id` with a non-literal map id, calls
`eval`, `send` or a relative, or names a method or class that the Scripts
file defines around a transfer call (the stock `command_201` included); a
script too long to read in full, or any script when the Scripts file cannot
be read, counts too. Any other script — `$game_variables[1] = 2`, a class
unlock, autodash — no longer makes a dead end uncertain. Vehicles are not
modelled. If the walk budget is exhausted the scope reports
`dead_ends_stopped` and no findings, since a partial graph would invent dead
ends.

`unreachable` stays a plain list of map ids; `unreachable_detail` has one
entry per id saying whether that map is `blank` (no tile on any of the three
tile layers and no events -- a folder-like placeholder, not a design finding)
and giving its width, height, `nonzero_cells` (tiles on layers 0-2) and
`event_count`. Shadows and region ids on VX Ace's layer 3 are not tiles, so
they make no map un-blank.

The third check, **dead_space**, reports per map its `width`/`height`,
`empty_ground_fraction` (cells whose tile id on layer 0, the ground layer, is
0), `bounding_box` (`x0,y0,x1,y1` inclusive, or `null` when layer 0 is
entirely empty) and `fill_of_bounding_box` (occupied ground cells over the
box's area). These are measurements, not verdicts: an autotile or any
non-zero id counts as occupied, and a cell empty on layer 0 may still hold an
upper-layer tile, a shadow or region id on layer 3 (VX Ace) or an event, so
the fraction is an upper bound on how much of the map the player sees as void.
Each row also has `blank`, `nonzero_cells` (tiles on layers 0-2 only) and, on
VX Ace, `region_cells` and `shadow_cells`: the layer-3 cells holding a region
id, and those with any shadow bit set (one cell can be both).

**Tile variety** (`scopes: ["tile_variety"]`) reports, for every tile layer
(0-2) of every map, the Shannon entropy of its tile ids in bits (`entropy_bits`), with
`cells`, `non_empty_cells`, `distinct_tiles` and `normalised`. Tile id 0
("nothing drawn") is not counted as a symbol: entropy is taken over the
non-empty cells, and a layer with none reports `null`, not 0.0. `normalised`
is entropy / log2(`distinct_tiles`), 0 to 1, so a small map and a large one
are comparable; it is `null` when only one id is present. This is a
descriptive figure for ordering maps, not a verdict: a lake or a bare floor is
correctly low-entropy, random noise is high-entropy, entropy ignores where
tiles sit, and each autotile edge shape counts as a distinct id. VX Ace's
layer 3 is not tile ids (each word is `region * 256 + shadow`), so it has no
row under `layers`: each map's `regions` (`cells`, `by_id`) and `shadows`
(`cells`, `by_mask`) count it instead, region 0 and mask 0 left out. Counting is
capped at 4,000,000 cells per call; maps past it are listed under `skipped`.

**`stat_outliers`** compares numeric fields of each database table (actors,
classes, skills, items, weapons, armors, enemies, states) against that
table's own distribution, using Tukey's fences (below `q1 - 1.5*IQR` or above
`q3 + 1.5*IQR`; quartiles by linear interpolation). Per field it reports `n`,
`median`, `q1`, `q3`, the fences and the outlying entries (`id`, `name`,
`value`, `direction`, plus an informational `modified_z`), worst first, at most
10 per field with `truncated` set when there are more (`outliers_total` is
always the full count). Only an explicit allowlist of balance-relevant fields is
read — never ids, icon indexes, animation ids, scopes, flags or other codes;
the list actually used is echoed under `method.fields_considered`. Weapon,
armor and enemy `params` are read as `mhp mmp atk def mat mdf agi luk` (VX
Ace); class params Tables are sampled at levels 1 and 99. A field with fewer
than 8 values, or with a zero interquartile range (most of the table shares one
value), is listed under `skipped` with the reason rather than judged. An
outlier is a prompt to look, not a defect: a boss is supposed to have the
largest HP in its table.

**`switch_usage`** answers the other classic question: why a gated door or
event can never fire. For numbered game switches and variables it reports
`read_but_never_set` (a page condition, Conditional Branch, troop page
condition, common-event trigger, enemy action condition or variable operand
that nothing in the project ever writes — so it is the constant false/0 for
the whole game) and `set_but_never_read` (dead state). Every finding carries
its id, the name from System, a count, and up to three where-found
locations, with `locations_truncated` when there are more; each list shows at
most 25 findings and gives its `total` and `truncated`. Each read also says
whether its own condition `holds_while_unset`.

* **Ranges** (Control Switches/Variables `start..end`) are kept as intervals
  and clamped to the engine's 5000 ids, never expanded, so a crafted
  `1..2000000000` costs the same as `1..2`.
* **Writes** also include Input Number, Select Key Item, Get Location Info
  and Switch ON/OFF steps in move routes; **reads** include the
  Control Variables operand form, the variable-operand parameters of a
  documented list of commands, `\V[n]` in message text, and `v[N]` in
  damage formulas.
* **Self switches** (A–D per event) are a separate namespace and are
  *excluded*; only their read/write counts are given under `self_switches`.
* **Scripts** can touch any switch. Literal indices (`$game_switches[12] =
  true`) are followed by line-by-line pattern matching; anything else —
  `$game_switches[Config::DOOR]`, an alias such as `s = $game_switches`, an
  access through `send` or `eval` — is counted under `script_access_unknown`
  and drops the `confidence` of every claim it could invalidate from `high`
  to `low` (a non-literal write undermines every "never set"; a non-literal
  read undermines every "never read"). Dynamic accesses inside slots named
  like the stock engine classes are not counted. Treat a `low` finding as a
  lead, not a verdict; many popular scripts (the YEA family) read switches
  through constants and will always lower the confidence of "never read".
* A walk that ran out of allowance says so under `switch_usage_stopped`
  and every claim is then `low`.

<!-- live: begin -->
## Live play-testing

`live` lets the agent play-test what it just changed, on Windows, for VX
Ace projects. `start` copies the project to a scratch folder under the
system temp directory -- leaving out save files and this server's own
folder -- injects a small Ruby bridge script above Main in the *copy's*
script list, and runs the copy's `Game.exe`. The project itself is never
run and never written. The game window appears on the desktop.

From there the agent can read `state` (map, player position, party, gold,
items, switches, variables, self switches, the running event, the message
window), `pause` and `step` an exact number of frames, queue virtual key
presses with `input` (nothing is sent to the operating system), `set`
switches, variables, gold and items, `teleport` the player, take a
`screenshot` (returned as an image), and -- only if the server was started
with `--allow-live-eval` *and* the session with `allow_eval` -- `eval` Ruby in
the game. A Ruby exception is caught and
returned with its backtrace instead of ending in a dialog. `stop` ends the
game and deletes the copy; the game also ends if the server does.

One session at a time, every wait bounded, refused under `--read-only`.
Protocol, limits and what it cannot do: [docs/live.md](docs/live.md).
<!-- live: end -->

## The RTP

An RPG Maker project can declare an RTP (Run Time Package) in `Game.ini` — the
shared library of graphics and audio that ships with the editor and lives
*outside* the project folder. The server reads that declaration and locates the
folder from the registry (`Enterbrain\RGSS3\RTP`) or the usual install
locations, then searches it alongside the project when checking asset
references.

If an RTP is declared but cannot be found, asset findings are reported as `info`
and say the file *may* come from the RTP, rather than claiming it is missing.
`project_info` reports what was declared, what resolved, and what didn't. Pass
`--rtp PATH` when the RTP lives somewhere unusual, or when running on a
non-Windows host where there is no registry to consult.

## Safety

* **Close the editor while working.** It holds the whole project in memory and
  rewrites every data file when it saves. The server detects a file that changed
  on disk after it was read and refuses that write, rather than silently losing
  one side of the edit.
* Writes are atomic (temp file + rename), so an interrupted write can't leave a
  half-serialised data file.
* The first time a run modifies a file, the original is copied to
  `.rpgvxace-mcp/backups/<timestamp>/`. `backups op=create` snapshots the whole
  `Data` folder; `backups op=restore` rolls back and takes a safety snapshot
  first.
* Every write is appended to `.rpgvxace-mcp/changes.jsonl`.
* `dry_run` on any writing tool reports the change without touching disk.

## Scope

**Included:** everything under `Data/` — the database, maps, events, tile
layers, the map tree, tilesets (Ace, XP) or encounter areas (VX), `System`, and
the script slots, for VX Ace, VX and XP projects.

**Not included:**

* **A live connection to a running game.** There is no runtime state, screenshot
  or playtest control. Editing files is the whole of it.
* **Editing an encrypted archive** (`Game.rgss3a`, `Game.rgss2a`). A folder
  that ships only the archive, with no `Data` folder beside it, opens
  **read-only**: `Data` is unpacked to a temporary cache (removed on exit) and
  the graphics and audio names are read from the archive for the asset checks.
  Every write is refused and says why, because the game loads the archive and
  would never see the edits. Unpack the archive into a `Data` folder to edit;
  a real `Data` folder always wins over an archive. The reader is bounded
  (100,000 entries, 260-byte names, 4 GiB extracted) and refuses names that
  would leave the cache. It was written from the format description and tested
  against an independently written encoder, not against a real archive.
* **MV and MZ.** They store JSON, which this codec cannot read; the server says
  so plainly rather than failing obscurely.
* **Most of XP's command authoring, and XP's pictures.** XP (`.rxdata`,
  `Game.rxproj`) is modelled and opens without a flag: its data files, its
  RGSS1 classes, its own command table, its three-layer maps and its tilesets
  are read and written, and new maps, events and pages are built in XP's
  shape. What XP does not get: `map_render` (XP's autotile patterns are its
  built-in Tilemap's), regions and shadows (XP has neither), the `event_edit`
  forms whose XP layout differs or was not checked (`wait`, `move_route`,
  the audio forms and the rest of the layout forms -- write those with `raw`),
  and `validate`'s rules that read graphics and audio out of command
  parameters. Each of these refuses on XP and says why.

### Engine differences the server knows about

|  | VX Ace (RGSS3) | VX (RGSS2) | XP (RGSS1) |
| --- | --- | --- | --- |
| Data files | `.rvdata2` | `.rvdata` | `.rxdata` |
| Tilesets | `Tilesets.rvdata2` database, `RPG::Map#tileset_id` | one global tileset in `Graphics/System`, no `tileset_id` | `Tilesets.rxdata`: one graphic, seven `autotile_names`, and `passages`, `priorities`, `terrain_tags` tables; `tileset_id` |
| Encounter areas | none — regions painted into tile layer 3 | `Areas.rvdata` | none — a map's `encounter_list` of troop ids |
| Map tile layers | 4: three of tile ids (0-2), then one word per cell of `region * 256 + shadow` (3) | 3, all tile ids (advisory beyond that: see below) | 3, all tile ids: 0, autotiles 48-383, tileset tiles from 384; drawn by priority, not layer |
| Audio references | `RPG::BGM` / `BGS` / `ME` / `SE` | the same (see below) | `RPG::AudioFile`, played through `Game_System` |
| Stats | `features`, `params`, damage `formula` | flat fields, parameter curves as Tables | flat fields (str, dex, agi, int, pdef, mdef, SP), actor `parameters` Table, icons by file name |
| Event pages | `priority_type`, a condition with item and actor | the same | `always_on_top`; a condition of switches, variable and self switch; a graphic with hue, opacity and blend |
| Event commands | 116 codes | 100 codes (no 104, 105, 137, 138, 216, 217, 243, 244, 261, 281–285, 324, 405) | its own table: Wait is 106, Set Move Route 209 with 509 step lines, Set Event Location 202, Show Animation 207; 104 and 105 are text options and button input |
| Strings | Ruby 1.9: carry an encoding, normally UTF-8 | Ruby 1.8: bare bytes in the editor's code page | Ruby 1.8: bare bytes |

Common to all three: an event runs the **last** page whose conditions match,
and a command list ends with a blank command at indent 0.

XP's facts come from the RGSS1 reference (mirrored
[here](http://www.rpg-maker.fr/dl/monos/aide/xp/index.html)) for the classes,
from XP's default scripts for the command table and layouts (`Interpreter`
parts 1-7, `Game_Map`, `Game_Character`, `Sprite_Character`), and from copies
of XP editor data — a new project and a small game in the
[ARC repository](https://github.com/borisblizzard/arcreator) — for the field
sets, the tile id ranges and the parameter counts. There is no XP install or
project on the machine this was written on, so what only data could settle is
marked advisory where it is served: which of an autotile's 48 patterns an id
selects (the built-in Tilemap's), and the code page an XP editor writes.

The tile layers were described wrongly here before: layer 2 was called a
shadow mask on both engines and Ace's layer 3 a bare region id. RGSS3's
`Game_Map` reads layers 0-2 as tile ids (`layered_tiles`) and a region as
`data[x, y, 3] >> 8`, and a real VX Ace project agrees (layer 2 full of B-E
ids, region 1 stored as 256, shadows in the low four bits). For VX, RGSS2's
`Game_Map#passable?` reads all three layers as tile ids, which is all that is
confirmed: which sheets go on which layer and where VX keeps the editor's wall
shadows have not been checked against a real VX project, and the served
documents mark that advisory.

VX's audio was described as `RPG::AudioFile` here, which is what the RGSS2
reference prints beside each audio attribute. It is the superclass. RGSS2
calls `play` on every stored audio object — `@map.bgm.play`,
`$data_system.sounds[n].play`, `timing.se.play`, and `@params[0].play` in
commands 241, 245, 249 and 250 and in a route's play-SE step — and only
`RPG::BGM`, `BGS`, `ME` and `SE` have `play`, so a stock VX game could not
reach its title screen with an `RPG::AudioFile` in those slots. The
reference's own class pages agree (`RPG::BGM`'s referrers are Map, System,
Vehicle and EventCommand). So VX is modelled with the same four classes, and
the `bgm`/`bgs`/`me`/`se` forms and the route's `se` step work on VX. What
stays unconfirmed without a VX project is the order VX's editor writes their
fields in.

## Confidence and provenance

Class structures come from the RGSS reference that ships with each editor,
cross-checked against a real project's files; event command codes come from each
engine's `Game_Interpreter`. Two things are marked advisory in the code and in
the served documents because they were derived from runtime behaviour rather
than the manuals: **tile id ranges** and **tileset flag bits** (on XP, the
autotile patterns). Move route
command codes used to be the third; they are now read from each engine's
`Game_Character` and match every route in a real VX Ace project.
`command_reference` returns `null` parameters for any command
whose layout isn't confirmed, rather than guessing — in that case read an event
that already uses the command (`event_get` with `include_raw`) and copy the
shape.

The schema this server reports is *observed from your project*, not asserted
from documentation: `project_info include_schema=true` tallies the instance
variables actually present and flags any it has no description for. That matters
for projects with script-added fields.

## Development

The server has no dependencies and the test suite needs only the standard
library, with one exception: `scripts/prove_guards.py` (the guard prover) runs
each guard's tests through **pytest**, and `tests/test_prove_guards.py` is
meant to be run with it. Install it as a development-only extra (the server
itself stays stdlib-only):

```bash
pip install -e ".[dev]"      # or: pip install -r requirements-dev.txt
```

Run everything from the repository root. The test modules put `src/`, `tests/`
and `tests/fixtures/` on `sys.path` themselves, so none of these needs
`PYTHONPATH`:

```bash
python -m unittest discover -s tests                  # everything
python -m unittest tests.test_tools.TestProjectInfo   # one class
python -m unittest tests.test_guards_fields           # one module
python -m pytest tests                                # everything, under pytest
python -m pytest tests/test_tools.py -k ProjectInfo   # a subset
```

The full suite is about a thousand tests and takes about three minutes, half of
it in `tests/test_prove_guards.py`, whose two meta-tests re-run the other test
files in subprocesses (a batch of files at a time, then the suite in reverse
order). For a quicker check while editing:

```bash
python -m pytest tests --ignore=tests/test_prove_guards.py    # ~1.5 minutes
python -m pytest tests/test_codebase_shape.py tests/test_schema.py \
  tests/test_marshal.py tests/test_tools.py                   # ~1 minute
```

They cover the Marshal codec against Ruby's exact
byte output, byte-identical round trips over whole projects, every tool against
generated demo projects **for every engine** (the VX and XP suites re-run the
Ace ones, so each profile is exercised by the same assertions; where XP's data
differs -- a Show Text with no face, a tileset tile of 384 -- its suite says
the XP version of the same check), Shift_JIS projects,
RTP resolution, concurrent-editor protection, and the MCP protocol driven end to
end against the server as a subprocess.

Nine further tests run only when you point them at a real project:

```bash
RPGVXACE_TEST_PROJECT="C:/Games/MyAceProject" \
  python -m unittest tests.test_tools.TestRealProject
```

They are read-only: they check that every data file round-trips byte-for-byte,
that every map and event reads, that `validate` runs, and that
`map_critique`'s `dead_ends` agrees with its own reachability graph. The
last four check figures read from the author's own project, one of them
what each tile layer holds, and skip themselves on any other, which they
recognise by its title in System and `Game.ini`. One more lives in `tests/test_event_dsl.py` and takes the same
variable: it rebuilds every command the `event_edit` layout forms cover from
the form that should produce it, and requires the editor's exact bytes.

```bash
RPGVXACE_TEST_PROJECT="C:/Games/MyAceProject" \
  python -m unittest tests.test_event_dsl.TestEveryRealCommandRebuilds
```

And one in `tests/test_schema.py`: every real object of a class the server
builds from scratch, rebuilt by the server's own constructor with the real
values filled in, must dump to the real object's bytes.

```bash
RPGVXACE_TEST_PROJECT="C:/Games/MyAceProject" \
  python -m unittest tests.test_schema.TestNewObjectsMatchARealProject
```

| Module | Role |
| --- | --- |
| `marshal_rb.py` | Ruby Marshal 4.8 reader and writer. |
| `rubytypes.py` | Python stand-ins for Ruby values, including RGSS `Table`, `Color`, `Tone`. |
| `rgss_archive.py` | Reader and safe extractor for `Game.rgss3a` / `Game.rgss2a`. |
| `convert.py` | Reversible Ruby ↔ JSON mapping, text codec, patch merging. |
| `schema.py` | Engine profiles: file registries, documented class fields, templates. |
| `commands.py` | Per-engine event command tables, readable rendering, the authoring DSL. |
| `paths.py` | What a caller may name, and where a name may lead; the project errors. |
| `project.py` | Project and engine discovery, RTP resolution, caching, atomic writes. |
| `backups.py` | Snapshots and the change log under `.rpgvxace-mcp/`. |
| `docs.py` | The reference documents served as MCP resources. |
| `analysis/` | Command walks, reference scanning, the `validate` rules (`lint/`), and the `map_critique` scopes. |
| `tools/` | The MCP tools. |
| `server.py` | JSON-RPC 2.0 over stdio, tools and resources. |

## Prior art

Design ideas were taken from the existing RPG Maker MCP servers, which are worth
looking at if you work on MV/MZ:

* [Zagos/RPG-Maker-AI-Toolkit-Multi-Platform](https://github.com/Zagos/RPG-Maker-AI-Toolkit-Multi-Platform)
  — multi-engine including VX Ace, via a Ruby bridge, plus live game control.
* [DiegoLopez0208/RpgMakerMVUltimate-MCP](https://github.com/DiegoLopez0208/RpgMakerMVUltimate-MCP)
  — the consolidated-tool approach and offline project validation.
* [Xerolo44/RPG-Maker-MV-MCP](https://github.com/Xerolo44/RPG-Maker-MV-MCP)
  — backing up everything the agent touches.
* [devmagary/MCP-Maker](https://github.com/devmagary/MCP-Maker) and
  [k4zuki0539/rpgmaker-mz-mcp](https://github.com/k4zuki0539/-rpgmaker-mz-mcp)
  — MZ project manipulation.

Borrowed from them: consolidating operations into a small number of tools rather
than one per field, `dry_run` previews, automatic backups, a change log, and
offline validation. The departures are the Ruby-free Marshal codec, per-engine
schema and command tables, observed-schema reporting, RTP-aware asset checks,
and refusing writes when the editor has touched a file underneath the server.

Format references: the RGSS help that ships with RPG Maker VX and VX Ace
(mirrored [here](http://www.rpg-maker.fr/dl/monos/aide/vx/index.html)), the
default script documentation for `Game_Interpreter` in
[VX](http://lib.orzfly.com/sites/rpgmaker-default-scripts-docs/docs/rpgmaker-vx.ja/classes/Game_Interpreter.html)
and
[VX Ace](http://lib.orzfly.com/sites/rpgmaker-default-scripts-docs/docs/rpgmaker-vxace.ja/classes/Game_Interpreter.html),
and Ruby's `marshal.c`.

## License

MIT.
