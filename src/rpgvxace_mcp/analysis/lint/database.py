"""Rules about the database tables, the tilesets and the system."""

from __future__ import annotations

from typing import NamedTuple

from ...project import (
    Project,
    ProjectError,
)
from ...rubytypes import RubyObject
from ...schema import (
    TILESET_SHEET_NAMES,
    on_map,
)
from ..references import TABLE_NOUNS
from ..walk import (
    WalkBudget,
    _db_at,
    _int,
    _map_at,
    _seq,
    _text,
    iter_maps,
)
from .findings import (
    ERROR,
    Finding,
    _Findings,
    _Tables,
    _missing,
)
from .assets import _AssetChecker


def check_tilesets(project: Project, map_ids: list[int], tables: _Tables,
                   budget: "WalkBudget | None" = None,
                   collected: "_Findings | None" = None) -> list[Finding]:
    """VX Ace only: every map names a tileset, and tilesets name graphics."""
    if not project.profile.has_tileset_database:
        return []
    findings = _Findings.given(collected)
    tileset_ids = tables.ids("tilesets")
    if tileset_ids is None:
        return findings.items

    for map_id, game_map in iter_maps(project, map_ids):
        if findings.full:
            break
        tileset_id = _int(game_map.get("tileset_id"))
        if tileset_id is not None and tileset_id not in tileset_ids:
            findings.add(_missing(
                "missing_tileset",
                "map %s uses tileset %s" % (map_id, tileset_id),
                "Pick an existing tileset for the map, or add that tileset to "
                "the database.", _map_at(map_id, "tileset_id")))

    # The sheet names are asset references, so they go through the same checker
    # as every other one: it is what knows about the RTP and about names that
    # differ only in case.
    checker = _AssetChecker(project)
    for entry_id, tileset in tables.rows_of("tilesets"):
        # `checker.full` is the bound that matters, because this loop
        # appends there: the guard here used to test the map loop's
        # `findings`, which this loop never touches, so it could not fire.
        # `findings.full` stops the iterating too -- a tileset carries nine
        # sheet names and the row count comes out of the file -- but cannot
        # change what this returns, so it carries no guard.
        if checker.full or findings.full:
            break
        if budget is not None and not budget.spend(1):
            break
        if not isinstance(tileset, RubyObject):
            continue
        name = _text(project, tileset.get("name"))
        for index, sheet_name in enumerate(_seq(tileset.get("tileset_names"))):
            # Per sheet slot. `_AssetChecker.check` returns before it counts
            # anything when the name is empty, so `checker.full` never became
            # true and the row-times-slot product had no bound at all -- and
            # both factors come out of the file.
            if budget is not None and not budget.spend(1):
                break
            sheet = (TILESET_SHEET_NAMES[index]
                     if index < len(TILESET_SHEET_NAMES) else str(index))
            # The context is a function: it formats four values into a
            # sentence that `check` reads only when it reports, and this
            # runs per sheet slot of every tileset row.
            checker.check(sheet_name, "tilesets",
                          _db_at("tilesets", entry_id, "tileset_names"),
                          context=lambda: "%s %s (%s) sheet %s"
                          % (TABLE_NOUNS["tilesets"], entry_id, name, sheet))
    # Into the one collector rather than concatenated beside it:
    # returning two independently-bounded lists was this file's own example
    # of "two ceilings of MAX_FINDINGS are a ceiling of twice that", which
    # is `_parent_cycles`' phrase in `maps.py`.
    findings.extend(checker.findings)
    return findings.items


class _NestedRef(NamedTuple):
    """A database reference that lives one level down.

    A row of ``owner`` holds a list under ``field``; each object in it names a
    row of ``target`` under ``child_field``. Named rather than a bare tuple
    because five strings in a row is exactly the sort of thing that gets
    reordered by accident.
    """

    owner: str
    field: str
    child_field: str
    target: str
    sentence: str


NESTED_DB_REFERENCES = (
    _NestedRef("classes", "learnings", "skill_id", "skills",
               "class %s learns skill %s"),
    _NestedRef("troops", "members", "enemy_id", "enemies",
               "troop %s includes enemy %s"),
)


def check_database(project: Project, tables: _Tables,
                   budget: "WalkBudget | None" = None,
                   collected: "_Findings | None" = None) -> list[Finding]:
    """Ids one database table holds for another.

    Each check walks one table's rows, so the ceiling is tested between them
    rather than inside each loop: that bounds the whole function to one
    table's overshoot without a guard in every one of a dozen loops.
    """
    findings = _Findings.given(collected)

    # `findings.room` is the ceiling; this closure was the fourth spelling
    # of it in one file.
    class_ids = tables.ids("classes")
    if class_ids is not None and findings.room:
        for entry_id, actor in tables.rows_of("actors"):
            if budget is not None and not budget.spend(1):
                break
            if not isinstance(actor, RubyObject) or not actor.has("class_id"):
                continue
            class_id = _int(actor.get("class_id"))
            if class_id is not None and class_id not in class_ids:
                    # The noun from `TABLE_NOUNS`, like the loop below it, the
        # one above it, and `_check_references` in `command_rules`.
                # Spelled by hand here while the table carried an entry for
                # it, which is how a table with two live readers came to have
                # entries nothing read.
                noun = TABLE_NOUNS["classes"]
                findings.add(_missing(
                    "missing_%s" % noun,
                    "actor %s has class_id %s" % (entry_id, class_id),
                    "Set class_id to a %s in the Classes table, or add that "
                    "%s." % (noun, noun),
                    _db_at("actors", entry_id, "class_id")))

    for ref in NESTED_DB_REFERENCES:
        if not findings.room:
            break
        target_ids = tables.ids(ref.target)
        if target_ids is None:
            continue
        noun = TABLE_NOUNS[ref.target]
        for entry_id, row in tables.rows_of(ref.owner):
            if not findings.room:
                # Inside the row loop as well as outside it: one owner table
                # with a million rows is one iteration of the loop above.
                break
            if budget is not None and not budget.spend(1):
                break
            if not isinstance(row, RubyObject):
                continue
            for child in _seq(row.get(ref.field)):
                # Per child, because `findings.room` is tested per owner row and a
                # child that is not an RPG object continues for free: one row
                # whose @members is a shared million-entry Array is a
                # product, and both factors come out of the file.
                if budget is not None and not budget.spend(1):
                    break
                if not isinstance(child, RubyObject):
                    continue
                value = _int(child.get(ref.child_field))
                if value is not None and value not in target_ids:
                    findings.add(_missing(
                        "missing_%s" % noun,
                        ref.sentence % (entry_id, value),
                        "Point it at an existing %s, or add that %s."
                        % (noun, noun),
                        _db_at(ref.owner, entry_id, ref.field)))

    _check_system(project, tables.ids("actors"), budget, findings)
    return findings.items


def _check_system(project: Project, actor_ids: "set[int] | None",
                  budget: "WalkBudget | None" = None,
                  collected: "_Findings | None" = None) -> list[Finding]:
    """The System file: the party, and where a new game starts.

    Takes the caller's collector like its siblings. It kept a plain list
    bounded by its own `MAX_FINDINGS`, on top of whatever `check_database`
    already held -- which is the "two ceilings of MAX_FINDINGS are a
    ceiling of twice that" shape this package's docstrings say was removed,
    still standing in the one check that did not take a collector.
    """
    findings = _Findings.given(collected)
    try:
        system = project.load_data("system")
    except (ProjectError, ValueError, OSError) as exc:
        findings.add(Finding(
            ERROR, "system_unreadable",
            "System could not be read (%s), so the starting party and start "
            "position were not checked." % exc, _db_at("system", 0)))
        return findings.items
    if not isinstance(system, RubyObject):
        return findings.items
    if actor_ids is not None:
        for raw_id in _seq(system.get("party_members")):
            # The only lint loop that appended findings with neither a
            # budget nor a ceiling: @party_members is an Array out of the
            # System file, so five million two-byte Fixnums in a 10 MB file
            # were five million Finding objects, which `_report` then
            # sorted and counted three times.
            if findings.full:
                break
            if budget is not None and not budget.spend(1):
                break
            actor_id = _int(raw_id)
            if actor_id is not None and actor_id not in actor_ids:
                findings.append(_missing(
                    "missing_party_member",
                    "the starting party includes actor %s" % actor_id,
                    "Remove it from party_members, or add that actor.",
                    _db_at("system", 0, "party_members")))
    start_map = _int(system.get("start_map_id"))
    if start_map:
        on_disk = project.map_ids_present()
        if start_map not in on_disk:
            findings.append(_missing(
                "missing_start_map",
                "the party starts on map %s" % start_map,
                "Set the start position to a map that exists, or the game will "
                "not boot.", _db_at("system", 0, "start_map_id")))
        else:
            findings.extend(_check_start_position(project, system, start_map))
    return findings.items


def _check_start_position(project: Project, system: RubyObject,
                          start_map: int) -> list[Finding]:
    """The start coordinates against the size of the start map.

    The same check ``_check_transfer`` does, on the one transfer the engine
    performs before the player can do anything about it.
    """
    x, y = _int(system.get("start_x")), _int(system.get("start_y"))
    if x is None or y is None:
        return []
    try:
        game_map = project.load_map(start_map)
    except (ProjectError, ValueError, OSError):
        return []
    width, height = _int(game_map.get("width")), _int(game_map.get("height"))
    if width is None or height is None:
        return []
    if not on_map(x, y, width, height):
        return [Finding(
            ERROR, "start_position_outside_map",
            "the party starts at (%s, %s) on map %s, which is only %sx%s, so "
            "the game boots with the party off the map. Move the start "
            "position inside it." % (x, y, start_map, width, height),
            _db_at("system", 0, "start_x"))]
    return []
