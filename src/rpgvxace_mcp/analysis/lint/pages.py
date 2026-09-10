"""Rules about event pages: which one runs, and which can never run."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any

from ...commands import (
    CODE_CALL_COMMON_EVENT,
    CODE_ERASE_EVENT,
    CODE_GAME_OVER,
    CODE_RETURN_TO_TITLE,
    CODE_SCRIPT,
    CODE_SCRIPT_MORE,
    CODE_TRANSFER_PLAYER,
)
from ...project import Project
from ...rubytypes import (
    RubyHash,
    RubyObject,
)
from ...schema import (
    PAGE_CONDITION_FLAGS,
    TRIGGER_AUTORUN,
)
from ..walk import (
    WalkBudget,
    _event_at,
    _int,
    _seq,
    _text,
    iter_maps,
)
from .assets import (
    _AssetChecker,
    map_assets,
    page_graphic,
)
from .findings import (
    Finding,
    WARNING,
    _Findings,
)


def check_event_pages(project: Project, map_ids: list[int],
                      budget: "WalkBudget | None" = None,
                      collected: "_Findings | None" = None,
                      checker: "_AssetChecker | None" = None
                      ) -> list[Finding]:
    """Page-level rules: pages a later page hides, and autoruns that never end.

    Both turn on the same engine fact: RPG Maker runs the *last* page whose
    conditions are satisfied, so a page with no conditions at all hides every
    page before it, and it is the final page being an unconditional Autorun
    that traps the player.

    ``checker`` makes this the map and page *asset* walk too. `validate`
    passes one when it was asked for both the events and the assets scope,
    which the default run is -- and this walk already has the map open, the
    events sorted, the event name decoded and each page in hand, which is
    every value `_check_map_assets` opened a second full pass over the map
    set to get.
    """
    findings = _Findings.given(collected)
    for map_id, game_map in iter_maps(project, map_ids):
        if checker is not None:
            map_assets(checker, map_id, game_map)
        events = game_map.get("events")
        if not isinstance(events, RubyHash):
            continue
        for event_id, event in events.sorted_items():
            # Two findings per event at most, and the event count comes out of
            # the file: a crafted map that shares one event object across
            # forty thousand slots produced forty thousand findings here.
            if findings.full:
                return findings.items
            # And a ceiling on findings cannot bound a walk that finds
            # nothing: this rule collects only for an *unconditional* page, so
            # one condition flag set on a crafted page made it walk every page
            # of every event for free. This walk does not go through
            # `iter_command_lists`, so it needs its own charge.
            if budget is not None and not budget.spend(1):
                return findings.items
            if not isinstance(event, RubyObject):
                continue
            pages = _seq(event.get("pages"))
            if budget is not None and not budget.spend(len(pages)):
                return findings.items
            name = _text(project, event.get("name"))
            findings.extend(_check_shadowing(map_id, event_id, name,
                                             pages))
            findings.extend(_check_autorun(map_id, event_id, name, pages,
                                           budget))
            if checker is not None:
                for index, page in enumerate(pages):
                    page_graphic(checker, map_id, event_id, name,
                                 index, page)
    return findings.items


def _is_unconditional(page: Any) -> bool:
    """Whether a page's conditions are all switched off.

    Such a page always matches, so it wins whenever the engine reaches it.
    """
    if not isinstance(page, RubyObject):
        return False
    condition = page.get("condition")
    if not isinstance(condition, RubyObject):
        return False
    return not any(condition.get(flag) for flag in PAGE_CONDITION_FLAGS)


def _check_shadowing(map_id: int, event_id: int, name: str,
                     pages: list[Any]) -> list[Finding]:
    """Report the highest unconditional page, and only that one.

    Reporting each of several would make overlapping claims and name the wrong
    culprit: with pages 2 and 3 both unconditional it is page 3, the last one,
    that actually decides.
    """
    last_unconditional = None
    for index, page in enumerate(pages):
        if index and _is_unconditional(page):
            last_unconditional = index
    if last_unconditional is None:
        return []
    return [Finding(
        WARNING, "shadowed_pages",
        "page %s of event %s (%s) has no conditions, so pages 0 to %s can "
        "never run -- the last page whose conditions match is the one that "
        "runs. Give this page a condition, or move it before the ones it hides."
        % (last_unconditional, event_id, name, last_unconditional - 1),
        _event_at(map_id, event_id, name, last_unconditional),
        "page conditions of every multi-page event")]


# Commands that end an unconditional autorun page's grip on the player: moving
# them to another map, erasing the event for this visit, or ending the game.
CONTROL_RELEASING_CODES = frozenset({CODE_TRANSFER_PLAYER, CODE_ERASE_EVENT,
                                     CODE_GAME_OVER, CODE_RETURN_TO_TITLE})


# Commands that might release control somewhere this file cannot follow: a
# common event that transfers, or a script calling reserve_transfer or goto. A
# page containing one of these is left alone rather than reported.
OPAQUE_ESCAPE_CODES = frozenset({CODE_CALL_COMMON_EVENT, CODE_SCRIPT,
                                 CODE_SCRIPT_MORE})


def _check_autorun(map_id: int, event_id: int, name: str,
                   pages: list[Any],
                   budget: "WalkBudget | None" = None) -> list[Finding]:
    """Report a last page that is Autorun and never gives control back.

    Only the last page, because that is the one whose conditions win. A
    page that transfers the player, erases the event or ends the game is
    left alone, and so is one holding a Call Common Event or a Script,
    which this file cannot see through -- suppressing a real finding now
    and then costs less than sending an agent to edit a cutscene that
    works.
    """
    if not pages:
        return []
    last = pages[-1]
    if not isinstance(last, RubyObject) or not _is_unconditional(last):
        return []
    if last.get("trigger") != TRIGGER_AUTORUN:
        return []
    commands = _seq(last.get("list"))
    # Charged before the two scans, not after. `_has_code` walks the whole
    # list twice and neither pass short-circuits on the codes a crafted file
    # simply omits, so a 2.5 MB map -- one command, a @list of a million
    # links to it, one unconditional Autorun page, an events hash of
    # 100,000 links to one event -- put two unbudgeted million-step scans
    # behind a per-event charge of two.
    if budget is not None and not budget.spend(len(commands)):
        return []
    if _has_code(commands, OPAQUE_ESCAPE_CODES):
        # A common event or a script could transfer the player, erase the event
        # or change scene, and this file cannot see through either. Suppressing
        # the finding costs a real one now and then; reporting it would send an
        # agent editing a cutscene that works.
        return []
    if _has_code(commands, CONTROL_RELEASING_CODES):
        return []
    return [Finding(
        WARNING, "unstoppable_autorun",
        "the last page (%s) of event %s (%s) is Autorun with no page "
        "conditions and never transfers the player, erases itself or ends the "
        "game, so it restarts as soon as it finishes and the player never "
        "regains control. Give the page a condition it can turn off (a self "
        "switch is the usual way), or add a Transfer Player or Erase Event."
        % (len(pages) - 1, event_id, name),
        _event_at(map_id, event_id, name, len(pages) - 1),
        "the last page of every event, since that is the page that wins when "
        "its conditions match")]


def _has_code(commands: list[Any], codes: AbstractSet[int]) -> bool:
    return any(isinstance(command, RubyObject) and _int(command.get("code")) in codes
               for command in commands)
