"""`text`: every player-visible string in a project, out to a file and back.

The point is a proofread, a restyle or a translation done by a model that
never touches anything that is not text. `export` walks the places the game
shows words from and writes one JSON entry per string; `import` reads such a
file back and changes exactly the entries whose `text` the caller edited.

What makes this safe to hand a whole project to is that an entry is *found*,
not *constructed*: `import` re-walks the project with the same walker
`export` used, builds the table of text that exists now, and looks each key up
in it. A key that addresses something that is not text, a command that has
since changed code, a list that moved -- none of those is in the table, so
none can be written. Nothing in an import file is trusted to say where to
write; it can only choose among places the walker would have exported.

The walker is the single definition of "what is text here". Its scope was
decided from what the editor itself shows the player:

* Show Text (101 with its 401 lines), Show Choices (102, one entry per choice),
  Show Scrolling Text (105 with its 405 lines), and -- only when asked --
  Comments (108 with 408), in map event pages, common events and troop battle
  event pages.
* A map's display name (VX Ace). Not the map tree's names in MapInfos, which
  only the editor shows, and not event names or switch and variable names.
* Database `name`, `nickname`, `description` and `profile` of actors, classes,
  skills, items, weapons, armors, enemies and states, and the battle messages
  of skills and states. Not `note` (the developer's field), and not the names
  of troops, animations, tilesets or common events (editor-only).
* The System table: game title, currency unit, the skill, weapon and armour
  type names, and every term in `terms`.
* Empty strings are not exported: there is nothing to proofread in them.

Not covered, deliberately: script slots (code, not prose), Change Actor Name
and other commands that carry a name in their parameters, text drawn by
scripts, and picture or audio file names.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from ..analysis import WalkBudget
from ..commands import (
    CODE_CHOICE_BRANCH,
    CODE_COMMENT,
    CODE_COMMENT_LINE,
    CODE_END_CHOICES,
    CODE_SHOW_CHOICES,
    CODE_SHOW_TEXT,
    MAX_CHOICES,
    MESSAGE_LINES_PER_BOX,
)
from ..convert import ConversionError, decoded_text
from ..project import MAX_MAP_ID, ProjectError, _contained
from ..rubytypes import (
    RubyHash,
    RubyObject,
    RubyString,
    as_int,
    as_list,
    printable_text,
)
from ..schema import new_event_command
from .registry import (
    ARR,
    BOOL,
    Context,
    DRY_RUN,
    INT,
    STR,
    ToolError,
    check_op_args,
    int_list,
    need_str,
    one_of,
    opt_bool,
    opt_int_default,
    partial,
    prop,
    schema,
    scopes_arg,
    tool,
    write_reply,
)


# How many entries one export carries. A large game has tens of thousands of
# lines of dialogue; past this the file is too big for a model to read in one
# piece anyway, and `scopes` / `map_ids` split the work.
MAX_EXPORT_ENTRIES = 50_000

# The stored size of one entry, in bytes. An entry above it is still exported,
# read-only and without its text, so a caller sees that it exists.
MAX_TEXT_ENTRY_BYTES = 65_536

# The export file, and the largest file `import` reads.
MAX_EXPORT_FILE_BYTES = 16 * 1024 * 1024

# How many export files `.rpgvxace-mcp/exports/` keeps; the oldest go first.
MAX_KEPT_EXPORTS = 20

# How many entries an export's reply quotes as a preview.
MAX_PREVIEW_ENTRIES = 50

# The longest key an import file may carry. Keys this tool writes are shorter
# than sixty characters; the rest is room for a six-digit id at every level.
MAX_KEY_CHARS = 200

# Lines in one scrolling text or one comment. The editor's dialog has no fixed
# limit; this is far above any real one and keeps a rewrite from becoming an
# unbounded run of commands.
MAX_LONG_TEXT_LINES = 200

# How many maps one export names.
MAX_EXPORT_MAPS = 4096

# How many applied and how many skipped entries an import quotes back, and how
# much of each text. The counts are always complete.
MAX_REPORTED_EDITS = 200
MAX_REPORTED_SKIPS = 100
MAX_SHOWN_CHARS = 300

# How many unreadable files or maps a reply names.
_MAX_NOT_READ = 50

# Bytes of stored text one call will read or hash. Each string is charged
# before it is decoded, so a project whose Marshal links point a million
# entries at one large string costs a bounded amount of work.
_TEXT_BYTE_BUDGET = 256 * 1024 * 1024


_FORMAT = "rpgvxace-mcp-text"
_VERSION = 1
_EXPORT_DIR = "exports"
_OWN_EXPORT = re.compile(r"text-[0-9a-f]{10}\.json")


TEXT_SCOPES = ("map_events", "map_names", "common_events", "troops",
               "database", "system")


TEXT_OP_ARGS = {
    "export": {"op", "scopes", "map_ids", "include_comments", "preview",
               "refresh"},
    "import": {"op", "path", "force", "dry_run", "refresh"},
}

# Database tables whose strings the player reads, in the order they are
# walked, and the fields of each. A field is exported when the entry has it
# and it is a non-empty string; VX has no nickname, Ace has no profile.
_DB_TABLES = ("actors", "classes", "skills", "items", "weapons", "armors",
              "enemies", "states")
_DB_COMMON_FIELDS = ("name", "nickname", "description", "profile")
_DB_EXTRA_FIELDS = {
    "skills": ("message1", "message2"),
    "states": ("message1", "message2", "message3", "message4"),
}
_FIELD_KIND = {"message1": "battle_message", "message2": "battle_message",
               "message3": "battle_message", "message4": "battle_message"}


_SYSTEM_STRINGS = (("game_title", "title"), ("currency_unit", "currency"))
_SYSTEM_LISTS = ("skill_types", "weapon_types", "armor_types")
_MAX_LIST_ELEMENTS = 1000
_DEFAULT_PREVIEW = 10
_MAX_TERM_FIELDS = 200

# Kinds whose text may span lines, and kinds that may not be blank.
_MULTILINE = frozenset({"show_text", "scroll_text", "comment", "description",
                        "profile"})
_NEEDS_TEXT = frozenset({"name", "nickname", "choice", "term", "title",
                         "currency", "type_name", "map_display_name"})

# What an edited text may not contain. Control characters other than the line
# break (which a multi-line kind uses to separate lines), line and paragraph
# separators, and lone surrogates -- which JSON allows and no codec can store.
_BAD_MULTILINE = re.compile("[\x00-\x09\x0b-\x1f\x7f-\x9f  "
                            "\ud800-\udfff]")
_BAD_SINGLE = re.compile("[\x00-\x1f\x7f-\x9f  \ud800-\udfff]")


_KEY_SHAPE = re.compile(r"[A-Za-z0-9_:/]+")
_HASH_SHAPE = re.compile(r"[0-9a-f]{24}")
_NAME_SHAPE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


_SHARED = ("this object is reached from more than one place in the file, so "
           "an edit here would change all of them")


_TEXT_DESCRIPTION = (
    "Take every player-visible string in the project out to a JSON file, let "
    "the caller proofread, restyle or translate it, and write the result "
    "back -- without ever touching anything that is not text. op=export "
    "writes the file (to .rpgvxace-mcp/exports/ inside the project, keeping "
    "the %d newest, and not a project edit, so it works under --read-only) "
    "and replies with its path, the count of entries per kind and the first "
    "%d as a preview. Each entry has a stable key (\"map:3/event:5/page:0/"
    "cmd:12\", \"items:12/name\", \"system/terms/basic/3\"), a kind, the "
    "text, and original_hash, a hash of the stored bytes. Edit the text of "
    "entries in that file and call op=import with its path: only entries "
    "whose text you changed are written, each only if its original_hash "
    "still matches what the project holds now (a stale entry is reported and "
    "skipped unless force is set), and every other byte of every data file "
    "is left as it was. Covered: Show Text (a 101 and its 401 lines are one "
    "entry; face, background and position are read-only context), Show "
    "Choices (one entry per choice, and the editor's copy of each choice in "
    "its When branch is updated too), Show Scrolling Text, comments only with "
    "include_comments, in map events, common events and troop battle pages; "
    "map display names; name, nickname, description, profile and the battle "
    "messages of actors, classes, skills, items, weapons, armors, enemies "
    "and states; the game title, currency unit, type names and terms. Not "
    "covered: notes, event and switch names, scripts, and the names of "
    "troops, animations and tilesets. Empty strings are not exported. Text "
    "is read and written in the project's encoding; an entry whose bytes do "
    "not survive that round trip is exported read-only with its reason and "
    "is never written. Keep the line structure you want: a Show Text entry "
    "is written as one 401 line per line of text, at most %d lines (the "
    "message window's size, unless the command already had more); "
    "scrolling text and comments take at most %d. Names, choices and terms "
    "are one line; control characters other than the line break are refused. "
    "An edited entry is refused, not truncated, when it breaks a rule, and "
    "import says why in skipped. Limits: %s entries per export, %s stored "
    "bytes per entry, a %s byte file, keys of at most %d characters; "
    "scopes and map_ids narrow an export "
    "that is too big; at most %d maps; preview at most %d entries. An import "
    "reply quotes at most %d applied and %d skipped entries, %d characters "
    "of each text. dry_run previews every change as before and after. The "
    "files an import changes are written one after the other, so a failure "
    "on a later file says which already landed. VX Ace and VX only: XP's "
    "Show Text keeps its first line in the 101 and is refused."
    % (MAX_KEPT_EXPORTS, _DEFAULT_PREVIEW, MESSAGE_LINES_PER_BOX,
       MAX_LONG_TEXT_LINES,
       "{:,}".format(MAX_EXPORT_ENTRIES), "{:,}".format(MAX_TEXT_ENTRY_BYTES),
       "{:,}".format(MAX_EXPORT_FILE_BYTES), MAX_KEY_CHARS, MAX_EXPORT_MAPS,
       MAX_PREVIEW_ENTRIES, MAX_REPORTED_EDITS, MAX_REPORTED_SKIPS,
       MAX_SHOWN_CHARS))


# ---------------------------------------------------------------------------
# What a string is, and what it hashes to
# ---------------------------------------------------------------------------


def _digest(kind: str, parts: "list[bytes]") -> str:
    """A hash of the stored bytes of one entry, length-prefixed.

    Prefixed so that two lines "ab" and "c" do not hash as "a" and "bc", and
    keyed by kind so that the same bytes read as a different thing do not
    match. 96 bits: this detects a project that changed since the export, not
    an adversary -- the import file is already the caller's to write.
    """
    digest = hashlib.blake2b(digest_size=12)
    digest.update(kind.encode("ascii"))
    for part in parts:
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


def _clipped(text: str, limit: int = MAX_SHOWN_CHARS) -> str:
    """Text for a reply: bounded, and safe to put in a terminal."""
    if len(text) <= limit:
        return printable_text(text)
    return printable_text(text[:limit]) + "... (%d characters)" % len(text)


def _faithful(codec: Any, raw: bytes) -> "tuple[str, str | None]":
    """The text of stored bytes, and why they cannot be edited, if they cannot.

    Two ways to fail. Bytes the codec cannot decode (`surrogateescape` would
    turn them into lone surrogates, which a JSON file cannot carry), and bytes
    that decode and then encode to something else, so writing the text back
    would change the string even if the caller touched nothing. Either way the
    text is a best-effort reading for display, and the entry is read-only.
    """
    if not codec.is_clean(raw):
        return (raw.decode(codec.name, "replace"),
                "the stored bytes are not valid %s text, so they cannot be "
                "written back faithfully" % codec.name)
    text = codec.decode(raw)
    try:
        again = codec.encode_strict(text)
    except ConversionError:
        again = None
    if again != raw:
        return (text, "this text does not encode back to the stored bytes in "
                      "%s, so writing it would change the string" % codec.name)
    return text, None


class _Item:
    """One string the project holds, where it is, and what it says now.

    `owner` and `where` locate it for a write: an object and a field name, a
    list and an index (a type name, a term, or the head of a command group),
    and for a choice the 102's list plus which choice. They are dropped after
    an export, which has no use for them and would otherwise keep every
    walked command list alive.
    """

    __slots__ = ("key", "kind", "text", "digest", "reason", "context",
                 "form", "file", "owner", "where", "count", "choice",
                 "max_lines")

    def __init__(self, key: str, kind: str, text: str, digest: str,
                 reason: "str | None", form: str, file: str, owner: Any,
                 where: "str | int", count: int = 0,
                 choice: "int | None" = None, max_lines: int = 0,
                 context: "dict[str, Any] | None" = None):
        self.key = key
        self.kind = kind
        self.text = text
        self.digest = digest
        self.reason = reason
        self.context = context
        self.form = form
        self.file = file
        self.owner = owner
        self.where = where
        self.count = count
        self.choice = choice
        self.max_lines = max_lines


class _Scope:
    """Which parts of the project a walk looks at."""

    __slots__ = ("maps", "map_events", "map_names", "common_events", "troops",
                 "tables", "system", "comments")

    def __init__(self, maps: "list[int] | None" = None,
                 map_events: bool = True, map_names: bool = True,
                 common_events: bool = True, troops: bool = True,
                 tables: "tuple[str, ...]" = _DB_TABLES, system: bool = True,
                 comments: bool = False):
        self.maps = maps
        self.map_events = map_events
        self.map_names = map_names
        self.common_events = common_events
        self.troops = troops
        self.tables = tables
        self.system = system
        self.comments = comments


class _Halt(Exception):
    """The walk cannot go on: a ceiling or a budget was reached."""


def _code(command: Any) -> "int | None":
    if not isinstance(command, RubyObject):
        return None
    return as_int(command.get("code"))


def _params(command: Any) -> "list[Any]":
    return as_list(command.get("parameters")) \
        if isinstance(command, RubyObject) else []


def _event_id_ok(value: Any) -> bool:
    number = as_int(value)
    return number is not None and 0 <= number < 1_000_000


class _Collector:
    """Walks the project once and lists every string in scope.

    The only definition of what text is: `export` serialises what it found and
    `import` looks keys up in it. Bounded three ways -- the walk budget every
    event walk here shares, a byte budget for the strings it reads, and
    (for an export) the entry ceiling -- and each says so in `stopped` or
    `truncated` rather than ending quietly.

    A command list, a database entry or a list of terms that is reached twice
    in one file (Marshal links make that legal) is claimed on first sight; the
    second sight flips its items read-only, because an edit would change both
    places. A key produced twice does the same to both.
    """

    def __init__(self, ctx: Context, scope: _Scope, what: str,
                 limit: "int | None" = None, keep_references: bool = True):
        self.ctx = ctx
        self.project = ctx.project
        self.codec = ctx.codec
        self.scope = scope
        self.what = what
        self.limit = limit
        self.keep_references = keep_references
        self.budget = WalkBudget()
        self.bytes_left = _TEXT_BYTE_BUDGET
        self.items: "list[_Item]" = []
        self.by_key: "dict[str, _Item]" = {}
        self.truncated = False
        self.stopped: "list[str]" = []
        self.not_read: "dict[str, str]" = {}
        self.shared = 0
        self.duplicates = 0
        self._claims: "dict[int, list[Any]]" = {}
        self._file_items: "list[_Item]" = []

    # -- running ----------------------------------------------------------
    def run(self) -> "_Collector":
        try:
            self._walk()
        except _Halt:
            pass
        finally:
            self._release()
        return self

    def _release(self) -> None:
        """Let go of the walked objects an export has no further use for."""
        if not self.keep_references:
            for item in self.items:
                item.owner = None

    def _walk(self) -> None:
        scope = self.scope
        if scope.map_events or scope.map_names:
            self._maps()
        if scope.common_events:
            self._common_events()
        if scope.troops:
            self._troops()
        for table in scope.tables:
            self._table(table)
        if scope.system:
            self._system()

    def _step(self, count: int = 1) -> None:
        if not self.budget.spend(count):
            self.stopped.append(self.budget.note(self.what) or "")
            raise _Halt

    def _charge(self, size: int) -> None:
        self.bytes_left -= size
        if self.bytes_left < 0:
            self.stopped.append(
                "the stored text read for this %s passed %d bytes, so the "
                "walk stopped early and this answer is incomplete. Narrow "
                "the request (fewer maps, or a smaller scope)."
                % (self.what, _TEXT_BYTE_BUDGET))
            raise _Halt

    def _note_unread(self, label: str, exc: Exception) -> None:
        if len(self.not_read) < _MAX_NOT_READ:
            self.not_read[label] = _clipped(str(exc), 200)

    # -- files and sharing --------------------------------------------------
    def _begin_file(self) -> None:
        self._claims = {}
        self._file_items = []

    def _end_file(self) -> None:
        self._release_file()
        self._claims = {}
        self._file_items = []

    def _release_file(self) -> None:
        if not self.keep_references:
            for item in self._file_items:
                item.owner = None

    def _claim(self, obj: Any) -> "list[_Item] | None":
        """The item list of a thing seen for the first time, else None.

        On a second sight the first sight's items become read-only -- an edit
        through either route changes both -- and the caller skips the rescan.
        """
        record = self._claims.get(id(obj))
        if record is None:
            # The object itself is kept in the record so its id cannot be
            # reused by a different object during this file's walk.
            record = self._claims[id(obj)] = [obj, []]
            return record[1]
        self.shared += 1
        for item in record[1]:
            item.reason = item.reason or _SHARED
        return None

    def _add(self, item: _Item, claimed: "list[_Item] | None") -> None:
        if self.limit is not None and len(self.items) >= self.limit:
            self.truncated = True
            raise _Halt
        prior = self.by_key.get(item.key)
        if prior is not None:
            # Two strings with one key cannot both be addressed, so neither
            # is: the second is not listed and the first is read-only.
            self.duplicates += 1
            prior.reason = prior.reason or (
                "another string in this project has the same key")
            return
        self.by_key[item.key] = item
        self.items.append(item)
        self._file_items.append(item)
        if claimed is not None:
            claimed.append(item)

    # -- single strings -------------------------------------------------------
    def _string_item(self, key: str, kind: str, value: Any, form: str,
                     file: str, owner: Any, where: "str | int",
                     claimed: "list[_Item] | None",
                     choice: "int | None" = None) -> None:
        if not isinstance(value, RubyString) or not value.data:
            return
        raw = value.data
        self._charge(len(raw))
        if len(raw) > MAX_TEXT_ENTRY_BYTES:
            item = _Item(key, kind, "", _digest(kind + "!large",
                                                [str(len(raw)).encode()]),
                         _too_large(len(raw)), form, file, owner, where,
                         choice=choice)
        else:
            text, reason = _faithful(self.codec, raw)
            if b"\n" in raw and kind not in _MULTILINE:
                reason = reason or "the stored string contains a line break"
            item = _Item(key, kind, text, _digest(kind, [raw]), reason, form,
                         file, owner, where, choice=choice)
        self._add(item, claimed)

    # -- the system table ------------------------------------------------------
    def _system(self) -> None:
        project = self.project
        filename = project.filename_for("system")
        if not project.exists(filename):
            return
        self._begin_file()
        try:
            system = project.load_data("system")
        except (ProjectError, ValueError, OSError) as exc:
            self._note_unread("system", exc)
            return
        if not isinstance(system, RubyObject):
            return
        self._step()
        claimed = self._claim(system)
        for field, kind in _SYSTEM_STRINGS:
            self._string_item("system/%s" % field, kind, system.get(field),
                              "field", filename, system, field, claimed)
        for field in _SYSTEM_LISTS:
            self._string_list("system/%s" % field, "type_name",
                              system.get(field), filename)
        terms = system.get("terms")
        if isinstance(terms, RubyObject):
            term_claim = self._claim(terms)
            for name in terms.fields()[:_MAX_TERM_FIELDS]:
                self._step()
                if not _NAME_SHAPE.fullmatch(name):
                    continue
                value = terms.get(name)
                if isinstance(value, list):
                    self._string_list("system/terms/%s" % name, "term",
                                      value, filename)
                else:
                    self._string_item("system/terms/%s" % name, "term",
                                      value, "field", filename, terms, name,
                                      term_claim)
        self._end_file()

    def _string_list(self, prefix: str, kind: str, values: Any,
                     filename: str) -> None:
        if not isinstance(values, list):
            return
        claimed = self._claim(values)
        if claimed is None:
            return
        for index, value in enumerate(values[:_MAX_LIST_ELEMENTS]):
            self._step()
            self._string_item("%s/%d" % (prefix, index), kind, value,
                              "element", filename, values, index, claimed)

    # -- the database --------------------------------------------------------
    def _table(self, table: str) -> None:
        project = self.project
        if table not in project.data_files:
            return
        filename = project.filename_for(table)
        if not project.exists(filename):
            return
        self._begin_file()
        try:
            entries = project.entries(table)
        except (ProjectError, ValueError, OSError) as exc:
            self._note_unread(table, exc)
            return
        fields = _DB_COMMON_FIELDS + _DB_EXTRA_FIELDS.get(table, ())
        for entry_id, entry in entries:
            self._step()
            number = as_int(entry_id)
            if number is None or not 0 <= number < 1_000_000 \
                    or not isinstance(entry, RubyObject):
                continue
            claimed = self._claim(entry)
            if claimed is None:
                continue
            for field in fields:
                self._string_item("%s:%d/%s" % (table, number, field),
                                  _FIELD_KIND.get(field, field),
                                  entry.get(field), "field", filename,
                                  entry, field, claimed)
        self._end_file()

    # -- maps ---------------------------------------------------------------
    def _maps(self) -> None:
        project = self.project
        on_disk = project.map_ids_present()
        wanted = (sorted(on_disk) if self.scope.maps is None
                  else self.scope.maps)
        for map_id in wanted:
            self._step()
            if map_id not in on_disk:
                self._note_unread("map %d" % map_id,
                                  ProjectError("no such map file"))
                continue
            filename = project.map_file(map_id)
            self._begin_file()
            try:
                game_map = project.load_map(map_id)
            except (ProjectError, ValueError, OSError) as exc:
                self._note_unread("map %d" % map_id, exc)
                continue
            if self.scope.map_names:
                self._string_item("map:%d/display_name" % map_id,
                                  "map_display_name",
                                  game_map.get("display_name"), "field",
                                  filename, game_map, "display_name", None)
            if self.scope.map_events:
                self._map_events(map_id, filename, game_map)
            self._end_file()

    def _map_events(self, map_id: int, filename: str,
                    game_map: RubyObject) -> None:
        events = game_map.get("events")
        if not isinstance(events, RubyHash):
            return
        for event_id, event in events.sorted_items():
            self._step()
            if not _event_id_ok(event_id) or not isinstance(event, RubyObject):
                continue
            for page_index, page in enumerate(as_list(event.get("pages"))):
                self._step()
                if isinstance(page, RubyObject):
                    self._command_list(
                        "map:%d/event:%d/page:%d" % (
                            map_id, as_int(event_id), page_index),
                        filename, as_list(page.get("list")))

    def _common_events(self) -> None:
        project = self.project
        if "common_events" not in project.data_files:
            return
        filename = project.filename_for("common_events")
        if not project.exists(filename):
            return
        self._begin_file()
        try:
            entries = project.entries("common_events")
        except (ProjectError, ValueError, OSError) as exc:
            self._note_unread("common_events", exc)
            return
        for entry_id, entry in entries:
            self._step()
            number = as_int(entry_id)
            if number is None or not 0 <= number < 1_000_000 \
                    or not isinstance(entry, RubyObject):
                continue
            self._command_list("common_events:%d" % number, filename,
                               as_list(entry.get("list")))
        self._end_file()

    def _troops(self) -> None:
        project = self.project
        filename = project.filename_for("troops")
        if not project.exists(filename):
            return
        self._begin_file()
        try:
            entries = project.entries("troops")
        except (ProjectError, ValueError, OSError) as exc:
            self._note_unread("troops", exc)
            return
        for entry_id, entry in entries:
            self._step()
            number = as_int(entry_id)
            if number is None or not 0 <= number < 1_000_000 \
                    or not isinstance(entry, RubyObject):
                continue
            for page_index, page in enumerate(as_list(entry.get("pages"))):
                self._step()
                if isinstance(page, RubyObject):
                    self._command_list(
                        "troops:%d/page:%d" % (number, page_index), filename,
                        as_list(page.get("list")))
        self._end_file()

    # -- command lists -------------------------------------------------------
    def _command_list(self, prefix: str, filename: str,
                      commands: "list[Any]") -> None:
        claimed = self._claim(commands)
        if claimed is None:
            return
        affordable = self.budget.take(len(commands))
        cut = affordable < len(commands)
        index = 0
        while index < affordable:
            code = _code(commands[index])
            if code == CODE_SHOW_TEXT:
                index = self._group(prefix, filename, commands, affordable,
                                    index, 401, "show_text", claimed)
            elif code == 105:
                index = self._group(prefix, filename, commands, affordable,
                                    index, 405, "scroll_text", claimed)
            elif code == CODE_COMMENT and self.scope.comments:
                index = self._group(prefix, filename, commands, affordable,
                                    index, 408, "comment", claimed)
            elif code == CODE_SHOW_CHOICES:
                self._choices(prefix, filename, commands, index, claimed)
                index += 1
            else:
                index += 1
        if cut:
            self.stopped.append(self.budget.note(self.what) or "")
            raise _Halt

    def _group(self, prefix: str, filename: str, commands: "list[Any]",
               end: int, index: int, continuation: int, kind: str,
               claimed: "list[_Item]") -> int:
        """A head command and the lines that follow it, as one entry.

        Returns the index after the group, whether or not it became an entry.
        A comment keeps its first line in the head itself; Show Text and
        scrolling text keep every line in a continuation command.
        """
        head = commands[index]
        head_params = _params(head)
        lines: "list[Any]" = []
        if kind == "comment":
            lines.append(head_params[0] if head_params else None)
        after = index + 1
        while after < end and _code(commands[after]) == continuation:
            line_params = _params(commands[after])
            lines.append(line_params[0] if line_params else None)
            after += 1
        count = after - index - 1
        raws = [line.data if isinstance(line, RubyString) else b""
                for line in lines]
        if not any(raws):
            return after
        total = sum(len(raw) for raw in raws)
        self._charge(total)
        key = "%s/cmd:%d" % (prefix, index)
        reason = None
        if any(not isinstance(line, RubyString) for line in lines):
            reason = "a line of this command is not a string"
        max_lines = (MESSAGE_LINES_PER_BOX if kind == "show_text"
                     else MAX_LONG_TEXT_LINES)
        if total > MAX_TEXT_ENTRY_BYTES:
            text, reason = "", _too_large(total)
            digest = _digest(kind + "!large", [str(total).encode()])
        else:
            decoded = []
            for raw in raws:
                piece, why = _faithful(self.codec, raw)
                reason = reason or why
                if b"\n" in raw:
                    reason = reason or "a line of this command contains a " \
                                       "line break"
                decoded.append(piece)
            text = "\n".join(decoded)
            digest = _digest(kind, raws)
        item = _Item(key, kind, text, digest, reason, "group", filename,
                     commands, index, count=count, max_lines=max_lines,
                     context=self._context(kind, head_params))
        self._add(item, claimed)
        return after

    def _context(self, kind: str, params: "list[Any]"
                 ) -> "dict[str, Any] | None":
        """The head command's settings, to read and never to edit."""
        if kind == "show_text":
            names = ("face_name", "face_index", "background", "position")
        elif kind == "scroll_text":
            names = ("speed", "no_fast")
        else:
            return None
        out: "dict[str, Any]" = {}
        for name, value in zip(names, params):
            if isinstance(value, RubyString):
                out[name] = decoded_text(self.codec, value)
            elif isinstance(value, bool) or as_int(value) is not None:
                out[name] = value
        return out or None

    def _choices(self, prefix: str, filename: str, commands: "list[Any]",
                 index: int, claimed: "list[_Item]") -> None:
        params = _params(commands[index])
        texts = as_list(params[0]) if params else []
        for choice, value in enumerate(texts[:MAX_CHOICES]):
            self._string_item("%s/cmd:%d/choice:%d" % (prefix, index, choice),
                              "choice", value, "choice", filename, commands,
                              index, claimed, choice=choice)


def _too_large(size: int) -> str:
    return ("the stored text is %d bytes, above the %d an entry carries, so "
            "it is not exported and cannot be edited here"
            % (size, MAX_TEXT_ENTRY_BYTES))


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------


def _key_scope(key: str) -> "tuple[str, int | str] | None":
    """What a key asks the walker to load, without trusting the rest of it.

    Only the first segment, which names a file. Whether the rest addresses
    anything is the walker's answer, not this function's: a key is looked up,
    never parsed into a place to write.
    """
    head = key.split("/", 1)[0]
    if head == "system":
        return ("system", 0)
    name, colon, number = head.partition(":")
    if not colon or not number.isascii() or not number.isdigit() \
            or len(number) > 6:
        return None
    if name == "map" and 1 <= int(number) <= MAX_MAP_ID:
        return ("map", int(number))
    if name in ("common_events", "troops"):
        return (name, 0)
    if name in _DB_TABLES:
        return ("table", name)
    return None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _refuse_text(item: _Item, text: str, codec: Any) -> "str | None":
    """Why this edited text cannot be written, or None.

    Checked per entry and reported, not raised: one bad line in a translated
    file should cost that line and not the other four thousand.
    """
    if text == "" and item.kind in _NEEDS_TEXT:
        return "a %s cannot be empty" % item.kind.replace("_", " ")
    pattern = _BAD_MULTILINE if item.kind in _MULTILINE else _BAD_SINGLE
    bad = pattern.search(text)
    if bad:
        char = bad.group()
        if char == "\n":
            return "a %s is one line; it contains a line break" \
                   % item.kind.replace("_", " ")
        if "\ud800" <= char <= "\udfff":
            return "contains a lone surrogate, which no encoding can store"
        return "contains the control character U+%04X" % ord(char)
    try:
        encoded = codec.encode_strict(text)
    except ConversionError as exc:
        return _clipped(str(exc), 200)
    if len(encoded) > MAX_TEXT_ENTRY_BYTES:
        return "is %d bytes encoded, above the %d an entry may be" % (
            len(encoded), MAX_TEXT_ENTRY_BYTES)
    if item.form == "group":
        lines = text.split("\n")
        allowed = max(item.max_lines, item.count + (
            1 if item.kind == "comment" else 0))
        if len(lines) > allowed:
            return ("is %d lines, above the %d this command may have"
                    % (len(lines), allowed))
    return None


def _copy_command(command: Any, parameters: "list[Any]") -> RubyObject:
    """A command like this one with other parameters, leaving this one alone.

    A copy and not an edit: the same command object can sit in more than one
    list, and a changed parameter would show up in all of them.
    """
    clone = RubyObject(command.cls, dict(command.ivars))
    clone.set("parameters", parameters)
    return clone


def _apply(item: _Item, text: str, codec: Any) -> None:
    """Write one edited text into the cached file."""
    if item.form == "field":
        item.owner.set(item.where, codec.make_string(text))
    elif item.form == "element":
        item.owner[item.where] = codec.make_string(text)
    elif item.form == "choice":
        _apply_choice(item, text, codec)
    else:
        _apply_group(item, text, codec)


def _apply_choice(item: _Item, text: str, codec: Any) -> None:
    commands, index, choice = item.owner, item.where, item.choice
    command = commands[index]
    params = list(_params(command))
    options = list(as_list(params[0]))
    previous = options[choice]
    options[choice] = codec.make_string(text)
    params[0] = options
    commands[index] = _copy_command(command, params)
    # The editor keeps a second copy of each choice's text in its When
    # branch. The interpreter picks a branch by number, so the copy is only
    # ever read by the editor, which shows it -- and a stale one would show
    # the old wording beside the new.
    indent = command.get("indent")
    for follow in range(index + 1, len(commands)):
        other = commands[follow]
        code = _code(other)
        if code == CODE_END_CHOICES and other.get("indent") == indent:
            break
        if code == CODE_CHOICE_BRANCH and other.get("indent") == indent:
            branch = _params(other)
            if len(branch) >= 2 and branch[0] == choice \
                    and isinstance(branch[1], RubyString) \
                    and branch[1].data == previous.data:
                commands[follow] = _copy_command(
                    other, [branch[0], codec.make_string(text), *branch[2:]])
            if branch and branch[0] == choice:
                break


def _apply_group(item: _Item, text: str, codec: Any) -> None:
    commands, index = item.owner, item.where
    head = commands[index]
    comment = item.kind == "comment"
    continuation = {"show_text": 401, "scroll_text": 405,
                    "comment": 408}[item.kind]
    old = commands[index + 1:index + 1 + item.count]
    if len(old) != item.count or any(_code(c) != continuation for c in old) \
            or _code(head) != {"show_text": 101, "scroll_text": 105,
                               "comment": 108}[item.kind]:
        raise ToolError(
            "%s changed while the import was being applied, so it was not "
            "written. This is a bug in the server, not in the request."
            % item.key)
    lines = text.split("\n")
    indent = as_int(head.get("indent")) or 0
    if comment:
        previous = [head, *old]
        built = [_line_command(c, line, codec)
                 for c, line in zip(previous, lines)]
        built += [new_event_command(CODE_COMMENT_LINE, indent,
                                    [codec.make_string(line)])
                  for line in lines[len(previous):]]
        commands[index] = built[0]
        commands[index + 1:index + 1 + item.count] = built[1:]
        return
    built = [_line_command(c, line, codec) for c, line in zip(old, lines)]
    built += [new_event_command(continuation, indent,
                                [codec.make_string(line)])
              for line in lines[len(old):]]
    commands[index + 1:index + 1 + item.count] = built


def _line_command(command: Any, line: str, codec: Any) -> Any:
    """The command for one line: the existing one when the line is unchanged."""
    params = _params(command)
    if params and isinstance(params[0], RubyString) \
            and params[0].data == codec.encode_strict(line):
        return command
    return _copy_command(command, [codec.make_string(line), *params[1:]])


# ---------------------------------------------------------------------------
# The export file
# ---------------------------------------------------------------------------


def _entry_json(item: _Item) -> "dict[str, Any]":
    entry: "dict[str, Any]" = {"key": item.key, "kind": item.kind,
                               "text": item.text,
                               "original_hash": item.digest}
    if item.context:
        entry["context"] = item.context
    if item.form == "group":
        entry["max_lines"] = item.max_lines
    if item.reason:
        entry["read_only"] = True
        entry["reason"] = item.reason
    return entry


def _exports_folder(ctx: Context) -> Path:
    return ctx.project.work_dir() / _EXPORT_DIR


def _write_export(ctx: Context, body: bytes) -> Path:
    """Put the file in the exports folder and trim the folder to its ceiling."""
    folder = _exports_folder(ctx)
    # Checked before it is made: a link planted as the work folder carried
    # the `mkdir` outside the project before the check below refused.
    _contained(folder, ctx.project.root, "the exports folder")
    folder.mkdir(parents=True, exist_ok=True)
    name = "text-%s.json" % hashlib.sha1(body).hexdigest()[:10]
    target = folder / name
    _contained(target, ctx.project.root, "the export file")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=folder, prefix=name + ".", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
        os.replace(temporary, target)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    kept = sorted((p for p in folder.glob("text-??????????.json")
                   if p.is_file() and _OWN_EXPORT.fullmatch(p.name)),
                  key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for stale in kept[MAX_KEPT_EXPORTS:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return target


def _export(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    project = ctx.project
    scopes = scopes_arg(args, "scopes", TEXT_SCOPES)
    map_ids = int_list(args, "map_ids", MAX_EXPORT_MAPS,
                       minimum=1, maximum=MAX_MAP_ID) or None
    if map_ids is not None:
        map_ids = sorted(set(map_ids))
    preview = opt_int_default(args, "preview", _DEFAULT_PREVIEW, 0,
                              MAX_PREVIEW_ENTRIES)
    if opt_bool(args, "refresh"):
        project.forget_cached()
    scope = _Scope(
        maps=map_ids, map_events="map_events" in scopes,
        map_names="map_names" in scopes,
        common_events="common_events" in scopes, troops="troops" in scopes,
        tables=_DB_TABLES if "database" in scopes else (),
        system="system" in scopes,
        comments=opt_bool(args, "include_comments"))
    walked = _Collector(ctx, scope, "text export",
                        limit=MAX_EXPORT_ENTRIES,
                        keep_references=False).run()

    # Serialised against the file ceiling as it grows, so the file that is
    # written is never one `import` would refuse to read.
    head = json.dumps({"format": _FORMAT, "version": _VERSION,
                       "engine": project.engine,
                       "encoding": ctx.codec.name}, ensure_ascii=False)
    size = len(head.encode("utf-8")) + 64
    rows: "list[str]" = []
    kept: "list[_Item]" = []
    full = False
    for item in walked.items:
        row = json.dumps(_entry_json(item), ensure_ascii=False)
        size += len(row.encode("utf-8")) + 2
        if size > MAX_EXPORT_FILE_BYTES:
            full = True
            break
        rows.append(row)
        kept.append(item)
    body = (head[:-1] + ', "entries": [\n' + ",\n".join(rows) + "\n]}\n"
            ).encode("utf-8")
    path = _write_export(ctx, body)

    counts = Counter(item.kind for item in kept)
    reply: "dict[str, Any]" = {
        "path": str(path),
        "bytes": len(body),
        "entries": len(kept),
        "counts": dict(sorted(counts.items())),
        "read_only_entries": sum(1 for item in kept if item.reason),
        "encoding": ctx.codec.name,
        "preview": [
            {"key": item.key, "kind": item.kind, "text": _clipped(item.text),
             **({"read_only": True} if item.reason else {})}
            for item in kept[:preview]],
    }
    reply.update(partial(
        "entries", truncated=walked.truncated or full,
        notes=(
            ("the export holds %d entries, the most one file carries; use "
             "scopes or map_ids to export the rest in pieces"
             % MAX_EXPORT_ENTRIES) if walked.truncated else "",
            ("the file reached %d bytes, the most one file holds; use scopes "
             "or map_ids to export the rest in pieces"
             % MAX_EXPORT_FILE_BYTES) if full else "")))
    reply.update(partial("walk", stopped=" ".join(
        note for note in walked.stopped if note) or None))
    reply.update(partial("files", not_read=walked.not_read))
    reply.update(partial("shared", notes=(
        ("%d command lists or entries are reached from more than one place "
         "in their file, and the strings in them are read-only"
         % walked.shared) if walked.shared else "",
        ("%d strings share a key with another and are not listed"
         % walked.duplicates) if walked.duplicates else "")))
    reply.update(partial("export", notes=(
        "Wrote a cache file under .rpgvxace-mcp/exports; this is not a "
        "project edit, so it is done under --read-only too and no project "
        "data changed.",)))
    return reply


# ---------------------------------------------------------------------------
# Reading an import file
# ---------------------------------------------------------------------------


def _no_duplicate_keys(pairs: "list[tuple[str, Any]]") -> "dict[str, Any]":
    out: "dict[str, Any]" = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("the JSON object repeats the key %s"
                             % _clipped(repr(key), 60))
        out[key] = value
    return out


def _refuse_constant(name: str) -> Any:
    raise ValueError("%s is not valid JSON" % name)


def _import_path(ctx: Context, given: str) -> Path:
    """The file an import may read: inside the project, resolved, a .json file.

    A relative name is taken relative to the exports folder, which is where
    `export` puts its files. The path is resolved first -- so a link or `..`
    is judged by where it ends up -- and the answer must be inside the
    project root. The exports folder itself being a link to elsewhere
    therefore refuses every file in it.
    """
    if any(ord(char) < 32 for char in given):
        raise ToolError("path contains a control character")
    candidate = Path(given)
    if not candidate.is_absolute():
        candidate = _exports_folder(ctx) / candidate
    try:
        real = candidate.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise ToolError("path cannot be resolved: %s"
                        % _clipped(str(exc), 120)) from exc
    root = ctx.project.root
    if not real.is_relative_to(root):
        raise ToolError(
            "path resolves to %s, outside the project (%s). An import file "
            "has to be inside the project; export writes to %s."
            % (_clipped(str(real), 200), root, _exports_folder(ctx)))
    if real.suffix.lower() != ".json":
        raise ToolError("path is not a .json file: %s"
                        % _clipped(real.name, 120))
    return real


def _read_import(real: Path) -> bytes:
    try:
        if not real.is_file():
            raise ToolError("no such file: %s" % _clipped(str(real), 200))
        on_disk = real.stat().st_size
        if on_disk > MAX_EXPORT_FILE_BYTES:
            raise ToolError("the file is %d bytes, above the %d an export "
                            "file is" % (on_disk, MAX_EXPORT_FILE_BYTES))
        with open(real, "rb") as handle:
            data = handle.read(MAX_EXPORT_FILE_BYTES + 1)
    except OSError as exc:
        raise ToolError("could not read the file: %s"
                        % _clipped(str(exc), 120)) from exc
    if len(data) > MAX_EXPORT_FILE_BYTES:
        raise ToolError("the file is above the %d bytes an export file is"
                        % MAX_EXPORT_FILE_BYTES)
    return data


def _parse_entries(ctx: Context, data: bytes) -> "list[dict[str, Any]]":
    """The entries of an import file, or a refusal naming what is wrong.

    Structure is the file's, and is refused whole: a file that is not JSON,
    that repeats a key, that has an entry of the wrong type, or that names a
    key twice has no reading a caller could be relied on to have meant. What
    an entry *says* is judged entry by entry afterwards.
    """
    try:
        text = data.decode("utf-8-sig")
        document = json.loads(text, object_pairs_hook=_no_duplicate_keys,
                              parse_constant=_refuse_constant)
    except UnicodeDecodeError:
        raise ToolError("the file is not UTF-8 text") from None
    except (ValueError, RecursionError) as exc:
        raise ToolError("the file is not usable JSON: %s"
                        % _clipped(str(exc), 160)) from exc
    if not isinstance(document, dict) or document.get("format") != _FORMAT:
        raise ToolError("this is not a file `text op=export` wrote (its "
                        "\"format\" is not %s)" % _FORMAT)
    version = document.get("version")
    if isinstance(version, bool) or version != _VERSION:
        raise ToolError("this file's version is %s; this server reads "
                        "version %d" % (_clipped(repr(version), 40), _VERSION))
    for field, expected in (("engine", ctx.project.engine),
                            ("encoding", ctx.codec.name)):
        if document.get(field) != expected:
            raise ToolError(
                "this file was exported from a project with %s %s, and the "
                "open one has %s: text from one cannot be written into the "
                "other" % (field, _clipped(repr(document.get(field)), 40),
                           expected))
    entries = document.get("entries")
    if not isinstance(entries, list):
        raise ToolError("entries must be an array")
    if len(entries) > MAX_EXPORT_ENTRIES:
        raise ToolError("the file has %d entries; an export carries at most "
                        "%d" % (len(entries), MAX_EXPORT_ENTRIES))
    seen: "set[str]" = set()
    for number, entry in enumerate(entries):
        where = "entries[%d]" % number
        if not isinstance(entry, dict):
            raise ToolError("%s must be an object" % where)
        key = entry.get("key")
        if not isinstance(key, str) or not 0 < len(key) <= MAX_KEY_CHARS \
                or not _KEY_SHAPE.fullmatch(key):
            raise ToolError("%s.key is not a key this tool writes: %s"
                            % (where, _clipped(repr(key), 60)))
        for field in ("kind", "text", "original_hash"):
            if not isinstance(entry.get(field), str):
                raise ToolError("%s.%s must be a string (key %s)"
                                % (where, field, key))
        if not _HASH_SHAPE.fullmatch(entry["original_hash"]):
            raise ToolError("%s.original_hash is not a hash this tool writes "
                            "(key %s)" % (where, key))
        if key in seen:
            raise ToolError("%s repeats the key %s, which an export never "
                            "does; edit one entry per key" % (where, key))
        seen.add(key)
    return entries


# ---------------------------------------------------------------------------
# The import
# ---------------------------------------------------------------------------


def _scope_for(ctx: Context, entries: "list[dict[str, Any]]"
               ) -> "tuple[_Scope, list[str]]":
    """What to load for these keys, and the files that is."""
    project = ctx.project
    maps: "set[int]" = set()
    tables: "set[str]" = set()
    flags = {"system": False, "common_events": False, "troops": False}
    for entry in entries:
        found = _key_scope(entry["key"])
        if found is None:
            continue
        kind, which = found
        if kind == "map":
            maps.add(which)
        elif kind == "table":
            tables.add(which)
        else:
            flags[kind] = True
    files = [project.map_file(i) for i in sorted(maps)]
    files += [project.filename_for(t) for t in _DB_TABLES if t in tables
              and t in project.data_files]
    for name in ("system", "common_events", "troops"):
        if flags[name]:
            files.append(project.filename_for(name))
    scope = _Scope(
        maps=sorted(maps), map_events=bool(maps), map_names=bool(maps),
        common_events=flags["common_events"], troops=flags["troops"],
        tables=tuple(t for t in _DB_TABLES if t in tables),
        system=flags["system"], comments=True)
    return scope, files


def _import(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    project = ctx.project
    dry_run = opt_bool(args, "dry_run")
    force = opt_bool(args, "force")
    real = _import_path(ctx, need_str(args, "path"))
    entries = _parse_entries(ctx, _read_import(real))
    scope, files = _scope_for(ctx, entries)
    refresh = opt_bool(args, "refresh")

    with ctx.staged(*files) as landed:
        if refresh:
            for filename in files:
                project.forget_file(filename)
        # Inside the staged block: the walk loads the files it reads, and
        # only a pinned file stays in the cache between the walk that finds
        # a string and the write that changes it.
        walked = _Collector(ctx, scope, "text import").run()
        edits, skipped, unchanged = _plan(ctx, walked, entries, force)

        # From the highest command index down, so a group that gains or loses
        # lines cannot move one that is still waiting to be written. Keys
        # name positions as the export saw them.
        edits.sort(key=lambda edit: (edit[0].form in ("group", "choice"),
                                     -edit[0].where
                                     if isinstance(edit[0].where, int) else 0))
        for item, text, _ in edits:
            _apply(item, text, ctx.codec)

        writes = _save(ctx, landed, edits, dry_run)

    rows = [{"key": item.key, "kind": item.kind,
             "before": _clipped(item.text), "after": _clipped(text),
             **({"forced": True} if forced else {})}
            for item, text, forced in edits[:MAX_REPORTED_EDITS]]
    body: "dict[str, Any]" = {
        "path": str(real),
        "entries_in_file": len(entries),
        "edit_count": len(edits),
        "applied": rows,
        "unchanged": unchanged,
        "skipped_count": len(skipped),
        "skipped": skipped[:MAX_REPORTED_SKIPS],
        "writes": writes,
    }
    body.update(partial(
        "edits", truncated=len(edits) > MAX_REPORTED_EDITS,
        notes=(("only the first %d are quoted; edit_count is the total"
                % MAX_REPORTED_EDITS) if len(edits) > MAX_REPORTED_EDITS
               else "",)))
    body.update(partial(
        "skipped", truncated=len(skipped) > MAX_REPORTED_SKIPS,
        notes=(("only the first %d are quoted; skipped_count is the total"
                % MAX_REPORTED_SKIPS) if len(skipped) > MAX_REPORTED_SKIPS
               else "",)))
    body.update(partial("walk", stopped=" ".join(
        note for note in walked.stopped if note) or None))
    body.update(partial("files", not_read=walked.not_read))
    return write_reply(body, dry_run)


def _plan(ctx: Context, walked: _Collector, entries: "list[dict[str, Any]]",
          force: bool
          ) -> "tuple[list[tuple[_Item, str, bool]], list[dict[str, str]], int]":
    """Decide, entry by entry, what is written, skipped, or already so."""
    edits: "list[tuple[_Item, str, bool]]" = []
    skipped: "list[dict[str, str]]" = []
    unchanged = 0
    cut = ("" if not walked.stopped else
           " (the walk of the project stopped early, so this may only mean "
           "the key was not reached)")

    def skip(key: str, why: str) -> None:
        skipped.append({"key": key, "reason": why})

    for entry in entries:
        key, text = entry["key"], entry["text"]
        item = walked.by_key.get(key)
        if item is None:
            skip(key, "no text is stored at this key now: it is not a place "
                      "this tool exports, or the project has changed since"
                      + cut)
            continue
        if entry["kind"] != item.kind:
            skip(key, "the file says this is a %s and the project holds a %s "
                      "there now" % (_clipped(entry["kind"], 30), item.kind))
            continue
        if text == item.text:
            unchanged += 1
            continue
        if item.reason:
            skip(key, "read-only: " + item.reason)
            continue
        forced = False
        if entry["original_hash"] != item.digest:
            if not force:
                skip(key, "stale: the project's text here has changed since "
                          "the export (its original_hash no longer matches). "
                          "Export again, or pass force to write it anyway")
                continue
            forced = True
        problem = _refuse_text(item, text, ctx.codec)
        if problem:
            skip(key, "the new text " + problem)
            continue
        edits.append((item, text, forced))
    return edits, skipped, unchanged


def _save(ctx: Context, landed: Any, edits: "list[tuple[_Item, str, bool]]",
          dry_run: bool) -> "list[dict[str, Any]]":
    """Save each file that changed, saying what already landed if one fails."""
    per_file = Counter(item.file for item, _, _ in edits)
    names = sorted(per_file)
    if not dry_run:
        # Marshal every file first. A value that cannot be written is found
        # here, before the first file replaces the one on disk.
        for filename in names:
            landed.save(filename, dry_run=True, tool="text")
    writes: "list[dict[str, Any]]" = []
    for filename in names:
        try:
            writes.append(landed.save(
                filename, dry_run=dry_run, tool="text",
                summary="text import: %d entries" % per_file[filename]))
        except (ProjectError, OSError) as exc:
            if not writes or dry_run:
                raise
            raise ToolError(
                "%s were written (%s), but %s could not be (%s). The files "
                "do not agree now, so this is not \"nothing happened\": "
                "export again and import the entries that did not land; the "
                "ones that did will show as unchanged."
                % (", ".join(w["file"] for w in writes),
                   "; ".join("%s: %d entries" % (w["file"], per_file[w["file"]])
                             for w in writes),
                   filename, _clipped(str(exc), 200))) from exc
    return writes


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


@tool(
    "text",
    "Export and import the project's text",
    _TEXT_DESCRIPTION,
    schema({
        "op": prop(STR, "export (write every string to a JSON file) or import "
                        "(write the entries whose text you edited back).",
                   enum=list(TEXT_OP_ARGS)),
        "scopes": prop(ARR, "For export: which parts to include (default "
                            "all): " + ", ".join(TEXT_SCOPES),
                       items={"type": STR, "enum": list(TEXT_SCOPES)},
                       maxItems=len(TEXT_SCOPES)),
        "map_ids": prop(ARR, "For export: only these maps' events and display "
                             "names. Common events and troops are not "
                             "maps; leave them out with scopes. At most %d."
                        % MAX_EXPORT_MAPS,
                        items={"type": INT}, maxItems=MAX_EXPORT_MAPS),
        "include_comments": prop(BOOL, "For export: also include Comment "
                                       "commands (default false -- they are "
                                       "mostly notes to the developer)."),
        "preview": prop(INT, "For export: how many entries to quote in the "
                             "reply (default %d, at most %d)."
                        % (_DEFAULT_PREVIEW, MAX_PREVIEW_ENTRIES),
                        minimum=0, maximum=MAX_PREVIEW_ENTRIES),
        "path": prop(STR, "For import: the export file, as export returned "
                          "it. It must be inside the project; a relative "
                          "name is looked for in .rpgvxace-mcp/exports. At "
                          "most 1024 characters.", maxLength=1024),
        "force": prop(BOOL, "For import: write an edited entry even though "
                            "the project's text has changed since the export "
                            "(default false: such entries are skipped). It "
                            "does not let an entry reach anything that is "
                            "not the same kind of text."),
        "dry_run": DRY_RUN,
        "refresh": prop(BOOL, "Re-read the files from disk instead of using "
                              "the copies already in memory. Use after the "
                              "RPG Maker editor has saved."),
    }, ["op"]),
    writes=True,
)
def text_tool(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Export or import the project's player-visible text. See the module."""
    op = one_of(args, "op", list(TEXT_OP_ARGS))
    check_op_args(op, args, TEXT_OP_ARGS, "text")
    if not ctx.project.command_layouts_modelled:
        raise ToolError(
            "text works on VX Ace and VX projects. This is a %s project: its "
            "Show Text keeps the first line in the 101 itself and has no "
            "face, so the line structure this tool writes is not its own."
            % ctx.project.profile.label)
    if op == "export":
        return _export(ctx, args)
    return _import(ctx, args)
