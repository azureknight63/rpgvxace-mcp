"""Rules about the maps themselves: the tree, the sizes, the encounters."""

from __future__ import annotations

from typing import Any

from ...convert import brief
from ...project import (
    MAX_MAP_ID,
    Project,
    ProjectError,
)
from ...rubytypes import (
    RubyObject,
    Table,
)
from ..walk import (
    WalkBudget,
    _int,
    _map_at,
    _map_file_name,
    _seq,
    _text,
)
from .findings import (
    ERROR,
    Finding,
    WARNING,
    _Findings,
    _Tables,
    _missing,
)


def check_map_tree(project: Project, budget: "WalkBudget | None" = None,
                   collected: "_Findings | None" = None) -> list[Finding]:
    """The editor's map tree against the files on disk.

    Collects into one `_Findings` across all three of its passes. Each pass
    used to bound a list of its own and the three were then concatenated, so
    a ceiling of MAX_FINDINGS produced up to three times that many -- the
    fourth time a ceiling here was written against the wrong list, after
    `_AssetChecker`'s four callers, `check_tilesets`, and `check_maps`.
    """
    findings = _Findings.given(collected)
    try:
        infos = dict(project.entries("map_infos"))
    except (ProjectError, ValueError, OSError, TypeError) as exc:
        return [Finding(ERROR, "map_infos_unreadable",
                        "MapInfos could not be read (%s), so the map tree could "
                        "not be checked. Restore that file, then validate again."
                        % exc)]
    on_disk = project.map_ids_present()

    for map_id, info in infos.items():
        if not findings.room:
            break
        # One step per tree entry. The entry count comes out of MapInfos,
        # and `MAX_FINDINGS` bounds only the entries that produce a finding.
        if budget is not None and not budget.spend(1):
            break
        name = _text(project, info.get("name")) if isinstance(info, RubyObject) else "?"
        if _int(map_id) is None or not 1 <= map_id <= MAX_MAP_ID:
            findings.add(Finding(
                ERROR, "bad_map_id",
                "MapInfos has an entry keyed %s, which is not an id any map "
                "file can have (they run 1 to %d). Remove that entry in the "
                "editor." % (brief(map_id), MAX_MAP_ID), None,
                "MapInfos keys"))
            continue
        if map_id not in on_disk:
            findings.add(Finding(
                ERROR, "missing_map_file",
                "MapInfos lists map %s (%s) but %s is not on disk. Either "
                "restore that file or delete the tree entry."
                % (map_id, name, _map_file_name(project, map_id)),
                _map_at(map_id), "MapInfos against the map files present"))
        parent = _int(info.get("parent_id")) if isinstance(info, RubyObject) else None
        if parent and parent not in infos:
            findings.add(Finding(
                WARNING, "missing_parent_map",
                "map %s (%s) has parent_id %s, which is not in the map tree, so "
                "the editor cannot place it. Set parent_id to an existing map "
                "or to 0 for the top level." % (map_id, name, parent),
                _map_at(map_id, "parent_id")))

    for map_id in sorted(on_disk - set(infos)):
        if not findings.room:
            break
        findings.add(Finding(
            WARNING, "orphan_map_file",
            "%s exists but no MapInfos entry points at it, so the editor will "
            "not show it. Add a tree entry or delete the file."
            % _map_file_name(project, map_id), _map_at(map_id)))

    _parent_cycles(infos, findings, budget)
    return findings.items


def _parent_cycles(infos: dict[Any, Any],
                   findings: "_Findings | None" = None,
                   budget: "WalkBudget | None" = None) -> list[Finding]:
    """Maps whose parent chain never reaches the top of the tree.

    One pass over the whole tree: walking each map's ancestry separately is
    quadratic, and a MapInfos chained parent-to-parent is a small file that
    would take a very long time.

    Appends to the caller's collector when given one, for the same reason
    `_check_encounters` does: a list of its own is a second ceiling, and two
    ceilings of MAX_FINDINGS are a ceiling of twice that.

    Returns that collector's whole contents -- everything in it, including
    what other checks have put there -- and not this check's findings
    alone. `check_map_tree` reads the collector it passed in; the return
    value is for the tests, which pass none.
    """
    findings = _Findings() if findings is None else findings
    settled: dict[int, bool] = {}   # map id -> is its chain sound
    for start in infos:
        if not findings.room:
            break
        if _int(start) is None or start in settled:
            continue
        chain: list[int] = []
        seen: set[int] = set()
        current: Any = start
        looped = False
        while True:
            current = _int(current)
            if current is None or current == 0:
                break               # reached the top of the tree
            if current in settled:
                looped = not settled[current]
                break
            if current in seen:
                looped = True
                break
            seen.add(current)
            chain.append(current)
            info = infos.get(current)
            current = info.get("parent_id") if isinstance(info, RubyObject) else 0
        for map_id in chain:
            settled[map_id] = not looped
            # Inside the chain loop, not only around it. The ceiling was on
            # the outer `for start in infos`, so one MapInfos that is a
            # single cycle through every entry reached this loop once and
            # appended a finding per entry -- past the ceiling, in one
            # outer iteration.
            if not findings.room:
                break
            if budget is not None and not budget.spend(1):
                break
            if looped:
                findings.add(Finding(
                    ERROR, "map_tree_cycle",
                    "map %s has a parent chain that never reaches the top of "
                    "the tree, so the editor's tree is infinite. Set parent_id "
                    "to 0 on one map in the loop." % map_id,
                    _map_at(map_id, "parent_id"), "every parent_id chain"))
    return findings.items


def check_maps(project: Project, map_ids: list[int], tables: _Tables,
               budget: "WalkBudget | None" = None,
               collected: "_Findings | None" = None) -> list[Finding]:
    """Per-map properties: readability, encounters and the tile table.

    Collects into a `_Findings`, so the ceiling holds across maps as well as
    within one. This loop used to build a fresh list per map and discard the
    one `_check_encounters` had carefully bounded, which made that guard
    describe nothing: two maps against a ceiling of four collected eight, and
    the map count comes out of the project rather than from anything here.
    """
    findings = _Findings.given(collected)
    troop_ids = tables.ids("troops")

    for map_id in map_ids:
        if not findings.room:
            break
        if budget is not None and not budget.spend(1):
            break
        try:
            game_map = project.load_map(map_id)
        except (ProjectError, ValueError, OSError) as exc:
            findings.add(Finding(
                ERROR, "map_unreadable",
                "map %s could not be read: %s. Restore it from a backup, or "
                "delete it and its MapInfos entry." % (map_id, exc),
                _map_at(map_id)))
            continue
        if not isinstance(game_map, RubyObject):
            findings.add(Finding(
                ERROR, "map_unreadable",
                "map %s does not hold an RPG::Map object, so nothing about it "
                "could be checked" % map_id, _map_at(map_id)))
            continue
        # The collector is passed down rather than the return value being
        # extended, so a map's findings are never built only to be truncated.
        # `extend` would bound it either way -- that is where the ceiling
        # lives -- so this is an efficiency refinement and carries no guard.
        _check_encounters(project, map_id, game_map, troop_ids, findings,
                          budget)
        # Bounded by the map's own fields rather than by anything from the
        # file, so `extend` is enough: it takes what fits.
        findings.extend(_check_map_dimensions(map_id, game_map))
    return findings.items


def _check_encounters(project: Project, map_id: int, game_map: RubyObject,
                      troop_ids: "set[int] | None",
                      findings: "_Findings | None" = None,
                      budget: "WalkBudget | None" = None) -> list[Finding]:
    """Encounters against the Troops table, and the encounter step.

    Appends to the caller's collector when given one, which is what makes the
    ceiling hold across maps: this used to bound a list of its own that
    `check_maps` then discarded.

    What it returns is that collector's whole contents, not just this
    check's findings -- so a caller reading the return value of two
    checks in a row would see the first check's findings twice. `check_maps`
    reads the collector; the return value is for the tests, which pass
    none.
    """
    findings = _Findings() if findings is None else findings
    encounters = _seq(game_map.get("encounter_list"))
    if troop_ids is not None:
        for entry in encounters:
            # The list's length comes out of the map file, and an entry that
            # names a troop that exists produces no finding -- so the
            # collector ceiling bounds the reporting and not the walking.
            if budget is not None and not budget.spend(1):
                break
            # The list length comes out of the file, and one bad troop id
            # repeated is one finding each.
            if not findings.room:
                break
            troop_id = _encounter_troop_id(entry)
            if troop_id is not None and troop_id not in troop_ids:
                findings.add(_missing(
                    "missing_troop",
                    "map %s can encounter troop %s" % (map_id, troop_id),
                    "Point that encounter at a troop in the Troops table, or "
                    "remove the entry.",
                    _map_at(map_id, "encounter_list")))
    if encounters and not game_map.get("encounter_step"):
        findings.add(Finding(
            WARNING, "zero_encounter_step",
            "map %s has encounters but an encounter_step of 0. Set it to the "
            "number of steps between battles (the editor's default is 30)."
            % map_id, _map_at(map_id, "encounter_step")))
    return findings.items


def _check_map_dimensions(map_id: int, game_map: RubyObject) -> list[Finding]:
    """The tile table against the size the map claims."""
    data = game_map.get("data")
    if data is None:
        return []
    if not isinstance(data, Table):
        # The codec hands back the raw payload when a Table will not decode,
        # and wraps a Table in RubyIvars when the file carried an encoding
        # record. Either way there is no tile table to measure.
        return [Finding(
            ERROR, "tile_data_unreadable",
            "map %s has a @data that is not a tile table (it decoded as %s), so "
            "its size could not be checked and the map tools cannot edit it"
            % (map_id, type(data).__name__), _map_at(map_id, "data"))]
    width, height = _int(game_map.get("width")), _int(game_map.get("height"))
    if width is None or height is None:
        return [Finding(
            ERROR, "map_size_unreadable",
            "map %s does not give a usable width and height, so its tile table "
            "could not be checked" % map_id, _map_at(map_id, "width"))]
    if data.xsize != width or data.ysize != height:
        return [Finding(
            ERROR, "tile_data_size_mismatch",
            "map %s says it is %sx%s but its tile table is %sx%s. Resize the "
            "map in the editor so the two agree."
            % (map_id, width, height, data.xsize, data.ysize),
            _map_at(map_id, "data"))]
    return []


def _encounter_troop_id(entry: Any) -> int | None:
    """The troop id of an encounter entry, whichever engine wrote it.

    VX keeps a plain array of troop ids; VX Ace wraps each one in an
    ``RPG::Map::Encounter`` alongside its weight and region set.
    """
    if isinstance(entry, int):
        return entry
    if isinstance(entry, RubyObject):
        return _int(entry.get("troop_id"))
    return None


class _MapSizes:
    """Map id to (width, height), parsed on first ask and remembered.

    A dict was wrong for one reason only: building it read every map file in
    the project. The rules that ask this reach for a handful of ids -- the
    targets of the Transfer Player commands actually present -- so almost all
    of that reading was for entries nobody looked up.

    A map that will not parse answers (None, None), the same as a map with no
    width: `check_maps` reports an unreadable file, and answering "unknown
    size" here keeps this from reporting a second finding about it.
    """

    __slots__ = ("project", "_sizes")

    def __init__(self, project: Project):
        self.project = project
        self._sizes: dict[int, tuple["int | None", "int | None"]] = {}

    def get(self, map_id: int) -> "tuple[int | None, int | None]":
        """The size of one map, parsed on first ask.

        No default parameter, because no path reaches one: every id is
        answerable here, since a map that will not parse answers
        (None, None) rather than being absent. It had one, unread, kept "so
        the call site reads unchanged" -- which is an argument for changing
        the call site.
        """
        if map_id not in self._sizes:
            try:
                game_map = self.project.load_map(map_id)
            except (ProjectError, ValueError, OSError):
                self._sizes[map_id] = (None, None)
            else:
                self._sizes[map_id] = (
                    (_int(game_map.get("width")), _int(game_map.get("height")))
                    if isinstance(game_map, RubyObject) else (None, None))
        return self._sizes[map_id]
