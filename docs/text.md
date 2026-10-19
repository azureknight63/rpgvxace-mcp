# Text export and import (`text`)

`text` lets a model proofread, restyle or translate every player-visible string
in a VX Ace or VX project and write the result back, without touching anything
that is not text. `op=export` writes the strings to a JSON file; `op=import`
reads such a file and writes the entries whose `text` you changed.

XP is refused: its Show Text keeps the first line in the 101 itself and has no
face, so the line structure this tool writes is not XP's.

## What is text

Decided from what the editor shows the player.

| Where | Keys | Kind |
| --- | --- | --- |
| Show Text (101 + its 401 lines, one entry) | `map:3/event:5/page:0/cmd:12`, `common_events:2/cmd:0`, `troops:1/page:0/cmd:4` | `show_text` |
| Show Choices (102), one entry per choice | `.../cmd:12/choice:1` | `choice` |
| Show Scrolling Text (105 + 405 lines) | as Show Text | `scroll_text` |
| Comments (108 + 408 lines), only with `include_comments` | as Show Text | `comment` |
| Map display name (Ace) | `map:3/display_name` | `map_display_name` |
| Actors, classes, skills, items, weapons, armors, enemies, states: `name`, `nickname`, `description`, `profile` | `items:12/name` | same as the field |
| Skill and state battle messages | `skills:1/message1` | `battle_message` |
| System: game title, currency unit, skill/weapon/armor type names, every term | `system/game_title`, `system/skill_types/2`, `system/terms/basic/3` | `title`, `currency`, `type_name`, `term` |

Not text: notes, event, switch, variable, troop, animation, tileset and common
event names, the map tree's names (only the editor shows them), scripts, and
commands that carry a name in their parameters (Change Actor Name and the
like). Empty strings are not exported.

Show Text's face, background and position (and scrolling text's speed) are
exported as read-only `context`.

## The file

```json
{"format": "rpgvxace-mcp-text", "version": 1, "engine": "vx_ace", "encoding": "utf-8", "entries": [
{"key": "items:1/name", "kind": "name", "text": "Potion", "original_hash": "..."}
]}
```

`original_hash` is a 96-bit blake2b over the kind and the stored bytes of the
entry's lines, each prefixed by its length as eight big-endian bytes. It says
whether the project still holds what was exported. Entries that cannot be
edited carry `"read_only": true` and a `reason`: bytes that are not valid in
the project's encoding, bytes that do not encode back to themselves
(for example two spellings of one character in cp932), text over the entry
ceiling, a line that is not a string or contains a line break, an object
reached from more than one place, or two strings that share a key.

Files go to `.rpgvxace-mcp/exports/` (the newest 20 are kept). This is a cache
and not a project edit, so an export works under `--read-only`. The folder is
checked to be inside the project before it is created.

## Import

`path` must resolve to a `.json` file inside the project (a relative name is
looked for in the exports folder); links and `..` are judged by where they end.
Import re-walks the project with the exporter's walker and looks each key up:
a key that addresses something that is not text, or a command that has changed
code, is not found and is never written, with or without `force`.

Per entry, in order: not found, wrong kind, unchanged (same text as now),
read-only, stale (hash differs, skipped unless `force`), rule violations
(control characters other than the line break, lone surrogates, NUL, a line
break in a one-line kind, an empty name, characters the encoding lacks, too
many lines or bytes). A skipped entry costs only itself and is listed with its
reason. Problems with the file's structure (not JSON, wrong types, repeated
keys or members, another engine or encoding, over the ceilings) refuse the
whole file.

An entry the project has changed since the export is reported stale even if
you did not edit it, because the file keeps only a hash of the old text.

### Line structure

Show Text becomes one 401 per line of text, at most 4 lines (or as many as the
command already had); scrolling text and comments take at most 200. The head
command (and its face and window settings) is kept; unchanged lines keep their
command objects; new lines take the head's indent. Edits within one list are
applied from the highest command index down, so a message that gains or loses
lines does not move one still waiting. A choice is rewritten in the 102 and in
the editor's matching `When` branch (402) when that copy still has the old text.
Commands are copied, never edited in place, so a command object linked from
several slots changes in one.

Unchanged entries leave every data file byte for byte as it was; only files
with at least one edit are written. Replaced strings take the project's usual
string form.

### Writes

Staged like every write: backed up, change-logged as tool `text`, stale-checked.
All files are marshalled before the first is replaced. Files are then written
one after another; a failure on a later file says which already landed (rerun
the export and import: entries that landed show as unchanged).
`dry_run` returns each change as `before` and `would_become`.

## Ceilings

50,000 entries per export, 65,536 stored bytes per entry, a 16,777,216-byte file,
keys of at most 200 characters, 4,096 maps, 20 kept exports, previews of at
most 50 entries. An import reply quotes at most 200
applied and 100 skipped entries, 300 characters of each text; the counts are
complete. The walk shares the project's event-walk budget and a byte budget for
the text it reads; a walk that stops says so in `walk_stopped`.
