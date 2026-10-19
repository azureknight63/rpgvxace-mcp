# Find usages, and safe renumber and rename

Two tools on one scan. `references` answers "where is item 12 used?";
`refactor` acts on the answer: it moves item 12 to 40 and changes every place
that named 12. They share the scan on purpose, so what `references` lists is
exactly what `refactor` would rewrite, and a `dry_run` is a preview of
something you have already read.

## `references`

Read-only.

| Argument | |
| --- | --- |
| `kind` | `actors`, `animations`, `armors`, `classes`, `common_events`, `enemies`, `items`, `maps`, `skills`, `states`, `switches`, `tilesets`, `troops`, `variables`, `weapons` |
| `id` | The number. Switches and variables 1 to 5000, everything else 1 to 9999. |
| `limit` | Sites listed (default 50, at most 200). |
| `offset` | Sites skipped, to page (at most 100,000). |

```json
{"kind": "items", "id": 1, "total": 3,
 "sites": [
   {"where": "map 2 event 1 (Chest) page 0 command 0", "what": "Change Items", "site": "command"},
   {"where": "enemies 1 field drop_items[0].data_id", "what": "drop_items[0].data_id", "site": "field"}],
 "sites_truncated": true,
 "coverage": {"scanned": ["event commands (...)", "map event page conditions", "enemy drop items"],
              "not_scanned": ["Ruby scripts (...)", "plug-in scripts and note tags", "..."]},
 "script_mentions": {"count": 0, "listed": [], "note": "text matches ..."}}
```

* `where` is written the way the other tools take ids: pages and commands are
  0-based, a map event is `map 3 event 5 (Name) page 0 command 12`, a database
  field is `classes 1 field learnings[0].skill_id`.
* `site` is the kind of place: `command`, `page_condition`, `field`, `text`
  (a `\V[n]` in a message) or `formula` (`v[n]` in a damage formula).
* A Control Switches or Control Variables over a range reports one site with
  `range: [lo, hi]` when the id is inside it.
* `coverage` is built from the same rule tables the scanner reads, so it cannot
  claim a kind of site the scan does not look at. `not_scanned` is hand-written
  per kind: for weapons and armors it says the starting equipment and the
  Change Equipment command are not modelled.
* **Ruby scripts are not references.** `$game_switches[40]`, `$data_items[12]`,
  `reserve_transfer(4, ...)` and the like are found by pattern and reported in
  `script_mentions` (a count, and up to ten lines), never in `total`. An index
  computed at run time is invisible, and the note on the reply says so.
* A walk that outruns its allowance says `references_stopped`; a map or table
  that could not be read is named in `references_not_read`. Either means
  `total` is a lower bound.

### What is scanned

* Event commands in map event pages, common events and troop battle pages. The
  base rules are `analysis/references.py`'s; VX Ace adds the commands that
  table lacks (`ACE_EXTRA_COMMAND_RULES` in `analysis/ref_sites.py`): Change
  HP/MP/State/Recover All/EXP/Level/Parameters/Skills, Change Equipment
  (actor), Change Nickname/Profile, Force Action, Change Class, Change
  Tileset, Set Vehicle Location, and the actor, enemy and class sub-kinds of
  Conditional Branch.
* A map event page's switch, variable, item and actor conditions (only when the
  condition's own flag is on: every new page carries inactive `switch1_id: 1`,
  `item_id: 1` and so on, and those are not references), a troop page's switch
  and actor conditions, and autonomous move routes.
* Database fields (`FIELD_RULES`): class learnings, enemy drops, actions and
  action conditions, troop members, actor classes, features (skill add/seal,
  state rates/resist/attack states), item and skill effects (state, skill,
  common event), animations on items, skills and weapons, a common event's
  trigger switch, the System starting party, battle-test members and troop,
  start positions, the last-edited map and the three vehicles' maps, map
  encounter lists and tilesets, and MapInfos `parent_id`.

Database fields are modelled for VX Ace only. On VX and XP `references` still
answers from the shared command rules, and `coverage.not_scanned` says what it
is not looking at.

## `refactor`

| Op | Arguments |
| --- | --- |
| `renumber` | `table`, `from_id`, `to_id`, `swap`, `dry_run`, `limit`, `offset`, `allow_script_mentions` |
| `rename_switch`, `rename_variable` | `id`, `name`, `dry_run` |

### `renumber`

Moves `table` entry `from_id` to `to_id`:

* the entry's own `id` field and its place in the array;
* every reference the scan finds, rewritten in place;
* for **maps**: the `Map###` file, the MapInfos key, children's `parent_id`,
  Transfer Player and Set Vehicle Location commands, and the System start,
  editor and vehicle maps.

`to_id` must be free. With `swap: true` both ids must exist and they exchange;
a second identical swap puts every byte back.

**It fails closed.** `analysis.RENUMBER_COMPLETE` lists, per engine and table,
the kinds of site a reviewer confirmed are *all* of the places that table is
named. Today that is VX Ace: `maps`, `common_events`, `items`, `skills`,
`troops`. A test requires the list to equal what the scanner reads for the
table, so adding a rule without revisiting the review fails rather than quietly
widening the claim. Every other table is refused, and the refusal names what it
would have missed (`RENUMBER_UNCOVERED`): for `weapons`, "actors' starting
equipment, the Change Equipment command, battle-test equipment". Skills 1 and
2 are the engine's Attack and Guard and cannot be renumbered or swapped.

**What blocks a renumber.** A `dry_run` lists these under `blocked`; the real
call refuses with the same text:

* the scan ran out of allowance, or a map or table could not be read;
* a reference already names the free `to_id` (moving the entry there would make
  it point at the wrong thing);
* a Ruby script mentions the number (`allow_script_mentions: true` proceeds;
  scripts are never rewritten).

**A site is rewritten once.** Marshal links share objects: one command list may
sit on several pages. A site is identified by the list and the slot that holds
the number, not by where the walk met it, so a shared parameter is rewritten
once (twice would map a swap straight back). The preview counts it once and
says `also_reached_at`.

**Atomic.** Every file is checked for staleness before the first is written. If
a later write fails, the earlier ones are restored byte for byte and a created
map file is removed (`Project.restore_bytes`); the reply says exactly what was
and was not put back. Files that needed no change are not touched.

**Preview.** `dry_run` returns `sites` (a window: `limit`, `offset`,
`sites_truncated`), each with `where`, `what`, `before` and `would_become`; the
completed call says `after`. It also returns `moves` (what happens to the entry
itself), `would_write` (every file), `blocked` and `script_mentions`.

### `rename_switch` / `rename_variable`

Sets the name in System's list for `id` (1 to 5000). The list is extended with
empty names if `id` is past its end. Names are one line, at most 100
characters, and must fit the project's code page.

## Limits

| Constant | Value |
| --- | --- |
| `MAX_SITES` | 200 sites per reply |
| `MAX_OFFSET` | 100,000 |
| `MAX_SWITCH_ID` | 5000 |
| `MAX_REFERENCE_ID` | 9999 |
| `MAX_NAME_CHARS` | 100 |
| `MAX_MENTIONS_LISTED` | 10 script lines shown (all are counted) |

Every scan shares one `WalkBudget`; a list inside an entry is charged per
element, so a Marshal link to a million-element list is stopped, not walked.

## Defects found in the existing scan

* `delete` previews (`db_edit`, `map_edit`) reported a finished scan when a map
  or the CommonEvents/Troops table could not be read: `iter_command_lists`
  skips an unreadable file silently, so "nothing points at this" was said
  about a project nobody had finished reading. They now say
  `references_scan_stopped` and name what was skipped
  (`analysis.references.scan_note`).
* `registry.as_int` formatted the caller's out-of-range number with `%d`, which
  raises a bare `ValueError` past 4,300 digits; it is quoted through `echo`
  now, so a 5,000-digit id is a `ToolError`.
