"""Rules about files on disk, and the one checker that knows what is there.

Three walks are here, and a fourth -- `check_tilesets` -- is in
`database.py`, because a tileset's nine sheets are files on disk.

The three here share one `_AssetChecker`. `check_tilesets` builds its own,
and `validate` shares one across the events and assets scopes only when
both are selected -- so the number of checkers in a run is between one and
two, and it is not four. The finding ceiling lives on the collector rather
than at the call sites for the same reason either way: the one site that
carried the guard had written it against the wrong list.
"""

from __future__ import annotations

from typing import Any, Callable

from ...commands import (
    AUDIO_COMMAND_KINDS,
    CODE_BLANK,
    CODE_SHOW_TEXT,
)
from ...project import (
    ASSET_DIRS,
    Project,
)
from ...rubytypes import (
    RubyHash,
    RubyObject,
)
from ..walk import (
    Location,
    WalkBudget,
    _db_at,
    _event_at,
    _int,
    _map_at,
    _safe_entries,
    _seq,
    _text,
    iter_command_lists,
    iter_maps,
)
from .findings import (
    Finding,
    INFO,
    MAX_FINDINGS,
    WARNING,
)


class _AssetChecker:
    """Reports references to graphic and audio files that are not there.

    One object rather than a closure so the two facts every such finding needs
    -- the asset pool for a folder, and whether the pool is complete -- are
    looked up here instead of being passed in at every call site. Passing a
    pool and a folder name separately is how a check ends up reporting the
    wrong folder.
    """

    def __init__(self, project: Project):
        self.project = project
        self.findings: list[Finding] = []
        # A project whose RTP could not be found references files this server
        # cannot see, so a miss is only evidence of a problem when the pool is
        # complete. This is the guard that removed 185 false warnings.
        self.complete = project.assets_complete
        self.severity = WARNING if self.complete else INFO
        # Folded name -> the real spelling, per asset kind, built on first
        # use. See the case-mismatch branch of `check`.
        self._folded: dict[str, dict[str, str]] = {}

    def folded(self, kind: str) -> dict[str, str]:
        """One asset kind's names, keyed by their case-folded form.

        Built once per kind and per checker. The case-mismatch branch of
        `check` scanned the whole pool with `str.lower()` on every miss, and
        the finding ceiling allows five thousand of those.
        """
        if kind not in self._folded:
            self._folded[kind] = {name.lower(): name
                                  for name in self.project.asset_names(kind)}
        return self._folded[kind]

    @property
    def full(self) -> bool:
        """Whether this has collected all it is going to.

        The ceiling lives on the collector because several walks share
        one of these and a guard written at a call site is a guard that can
        be written against the wrong list -- which is exactly what
        `check_tilesets` did: it tested its own `findings` while its loop
        appended here, so the break could never fire. Another caller gets
        this for free.
        """
        return len(self.findings) >= MAX_FINDINGS

    def check(self, name: Any, kind: str,
              where: "Location | Callable[[], Location]",
              rule: str = "missing_graphic",
              context: "str | Callable[[], str]" = "") -> None:
        """Report a reference to a file that is not there.

        ``where`` and ``context`` may each be a callable. Four of the five
        early returns below happen before either is read -- an empty name,
        a pool that could not be assembled, a name that is present -- and
        on a healthy project that is very nearly every call: the
        developer's own 39-map project produces two asset findings from
        about two thousand checks. Each of the other 1,998 built a
        `Location`, and `check_tilesets` formatted a four-value sentence,
        to be dropped.

        The callers that can build one for the price of the call still pass
        a value: `_map_at(map_id, field)` is two arguments and a
        constructor. The ones that decode a name or format a string pass a
        function.
        """
        if self.full:
            return
        text = _text(self.project, name)
        if not text:
            return
        pool = self.project.asset_names(kind)
        if not pool and not self.complete:
            # Nothing to compare against and a reason to expect that, so any
            # answer here would be a guess.
            return
        if text in pool:
            return
        location = where() if callable(where) else where
        context = context() if callable(context) else context
        folder = ASSET_DIRS.get(kind, kind)
        # `context` names what holds the reference when the location alone is
        # not enough to find it -- which tileset sheet, say. Without one the
        # message leads with the name, because the finding's own "where"
        # already says which field it came from.
        if context:
            lead, verb = '%s names "%s"' % (context, text), ", which is"
        else:
            lead, verb = '"%s"' % text, " is"

        # A name that differs only in case works on Windows and fails on a
        # case-sensitive filesystem, so it survives every local playtest. This
        # is certain regardless of whether the RTP was located: the file is
        # demonstrably there under another spelling.
        # Against a folded map built once per kind, not by folding the
        # pool again on every miss: the scan was `len(pool)` `str.lower()`
        # calls each time, the ceiling allows 5000 misses per checker, and a
        # VX Ace Characters pool with its RTP is ~400 names -- two million
        # redundant folds on exactly the broken project the ceiling exists
        # for, which is also the run the user cannot interrupt.
        near = self.folded(kind).get(text.lower())
        if near is not None:
            self.findings.append(Finding(
                WARNING, "asset_case_mismatch",
                '%s%s spelled "%s" in %s. Windows ignores the difference, so '
                "this loads while you develop and fails on a case-sensitive "
                "filesystem. Rename one of them to match."
                % (lead, verb, near, folder),
                location, "exact file names in %s" % folder))
            return

        if self.complete:
            message = ("%s%s not in %s. Add the file, or point the reference at "
                       "one that is there." % (lead, verb, folder))
        else:
            message = ("%s%s not in %s; it may come from %s, which could not be "
                       "located. Pass --rtp with that folder to check properly."
                       % (lead, verb, folder,
                          " or ".join(self.project.rtp_unresolved)))
        self.findings.append(Finding(
            self.severity, rule, message, location,
            "%d files in %s and any located RTP" % (len(pool), folder)))


def map_assets(checker: _AssetChecker, map_id: int,
               game_map: RubyObject) -> None:
    """The graphic and audio names one map carries itself.

    Public within the package, because two walks reach every map and
    either may be the one that runs: `_check_map_assets` below when the
    assets scope was asked for alone, and `check_event_pages` in `pages`
    when the events scope is selected too and has the map open already.

    One copy, here, in the module that owns `_AssetChecker`. Round 10f
    merged those walks by *copying* this body into `pages` and then
    describing the copy as a shared body in two docstrings -- so a reader
    was told the second copy did not exist, and nothing held the two equal.
    """
    checker.check(game_map.get("parallax_name"), "parallaxes",
                  _map_at(map_id, "parallax_name"))
    if game_map.get("specify_battleback"):
        for field, kind in (("battleback1_name", "battlebacks1"),
                            ("battleback2_name", "battlebacks2")):
            checker.check(game_map.get(field), kind, _map_at(map_id, field))
    for field in ("bgm", "bgs"):
        # Only when the map actually plays it. The editor keeps the name
        # when autoplay is unchecked, so a stale one is not a problem the
        # engine will ever hit.
        if not game_map.get("autoplay_" + field):
            continue
        track = game_map.get(field)
        if isinstance(track, RubyObject):
            checker.check(track.get("name"), field, _map_at(map_id, field),
                          "missing_audio")


def page_graphic(checker: _AssetChecker, map_id: int, event_id: int,
                 name: str, page_index: int, page: Any) -> None:
    """The character graphic one event page names."""
    if not isinstance(page, RubyObject):
        return
    graphic = page.get("graphic")
    if isinstance(graphic, RubyObject):
        checker.check(graphic.get("character_name"), "characters",
                      lambda: _event_at(map_id, event_id, name, page_index))


def command_assets(checker: _AssetChecker, code_int: "int | None",
                   code: Any, params: list[Any], location: Location,
                   index: int) -> None:
    """The two asset references one event command can carry.

    Reached from `_check_command_assets` below and from `check_commands`
    in `command_rules`, for the same reason `map_assets` has two callers:
    whichever walk is already reading the commands does these checks.
    """
    if code == CODE_SHOW_TEXT:
        if params:
            checker.check(params[0], "faces", lambda: location.at(index))
    elif code_int in AUDIO_COMMAND_KINDS:
        track = params[0] if params else None
        if isinstance(track, RubyObject):
            checker.check(track.get("name"), AUDIO_COMMAND_KINDS[code],
                          lambda: location.at(index), "missing_audio")


def check_assets(project: Project, map_ids: list[int],
                 budget: "WalkBudget | None" = None,
                 checker: "_AssetChecker | None" = None,
                 commands_done: bool = False,
                 pages_done: bool = False) -> list[Finding]:
    """Graphic and audio names, against the files present.

    Covers what an agent is most likely to break: map parallaxes and
    battlebacks, map BGM and BGS that actually autoplay, event page character
    graphics, Show Text faces, the four audio commands, actor character and
    face graphics, enemy battlers, and animation sheets.

    Not tileset sheets, though they are asset names too: those are checked by
    `check_tilesets`, which runs under the "maps" scope because a tileset is
    reached through the map that uses it. Asking for scopes=["assets"] alone
    does not check them.

    Not everything: RPG::System's own graphics and sounds, pictures, titles,
    and the graphics named by the commands that change an actor's appearance
    or a battle's music, are not checked. Naming the subset rather than
    claiming the whole set, because a caller who believes this is exhaustive
    will read a clean result as "no missing assets" when it means "none of the
    ones this looks at".
    """
    checker = _AssetChecker(project) if checker is None else checker
    if not pages_done:
        # Only when nothing else walked the maps. With the events scope
        # selected -- which the default run has -- `check_event_pages` does
        # these checks in the walk it already makes, and this would be a
        # second full pass over the map set.
        _check_map_assets(project, checker, map_ids, budget)
    if not commands_done:
        # Only when nothing else walked them. With the events scope selected
        # -- which the default run has -- `check_commands` does these two
        # checks in the loop it already runs, and this walk would be a
        # second full traversal charged to the same allowance.
        _check_command_assets(project, checker, map_ids, budget)
    _check_database_assets(project, checker, budget)
    return checker.findings


def _check_map_assets(project: Project, checker: _AssetChecker,
                      map_ids: list[int],
                      budget: "WalkBudget | None" = None) -> None:
    """Map, event and page graphic and audio references.

    The default run does not reach this: `check_event_pages` calls
    `map_assets` and `page_graphic` inside the walk it already makes. This
    is the path for `validate scopes=["assets"]`, which has no such walk to
    join -- the same arrangement `_check_command_assets` has.

    Takes the budget `_check_command_assets` has always taken. Without it the
    only bound here was the collector's finding ceiling, and a page whose
    graphic name is empty produces no finding at all -- `_AssetChecker.check`
    returns before it counts anything -- so a crafted map of empty graphics
    walked events x pages for nothing.
    """
    for map_id, game_map in iter_maps(project, map_ids):
        # The collector refuses past its ceiling on its own; this stops the
        # loading and decoding too. One crafted map is events x pages, and
        # those two numbers come out of the file.
        if checker.full:
            return
        map_assets(checker, map_id, game_map)
        events = game_map.get("events")
        if not isinstance(events, RubyHash):
            continue
        for event_id, event in events.sorted_items():
            if checker.full:
                return
            if budget is not None and not budget.spend(1):
                return
            if not isinstance(event, RubyObject):
                continue
            name = _text(project, event.get("name"))
            for page_index, page in enumerate(_seq(event.get("pages"))):
                if checker.full:
                    return
                if budget is not None and not budget.spend(1):
                    return
                if not isinstance(page, RubyObject):
                    continue
                page_graphic(checker, map_id, event_id, name,
                             page_index, page)


def _check_command_assets(project: Project, checker: _AssetChecker,
                          map_ids: list[int],
                          budget: "WalkBudget | None" = None) -> None:
    """The command asset references, when no other rule walked the lists.

    The default run does not reach this: `check_commands` calls
    `command_assets` inside the walk it already makes. This is the path for
    `validate scopes=["assets"]`, which has no such walk to join.
    """
    if not project.command_layouts_modelled:
        # An unmodelled engine's parameter layouts are different: XP's Show
        # Text keeps the message where Ace keeps the face graphic, so these
        # rules would invent a missing file for every line of dialogue.
        return
    for location, commands in iter_command_lists(project, map_ids, budget):
        if checker.full:
            break
        for index, command in enumerate(commands):
            if checker.full:
                break
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", CODE_BLANK)
            command_assets(checker, _int(code), code,
                           _seq(command.get("parameters")), location, index)


def _check_database_assets(project: Project, checker: _AssetChecker,
                           budget: "WalkBudget | None" = None) -> None:
    """Graphics named by the database rows: animations, actors, enemies.

    The last asset walk in this file; `check_tilesets` in `database.py` is
    the fourth. Each of the three blocks below takes the budget and tests
    `checker.full`, which this walk once had neither of: `_AssetChecker`
    returns before it counts anything for a name that is empty or already
    present, so the collector's ceiling cannot stop a walk on its own --
    an array of a million links to one row was a million free visits.
    """
    for entry_id, animation in _safe_entries(project, "animations"):
        if checker.full or (budget is not None and not budget.spend(1)):
            return
        if not isinstance(animation, RubyObject):
            continue
        for field in ("animation1_name", "animation2_name"):
            checker.check(animation.get(field), "animations",
                          _db_at("animations", entry_id, field))
    for entry_id, actor in _safe_entries(project, "actors"):
        if checker.full or (budget is not None and not budget.spend(1)):
            return
        if not isinstance(actor, RubyObject):
            continue
        for field, kind in (("character_name", "characters"),
                            ("face_name", "faces")):
            checker.check(actor.get(field), kind,
                          _db_at("actors", entry_id, field))
    for entry_id, foe in _safe_entries(project, "enemies"):
        if checker.full or (budget is not None and not budget.spend(1)):
            return
        if isinstance(foe, RubyObject):
            checker.check(foe.get("battler_name"), "battlers",
                          _db_at("enemies", entry_id, "battler_name"))
