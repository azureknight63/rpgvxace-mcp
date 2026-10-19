"""The `undo` tool: list the journal of previous file contents, and revert.

The journal itself, and the reasons it is built the way it is, are in
`undo_journal`. This is only the tool over it: argument handling, the reply
shape, and the description a model reads.
"""

from __future__ import annotations

from typing import Any

from ..undo_journal import (
    MAX_UNDO_BLOB_BYTES,
    MAX_UNDO_BYTES,
    MAX_UNDO_ENTRIES,
    MAX_UNDO_LIST,
    MAX_UNDO_STEPS,
)
from .registry import (
    BOOL,
    Context,
    DRY_RUN,
    INT,
    STR,
    ToolError,
    check_op_args,
    echo,
    one_of,
    opt_bool,
    opt_int,
    opt_int_default,
    partial,
    prop,
    schema,
    tool,
    write_reply,
)


DEFAULT_UNDO_LIST = 20

# What each undo op reads.
UNDO_OP_ARGS = {
    "list": {"op", "limit"},
    "revert": {"op", "count", "to", "force", "dry_run"},
}


def _row(entry: Any, restorable: bool) -> dict[str, Any]:
    """One entry as a caller sees it. Journal text is hostile: bounded."""
    return {
        "id": entry.seq,
        "step": entry.step,
        "at": echo(entry.at),
        "tool": echo(entry.tool),
        "summary": echo(entry.summary),
        "file": entry.file,
        "change": entry.change,
        "previous_bytes": entry.before_size,
        "restorable": restorable,
    }


@tool(
    "undo",
    "Undo recent edits to the project data",
    "Every edit this server writes keeps the file's PREVIOUS bytes in a "
    "bounded journal under .rpgvxace-mcp/undo, so a mistake can be taken "
    "back one change at a time -- unlike `backups`, which holds the state at "
    "the start of the session only. A change is one tool call, however many "
    "files it wrote (map_edit create writes the map and MapInfos: one undo "
    "reverts both). Ops: list (newest first; each row has an id, the tool, "
    "its summary, the file, and whether it created, modified or deleted "
    "it) and revert (count=N undoes the last N changes; to=ID undoes every "
    "change made after the one that entry id belongs to; dry_run previews "
    "exactly which files go back to which entry). An undo is itself "
    "journalled, so reverting it again redoes the change. Safety: a file "
    "that is no longer the bytes the journal says were last written (the "
    "RPG Maker editor saved since) makes the whole undo refuse, naming the "
    "file, unless force=true; a revert across several files is all or "
    "nothing; a read-only server refuses (a dry_run still previews). "
    "Ceilings: the journal keeps the last %d entries and %d bytes (%d MB) of "
    "previous file contents, oldest changes dropped first -- list reports "
    "how many were dropped; no single file above %d bytes is kept (the "
    "server reads none larger); one change that alone needs more than the "
    "byte ceiling is refused; list returns at most %d rows (default %d); "
    "revert undoes at most %d changes at once. A write the journal cannot record (disk full, "
    "read-only folder) is REFUSED rather than made without an undo, and says "
    "so. Does NOT undo changes made outside this server, and does not "
    "journal a write that leaves a file's bytes unchanged."
    % (MAX_UNDO_ENTRIES, MAX_UNDO_BYTES, MAX_UNDO_BYTES // (1024 * 1024),
       MAX_UNDO_BLOB_BYTES, MAX_UNDO_LIST, DEFAULT_UNDO_LIST,
       MAX_UNDO_STEPS),
    schema({
        "op": prop(STR, "list or revert.", enum=list(UNDO_OP_ARGS)),
        "limit": prop(INT, "For list: how many rows, newest first. At most "
                           "%d, default %d." % (MAX_UNDO_LIST,
                                                DEFAULT_UNDO_LIST),
                      minimum=1, maximum=MAX_UNDO_LIST),
        "count": prop(INT, "For revert: undo the last N changes (default 1 "
                           "when `to` is not given). At most %d."
                           % MAX_UNDO_STEPS,
                      minimum=1, maximum=MAX_UNDO_STEPS),
        "to": prop(INT, "For revert, instead of count: an entry id from "
                        "list. Undoes every change made AFTER the change "
                        "that entry belongs to, leaving the project as it "
                        "was just after it.", minimum=1,
                   maximum=10 ** 12),
        "force": prop(BOOL, "For revert: overwrite a file that was changed "
                            "since the journal last wrote it (the "
                            "overwritten bytes are journalled too)."),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def undo(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """List the journal, or revert recent changes."""
    op = one_of(args, "op", list(UNDO_OP_ARGS))
    check_op_args(op, args, UNDO_OP_ARGS, "undo")
    journal = ctx.project.journal
    if op == "list":
        limit = opt_int_default(args, "limit", DEFAULT_UNDO_LIST, 1,
                                MAX_UNDO_LIST)
        shot = journal.snapshot()
        newest_first = list(reversed(shot.entries))
        rows = [_row(entry, shot.blob_sizes.get(entry.seq) == entry.before_size)
                for entry in newest_first[:limit]]
        out: dict[str, Any] = {
            "entries": rows,
            "journal": {
                "entries": shot.count,
                "bytes": shot.bytes,
                "max_entries": MAX_UNDO_ENTRIES,
                "max_bytes": MAX_UNDO_BYTES,
                "dropped_entries": shot.dropped,
                "directory": str(journal.directory()),
            },
        }
        notes = []
        if shot.dropped:
            notes.append("%d older entries were dropped to stay inside the "
                         "ceilings (through entry %d); they cannot be undone."
                         % (shot.dropped, shot.dropped_through))
        out.update(partial(
            "entries", truncated=len(newest_first) > limit,
            notes=notes,
            not_read={str(seq): echo(why)
                      for seq, why in sorted(shot.problems.items())[:limit]},
            stopped=shot.stopped))
        return out
    count = opt_int(args, "count", None, 1, MAX_UNDO_STEPS)
    to = opt_int(args, "to", None, 1, 10 ** 12)
    if count is not None and to is not None:
        raise ToolError("give count or to, not both")
    if count is None and to is None:
        count = 1
    dry_run = opt_bool(args, "dry_run")
    return write_reply(
        journal.revert(count=count, to=to, force=opt_bool(args, "force"),
                       dry_run=dry_run), dry_run)
