"""The undo journal: the previous bytes of every data file this server writes.

Two records already exist and neither can undo anything. The backup store
copies a file aside *once per server run*, so it holds the state at session
start, not the state before the last edit. The change log (`recent_changes`)
records which tool touched which file and why, never the old bytes. This
journal is the third record, and the only one that answers "put it back the
way it was a moment ago".

How it is kept
--------------
Every write of a data file -- `save_file`, `delete_file`, `restore_backup`
and the undo itself -- first stores the file's *previous* bytes under
`.rpgvxace-mcp/undo/` and only then writes. One journal entry per file per
write, two files each:

    e000000000042.json   what happened (sequence, step, time, tool, summary,
                         file name, size and hash of the previous bytes, hash
                         of the bytes written)
    e000000000042.bin    the previous bytes themselves (empty when the file
                         did not exist: undo then deletes it again)

The sequence number is the number in the name and is taken from the folder,
not from memory: a restarted server, or a second server on the same folder,
continues the numbering, and the number is claimed by creating its `.bin`
exclusively, so two writers in one second (or one instant) cannot share it.

A *step* groups the entries one tool call made. `map_edit create` writes the
map and `MapInfos`; both entries carry the same step, and one undo reverts
both or neither.

Bounds (advertised by the `undo` tool)
--------------------------------------
`MAX_UNDO_ENTRIES` entries and `MAX_UNDO_BYTES` of previous bytes. When a
write would pass either, whole steps are dropped oldest first, never the one
just written, and the count of what was dropped is kept in `dropped.json` and
reported by `list`: history that is gone says so. One step that alone would
pass a ceiling is refused (see below), so the ceilings hold.

When the journal cannot be written
----------------------------------
The write is refused: FAIL CLOSED. A server that edits a game and quietly
stops being able to undo it has traded the user's safety for convenience
without telling them; refusing costs one clear error ("the edit was not
applied, the journal could not be written because ...") and nothing else. A
write whose bytes equal what is on disk is not journalled (there is nothing
to put back).

The folder is untrusted
-----------------------
`.rpgvxace-mcp/` sits in the project, which the threat model treats as
hostile, so a hand-edited journal must not be able to write anywhere or read
anything it should not. Every entry is validated before it is shown or acted
on: the name is matched against a strict pattern, the file name goes through
`safe_component` and must be a data file of this engine, the sizes and hashes
are bounded, the blob is a regular non-link file of exactly the size the
entry states and hashes to what it says. An entry that fails is reported by
id with its reason and, because reverting to an older point has to pass
through it, blocks any revert that would.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .paths import (
    ProjectError,
    TMP_SUFFIX,
    WORK_DIR,
    _contained,
    _is_reparse_point,
    safe_component,
)


UNDO_DIR = "undo"

# How many journal entries (one per file per write) are kept, and how many
# bytes of previous file contents. Past either, whole steps go oldest first.
MAX_UNDO_ENTRIES = 200
MAX_UNDO_BYTES = 256 * 1024 * 1024

# How many entries one `list` returns, and how many changes (steps) one
# `revert` may undo at once.
MAX_UNDO_LIST = 100
MAX_UNDO_STEPS = 50

# The largest file the journal will keep the old bytes of. Equal to the
# largest data file the server reads (`project.MAX_DATA_FILE_BYTES`; a test
# holds the two together): a file the server could not have loaded is not
# one it edits.
MAX_UNDO_BLOB_BYTES = 128 * 1024 * 1024

# Internal. A real entry is under a kilobyte; the file is hostile input.
_MAX_META_BYTES = 16 * 1024
# How many names of the journal folder are looked at. A folder stuffed with
# junk must cost a bounded scan, and says so rather than guessing.
_MAX_SCAN = 4 * MAX_UNDO_ENTRIES + 64
_MAX_SEQ = 10 ** 12 - 1
_RESERVE_TRIES = 1000
_ORPHAN_AGE_SECONDS = 3600
_TEXT_CHARS = 200


_ENTRY_NAME = re.compile(r"^e(\d{12})\.json\Z")
_BLOB_NAME = re.compile(r"^e(\d{12})\.bin\Z")
_HASH = re.compile(r"^[0-9a-f]{32}\Z")
_DROPPED = "dropped.json"


_NOT_JOURNALLED = (
    "the edit was NOT applied: the undo journal could not be written (%s). "
    "This server refuses a change it cannot take back, rather than making "
    "it and silently losing the ability to undo. Fix the folder (free disk "
    "space, make %s writable) and retry.")


def hash_hex(data: bytes) -> str:
    """The digest the journal compares by: 16 bytes of blake2b, in hex."""
    return hashlib.blake2b(data, digest_size=16).hexdigest()


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` through a unique temp file in its folder."""
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent),
                                         prefix=path.name + ".",
                                         suffix=TMP_SUFFIX)
    temp = Path(temp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            shutil.copymode(path, temp)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


class _Bad(Exception):
    """An entry that cannot be trusted, with the reason."""


class Entry:
    """One validated journal entry."""

    __slots__ = ("seq", "step", "at", "session", "tool", "summary", "file",
                 "before_present", "before_size", "before_hash",
                 "after_hash", "after_size")

    def __init__(self, **fields: Any) -> None:
        for name in self.__slots__:
            setattr(self, name, fields[name])

    @property
    def change(self) -> str:
        if not self.before_present:
            return "created"
        return "deleted" if self.after_hash is None else "modified"


class Receipt:
    """What `record` hands back so a failed write can take its entry back."""

    __slots__ = ("seq", "blob", "meta", "size")

    def __init__(self, seq: int, blob: Path, meta: Path, size: int) -> None:
        self.seq, self.blob, self.meta, self.size = seq, blob, meta, size


class Snapshot:
    """The journal as found on disk: entries, the ones refused, the totals."""

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.problems: dict[int, str] = {}
        self.stopped: str | None = None
        self.bytes = 0
        self.count = 0
        self.dropped = 0
        self.dropped_through = 0
        self.blob_sizes: dict[int, int] = {}


class UndoJournal:
    """The journal for one `Project`. See the module docstring."""

    def __init__(self, project: Any) -> None:
        self._p = project
        self._depth = 0
        self._step_id: int | None = None
        self._step_bytes = 0
        self._step_entries = 0

    # -- where ----------------------------------------------------------------
    def directory(self) -> Path:
        return self._p.work_dir() / UNDO_DIR

    def _checked_dir(self, create: bool) -> Path | None:
        """The journal folder, refusing links; None when it does not exist."""
        work = self._p.work_dir()
        folder = self.directory()
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        for part in (work, folder):
            if part.exists() and _is_reparse_point(part):
                raise ProjectError(
                    "%s is a link, not a folder this server made; the undo "
                    "journal will not read or write through it" % part)
        if not folder.is_dir():
            return None
        _contained(work, self._p.root, "the work folder %s" % WORK_DIR)
        _contained(folder, work, "the undo folder")
        return folder

    # -- steps ----------------------------------------------------------------
    @contextlib.contextmanager
    def step(self) -> Iterator[None]:
        """Entries recorded inside this block are one undoable change."""
        if self._depth == 0:
            self._step_id, self._step_bytes, self._step_entries = None, 0, 0
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._step_id = None

    # -- scanning -------------------------------------------------------------
    def _scan(self, folder: Path) -> tuple[dict[int, tuple[int, float]],
                                           dict[int, tuple[int, float]],
                                           str | None]:
        """Names in the folder, bounded: (json {seq: (size, mtime)}, bin, stopped)."""
        jsons: dict[int, tuple[int, float]] = {}
        blobs: dict[int, tuple[int, float]] = {}
        stopped = None
        with os.scandir(folder) as listing:
            for number, item in enumerate(listing):
                if number >= _MAX_SCAN:
                    stopped = ("the journal folder holds more than %d names; "
                               "only the first %d were looked at"
                               % (_MAX_SCAN, _MAX_SCAN))
                    break
                try:
                    if not item.is_file(follow_symlinks=False):
                        continue
                    info = item.stat(follow_symlinks=False)
                except OSError:
                    continue
                found = _ENTRY_NAME.match(item.name)
                if found:
                    jsons[int(found.group(1))] = (info.st_size, info.st_mtime)
                    continue
                found = _BLOB_NAME.match(item.name)
                if found:
                    blobs[int(found.group(1))] = (info.st_size, info.st_mtime)
        return jsons, blobs, stopped

    def _name(self, seq: int, kind: str) -> str:
        return "e%012d.%s" % (seq, kind)

    def _read_dropped(self, folder: Path) -> tuple[int, int]:
        path = folder / _DROPPED
        try:
            if not path.is_file() or _is_reparse_point(path) \
                    or path.stat().st_size > 1024:
                return 0, 0
            data = json.loads(path.read_text(encoding="utf-8"))
            count, through = data["entries"], data["through_seq"]
            for number in (count, through):
                if isinstance(number, bool) or not isinstance(number, int) \
                        or not 0 <= number <= _MAX_SEQ:
                    return 0, 0
            return count, through
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            return 0, 0

    # -- reading --------------------------------------------------------------
    def snapshot(self) -> Snapshot:
        """Every entry in the folder, validated; the bad ones named, not hidden."""
        result = Snapshot()
        folder = self._checked_dir(create=False)
        if folder is None:
            return result
        jsons, blobs, result.stopped = self._scan(folder)
        result.count = len(jsons)
        result.bytes = (sum(size for size, _ in jsons.values())
                        + sum(size for size, _ in blobs.values()))
        result.blob_sizes = {seq: size for seq, (size, _) in blobs.items()}
        result.dropped, result.dropped_through = self._read_dropped(folder)
        for seq in sorted(jsons):
            try:
                result.entries.append(self._parse(folder, seq, jsons[seq][0]))
            except _Bad as bad:
                result.problems[seq] = str(bad)
        return result

    def _parse(self, folder: Path, seq: int, size: int) -> Entry:
        if size > _MAX_META_BYTES:
            raise _Bad("the entry file is %d bytes; a real one is under 1 KB"
                       % size)
        path = folder / self._name(seq, "json")
        try:
            with open(path, "rb") as handle:
                raw = handle.read(_MAX_META_BYTES + 1)
            data = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, RecursionError) as exc:
            raise _Bad("it could not be read as an entry (%s)"
                       % type(exc).__name__) from exc
        if not isinstance(data, dict) or data.get("v") != 1:
            raise _Bad("it is not a version 1 journal entry")

        def whole(key: str, low: int, high: int) -> int:
            value = data.get(key)
            if isinstance(value, bool) or not isinstance(value, int) \
                    or not low <= value <= high:
                raise _Bad("%s is not a whole number from %d to %d"
                           % (key, low, high))
            return value

        def text(key: str, limit: int) -> str:
            value = data.get(key)
            if not isinstance(value, str) or len(value) > limit:
                raise _Bad("%s is not text of at most %d characters"
                           % (key, limit))
            return value

        if whole("seq", 0, _MAX_SEQ) != seq:
            raise _Bad("its sequence number is not the one in its name")
        step = whole("step", 1, seq)
        name = data.get("file")
        try:
            safe_component(name, "journalled file name")
            self._p.path_for(name)
        except ProjectError as exc:
            raise _Bad("its file name is not usable: %s" % exc) from exc
        if not name.endswith(self._p.extension) or name.endswith(TMP_SUFFIX):
            raise _Bad("its file name is not a %s data file" % self._p.extension)
        state = data.get("before")
        if state not in ("absent", "present"):
            raise _Bad("before is neither \"absent\" nor \"present\"")
        before_size = whole("before_size", 0, MAX_UNDO_BLOB_BYTES)
        before_hash = data.get("before_hash")
        if state == "absent":
            if before_size != 0 or before_hash is not None:
                raise _Bad("it says the file was absent and gives bytes for it")
        elif not isinstance(before_hash, str) or not _HASH.match(before_hash):
            raise _Bad("before_hash is not a 32-character lowercase hex hash")
        after_hash = data.get("after_hash")
        if after_hash is not None and (not isinstance(after_hash, str)
                                       or not _HASH.match(after_hash)):
            raise _Bad("after_hash is not a 32-character lowercase hex hash")
        after_size = 0 if after_hash is None \
            else whole("after_size", 0, MAX_UNDO_BLOB_BYTES)
        return Entry(
            seq=seq, step=step, at=text("at", 40), session=text("session", 64),
            tool=text("tool", 64), summary=text("summary", 4 * _TEXT_CHARS),
            file=name, before_present=state == "present",
            before_size=before_size, before_hash=before_hash,
            after_hash=after_hash, after_size=after_size)

    def load_blob(self, entry: Entry) -> bytes | None:
        """The previous bytes of an entry (None for 'was absent'), verified."""
        folder = self._checked_dir(create=False)
        if folder is None:
            raise ProjectError("the undo folder is gone")
        blob = folder / self._name(entry.seq, "bin")
        try:
            info = os.lstat(blob)
        except OSError as exc:
            raise ProjectError("entry %d: its saved bytes are missing (%s)"
                               % (entry.seq, type(exc).__name__)) from exc
        if _is_reparse_point(blob) or not os.path.isfile(blob):
            raise ProjectError("entry %d: its saved bytes are not a plain file"
                               % entry.seq)
        _contained(blob, folder, "saved bytes of entry %d" % entry.seq)
        if info.st_size != entry.before_size:
            raise ProjectError(
                "entry %d: its saved bytes are %d bytes but the entry says "
                "%d" % (entry.seq, info.st_size, entry.before_size))
        if not entry.before_present:
            return None
        with open(blob, "rb") as handle:
            data = handle.read(entry.before_size + 1)
        if len(data) != entry.before_size or hash_hex(data) != entry.before_hash:
            raise ProjectError("entry %d: its saved bytes do not match the "
                               "hash recorded for them" % entry.seq)
        return data

    # -- writing --------------------------------------------------------------
    def record(self, filename: str, new: bytes | None, tool: str,
               summary: str) -> Receipt | None:
        """Keep the previous bytes of ``filename`` before ``new`` replaces them.

        ``new`` is None for a delete. Returns None when there is nothing to
        keep (the bytes would not change). Raises `ProjectError` -- and the
        caller must then not write -- when the journal cannot be written.
        """
        path = self._p.path_for(filename)
        folder_text = str(self.directory())
        if not self._depth:
            self._step_bytes = self._step_entries = 0
        try:
            old = self._current(path)
            if old == new:
                return None
            size = len(old or b"")
            if (self._step_bytes + size > MAX_UNDO_BYTES
                    or self._step_entries + 1 > MAX_UNDO_ENTRIES):
                raise ProjectError(
                    "this one change touches more than the journal can hold "
                    "(%d entries or %d MB of previous bytes at most)"
                    % (MAX_UNDO_ENTRIES, MAX_UNDO_BYTES // (1024 * 1024)))
            receipt = self._write_entry(filename, old, new, tool, summary)
        except ProjectError as exc:
            raise ProjectError(_NOT_JOURNALLED % (exc, folder_text)) from exc
        except OSError as exc:
            raise ProjectError(_NOT_JOURNALLED % (exc, folder_text)) from exc
        self._step_bytes += receipt.size
        self._step_entries += 1
        try:
            self._trim()
        except OSError:
            pass  # the entry is safely written; a failed trim only delays one
        return receipt

    def discard(self, receipt: Receipt | None) -> None:
        """Take back an entry whose write did not happen."""
        if receipt is None:
            return
        # Best effort: this runs because something else already failed, and
        # a second failure here must not hide the first. An entry that
        # survives is harmless -- the file on disk does not match what it
        # says was written, so an undo of it is refused as a conflict.
        for part in (receipt.meta, receipt.blob):
            with contextlib.suppress(OSError):
                part.unlink(missing_ok=True)
        self._step_bytes = max(0, self._step_bytes - receipt.size)
        self._step_entries = max(0, self._step_entries - 1)

    def _current(self, path: Path) -> bytes | None:
        """The bytes now on disk, or None when there is no file."""
        if not os.path.lexists(path):
            return None
        if _is_reparse_point(path) or not path.is_file():
            raise ProjectError("%s is a link or not a plain file, so its "
                               "previous bytes are not kept" % path.name)
        if path.stat().st_size > MAX_UNDO_BLOB_BYTES:
            raise ProjectError("%s is above the %d MB the journal keeps"
                               % (path.name, MAX_UNDO_BLOB_BYTES // (1024 * 1024)))
        return path.read_bytes()

    def _write_entry(self, filename: str, old: bytes | None,
                     new: bytes | None, tool: str, summary: str) -> Receipt:
        folder = self._checked_dir(create=True)
        if folder is None:
            raise ProjectError("the undo folder could not be created")
        jsons, blobs, _ = self._scan(folder)
        seq = max([0, *jsons, *blobs]) + 1
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        for _ in range(_RESERVE_TRIES):
            if seq > _MAX_SEQ:
                raise ProjectError("the journal's sequence numbers are used up")
            blob = folder / self._name(seq, "bin")
            try:
                descriptor = os.open(blob, flags, 0o600)
                break
            except FileExistsError:
                seq += 1
        else:
            raise ProjectError("no free sequence number in the journal folder")
        meta = folder / self._name(seq, "json")
        receipt = Receipt(seq, blob, meta, len(old or b""))
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(old or b"")
                stream.flush()
                os.fsync(stream.fileno())
            if self._step_id is None and self._depth:
                self._step_id = seq
            record = {
                "v": 1, "seq": seq,
                "step": self._step_id if self._depth else seq,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "session": self._p._session,
                "tool": tool[:64], "summary": summary[:4 * _TEXT_CHARS],
                "file": filename,
                "before": "absent" if old is None else "present",
                "before_size": len(old or b""),
                "before_hash": None if old is None else hash_hex(old),
                "after_hash": None if new is None else hash_hex(new),
                "after_size": 0 if new is None else len(new),
            }
            temp = folder / (self._name(seq, "json") + ".tmp")
            with open(temp, "wb") as stream:
                stream.write(json.dumps(record).encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, meta)
        except BaseException:
            self.discard(receipt)
            (folder / (self._name(seq, "json") + ".tmp")).unlink(missing_ok=True)
            raise
        return receipt

    def _trim(self) -> None:
        folder = self._checked_dir(create=False)
        if folder is None:
            return
        jsons, blobs, _ = self._scan(folder)
        now = time.time()
        for seq, (_, mtime) in list(blobs.items()):
            if seq not in jsons and now - mtime > _ORPHAN_AGE_SECONDS:
                (folder / self._name(seq, "bin")).unlink(missing_ok=True)
                del blobs[seq]
        total = (sum(size for size, _ in jsons.values())
                 + sum(size for size, _ in blobs.values()))
        if len(jsons) <= MAX_UNDO_ENTRIES and total <= MAX_UNDO_BYTES:
            return
        # Units of whole steps, oldest first. An entry that cannot be read
        # has no step and is its own unit, so garbage cannot hold the line.
        units: dict[int, list[int]] = {}
        for seq in sorted(jsons):
            try:
                key = self._parse(folder, seq, jsons[seq][0]).step
            except _Bad:
                key = seq
            units.setdefault(key, []).append(seq)
        order = sorted(units.values(), key=min)
        dropped = through = 0
        count = len(jsons)
        for unit in order[:-1]:
            if count <= MAX_UNDO_ENTRIES and total <= MAX_UNDO_BYTES:
                break
            for seq in unit:
                (folder / self._name(seq, "json")).unlink(missing_ok=True)
                (folder / self._name(seq, "bin")).unlink(missing_ok=True)
                total -= jsons[seq][0] + blobs.get(seq, (0, 0))[0]
                count -= 1
                dropped += 1
                through = max(through, seq)
        if dropped:
            before, before_through = self._read_dropped(folder)
            note = {"entries": min(before + dropped, _MAX_SEQ),
                    "through_seq": max(through, before_through)}
            write_bytes_atomic(folder / _DROPPED,
                               json.dumps(note).encode("utf-8"))

    # -- reverting ------------------------------------------------------------
    def revert(self, *, count: int | None = None, to: int | None = None,
               force: bool = False, dry_run: bool = False) -> dict[str, Any]:
        """Put files back as they were before the newest ``count`` changes.

        ``to`` instead names an entry id: everything *after* the step that
        entry belongs to is reverted, leaving the state just after that step.
        All or nothing: every check and every saved byte is loaded before the
        first file is touched, and a write that fails part-way puts back the
        files already written.

        A file whose bytes on disk are not what the journal says were last
        written (the RPG Maker editor saved since) is a conflict: refused,
        naming the file and why, unless ``force``. The revert is itself
        journalled, so it can be reverted in turn.
        """
        if (count is None) == (to is None):
            raise ProjectError("give exactly one of count and to")
        if not dry_run:
            self._p._assert_writable("undoing changes")
        shot = self.snapshot()
        if shot.stopped:
            raise ProjectError("cannot undo: " + shot.stopped)
        if not shot.entries and shot.problems:
            first = min(shot.problems)
            raise ProjectError(
                "cannot undo: every entry in the journal is unreadable; "
                "entry %d: %s" % (first, shot.problems[first]))
        chosen = self._choose(shot, self._steps(shot), count, to)
        chosen_seqs = {entry.seq for step in chosen for entry in step}
        floor = min(chosen_seqs)
        blocked = sorted(seq for seq in shot.problems if seq > floor)
        if blocked:
            raise ProjectError(
                "cannot undo that far: entry %d (and %d other unreadable "
                "ones) cannot be trusted -- %s. Reverting past it would "
                "skip a change this journal cannot describe."
                % (blocked[0], len(blocked) - 1, shot.problems[blocked[0]]))
        by_file: dict[str, list[Entry]] = {}
        for step in chosen:
            for entry in step:
                by_file.setdefault(entry.file, []).append(entry)
        rows, conflicts, saved = [], [], {}
        loaded = 0
        for name in sorted(by_file):
            entries = sorted(by_file[name], key=lambda e: -e.seq)
            newest, oldest = entries[0], entries[-1]
            path = self._p.path_for(name)
            now = self._current(path)
            reason = self._conflict(newest, now)
            for later, earlier in zip(entries, entries[1:]):
                if later.before_hash != earlier.after_hash:
                    reason = reason or (
                        "entries %d and %d do not follow each other: the "
                        "file was changed outside this server in between"
                        % (earlier.seq, later.seq))
            if reason:
                conflicts.append({"file": name, "reason": reason})
            target = self.load_blob(oldest)
            loaded += len(target or b"")
            if loaded > MAX_UNDO_BYTES:
                raise ProjectError("that is more than %d MB of files to put "
                                   "back at once; undo fewer changes"
                                   % (MAX_UNDO_BYTES // (1024 * 1024)))
            saved[name] = (target, now)
            rows.append({
                "file": name,
                "action": ("delete" if target is None else
                           "recreate" if now is None else "restore"),
                "back_to": "the state just before entry %d" % oldest.seq,
                "from_entries": [e.seq for e in entries],
                "bytes": len(target or b""),
            })
        out: dict[str, Any] = {
            "reverts_steps": [step[0].step for step in chosen],
            "restored": rows,
        }
        if conflicts:
            out["conflicts"] = conflicts
        if dry_run:
            if conflicts and not force:
                out["would_be_refused"] = True
            return out
        if conflicts and not force:
            raise ProjectError(
                "undo refused, nothing was changed: "
                + "; ".join("%s: %s" % (c["file"], c["reason"])
                            for c in conflicts)
                + ". Pass force=true to overwrite anyway (the overwritten "
                  "bytes are journalled too).")
        summary = "undid entries %d-%d" % (floor, max(chosen_seqs))
        receipts: list[Receipt | None] = []
        done: list[str] = []
        with self.step():
            try:
                for name, (target, _) in saved.items():
                    receipts.append(self.record(name, target, "undo", summary))
                for name, (target, _) in saved.items():
                    path = self._p.path_for(name)
                    if target is None:
                        path.unlink()
                    else:
                        write_bytes_atomic(path, target)
                    done.append(name)
            except BaseException as exc:
                for name in done:
                    with contextlib.suppress(OSError):
                        path = self._p.path_for(name)
                        before = saved[name][1]
                        if before is None:
                            path.unlink(missing_ok=True)
                        else:
                            write_bytes_atomic(path, before)
                for receipt in receipts:
                    self.discard(receipt)
                if isinstance(exc, OSError):
                    raise ProjectError(
                        "undo failed part-way (%s); the files already put "
                        "back were restored, so nothing changed" % exc) from exc
                raise
        for name in saved:
            self._p._cache.pop(name, None)
            self._p._log_change("undo", name, summary)
        self._p._forget_map_ids()
        out["undo_entries"] = [r.seq for r in receipts if r is not None]
        return out

    @staticmethod
    def _conflict(newest: Entry, now: bytes | None) -> str | None:
        if newest.after_hash is None:
            if now is not None:
                return ("it exists again, but entry %d deleted it; it was "
                        "recreated since (probably by the editor)" % newest.seq)
            return None
        if now is None:
            return ("it is gone from disk, but entry %d left it there"
                    % newest.seq)
        if hash_hex(now) != newest.after_hash:
            return ("its bytes are not what entry %d wrote: it was changed "
                    "since, probably by the RPG Maker editor" % newest.seq)
        return None

    @staticmethod
    def _steps(shot: Snapshot) -> list[list[Entry]]:
        """Entries grouped by step, newest step first."""
        grouped: dict[int, list[Entry]] = {}
        for entry in shot.entries:
            grouped.setdefault(entry.step, []).append(entry)
        return [grouped[key] for key in sorted(grouped, reverse=True)]

    @staticmethod
    def _choose(shot: Snapshot, steps: list[list[Entry]],
                count: int | None, to: int | None) -> list[list[Entry]]:
        note = ("" if not shot.dropped else
                " (%d older entries were dropped to stay inside the "
                "journal's ceilings, through entry %d)"
                % (shot.dropped, shot.dropped_through))
        if not steps:
            raise ProjectError("the undo journal is empty" + note)
        if count is not None:
            if count > len(steps):
                raise ProjectError(
                    "asked to undo %d changes but the journal holds %d%s"
                    % (count, len(steps), note))
            return steps[:count]
        for position, step in enumerate(steps):
            if any(entry.seq == to for entry in step):
                if position == 0:
                    raise ProjectError("entry %d is already the newest "
                                       "change; nothing to undo" % to)
                return steps[:position]
        if to in shot.problems:
            raise ProjectError("entry %d cannot be trusted: %s"
                               % (to, shot.problems[to]))
        raise ProjectError("no entry %d in the journal%s" % (to, note))
