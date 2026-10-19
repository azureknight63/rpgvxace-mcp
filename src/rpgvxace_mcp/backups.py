"""Everything under `.rpgvxace-mcp/`: the snapshots and the change log.

`Project`'s own methods, in their own file. A quarter of that class was the
backup store, which is read for its own reasons -- what a restore does
before it copies, which snapshots a prune spares -- and had to be scrolled
past to reach the loading and saving it sits between.

A mixin rather than a collaborator: this is one class in two files, and
`TestTheBackupMixinContractHolds` is where that trade is argued and
checked. What this half needs from the other is `NEEDS_FROM_PROJECT`
below.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import (
    BACKUP_DIR,
    BACKUP_STAMP_FORMAT,
    CHANGE_LOG,
    ProjectError,
    _BACKUP_STAMP,
    _contained,
    _is_reparse_point,
    safe_component,
)


# How long one line of the change log may be. This server writes that file
# itself and a record it writes is a few hundred bytes -- but the file sits
# in a folder the threat model treats as untrusted, so the bound is a fact
# about where it lives rather than about who wrote it.
MAX_CHANGE_LINE_BYTES = 64 * 1024


class BackupStore:
    """The backup store and the change log, mixed into `Project`.

    `NEEDS_FROM_PROJECT` is what this half needs from the other, and it is
    the only statement of it -- a prose copy alongside would be a second
    list, checked by nothing, of the kind this class exists to argue
    against. The tuple is checked three ways, including that nothing in it
    is unread: that is what rejected `root` and `read_only`, which are
    reached only through `work_dir()` and `_assert_writable()`, so listing
    them described those methods' business as this class's.
    """

    # Each is set or defined by `Project`; declared here so a reader of this
    # file knows what it may use, and so the contract test has a list. Both
    # directions are checked, because a list that reads as checked and is
    # not is worse than no list.
    NEEDS_FROM_PROJECT = (
        "data_dir", "extension", "backup_keep",
        "_session", "_cache", "_backed_up", "_forget_map_ids",
        "work_dir", "path_for", "_assert_writable", "_assert_not_stale",
        "journal",
    )

    # -- backups --------------------------------------------------------------
    def backup_root(self) -> Path:
        return self.work_dir() / BACKUP_DIR

    def session_backup_dir(self) -> Path:
        return self.backup_root() / self._session

    def _backup_dir(self, stamp: str) -> Path:
        """The directory of one named snapshot, and only inside the backup root.

        ``stamp`` comes from a tool argument, so it is validated as a single
        component and the result is checked to be under the backup root. Without
        both, an absolute path replaces the join entirely and the restore reads
        and writes wherever it is pointed.
        """
        safe_component(stamp, "backup name")
        root = self.backup_root()
        directory = root / stamp
        _contained(directory, root, "backup %r" % stamp)
        return directory

    @staticmethod
    def _backup_contents(directory: Path) -> list[str]:
        return sorted(p.name for p in directory.iterdir() if p.is_file())

    def backup_file(self, filename: str) -> str | None:
        """Copy a file aside the first time this run touches it.

        Old snapshots are pruned when this run's snapshot directory is first
        created, so pruning happens once per run rather than per file.
        """
        if filename in self._backed_up:
            return None
        source = self.path_for(filename)
        if not source.is_file():
            self._backed_up.add(filename)
            return None
        target_dir = self.session_backup_dir()
        first = not target_dir.exists()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_component(filename, "data file name")
        _contained(target, target_dir, "backup of %r" % filename)
        shutil.copy2(source, target)
        self._backed_up.add(filename)
        if first:
            self.prune_backups()
        return str(target)

    def snapshot(self, label: str = "manual", keep: set[str] | None = None,
                 dry_run: bool = False) -> dict[str, Any]:
        """Copy every data file into a fresh backup directory.

        Prunes old snapshots afterwards, sparing anything named in ``keep``.

        ``dry_run`` reports what would be copied without writing anything.
        It walks the same files with the same skips rather than leaving the
        caller to glob for itself: the `backups` tool did that, so its preview
        listed the symlinked data files this refuses to copy -- a preview that
        does not match the act is worse than no preview.
        """
        if not dry_run:
            # Gated like every sibling preview: save_file, delete_file,
            # restore_backup and prune_backups all check this only when they
            # are about to write. A read-only server used to refuse a snapshot
            # *preview*, which writes nothing, so a model exploring one was
            # told a write had failed.
            self._assert_writable("taking a snapshot")
        clean = re.sub(r"[^A-Za-z0-9._-]", "-", label) or "manual"
        stamp = datetime.now().strftime(BACKUP_STAMP_FORMAT) + "-" + clean
        # The stamp has one-second resolution, and a snapshot copies over a
        # directory that already exists: two safety snapshots in one second
        # were one, holding the later data, and the original was gone.
        base, number = stamp, 1
        while self._backup_dir(stamp).exists():
            number += 1
            stamp = "%s-%d" % (base, number)
        target = self._backup_dir(stamp)
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)
        copied = []
        for path in sorted(self.data_dir.glob("*" + self.extension)):
            if _is_reparse_point(path):
                # Copying one would put whatever it points at into the backup
                # tree, where the read tools would serve it as project data.
                # Through `_is_reparse_point`, whose own docstring calls
                # `is_symlink()` insufficient on Windows: this was the one
                # filter left asking the narrower question.
                continue
            name = safe_component(path.name, "data file name")
            _contained(target / name, target, "backup of %r" % name)
            if not dry_run:
                shutil.copy2(path, target / name)
            copied.append(name)
        # The prune is part of the act, so the preview reports it too: it
        # was left out of both, and `backups op="create"` quietly removed
        # the oldest snapshot while the sibling op="prune" reported every
        # removal.
        #
        # One key set on both paths. The dry run used to answer
        # `would_snapshot`, `label` and `would_prune` where the act answers
        # `backup`, `path`, `files` and `pruned` -- two different replies to
        # one question, so a caller could not compare them, and the preview
        # led with a past-tense-free key rather than with the fact that
        # nothing had happened. Naming a *fact* here and letting the tools
        # layer phrase it keeps `write_reply` the one place that decides how
        # a preview reads, and keeps this module below that layer.
        return {"backup": stamp, "path": str(target), "label": clean,
                "files": copied,
                "pruned": self.prune_backups(keep, dry_run=dry_run)}

    def list_backups(self) -> list[dict[str, Any]]:
        root = self.backup_root()
        if not root.is_dir():
            return []
        out = []
        for entry in sorted(root.iterdir(), reverse=True):
            if entry.is_dir():
                files = self._backup_contents(entry)
                out.append({
                    "backup": entry.name,
                    "created": datetime.fromtimestamp(
                        entry.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                    "files": files,
                    "is_current_session": entry.name == self._session,
                })
        return out

    def restore_backup(self, stamp: str, files: list[str] | None = None,
                       dry_run: bool = False) -> dict[str, Any]:
        # Validated before anything else, including the read-only gate: an
        # unchecked name here both reads and writes outside the project, and
        # checking it first means the caller is told the real reason and the
        # containment guard is exercised in both modes.
        source_dir = self._backup_dir(stamp)
        if not dry_run:
            self._assert_writable("restoring %s" % stamp)
        if not source_dir.is_dir():
            available = [b["backup"] for b in self.list_backups()]
            raise ProjectError(
                "no backup named %r; available: %s"
                % (stamp, ", ".join(available) if available else "none")
            )
        available_files = self._backup_contents(source_dir)
        # Validated as a whole before anything is copied: a rejection partway
        # through the loop would leave Data/ half restored.
        # Both checks in the same pass, before the safety snapshot: leaving
        # the containment check in the copy loop meant a symlink planted in a
        # backup aborted partway, with Data/ half old and half new.
        wanted = []
        if files is not None and not files:
            # `[]` used to mean "every file": the one call that overwrites
            # the whole Data folder, from the argument a caller uses to say
            # "only these". The tool refuses it too; the store must not rely
            # on that.
            raise ProjectError("files is empty; name the files to restore, "
                               "or leave files out to restore the whole "
                               "snapshot")
        for name in (available_files if files is None else files):
            safe_component(name, "file name")
            # Both ends of the copy, and both with the boundary the copy
            # loop uses -- the snapshot, not the backup root.
            #
            # With the root here, a symlink resolving into a *sibling*
            # snapshot passed this pass and was caught only in the copy
            # loop. And the destination was not checked here at all, so a
            # `Data/Map002.rvdata2` that is itself a symlink out of the
            # project -- the case `snapshot`'s own reparse-point filter
            # exists for -- passed the pass, was skipped by the safety
            # snapshot for the same reason, and then raised from the copy
            # loop with Map001 already restored. Half old, half new, and
            # the one file that aborted was the one not backed up.
            _contained(source_dir / name, source_dir,
                       "backup file %r" % name)
            self.path_for(name)
            wanted.append(name)
        missing = [f for f in wanted if f not in available_files]
        if missing:
            raise ProjectError(
                "backup %s does not contain %s (it holds: %s)"
                % (stamp, ", ".join(missing), ", ".join(available_files))
            )
        if dry_run:
            # The facts, in the same key set as the act below and with no
            # phrasing of its own: `write_reply` in the tools layer renames
            # `restored` and puts `dry_run` first. This branch used to say
            # `would_restore` here and lead with `backup`, so the one thing a
            # caller most needs to see -- that nothing happened -- came third.
            # `safety_backup` is absent because a dry run makes none, which is
            # a real difference and not a phrasing one.
            return {"backup": stamp, "restored": wanted}
        # Snapshot what is about to be overwritten, so a mistaken restore is
        # itself reversible. `keep` spares the snapshot being restored from:
        # pruning runs inside this call, and would otherwise be free to delete
        # the very directory about to be copied out of.
        pre = self.snapshot("pre-restore", keep={stamp})
        # Staleness-checked before anything is copied, so a restore that would
        # overwrite an editor's unsaved work refuses with none of it done. This
        # was the one write path exempt from the module's own stated habit --
        # "refuse to write over a file that changed underneath us" -- and the
        # safety snapshot above made it recoverable rather than harmless.
        for name in wanted:
            cached = self._cache.get(name)
            if cached is not None:
                self._assert_not_stale(name, self.path_for(name), cached)
        # One undoable change, however many files: the journal keeps what
        # each file held just before the copy, so a restore can be undone
        # even after `prune` has removed the snapshot it came from.
        with self.journal.step():
            for name in wanted:
                # The snapshot being restored from, not the backup root: with
                # the root as the boundary a symlink planted in one snapshot
                # could resolve into another and be restored from there.
                source = _contained(source_dir / name, source_dir,
                                    "backup file %r" % name)
                receipt = self.journal.record(
                    name, source.read_bytes(), "backups",
                    "restored from %s" % stamp)
                try:
                    shutil.copy2(source, self.path_for(name))
                except OSError:
                    self.journal.discard(receipt)
                    raise
                self._cache.pop(name, None)
                # With the cache entry gone, the per-run backup flag has to
                # go too, or the next save of a restored file skips its own
                # backup.
                self._backed_up.discard(name)
        self._forget_map_ids()
        self._log_change("backups", ",".join(wanted), "restored from %s" % stamp)
        return {"backup": stamp, "restored": wanted, "safety_backup": pre["backup"]}

    def prune_backups(self, keep: set[str] | None = None,
                      dry_run: bool = False) -> list[str]:
        """Delete all but the newest snapshots, sparing ``keep``.

        ``dry_run`` returns the names that would go without removing them, so
        a caller previewing a prune does not have to reimplement which
        directories are candidates.
        """
        if not dry_run:
            self._assert_writable("pruning backups")
        root = self.backup_root()
        if not root.is_dir() or self.backup_keep <= 0:
            return []
        spared = {self._session} | (keep or set())
        # Only this server's own snapshots are candidates, and only by their
        # stamped names: a directory someone else put here is not ours to
        # delete, and a name that is not a timestamp would sort unpredictably
        # against ones that are.
        dirs = sorted((p for p in root.iterdir()
                       if p.is_dir() and _BACKUP_STAMP.match(p.name)),
                      reverse=True)
        removed = []
        for stale in dirs[self.backup_keep:]:
            if stale.name in spared or _is_reparse_point(stale):
                continue
            _contained(stale, root, "backup %r" % stale.name)
            if not dry_run:
                shutil.rmtree(stale, ignore_errors=True)
            removed.append(stale.name)
        return removed

    # -- change log -----------------------------------------------------------
    def _log_change(self, tool: str, filename: str, summary: str) -> None:
        try:
            self.work_dir().mkdir(parents=True, exist_ok=True)
            record = {
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "session": self._session,
                "tool": tool,
                "file": filename,
                "summary": summary,
            }
            with open(self.work_dir() / CHANGE_LOG, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record) + "\n")
        except OSError:
            pass  # a project on read-only media must not break editing in memory

    def recent_changes(self, limit: int = 20) -> list[dict[str, Any]]:
        path = self.work_dir() / CHANGE_LOG
        if not path.is_file():
            return []
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                # A deque keeps only the tail: the log grows for the life of
                # the project and there is no reason to hold all of it.
                rows: "deque[str]" = deque(maxlen=max(limit, 0))
                # Bounded per line as well. `for line in handle` reads to
                # the next newline however far away it is, and this file
                # lives in the project folder -- which the threat model
                # treats as untrusted, and which is routinely the process
                # CWD -- so one line with no newline in it is an unbounded
                # read into memory before `maxlen` can drop anything. The
                # same bound `MAX_GAME_INI_BYTES` and `server._read_line`
                # were written for; this was the reader still without it.
                while True:
                    line = handle.readline(MAX_CHANGE_LINE_BYTES + 1)
                    if not line:
                        break
                    if len(line) > MAX_CHANGE_LINE_BYTES:
                        # Dropped, not truncated: half a JSON record is not
                        # a record. The rest of it is read past one bounded
                        # chunk at a time, so the next line starts at a
                        # record boundary.
                        while line and not line.endswith("\n"):
                            line = handle.readline(
                                MAX_CHANGE_LINE_BYTES + 1)
                        continue
                    if line.strip():
                        rows.append(line)
        except OSError:
            return []
        out = []
        for line in rows:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, RecursionError):
                # A line of 30,000 brackets is under the line bound and
                # overflows the parser's stack.
                continue
            if isinstance(record, dict):
                out.append(record)
        return out
