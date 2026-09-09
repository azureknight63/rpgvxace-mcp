"""Everything under `.rpgvxace-mcp/`: the snapshots and the change log.

`Project`'s own methods, in their own file. A quarter of that class was the
backup store, which is read for its own reasons -- what a restore does
before it copies, which snapshots a prune spares -- and had to be scrolled
past to reach the loading and saving it sits between.

A mixin rather than a collaborator, and deliberately: `restore_backup`
reads the file cache, the per-run backup flags, the map-id cache and the
staleness check. An object holding a reference to `Project` in order to
reach six of its private attributes would be the same coupling with a
longer name. This is one class in two files, and what it needs from the
other file is written down below and checked by
`TestTheBackupMixinContractHolds`.
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


class BackupStore:
    """The backup store and the change log, mixed into `Project`.

    What this needs from the class it is mixed into. Every name is checked
    against `Project` by a test, so this list cannot go stale silently:

        data_dir, extension     which files a snapshot copies
        backup_keep             how many snapshots a prune leaves
        _session                this run's snapshot name
        _cache, _backed_up      what has been read and backed up
        _map_ids, _map_ids_set  invalidated by a restore
        work_dir(), path_for()  where a file goes
        _assert_writable()      the read-only gate
        _assert_not_stale()     the editor-changed-it gate

    Not `root` or `read_only`, which is the sort of thing the test caught:
    they are reached only through `work_dir()` and `_assert_writable()`, so
    listing them describes those two methods' business as this class's.
    """

    # Each is set or defined by `Project`; declared here so a reader of this
    # file knows what it may use, and so the contract test has a list. Both
    # directions are checked, because a list that reads as checked and is
    # not is worse than no list.
    NEEDS_FROM_PROJECT = (
        "data_dir", "extension", "backup_keep",
        "_session", "_cache", "_backed_up", "_map_ids",
        "_map_ids_set",
        "work_dir", "path_for", "_assert_writable", "_assert_not_stale",
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
        # The prune is part of the act, so the preview reports it too. It was
        # left out of both, so `backups op="create"` quietly removed the
        # oldest snapshot while the sibling op="prune" reports every removal.
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
        for name in (files or available_files):
            safe_component(name, "file name")
            _contained(source_dir / name, self.backup_root(),
                       "backup file %r" % name)
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
        for name in wanted:
            # The snapshot being restored from, not the backup root: with the
            # root as the boundary a symlink planted in one snapshot could
            # resolve into another and be restored from there.
            source = _contained(source_dir / name, source_dir,
                                "backup file %r" % name)
            shutil.copy2(source, self.path_for(name))
            self._cache.pop(name, None)
            # With the cache entry gone, the per-run backup flag has to go too,
            # or the next save of a restored file skips its own backup.
            self._backed_up.discard(name)
        self._map_ids = None
        self._map_ids_set = frozenset()
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
                rows = deque((line for line in handle if line.strip()),
                             maxlen=limit)
        except OSError:
            return []
        out = []
        for line in rows:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
