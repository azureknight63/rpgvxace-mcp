"""Opening, reading and safely rewriting an RPG Maker VX Ace or VX project.

Three habits keep an agent from ruining someone's game:

* **Back up before the first change to a file**, once per server run, under
  ``.rpgvxace-mcp/backups/``.
* **Write atomically** -- a temporary file plus a rename, so an interrupted
  write cannot leave a half-serialised ``.rvdata`` behind.
* **Refuse to write over a file that changed underneath us.** The editor holds
  the whole project in memory and rewrites every file when it saves, so a file
  whose timestamp moved since we read it means the editor (or another tool) is
  also working, and our copy is stale.

Which engine made the project decides the file registry, the map layer meanings
and the event command table; see :mod:`rpgvxace_mcp.schema` for the profiles.
"""

from __future__ import annotations

import configparser
import json
import os
import shutil
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import marshal_rb
from .convert import TextCodec, detect_encoding
from .rubytypes import RubyHash, RubyObject, RubyString
from .schema import (
    ARRAY,
    EngineProfile,
    HASH,
    PROFILES,
    SCRIPTS,
    SINGLE,
    VX_PROFILE,
    map_filename,
)

WORK_DIR = ".rpgvxace-mcp"
BACKUP_DIR = "backups"
CHANGE_LOG = "changes.jsonl"

# Project files that identify an engine, most specific first. XP is recognised
# so the server can say what it found instead of failing obscurely.
ENGINE_SIGNATURES = [
    ("vx_ace", "Game.rvproj2", ".rvdata2"),
    ("vx", "Game.rvproj", ".rvdata"),
    ("xp", "Game.rxproj", ".rxdata"),
]

# The engines whose data classes and command codes this server models.
MODELLED_ENGINES = ("vx_ace", "vx")

# A superset across the VX family: folders an engine does not use simply come
# back empty, and checks against an empty pool are skipped.
ASSET_DIRS = {
    "characters": "Graphics/Characters",
    "faces": "Graphics/Faces",
    "battlers": "Graphics/Battlers",
    "parallaxes": "Graphics/Parallaxes",
    "pictures": "Graphics/Pictures",
    "system": "Graphics/System",
    "animations": "Graphics/Animations",
    "tilesets": "Graphics/Tilesets",
    "battlebacks1": "Graphics/Battlebacks1",
    "battlebacks2": "Graphics/Battlebacks2",
    "battlebacks": "Graphics/Battlebacks",
    "titles1": "Graphics/Titles1",
    "titles2": "Graphics/Titles2",
    "bgm": "Audio/BGM",
    "bgs": "Audio/BGS",
    "me": "Audio/ME",
    "se": "Audio/SE",
}


# Where each engine's installer records its RTP directories. Checked under both
# hives, with and without the 32-bit redirection node.
RTP_REGISTRY_PATHS = {
    "vx_ace": r"SOFTWARE\Enterbrain\RGSS3\RTP",
    "vx": r"SOFTWARE\Enterbrain\RGSS2\RTP",
    "xp": r"SOFTWARE\Enterbrain\RGSS\RTP",
}

# Fallbacks for a machine whose registry has no entry (a copied install, or a
# non-Windows host running the server over a shared folder).
RTP_SEARCH_ROOTS = [
    r"C:\Program Files (x86)\Common Files\Enterbrain",
    r"C:\Program Files\Common Files\Enterbrain",
    r"C:\Program Files (x86)\Steam\steamapps\common",
    r"C:\Program Files\Steam\steamapps\common",
]


class ProjectError(RuntimeError):
    """Raised for problems with the project itself, not with a request."""


class ReadOnlyError(ProjectError):
    pass


class StaleFileError(ProjectError):
    pass


class CachedFile:
    __slots__ = ("value", "mtime_ns", "size", "warnings")

    def __init__(self, value: Any, mtime_ns: int, size: int, warnings: list[str]):
        self.value = value
        self.mtime_ns = mtime_ns
        self.size = size
        self.warnings = warnings


class Project:
    def __init__(self, root: str | os.PathLike[str], encoding: str = "auto",
                 read_only: bool = False, backup_keep: int = 10,
                 allow_other_engines: bool = False,
                 rtp_paths: list[str] | None = None):
        self.root = Path(root).resolve()
        self.read_only = read_only
        self.backup_keep = backup_keep
        self._cache: dict[str, CachedFile] = {}
        self._backed_up: set[str] = set()
        self._session = datetime.now().strftime("%Y%m%d-%H%M%S")

        if not self.root.is_dir():
            raise ProjectError("no such directory: %s" % self.root)
        self.data_dir = self._find_data_dir()
        self.engine, self.extension, self.engine_note = self._detect_engine()

        if self.engine in ("mv", "mz"):
            # Not a question of schema: MV and MZ store JSON, which this
            # server's Marshal codec cannot read at all.
            raise ProjectError(
                "%s This server works on the Marshal-based engines (VX Ace and "
                "VX). MV and MZ store their data as JSON, which it cannot read, "
                "so use an MV/MZ server for this project." % self.engine_note
            )
        if self.engine not in MODELLED_ENGINES and not allow_other_engines:
            raise ProjectError(
                "%s This server models RPG Maker VX Ace (.rvdata2) and RPG Maker "
                "VX (.rvdata). The Marshal codec reads XP files too, but XP's "
                "data classes and event command codes are not modelled here, so "
                "edits could produce files the editor rejects. Pass "
                "--allow-other-engines to proceed anyway." % self.engine_note
            )
        # XP, when explicitly allowed, is described with the VX profile: it is
        # the closest model available, and every report says so.
        self.profile: EngineProfile = PROFILES.get(self.engine, VX_PROFILE)
        self.codec, self.encoding_reason = self._resolve_encoding(encoding)
        self.rtp_names = self._declared_rtp_names()
        self.rtp_dirs, self.rtp_unresolved = self._resolve_rtp(rtp_paths or [])

    # -- discovery ------------------------------------------------------------
    def _find_data_dir(self) -> Path:
        for name in ("Data", "data"):
            candidate = self.root / name
            if candidate.is_dir():
                return candidate
        raise ProjectError(
            "%s has no Data directory, so it is not an RPG Maker project root. "
            "Point --project at the folder containing Game.rvproj2 (VX Ace) or "
            "Game.rvproj (VX)." % self.root
        )

    def _detect_engine(self) -> tuple[str, str, str]:
        for engine, project_file, extension in ENGINE_SIGNATURES:
            if (self.root / project_file).is_file():
                return engine, extension, "Found %s, so this is an %s project." % (
                    project_file, _engine_label(engine))
        # No project file (a distributed game, or a renamed folder): fall back to
        # whichever data extension is actually present.
        for engine, _, extension in ENGINE_SIGNATURES:
            if any(self.data_dir.glob("*" + extension)):
                return engine, extension, (
                    "No project file found; guessed %s from the %s files in Data."
                    % (_engine_label(engine), extension))
        if any(self.data_dir.glob("*.json")):
            return "mv", ".json", (
                "Data holds .json files, so this is an RPG Maker MV or MZ project.")
        raise ProjectError(
            "%s contains no recognisable RPG Maker data files (looked for "
            ".rvdata, .rvdata2, .rxdata and .json)." % self.data_dir
        )

    def _resolve_encoding(self, requested: str) -> tuple[TextCodec, str]:
        style = self.profile.string_ivars
        if requested and requested != "auto":
            return (TextCodec(requested, style),
                    "set explicitly with --encoding")
        samples: list[bytes] = []
        for key in ("system", "actors", "items", "map_infos"):
            try:
                collect_strings(self.load_data(key), samples, limit=400)
            except (ProjectError, marshal_rb.MarshalError):
                continue
            if len(samples) >= 400:
                break
        name, reason = detect_encoding(samples)
        return TextCodec(name, style), reason

    # -- run time package -----------------------------------------------------
    def _declared_rtp_names(self) -> list[str]:
        """RTP names from Game.ini.

        VX Ace writes a single ``RTP=``; XP and VX wrote ``RTP1``..``RTP3``.
        """
        path = self.root / "Game.ini"
        if not path.is_file():
            return []
        parser = configparser.ConfigParser(strict=False, interpolation=None)
        try:
            # Game.ini is Windows-encoded and occasionally malformed; neither
            # should stop the server from opening the project.
            parser.read_string(path.read_text(encoding="cp1252", errors="replace"))
        except (configparser.Error, OSError):
            return []
        names = []
        for section in parser.sections():
            for key in ("rtp", "rtp1", "rtp2", "rtp3"):
                value = parser[section].get(key, "").strip()
                if value and value not in names:
                    names.append(value)
        return names

    def _resolve_rtp(self, explicit: list[str]) -> tuple[list[Path], list[str]]:
        """Find a directory for each declared RTP name."""
        found: list[Path] = []
        for given in explicit:
            path = Path(given).expanduser()
            if path.is_dir():
                found.append(path.resolve())
            else:
                raise ProjectError("--rtp path does not exist: %s" % given)
        if not self.rtp_names:
            return found, []
        unresolved = []
        for name in self.rtp_names:
            if explicit:
                # An explicit path is taken as the answer for every declared RTP.
                continue
            located = _find_rtp_dir(name, self.engine)
            if located is None:
                unresolved.append(name)
            elif located not in found:
                found.append(located)
        return found, unresolved

    @property
    def assets_complete(self) -> bool:
        """Whether every asset this project can reference is visible to us.

        False when Game.ini declares an RTP we could not locate: a file missing
        from the project folder may simply live there.
        """
        return not self.rtp_unresolved

    # -- paths ----------------------------------------------------------------
    @property
    def data_files(self):
        """The tables this engine has, keyed by short name."""
        return self.profile.data_files

    def filename_for(self, key: str) -> str:
        try:
            entry = self.profile.data_file(key)
        except KeyError as exc:
            raise ProjectError(exc.args[0]) from exc
        return entry.filename(self.extension)

    def path_for(self, filename: str) -> Path:
        return self.data_dir / filename

    def map_file(self, map_id: int) -> str:
        return map_filename(map_id, self.extension)

    def work_dir(self) -> Path:
        return self.root / WORK_DIR

    # -- loading --------------------------------------------------------------
    def load_file(self, filename: str, refresh: bool = False) -> Any:
        cached = self._cache.get(filename)
        if cached is not None and not refresh:
            return cached.value
        path = self.path_for(filename)
        if not path.is_file():
            raise ProjectError("%s does not exist in %s" % (filename, self.data_dir))
        stat = path.stat()
        raw = path.read_bytes()
        try:
            value, warnings = marshal_rb.load(raw, with_warnings=True)
        except marshal_rb.MarshalError as exc:
            raise ProjectError("%s is not valid Marshal data: %s" % (filename, exc)) from exc
        self._cache[filename] = CachedFile(value, stat.st_mtime_ns, stat.st_size, warnings)
        return value

    def load_data(self, key: str, refresh: bool = False) -> Any:
        return self.load_file(self.filename_for(key), refresh)

    def load_map(self, map_id: int, refresh: bool = False) -> RubyObject:
        value = self.load_file(self.map_file(map_id), refresh)
        if not isinstance(value, RubyObject):
            raise ProjectError("Map %03d does not contain an RPG::Map object" % map_id)
        return value

    def file_warnings(self, filename: str) -> list[str]:
        cached = self._cache.get(filename)
        return list(cached.warnings) if cached else []

    def exists(self, filename: str) -> bool:
        return self.path_for(filename).is_file()

    def map_ids_on_disk(self) -> list[int]:
        ids = []
        for path in self.data_dir.glob("Map*" + self.extension):
            stem = path.stem
            digits = stem[3:]
            if digits.isdigit():
                ids.append(int(digits))
        return sorted(ids)

    # -- containers -----------------------------------------------------------
    def container_kind(self, key: str, value: Any) -> str:
        """What shape a database table actually has on disk.

        The registry records what to expect, but the file decides: a project
        edited by other tools can hand back an Array where a Hash was expected.
        """
        if isinstance(value, RubyHash):
            return HASH
        if isinstance(value, list):
            return ARRAY
        declared = self.profile.data_file(key).container
        return declared if declared in (SINGLE, SCRIPTS) else SINGLE

    def entries(self, key: str, refresh: bool = False) -> list[tuple[int, Any]]:
        """``(id, entry)`` pairs for a database table, skipping empty slots.

        RPG Maker keeps id-indexed arrays whose slot 0 is nil so that ids line
        up with positions; those nil slots are not entries.
        """
        value = self.load_data(key, refresh)
        kind = self.container_kind(key, value)
        if kind == ARRAY:
            return [(i, item) for i, item in enumerate(value) if item is not None]
        if kind == HASH:
            return [(k, v) for k, v in value.sorted_items() if v is not None]
        return [(0, value)]

    def get_entry(self, key: str, entry_id: int, refresh: bool = False) -> Any:
        value = self.load_data(key, refresh)
        kind = self.container_kind(key, value)
        if kind == ARRAY:
            if 0 <= entry_id < len(value):
                return value[entry_id]
            return None
        if kind == HASH:
            return value.get(entry_id)
        return value

    def set_entry(self, key: str, entry_id: int, entry: Any) -> None:
        value = self.load_data(key)
        kind = self.container_kind(key, value)
        if kind == ARRAY:
            while len(value) <= entry_id:
                value.append(None)
            value[entry_id] = entry
        elif kind == HASH:
            value.set(entry_id, entry)
        else:
            raise ProjectError("%s holds a single object and has no entries" % key)

    # -- scripts --------------------------------------------------------------
    def load_scripts(self) -> list[dict[str, Any]]:
        raw = self.load_data("scripts")
        if not isinstance(raw, list):
            raise ProjectError("Scripts data is not an Array")
        scripts = []
        for index, row in enumerate(raw):
            if not isinstance(row, list) or len(row) < 3:
                scripts.append({"index": index, "error": "unexpected row shape"})
                continue
            magic, name, packed = row[0], row[1], row[2]
            entry: dict[str, Any] = {
                "index": index,
                "magic": magic,
                "name": self.codec.decode(name.data) if isinstance(name, RubyString) else "",
            }
            try:
                entry["code"] = zlib.decompress(packed.data).decode(
                    self.codec.name, "surrogateescape")
            except (zlib.error, AttributeError) as exc:
                entry["error"] = "could not decompress: %s" % exc
            scripts.append(entry)
        return scripts

    def set_script(self, index: int, name: str | None = None, code: str | None = None) -> None:
        raw = self.load_data("scripts")
        if not 0 <= index < len(raw):
            raise ProjectError("script index %d is outside 0..%d" % (index, len(raw) - 1))
        row = raw[index]
        if name is not None:
            row[1] = RubyString(self.codec.encode(name))
        if code is not None:
            row[2] = RubyString(zlib.compress(self.codec.encode(code)))

    # -- writing --------------------------------------------------------------
    def save_file(self, filename: str, dry_run: bool = False,
                  tool: str = "", summary: str = "") -> dict[str, Any]:
        if self.read_only and not dry_run:
            raise ReadOnlyError(
                "this server is running read-only, so %s was not written. Restart "
                "without --read-only to allow changes." % filename
            )
        cached = self._cache.get(filename)
        if cached is None:
            raise ProjectError("%s was never loaded, so there is nothing to save" % filename)

        encoded = marshal_rb.dump(cached.value)
        path = self.path_for(filename)
        result: dict[str, Any] = {
            "file": filename,
            "bytes": len(encoded),
            "dry_run": dry_run,
        }
        if dry_run:
            result["would_write"] = str(path)
            return result

        self._assert_not_stale(filename, path, cached)
        backup = self.backup_file(filename)
        if backup:
            result["backup"] = backup

        tmp = path.with_name(path.name + ".rpgvxace-tmp")
        tmp.write_bytes(encoded)
        os.replace(tmp, path)

        stat = path.stat()
        cached.mtime_ns, cached.size = stat.st_mtime_ns, stat.st_size
        self._log_change(tool, filename, summary)
        result["written"] = str(path)
        return result

    def _assert_not_stale(self, filename: str, path: Path, cached: CachedFile) -> None:
        if not path.exists():
            return  # a new file; nothing to clobber
        stat = path.stat()
        if stat.st_mtime_ns == cached.mtime_ns and stat.st_size == cached.size:
            return
        raise StaleFileError(
            "%s changed on disk after this server read it (size %d -> %d). The %s "
            "editor rewrites every data file when it saves, so it is probably "
            "open -- close it, then re-read the file (any tool with refresh, or "
            "project_info) before writing again. Your edit was not applied."
            % (filename, cached.size, stat.st_size, self.profile.label)
        )

    def create_file(self, filename: str, value: Any) -> None:
        """Register a brand new data file so :meth:`save_file` can write it."""
        if self.path_for(filename).exists():
            raise ProjectError("%s already exists" % filename)
        self._cache[filename] = CachedFile(value, 0, 0, [])
        self._backed_up.add(filename)  # nothing to back up

    def delete_file(self, filename: str, dry_run: bool = False) -> dict[str, Any]:
        if self.read_only and not dry_run:
            raise ReadOnlyError("read-only mode: %s was not deleted" % filename)
        path = self.path_for(filename)
        if not path.is_file():
            raise ProjectError("%s does not exist" % filename)
        if dry_run:
            return {"file": filename, "would_delete": str(path), "dry_run": True}
        backup = self.backup_file(filename)
        path.unlink()
        self._cache.pop(filename, None)
        return {"file": filename, "deleted": str(path), "backup": backup}

    # -- backups --------------------------------------------------------------
    def backup_root(self) -> Path:
        return self.work_dir() / BACKUP_DIR

    def session_backup_dir(self) -> Path:
        return self.backup_root() / self._session

    def backup_file(self, filename: str) -> str | None:
        """Copy a file aside the first time this run touches it."""
        if filename in self._backed_up:
            return None
        source = self.path_for(filename)
        if not source.is_file():
            self._backed_up.add(filename)
            return None
        target_dir = self.session_backup_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        shutil.copy2(source, target)
        self._backed_up.add(filename)
        self.prune_backups()
        return str(target)

    def snapshot(self, label: str = "manual") -> dict[str, Any]:
        """Copy every data file into a fresh backup directory."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + label
        target = self.backup_root() / stamp
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for path in sorted(self.data_dir.glob("*" + self.extension)):
            shutil.copy2(path, target / path.name)
            copied.append(path.name)
        self.prune_backups()
        return {"backup": stamp, "path": str(target), "files": copied}

    def list_backups(self) -> list[dict[str, Any]]:
        root = self.backup_root()
        if not root.is_dir():
            return []
        out = []
        for entry in sorted(root.iterdir(), reverse=True):
            if entry.is_dir():
                files = sorted(p.name for p in entry.iterdir() if p.is_file())
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
        if self.read_only and not dry_run:
            raise ReadOnlyError("read-only mode: nothing was restored")
        source_dir = self.backup_root() / stamp
        if not source_dir.is_dir():
            available = [b["backup"] for b in self.list_backups()]
            raise ProjectError(
                "no backup named %r; available: %s"
                % (stamp, ", ".join(available) if available else "none")
            )
        available_files = sorted(p.name for p in source_dir.iterdir() if p.is_file())
        wanted = files or available_files
        missing = [f for f in wanted if f not in available_files]
        if missing:
            raise ProjectError(
                "backup %s does not contain %s (it holds: %s)"
                % (stamp, ", ".join(missing), ", ".join(available_files))
            )
        if dry_run:
            return {"backup": stamp, "would_restore": wanted, "dry_run": True}
        # Snapshot what is about to be overwritten, so a mistaken restore is
        # itself reversible.
        pre = self.snapshot("pre-restore")
        for name in wanted:
            shutil.copy2(source_dir / name, self.path_for(name))
            self._cache.pop(name, None)
        self._log_change("backups", ",".join(wanted), "restored from %s" % stamp)
        return {"backup": stamp, "restored": wanted, "safety_backup": pre["backup"]}

    def prune_backups(self) -> list[str]:
        root = self.backup_root()
        if not root.is_dir() or self.backup_keep <= 0:
            return []
        dirs = sorted((p for p in root.iterdir() if p.is_dir()), reverse=True)
        removed = []
        for stale in dirs[self.backup_keep:]:
            if stale.name == self._session:
                continue
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
        with open(path, encoding="utf-8") as handle:
            rows = [line for line in handle if line.strip()]
        out = []
        for line in rows[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # -- assets ---------------------------------------------------------------
    def asset_names(self, kind: str) -> set[str]:
        """Base names (no extension) of one asset kind, project plus RTP.

        Extensions are dropped because the data files store names without them,
        and the engine accepts several formats per kind.
        """
        relative = ASSET_DIRS.get(kind)
        if relative is None:
            raise ProjectError("unknown asset kind %r" % kind)
        names: set[str] = set()
        for base in [self.root, *self.rtp_dirs]:
            directory = base / relative
            if directory.is_dir():
                names |= {p.stem for p in directory.iterdir() if p.is_file()}
        return names

    def describe(self) -> dict[str, Any]:
        files = []
        for key, entry in self.profile.data_files.items():
            filename = self.filename_for(key)
            path = self.path_for(filename)
            files.append({
                "table": key,
                "file": filename,
                "class": entry.cls,
                "description": entry.description,
                "present": path.is_file(),
                "bytes": path.stat().st_size if path.is_file() else 0,
            })
        return {
            "root": str(self.root),
            "engine": self.engine,
            "engine_label": _engine_label(self.engine),
            "modelled_as": self.profile.label,
            "engine_notes": self.profile.notes,
            "engine_detection": self.engine_note,
            "data_dir": str(self.data_dir),
            "extension": self.extension,
            "encoding": self.codec.name,
            "encoding_detection": self.encoding_reason,
            "read_only": self.read_only,
            "rtp": {
                "declared": self.rtp_names,
                "resolved": [str(p) for p in self.rtp_dirs],
                "unresolved": self.rtp_unresolved,
                "note": ("Game.ini declares no RTP, so every asset should be "
                         "inside the project." if not self.rtp_names else
                         "All declared RTPs were located, so asset checks see "
                         "every file this project can reference."
                         if self.assets_complete else
                         "Could not locate %s. Assets that live there will look "
                         "missing; pass --rtp with the folder to fix that."
                         % ", ".join(self.rtp_unresolved)),
            },
            "backup_dir": str(self.backup_root()),
            "backups_kept": self.backup_keep,
            "session": self._session,
            "data_files": files,
            "map_files": len(self.map_ids_on_disk()),
        }


def _find_rtp_dir(name: str, engine: str) -> Path | None:
    """Locate an RTP by the name Game.ini uses for it."""
    registry_path = RTP_REGISTRY_PATHS.get(engine)
    if registry_path:
        for candidate in _registry_rtp_values(registry_path):
            value_name, value = candidate
            if value_name.lower() == name.lower() and Path(value).is_dir():
                return Path(value).resolve()
    for root in RTP_SEARCH_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        for child in (base / name, base / name / "rtp", base / "RGSS3" / "Standard"):
            if child.is_dir():
                return child.resolve()
    return None


def _registry_rtp_values(registry_path: str) -> list[tuple[str, str]]:
    """Every (name, path) pair under one RTP registry key, or nothing."""
    try:
        import winreg
    except ImportError:
        return []  # not Windows; --rtp is the way in
    out: list[tuple[str, str]] = []
    variants = [registry_path,
                registry_path.replace("SOFTWARE\\", "SOFTWARE\\WOW6432Node\\", 1)]
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for path in variants:
            try:
                with winreg.OpenKey(hive, path) as key:
                    index = 0
                    while True:
                        try:
                            value_name, value, _ = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        if isinstance(value, str):
                            out.append((value_name, value))
                        index += 1
            except OSError:
                continue
    return out


def _engine_label(engine: str) -> str:
    return {
        "vx_ace": "RPG Maker VX Ace",
        "vx": "RPG Maker VX",
        "xp": "RPG Maker XP",
        "mv": "RPG Maker MV/MZ",
    }.get(engine, engine)


def collect_strings(value: Any, out: list[bytes], limit: int = 200, depth: int = 0) -> None:
    """Gather string samples for encoding detection."""
    if len(out) >= limit or depth > 12:
        return
    if isinstance(value, RubyString):
        if value.data:
            out.append(value.data)
    elif isinstance(value, RubyObject):
        for item in value.ivars.values():
            collect_strings(item, out, limit, depth + 1)
    elif isinstance(value, list):
        for item in value:
            collect_strings(item, out, limit, depth + 1)
    elif isinstance(value, RubyHash):
        for _, item in value.pairs:
            collect_strings(item, out, limit, depth + 1)


def now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
