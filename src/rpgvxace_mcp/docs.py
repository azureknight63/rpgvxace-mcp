"""Reference documents served as MCP resources.

These exist so an agent can read the format's rules once instead of guessing
them per call: which files hold what, which command code does what, how tile ids
map to the tileset sheets, and which of that is certain versus advisory.

Every document is generated for the engine of the project that is actually open,
because VX Ace and VX differ in exactly the places an agent would otherwise get
wrong.
"""

from __future__ import annotations

from typing import Any

from .commands import (
    CHARACTER_TARGETS,
    COMPARISONS,
    CONDITION_KINDS,
    CONTINUATION_CODES,
    MOVE_COMMAND_NAMES,
    SHARED_VARIABLE_OPERANDS,
    SWITCH_OPERATIONS,
    VARIABLE_OPERATIONS,
    VARIABLE_OP_CODES,
    VX_VARIABLE_OPERANDS,
    command_table,
    param_specs,
)
from .project import CHANGE_LOG, WORK_DIR, Project
from .schema import (
    numbered,
    TILESET_FLAG_BITS,
    TRIGGER_NAMES,
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
        "* One op carries only its own arguments. Every tool that takes an op "
        "declares which arguments each op reads, and anything else is refused "
        "rather than ignored -- so \"rename this event and rewrite its "
        "commands\" is two ops, not one. The refusal names the ops that do "
        "read what you passed.",
        "* A refusal is information, not a wall: each one names what it found "
        "and what to do instead, and is worth reading before rephrasing. A "
        "reply that reports what it did *not* do -- scopes it could not read, "
        "counts it had to truncate, text it matched only part of -- is telling "
        "you the answer is incomplete, not that nothing is there.",
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


# How each container kind reads in the reference table. A `.get` with a
# fallback rather than a subscript: an unrecognised kind is a reason to say so
# in one row, not to fail the whole document with a bare KeyError.
CONTAINER_SHAPES = {
    "array": "Array indexed by id (slot 0 is nil)",
    "hash": "Hash of id => object",
    "single": "One object",
    "scripts": "Array of [id, name, zlib code]",
}


def _bullets(mapping: "dict[str, str]") -> list[str]:
    return ["* `%s` -- %s" % (key, value) for key, value in mapping.items()]


def _numbered(mapping: "dict[int, Any]") -> str:
    """A code-to-meaning mapping as one readable clause.

    `schema.numbered` with this document's separator. The two used to be
    independent and disagreed on it -- "0 action button" here, "0: action
    button" there -- and both are rendered to the same model from the same
    tables, one in a field description and one in a bullet.
    """
    return numbered(mapping, separator=" ")


def _shared_values(engine: str) -> list[str]:
    """The parameter values several commands share, from the live tables.

    Hand-typed, this was six copies of six dicts, and one of them had drifted
    into contradicting the module it describes: it stated the Control
    Variables operand types as a closed set while `commands.py` records that
    only 0-2 are shared and that VX Ace reorganised the rest into a layout
    this server does not model. Generated, a table and its description cannot
    disagree -- which is the argument `_bullets` already makes for the tileset
    flag bits.

    Engine-keyed for the operands, because that is the one entry here whose
    answer really does differ between the two.
    """
    operands = (VX_VARIABLE_OPERANDS if engine == "vx"
                else SHARED_VARIABLE_OPERANDS)
    lines = [
        "* Switch and self-switch operations: %s."
        % _numbered({code: "turns it %s" % name
                     for code, name in SWITCH_OPERATIONS.items()}),
        # The symbols are what `summarize` prints in a rendered script; the
        # words are what the authoring DSL accepts. A model reads one and
        # writes the other, so both are named.
        "* Variable operations: %s. The DSL spells these %s."
        % (_numbered(VARIABLE_OPERATIONS),
           ", ".join(sorted(set(VARIABLE_OP_CODES) - set("=+-*/%")))),
        "* Variable operand types: %s." % _numbered(operands),
    ]
    if engine != "vx":
        # Said here rather than left to be inferred: the numbers above 2 exist
        # on VX Ace and mean something else, and a model that reads a closed
        # list will write one of them.
        lines.append(
            "  VX Ace uses higher operand numbers as well, and their layout is"
            " not confirmed here -- read a working Control Variables (122)"
            " with `event_get include_raw` and copy the shape.")
    lines += [
        "* Comparisons in a Conditional Branch: %s." % _numbered(COMPARISONS),
        "* Conditional Branch types: %s." % _numbered(CONDITION_KINDS),
        # The mapping covers the two special values; any other number is an
        # event id, which is the part a generated list cannot say for itself.
        "* Character targets: %s, and any other number is an event id."
        % _numbered(CHARACTER_TARGETS),
        "* Event triggers: %s." % _numbered(TRIGGER_NAMES),
    ]
    return lines


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
        shape = CONTAINER_SHAPES.get(entry.container, "an unrecognised shape")
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

    # The capability, not the engine key: `_schema_report` asks the
    # capability for this same content, and naming the engine here meant a
    # profile that gained a tileset database would lose the flags table
    # without anything saying so.
    if profile.has_tileset_database:
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
        *_shared_values(engine),
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
