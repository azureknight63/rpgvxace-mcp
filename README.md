# rpgvxace-mcp

An MCP server that lets an AI assistant work on an **RPG Maker VX Ace** project:
read and edit the database, maps, events, tile layers, tilesets and RGSS3
scripts, then check the result for broken references. **RPG Maker VX** projects
are supported too — the server detects which engine made the project and adapts.

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

## Install

Python 3.10 or newer (developed and tested on CPython 3.11).

```bash
git clone <this repo> && cd rpgvxace-mcp && pip install -e .
```

Or skip installing and run it from the source tree with
`PYTHONPATH=src python -m rpgvxace_mcp`.

## Configure your MCP client

Claude Code:

```bash
claude mcp add rpgvxace -- rpgvxace-mcp "C:/Games/MyAceProject"
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
| `--encoding cp932` | Force a text code page. The default auto-detects utf-8, cp932 (Japanese) or cp1252. |
| `--backup-keep N` | How many backup snapshots to keep (default 10). |
| `--allow-other-engines` | Open an XP project anyway (see [Scope](#scope)). |
| `--verbose` | Log tracebacks to stderr. |

## Try it without a project of your own

The test fixtures build a small but complete project — two maps, a talking NPC
with a conditional branch, a chest, tilesets, a database, scripts:

```bash
python tests/fixtures/build_project.py ./demo-ace
```

Add `vx` as a second argument for a VX project instead. Then point the server at
the folder.

## Tools

Seventeen tools, grouped by what they touch. The ones that write are marked ✏️;
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
| `map_tiles` ✏️ | Read, write or flood-fill a rectangle of tile ids on any layer — including Ace's region layer. |
| `event_get` | Events and their command lists, rendered as readable indented script. |
| `event_edit` ✏️ | Author events and command lists from a compact DSL (see below). |
| `command_reference` | The event command codes this engine dispatches, and their confirmed parameter layouts. |
| `search` | Find text or a command code anywhere: dialogue, comments, entry names, map names, scripts. |
| `validate` | Lint the project for broken references and event-logic traps. |
| `map_critique` | Design-quality analysis validate does not run — today, which maps nobody can reach from the party's start position. |
| `scripts` ✏️ | Read and replace script editor slots. |
| `backups` ✏️ | Snapshot, list, restore, prune. |
| `raw_data` ✏️ | Escape hatch: get or set any field by path. |

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
`loop`, `break_loop`, `choices`. Anything else goes through
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
about design quality, and today runs one check: **reachability**, walked from
the party's start position (`System#start_map_id`) across every direct
Transfer Player command in the project. A map on disk that nothing leads to
comes back in `unreachable`.

Two things it deliberately does not guess at: a transfer whose destination is
read from a game variable at runtime is counted under
`unresolved_transfers` rather than turned into an edge, since no static
reader can know what the variable will hold; and a transfer written inside a
common event is treated as reachable from anywhere, since a common event can
be called from any map or run on its own as a parallel process.

More checks from the same family — dead space, tile variety, stat outliers
across the database — are proposed but not yet implemented.

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
layers, the map tree, tilesets (Ace) or encounter areas (VX), `System`, and the
script slots.

**Not included:**

* **A live connection to a running game.** There is no runtime state, screenshot
  or playtest control. Editing files is the whole of it.
* **Encrypted archives** (`Game.rgss3a`, `Game.rgss2a`). Point the server at an
  unpacked project.
* **MV and MZ.** They store JSON, which this codec cannot read; the server says
  so plainly rather than failing obscurely.
* **XP.** The Marshal codec reads `.rxdata` fine, but XP's data classes and
  command codes are not modelled, so opening one needs
  `--allow-other-engines` and every report says it is being described with the
  closest model available.

### Engine differences the server knows about

|  | VX Ace (RGSS3) | VX (RGSS2) |
| --- | --- | --- |
| Data files | `.rvdata2` | `.rvdata` |
| Tilesets | `Tilesets.rvdata2` database, `RPG::Map#tileset_id` | one global tileset in `Graphics/System`, no `tileset_id` |
| Encounter areas | none — regions painted on tile layer 3 | `Areas.rvdata` |
| Map tile layers | 4 (2 tiles, shadow, region) | 3 (2 tiles, shadow) |
| Audio references | `RPG::BGM` / `BGS` / `ME` / `SE` | `RPG::AudioFile` |
| Stats | `features`, `params`, damage `formula` | flat fields, parameter curves as Tables |
| Event commands | 116 codes | 100 codes (no 104, 105, 137, 138, 216, 217, 243, 244, 261, 281–285, 324, 405) |
| Strings | Ruby 1.9: carry an encoding, normally UTF-8 | Ruby 1.8: bare bytes in the editor's code page |

Common to both: an event runs the **last** page whose conditions match, and a
command list ends with a blank command at indent 0.

## Confidence and provenance

Class structures come from the RGSS reference that ships with each editor,
cross-checked against a real project's files; event command codes come from each
engine's `Game_Interpreter`. Three things are marked advisory in the code and in
the served documents because they were derived from runtime behaviour rather
than the manuals: **tile id ranges**, **tileset flag bits**, and **move route
command codes**. `command_reference` returns `null` parameters for any command
whose layout isn't confirmed, rather than guessing — in that case read an event
that already uses the command (`event_get` with `include_raw`) and copy the
shape.

The schema this server reports is *observed from your project*, not asserted
from documentation: `project_info include_schema=true` tallies the instance
variables actually present and flags any it has no description for. That matters
for projects with script-added fields.

## Development

```bash
PYTHONPATH=src python -m unittest discover -s tests
```

265 tests, no dependencies. They cover the Marshal codec against Ruby's exact
byte output, byte-identical round trips over whole projects, every tool against
generated demo projects **for both engines** (the VX suites re-run the Ace ones,
so both profiles are exercised by the same assertions), Shift_JIS projects,
RTP resolution, concurrent-editor protection, and the MCP protocol driven end to
end against the server as a subprocess.

Four further tests run only when you point them at a real project:

```bash
RPGVXACE_TEST_PROJECT="C:/Games/MyAceProject" PYTHONPATH=src \
  python -m unittest tests.test_tools.TestRealProject
```

They are read-only: they check that every data file round-trips byte-for-byte,
that every map and event reads, and that `validate` runs.

| Module | Role |
| --- | --- |
| `marshal_rb.py` | Ruby Marshal 4.8 reader and writer. |
| `rubytypes.py` | Python stand-ins for Ruby values, including RGSS `Table`, `Color`, `Tone`. |
| `convert.py` | Reversible Ruby ↔ JSON mapping, text codec, patch merging. |
| `schema.py` | Engine profiles: file registries, documented class fields, templates. |
| `commands.py` | Per-engine event command tables, readable rendering, the authoring DSL. |
| `project.py` | Project and engine discovery, RTP resolution, caching, atomic writes, backups. |
| `analysis.py` | Reference scanning and the `validate` rules. |
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
