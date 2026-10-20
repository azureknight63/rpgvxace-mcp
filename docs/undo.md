# Undo (`undo`)

A design note for the tool that takes edits back one change at a time. Code:
`src/rpgvxace_mcp/undo_journal.py` (the journal) and
`src/rpgvxace_mcp/tools/undo_tools.py` (the tool). Tests:
`tests/test_undo_tools.py`.

## Why the existing records could not do it

- **Backups** copy a file aside once per server run, so a backup is the
  project as it was when the session started, not as it was before the last
  edit. Restoring one throws away every edit made since.
- **The change log** (`.rpgvxace-mcp/changes.jsonl`) says which tool touched
  which file and why. It never held the old bytes.

So there was nothing to put back. The undo journal is the third record: the
*previous bytes of a file, kept every time it is written*.

## The decision: a bounded per-write journal

Every write of a data file -- `save_file` (all editing tools), `delete_file`,
`backups restore`, and the undo itself -- first stores the file's previous
bytes under `.rpgvxace-mcp/undo/`, then writes. Two files per entry:

    e000000000042.json   sequence, step, time, tool, summary, file name, size
                         and hash of the previous bytes, hash of the bytes
                         written
    e000000000042.bin    the previous bytes (empty if the file did not exist)

A file that did not exist (a created map) is journalled as **absent**; undo
deletes it again. A file that was deleted is journalled with its bytes; undo
recreates it.

Alternatives not taken: re-reading the change log (it has no bytes); a copy
of the whole Data folder per edit (hundreds of megabytes for a ten-line
change); a reverse-diff format (a second serializer to keep faithful to the
editor's byte order, for a saving nobody needs on files this size).

### A change is a tool call

A **step** is one tool call. `map_edit create` writes the new map file and
`MapInfos`; both entries carry the same step, and one undo reverts both or
neither. The registry wraps every tool call in a step; `backups restore`
and `undo` open their own, so they are one change however many files they
touch. `count` and `to` count steps, not files.

### Sequence numbers live on disk

The sequence number is the number in the file name, taken from the folder.
A restarted server, or a second server on the same project, continues the
numbering. The number is claimed by creating its `.bin` file exclusively, so
two writers (even in the same second, even from a stale view of the folder)
cannot share one.

## Ops

| Op | Arguments | What it does |
|---|---|---|
| `list` | `limit` (1-100, default 20) | Entries newest first: id, step, time, tool, summary, file, `created`/`modified`/`deleted`, previous size, whether its saved bytes are intact. Reports the journal's size and how many entries were dropped. |
| `revert` | `count` (1-50, default 1) **or** `to` (entry id), `force`, `dry_run` | `count=N` undoes the last N changes. `to=ID` undoes every change made after the one that entry belongs to, leaving the project as it was just after it. `dry_run` previews exactly which files go back to the state before which entry. |

An undo is itself journalled, so reverting it again is a redo.

## Safety rules

- **Staleness.** A file is reverted only if its bytes on disk are the bytes
  the journal says were last written. If the RPG Maker editor (or anything
  else) saved since, the undo refuses, naming the file and why, and writes
  nothing. `force: true` overrides; the bytes it overwrites are journalled
  too, so the overrule can be undone.
- **A gap in the chain.** When an undo spans several changes to one file and
  the file was changed outside the server *between* two of them, that is also
  a conflict (the undo would discard the outside change).
- **All or nothing.** Every check runs, and every saved byte is loaded and
  verified, before the first file is touched. If a write fails part-way, the
  files already put back are restored.
- **The staleness check of the normal writers keeps working.** An undo drops
  the cached copies of the files it changed, so the next edit reads what the
  undo wrote, and a later editor save is still caught by the usual check.
- **Read-only.** A read-only server refuses a real revert; `list` and
  `dry_run` still work.
- **Containment.** Writes go through the same name rules as every other
  writer: a data file in `Data/` only, never a link, never outside it.

## The journal folder is not trusted

`.rpgvxace-mcp/` is inside the project, which the threat model treats as
hostile. Before an entry is shown or acted on it is validated: a strict name
pattern, a bounded size, JSON that parses (and does not recurse a parser to
death), whole numbers within their ranges, 32-character hex hashes, and the
file name through the same `safe_component` / `path_for` rules as backup
restore -- plus it must be a data file of this engine, not `Game.ini` or a
temp file. The saved bytes must be a regular, non-link file of exactly the
size the entry says, hashing to what it says. The journal folder itself must
not be a link.

An entry that fails is listed under `entries_not_read` with its reason and is
never acted on. Because reverting to an older point has to pass through it,
it also blocks any revert that would go that far; reverting only newer
changes still works.

## Ceilings

| Ceiling | Value | When passed |
|---|---|---|
| `MAX_UNDO_ENTRIES` | 200 entries (one per file per write) | oldest whole steps dropped |
| `MAX_UNDO_BYTES` | 268,435,456 bytes (256 MB) of previous bytes | oldest whole steps dropped |
| `MAX_UNDO_BLOB_BYTES` | 134,217,728 bytes: one file | refused (the server reads nothing larger) |
| `MAX_UNDO_LIST` | 100 rows per `list` | `entries_truncated` |
| `MAX_UNDO_STEPS` | 50 changes per `revert` | refused |

The newest step is never dropped. One change that alone needs more than the
entry or byte ceiling is refused rather than made. **A dropped entry is
reported, not hidden**: `list` shows `dropped_entries`, and a revert that
asks for more than remains says how many were dropped.

## When the journal cannot be written: fail closed

If the previous bytes cannot be kept (disk full, `.rpgvxace-mcp` read-only,
a file where the folder should be), **the write is refused**, with a message
that begins "the edit was NOT applied" and says why. The alternative was to
write anyway and warn; that was rejected because a warning is easy to miss
and the cost of the refusal is one clear error. A write whose bytes equal
what is on disk is not journalled (nothing to put back).

If the data write itself then fails, the journal entry is taken back. If even
that cleanup fails, the leftover entry is harmless: the file on disk does not
match what it says was written, so undoing it is refused as a conflict.

## Interplay with backups

- `backups restore` journals each file it overwrites (as one step), so a
  restore can be undone -- including after `prune` has removed the snapshot
  it came from. The journal does not depend on the snapshot.
- `prune` only ever touches `.rpgvxace-mcp/backups/<timestamp>` directories.
  The journal lives in `.rpgvxace-mcp/undo/`, a sibling, and snapshot names
  (a timestamp plus a label restricted to `[A-Za-z0-9._-]`) can never equal
  or enter it; a test makes snapshots labelled `undo` and `e000000000001`.

## What it does not do

- It does not undo changes made outside this server (the editor, a text
  editor, git).
- It does not journal anything but data files (not `changes.jsonl`, renders
  or snapshots).
- Journal entries are not cleared by closing the server; they live until the
  ceilings drop them. Delete `.rpgvxace-mcp/undo/` to forget them.
