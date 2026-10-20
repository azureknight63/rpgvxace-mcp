# Live game control (`live`)

A design note for the one tool that runs the game instead of reading its
files. Code: `src/rpgvxace_mcp/live/` (the machinery) and
`src/rpgvxace_mcp/tools/live_tools.py` (the tool surface).

## What it is for

An agent editing a VX Ace project can now play-test the result itself:
start the game, read its state, press keys, step frame by frame, set up a
test situation, teleport, take a screenshot, and get a Ruby exception back
as data instead of as a dialog on someone's screen.

## The decision: an injected bridge script

Three ways were on the table. Launch-and-observe (start Game.exe, watch the
window) gives no state and no determinism. A headless reimplementation of
RGSS3 would be a second engine to keep faithful to the first. An injected
bridge runs the game's own code in the real engine and adds only a thin
control layer, so what it reports is what the game actually does. That is
the one built.

## How a session works

1. **Scratch copy.** `start` copies the open project to a new folder under
   the system temp directory (`rpgvxace-live-*/game`). Save files
   (`Save*.rvdata2`), this server's `.rpgvxace-mcp` folder, symlinks and
   junctions are left out. The project itself is never run and never
   written; the copy carries `Game.exe`, so it runs on its own. The copy
   is bounded (100,000 files, 2 GiB).
2. **Injection.** The bridge is one script slot inserted directly above
   the slot that calls `rgss_main` (Main). Above Main so every class it
   patches exists; directly above so every other slot keeps its number,
   and a backtrace's `{0042}` names the same slot as the project's own
   script list.
3. **Launch.** The copy's `Game.exe` starts with the copy as its working
   directory. On Windows the process is put in a job object with
   kill-on-close, so if this server dies -- including being killed by its
   client, when no `atexit` runs -- the game dies with it. The next
   `start` sweeps scratch folders whose owning server is gone (each folder
   carries a marker naming its server's pid).
4. **Ready.** The bridge writes `ready.bin` (with the session token) on its
   first frame. `start` waits at most 60 s for it.

## Protocol

Files in `<copy>/.rpgvxace-mcp/bridge/`, each one message, Ruby Marshal on
both sides (RGSS3 has Marshal built in; this server has a Marshal codec).
Writers write `name.tmp` and rename, so a reader sees a whole message or
none.

| File | Direction | Content |
| --- | --- | --- |
| `config.bin` | Python -> Ruby, before launch | `token`, `paused`, `skip_title`, `allow_eval` |
| `ready.bin` | Ruby -> Python, once | `token`, `pid`, `frame`, `scene` |
| `request.bin` | Python -> Ruby | `{"seq", "op", "args"}` |
| `response.bin` | Ruby -> Python | `{"seq", "ok", "result" or "error", "frame", "paused", "scene", "crashed"}` |
| `crash.bin` | Ruby -> Python | `class`, `message`, `backtrace` (slot names added), `frame`, `scene` |

One request is in flight at a time. The bridge reads one request per
frame. A reply whose `seq` is not the one awaited is a late answer to a
request that timed out, and is discarded.

Why files and not a socket: RGSS3 is Ruby 1.9.2 with no standard library
on its load path. Checked on the real engine: `require` of `socket`,
`json`, `yaml` and `zlib` all raise LoadError (Zlib and Win32API do exist
as built-in constants). Files plus Marshal need nothing shipped.

## The hooks

All by aliasing, never by replacing a method body.

- **`Graphics.update`** -- once per rendered frame. After the real update
  the bridge counts the frame, reads one request, and while paused keeps
  calling the real update (so the window stays alive and repaints) without
  returning to the game. That is what makes a frame steppable: `step N`
  runs exactly N passes of the game's own update loop and pauses again.
  `Graphics.wait`, `fadeout` and `fadein` are redefined on top of it, so
  their frames are counted and steppable too.
- **`Input.update`, `press?`, `trigger?`, `repeat?`, `dir4`, `dir8`** -- a
  queue of virtual key presses, OR-ed with the real keyboard. `trigger?` is
  true on a press's first frame, `repeat?` follows RGSS3's 24-frame delay
  and 6-frame interval. Queued presses are separated by one released frame
  so the same key twice is two triggers. Nothing is sent to the operating
  system.
- **`SceneManager.run`** -- an exception that would have ended the game in
  a modal dialog is written to `crash.bin`, and the bridge enters a loop
  that keeps the window alive and still answers `ping`, `state`, `eval` and
  `quit`, so the wreck can be inspected. `RGSSReset` (F12) and `SystemExit`
  pass through untouched.
- **`SceneManager.first_scene_class`** -- with `skip_title` (the default),
  straight to a new game, as the title's New Game command would.

## Ops

`start`, `stop`, `status`, `state`, `pause`, `resume`, `step`, `input`,
`set`, `teleport`, `walk_to`, `interact`, `scenario`, `screenshot`, `eval`
(the last three before `screenshot` are described under
[Play-test automation](#play-test-automation)). The tool description carries the
limits; every argument is checked in Python before the game sees it, and
the bridge checks again against the running game's own data (switch and
variable ranges, item ids, map ids and sizes).

`screenshot` is taken from the Python side: the game's window is found by
pid and printed with `PrintWindow` (`PW_RENDERFULLCONTENT`, falling back to
`BitBlt`), then encoded as PNG with zlib -- standard library only. The MCP
reply carries the PNG as an image content block after the JSON text, and
the file is also written into the scratch folder (deleted by `stop`).

## Safety

- The project is copied, never run or written. A unit test hashes the
  fixture project before and after a session; the opt-in suite does the
  same for the real game's `Data/` and save files.
- One session at a time; a second `start` is refused until `stop`.
- `--read-only` refuses `start`. Every other op acts on a session, and a
  read-only server cannot have one, so refusing those too would be a
  refusal nothing could reach.
- `eval` runs arbitrary Ruby inside the game process, so it takes two
  keys. The first is the server's `--allow-live-eval` command-line flag,
  off by default, which only the person who configured the server can
  set; the model cannot. The second is `allow_eval: true` on `start`,
  which the model asks for per session. Without the flag, a `start` with
  `allow_eval` is refused before anything is copied or launched, and the
  refusal names the flag; `eval` itself re-reads the flag on every call
  and refuses, naming whichever key is missing. `live status`
  (`server_allows_eval`) and `project_info` (`live_eval_allowed`) say
  whether the flag is set. The session's key is also checked again by the
  bridge (which read it from `config.bin` before the first frame). The tool is advertised as not read-only and destructive,
  so a client should not auto-approve it.
- Every wait is bounded: 60 s to come up, 10 s per request, a step
  `10 + frames / 30` seconds, a teleport up to 600 frames. A game that
  exits, stops answering or shows a dialog produces a reply saying which.
- `stop` asks the bridge to quit, kills the process if it does not, and
  deletes the copy. A wedged game is killed after one wait, not two.

## Findings from the real engine

Verified on this machine against a real VX Ace game (the author's game, 39
maps, 169 script slots) with `tests/test_live_game.py`:

- **RGSS3 stops whenever its window is inactive.** With another window in
  front, or the game minimised, `Graphics.update` blocks inside the engine
  and the bridge never runs. Posting `WM_ACTIVATEAPP` (active) to the
  game's own window lets it run again without taking the focus from
  anyone, so every request and every wait sends it (`winapi.keep_awake`).
  A minimised window has no client area to print, so `screenshot` restores
  it without activating it first.
- An error raised while the scripts load -- in a slot above the bridge --
  cannot be caught by Ruby code that has not run yet. RGSS shows it in a
  message box and waits for a click forever. `start` reads the box's text
  (`EnumWindows` / `EnumChildWindows` on the game's pid), ends the game, and
  returns that text as the refusal.
- Backtraces name slots as `{NNNN}`, the slot's index; the bridge appends
  the slot's name.

## Limits and what could not be done

- **Windows and VX Ace only.** The bridge relies on VX Ace's
  `SceneManager` and RGSS3's Input symbols; XP and VX are refused.
- **Encrypted games** (`Game.rgss3a`) cannot be injected: there is no
  `Scripts.rvdata2` to put the bridge in.
- **`Graphics.transition` frames are not steppable.** The engine runs a
  transition's frames internally without calling `Graphics.update`, so a
  scene change consumes those frames in one go. Frame numbers count the
  bridge's frames, not `Graphics.frame_count`.
- **A screenshot shows the last rendered frame.** After a `teleport` or
  `set` while paused, `step` one or two frames before capturing.
- **A script that never returns to `Graphics.update`** (an infinite loop,
  a long synchronous load) cannot be reached; requests time out and `stop`
  kills the game. Ruby has no way to interrupt it from inside.
- **Errors in slots above the bridge** are reported from the dialog text,
  without a structured backtrace.
- **Scripts that replace (rather than alias) `Graphics.update`,
  `Input.*` or `SceneManager.run` at runtime** after the bridge loads would
  bypass it. No script in the test game does.
- **Audio** is not captured or controlled. The game plays its sound.
- **Real keyboard input** still reaches the game when its window has
  focus; virtual presses are OR-ed with it, not exclusive.
- **Save files** are not copied, so Continue on the title screen is
  disabled. Loading a save for a test is not supported yet.

## Play-test automation

`walk_to`, `interact` and `scenario` are made of the primitives above and add
no new power over the game: they press the same virtual keys and read the
same state. Logic is in `live/playtest.py`; the tool surface is in
`tools/live_tools.py`; three small read-only bridge ops serve them.

**Bridge ops (Ruby, not user-facing).**

- `passability {x0,y0,x1,y1}` -- one hex digit per cell (bit 1 down, 2 left,
  4 right, 8 up), each bit from the game's own
  `$game_player.passable?(x, y, d)`. Events that block, vehicles and the
  boat rules are therefore in the answer; nothing is recomputed from tile
  flags. At most `MAX_PASS_CELLS` (4096) cells per read; each cell is
  rescued on its own, so one that raises reads as blocked.
- `locate {event_id}` -- an event's x, y, direction, `through`, `erased`.
- `face {direction}` -- `set_direction` on the player, refused while it is
  moving. Used instead of a direction key because a key into open floor
  would walk.

A message window is detected from the `message` state section that already
existed; a battle from the scene name carried by every reply.

**`walk_to`.** Reads the player, asks for passability (the whole map when it
has at most 4096 cells, else a window around player and target with a
16-tile margin; a target needing a larger window is refused with advice to
walk to a waypoint), plans a breadth-first route, refuses one over 200
tiles, then loops: press the direction for one frame, step
`frames_per_step` (default 20, at most 60), read the player, let a step in
progress finish, classify. A press that moved nothing is repeated once.
Stop reasons: `arrived`, `blocked`, `moved_elsewhere` (a transfer, or an
event moved the player; the walk does not re-plan), `message_open`,
`battle`, `scene_changed`, `frame_budget`, `time_budget`, `no_path`. The
reply carries the planned `path`, `steps_taken`, the final `player`, the
scene, `frames_used` (the game's own frame counter), `seconds`, and the
open `message` if there is one. Bounds: `max_frames` (default 2400, at most
6000), `max_seconds` (default 60, at most 120). The clock is read between
requests, and every request has its own bounded wait (a step waits
`10 + 2 * frames / 60` seconds), so a call can overrun `max_seconds` by one
request. The game is left paused, as `step` leaves it.

**`interact`.** `direction` (turn in place, then press C), or `event_id`
(walk to the nearest tile beside the event, face it, press C), or neither
(press C facing the current way). Steps `frames` (default 12) and replies
with whether a message opened (and its text), whether the player moved, the
event the interpreter is running, and `changes`: switches, variables and
gold before against after (at most 100 entries each, with
`changes_truncated` and `changes_note` when a section was cut off or could
not be read). It refuses to act under an open message or a battle; advance
a message with `input` C. An event behind a counter is not reached (only the
four adjacent tiles are tried).

**`scenario`.** `steps` (at most 50), each `{op, args, expect}`. Allowed ops:
`status state pause resume step input set teleport walk_to interact
screenshot` (a screenshot step reports the file, not the image). `start`,
`stop`, `eval` and `scenario` are refused by name before anything runs, so
a scenario cannot reach the eval gate. Every step is checked up front with
the same schema bounds and per-op argument table as the op called alone. A
step fails when its op is refused by the game, reports it did not do the
thing (a walk that did not arrive; an interaction not made), or its `expect`
does not hold. `expect` keys: `map_id x y direction gold party_size` (a
number, or `{eq|ne|gt|ge|lt|le: n}`), `switches {id: bool}`, `variables
{id: n | {op: n}}`, at most 40 comparisons. A switch the bridge does not list
is off and a variable not listed is 0; a section cut at its entry ceiling
cannot vouch for an id past the cut. The reply: `passed`, `stop_reason`
(`completed`, `expectation_failed`, `step_failed`, `frame_budget`,
`time_budget`), `failed_step`, `failed_expect` {check, compare, expected,
actual}, and `results` for every step run (cut at 120,000 characters in total,
with `results_truncated`). `max_frames` (default 3000) and `max_seconds`
(default 90) bound the whole scenario; a walk inside gets what is left.

**Checked against a real game.** All seven checks below were run on a scratch
copy of a real VX Ace game (the project and its saves were byte-identical
afterwards):

1. `walk_to` two tiles away: `arrived`, `steps_taken` 2, `state` agrees.
2. `walk_to` onto a tile the game's passability blocks: `no_path`, nothing
   pressed.
3. A chest standing in a column: walking from two tiles below it to two
   tiles above took the 6-step route around it, not the 4-step one through.
4. `interact` with a chest (`event_id`): walked 2 tiles, faced it, the
   message opened with its text; further `input` C presses closed it. One C
   does not always close a message (the text may still be printing).
5. `walk_to` onto an exit tile: `moved_elsewhere` with `transfer_pending`
   true. The transfer happens on the frames after the step, so the player
   is still on the old map in that reply; `step` a few frames and read
   `state` for the new one.
6. A `scenario` with `expect` on position, a switch and a variable: passed,
   and with a wrong value gave `expectation_failed` with `failed_expect`.
7. An `eval` step in a scenario is refused before the game moves.

Two things came out of the real run. `interact` reports `interacted: true`
when the key was pressed, which is not the same as the target having run:
walking 24 tiles across a field, an enemy event touched the player, and the C
went to its balloon wait. The reply now carries a `note` naming the event
that was running when it was not the target. And a game kept in git copied
its `.git` folder into the scratch copy; the read-only objects then stopped
the copy being deleted, so version-control folders are no longer copied and a
read-only file no longer blocks the cleanup.

## Tests

- `tests/test_live.py` -- the Python side against
  `tests/fixtures/fake_game.py`, a stub that speaks the protocol (no
  RPG Maker, no Windows needed except one Win32 message-box test), plus
  static checks of the Ruby text: no `require`, every op served, file names
  substituted from Python, ASCII only, blocks balanced, `RGSSReset` and
  `SystemExit` never swallowed.
- `tests/test_live_game.py` -- opt-in, drives the real `Game.exe`:

      RPGVXACE_LIVE_TESTS=1 RPGVXACE_TEST_PROJECT="C:/path/to/project" \
        python -m pytest tests/test_live_game.py

  Set `RPGVXACE_LIVE_SHOTS=<folder>` to keep the screenshots.
- `tests/test_playtest.py` -- walk_to, interact and scenario against the
  fake game's tiny map model (grid, walls, a stale-blocked cell, an event
  that opens a message / flips a switch / adds gold, a teleport tile, a
  battle tile), plus the pure search and expectation logic.
