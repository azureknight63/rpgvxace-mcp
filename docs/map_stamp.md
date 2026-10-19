# Map stamps

`map_stamp` keeps a rectangle of a map under a name and pastes it back later,
onto the same map or a different one, optionally mirrored or rotated. It is
the tool for the thing `map_tiles` and `map_paint` cannot do: reuse what is
already built -- a house, a lamp post with its shadow, a room with its chest
and the event that opens it.

| op | reads | does |
| --- | --- | --- |
| `copy` | `name`, `map_id`, `x`, `y`, `width`, `height`, `layers`, `include_events`, `dry_run` | stores a stamp |
| `paste` | `name`, `map_id`, `x`, `y`, `mirror`, `rotate`, `include_events`, `reshape_edges`, `dry_run` | writes it onto a map as one change |
| `list` | | the stamps kept, with size, layers, event count and source map |
| `delete` | `name`, `dry_run` | removes one |

`x` and `y` of a paste are required: they are the cell the stamp's top-left
corner lands on *after* it has been turned.

## Where stamps live

`.rpgvxace-mcp/stamps/NAME.json` inside the project. A stamp is a work
product of this server, not game data: it is not backed up and is not in the
change log; the *paste* is (as one `map_stamp` entry for the map file it
changed). Names are 1 to 48 letters, digits, dashes or underscores starting
with a letter or digit -- no dots, spaces or separators, and not a Windows
device name. The stamp folder is checked to lie inside the project *before*
it is made, so a link planted as `.rpgvxace-mcp` or `stamps` cannot carry the
write elsewhere; a stamp file that is itself a link is not followed.
`copy` never overwrites a name. Under `--read-only`, `copy` and `delete` are
refused (a preview with `dry_run` is not); `paste` is refused when it would
save, as any map write is.

## Ceilings

| | |
| --- | --- |
| `MAX_STAMP_CELLS` 5,000 | width times height of a stamp |
| `MAX_STAMP_EVENTS` 50 | events in a stamp; a rectangle holding more is refused, never copied with some missing |
| `MAX_STAMP_PAGES` 20 | pages of one stamped event |
| `MAX_STAMP_PAGE_COMMANDS` 2,000 | commands on one stamped page |
| `MAX_STAMPS` 100 | stamps kept in the folder |
| `MAX_STAMP_NAME` 48 | characters in a name |
| `MAX_STAMP_FILE_BYTES` 1,000,000 | bytes in one stamp file; checked before the file is parsed |
| `MAX_STAMP_LIST_BYTES` 8,000,000 | bytes `list` opens in all; the rest are named in `stamps_not_read` |
| `MAX_STAMP_RESHAPE_CELLS` 100,000 | cells one paste may re-shape (painted cells, the ring, wall runs beside them) |
| `MAX_STAMP_FLAGS` 100 | `position_dependent` rows in one reply (`position_dependent_truncated` says more existed) |

Cell counts in replies are *layer-cells*: a 3x2 stamp of four layers is 24.

## What is stored

Layers 0-2 (tile ids) and, on VX Ace, layer 3 (`region << 8 | shadow`).
`layers` narrows the default of every layer the map has. Events are stored as
`{id, name, x, y, pages: [{patch, commands}]}` with positions relative to the
stamp; `patch` holds the page's fields except its command list, and every
command is a `{"raw": {code, indent, parameters}}` entry. Rebuilding goes
through `commands.compile_commands` -- the compiler `event_edit`'s `commands`
use -- and the page-patch checks `event_edit` applies (the command list is not
patchable, an unknown field is refused). So a hand-written stamp may use the
DSL forms too, and is held to the same rules as an authored event.

An event that cannot be stored (a parameter that is not plain data, a page
over the ceiling) is not silently dropped: it is named in `events_not_read`
with the reason, and the rest of the stamp is kept.

Not stored: self-switch state (it lives in the save game, not the map), the
map's tileset, music and other settings, and anything outside the rectangle.

## Turning

`mirror` (`none`, `horizontal`, `vertical`, `both`) is applied first, then
`rotate` (0, 90, 180, 270 degrees clockwise). Mirrored twice is the stamp as
it was; four quarter turns are too -- both are proved on the pure transform and
again through the tool.

* **Autotiles.** An autotile id carries a shape number the editor chose from the
  cell's neighbours; turning the stamp changes who the neighbours are. After
  the cells are written, `autotile.reshape` (the rules `map_paint` uses) is run
  over the pasted rectangle and the one-cell ring around it, plus the wall runs
  beside those -- `reshape_edges`, default true. Tests derive the expectation
  independently: a layout is painted with `map_paint`, stamped and pasted
  turned, and compared with the same layout *painted directly* in its turned
  position.
  With `reshape_edges=false` the neighbours are left alone and an unturned
  stamp is written verbatim; a *turned* stamp's own cells are still re-shaped,
  because the shape they carry was chosen for another orientation and is simply
  wrong in this one.
  Rotating a wall kind (A3, A4 wall faces) gives the shapes the rules produce
  for the new neighbours, which are valid ids but may not look like a wall.
* **Shadows.** Layer 3's low nibble is four quarter bits (1 top-left, 2
  top-right, 4 bottom-left, 8 bottom-right). They move with the cell *and* turn
  within it (a top-left quarter on a cell that lands on the right edge by a
  mirror is a top-right quarter). The region byte, and bits nothing reads, stay
  as they are. The automatic shadow the editor adds to the right of a wall is
  not re-derived; a stamp carries the shadows it was copied with.
* **Events.** Positions move with the cells. The way an event faces turns
  with the stamp (`graphic.direction` of every page).
* **Engines.** Mirror and rotate are VX Ace only (the autotile model exists for
  nothing else). On VX and XP a stamp pastes as it is; `reshape_edges` has no
  effect there.

## Edges, overlaps, ids

* **Past the map edge: clip, and say so.** The cells that fit are written; the
  reply has `clipped_cells`, `cells_truncated` and `cells_note`. Refusing
  instead would make the common case (a stamp pasted near the border) a retry
  loop; clipping with a loud report loses nothing the caller was not told.
  An origin that is itself off the map is refused.
* **Events never overwrite.** An event whose cell holds another event, or that
  would stand off the map, is skipped and listed in `events_skipped` with the
  reason (and the id of the event in the way); `events_truncated` is set.
* **Ids.** Pasted events take the lowest free ids in the order the stamp stores
  them (ascending source id) -- the same rule `event_edit create` uses.
* **Self-switch state is not copied.** A pasted chest is a closed chest.

## `position_dependent`

Some commands mean something only where they were. They are not rewritten
(whether a Set Event Location should move with the stamp is the author's
call), and not left to be found in play: each is listed in `position_dependent`
as `{event, name, page, command, code, why}`. `event` is the new id on a
paste, the source id on a copy. Reported:

* Set Event Location (any mode): absolute coordinates, coordinates held in
  variables, or an exchange.
* Transfer Player by coordinates to the map the stamp was copied from or is
  pasted into.
* Show Animation, Show Balloon Icon, Set Move Route, and a Conditional Branch on
  a character's facing, that name an event by number: the ids were re-assigned,
  and the row says what the stamp event is called now.
* When the stamp is turned: a move route (a page's custom route, or a Set
  Move Route command) with directional steps (move, jump, turn), which are not
  mirrored or rotated; and, in any paste, a move-route script step naming a
  number.

Not analysed: the contents of Script commands, and variable-driven
references. XP's command numbers are handled (Set Event Location is 202 there).

## Untrusted input

A stamp file is read as hostile. Before the map is loaded: the size is checked
against `MAX_STAMP_FILE_BYTES` before any byte is parsed; the text must be
JSON with no `NaN`/`Infinity` and survive the parser (a deeply nested file is
a refusal, not a crash); the keys must be exactly the known ones; `format` and
`version` must match; the engine must be the open project's; the dimensions
must be whole numbers within the ceiling and agree with the length of every
layer's cell array; every cell must be a whole number 0-65535 (layer 3: only
region and shadow bits); layers must be distinct, ascending, and exist on this
engine; events must stand inside the stamp, and each is built with the
compiler. Any failure is a refusal that names the stamp and the field, and the
map is untouched. The same builder runs at `copy`, so a stamp this server
writes is one it will read back.

## Failure and preview

A paste is one staged write (`Context.staged`): tiles, events and re-shaping are
applied to the cached map and saved together as one change with a backup and
one change-log entry, or not at all. `dry_run` runs the same code and reads as a
preview (`would_paint`, `would_reshape`, `would_create`).
