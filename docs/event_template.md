# Event templates (`event_template`)

A design note for the tool that builds whole events -- a treasure chest, a
door, a shopkeeper -- from a few parameters. Code:
`src/rpgvxace_mcp/tools/event_template_tools.py`; tests:
`tests/test_event_template_tools.py`.

## What it is for

Most events in a game are the same few shapes with different numbers: a chest
is "give this, say this, flip a self switch, show the open graphic on a
second page". Writing that with `event_edit` is correct and about forty lines
of JSON, and a model gets one of the forty subtly wrong now and then (the
self switch on the wrong page, a priority that lets the player walk through
the chest). `event_template` holds the shapes once, as data, and takes only
what varies.

It writes nothing `event_edit` could not. The event is built through the same
constructors (`profile.new_event`, `profile.new_page`, `apply_patch`,
`compile_commands`), so the map file is byte-for-byte what `event_edit
create` + `add_page` write for the same pages --
`TestBytesAreEventEdits` writes each template and the equivalent hand-written
`event_edit` ops into two copies of the project and compares the files.

## The decision: built-in templates, defined as data

The alternatives were user-defined templates (a caller saves a parameterised
command list) and templates as Python functions. Neither was taken.

- **Data, not functions.** A template is a record: `summary`, `engines`,
  `parameters`, and `events` -- each event a name and pages, each page a
  condition, a graphic, a trigger, a priority and `commands` in the DSL
  `event_edit` takes. `describe` returns the record itself, with
  `{"$": "reward_id"}` where a parameter goes, so the model reads exactly what
  will be written and a pinned test (`SIGN_DESCRIPTION`) holds its stability.
- **No user-defined templates this round.** They would need a place to store
  them (a project file this server does not otherwise own), a way to validate
  a template at definition time rather than at use, and a story for sharing
  them. The built-ins cover the events asked for; `event_edit` remains the
  tool for anything else.

Three constructs fill a record: `{"$": name}` (the parameter or a derived
value), `{"$fmt": "text {name}"}` (this file's own format strings; a value
that contains braces is inserted as typed, never re-read), and `{"$when":
name, "then": [...], "else": [...]}` (entries present only when a parameter
is given). Nothing a caller types is evaluated.

## Ops

| op | reads | does |
| --- | --- | --- |
| `list` | -- | the templates, their required parameters, the engines each is offered on |
| `describe` | `template` | one template: every parameter (type, bounds, default), and the event table |
| `preview` | `template`, `map_id`, `x`, `y`, `parameters`, `name`, `allow_overlap` | everything `create` would do, writing nothing: the events with their ids and, per page, the condition, graphic, trigger, DSL commands and the rendered script |
| `create` | the same, plus `dry_run` | writes. `dry_run` makes it a preview without the page detail |

`preview` runs the whole create path with the write switched off, so it
refuses what `create` would: a tile off the map, a tile with an event on it, an
id that does not exist, a graphic the project does not have.

## Placement

`map_id`, `x`, `y` place the (first) event. The event takes the map's next free
id, must be on the map, and is refused on another event's tile (naming it)
unless `allow_overlap`. The scan of the map's events is budgeted
(`_SCAN_STEPS`); a map with more events than that is refused rather than
walked, since "which id is free" cannot be answered from a part of the table.

## Parameters

Every parameter is read by a typed reader: a whole number is a whole number
(not a bool, a float or a string; a 5,000-digit integer is refused and not
echoed), a choice is one of its listed words, text is text. A refusal always
starts "parameter <name>", and an unknown name lists the real ones.

- **Text** (`lines`, `message`, `prompt`, ...): a list of lines, or one string
  split on newlines. At most 40 lines of 100 characters; no control
  characters (a stripped character would report the same success as one never
  typed), and it must be writable in the project's code page. The DSL then
  splits it into four-line message boxes. Names: at most 32 characters.
- **Database ids** (`reward_id`, `goods`): looked up in the project, so "there
  is no item 99" is a refusal, and the chest's default message reads the
  item's own name.
- **Assets** (`graphic`, `face`, `sound`): checked against the project's
  folders and the RTP. A file that is missing while every asset is visible is
  **refused**; a file that is missing while the RTP could not be located is a
  **warning** in the reply (it may live in the RTP), and so is a name that
  differs only in case. A path character in a file name is refused.
- **Switches**: a switch past the project's declared count is a warning (the
  editor's list will not show it), not a refusal -- the engine does not care.

## The templates

Facings are the words `down left right up` (and `keep` for a transfer, which
leaves the player's facing alone). Engines: **A** VX Ace, **V** VX, **X** XP.

| template | engines | parameters (required in bold) | writes |
| --- | --- | --- | --- |
| `treasure_chest` | A V | **`reward_type`** item/weapon/armor/gold, `reward_id` (not for gold), `amount` (**required for gold**), `message`, `show_message`, `sound`, `graphic`=`!Chest`, `graphic_index`, `opened_index`, `closed_facing`=down, `opened_facing`=up, `self_switch`=A | page 1 (action, same-as-characters): optional SE, the gain, "Found X!", self switch ON. Page 2 (self switch on): the opened sprite, no commands |
| `door` | A V X | **`target_map_id`**, **`target_x`**, **`target_y`**, `direction`=keep, `fade`=black/white/none, `sound`, `trigger`=touch/action, `graphic`, `graphic_index` | one page: optional SE, Transfer Player. The arrival tile is checked against the target map's size |
| `door_pair` | A V X | **`to_map_id`**, **`to_x`**, **`to_y`**, `direction`, `return_direction`, `fade`, `sound`, `trigger`, `graphic`, `graphic_index` | a door here and a door "`<name>` (return)" on `to_map_id`, each transferring to the other door's own tile; both maps in one staged change. The reply lists both events with their ids and coordinates |
| `sign` | A V X | **`lines`**, `graphic`, `graphic_index` | one page: Show Text on the action button |
| `npc_dialogue` | A V X | **`lines`**, `face`, `face_index`, `graphic`, `graphic_index`, `after_switch` + `after_lines` (together) | page 1: the lines. Page 2, when `after_switch` is ON: `after_lines` |
| `shop_npc` | A V | **`goods`** (item/weapon/armor ids, optional `price`, Ace only), `purchase_only`, `greeting`, `graphic`, `graphic_index` | optional greeting, Shop Processing |
| `save_point` | A V X | `prompt`, `graphic`=`!Crystal`, `graphic_index` | the prompt, then Open Save Screen (command 352) |
| `inn` | A V | **`price`**, `prompt`, `message`, `poor_message`, `graphic`, `graphic_index` | "Stay / Leave"; Stay: if the party has the gold, take it, fade out, wait 60, Recover All (the whole party), fade in, say the message; else say `poor_message` |
| `switch_toggle` | A V X | **`switch_id`**, `sound`, `graphic`=`!Switch`, `graphic_index`, `off_facing`=down, `on_facing`=up, `self_switch`=A | a lever: page 1 sets the switch ON and the self switch ON; page 2 (self switch on) sets both OFF. Each pull flips it |

A template that needs a form the open engine is not confirmed for is refused by
the same check `event_edit` makes -- an item or a shop on XP says "RPG Maker
XP: the "item" form is not offered" -- and `list` says so up front
(`engines`, `not_offered_elsewhere`). `TestVXAceMatrix`, `TestVXMatrix` and
`TestXPMatrix` create every template on every engine to hold the claim. On XP
the page has no `priority_type` (it has `always_on_top`), so the field is left
alone; `graphic_index`, `face` and `sound` are refused by name there.

## Why some things are the way they are

- **The inn's money check is a script condition** (`$game_party.gold >= N`).
  The DSL's `if` offers switch, self switch, variable and script conditions;
  the editor's own Gold condition (code 111, type 7) is not one of them, and
  building it with `raw` would have meant writing the whole branch's indents
  by hand. The script form runs the same test in both engines.
- **A door lands on the other door's own tile.** Arriving is not a move, so a
  Player Touch door does not fire again at once; the player steps off and back
  on to return. For a different landing tile use two `door` calls.
- **Recover All is written as `[0, 0]`** -- fixed designation, actor 0 = the
  whole party -- the layout the VX family's table lists for command 314 (XP is
  refused; its layout differs).
- **The chest's and lever's facings are defaults, not facts about your
  sheet.** The RTP chest and switch sheets show a chest opening and a lever
  pulling as the facing changes, which is the convention used; a custom sheet
  may differ. Pass `opened_facing` / `on_facing`, or check in the editor.
- **A `door_pair` writes two files in one call.** Both are validated before
  either is saved, and either both are staged or neither is; but if the second
  file's write is refused by the operating system after the first landed (the
  editor holding it, a full disk) the first stays written. The reply's
  `writes` says which landed, and `backups` restores the other.

## What it does not do

It does not edit an existing event, remove one, or move one (`event_edit`); it
does not draw the map around the event; it does not create switches or give
them names; it takes no user-defined templates; and `describe` does not run
the expansion for a set of parameters -- `preview` does.
