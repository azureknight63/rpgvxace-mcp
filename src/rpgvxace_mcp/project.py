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
import os
import shutil
import tempfile
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

from . import marshal_rb
from .backups import BackupStore
# Read here, and re-exported here. `CHANGE_LOG`, `WORK_DIR`,
# `DEFAULT_BACKUP_KEEP` and the three errors are imported *from this module*
# by the tools layer and the tests, under the names they have always had --
# moving the path rules into their own file is not a reason to make nine
# callers learn a second module name.
from .paths import (  # noqa: F401
    BACKUP_STAMP_FORMAT,
    CHANGE_LOG,
    DEFAULT_BACKUP_KEEP,
    ProjectError,
    ReadOnlyError,
    StaleFileError,
    TMP_SUFFIX,
    WORK_DIR,
    _contained,
    safe_component,
)
from .convert import TextCodec, decoded_text, detect_encoding
from .rubytypes import RubyHash, RubyObject, RubyString, ruby_children
from .schema import (
    ARRAY,
    DataFile,
    EngineProfile,
    HASH,
    # Imported rather than defined here: there used to be one of each,
    # so the highest id a tool accepted and the highest a filename
    # could express were two independent 9999s.
    MAX_MAP_ID,
    PROFILES,
    SCRIPTS,
    SINGLE,
    VX_PROFILE,
    map_filename,
)

# How much of a project is sampled to guess its text encoding.
ENCODING_SAMPLE_TABLES = ("system", "actors", "items", "map_infos")
ENCODING_SAMPLE_LIMIT = 400
MAX_STRING_SCAN_DEPTH = 12

# How many values one call of `collect_strings` will look at. The encoding
# sampler runs during construction, so it has to finish on any file at all --
# and it walks several files, so the sampler's own ceiling is one shared budget
# passed to each call. This is the per-call default for every other caller.
MAX_STRING_SCAN_NODES = 200_000

# The largest data file this server will read into memory. A real project's
# largest file is a few megabytes; this leaves room above that while turning a
# damaged or crafted multi-gigabyte file into a refusal rather than a
# MemoryError, which is not a ProjectError and so escaped Project.__init__ as
# a traceback.
MAX_DATA_FILE_BYTES = 128 * 1024 * 1024

# Game.ini is a handful of keys. Capped for the same reason and much lower,
# because nothing legitimate comes near it.
MAX_GAME_INI_BYTES = 1024 * 1024

# A script slot inflates into memory, so a crafted Scripts file must not be
# able to ask for an unbounded amount of it.
MAX_SCRIPT_BYTES = 16 * 1024 * 1024

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

# A superset across the VX family: Battlebacks1/2 are Ace's, the single
# Battlebacks folder is VX's. A folder an engine does not use simply comes back
# empty.
#
# An empty pool is *not* generally skipped -- `_AssetChecker` skips one only
# when a declared RTP could not be located, which is the case where "we could
# not look" and "there is nothing there" are indistinguishable. With no RTP
# declared, every reference into an empty folder is reported missing, which is
# right for a project that really has none.
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


# How much *source* the decoded-file cache may hold in total.
#
# MAX_DATA_FILE_BYTES bounds one file; nothing bounded the sum, and the cache
# has no eviction path but the named invalidations. Marshal decodes well above
# its on-disk size -- measured at 32.4x for the shape a database table really
# has, 120,000 distinct small objects, where 1.61 MB of file became 52 MB
# held.
#
# Counted in source bytes because measuring a decoded tree means walking it,
# which is the cost this cache exists to avoid; the source size is exact,
# free, and already on every entry.
#
# 16 MB is ten times the developer's entire real project (1.61 MB across 54
# files), so it will not evict in practice, and it bounds the worst case at
# roughly half a gigabyte rather than at nothing.
#
# One file may exceed it: a file larger than the whole ceiling still has to be
# held to be served, so the guarantee is "within the ceiling, or one entry".
# MAX_DATA_FILE_BYTES is what bounds that one.
MAX_CACHED_BYTES = 16 * 1024 * 1024


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


class Project(BackupStore):
    def __init__(self, root: str | os.PathLike[str], encoding: str = "auto",
                 read_only: bool = False, backup_keep: int = DEFAULT_BACKUP_KEEP,
                 allow_other_engines: bool = False,
                 rtp_paths: list[str] | None = None):
        self.root = Path(root).resolve()
        self.read_only = read_only
        self.backup_keep = backup_keep
        # Insertion-ordered, and read order is maintained by `_touch`, so
        # eviction can take the least recently used.
        self._cache: dict[str, CachedFile] = {}
        # Files an open staged write is holding. Never evicted: every editing
        # tool mutates the value in this cache and then asks for it to be
        # written, so dropping the entry in between would save the file as it
        # was on disk and report the edit as applied.
        self._pinned: set[str] = set()
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
        # One budget for the whole sample, not one per file: five files each
        # given MAX_STRING_SCAN_NODES is five times the work the constant
        # describes, during construction, before the server can answer
        # anything.
        budget = [MAX_STRING_SCAN_NODES]
        sources = ENCODING_SAMPLE_TABLES
        # Dialogue lives in maps, not in the database, so sample one: a project
        # with an ASCII database and Japanese event text is otherwise reported
        # as settled when it is not.
        first_map = next(iter(self.map_ids_on_disk()), None)
        for key in sources:
            try:
                collect_strings(self.load_data(key), samples,
                                limit=ENCODING_SAMPLE_LIMIT, budget=budget)
            except (ProjectError, ValueError, OSError, MemoryError):
                # MemoryError too: MAX_DATA_FILE_BYTES bounds the file on
                # disk and not the graph it decodes to, and a 128 MB file of
                # four-byte object records becomes about 32 million objects.
                # This runs from `Project.__init__`, before anything is
                # served, so an escape here is the traceback that constant's
                # own comment says it was added to prevent.
                continue
            sampled.append(key)
            if len(samples) >= ENCODING_SAMPLE_LIMIT:
                break
        if first_map is not None and len(samples) < ENCODING_SAMPLE_LIMIT:
            try:
                collect_strings(self.load_map(first_map), samples,
                                limit=ENCODING_SAMPLE_LIMIT, budget=budget)
                sampled.append("map %d" % first_map)
            except (ProjectError, ValueError, OSError, MemoryError):
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
            # Size-capped like every other untrusted read here: a real
            # Game.ini is a few hundred bytes, and reading a huge one whole
            # would raise MemoryError out of Project.__init__.
            if path.stat().st_size > MAX_GAME_INI_BYTES:
                return []
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
        self._touch(filename)
        cached = self._cache.get(filename)
        if cached is not None and not refresh:
            return cached.value
        path = self.path_for(filename)
        if not path.is_file():
            raise ProjectError("%s does not exist in %s" % (filename, self.data_dir))
        info = path.stat()
        if info.st_size > MAX_DATA_FILE_BYTES:
            # Checked before reading, not after: a damaged or crafted file of
            # a few gigabytes raised MemoryError, which is not a ProjectError
            # and so escaped Project.__init__ as a traceback rather than being
            # reported as a bad file.
            raise ProjectError(
                "%s is %.1f MB, above the %d MB this server reads. A real "
                "data file is a few megabytes; this one is damaged, or is not "
                "project data."
                % (filename, info.st_size / (1024 * 1024),
                   MAX_DATA_FILE_BYTES // (1024 * 1024)))
        raw = path.read_bytes()
        try:
            value, warnings = marshal_rb.load_with_warnings(raw)
        except marshal_rb.MarshalError as exc:
            raise ProjectError("%s is not valid Marshal data: %s" % (filename, exc)) from exc
        self._cache[filename] = CachedFile(
            value, info.st_mtime_ns, info.st_size, warnings,
            hashlib.blake2b(raw, digest_size=16).digest())
        # After the new entry is in, so the file just asked for is the last
        # thing eviction would consider rather than the first.
        self._evict_over_budget()
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
            # isascii() as well: isdigit() is true for non-decimal Unicode
            # numerics that int() then rejects, so "Map²9.rvdata2" reached
            # int() and raised ValueError out of the function whose job is to
            # decide which ids the engine can address.
            if digits.isascii() and digits.isdigit() \
                    and 1 <= int(digits) <= MAX_MAP_ID \
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
            # `id`, not `magic`. It is the slot's id: `schema` documents
            # this file as "Array of [id, name, zlib code]" and `docs`
            # renders that to the model, and this was the only place calling
            # it a magic number -- while `scripts op="get"` hands this row
            # straight to the caller, so the code's name is the one the model
            # reads.
            slot_id, name, packed = row[0], row[1], row[2]
            entry: dict[str, Any] = {
                "index": index,
                "id": slot_id,
                # Through `decoded_text`, which caps and sanitises: this
                # is decoded and retained for every row even when
                # `with_code` is false, and 200,000 rows all linking one
                # 1 MB name is a 2.6 MB file and 200 GB of decode --
                # reachable from `project_info`, the tool the description
                # tells the model to call first.
                "name": decoded_text(self.codec, name),
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

        info = path.stat()
        cached.mtime_ns, cached.size = info.st_mtime_ns, info.st_size
        cached.digest = hashlib.blake2b(encoded, digest_size=16).digest()
        self._log_change(tool, filename, summary)
        result["written"] = str(path)
        return result

    def _assert_not_stale(self, filename: str, path: Path, cached: CachedFile) -> None:
        if not path.exists():
            return  # a new file; nothing to clobber
        info = path.stat()
        unchanged = (info.st_mtime_ns == cached.mtime_ns
                     and info.st_size == cached.size)
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
            # No `if current is not None` here any more: `current` comes
            # from a digest three lines up and the only failure path raises,
            # so that condition could not be false.
            if current == cached.digest:
                # Same bytes; a rewritten timestamp is not a change.
                cached.mtime_ns, cached.size = info.st_mtime_ns, info.st_size
                return
            unchanged = False
        if unchanged:
            return
        if info.st_size == cached.size:
            detail = "its contents differ from what was read (still %d bytes)" \
                % info.st_size
        else:
            detail = "it is now %d bytes rather than %d" % (info.st_size,
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

    # -- assets ---------------------------------------------------------------
    def asset_names(self, kind: str) -> frozenset[str]:
        """Base names (no extension) of one asset kind, project plus RTP.

        Extensions are dropped because the data files store names without them,
        and the engine accepts several formats per kind.

        Listed once per run and remembered: an RTP folder holds hundreds of
        files, and `validate` asks for the same kinds repeatedly.
        :meth:`forget_assets` drops them, and `validate refresh=true` is what
        calls it -- without which a graphic added after a run that reported it
        missing went on being reported missing until the server restarted.
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

    def pin(self, *filenames: str) -> None:
        """Hold these files in the cache for the length of a staged write."""
        self._pinned.update(filenames)

    def unpin(self, *filenames: str) -> None:
        self._pinned.difference_update(filenames)

    def _touch(self, filename: str) -> None:
        """Move one entry to the most-recently-used end."""
        entry = self._cache.pop(filename, None)
        if entry is not None:
            self._cache[filename] = entry

    def _evict_over_budget(self) -> None:
        """Drop least-recently-used files until the cache fits its ceiling.

        Never a pinned file, and never the last one: a cache that evicts
        everything does nothing, and the entry just loaded is the one the
        caller is about to use.
        """
        held = sum(entry.size for entry in self._cache.values())
        if held <= MAX_CACHED_BYTES:
            return
        for filename in list(self._cache):
            if held <= MAX_CACHED_BYTES or len(self._cache) <= 1:
                return
            if filename in self._pinned:
                continue
            held -= self._cache.pop(filename).size

    def forget_assets(self) -> None:
        """Drop the remembered asset listings, so the next check re-reads them."""
        self._assets.clear()

    def forget_cached(self) -> None:
        """Drop the decoded files, so the next read comes from disk.

        The per-file `refresh` argument does this one file at a time, which is
        right for a tool that names its file. `validate` reads most of the
        project and cannot name them all, so it drops the lot.
        """
        self._cache.clear()

    def describe(self) -> dict[str, Any]:
        files = []
        for key, entry in self.profile.data_files.items():
            filename = self.filename_for(key)
            path = self.path_for(filename)
            try:
                info = path.stat()
                present, size = True, info.st_size
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

    # Through `ruby_children`, which is where the container set lives now.
    # This walked three kinds where `schema._observe` walks eight, so a
    # string inside a RubyIvars, a RubyStruct, a RubyUserMarshal, a
    # RubyUClass or a RubyExtended was invisible to encoding detection --
    # and `analysis`' own comment records that the codec really does wrap
    # values in RubyIvars in files the editor writes.
    for item in ruby_children(value):
        collect_strings(item, out, limit, depth + 1, seen, budget)
