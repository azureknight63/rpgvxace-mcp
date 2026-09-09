"""Walking a project's event command lists, and where each visit was.

The bottom of what was one 2,000-line `analysis` module. Everything here
answers "where in this project is X" without knowing what X means: a
location, a bounded walk, and the readers that make a crafted file safe to
iterate. It imports `project`, `convert` and `rubytypes` and nothing above
them, which is what makes the layering `walk <- references <- lint` acyclic
rather than merely tidy.

`WalkBudget` is the reason this file is worth reading on its own: every
bound in this project used to count nodes and none counted bytes, and every
charge placed after an `isinstance` test charged nothing for the slot it
skipped. Both defects lived in this walk, in more than one of its branches.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..convert import brief, decoded_text
from ..project import Project, ProjectError
from ..rubytypes import RubyHash, RubyObject, as_int, as_list


class Location:
    """Where in a project something was found."""

    __slots__ = ("kind", "map_id", "event_id", "event_name", "page", "index", "entry_id",
                 "table", "field")

    def __init__(self, kind: str, map_id: int | None = None, event_id: int | None = None,
                 event_name: str | None = None, page: int | None = None,
                 index: int | None = None, entry_id: int | None = None,
                 table: str | None = None, field: str | None = None):
        self.kind = kind
        self.map_id = map_id
        self.event_id = event_id
        self.event_name = event_name
        self.page = page
        self.index = index
        self.entry_id = entry_id
        self.table = table
        self.field = field

    def at(self, index: int) -> Location:
        """This location, pointing at one command in the list it names.

        Copies by slot name: building the copy positionally is how two earlier
        versions of this ended up silently dropping ``table`` and ``field``.
        """
        clone = Location(self.kind)
        for name in Location.__slots__[1:]:
            setattr(clone, name, getattr(self, name))
        clone.index = index
        return clone

    def as_dict(self) -> dict[str, Any]:
        out = {"kind": self.kind}
        for name in Location.__slots__[1:]:
            value = getattr(self, name)
            if value is not None:
                out[name] = value
        return out

    def describe(self) -> str:
        """A one-line "where" for a person or a model to read.

        Ids here are as the tools take them, so a model can act on a finding
        without translating anything: page and command indexes are 0-based, and
        a clause is left out entirely rather than printed as None -- claiming a
        command position that was never examined would be a lie about what the
        rule looked at.
        """
        if self.kind == "map_event":
            where = "map %s event %s (%s) page %s" % (
                self.map_id, self.event_id, self.event_name, self.page)
            return where + self._command_clause()
        if self.kind == "common_event":
            return "common event %s%s" % (self.entry_id, self._command_clause())
        if self.kind == "troop_page":
            return "troop %s battle page %s%s" % (
                self.entry_id, self.page, self._command_clause())
        if self.kind == "database":
            where = "%s %s" % (self.table, self.entry_id)
            return where + (" field %s" % self.field if self.field else "")
        if self.kind == "map":
            where = "map %s" % self.map_id
            return where + (" field %s" % self.field if self.field else "")
        if self.kind == "script":
            # A script slot has no fields of its own here: the slot index rides
            # in entry_id and the 1-based line number in index.
            return "script %s line %s" % (self.entry_id, self.index)
        return self.kind

    def _command_clause(self) -> str:
        return "" if self.index is None else " command %s" % self.index


def _map_at(map_id: int, field: str = "") -> Location:
    return Location("map", map_id=map_id, field=field or None)


def _db_at(table: str, entry_id: int, field: str = "") -> Location:
    return Location("database", table=table, entry_id=entry_id, field=field or None)


def _event_at(map_id: int, event_id: int, event_name: str, page: int) -> Location:
    return Location("map_event", map_id=map_id, event_id=event_id,
                    event_name=event_name, page=page)


# `_seq` and `_int` live in `rubytypes` now, under the names `as_list` and
# `as_int`: `commands` needs them and sits below `analysis`, so keeping them
# here meant that module spelled the rule as nine `or []` copies instead --
# and `or []` is not the same rule. Aliased rather than renamed at 40 call
# sites, because the short names read better inside a walk.
_seq = as_list
_int = as_int


def _brief(value: Any) -> str:
    """A value quoted into a message, sliced before it is repr'd.

    A wrapper over `convert.brief`, and that is the fix: this capped the
    result and not the work, so its one caller -- which passes a
    MapInfos hash key straight out of an untrusted file -- repr'd a
    crafted key whole and cut it afterwards.
    """
    return brief(value)


def _text(project: Project, value: Any) -> str:
    """Text from a project file, ready to put in a message.

    A wrapper, so this module keeps the `Project` receiver every caller here
    passes while the decoding itself lives in `convert` beside the codec --
    where `registry.decoded` reaches the same implementation. The tools layer
    had four copies of this that did neither the capping nor the sanitising,
    so a 1,000,006-character map name went into a reply whole.
    """
    return decoded_text(project.codec, value)


# How many nodes one call may visit on the event walk -- events, pages and
# commands alike. A real project's largest map is a few thousand all told;
# this is far above that. It exists because a file's *rendered* size is not
# its size on disk: Ruby Marshal has object links, so one command, a list of
# two thousand links to it, fifty links to that page and fifty links to that
# event is 7 KB describing five million visits -- measured at 36 seconds in
# event_get.
#
# Nodes, not commands, and the name says so because the first version of this
# counted only commands: every charge was `spend(len(commands))`, which is
# zero for a page holding an empty list, so an 18.4 KB map describing four
# million (event, page) pairs was walked in full -- `search` taking 5.7s and
# reporting no stop signal at all, because the deadline it does have is only
# tested inside the command loop that never ran.
MAX_WALK_STEPS = 200_000


class WalkBudget:
    """How much of the event walk one call has left, and whether it ran out.

    Passed in by the caller rather than started per traversal, so a tool that
    walks several times -- validate runs a command check and an asset check --
    spends one allowance across all of them. `marshal_rb` bounds how deeply a
    file nests and nothing bounds how much it can share, so this is the only
    thing standing between a crafted file and a walk the user has to kill the
    server to escape.

    Every caller that can run out has to say so in its reply: a search that
    stopped early and reports no hits is indistinguishable from one that
    looked everywhere, which is the worst shape of answer available here.
    """

    __slots__ = ("remaining", "exhausted")

    def __init__(self, limit: int = MAX_WALK_STEPS):
        self.remaining = limit
        self.exhausted = False

    def spend(self, steps: int) -> bool:
        """Charge for one visit. False once there is nothing left.

        At least one step, whatever ``steps`` says. A caller inside a loop
        that charges nothing per iteration has no bound at all, and every
        charge in this project was `spend(len(commands))` -- zero for a page
        holding an empty list. Enforced here rather than at each call site for
        the same reason the finding ceiling lives on `_AssetChecker`: the one
        call site that carried a guard is the one that got it wrong.
        """
        if self.exhausted:
            return False
        self.remaining -= max(1, steps)
        if self.remaining < 0:
            self.exhausted = True
            return False
        return True

    def take(self, commands: int) -> int:
        """Charge for as much of one list as there is allowance left for.

        Returns how many are affordable, which is fewer than asked for exactly
        when the walk has run out. `spend` is the all-or-nothing form and is
        right inside a loop: a page is rendered whole or not at all, because a
        reply of truncated fragments from two hundred pages is not worth
        reading. One common event has no such loop, so a prefix beats nothing.
        """
        if self.exhausted:
            return 0
        if commands <= self.remaining:
            self.remaining -= commands
            return commands
        affordable = self.remaining
        self.remaining = 0
        self.exhausted = True
        return affordable

    def note(self, what: str) -> "str | None":
        """What to tell the caller, or None if the walk finished."""
        if not self.exhausted:
            return None
        return ("this project's events, pages and commands together number "
                "more than the %d visits allowed for %s in one call, so "
                "the walk stopped "
                "early and this answer is incomplete. That happens when a "
                "file shares objects by reference far more than the editor "
                "does -- run validate, and narrow the request (fewer maps, or "
                "one event) to see the rest." % (MAX_WALK_STEPS, what))


def iter_command_lists(project: Project, map_ids: list[int] | None = None,
                       budget: "WalkBudget | None" = None
                       ) -> Iterator[tuple[Location, list[Any]]]:
    """Every event command list in the project, with where it came from.

    Covers map event pages, common events and troop battle event pages -- the
    three places an RPG Maker project stores an interpreter program.

    ``map_ids`` narrows the map pages only. Common events and troop pages
    belong to the project rather than to any map, so they are always included;
    every caller that reports a map count says so separately.

    ``budget`` bounds how many commands the walk visits in total. Without one
    it is unbounded, which is right for a caller that has already bounded
    itself and wrong for anything reading a file it did not write -- see
    :class:`WalkBudget`.
    """
    for map_id, game_map in iter_maps(project, map_ids):
        events = game_map.get("events")
        if not isinstance(events, RubyHash):
            continue
        for event_id, event in events.sorted_items():
            # One step for the event, and one for every page slot, before
            # anything about them is read. This is where the product is
            # bought: charging only for commands meant a map of empty pages
            # cost nothing, and skipping a slot that is not an RPG::Event::Page
            # would leave a list of four million integers free as well.
            if budget is not None and not budget.spend(1):
                return
            if not isinstance(event, RubyObject):
                continue
            name = _text(project, event.get("name"))
            for page_index, page in enumerate(_seq(event.get("pages"))):
                if budget is not None and not budget.spend(1):
                    return
                if isinstance(page, RubyObject):
                    commands = _seq(page.get("list"))
                    if budget is not None and not budget.spend(len(commands)):
                        return
                    yield (_event_at(map_id, event_id, name, page_index),
                           commands)

    # The same charge, before the same isinstance test, in the two branches
    # the rule above was written for and did not reach. Charging only
    # `len(commands)` meant a row that is not an RPG::CommonEvent cost
    # nothing; and the troop branch is the one that costs a *product*, since
    # a Troops array of a million links to one troop whose @pages is a
    # million integers is four megabytes on disk and 10^12 iterations here.
    for entry_id, common in _safe_entries(project, "common_events"):
        if budget is not None and not budget.spend(1):
            return
        if isinstance(common, RubyObject):
            commands = _seq(common.get("list"))
            if budget is not None and not budget.spend(len(commands)):
                return
            yield (Location("common_event", entry_id=entry_id), commands)

    for entry_id, troop in _safe_entries(project, "troops"):
        if budget is not None and not budget.spend(1):
            return
        if not isinstance(troop, RubyObject):
            continue
        for page_index, page in enumerate(_seq(troop.get("pages"))):
            if budget is not None and not budget.spend(1):
                return
            if isinstance(page, RubyObject):
                commands = _seq(page.get("list"))
                if budget is not None and not budget.spend(len(commands)):
                    return
                yield (Location("troop_page", entry_id=entry_id,
                                page=page_index), commands)


def iter_maps(project: Project, map_ids: list[int] | None = None
              ) -> Iterator[tuple[int, RubyObject]]:
    """Each map that loads, with its id.

    A map that will not load is skipped silently here. ``check_maps`` is the
    one rule that reports unreadable maps, so reporting it again from every
    walk would mean one broken file produced half a dozen findings.
    """
    ids = project.map_ids_on_disk() if map_ids is None else map_ids
    for map_id in ids:
        try:
            game_map = project.load_map(map_id)
        except (ProjectError, ValueError, OSError):
            continue
        if isinstance(game_map, RubyObject):
            yield map_id, game_map


def _safe_entries(project: Project, table: str) -> list[tuple[int, Any]]:
    """Rows of a table, or nothing when it cannot be read.

    Only for walks that do not turn absence into a finding. Anything comparing
    ids against a table must use :class:`_Tables`, which can tell "empty" from
    "unreadable".
    """
    try:
        return project.entries(table)
    except (ProjectError, ValueError, OSError):
        return []


def _map_file_name(project: Project, map_id: int) -> str:
    """The file a map id names, or a description of why it names none."""
    try:
        return project.map_file(map_id)
    except (ProjectError, ValueError):
        return "no file name is possible for id %s" % map_id
