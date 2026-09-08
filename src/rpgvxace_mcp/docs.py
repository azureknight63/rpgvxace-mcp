"""Reference documents served as MCP resources.

These exist so an agent can read the format's rules once instead of guessing
them per call: which files hold what, which command code does what, how tile ids
map to the tileset sheets, and which of that is certain versus advisory.

Every document is generated for the engine of the project that is actually open,
because VX Ace and VX differ in exactly the places an agent would otherwise get
wrong.
"""

from __future__ import annotations

from .commands import (
    CONTINUATION_CODES,
    MOVE_COMMAND_NAMES,
    command_table,
    param_specs,
)
from .project import CHANGE_LOG, WORK_DIR, Project
from .schema import (
    TILESET_FLAG_BITS,
    TILESET_FLAG_BITS_NOTE,
    TILE_ID_RANGES,
    TILE_ID_RANGES_NOTE,
)


def server_instructions(project: Project) -> str:
    profile = project.profile
    lines = [
        "This server edits an %s project's data files directly." % profile.label,
        "",
        "Start with project_info: it reports the engine, the text encoding, which "
        "files exist and how many entries each table holds.",
        "",
        "Ground rules that save wasted work:",
        "",
    ]
    lines += ["* " + note for note in profile.notes]
    lines += [
        "* Field names in patches are the RGSS instance variables without the "
        "leading @. project_info with include_schema reports the fields this "
        "project actually uses; that is the authority, not any general "
        "documentation.",
        "* To add a database entry, clone an existing one (db_edit op create with "
        "clone_from) so the new entry carries exactly the fields this project "
        "expects.",
        "* Anything with dry_run will show you the change before it touches disk. "
        "Writes back up the file first, and backups can be listed and restored.",
        "* Before writing a command whose parameter layout you are unsure of, read "
        "an event that already uses it with event_get include_raw, and copy the "
        "shape.",
        "",
        "Run validate after a batch of edits: it catches transfers to missing maps "
        "or off-map coordinates, missing graphics and audio, dangling database "
        "references and autorun pages that can never stop.",
    ]
    return "\n".join(lines)


def _modelling_banner(project: Project) -> list[str]:
    """A warning for a project whose engine is not the one being described.

    An engine opened with --allow-other-engines is described with the closest
    model available. Saying so beats a document that titles itself one engine
    and lists another's details.
    """
    if project.engine == project.profile.key:
        return []
    return [
        "> **This is a %s project described with the %s model**, because %s is "
        "not modelled by this server. The file names, class fields and command "
        "codes below are %s's. Treat every detail here as unconfirmed for this "
        "project." % (project.engine, project.profile.label, project.engine,
                      project.profile.label),
        "",
    ]


def _bullets(mapping: dict) -> list[str]:
    return ["* `%s` -- %s" % (key, value) for key, value in mapping.items()]


def data_format_reference(project: Project) -> str:
    profile = project.profile
    extension = project.extension
    lines = [
        "# %s data format" % profile.label,
        "",
        *_modelling_banner(project),
        "Every file under `Data/` is Ruby `Marshal.dump` output (format 4.8).",
        "This server reads and writes that format directly, so no Ruby",
        "installation is needed.",
        "",
        "## Files",
        "",
        "| Table name | File | Holds | Contents |",
        "| --- | --- | --- | --- |",
    ]
    for key, entry in profile.data_files.items():
        shape = {"array": "Array indexed by id (slot 0 is nil)",
                 "hash": "Hash of id => object",
                 "single": "One object",
                 "scripts": "Array of [id, name, zlib code]"}[entry.container]
        lines.append("| `%s` | `%s` | %s | %s |"
                     % (key, entry.filename(extension), shape, entry.description))
    lines += [
        "| (maps) | `Map001%s`, ... | One `RPG::Map` per file | Tiles and events |"
        % extension,
        "",
    ]

    if profile.has_tileset_database:
        lines += [
            "VX Ace has a `Tilesets` database and each map names one with",
            "`tileset_id`. It has no `Areas` file: encounter regions are painted",
            "onto tile layer 3 instead, and a map's `encounter_list` holds",
            "`RPG::Map::Encounter` entries carrying a troop, a weight and the set",
            "of regions the encounter applies to.",
            "",
        ]
    else:
        lines += [
            "VX has no `Tilesets` file: one global tileset lives in",
            "`Graphics/System` (`TileA1`..`TileA5`, `TileB`..`TileE`), so maps have",
            "no `tileset_id`. `Areas.rvdata` holds encounter areas, which VX Ace",
            "replaced with map regions.",
            "",
        ]

    lines += [
        "## Classes and fields",
        "",
        "Fields are listed without the leading `@` used in the files. This is the",
        "documented structure from the RGSS reference; use `project_info` with",
        "`include_schema` to see what this project actually contains.",
        "",
    ]
    for cls in sorted(profile.class_fields):
        lines.append("### %s" % cls)
        lines.append("")
        lines += _bullets(profile.class_fields[cls])
        lines.append("")

    lines += [
        "## Tile data",
        "",
        "`RPG::Map#data` is an RGSS `Table`: `width x height x %d`, addressed"
        % profile.tile_layer_count,
        "`[x, y, z]` with x running left to right and y top to bottom.",
        "",
    ]
    for layer, meaning in profile.map_layers.items():
        lines.append("* `z = %d` -- %s" % (layer, meaning))
    lines += [
        "",
        "Tile id ranges for a tileset's sheets. " + TILE_ID_RANGES_NOTE,
        "",
    ]
    for sheet, (low, high) in TILE_ID_RANGES.items():
        lines.append("* `%s` -- %d to %d" % (sheet, low, high))

    if profile.key == "vx_ace":
        lines += [
            "",
            "### Tileset flags",
            "",
            "`RPG::Tileset#flags` is a 1-dimensional `Table` of 8192 words, one",
            "per tile id. " + TILESET_FLAG_BITS_NOTE,
            "",
            *_bullets(TILESET_FLAG_BITS),
        ]

    lines += [
        "",
        "A `Table` serialises through `_dump` as five little-endian uint32 values",
        "(dim, xsize, ysize, zsize, size) followed by `size` uint16 cells. Some",
        "tables are semantically signed: animation cell data uses -1 (0xFFFF) for",
        "an empty cell.",
        "",
        "## Text encoding",
        "",
    ]
    if profile.strings_carry_encoding:
        lines += [
            "RGSS3 runs Ruby 1.9, so strings carry their encoding and are normally",
            "UTF-8. The server detects the code page at startup and reports it in",
            "`project_info`; override it with `--encoding` if that looks wrong.",
        ]
    else:
        lines += [
            "RGSS2 runs Ruby 1.8, whose strings are bytes with no encoding",
            "attached. Western installs of the editor write Windows-1252 and",
            "Japanese ones Shift_JIS (cp932). The server detects a code page at",
            "startup and reports it in `project_info`; override it with",
            "`--encoding` if the detection looks wrong.",
        ]
    lines.append("")
    lines.append("This project was read as **%s**: %s."
                 % (project.codec.name, project.encoding_reason))
    return "\n".join(lines)


def command_reference_document(project: Project) -> str:
    # Keyed off the *modelled* engine, not the detected one: an unmodelled
    # engine has no command table of its own, and titling the document with one
    # engine while listing another's codes is worse than saying so plainly.
    engine = project.profile.key
    table = command_table(engine)
    specs = param_specs(engine)
    lines = [
        "# %s event command codes" % project.profile.label,
        "",
        *_modelling_banner(project),
        "Codes are those this engine's `Game_Interpreter` dispatches, plus the",
        "continuation codes that only appear in data. A command is",
        "`{code, indent, parameters}`; a command list ends with a blank command",
        "(code 0) at indent 0.",
        "",
        "Parameter layouts are listed only where they are confirmed. For any other",
        "command, read an event that already uses it (`event_get` with",
        "`include_raw`) and copy the shape rather than guessing.",
        "",
        "| Code | Command | Parameters | Notes |",
        "| --- | --- | --- | --- |",
    ]
    for code in sorted(table):
        spec = specs.get(code)
        params = ", ".join(spec) if spec else ""
        note = "continues the command above" if code in CONTINUATION_CODES else ""
        lines.append("| %d | %s | %s | %s |" % (code, table[code], params, note))

    lines += [
        "",
        "## Nesting",
        "",
        "Conditional Branch (111) and Loop (112) open a block: the commands inside",
        "sit one indent deeper, and the block is closed by End Branch (412) or",
        "Repeat Above (413) back at the opener's indent. Show Choices (102) is",
        "followed by one When (402) per choice, an optional When Cancel (403), and",
        "End Choices (404).",
        "",
        "## Shared parameter values",
        "",
        "* Switch and self-switch operations: 0 turns it ON, 1 turns it OFF.",
        "* Variable operations: 0 set, 1 add, 2 subtract, 3 multiply, 4 divide,",
        "  5 modulo. Operand types: 0 constant, 1 variable, 2 random range.",
        "* Comparisons in a Conditional Branch: 0 ==, 1 >=, 2 <=, 3 >, 4 <, 5 !=.",
        "* Conditional Branch types: 0 switch, 1 variable, 2 self switch, 3 timer,",
        "  4 actor, 5 enemy, 6 character, 7 gold, 8 item, 9 weapon, 10 armor,",
        "  11 button, 12 script.",
        "* Character targets: -1 the player, 0 this event, otherwise an event id.",
        "* Event triggers: 0 action button, 1 player touch, 2 event touch,",
        "  3 autorun, 4 parallel process.",
        "",
        "## Move route commands",
        "",
        "Set Move Route (205) carries an `RPG::MoveRoute` in its second parameter.",
        "The editor also writes one 505 command per step so it can display the",
        "route; the interpreter ignores those, and `event_edit` adds them for you.",
        "These codes come from the move command set shared across XP, VX and VX",
        "Ace:",
        "",
        "| Code | Move command |",
        "| --- | --- |",
    ]
    for code in sorted(MOVE_COMMAND_NAMES):
        lines.append("| %d | %s |" % (code, MOVE_COMMAND_NAMES[code]))
    return "\n".join(lines)


def workflow_document(project: Project) -> str:
    label = project.profile.label
    regions_note = ("\n\nRegions: on VX Ace, tile layer 3 holds region ids, so "
                    "paint them with\n`map_tiles z=3`."
                    if project.profile.has_region_layer else "")
    return """\
# Working on a %(label)s project through this server

## Before you start

Close the %(label)s editor. It loads the whole project into memory and writes
every data file back when you press save, so anything this server changed while
it was open is lost. The server detects the case where a file changed underneath
it and refuses that write rather than fighting the editor.

## Finding your way around

1. `project_info` -- engine, encoding, file inventory, entry counts.
2. `map_list` -- the map tree, and where the party starts.
3. `map_get` -- one map's properties and a summary of its events.
4. `event_get` -- the command list of an event, rendered as readable lines.
5. `search` -- any line of dialogue, entry name or script line, anywhere.

## Making a change

Database entries: `db_list` to find the id, `db_get` to see the fields,
`db_edit` to change them. Patches merge, so send only what changes. To add an
entry, clone a similar one:

    db_edit table=items ops=[{"op": "create", "clone_from": 1,
                              "patch": {"name": "Elixir", "price": 900}}]

Events: `event_edit` writes command lists from a compact DSL that handles
message boxes, branches, loops and choices, including indentation:

    event_edit map_id=1 ops=[{"op": "create", "x": 8, "y": 6,
      "name": "Signpost",
      "patch": {"graphic": {"character_name": "People1"}},
      "commands": [
        {"text": "The road east leads to Riverbend."},
        {"if": {"switch": 1, "is": "on"},
         "then": [{"text": "The gate is open."}],
         "else": [{"text": "The gate is shut."}]}
      ]}]

Tiles: `map_tiles` reads and writes rectangles of tile ids per layer.%(tiles)s

Scripts: `scripts` reads and replaces the script editor slots.

## Staying safe

* `dry_run` on any writing tool shows the change without touching disk.
* The first time a run writes to a file, that file is copied into
  `%(work_dir)s/backups/`. `backups op=create` snapshots everything on demand,
  and `backups op=restore` rolls back.
* `validate` after a batch of edits. It finds broken transfers, missing assets,
  dangling database references, hidden event pages and autorun pages that never
  release control.
* Changes are appended to `%(work_dir)s/%(change_log)s`.

## What this server does not do

It edits files; it does not talk to a running game. There is no live state, no
screenshotting and no playtest control. Encrypted archives are not read -- point
the server at an unpacked project.
""" % {
        "label": label,
        "tiles": regions_note,
        "work_dir": WORK_DIR,
        "change_log": CHANGE_LOG,
    }


RESOURCES = [
    {
        "uri": "rpgvxace://reference/data-format",
        "name": "data_format",
        "title": "Data format",
        "description": "Which file holds what, the documented fields of the "
                       "map, event, command and (on VX Ace) tileset classes, "
                       "tile data layout and text encoding, for this project's "
                       "engine.",
        "mimeType": "text/markdown",
        "builder": data_format_reference,
    },
    {
        "uri": "rpgvxace://reference/event-commands",
        "name": "event_commands",
        "title": "Event command codes",
        "description": "Every event command code this engine dispatches, "
                       "confirmed parameter layouts, nesting rules and move "
                       "route codes.",
        "mimeType": "text/markdown",
        "builder": command_reference_document,
    },
    {
        "uri": "rpgvxace://guide/workflow",
        "name": "workflow",
        "title": "Working on a project",
        "description": "How to explore a project, make changes safely, and what "
                       "this server deliberately does not do.",
        "mimeType": "text/markdown",
        "builder": workflow_document,
    },
]
