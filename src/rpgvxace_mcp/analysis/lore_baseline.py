"""What a project already says: the baseline `lore_bible` starts from.

The scan answers one question for an author who is about to build the lore of
a game: what is on the page already, and where is it thin? It walks the places
the player reads words (Show Text, choices and scrolling text in map events,
common events and troop pages; map display names; database names,
descriptions and notes; the System table's title and terms) and reduces them
to a bounded, deterministic summary: how much text of each kind, which
capitalised names recur and whether anything explains them, who speaks, how
the maps are named and laid out, and a ranked list of plain-language gaps.

Nothing here edits anything, and nothing here is a verdict. A name that
recurs and is "unexplained" is a prompt to ask the author; a map with the
default name is a fact. The suggestions rest on word counts and are marked as
rule-of-thumb estimates.

What counts as text is the same set `text` exports, on the same engines (VX
Ace and VX: XP keeps its first Show Text line in the 101 itself and is refused
by the tool before it gets here). Comments are counted apart and never read as
prose, because they are the developer's notes.

How it is bounded
-----------------
The walk shares one `WalkBudget` (events, pages and commands are charged
before they are read, so Marshal links that point a million slots at one list
cost what they would if they were real). Stored bytes are charged before they
are decoded. Each string is analysed only up to `_LINE_CHARS`, so a
megabyte-long name is read for its length and its start. Every table that
grows with the project -- distinct words, phrases, speakers -- has a ceiling,
and the answer says when one was reached, in the one vocabulary a partial
answer uses (`walk_stopped`, `proper_nouns_truncated`, `files_not_read`).
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from ..commands import (
    CODE_COMMENT,
    CODE_COMMENT_LINE,
    CODE_SHOW_CHOICES,
    CODE_SHOW_TEXT,
    CODE_TEXT_LINE,
    MAX_CHOICES,
)
from ..convert import brief
from ..lore_store import (
    MAX_BASELINE_GAPS,
    MAX_BASELINE_NOUNS,
    MAX_BASELINE_SAMPLES,
    MAX_BASELINE_SAMPLE_CHARS,
    MAX_BASELINE_SPEAKERS,
    MAX_BASELINE_VARIANTS,
    MAX_REVIEW_FINDINGS,
    REVIEW_SCOPES,
)
from ..project import Project, ProjectError
from ..rubytypes import (
    RubyHash,
    RubyObject,
    RubyString,
    as_int,
    as_list,
    printable_text,
)
from .map_shape import map_shapes
from .walk import WalkBudget


# Stored text one scan will decode. Each string is charged before it is read,
# so a project whose links point many slots at one large string costs a
# bounded amount.
_TEXT_BYTES_UPPER = 16 * 1024 * 1024

# How much of one string is analysed. The rest is counted, not read.
_LINE_CHARS = 4096

# Distinct words, adjacent word pairs, capitalised phrases and speakers kept.
_VOCABULARY_UPPER = 200_000
_DISTINCT_WORDS_UPPER = 100_000
_PHRASES_UPPER = 20_000
_SPEAKERS_UPPER = 500
_VARIANT_CANDIDATES_UPPER = 300

# Names in a reply, and how many are listed for a question to quote.
_NAME_CHARS = 40
_NAMES_LISTED = 10
_WHERE_LISTED = 3
_FORMS_KEPT = 4

# How deep the map tree is followed before the depth is called this.
_DEPTH_UPPER = 64

# A name has to occur this often to be called recurring.
_RECURRING = 3


_MAP_ITEMS_UPPER = 20_000


# Map names a review compares with the bible.
_MAPS_NAMED_UPPER = 200


_SOURCES = ("dialogue", "choices", "scrolling", "comments", "map_names",
            "descriptions", "notes", "system_text")


_DB_TABLES = ("actors", "classes", "skills", "items", "weapons", "armors",
              "enemies", "states")


_SYSTEM_STRINGS = ("game_title", "currency_unit")
_SYSTEM_LISTS = ("skill_types", "weapon_types", "armor_types")
_TERM_FIELDS_UPPER = 200
_LIST_ELEMENTS_UPPER = 1000


_CODE_SCROLL = 105
_CODE_SCROLL_LINE = 405


_TRIGGER_BUCKET = {0: "action", 1: "touch", 2: "touch", 3: "scene",
                   4: "scene"}


_DEFAULT_MAP = re.compile(r"MAP\d{3}\Z", re.IGNORECASE)
_DEFAULT_EVENT = re.compile(r"EV\d{3}\Z", re.IGNORECASE)


# The editor's text codes: \V[1], \N[2], \C[3], \G, \{ \} \\ \$ \. \| \! \> \< \^
_CODE = re.compile(r"\\[A-Za-z]+(?:\[\d*\])?|\\[{}\\$.|!><^]")
_WORD = re.compile(r"[^\W\d_][\w'\u2019\-]*")
_POSSESSIVE = re.compile("['\u2019][sS]\\Z")

# Capitalised words that are not names however they sit in a sentence.
_NOT_NAMES = frozenset({"i", "im", "ill", "ive", "id", "oh", "ah", "hey",
                        "yes", "no", "okay", "ok", "mr", "mrs", "ms", "dr"})


_SENTENCE_END = frozenset(".!?\u2026")


class _Halt(Exception):
    """The scan cannot go on: the walk or the byte budget ran out."""


def _skeleton(text: str) -> str:
    """The letters and digits of a word, without case, accents or marks.

    Two spellings that differ only in case, diacritics, spacing or hyphens
    share a skeleton, which is how they are noticed and how a name is looked
    up regardless of how it was typed.
    """
    stripped = unicodedata.normalize("NFKD", text)
    return "".join(char for char in stripped.casefold()
                   if char.isalnum() and not unicodedata.combining(char))


def _clip(text: str, limit: int = _NAME_CHARS) -> str:
    """A name or sample for a reply: one line, printable, bounded."""
    text = printable_text(" ".join(text.split()))
    if len(text) > limit:
        return text[:limit - 3] + "..."
    return text


class _Noun:
    """What the scan saw of one word that is sometimes capitalised."""

    __slots__ = ("mid", "init", "lower", "forms", "where")

    def __init__(self) -> None:
        self.mid = 0
        self.init = 0
        self.lower = 0
        self.forms: dict[str, int] = {}
        self.where: list[str] = []


class _Speaker:
    __slots__ = ("lines", "words", "samples")

    def __init__(self) -> None:
        self.lines = 0
        self.words = 0
        self.samples: list[str] = []


class LoreCorpus:
    """Everything one walk of a project found; `baseline_of` reduces it."""

    def __init__(self, engine: str = "") -> None:
        self.engine = engine
        self.scanned_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds")
        self.volume = {name: [0, 0] for name in _SOURCES}
        self.tables: dict[str, list[int]] = {}
        self.map_count = 0
        self.map_default_named = 0
        self.map_no_display = 0
        self.map_has_display_field = False
        self.map_blank = 0
        self.map_without_events = 0
        self.map_depth = 0
        self.map_tree_names: list[str] = []
        self.maps_named: list[str] = []
        self.events_total = 0
        self.events_speaking = 0
        self.events_default_speaking = 0
        self.speaking_names: list[str] = []
        self.boxes = {"action": 0, "touch": 0, "scene": 0}
        self.actors: list[str] = []
        self.nouns: dict[str, _Noun] = {}
        self.phrases: dict[str, dict[str, int]] = {}
        self.speakers: dict[str, _Speaker] = {}
        self.token_set: set[str] = set()
        self.pair_set: set[str] = set()
        self.name_tokens: set[str] = set()
        self.map_names: set[str] = set()
        self.person_names: set[str] = set()
        self.event_names: set[str] = set()
        self.described: set[str] = set()
        self.vocabulary_truncated = False
        self.nouns_truncated = False
        self.speakers_truncated = False
        self.phrases_truncated = False
        self.stopped: list[str] = []
        self.not_read: dict[str, str] = {}


class _Scanner:
    """One walk. Holds the budget, the byte allowance and the corpus."""

    def __init__(self, project: Project, corpus: LoreCorpus,
                 budget: WalkBudget) -> None:
        self.project = project
        self.codec = project.codec
        self.corpus = corpus
        self.budget = budget
        self.bytes_left = _TEXT_BYTES_UPPER

    # -- running ---------------------------------------------------------------
    def run(self) -> None:
        try:
            self._system()
            self._database()
            self._map_tree()
            self._maps()
            self._common_events()
            self._troops()
        except _Halt:
            pass
        if not self.budget.exhausted:
            self._shapes()

    def _step(self, count: int = 1) -> None:
        if not self.budget.spend(count):
            self.corpus.stopped.append(self.budget.note("the lore scan") or "")
            raise _Halt

    def _charge(self, size: int) -> None:
        self.bytes_left -= size
        if self.bytes_left < 0:
            self.corpus.stopped.append(
                "the stored text read passed %d bytes, so the scan stopped "
                "early and this answer is incomplete" % _TEXT_BYTES_UPPER)
            raise _Halt

    def _unread(self, label: str, exc: Exception) -> None:
        if len(self.corpus.not_read) < 50:
            self.corpus.not_read[label] = brief(str(exc), 120)

    # -- strings ---------------------------------------------------------------
    def _plain(self, value: Any) -> str:
        """A project string as clean text, bounded; "" for anything else."""
        if not isinstance(value, RubyString):
            return ""
        raw = value.data
        self._charge(len(raw))
        if not raw:
            return ""
        text = self.codec.decode(raw[:_LINE_CHARS * 4])[:_LINE_CHARS]
        return printable_text(_CODE.sub(" ", text))

    def _count(self, source: str, text: str) -> None:
        if text.strip():
            row = self.corpus.volume[source]
            row[0] += 1
            row[1] += len(text.split())

    # -- vocabulary ------------------------------------------------------------
    def _remember(self, token: str, previous: "str | None") -> None:
        corpus = self.corpus
        if len(corpus.token_set) + len(corpus.pair_set) >= _VOCABULARY_UPPER:
            corpus.vocabulary_truncated = True
            return
        corpus.token_set.add(token)
        if previous is not None:
            corpus.pair_set.add(previous + " " + token)

    def _vocabulary(self, text: str, *, name: bool = False,
                    described: bool = False) -> None:
        """Words that only belong to the vocabulary: names, descriptions."""
        previous = None
        last_end = 0
        for match in _WORD.finditer(text):
            gap = text[last_end:match.start()]
            last_end = match.end()
            token = _skeleton(_POSSESSIVE.sub("", match.group().rstrip("'\u2019-")))
            if not token:
                continue
            self._remember(token, previous if gap.strip(" \t\r\n") == "" else None)
            previous = token
            if name:
                self.corpus.name_tokens.add(token)
            if described:
                self.corpus.described.add(token)

    def _prose(self, text: str, where: str) -> None:
        """Count a stretch of prose: nouns, vocabulary, phrases."""
        corpus = self.corpus
        previous_token = None
        previous_cap: "tuple[str, str] | None" = None
        last_end = 0
        first = True
        for match in _WORD.finditer(text):
            gap = text[last_end:match.start()]
            last_end = match.end()
            surface = _POSSESSIVE.sub("", match.group().rstrip("'\u2019-"))
            key = _skeleton(surface)
            if not key:
                continue
            joined = gap.strip(" \t\r\n") == ""
            self._remember(key, previous_token if joined else None)
            previous_token = key
            sentence_start = first or any(c in _SENTENCE_END for c in gap)
            first = False
            shouting = len(surface) > 1 and surface.isupper()
            capital = surface[0].isupper() and not shouting
            noun = corpus.nouns.get(key)
            if noun is None:
                if len(corpus.nouns) >= _DISTINCT_WORDS_UPPER:
                    corpus.nouns_truncated = True
                    previous_cap = None
                    continue
                noun = corpus.nouns[key] = _Noun()
            if shouting:
                previous_cap = None
                continue
            if not capital:
                noun.lower += 1
                previous_cap = None
                continue
            if sentence_start:
                noun.init += 1
                previous_cap = None
                continue
            noun.mid += 1
            if surface in noun.forms or len(noun.forms) < _FORMS_KEPT:
                noun.forms[surface] = noun.forms.get(surface, 0) + 1
            if where not in noun.where and len(noun.where) < _WHERE_LISTED:
                noun.where.append(where)
            if previous_cap is not None and gap == " ":
                self._phrase(previous_cap[1] + " " + surface)
            previous_cap = (key, surface)

    def _phrase(self, phrase: str) -> None:
        corpus = self.corpus
        key = _skeleton(phrase)
        forms = corpus.phrases.get(key)
        if forms is None:
            if len(corpus.phrases) >= _PHRASES_UPPER:
                corpus.phrases_truncated = True
                return
            forms = corpus.phrases[key] = {}
        if phrase in forms or len(forms) < _FORMS_KEPT:
            forms[phrase] = forms.get(phrase, 0) + 1

    # -- the System table ------------------------------------------------------
    def _system(self) -> None:
        project = self.project
        if "system" not in project.data_files or \
                not project.exists(project.filename_for("system")):
            return
        try:
            system = project.load_data("system")
        except (ProjectError, ValueError, OSError) as exc:
            self._unread("system", exc)
            return
        if not isinstance(system, RubyObject):
            return
        self._step()
        for field in _SYSTEM_STRINGS:
            self._system_string(system.get(field))
        for field in _SYSTEM_LISTS:
            self._system_list(system.get(field))
        terms = system.get("terms")
        if isinstance(terms, RubyObject):
            for field in terms.fields()[:_TERM_FIELDS_UPPER]:
                self._step()
                value = terms.get(field)
                if isinstance(value, list):
                    self._system_list(value)
                else:
                    self._system_string(value)

    def _system_string(self, value: Any) -> None:
        text = self._plain(value)
        if text.strip():
            self._count("system_text", text)
            self._vocabulary(text)

    def _system_list(self, values: Any) -> None:
        for value in as_list(values)[:_LIST_ELEMENTS_UPPER]:
            self._step()
            self._system_string(value)

    # -- the database ----------------------------------------------------------
    def _database(self) -> None:
        project = self.project
        for table in _DB_TABLES:
            if table not in project.data_files or \
                    not project.exists(project.filename_for(table)):
                continue
            try:
                entries = project.entries(table)
            except (ProjectError, ValueError, OSError) as exc:
                self._unread(table, exc)
                continue
            row = [0, 0, 0]
            for _entry_id, entry in entries:
                self._step()
                if not isinstance(entry, RubyObject):
                    continue
                name = self._plain(entry.get("name"))
                if not name.strip():
                    continue
                self._vocabulary(name, name=True)
                if table in ("actors", "enemies"):
                    self.corpus.person_names.add(_skeleton(name))
                if table == "actors" and len(self.corpus.actors) < _NAMES_LISTED:
                    self.corpus.actors.append(_clip(name))
                if entry.has("description"):
                    row[0] += 1
                    text = self._plain(entry.get("description"))
                    if text.strip():
                        self._count("descriptions", text)
                        self._vocabulary(text, described=True)
                    else:
                        row[1] += 1
                profile = self._plain(entry.get("profile")) \
                    if entry.has("profile") else ""
                if profile.strip():
                    self._count("descriptions", profile)
                    self._vocabulary(profile, described=True)
                note = self._plain(entry.get("note")) \
                    if entry.has("note") else ""
                if note.strip():
                    row[2] += 1
                    self._count("notes", note)
                    self._vocabulary(note, described=True)
            if row[0]:
                self.corpus.tables[table] = row

    # -- the map tree ----------------------------------------------------------
    def _map_tree(self) -> None:
        project = self.project
        corpus = self.corpus
        ids = project.map_ids_on_disk()
        corpus.map_count = len(ids)
        try:
            entries = project.entries("map_infos")
        except (ProjectError, ValueError, OSError) as exc:
            self._unread("map tree", exc)
            return
        on_disk = set(ids)
        parents: dict[int, int] = {}
        names: dict[int, str] = {}
        for map_id, info in entries[:_MAP_ITEMS_UPPER]:
            self._step()
            number = as_int(map_id)
            if number is None or number not in on_disk \
                    or not isinstance(info, RubyObject):
                continue
            names[number] = self._plain(info.get("name"))
            parents[number] = as_int(info.get("parent_id")) or 0
        for number in sorted(names):
            name = names[number]
            if _DEFAULT_MAP.match(name.strip()):
                corpus.map_default_named += 1
            elif name.strip():
                if len(corpus.map_tree_names) < _NAMES_LISTED:
                    corpus.map_tree_names.append(_clip(name))
                if len(corpus.maps_named) < _MAPS_NAMED_UPPER:
                    corpus.maps_named.append(_clip(name, 60))
                self._vocabulary(name, name=True)
                corpus.map_names.add(_skeleton(name))
        depth = 0
        for number in sorted(parents):
            seen = {number}
            here = 1
            parent = parents[number]
            while parent in parents and parent not in seen \
                    and here < _DEPTH_UPPER:
                seen.add(parent)
                here += 1
                parent = parents[parent]
            depth = max(depth, here)
        corpus.map_depth = depth

    # -- maps and events -------------------------------------------------------
    def _maps(self) -> None:
        project = self.project
        corpus = self.corpus
        for map_id in project.map_ids_on_disk():
            self._step()
            try:
                game_map = project.load_map(map_id)
            except (ProjectError, ValueError, OSError) as exc:
                self._unread("map %d" % map_id, exc)
                continue
            if game_map.has("display_name"):
                corpus.map_has_display_field = True
                text = self._plain(game_map.get("display_name"))
                if text.strip():
                    self._count("map_names", text)
                    self._vocabulary(text, name=True)
                    corpus.map_names.add(_skeleton(text))
                else:
                    corpus.map_no_display += 1
            events = game_map.get("events")
            if isinstance(events, RubyHash):
                self._events(map_id, events)

    def _events(self, map_id: int, events: RubyHash) -> None:
        corpus = self.corpus
        for event_id, event in events.sorted_items():
            self._step()
            number = as_int(event_id)
            if number is None or not 0 <= number < 1_000_000 \
                    or not isinstance(event, RubyObject):
                continue
            corpus.events_total += 1
            name = self._plain(event.get("name")).strip()
            default = bool(_DEFAULT_EVENT.match(name)) or not name
            if not default:
                self._vocabulary(name, name=True)
                corpus.event_names.add(_skeleton(name))
            spoke = False
            for page_index, page in enumerate(as_list(event.get("pages"))):
                self._step()
                if not isinstance(page, RubyObject):
                    continue
                where = "map %d event %d (%s) page %d" % (
                    map_id, number, _clip(name), page_index)
                bucket = _TRIGGER_BUCKET.get(as_int(page.get("trigger")),
                                             "action")
                boxes = self._commands(as_list(page.get("list")), where,
                                       "" if default else name, bucket)
                spoke = spoke or boxes > 0
            if spoke:
                corpus.events_speaking += 1
                if default:
                    corpus.events_default_speaking += 1
                elif len(corpus.speaking_names) < _NAMES_LISTED:
                    corpus.speaking_names.append(_clip(name))

    def _common_events(self) -> None:
        project = self.project
        if "common_events" not in project.data_files or \
                not project.exists(project.filename_for("common_events")):
            return
        try:
            entries = project.entries("common_events")
        except (ProjectError, ValueError, OSError) as exc:
            self._unread("common events", exc)
            return
        for entry_id, entry in entries:
            self._step()
            number = as_int(entry_id)
            if number is None or not isinstance(entry, RubyObject):
                continue
            self._commands(as_list(entry.get("list")),
                           "common event %d" % number, "", None)

    def _troops(self) -> None:
        project = self.project
        if "troops" not in project.data_files or \
                not project.exists(project.filename_for("troops")):
            return
        try:
            entries = project.entries("troops")
        except (ProjectError, ValueError, OSError) as exc:
            self._unread("troops", exc)
            return
        for entry_id, entry in entries:
            self._step()
            number = as_int(entry_id)
            if number is None or not isinstance(entry, RubyObject):
                continue
            for page_index, page in enumerate(as_list(entry.get("pages"))):
                self._step()
                if isinstance(page, RubyObject):
                    self._commands(as_list(page.get("list")),
                                   "troop %d battle page %d"
                                   % (number, page_index), "", None)

    # -- command lists ---------------------------------------------------------
    def _commands(self, commands: "list[Any]", where: str, event_name: str,
                  bucket: "str | None") -> int:
        """Read one command list; the number of dialogue boxes it held."""
        affordable = self.budget.take(len(commands))
        boxes = 0
        index = 0
        while index < affordable:
            command = commands[index]
            code = as_int(command.get("code")) \
                if isinstance(command, RubyObject) else None
            if code == CODE_SHOW_TEXT:
                index, lines = self._group(commands, affordable, index,
                                           CODE_TEXT_LINE)
                if lines:
                    boxes += 1
                    self._box(command, lines, where, event_name, bucket)
            elif code == _CODE_SCROLL:
                index, lines = self._group(commands, affordable, index,
                                           _CODE_SCROLL_LINE)
                text = "\n".join(lines)
                if text.strip():
                    self._count("scrolling", text)
                    self._prose(text, where)
            elif code == CODE_COMMENT:
                first = self._plain(as_list(command.get("parameters"))[0]) \
                    if as_list(command.get("parameters")) else ""
                index, lines = self._group(commands, affordable, index,
                                           CODE_COMMENT_LINE)
                self._count("comments", "\n".join([first] + lines))
            elif code == CODE_SHOW_CHOICES:
                self._choices(command, where)
                index += 1
            else:
                index += 1
        if affordable < len(commands):
            self.corpus.stopped.append(self.budget.note("the lore scan") or "")
            raise _Halt
        return boxes

    def _group(self, commands: "list[Any]", end: int, index: int,
               continuation: int) -> "tuple[int, list[str]]":
        """The lines after a head command, and the index after them."""
        lines: "list[str]" = []
        after = index + 1
        while after < end:
            line = commands[after]
            if not (isinstance(line, RubyObject)
                    and as_int(line.get("code")) == continuation):
                break
            params = as_list(line.get("parameters"))
            lines.append(self._plain(params[0]) if params else "")
            after += 1
        if not any(line.strip() for line in lines):
            return after, []
        return after, lines

    def _box(self, head: RubyObject, lines: "list[str]", where: str,
             event_name: str, bucket: "str | None") -> None:
        corpus = self.corpus
        text = "\n".join(lines)
        self._count("dialogue", text)
        self._prose(text, where)
        if bucket is not None:
            corpus.boxes[bucket] += 1
        params = as_list(head.get("parameters"))
        face = self._plain(params[0]).strip() if params else ""
        index = as_int(params[1]) if len(params) > 1 else None
        if face:
            label = "face %s #%d" % (_clip(face), index or 0)
        elif event_name:
            label = "event " + _clip(event_name)
        else:
            label = "unattributed"
        speaker = corpus.speakers.get(label)
        if speaker is None:
            if len(corpus.speakers) >= _SPEAKERS_UPPER:
                corpus.speakers_truncated = True
                return
            speaker = corpus.speakers[label] = _Speaker()
        speaker.lines += 1
        speaker.words += len(text.split())
        if len(speaker.samples) < MAX_BASELINE_SAMPLES:
            speaker.samples.append(_clip(text, MAX_BASELINE_SAMPLE_CHARS))

    def _choices(self, command: RubyObject, where: str) -> None:
        params = as_list(command.get("parameters"))
        for choice in as_list(params[0] if params else None)[:MAX_CHOICES]:
            text = self._plain(choice)
            if text.strip():
                self._count("choices", text)
                self._prose(text, where)

    # -- the shape of the maps -------------------------------------------------
    def _shapes(self) -> None:
        corpus = self.corpus
        shapes = map_shapes(self.project, self.budget)
        for row in shapes["maps"]:
            if row.get("blank") is True:
                corpus.map_blank += 1
            if not row.get("event_count"):
                corpus.map_without_events += 1
        if "dead_space_stopped" in shapes:
            corpus.stopped.append(shapes["dead_space_stopped"])


def lore_scan(project: Project, budget: "WalkBudget | None" = None
              ) -> LoreCorpus:
    """Walk the project once and keep what a baseline and a review need."""
    corpus = LoreCorpus(project.engine)
    _Scanner(project, corpus,
             WalkBudget() if budget is None else budget).run()
    return corpus


# ---------------------------------------------------------------------------
# From the walk to the baseline
# ---------------------------------------------------------------------------


def _is_name(noun: _Noun, key: str) -> bool:
    return noun.mid >= 1 and noun.mid > noun.lower and key not in _NOT_NAMES


def _surface(noun: _Noun) -> str:
    """The form of a name seen most often; the first alphabetically on a tie."""
    return sorted(noun.forms.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _names(corpus: LoreCorpus) -> "list[tuple[str, _Noun]]":
    found = [(key, noun) for key, noun in corpus.nouns.items()
             if _is_name(noun, key) and noun.forms]
    found.sort(key=lambda pair: (-(pair[1].mid + pair[1].init),
                                 _surface(pair[1])))
    return found


def _one_edit(left: str, right: str) -> bool:
    """Whether two different strings are one insertion, deletion or change apart."""
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    index = 0
    while index < len(left) and left[index] == right[index]:
        index += 1
    if len(left) == len(right):
        return left[index + 1:] == right[index + 1:]
    return left[index:] == right[index + 1:]


def _variants(corpus: LoreCorpus, names: "list[tuple[str, _Noun]]"
              ) -> "list[list[str]]":
    """Pairs of names that are probably one name spelled two ways."""
    pairs: "dict[tuple[str, str], int]" = {}

    def add(first: str, second: str, weight: int) -> None:
        if first != second:
            pair = (first, second) if first < second else (second, first)
            pairs[pair] = max(pairs.get(pair, 0), weight)

    for _key, noun in names:
        forms = sorted(noun.forms)
        for left, right in zip(forms, forms[1:]):
            add(left, right, noun.mid + noun.init)
    by_key = dict(names)
    for key, forms in corpus.phrases.items():
        noun = by_key.get(key)
        if noun is None:
            continue
        for phrase in forms:
            for single in noun.forms:
                add(phrase, single, noun.mid + noun.init)
    shortlist = names[:_VARIANT_CANDIDATES_UPPER]
    for position, (left_key, left) in enumerate(shortlist):
        if len(left_key) < 5:
            continue
        for right_key, right in shortlist[position + 1:]:
            if len(right_key) >= 5 and _one_edit(left_key, right_key):
                add(_surface(left), _surface(right),
                    min(left.mid + left.init, right.mid + right.init))
    ordered = sorted(pairs.items(), key=lambda item: (-item[1], item[0]))
    return [list(pair) for pair, _weight in ordered]


def _share(part: int, whole: int, places: int = 3) -> "float | None":
    return round(part / whole, places) if whole else None


def baseline_of(corpus: LoreCorpus) -> dict[str, Any]:
    """The bounded, deterministic summary of a walk, with its ranked gaps."""
    names = _names(corpus)
    nouns = [{"name": _surface(noun), "count": noun.mid + noun.init,
              "explained": key in corpus.described,
              "where": list(noun.where)}
             for key, noun in names[:MAX_BASELINE_NOUNS]]
    variants = _variants(corpus, names)
    speakers = sorted(corpus.speakers.items(),
                      key=lambda item: (-item[1].lines, item[0]))
    entries = sum(row[0] for row in corpus.tables.values())
    empty = sum(row[1] for row in corpus.tables.values())
    boxes = corpus.boxes
    placed = sum(boxes.values())
    baseline: dict[str, Any] = {
        "scanned_at": corpus.scanned_at,
        "engine": corpus.engine,
        "volume": {source: {"count": row[0], "words": row[1]}
                   for source, row in corpus.volume.items()},
        "database": {
            "entries": entries, "empty_description": empty,
            "empty_share": _share(empty, entries),
            "tables": {table: {"entries": row[0], "empty_description": row[1],
                               "with_note": row[2]}
                       for table, row in corpus.tables.items()}},
        "maps": {
            "count": corpus.map_count,
            "default_named": corpus.map_default_named,
            "no_display_name": (corpus.map_no_display
                                if corpus.map_has_display_field else None),
            "blank": corpus.map_blank,
            "max_depth": corpus.map_depth,
            "events_total": corpus.events_total,
            "events_per_map": (round(corpus.events_total / corpus.map_count, 1)
                               if corpus.map_count else None),
            "maps_without_events": corpus.map_without_events},
        "events": {
            "total": corpus.events_total,
            "speaking": corpus.events_speaking,
            "default_named_speaking": corpus.events_default_speaking,
            "dialogue_boxes_on_action_button": boxes["action"],
            "dialogue_boxes_on_touch": boxes["touch"],
            "dialogue_boxes_autorun_or_parallel": boxes["scene"],
            "action_button_share": _share(boxes["action"], placed, 2)},
        "proper_nouns": nouns,
        "spelling_variants": variants[:MAX_BASELINE_VARIANTS],
        "speakers": [
            {"speaker": label, "lines": speaker.lines,
             "avg_words": round(speaker.words / speaker.lines, 1),
             "samples": list(speaker.samples)}
            for label, speaker in speakers[:MAX_BASELINE_SPEAKERS]],
        "names": {"actors": list(corpus.actors),
                  "maps": list(corpus.map_tree_names),
                  "speaking_events": list(corpus.speaking_names)},
    }
    baseline["gaps"] = _gaps(baseline)[:MAX_BASELINE_GAPS]
    if corpus.stopped:
        baseline["walk_stopped"] = " ".join(
            note for note in dict.fromkeys(corpus.stopped) if note)
    if len(names) > MAX_BASELINE_NOUNS:
        baseline["proper_nouns_truncated"] = True
    if len(variants) > MAX_BASELINE_VARIANTS:
        baseline["spelling_variants_truncated"] = True
    if len(speakers) > MAX_BASELINE_SPEAKERS or corpus.speakers_truncated:
        baseline["speakers_truncated"] = True
    if corpus.vocabulary_truncated or corpus.nouns_truncated \
            or corpus.phrases_truncated:
        baseline["vocabulary_truncated"] = True
    if corpus.not_read:
        baseline["files_not_read"] = dict(corpus.not_read)
    return baseline


# What a gap is worth when ranking, highest first. The order is a judgement
# about what an author most needs to hear first, not a measurement.
_GAP_RANK = {"no_text": 100, "unexplained_name": 80,
             "default_named_speakers": 70, "default_named_maps": 60,
             "empty_descriptions": 50, "blank_maps": 45,
             "no_display_names": 40, "unattributed": 30,
             "spelling": 25}


def _gaps(baseline: dict[str, Any]) -> "list[dict[str, str]]":
    gaps: "list[dict[str, str]]" = []

    def add(kind: str, text: str) -> None:
        gaps.append({"kind": kind, "text": text})

    volume = baseline["volume"]
    spoken = (volume["dialogue"]["count"] + volume["choices"]["count"]
              + volume["scrolling"]["count"] + volume["descriptions"]["count"])
    if spoken == 0:
        add("no_text", "There is no player-visible text yet (no dialogue, "
                       "choices, scrolling text or database descriptions), so "
                       "the bible can start from a blank page.")
    recurring = [n for n in baseline["proper_nouns"]
                 if n["count"] >= _RECURRING and not n["explained"]]
    if recurring:
        top = recurring[0]
        text = ("The name %s appears %d times in the game text and no "
                "database description or note mentions it."
                % (top["name"], top["count"]))
        if len(recurring) > 1:
            text += " %d other recurring names are in the same position (%s)." \
                    % (len(recurring) - 1,
                       ", ".join(n["name"] for n in recurring[1:4]))
        add("unexplained_name", text)
    events = baseline["events"]
    if events["default_named_speaking"]:
        add("default_named_speakers",
            "%d events that speak still have the default name (EV001 "
            "style), so their lines cannot be tied to a character."
            % events["default_named_speaking"])
    maps = baseline["maps"]
    if maps["default_named"]:
        add("default_named_maps", "%d of the %d maps still have the default "
                                  "name (MAP001 style)."
            % (maps["default_named"], maps["count"]))
    database = baseline["database"]
    if database["entries"] >= 4 and (database["empty_share"] or 0) >= 0.5:
        worst = sorted(database["tables"].items(),
                       key=lambda item: (-item[1]["empty_description"],
                                         item[0]))[:3]
        add("empty_descriptions",
            "%d of the %d database entries that can carry a description have "
            "none (%s)." % (database["empty_description"], database["entries"],
                            ", ".join("%s %d of %d" % (
                                table, row["empty_description"], row["entries"])
                                for table, row in worst
                                if row["empty_description"])))
    if maps["blank"]:
        add("blank_maps", "%d maps are blank: no tiles and no events."
            % maps["blank"])
    if maps["no_display_name"]:
        add("no_display_names",
            "%d of the %d maps have no display name, so the player never "
            "sees a place name there." % (maps["no_display_name"],
                                          maps["count"]))
    boxes = volume["dialogue"]["count"]
    unattributed = next((s["lines"] for s in baseline["speakers"]
                         if s["speaker"] == "unattributed"), 0)
    if boxes >= 10 and unattributed * 2 >= boxes:
        add("unattributed", "%d of the %d dialogue boxes have no face graphic "
                            "and no named event, so they cannot be tied to a "
                            "speaker." % (unattributed, boxes))
    if baseline["spelling_variants"]:
        add("spelling", "These may be one name spelled two ways: %s."
            % "; ".join(" / ".join(pair) for pair in
                        baseline["spelling_variants"][:3]))
    gaps.sort(key=lambda gap: -_GAP_RANK[gap["kind"]])
    return gaps


# ---------------------------------------------------------------------------
# Reading a stored baseline, which may be anything
# ---------------------------------------------------------------------------


def _dig(value: Any, *path: str) -> Any:
    """A value from nested dicts, or None at the first thing that is not one."""
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _whole(value: Any) -> int:
    """A count out of a stored baseline: a non-negative whole number, else 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    if isinstance(value, float):
        if not math.isfinite(value):
            return 0
        value = int(value)
    return min(max(value, 0), 10 ** 9)


def suggest_tier(baseline: Any) -> dict[str, Any]:
    """The tier a baseline points to, and why, as a rule-of-thumb estimate.

    From how much story text the game already has. It is a prompt, not a
    verdict: a small game can want a deep bible, and the author chooses.
    """
    if not isinstance(baseline, dict) or not baseline:
        return {"tier": None,
                "why": "No scan is saved yet, so there is nothing to base a "
                       "suggestion on: run op=scan first."}
    words = (_whole(_dig(baseline, "volume", "dialogue", "words"))
             + _whole(_dig(baseline, "volume", "scrolling", "words")))
    maps = _whole(_dig(baseline, "maps", "count"))
    if words < 2000:
        tier = "light"
    elif words < 15000:
        tier = "moderate"
    elif words < 60000:
        tier = "rich"
    else:
        tier = "deep"
    return {"tier": tier,
            "why": "Your game has about %d words of dialogue and scrolling "
                   "text across %d maps, which as a rough rule of thumb (an "
                   "estimate, not a rule) fits the %s tier." % (words, maps,
                                                                tier)}


# ---------------------------------------------------------------------------
# What the questions can quote
# ---------------------------------------------------------------------------


def _strings(value: Any, limit: int = 6) -> "list[str]":
    """Short printable strings out of a stored list; anything else is dropped."""
    if not isinstance(value, list):
        return []
    out: "list[str]" = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(_clip(item))
            if len(out) >= limit:
                break
    return out


def _rows(value: Any) -> "list[dict[str, Any]]":
    return [row for row in value if isinstance(row, dict)] \
        if isinstance(value, list) else []


_HOOK_CHARS = 400


def _fraction(value: Any) -> float:
    """A stored average: a finite, non-negative number, else 0."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    if isinstance(value, float) and not math.isfinite(value):
        return 0.0
    return float(min(max(value, 0), 10 ** 6))


# Where an unexplained name is offered, in order. Each question gets a
# different name, so three different questions are not all about one word.
_NAME_QUESTIONS = ("world.recent_past", "conflict.secret", "world.difference")


def question_hooks(baseline: Any) -> dict[str, str]:
    """A line per question that the game itself can give: "from your game".

    Every name and number is read from a stored baseline defensively, because
    that file is the author's and may have been edited: a field of the wrong
    type is skipped, a name is clipped and made printable, and a line is
    capped. The result is a prompt to quote when asking, never an answer.
    """
    hooks: dict[str, str] = {}
    if not isinstance(baseline, dict):
        return hooks
    actors = _strings(_dig(baseline, "names", "actors"))
    if actors:
        hooks["cast.party"] = ("Your project already lists these actors: %s. "
                               "Are they your party?" % ", ".join(actors))
    places = _strings(_dig(baseline, "names", "maps"))
    if places:
        hooks["world.places"] = (
            "Your map tree names these places: %s. Which are the main "
            "places, and what does the player do in each?"
            % ", ".join(places))
    speakers = [row for row in _rows(baseline.get("speakers"))
                if isinstance(row.get("speaker"), str)]
    named = _strings(_dig(baseline, "names", "speaking_events"))
    if named:
        hooks["cast.npcs"] = (
            "These named events speak in your game: %s. Which are memorable "
            "NPCs, and what does each one know?" % ", ".join(named))
    if speakers:
        parts = ["%s (%d lines, %.1f words a box)" % (
            _clip(row["speaker"]), _whole(row.get("lines")),
            _fraction(row.get("avg_words"))) for row in speakers[:3]]
        hooks["tone.voices"] = ("Your most frequent speakers are %s. How "
                                "does each one sound?" % "; ".join(parts))
    unexplained = [row for row in _rows(baseline.get("proper_nouns"))
                   if isinstance(row.get("name"), str)
                   and row.get("explained") is False
                   and _whole(row.get("count")) >= 2]
    for question_id, row in zip(_NAME_QUESTIONS, unexplained):
        hooks[question_id] = (
            "The name %s appears %d times in your dialogue and nothing in "
            "the database explains it. What is it?"
            % (_clip(row["name"]), _whole(row.get("count"))))
    items = _dig(baseline, "database", "tables", "items")
    empty, total = (_whole(_dig(items, "empty_description")),
                    _whole(_dig(items, "entries")))
    if empty:
        hooks["delivery.item_text"] = (
            "%d of your %d items have no description. Which lore facts could "
            "live there?" % (empty, total))
    boxes = _whole(_dig(baseline, "volume", "dialogue", "count"))
    words = _whole(_dig(baseline, "volume", "dialogue", "words"))
    described = _whole(_dig(baseline, "volume", "descriptions", "count"))
    if boxes or described:
        hooks["delivery.channels"] = (
            "So far your text is %d dialogue boxes (%d words) and %d "
            "database descriptions. What mix do you want?"
            % (boxes, words, described))
    if boxes:
        hooks["delivery.reading_budget"] = (
            "Your dialogue boxes average %.1f words."
            % (round(words / boxes, 1)))
    maps = _whole(_dig(baseline, "maps", "count"))
    events = _whole(_dig(baseline, "maps", "events_total"))
    if maps:
        hooks["scope.length"] = (
            "Your project has %d maps, %d events and %d actors so far. How "
            "big should the finished game be?" % (maps, events, len(actors)))
    return {key: text[:_HOOK_CHARS] for key, text in hooks.items()}


# ---------------------------------------------------------------------------
# Review: the bible against the game's own words
# ---------------------------------------------------------------------------


# The word a finding kind falls under.
_FINDING_SCOPE = {"not_in_game": "bible_to_game",
                  "character_not_an_event": "bible_to_game",
                  "spelling_variant": "spellings",
                  "place_not_a_map": "places", "map_not_in_bible": "places",
                  "game_name_not_in_bible": "game_to_bible"}


def _tokens(text: str) -> "list[tuple[str, str]]":
    """(skeleton, as written) for each word of a name or a stretch of prose."""
    out = []
    for match in _WORD.finditer(text):
        surface = _POSSESSIVE.sub("", match.group().rstrip("'’-"))
        key = _skeleton(surface)
        if key:
            out.append((key, surface))
    return out


def _matches(joined: str, phrases: "set[str]") -> bool:
    """Whether a name is one of these, or contains one, or sits inside one.

    Containment only for the longer of the two being a whole word-run longer
    than three letters: "Riverbend" is "Riverbend Cave"'s town, but "Al" is
    not in "Alder".
    """
    if joined in phrases:
        return True
    return len(joined) > 3 and any(
        len(other) > 3 and (joined in other or other in joined)
        for other in phrases)


def lore_review(corpus: LoreCorpus, bible: dict[str, Any],
                scopes: "list[str] | None" = None,
                limit: int = MAX_REVIEW_FINDINGS) -> dict[str, Any]:
    """The bible's names against the game's text, with a question for each gap.

    Not a verdict. Every finding is something worth asking the author:
    lore the game never says (which may be fine background, or lore with no
    way to reach the player), a name the game says many times that the bible
    never mentions, a name spelled two ways, a character or place the bible
    knows and the game never shows, a map the bible never mentions.

    A name counts as "found in the game" when every word of it, and every
    adjacent pair of words, appears in the game's text, names or
    descriptions; case, accents and spacing are ignored. That is a heuristic
    over a bounded vocabulary, and the reply says when the vocabulary was cut.
    """
    wanted = list(REVIEW_SCOPES) if scopes is None else list(scopes)
    unknown = [scope for scope in wanted if scope not in REVIEW_SCOPES]
    if unknown:
        raise ValueError("unknown review scope %s" % brief(unknown[0], 40))
    entries = as_list(bible.get("entries"))
    answers = bible.get("answers")
    answers = answers if isinstance(answers, dict) else {}
    reply: dict[str, Any] = {"findings": [], "findings_total": 0}
    if not entries and not answers:
        reply["note"] = ("the bible is empty, so there is nothing to compare "
                         "with the game yet: save some answers or entries "
                         "first")
        return _review_partials(reply, corpus)
    in_bible: "set[str]" = set()
    for entry in entries:
        in_bible.update(key for key, _ in _tokens(entry["name"]))
        in_bible.update(key for key, _ in _tokens(entry["text"]))
    for text in answers.values():
        in_bible.update(key for key, _ in _tokens(text))
    joined_pairs = {pair.replace(" ", "") for pair in corpus.pair_set}
    findings: "list[dict[str, str]]" = []

    def add(kind: str, where: str, text: str, ask: str) -> None:
        if _FINDING_SCOPE[kind] in wanted:
            findings.append({"kind": kind, "where": where, "text": text,
                             "ask": ask})

    names = _names(corpus)
    near = _NearNames(corpus, names)
    for entry in entries:
        shown = _clip(entry["name"])
        where = "bible entry (%s): %s" % (entry["kind"], shown)
        words = _tokens(entry["name"])
        if not words:
            continue
        keys = [key for key, _ in words]
        joined = "".join(keys)
        whole = (all(key in corpus.token_set for key in keys)
                 and all(a + " " + b in corpus.pair_set
                         for a, b in zip(keys, keys[1:])))
        if not whole:
            spaced = joined in corpus.token_set or joined in joined_pairs
            close = None if spaced else near.close_to(
                [(k, s) for k, s in words if k not in corpus.token_set])
            if spaced or close:
                seen = close if close else _spaced_form(corpus, joined)
                add("spelling_variant", where,
                    "The bible has %s, but the game text has %s."
                    % (shown, seen),
                    "Which spelling is right: %s or %s?" % (shown, seen))
            else:
                add("not_in_game", where,
                    "The bible lists %s (%s), but no dialogue, description, "
                    "map name or event name in the game says it."
                    % (shown, entry["kind"]),
                    "Where does the player meet %s, or should it stay "
                    "background?" % shown)
            continue
        if entry["kind"] == "character" and not _matches(
                joined, corpus.event_names | corpus.person_names):
            add("character_not_an_event", where,
                "The bible has the character %s and the dialogue says the "
                "name, but no event, actor or enemy in the game is called "
                "that." % shown,
                "Is %s ever on screen, or only talked about?" % shown)
        elif entry["kind"] == "place" and not _matches(joined,
                                                       corpus.map_names):
            add("place_not_a_map", where,
                "The bible has the place %s and the text says the name, but "
                "no map is called that." % shown,
                "Can the player visit %s, or is it only mentioned?" % shown)
    if bible.get("entries") or answers:
        for name in corpus.maps_named:
            keys = [key for key, _ in _tokens(name)]
            if keys and not all(key in in_bible for key in keys):
                add("map_not_in_bible", "map tree: %s" % name,
                    "The map %s is in the game, but the bible never "
                    "mentions it." % name,
                    "What happens at %s, and does it belong in the bible?"
                    % name)
    for key, noun in names:
        count = noun.mid + noun.init
        if count >= _RECURRING and key not in in_bible:
            shown = _surface(noun)
            add("game_name_not_in_bible",
                noun.where[0] if noun.where else "the game text",
                "The name %s appears %d times in the game text (first at %s) "
                "but the bible never mentions it."
                % (shown, count, noun.where[0] if noun.where else "?"),
                "Who or what is %s, and does it belong in the bible?" % shown)
    order = list(_FINDING_SCOPE)
    findings.sort(key=lambda finding: order.index(finding["kind"]))
    reply["findings_total"] = len(findings)
    reply["findings"] = findings[:limit]
    if len(findings) > limit:
        reply["findings_truncated"] = True
    reply["compared"] = {"bible_entries": len(entries),
                         "bible_answers": len(answers),
                         "game_names_checked": len(names),
                         "maps_checked": len(corpus.maps_named)}
    return _review_partials(reply, corpus)


def _review_partials(reply: dict[str, Any], corpus: LoreCorpus
                     ) -> dict[str, Any]:
    if corpus.stopped:
        reply["walk_stopped"] = " ".join(
            note for note in dict.fromkeys(corpus.stopped) if note)
    if corpus.vocabulary_truncated or corpus.nouns_truncated:
        reply["vocabulary_truncated"] = True
        reply["vocabulary_note"] = (
            "the game's vocabulary was larger than this server keeps, so "
            "some names reported as missing from the game may be there")
    if corpus.not_read:
        reply["files_not_read"] = dict(corpus.not_read)
    return reply


def _spaced_form(corpus: LoreCorpus, joined: str) -> str:
    """How the game writes a name the bible joined or split differently."""
    for key, forms in corpus.phrases.items():
        if key == joined and forms:
            return sorted(forms.items(), key=lambda item: (-item[1], item[0]))[0][0]
    for name in corpus.maps_named:
        if _skeleton(name) == joined:
            return name
    noun = corpus.nouns.get(joined)
    return _surface(noun) if noun is not None and noun.forms else joined


class _NearNames:
    """The game's names by length, to find one a letter away from a bible name."""

    def __init__(self, corpus: LoreCorpus,
                 names: "list[tuple[str, _Noun]]") -> None:
        self.corpus = corpus
        self.by_length: "dict[int, list[str]]" = {}
        pool = set(corpus.name_tokens) | {key for key, _ in names}
        for key in sorted(pool):
            self.by_length.setdefault(len(key), []).append(key)

    def close_to(self, words: "list[tuple[str, str]]") -> "str | None":
        for key, _surface_given in words:
            if len(key) < 5:
                continue
            for length in (len(key) - 1, len(key), len(key) + 1):
                for other in self.by_length.get(length, ()):
                    if _one_edit(key, other):
                        noun = self.corpus.nouns.get(other)
                        return _surface(noun) if noun is not None \
                            and noun.forms else other
        return None
