"""The bible file: where `lore_bible` keeps what the author has said.

One JSON file, `.rpgvxace-mcp/lore/bible.json`, in the project's work folder
beside the backups, renders and stamps. It is a cache the author owns, not
game data: nothing in it is ever written into a data file, and deleting the
folder loses the author's answers and nothing else.

Why it is read like a hostile file
----------------------------------
`.rpgvxace-mcp/` sits inside the project, which this server's threat model
treats as untrusted, and a person (or a model) may have edited the bible by
hand. So every read goes through :func:`clean`, which rebuilds the document
field by field and refuses, with a message that names the field, anything it
does not recognise: a wrong type, an over-long string, a control character, an
unknown top-level key, a number that is not finite, a baseline nested deeper
than the one this server writes. The file's size is checked before it is
opened. The folder, the work folder above it and the file itself are refused if
they are links or junctions, in the discipline of the undo journal and the stamp
store, so a planted link cannot carry a write out of the project.

Writes are atomic (a temporary file in the same folder, then a rename), so a
failed write leaves the old bible. A read-only server refuses to save.
"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from typing import Any

from .convert import brief
from .lore_guidance import TIER_ORDER
from .paths import ProjectError, _contained, _is_reparse_point
from .undo_journal import write_bytes_atomic


LORE_DIR = "lore"
BIBLE_FILE = "bible.json"
EXPORT_FILES = ("bible.html", "bible.md")


FORMAT = "rpgvxace-mcp-lore"
VERSION = 1


MODES = ("qa", "artifact", "custom")
REVIEW_SCOPES = ("bible_to_game", "game_to_bible", "spellings", "places")
EXPORT_FORMATS = {"html": "bible.html", "markdown": "bible.md"}
ENTRY_KINDS = ("place", "character", "faction", "event", "term", "item",
               "other")


# Ceilings a caller meets. Each is advertised in the tool's description.
MAX_BIBLE_BYTES = 1_000_000
MAX_ANSWER_CHARS = 4_000
MAX_BIBLE_ENTRIES = 500
MAX_ENTRIES_PER_SAVE = 100
MAX_ENTRY_NAME_CHARS = 80
MAX_ENTRY_TEXT_CHARS = 2_000
MAX_DELETE_NAMES = 100
MAX_APPROACH_CHARS = 2_000
MAX_QUESTIONS_PER_CALL = 10
MAX_REVIEW_FINDINGS = 60
MAX_BASELINE_BYTES = 65_536
MAX_BASELINE_NOUNS = 40
MAX_BASELINE_VARIANTS = 20
MAX_BASELINE_SPEAKERS = 15
MAX_BASELINE_SAMPLES = 3
MAX_BASELINE_GAPS = 10
MAX_BASELINE_SAMPLE_CHARS = 120


# Internal: bounds on reading a file this server did not necessarily write.
# A file that trips one is reported as unusable, which is the actionable fact.
_STORED_ANSWERS_UPPER = 200
_BASELINE_NODES_UPPER = 4_000
_BASELINE_STRING_UPPER = 600
_BASELINE_LIST_UPPER = 300
_BASELINE_KEY_UPPER = 64
_BASELINE_NUMBER_UPPER = 10 ** 12
_EXPORT_BYTES_UPPER = 8_000_000
_ANSWER_ID_UPPER = 90
BASELINE_DEPTH = 6


_ANSWER_ID = re.compile(r"[a-z][a-z_]{0,31}\.[a-z0-9_]{1,48}\Z")
# What a stored string may not contain: control characters other than the
# line break and tab, and lone surrogates (which JSON permits and UTF-8
# cannot hold).
_BAD_LONG = re.compile("[\x00-\x08\x0b-\x1f\x7f\ud800-\udfff]")
_BAD_SHORT = re.compile("[\x00-\x1f\x7f\ud800-\udfff]")


_TOP_KEYS = ("format", "version", "tier", "mode", "approach", "answers",
             "entries", "baseline")


class BibleError(ValueError):
    """A problem with a value bound for the bible. The message names the field."""


def new_bible() -> dict[str, Any]:
    """An empty bible, in the key order the file is written in."""
    return {"format": FORMAT, "version": VERSION, "tier": None, "mode": None,
            "approach": None, "answers": {}, "entries": [], "baseline": None}


# ---------------------------------------------------------------------------
# Checking values: one definition, used for the file and for a request
# ---------------------------------------------------------------------------


def check_text(value: Any, what: str, limit: int, *, multiline: bool,
               allow_empty: bool = True) -> str:
    """A string for the bible: bounded, and free of what JSON or UTF-8 cannot hold."""
    if not isinstance(value, str):
        raise BibleError("%s must be text, got %s" % (what, brief(value, 60)))
    if len(value) > limit:
        raise BibleError("%s is %d characters, above the %d it may hold"
                         % (what, len(value), limit))
    bad = (_BAD_LONG if multiline else _BAD_SHORT).search(value)
    if bad:
        raise BibleError(
            "%s contains %s, which cannot be stored%s"
            % (what, "a lone surrogate" if "\ud800" <= bad.group() <= "\udfff"
               else "the control character U+%04X" % ord(bad.group()),
               "" if multiline else " in a one-line value"))
    if not allow_empty and not value.strip():
        raise BibleError("%s is empty" % what)
    return value


def check_answer_id(value: Any, what: str = "an answer") -> str:
    """A question id's shape. Whether this server knows it is another question."""
    if (not isinstance(value, str) or len(value) > _ANSWER_ID_UPPER
            or not _ANSWER_ID.match(value)):
        raise BibleError("%s has a key that is not a question id: %s"
                         % (what, brief(value, 60)))
    return value


def check_entry(raw: Any, where: str) -> dict[str, str]:
    """One entry, rebuilt from only the fields it may have."""
    if not isinstance(raw, dict):
        raise BibleError("%s must be an object, got %s"
                         % (where, brief(raw, 60)))
    extra = sorted(set(raw) - {"kind", "name", "text"})
    if extra:
        raise BibleError("%s has a field %s that an entry does not have; it "
                         "has kind, name and text"
                         % (where, brief(extra[0], 40)))
    for field in ("kind", "name", "text"):
        if field not in raw:
            raise BibleError("%s has no %s" % (where, field))
    kind = raw["kind"]
    if not isinstance(kind, str) or kind not in ENTRY_KINDS:
        raise BibleError("%s.kind must be one of %s, got %s"
                         % (where, ", ".join(ENTRY_KINDS), brief(kind, 40)))
    name = check_text(raw["name"], where + ".name", MAX_ENTRY_NAME_CHARS,
                      multiline=False, allow_empty=False).strip()
    text = check_text(raw["text"], where + ".text", MAX_ENTRY_TEXT_CHARS,
                      multiline=True)
    return {"kind": kind, "name": name, "text": text}


def entry_key(entry: dict[str, str]) -> tuple[str, str]:
    """What makes two entries the same one: the kind and the name, any case."""
    return entry["kind"], entry["name"].casefold()


def _clean_entries(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise BibleError("entries must be an array, got %s" % brief(value, 60))
    if len(value) > MAX_BIBLE_ENTRIES:
        raise BibleError("entries lists %d; a bible holds at most %d"
                         % (len(value), MAX_BIBLE_ENTRIES))
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        entry = check_entry(raw, "entries[%d]" % index)
        key = entry_key(entry)
        if key in seen:
            raise BibleError("entries[%d] lists the %s %s twice; each kind "
                             "and name is listed once"
                             % (index, entry["kind"],
                                brief(entry["name"], 40)))
        seen.add(key)
        out.append(entry)
    return out


def _clean_answers(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise BibleError("answers must be an object, got %s"
                         % brief(value, 60))
    if len(value) > _STORED_ANSWERS_UPPER:
        raise BibleError("answers holds %d; a bible has at most %d"
                         % (len(value), _STORED_ANSWERS_UPPER))
    out: dict[str, str] = {}
    for key, text in value.items():
        check_answer_id(key, "answers")
        out[key] = check_text(text, "answers[%s]" % key, MAX_ANSWER_CHARS,
                              multiline=True)
    return out


def _clean_baseline(value: Any) -> "dict[str, Any] | None":
    """The scan the bible carries: JSON of a bounded size, depth and width.

    Nothing here is read for meaning; the consumers look up the few fields
    they know and treat any other shape as absent. What is checked is that it
    cannot be large, deep, or carry a number or string that breaks the reply
    it ends up in.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BibleError("baseline must be an object or null, got %s"
                         % brief(value, 60))
    nodes = [0]

    def walk(item: Any, depth: int, where: str) -> Any:
        nodes[0] += 1
        if nodes[0] > _BASELINE_NODES_UPPER:
            raise BibleError("baseline holds more than %d values"
                             % _BASELINE_NODES_UPPER)
        if depth > BASELINE_DEPTH:
            raise BibleError("baseline nests deeper than %d levels at %s"
                             % (BASELINE_DEPTH, where))
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, int):
            if abs(item) > _BASELINE_NUMBER_UPPER:
                raise BibleError("baseline has a number too large to be a "
                                 "count, at %s" % where)
            return item
        if isinstance(item, float):
            if not math.isfinite(item) or abs(item) > _BASELINE_NUMBER_UPPER:
                raise BibleError("baseline has a number that is not finite "
                                 "or not a count, at %s" % where)
            return item
        if isinstance(item, str):
            if len(item) > _BASELINE_STRING_UPPER:
                raise BibleError("baseline has a string over %d characters "
                                 "at %s" % (_BASELINE_STRING_UPPER, where))
            if _BAD_LONG.search(item):
                raise BibleError("baseline has a control character at %s"
                                 % where)
            return item
        if isinstance(item, list):
            if len(item) > _BASELINE_LIST_UPPER:
                raise BibleError("baseline has a list over %d long at %s"
                                 % (_BASELINE_LIST_UPPER, where))
            return [walk(inner, depth + 1, where + "[]") for inner in item]
        if isinstance(item, dict):
            out: dict[str, Any] = {}
            for key, inner in item.items():
                if (not isinstance(key, str)
                        or len(key) > _BASELINE_KEY_UPPER):
                    raise BibleError("baseline has a key over %d characters "
                                     "at %s" % (_BASELINE_KEY_UPPER, where))
                out[key] = walk(inner, depth + 1, where + "." + key)
            return out
        raise BibleError("baseline has a value that is not JSON at %s" % where)

    cleaned = walk(value, 1, "baseline")
    size = len(json.dumps(cleaned, ensure_ascii=False).encode("utf-8"))
    if size > MAX_BASELINE_BYTES:
        raise BibleError("baseline is %d bytes, above the %d bytes it may "
                         "hold" % (size, MAX_BASELINE_BYTES))
    return cleaned


def clean(doc: Any) -> dict[str, Any]:
    """The document, rebuilt from only what a bible may hold, or a refusal."""
    if not isinstance(doc, dict):
        raise BibleError("it is not a JSON object")
    for key in doc:
        if key not in _TOP_KEYS:
            raise BibleError("it has a field %s that a bible does not have"
                             % brief(key, 40))
    for key in _TOP_KEYS:
        if key not in doc:
            raise BibleError("it has no %s field" % key)
    if doc["format"] != FORMAT:
        raise BibleError("its format is not %s" % FORMAT)
    version = doc["version"]
    if type(version) is not int or version != VERSION:
        raise BibleError("its version is %s; this server reads version %d"
                         % (brief(version, 20), VERSION))
    tier, mode, approach = doc["tier"], doc["mode"], doc["approach"]
    if tier is not None and (not isinstance(tier, str)
                             or tier not in TIER_ORDER):
        raise BibleError("tier must be one of %s or null, got %s"
                         % (", ".join(TIER_ORDER), brief(tier, 40)))
    if mode is not None and (not isinstance(mode, str) or mode not in MODES):
        raise BibleError("mode must be one of %s or null, got %s"
                         % (", ".join(MODES), brief(mode, 40)))
    if mode == "custom":
        approach = check_text(approach, "approach", MAX_APPROACH_CHARS,
                              multiline=True, allow_empty=False)
    elif approach is not None:
        raise BibleError("approach is set but the mode is not custom, and "
                         "only a custom mode has an approach")
    return {"format": FORMAT, "version": VERSION, "tier": tier, "mode": mode,
            "approach": approach, "answers": _clean_answers(doc["answers"]),
            "entries": _clean_entries(doc["entries"]),
            "baseline": _clean_baseline(doc["baseline"])}


# ---------------------------------------------------------------------------
# The folder
# ---------------------------------------------------------------------------


def _refuse_link(path: Path, what: str) -> None:
    if os.path.lexists(path) and _is_reparse_point(path):
        raise ProjectError(
            "%s is a link, not a %s this server made; the lore bible will "
            "not read or write through it. Remove the link, or delete it and "
            "let lore_bible make the folder again." % (path, what))


def lore_dir(project: Any, create: bool = False) -> Path:
    """The lore folder, judged to lie inside the project before it is touched.

    Checked before it is made: a link planted as the work folder or the lore
    folder would otherwise carry the `mkdir`, and then the write, elsewhere.
    """
    work = project.work_dir()
    folder = work / LORE_DIR
    _refuse_link(work, "folder")
    _refuse_link(folder, "folder")
    _contained(work, project.root, "the work folder")
    _contained(folder, work, "the lore folder")
    if create:
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def _unusable(why: str) -> ProjectError:
    return ProjectError(
        "%s is not usable: %s. It was edited by hand or made by something "
        "other than lore_bible. Fix it, or delete it to start the bible "
        "again (nothing in the game changes either way)." % (BIBLE_FILE, why))


def _no_duplicates(pairs: "list[tuple[str, Any]]") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("the JSON object repeats the key %s"
                             % brief(key, 40))
        out[key] = value
    return out


def _refuse_constant(token: str) -> Any:
    raise ValueError("%s is not valid JSON" % token)


def load(project: Any) -> dict[str, Any]:
    """The bible on disk, validated; an empty one if there is none yet."""
    folder = lore_dir(project)
    path = folder / BIBLE_FILE
    if not os.path.lexists(path):
        return new_bible()
    _refuse_link(path, "file")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise _unusable("it could not be read (%s)" % brief(str(exc), 80)) \
            from exc
    if size > MAX_BIBLE_BYTES:
        raise _unusable("it is %d bytes, above the %d bytes a bible may hold"
                        % (size, MAX_BIBLE_BYTES))
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_BIBLE_BYTES + 1)
    except OSError as exc:
        raise _unusable("it could not be read (%s)" % brief(str(exc), 80)) \
            from exc
    if len(raw) > MAX_BIBLE_BYTES:
        raise _unusable("it grew past %d bytes while being read"
                        % MAX_BIBLE_BYTES)
    try:
        document = json.loads(raw.decode("utf-8-sig"),
                              object_pairs_hook=_no_duplicates,
                              parse_constant=_refuse_constant)
    except UnicodeDecodeError:
        raise _unusable("it is not UTF-8 text") from None
    except (ValueError, RecursionError) as exc:
        raise _unusable("it is not valid JSON (%s)"
                        % brief(str(exc), 80)) from exc
    try:
        return clean(document)
    except BibleError as exc:
        raise _unusable(str(exc)) from exc


def save(project: Any, bible: dict[str, Any]) -> Path:
    """Write the bible, atomically, after checking it is one."""
    project._assert_writable("saving the lore bible")
    try:
        document = clean(bible)
    except BibleError as exc:
        raise ProjectError("the bible was not saved, because it would not be "
                           "a usable one: %s" % exc) from exc
    body = (json.dumps(document, ensure_ascii=False, indent=1) + "\n"
            ).encode("utf-8")
    if len(body) > MAX_BIBLE_BYTES:
        raise ProjectError(
            "the bible would be %d bytes, above the %d bytes it may hold, so "
            "it was not saved and the earlier one stands. Shorten some "
            "answers or entries, or delete entries that are no longer needed."
            % (len(body), MAX_BIBLE_BYTES))
    folder = lore_dir(project, create=True)
    path = folder / BIBLE_FILE
    _refuse_link(path, "file")
    write_bytes_atomic(path, body)
    return path


def write_exports(project: Any, files: "dict[str, str]") -> dict[str, Path]:
    """Write export files into the lore folder, atomically, all or none.

    Every name, size and destination is judged before the first byte is
    written, so a link where the second file goes does not leave the first
    rewritten and the pair out of step.
    """
    bodies: "dict[str, bytes]" = {}
    for name, text in files.items():
        if name not in EXPORT_FILES:
            raise ProjectError(
                "%s is not a file lore_bible writes; it writes %s"
                % (brief(name, 40), " and ".join(EXPORT_FILES)))
        body = text.encode("utf-8")
        if len(body) > _EXPORT_BYTES_UPPER:
            raise ProjectError(
                "the export %s would be %d bytes, above the %d this server "
                "writes" % (name, len(body), _EXPORT_BYTES_UPPER))
        bodies[name] = body
    folder = lore_dir(project, create=True)
    for name in bodies:
        _refuse_link(folder / name, "file")
    written: "dict[str, Path]" = {}
    for name, body in bodies.items():
        path = folder / name
        write_bytes_atomic(path, body)
        written[name] = path
    return written


def write_export(project: Any, name: str, text: str) -> Path:
    """Write one of the export files; see :func:`write_exports`."""
    return write_exports(project, {name: text})[name]
