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
import functools
import hashlib
import json
import os
import re
import shutil
import tempfile
import zlib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import marshal_rb
from .convert import TextCodec, detect_encoding
from .rubytypes import RubyHash, RubyObject, RubyString
from .schema import (
    ARRAY,
    DataFile,
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
BACKUP_STAMP_FORMAT = "%Y%m%d-%H%M%S"
TMP_SUFFIX = ".rpgvxace-mcp.tmp"
DEFAULT_BACKUP_KEEP = 10

# How much of a project is sampled to guess its text encoding.
ENCODING_SAMPLE_TABLES = ("system", "actors", "items", "map_infos")
ENCODING_SAMPLE_LIMIT = 400
MAX_STRING_SCAN_DEPTH = 12

# How many values the encoding sampler will look at. It runs during
# construction, so it has to finish on any file at all.
MAX_STRING_SCAN_NODES = 200_000

# The highest map id the editor's Map%03d naming can express.
MAX_MAP_ID = 9999

# A script slot inflates into memory, so a crafted Scripts file must not be
# able to ask for an unbounded amount of it.
MAX_SCRIPT_BYTES = 16 * 1024 * 1024

# A single path component that cannot lead anywhere but where it is joined.
# Everything this server opens is named either by its own registry or by a
# caller, and a caller's string reaches the filesystem, so it is checked here.
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")

# Names Windows resolves to devices rather than files, whatever the extension.
# safe_component is meant to be the one place a caller's name is judged, so it
# refuses these rather than leaving each call site to prove it cannot reach one.
_RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"]
    + ["com%d" % n for n in range(1, 10)]
    + ["lpt%d" % n for n in range(1, 10)]
)

# A snapshot directory this server made: the stamp, and an optional label.
_BACKUP_STAMP = re.compile(r"^\d{8}-\d{6}(-[A-Za-z0-9._-]+)?$")

# Project files that identify an engine, most specific first. The modelled
# engines come from their own profiles so this cannot drift; XP has no profile,
# and MV/MZ are recognised only to say what they are.
ENGINE_SIGNATURES = [
    (profile.key, profile.project_file, profile.extension)
    for profile in PROFILES.values()
] + [("xp", "Game.rxproj", ".rxdata")]

# The engines whose data classes and command codes this server models.
MODELLED_ENGINES = tuple(PROFILES)

# Engines that store JSON rather than Marshal, which this server cannot read.
JSON_ENGINES = ("mv",)

# Which generation of RGSS each engine ships, for finding its RTP.
RGSS_GENERATION = {"vx_ace": "RGSS3", "vx": "RGSS2", "xp": "RGSS"}

# A superset across the VX family. A folder an engine does not use simply comes
# back empty, and checks against an empty pool are skipped -- Battlebacks1/2 are
# Ace, the single Battlebacks folder is VX.
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
    engine: r"SOFTWARE\Enterbrain\%s\RTP" % generation
    for engine, generation in RGSS_GENERATION.items()
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
    """Raised when something would write while --read-only is in force."""


class StaleFileError(ProjectError):
    """Raised when a file changed on disk after this server read it."""


def safe_component(name: Any, what: str) -> str:
    """One path component, with no way out of the directory it is joined to.

    Tool arguments reach the filesystem, and this server feeds the model text
    out of the project itself -- map names, dialogue, script source -- so a path
    argument has to be treated as something an attacker may have influenced. A
    name with a separator, a drive letter, or ``..`` in it is refused here
    rather than resolved somewhere later.
    """
    if not isinstance(name, str) or not _SAFE_COMPONENT.match(name):
        raise ProjectError(
            "%r is not a usable %s. It has to be a single name made of letters, "
            "digits, dots, dashes, plus signs or underscores -- no directory "
            "separators, drive letters or \"..\"." % (name, what)
        )
    if name.split(".")[0].lower() in _RESERVED_NAMES or name.endswith("."):
        raise ProjectError(
            "%r is not a usable %s: Windows resolves that name to a device "
            "rather than a file." % (name, what)
        )
    return name


def _contained(path: Path, inside: Path, what: str) -> Path:
    """Assert a built path really is under the directory it should be.

    Belt to :func:`safe_component`'s braces: a symlink or junction inside the
    tree could otherwise carry a validated name somewhere else.
    """
    resolved = path.resolve()
    if not resolved.is_relative_to(inside.resolve()):
        raise ProjectError(
            "%s resolves to %s, which is outside %s" % (what, resolved, inside)
        )
    return resolved


class CachedFile:
    """One data file as this server last saw it on disk.

    The digest is what makes the staleness check reliable on filesystems whose
    timestamps are coarse; it is None for a file this server invented.
    """

    __slots__ = ("value", "mtime_ns", "size", "warnings", "digest")

    def __init__(self, value: Any, mtime_ns: int, size: int,
                 warnings: list[str], digest: bytes | None = None):
        self.value = value
        self.mtime_ns = mtime_ns
        self.size = size
        self.warnings = warnings
        self.digest = digest


class Project:
    def __init__(self, root: str | os.PathLike[str], encoding: str = "auto",
                 read_only: bool = False, backup_keep: int = DEFAULT_BACKUP_KEEP,
                 allow_other_engines: bool = False,
                 rtp_paths: list[str] | None = None):
        self.root = Path(root).resolve()
        self.read_only = read_only
        self.backup_keep = backup_keep
        self._cache: dict[str, CachedFile] = {}
        self._assets: dict[str, frozenset[str]] = {}
        self._backed_up: set[str] = set()
        self._map_ids: list[int] | None = None
        self.rtp_override = False
        self._session = datetime.now().strftime(BACKUP_STAMP_FORMAT)

        if not self.root.is_dir():
            raise ProjectError("no such directory: %s" % self.root)
        self.data_dir = self._find_data_dir()
        self.engine, self.extension, self.engine_note = self._detect_engine()

        if self.engine in JSON_ENGINES:
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
        """Pick the code page this project's text is stored in.

        A sample table that is missing or unreadable simply contributes no
        samples: this runs during construction, before anything can be reported.
        """
        style = self.profile.string_ivars
        if requested and requested != "auto":
            return (TextCodec(requested, style),
                    "set explicitly with --encoding")
        samples: list[bytes] = []
        sampled: list[str] = []
        sources = list(ENCODING_SAMPLE_TABLES)
        # Dialogue lives in maps, not in the database, so sample one: a project
        # with an ASCII database and Japanese event text is otherwise reported
        # as settled when it is not.
        first_map = next(iter(self.map_ids_on_disk()), None)
        for key in sources:
            try:
                collect_strings(self.load_data(key), samples,
                                limit=ENCODING_SAMPLE_LIMIT)
            except (ProjectError, ValueError, OSError):
                continue
            sampled.append(key)
            if len(samples) >= ENCODING_SAMPLE_LIMIT:
                break
        if first_map is not None and len(samples) < ENCODING_SAMPLE_LIMIT:
            try:
                collect_strings(self.load_map(first_map), samples,
                                limit=ENCODING_SAMPLE_LIMIT)
                sampled.append("map %d" % first_map)
            except (ProjectError, ValueError, OSError):
                pass
            finally:
                self._cache.pop(self.map_file(first_map), None)
        # Everything the sampler read is evicted, not just the map: detection
        # runs before anything is served, and these are the files agents edit
        # most, so leaving any of them cached would serve construction-time
        # bytes until someone happened to pass refresh.
        for key in sampled:
            if not key.startswith("map "):
                self._cache.pop(self.filename_for(key), None)
        name, reason = detect_encoding(samples)
        return TextCodec(name, style), "%s (sampled %s)" % (
            reason, ", ".join(sampled) if sampled else "nothing")

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
        if explicit:
            # An explicit path is taken as the answer for every declared RTP:
            # there is no way to tell which of several it satisfies, and the
            # operator has said where to look. Recorded, because the reports
            # must not then claim every RTP was located on its own.
            self.rtp_override = True
            return found, []
        unresolved = []
        for name in self.rtp_names:
            located = _find_rtp_dir(name, self.engine)
            if located is None:
                unresolved.append(name)
            elif located not in found:
                found.append(located)
        return found, unresolved

    def _rtp_note(self) -> str:
        """How much of this project's asset pool the checks can actually see."""
        if not self.rtp_names:
            return ("Game.ini declares no RTP, so every asset should be inside "
                    "the project.")
        if self.rtp_override:
            return ("Asset checks use the --rtp folder given on the command "
                    "line for all %d declared RTPs (%s). If an asset looks "
                    "missing, check that folder is the right one."
                    % (len(self.rtp_names), ", ".join(self.rtp_names)))
        if self.assets_complete:
            return ("All declared RTPs were located, so asset checks see every "
                    "file this project can reference.")
        return ("Could not locate %s. Assets that live there will look missing; "
                "pass --rtp with the folder to fix that."
                % ", ".join(self.rtp_unresolved))

    @property
    def command_layouts_modelled(self) -> bool:
        """Whether this project's event command parameter layouts are known.

        Not a profile capability, because it cannot be one: an engine opened
        with --allow-other-engines is *described* with the closest profile
        available, so the profile says yes while the engine's real layouts
        differ. XP's Show Text keeps the message where Ace keeps the face
        graphic, which is the difference that matters. So this is one named
        fact about the open project, asked in the three places that care,
        rather than an engine-string comparison spelled out at each of them.
        """
        return self.engine in MODELLED_ENGINES

    @property
    def assets_complete(self) -> bool:
        """Whether every asset this project can reference is visible to us.

        False when Game.ini declares an RTP we could not locate: a file missing
        from the project folder may simply live there.
        """
        return not self.rtp_unresolved

    # -- paths ----------------------------------------------------------------
    @property
    def data_files(self) -> dict[str, DataFile]:
        """The tables this engine has, keyed by short name."""
        return self.profile.data_files

    def filename_for(self, key: str) -> str:
        try:
            entry = self.profile.data_file(key)
        except KeyError as exc:
            raise ProjectError(exc.args[0]) from exc
        return entry.filename(self.extension)

    def path_for(self, filename: str) -> Path:
        """The path of one file under ``Data/``, and only under ``Data/``."""
        safe_component(filename, "data file name")
        path = self.data_dir / filename
        _contained(path, self.data_dir, "data file %r" % filename)
        return path

    def map_file(self, map_id: int) -> str:
        return map_filename(map_id, self.extension)

    def _assert_writable(self, what: str) -> None:
        """Refuse a change when the server was started read-only."""
        if self.read_only:
            raise ReadOnlyError(
                "this server is running read-only, so %s did not happen. "
                "Restart without --read-only to allow changes." % what
            )

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
            value, warnings = marshal_rb.load_with_warnings(raw)
        except marshal_rb.MarshalError as exc:
            raise ProjectError("%s is not valid Marshal data: %s" % (filename, exc)) from exc
        self._cache[filename] = CachedFile(
            value, stat.st_mtime_ns, stat.st_size, warnings,
            hashlib.blake2b(raw, digest_size=16).digest())
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

    def map_ids_on_disk(self, refresh: bool = False) -> list[int]:
        """The ids of the map files present, listed once per run.

        `validate` alone asks four times and `project_info` three, so the
        listing is remembered; :meth:`create_file` and :meth:`delete_file`
        forget it when a map file appears or goes away.
        """
        if self._map_ids is not None and not refresh:
            return list(self._map_ids)
        ids = []
        for path in self.data_dir.glob("Map*" + self.extension):
            digits = path.stem.removeprefix("Map")
            # Only ids the engine can actually address. A Map0 or Map10000 in
            # the folder is not a map the editor made, and letting one through
            # here means every caller that turns an id back into a filename has
            # to handle a name that cannot exist.
            # Only the canonical spelling. Map1, Map01 and Map0001 all
            # parse as id 1, so accepting them let one cached map be walked
            # once per file, and made an id whose name maps back to a file
            # that is not there.
            if digits.isdigit() and 1 <= int(digits) <= MAX_MAP_ID \
                    and digits == "%03d" % int(digits):
                ids.append(int(digits))
        self._map_ids = sorted(set(ids))
        return list(self._map_ids)

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
        try:
            declared = self.profile.data_file(key).container
        except KeyError as exc:
            raise ProjectError(exc.args[0]) from exc
        if declared in (SINGLE, SCRIPTS):
            return declared
        # The registry says this file holds a collection but the file does not;
        # treat it as one object rather than guess at a shape it does not have.
        return SINGLE

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
    def load_scripts(self, with_code: bool = True,
                     indices: set[int] | None = None) -> list[dict[str, Any]]:
        """The script editor slots.

        ``with_code`` false skips the inflate and decode, which is what a caller
        that only wants the names and sizes should ask for -- a project carrying
        script packs is megabytes of Ruby.
        """
        raw = self.load_data("scripts")
        if not isinstance(raw, list):
            raise ProjectError("Scripts data is not an Array")
        scripts = []
        budget = MAX_SCRIPT_BYTES
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
            if not isinstance(packed, RubyString):
                entry["error"] = "the script body is not a String"
            elif with_code and (indices is None or index in indices):
                try:
                    body = _inflate(packed.data, budget)
                    budget -= len(body)
                    entry["code"] = self.codec.decode(body)
                except zlib.error as exc:
                    entry["error"] = "could not decompress: %s" % exc
                except ProjectError as exc:
                    entry["error"] = str(exc)
            scripts.append(entry)
        return scripts

    def set_script(self, index: int, name: str | None = None,
                   code: str | None = None) -> None:
        """Replace a script slot's name or body in the cached value.

        Mutates the cache only; call :meth:`save_file` to persist it.
        """
        raw = self.load_data("scripts")
        if not raw:
            raise ProjectError("this project's Scripts data holds no slots")
        if not 0 <= index < len(raw):
            raise ProjectError("script index %d is outside 0..%d"
                               % (index, len(raw) - 1))
        row = raw[index]
        if not isinstance(row, list) or len(row) < 3:
            raise ProjectError(
                "script slot %d is not shaped like [id, name, code]" % index)
        if name is not None:
            # make_string, so the name carries this engine's string form like
            # every other text this server writes.
            row[1] = self.codec.make_string(name)
        if code is not None:
            row[2] = RubyString(zlib.compress(self.codec.encode_strict(code)))

    # -- writing --------------------------------------------------------------
    def save_file(self, filename: str, dry_run: bool = False,
                  tool: str = "", summary: str = "") -> dict[str, Any]:
        if not dry_run:
            self._assert_writable("writing %s" % filename)
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

        # A unique temp name, opened exclusively: a predictable one in a
        # directory someone else can write is a way to redirect this write.
        handle, temp_name = tempfile.mkstemp(dir=str(path.parent),
                                             prefix=path.name + ".",
                                             suffix=TMP_SUFFIX)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                # The rename is atomic, but only against a crash of this
                # process; fsync is what makes it hold against the machine
                # going down between the write and the rename.
                os.fsync(stream.fileno())
            if path.exists():
                # After the content, not before: mkstemp's 0600 should hold for
                # the duration of the write, and only the finished file needs
                # to carry the mode the old one had -- otherwise the rename
                # would narrow a data file's permissions on a shared folder.
                shutil.copymode(path, temp_path)
            os.replace(temp_path, path)
        except OSError as exc:
            temp_path.unlink(missing_ok=True)
            raise ProjectError("could not write %s: %s" % (filename, exc)) from exc

        stat = path.stat()
        cached.mtime_ns, cached.size = stat.st_mtime_ns, stat.st_size
        cached.digest = hashlib.blake2b(encoded, digest_size=16).digest()
        self._log_change(tool, filename, summary)
        result["written"] = str(path)
        return result

    def _assert_not_stale(self, filename: str, path: Path, cached: CachedFile) -> None:
        if not path.exists():
            return  # a new file; nothing to clobber
        stat = path.stat()
        unchanged = (stat.st_mtime_ns == cached.mtime_ns
                     and stat.st_size == cached.size)
        if cached.digest is not None:
            # The content decides, not the timestamp. Modification times are
            # coarse on FAT and on many network shares -- one or two seconds --
            # so an editor save inside the same tick that happened to produce
            # the same byte count would pass a time-and-size check while the
            # data underneath had changed completely. This runs only on a write,
            # and data files are small, so re-reading one is cheap next to
            # silently overwriting someone's work.
            try:
                current = hashlib.blake2b(path.read_bytes(),
                                          digest_size=16).digest()
            except OSError as exc:
                # On Windows this is precisely another process holding the
                # file -- the case the digest exists to catch -- so it is a
                # refusal, not a reason to fall back to the weaker test.
                raise StaleFileError(
                    "%s could not be re-read to check whether it changed (%s), "
                    "which usually means another program has it open. Close "
                    "the editor and try again." % (filename, exc)) from exc
            if current is not None:
                if current == cached.digest:
                    # Same bytes; a rewritten timestamp is not a change.
                    cached.mtime_ns, cached.size = stat.st_mtime_ns, stat.st_size
                    return
                unchanged = False
        if unchanged:
            return
        if stat.st_size == cached.size:
            detail = "its contents differ from what was read (still %d bytes)" \
                % stat.st_size
        else:
            detail = "it is now %d bytes rather than %d" % (stat.st_size,
                                                            cached.size)
        raise StaleFileError(
            "%s changed on disk after this server read it: %s. The %s editor "
            "rewrites every data file when it saves, so it is probably open -- "
            "close it, then re-read the file (any tool with refresh, or "
            "project_info) before writing again. Your edit was not applied."
            % (filename, detail, self.profile.label)
        )

    def create_file(self, filename: str, value: Any) -> None:
        """Register a brand new data file so :meth:`save_file` can write it.

        Mutates the cache only; call :meth:`save_file` to write it.
        """
        self._assert_writable("creating %s" % filename)
        if self.path_for(filename).exists():
            raise ProjectError("%s already exists" % filename)
        self._cache[filename] = CachedFile(value, 0, 0, [])
        self._backed_up.add(filename)  # nothing to back up
        self._map_ids = None

    def forget_file(self, filename: str) -> None:
        """Drop a cached file, undoing a :meth:`create_file` that came to nothing."""
        self._cache.pop(filename, None)
        self._map_ids = None

    def delete_file(self, filename: str, dry_run: bool = False,
                    tool: str = "") -> dict[str, Any]:
        if not dry_run:
            self._assert_writable("deleting %s" % filename)
        path = self.path_for(filename)
        if not path.is_file():
            raise ProjectError("%s does not exist" % filename)
        if dry_run:
            return {"file": filename, "would_delete": str(path), "dry_run": True}
        # The same check every write makes. Deleting is the most destructive
        # thing here, so exempting it from "refuse to act on a file that
        # changed underneath us" was the wrong way round.
        cached = self._cache.get(filename)
        if cached is not None:
            self._assert_not_stale(filename, path, cached)
        backup = self.backup_file(filename)
        path.unlink()
        self._cache.pop(filename, None)
        self._map_ids = None
        # Deleting is the most destructive thing here, so it is logged like
        # every other write.
        self._log_change(tool, filename, "deleted")
        return {"file": filename, "deleted": str(path), "backup": backup}

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

    def snapshot(self, label: str = "manual", keep: set[str] | None = None
                 ) -> dict[str, Any]:
        """Copy every data file into a fresh backup directory.

        Prunes old snapshots afterwards, sparing anything named in ``keep``.
        """
        self._assert_writable("taking a snapshot")
        clean = re.sub(r"[^A-Za-z0-9._-]", "-", label) or "manual"
        stamp = datetime.now().strftime(BACKUP_STAMP_FORMAT) + "-" + clean
        target = self._backup_dir(stamp)
        target.mkdir(parents=True, exist_ok=True)
        copied = []
        for path in sorted(self.data_dir.glob("*" + self.extension)):
            if path.is_symlink():
                # Copying one would put whatever it points at into the backup
                # tree, where the read tools would serve it as project data.
                continue
            name = safe_component(path.name, "data file name")
            _contained(target / name, target, "backup of %r" % name)
            shutil.copy2(path, target / name)
            copied.append(name)
        self.prune_backups(keep)
        return {"backup": stamp, "path": str(target), "files": copied}

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
            return {"backup": stamp, "would_restore": wanted, "dry_run": True}
        # Snapshot what is about to be overwritten, so a mistaken restore is
        # itself reversible. `keep` spares the snapshot being restored from:
        # pruning runs inside this call, and would otherwise be free to delete
        # the very directory about to be copied out of.
        pre = self.snapshot("pre-restore", keep={stamp})
        for name in wanted:
            source = _contained(source_dir / name, self.backup_root(),
                                "backup file %r" % name)
            shutil.copy2(source, self.path_for(name))
            self._cache.pop(name, None)
        self._map_ids = None
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
            if stale.name in spared or stale.is_symlink():
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

    # -- assets ---------------------------------------------------------------
    def asset_names(self, kind: str) -> frozenset[str]:
        """Base names (no extension) of one asset kind, project plus RTP.

        Extensions are dropped because the data files store names without them,
        and the engine accepts several formats per kind.

        Listed once per run and remembered: an RTP folder holds hundreds of
        files, and `validate` asks for the same kinds repeatedly. Call
        :meth:`forget_assets` after adding files to a project mid-session.
        """
        cached = self._assets.get(kind)
        if cached is not None:
            return cached
        # frozenset, so a caller cannot mutate the pool every later check reads.
        relative = ASSET_DIRS.get(kind)
        if relative is None:
            raise ProjectError("unknown asset kind %r" % kind)
        names: set[str] = set()
        for base in [self.root, *self.rtp_dirs]:
            directory = base / relative
            try:
                # scandir rather than iterdir: its entries carry the file/dir
                # flag from the directory read, so this costs one stat per
                # directory instead of one per file.
                with os.scandir(directory) as entries:
                    for entry in entries:
                        if entry.is_file():
                            names.add(os.path.splitext(entry.name)[0])
            except OSError:
                # Any unreadable directory, not just a missing one: a
                # permission error on a network RTP share used to abort the
                # whole validate run.
                continue
        frozen = frozenset(names)
        self._assets[kind] = frozen
        return frozen

    def forget_assets(self) -> None:
        """Drop the remembered asset listings, so the next check re-reads them."""
        self._assets.clear()

    def describe(self) -> dict[str, Any]:
        files = []
        for key, entry in self.profile.data_files.items():
            filename = self.filename_for(key)
            path = self.path_for(filename)
            try:
                stat = path.stat()
                present, size = True, stat.st_size
            except OSError:
                present, size = False, 0
            files.append({
                "table": key,
                "file": filename,
                "class": entry.cls,
                "description": entry.description,
                "present": present,
                "bytes": size,
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
                "note": self._rtp_note(),
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
        for value_name, value in _registry_rtp_values(registry_path):
            if value_name.lower() == name.lower() and Path(value).is_dir():
                return Path(value).resolve()
    # The name came out of the project's own Game.ini, so it is not trusted as
    # a path component.
    try:
        component = safe_component(name, "RTP name")
    except ProjectError:
        return None
    # Only this engine's generation: probing RGSS3 for a VX project finds the
    # wrong asset pool and then claims the RTP was located.
    generation = RGSS_GENERATION.get(engine, "")
    for root in RTP_SEARCH_ROOTS:
        base = Path(root)
        if not base.is_dir():
            continue
        candidates = [base / component, base / component / "rtp"]
        if generation:
            # Ace ships its pool as RGSS3/Standard; VX's is RGSS2/RPGVX, where
            # the leaf is the name Game.ini declares.
            candidates.append(base / generation / component)
            candidates.append(base / generation / "Standard")
        for child in candidates:
            if child.is_dir():
                return child.resolve()
    return None


@functools.lru_cache(maxsize=None)
def _registry_rtp_values(registry_path: str) -> tuple[tuple[str, str], ...]:
    """Every (name, path) pair under one RTP registry key, or nothing."""
    try:
        import winreg
    except ImportError:
        return ()  # not Windows; --rtp is the way in
    out: list[tuple[str, str]] = []
    variants = [registry_path,
                registry_path.replace("SOFTWARE\\", "SOFTWARE\\WOW6432Node\\", 1)]
    # Machine-wide first: a per-user key is writable by anything running as the
    # user, and should not silently redirect asset scanning.
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
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
    return tuple(out)


def _inflate(data: bytes, budget: int) -> bytes:
    """Decompress a script body within a byte budget.

    A zlib stream can expand by a thousand to one, so a crafted Scripts file
    would otherwise be able to name any amount of memory it liked. The budget
    is shared across the whole file, because every slot is inflated and kept.

    A truncated stream is still an error: returning the part that did inflate
    would let a read-edit-write cycle quietly drop the tail of a real script.
    """
    stream = zlib.decompressobj()
    out = stream.decompress(data, max(budget, 1))
    if stream.unconsumed_tail:
        raise ProjectError(
            "this file's script bodies inflate to more than %d bytes in total, "
            "which is not something anyone wrote" % MAX_SCRIPT_BYTES
        )
    if not stream.eof:
        raise ProjectError("the compressed script body is truncated")
    return out


def _engine_label(engine: str) -> str:
    profile = PROFILES.get(engine)
    if profile is not None:
        return profile.label
    return {"xp": "RPG Maker XP", "mv": "RPG Maker MV/MZ"}.get(engine, engine)


def collect_strings(value: Any, out: list[bytes], limit: int = 200,
                    depth: int = 0, seen: set[int] | None = None,
                    budget: list[int] | None = None) -> None:
    """Gather string samples for encoding detection.

    Memoised on container identity and bounded by a shared node budget, like
    every other walk over decoded project data. Marshal registers a container
    for back-reference before it fills it, so one file can legitimately name
    the same child from many places -- and a crafted one can do so at every
    level, which without a memo is exponential. This walk runs inside
    ``Project.__init__``, so that would hang the server before it served
    anything.
    """
    if seen is None:
        seen = set()
    if budget is None:
        budget = [MAX_STRING_SCAN_NODES]
    if len(out) >= limit or depth > MAX_STRING_SCAN_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1

    if isinstance(value, RubyString):
        if value.data:
            out.append(value.data)
        return
    if id(value) in seen:
        return
    seen.add(id(value))

    if isinstance(value, RubyObject):
        children: Any = value.ivars.values()
    elif isinstance(value, list):
        children = value
    elif isinstance(value, RubyHash):
        children = [item for _, item in value.pairs]
    else:
        return
    for item in children:
        collect_strings(item, out, limit, depth + 1, seen, budget)



