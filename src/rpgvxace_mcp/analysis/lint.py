"""`validate`: every rule, and the report it builds.

The top layer, and the largest: it reads `walk` for its traversals and
`references` for the rules that say which command parameter names which
table. Nothing here is imported by either of them, which is what keeps the
layering acyclic -- and `misc_tools` is the only module in the tools layer
that imports anything from this file.

A finding says what is wrong, where, and what the rule inspected -- all
three, because "map 3 has a problem" is not something a caller can act on.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any, NamedTuple

from ..commands import (
    AUDIO_COMMAND_KINDS,
    CODE_BLANK,
    CODE_CALL_COMMON_EVENT,
    CODE_JUMP_TO_LABEL,
    CODE_LABEL,
    CODE_SCRIPT,
    CODE_SCRIPT_MORE,
    CODE_SHOW_TEXT,
    CODE_TRANSFER_PLAYER,
    TRANSFER_X,
    TRANSFER_Y,
    command_name,
)
from ..project import (
    ASSET_DIRS,
    MAX_MAP_ID,
    Project,
    ProjectError,
)
from ..rubytypes import RubyHash, RubyObject, Table
from ..schema import (
    PAGE_CONDITION_FLAGS,
    TILESET_SHEET_NAMES,
    TRIGGER_AUTORUN,
    on_map,
)
from .references import (
    MAP_TARGET,
    REFERENCE_RULES,
    TABLE_NOUNS,
    _referenced_ids,
)
from .walk import (
    Location,
    WalkBudget,
    _brief,
    _db_at,
    _event_at,
    _int,
    _map_at,
    _map_file_name,
    _safe_entries,
    _seq,
    _text,
    iter_command_lists,
    iter_maps,
)


# The checks `validate` knows how to run. It implements them, so it owns the
# list; the tool layer advertises this rather than keeping its own copy.
VALIDATE_SCOPES = ("maps", "events", "assets", "database")


ERROR, WARNING, INFO = "error", "warning", "info"


# Most serious first. The sort order, the counts and the severity constants all
# come from this one sequence.
SEVERITIES = (ERROR, WARNING, INFO)


# Two caller-facing ceilings, separately: `validate`'s default number of
# findings and `find_references`' default number of hits. One constant served
# both, so raising the first silently raised the second -- and they are
# answers to different questions asked by different tools.
DEFAULT_FINDING_LIMIT = 200


# A ceiling on findings collected, not just findings returned. A project broken
# enough -- or crafted carefully enough -- to produce a finding per event
# command would otherwise build millions of them in order to report two
# hundred. It is far above any real project's count, so reaching it means
# something is wrong with the project rather than with the rules.
#
# Enforced in every walk that appends inside a loop: the map tree, the
# parent-cycle pass, the maps, the tilesets (both loops), the encounters, the
# command walk, the event-page rules, and the asset walk -- where the ceiling
# belongs to `_AssetChecker` itself, since four walks share one and the one
# call site that carried the guard had written it against the wrong list.
# `check_database`'s own `room()` gates each of its blocks.
#
# Two earlier versions of this comment were wrong, in the two ways such a
# comment goes wrong: the first named two walks and claimed the database checks
# were "bounded by their tables and left to run" -- a table's entry count comes
# out of the file, so that is not a bound. The second listed walks that had no
# guard at all. Both are why `TestNoWalkCollectsPastTheCeiling` tests the walks
# directly with this patched small, rather than trusting the list above.
MAX_FINDINGS = 5000


class Finding:
    """One rule violation: what was wrong, where, and what the rule inspected."""

    __slots__ = ("severity", "rule", "message", "location", "checked")

    def __init__(self, severity: str, rule: str, message: str,
                 location: Location | None = None, checked: str = ""):
        self.severity = severity
        self.rule = rule
        self.message = message
        self.location = location
        self.checked = checked

    def as_dict(self) -> dict[str, Any]:
        out = {"severity": self.severity, "rule": self.rule, "message": self.message}
        if self.location is not None:
            out["where"] = self.location.describe()
            out["location"] = self.location.as_dict()
        if self.checked:
            out["checked"] = self.checked
        return out


def _missing(rule: str, sentence: str, remedy: str,
             location: Location | None = None, checked: str = "") -> Finding:
    """The commonest finding in this file: a reference with nothing behind it.

    Phrased in one place, and always with a remedy -- a message that only says
    what is wrong leaves the model to guess what to do about it.
    """
    return Finding(ERROR, rule, "%s, which does not exist. %s" % (sentence, remedy),
                   location, checked)


class _Tables:
    """The database tables the rules read, loaded once and asked many times.

    Two things live here. First, a table that could not be read is *not* an
    empty table: treating it as one turns every reference into a dangling
    reference, so a Troops file caught half-written by the editor would make
    every encounter in the project report a missing troop. Second, one run
    asks for the same tables from several checks, and a broken file should be
    reported once rather than once per check that noticed it.

    Loading is lazy, so a narrowly scoped run only reads what it needs.
    """

    def __init__(self, project: Project):
        self.project = project
        self._rows: dict[str, list[tuple[int, Any]]] = {}
        self._failures: dict[str, str] = {}

    def _load(self, name: str) -> None:
        if name in self._rows or name in self._failures:
            return
        if name not in self.project.data_files:
            # Not a table this engine has at all. Absence is a fact about
            # the engine rather than damage to the project, so it is not
            # reported; the rules that need such a table ask the profile.
            self._rows[name] = []
            return
        try:
            self._rows[name] = self.project.entries(name)
        except (ProjectError, ValueError, OSError) as exc:
            self._failures[name] = str(exc)

    def ids(self, name: str) -> set[int] | None:
        """The ids in a table, or None if it could not be read.

        None and ``set()`` mean different things to every caller here, which
        is the point of this class: an empty table has no matching entry, an
        unreadable one cannot be asked.
        """
        self._load(name)
        if name in self._failures:
            return None
        return {i for i, _ in self._rows[name]}

    def rows_of(self, name: str) -> list[tuple[int, Any]]:
        """Rows of a table, or none when it could not be read.

        Safe to walk without checking first: a table that failed to load is
        reported by :meth:`findings`, so there is nothing further to say
        about rows it does not have.
        """
        self._load(name)
        return self._rows.get(name, [])

    def findings(self) -> list[Finding]:
        """One finding per table that was asked for and could not be read."""
        return [
            Finding(ERROR, "table_unreadable",
                    "%s could not be read (%s), so nothing that refers to it "
                    "could be checked. Fix or restore that file, then "
                    "validate again." % (name, reason),
                    _db_at(name, 0), "the file itself")
            for name, reason in sorted(self._failures.items())
        ]


def validate(project: Project, scopes: list[str], map_ids: list[int] | None = None,
             limit: int = DEFAULT_FINDING_LIMIT) -> dict[str, Any]:
    """Run the rules in ``scopes`` and report what they found.

    ``scopes`` takes any of :data:`VALIDATE_SCOPES`; a name outside that set is
    ignored so a caller may pass a superset. ``map_ids`` narrows the map-based
    rules to those maps, is de-duplicated, and reports any id that is not on
    disk. ``counts`` covers every finding collected, while ``findings`` is
    ordered most severe first and truncated to ``limit``.
    """
    limit = max(1, _int(limit) or DEFAULT_FINDING_LIMIT)
    # One collector for the whole run, handed to every check. Each check
    # used to return a list of its own and this concatenated seven of them
    # -- eight, counting the second list `check_tilesets` returned -- so
    # `MAX_FINDINGS`, whose comment says it bounds "findings collected, not
    # just findings returned", bounded each list separately and a
    # whole-project run could collect about eight times it. `_report` then
    # said "collection stopped at 5000 findings" when it had not.
    findings = _Findings()
    ran: list[str] = []
    # One loader for the whole run: several checks want the same tables,
    # and a file that will not load should be reported once, not once per
    # check that happened to notice.
    tables = _Tables(project)

    ids, absent = _resolve_map_ids(project, map_ids)
    for map_id in absent:
        findings.add(Finding(
            WARNING, "no_such_map",
            "map %s was asked for but there is no map file with that id, so "
            "nothing about it was checked" % map_id, _map_at(map_id)))

    walked_maps = bool({"maps", "events", "assets"} & set(scopes))
    # One allowance for every walk this call makes, built before the first of
    # them rather than between two: a file that shares objects by reference
    # can render as millions of nodes from a few kilobytes, and neither the
    # Marshal reader nor MAX_FINDINGS bounds that. This sat below the "maps"
    # branch, so the map tree, the map properties, the encounters and the
    # tilesets walked with no allowance at all -- and every one of them is a
    # row-times-child product whose two factors both come out of the file.
    walk_budget = WalkBudget()
    if "maps" in scopes:
        ran.append("map tree, map properties and encounters")
        check_map_tree(project, walk_budget, findings)
        check_maps(project, ids, tables, walk_budget, findings)
        check_tilesets(project, ids, tables, walk_budget, findings)
    if "events" in scopes and findings.room:
        ran.append("event command structure and references (plus every common "
                   "event and troop page, which belong to the project rather "
                   "than to a map)")
        check_commands(project, ids, tables, walk_budget, findings)
        check_event_pages(project, ids, walk_budget, findings)
    if "assets" in scopes and findings.room:
        ran.append("graphic and audio file references")
        findings.extend(check_assets(project, ids, walk_budget))
    if "database" in scopes and findings.room:
        ran.append("database cross-references")
        check_database(project, tables, walk_budget, findings)

    # Last, so it covers every table any check actually asked for.
    findings.extend(tables.findings())

    # Counted before anything is dropped, and sorted before it: truncating by
    # arrival order first meant five thousand warnings from one check could
    # push out the errors from a later one, and `counts` would then report zero
    # errors while errors existed. A count the model cannot trust is worse than
    # no count.
    return _report(project, findings.items, ids if walked_maps else [], ran,
                   walk_budget, limit)


def _resolve_map_ids(project: Project, map_ids: "list[int] | None"
                     ) -> "tuple[list[int], list[int]]":
    """Which maps to check, and which asked-for ids are not on disk.

    Separate from `validate` because it is the only part of that function
    with an answer of its own: two lists, one of them a finding waiting to
    be written. De-duplicated, because a caller repeating an id would
    otherwise multiply every per-map rule by however many times it appeared.
    """
    on_disk = project.map_ids_on_disk()
    if map_ids is None:
        return on_disk, []
    wanted = sorted({i for i in (_int(m) for m in map_ids) if i is not None})
    present = set(on_disk)
    return ([i for i in wanted if i in present],
            [i for i in wanted if i not in present])


def _report(project: Project, findings: "list[Finding]",
            examined: "list[int]", ran: "list[str]", walk_budget: WalkBudget,
            limit: int) -> dict[str, Any]:
    """Sort, count, cut, and say every way in which this is not the whole story.

    Extracted because `validate` was doing three separable things and this is
    the one with the most rules of its own: three separate conditions can each
    make the answer partial, and each has to be said, in its own words,
    without being confused for the others.
    """
    order = {level: rank for rank, level in enumerate(SEVERITIES)}
    findings = sorted(
        findings,
        key=lambda f: (order.get(f.severity, len(SEVERITIES)), f.rule))
    # Counted before anything is dropped: truncating first meant five thousand
    # warnings from one check could push out a later check's errors, and
    # `counts` would then report zero errors while errors existed.
    counts = {level: sum(1 for f in findings if f.severity == level)
              for level in SEVERITIES}

    stopped = len(findings) >= MAX_FINDINGS
    if stopped:
        # Most severe kept, since the sort has already run.
        findings = findings[:MAX_FINDINGS]

    report = {
        "checks_run": ran,
        "maps_examined": len(examined),
        "counts": counts,
        "findings": [f.as_dict() for f in findings[:limit]],
        # `findings_truncated` and `rules_note`, not a bare `truncated` and
        # `note`. The three suffixes each take a subject, and a caller that
        # learned `events_truncated` from map_get and `references_truncated`
        # from db_edit had to learn a third, subject-less form here -- which
        # the suffix test could not see, because it matches on the suffix.
        "findings_truncated": len(findings) > limit,
        "rules_note": "Rules only cover event command parameters whose layout "
                      "is confirmed for both VX Ace and VX; commands outside "
                      "that set are not checked.",
    }
    # Named apart from `stopped` above, which means "MAX_FINDINGS was hit".
    # Two different facts -- the walk ran out, versus the findings did -- and
    # reusing the one name made the second branch below read the first fact.
    walk_stopped = walk_budget.note("validate")
    if walk_stopped:
        # Said in the reply, because a clean bill from a walk that never
        # finished is the worst answer this function can give.
        report["command_walk_stopped"] = walk_stopped
    if not project.command_layouts_modelled:
        report["engine_warning"] = (
            "this is a %s project, whose event command layouts are not modelled "
            "here, so command parameter rules were skipped. Only structural and "
            "database checks ran." % project.engine
        )
    if stopped:
        report["stopped_early"] = (
            "collection stopped at %d findings, so this is not the whole "
            "picture. That many findings means something is wrong with the "
            "project as a whole -- start with the errors listed here."
            % MAX_FINDINGS
        )
    return report


class _Findings:
    """A findings list that stops collecting at MAX_FINDINGS.

    The ceiling belongs here because it has been written against the wrong
    list three times: `_AssetChecker`'s four callers (only one of which had a
    guard, and that one tested a list its loop never touched), `check_tilesets`
    in the same shape, and `_check_encounters` -- which guarded its own local
    list while `check_maps` discarded it per map and had no guard of its own,
    so two maps against a ceiling of four collected eight.

    `room` is for a walk that wants to stop looping rather than merely stop
    appending; `add` is safe either way, so a caller that forgets `room` still
    cannot exceed the ceiling. That is the difference between this and a guard
    at a call site.
    """

    __slots__ = ("items",)

    def __init__(self) -> None:
        self.items: list[Finding] = []

    @property
    def full(self) -> bool:
        return len(self.items) >= MAX_FINDINGS

    @property
    def room(self) -> bool:
        """Whether there is space for another finding. The loop condition."""
        return not self.full

    def add(self, finding: Finding) -> None:
        if not self.full:
            self.items.append(finding)

    def extend(self, findings: "list[Finding]") -> None:
        """Take what fits. For a sub-check that is bounded by its own inputs."""
        for finding in findings:
            if self.full:
                return
            self.items.append(finding)

    @classmethod
    def given(cls, findings: "_Findings | None") -> "_Findings":
        """The caller's collector, or a fresh one when it did not pass one.

        Every `check_*` takes one now, so that one `validate` run has one
        ceiling. They used to return a list each and `validate`
        concatenated seven of them -- eight, counting the second list
        `check_tilesets` returned -- so the ceiling that says it bounds
        "findings collected" bounded each list separately and the run
        collected up to eight times it. The standalone return value is
        kept for the tests and for a caller that wants one check.
        """
        return cls() if findings is None else findings


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
    on_disk = set(project.map_ids_on_disk())

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
                "editor." % (_brief(map_id), MAX_MAP_ID), None,
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
    `check_maps` then discarded. The return value is kept for the tests and
    for any caller that wants just this check.
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
        # The bound that matters is `_AssetChecker`'s own, because this loop
        # appends there: the guard here used to test the map loop's
        # `findings`, which this loop never touches, so it could not fire.
        # Kept to stop the iterating as well as the collecting -- a tileset
        # carries nine sheet names and the row count comes out of the file --
        # but it cannot change what this returns, so it has no entry in
        # `prove_guards`. That is the honest description of a line whose
        # reverted form passes every test.
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
            checker.check(sheet_name, "tilesets",
                          _db_at("tilesets", entry_id, "tileset_names"),
                          context="%s %s (%s) sheet %s"
                                  % (TABLE_NOUNS["tilesets"], entry_id, name,
                                     sheet))
    # Into the one collector rather than concatenated beside it:
    # returning two independently-bounded lists was this file's own example
    # of "two ceilings of MAX_FINDINGS are a ceiling of twice that", from
    # `_Findings`' docstring.
    findings.extend(checker.findings)
    return findings.items


def check_commands(project: Project, map_ids: list[int], tables: _Tables,
                   budget: "WalkBudget | None" = None,
                   collected: "_Findings | None" = None) -> list[Finding]:
    """Every event command list: structure, and the ids the commands name.

    ``budget`` is shared with the asset walk, so one validate call spends one
    allowance across both -- see :class:`WalkBudget`.
    """
    findings = _Findings.given(collected)
    # A table absent from this mapping is one that could not be read, so
    # the rules comparing ids against it are skipped rather than answered
    # wrongly.
    existing = {name: ids for name in REFERENCE_RULES if name != MAP_TARGET
                for ids in [tables.ids(name)] if ids is not None}

    # Every map is *reachable*, because a transfer on map 1 can target any
    # map in the project -- but reachable is not the same as loaded. This was
    # a dict comprehension over `iter_maps(project)` with no ids, so it
    # parsed every Map### file even when the caller had narrowed the run, and
    # parsing the maps is most of what a validate costs: `validate
    # map_ids=[5] scopes=["events"]` measured at 74% of a whole-project run,
    # which is the argument buying nothing. `_MapSizes` parses a map the
    # first time a Transfer Player actually names it, so the check sees the
    # same answers and a narrow run stays narrow.
    map_sizes = _MapSizes(project)
    on_disk = set(project.map_ids_on_disk())
    check_parameters = project.command_layouts_modelled

    for location, commands in iter_command_lists(project, map_ids, budget):
        if findings.full:
            break
        # One per command list, because both are facts about the list rather
        # than about a command: the labels it declares, and the indent the
        # previous command left behind.
        labels: "set[str] | None" = None
        indent_previous = 0
        for index, command in enumerate(commands):
            if findings.full:
                # The ceiling is a collection ceiling, so it is checked here
                # rather than at the end: one page of a million Jump to Label
                # commands with no matching Label is a million findings.
                break
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", CODE_BLANK)
            params = _seq(command.get("parameters"))
            here = location.at(index)

            findings.extend(_check_indent(index, command,
                                          indent_previous, here))
            indent_previous = _int(command.get("indent")) or 0

            if not check_parameters:
                continue
            findings.extend(_check_references(code, params, existing,
                                              on_disk, map_sizes, here))
            if code == CODE_JUMP_TO_LABEL and params:
                if labels is None:
                    # Built on first use: most pages have no labels at all,
                    # and this is a second pass over the whole command list.
                    labels = _labels_in(project, commands)
                findings.extend(_check_jump(project, params, labels,
                                            here))

        findings.extend(check_list_terminator(location, commands))
    return findings.items


def _check_indent(index: int, command: RubyObject, previous: int,
                  here: Location) -> list[Finding]:
    """One rule: the branch structure the indents describe is continuous.

    Extracted with the two below because `check_commands` was three unrelated
    rules at four levels of nesting, so the loop read as one long procedure
    rather than as "for every command, apply these three rules".
    """
    indent = _int(command.get("indent")) or 0
    if indent <= previous + 1:
        return []
    return [Finding(
        WARNING, "indent_jump",
        "command %s jumps from indent %s to %s, which means the branch "
        "structure is inconsistent. Re-check the branches around it in the "
        "editor." % (index, previous, indent), here)]


def _check_references(code: Any, params: list[Any],
                      existing: "dict[str, AbstractSet[int]]",
                      on_disk: "set[int]", map_sizes: "_MapSizes",
                      here: Location) -> list[Finding]:
    """One rule: every id a command names is an id that exists.

    A table absent from ``existing`` could not be read, so its rules are
    skipped rather than answered wrongly -- which is a different outcome from
    "the id is missing" and is why the caller builds that mapping.
    """
    findings = []
    for table, value in _referenced_ids(code, params):
        if table == MAP_TARGET:
            findings += _check_transfer(value, params, on_disk, map_sizes,
                                        here)
        elif table in existing and value not in existing[table]:
            noun = TABLE_NOUNS[table]
            findings.append(_missing(
                "missing_%s" % noun.replace(" ", "_"),
                "%s refers to %s id %s" % (command_name(code), noun, value),
                "Point it at an existing entry, or add that entry to the "
                "database.", here,
                "command parameters against the %s table" % table))
    return findings


def _check_jump(project: Project, params: list[Any], labels: "set[str]",
                here: Location) -> list[Finding]:
    """One rule: a Jump to Label names a Label in the same command list."""
    target = _text(project, params[0])
    if target in labels:
        return []
    return [Finding(
        ERROR, "missing_label",
        'Jump to Label "%s" has no matching Label in this command list, so '
        "the jump does nothing. Add a Label command with that name, or "
        "remove the jump." % target, here)]


def _labels_in(project: Project, commands: list[Any]) -> set[str]:
    return {
        _text(project, command.get("parameters")[0])
        for command in commands
        if isinstance(command, RubyObject) and command.get("code") == CODE_LABEL
        and _seq(command.get("parameters"))
    }


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


def _check_transfer(map_id: int, params: list[Any], on_disk: AbstractSet[int],
                    map_sizes: "_MapSizes",
                    here: Location) -> list[Finding]:
    """Transfer Player: does the target map exist, and are the coordinates on it?

    Existence is decided by the files present, not by which maps happened to
    load: a map that exists but will not parse is a different problem, reported
    by ``check_maps``, and saying "does not exist" about it would send an agent
    recreating a file that is already there.
    """
    if map_id not in on_disk:
        return [_missing(
            "transfer_to_missing_map",
            "Transfer Player targets map %s" % map_id,
            "Point the transfer at an existing map.", here,
            "every Transfer Player against the map files present")]
    width, height = map_sizes.get(map_id)
    if width is None or height is None:
        return []       # unreadable or size-less; check_maps reports that
    if len(params) <= max(TRANSFER_X, TRANSFER_Y):
        return []
    x, y = _int(params[TRANSFER_X]), _int(params[TRANSFER_Y])
    if x is None or y is None:
        return []
    if not on_map(x, y, width, height):
        return [Finding(
            ERROR, "transfer_outside_map",
            "Transfer Player sends the party to (%s, %s) on map %s, which is "
            "only %sx%s, so the party would land off the map. Move the "
            "destination inside it." % (x, y, map_id, width, height), here,
            "Transfer Player coordinates against the target map's size")]
    return []


def check_list_terminator(location: Location, commands: list[Any]) -> list[Finding]:
    """Flag command lists whose final command is not the blank end marker.

    The interpreter reads a code-0 command as "this page is finished"; a list
    that ends on a real command runs off the end of the array instead. An
    entirely empty list is fine -- there is no marker to be missing, and the
    interpreter stops immediately.
    """
    if not commands:
        return []
    last = commands[-1]
    if isinstance(last, RubyObject) and last.get("code") != CODE_BLANK:
        return [Finding(
            WARNING, "missing_list_terminator",
            "this command list does not end with a blank command, which the "
            "interpreter uses to know the page is finished. Append an empty "
            "command (code 0) at indent 0.",
            location.at(len(commands) - 1))]
    return []


def check_event_pages(project: Project, map_ids: list[int],
                      budget: "WalkBudget | None" = None,
                      collected: "_Findings | None" = None) -> list[Finding]:
    """Page-level rules: pages a later page hides, and autoruns that never end.

    Both turn on the same engine fact: RPG Maker runs the *last* page whose
    conditions are satisfied, so a page with no conditions at all hides every
    page before it, and it is the final page being an unconditional Autorun
    that traps the player.
    """
    findings = _Findings.given(collected)
    for map_id, game_map in iter_maps(project, map_ids):
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


def _check_autorun(map_id: int, event_id: int, name: str,
                   pages: list[Any],
                   budget: "WalkBudget | None" = None) -> list[Finding]:
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


# Commands that end an unconditional autorun page's grip on the player: moving
# them to another map, erasing the event for this visit, or ending the game.
CONTROL_RELEASING_CODES = frozenset({CODE_TRANSFER_PLAYER, 214, 353, 354})


# Commands that might release control somewhere this file cannot follow: a
# common event that transfers, or a script calling reserve_transfer or goto. A
# page containing one of these is left alone rather than reported.
OPAQUE_ESCAPE_CODES = frozenset({CODE_CALL_COMMON_EVENT, CODE_SCRIPT,
                                 CODE_SCRIPT_MORE})


def _has_code(commands: list[Any], codes: AbstractSet[int]) -> bool:
    return any(isinstance(command, RubyObject) and _int(command.get("code")) in codes
               for command in commands)


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

    @property
    def full(self) -> bool:
        """Whether this has collected all it is going to.

        The ceiling lives on the collector because four walks share one of
        these and a guard written at a call site is a guard that can be
        written against the wrong list -- which is exactly what
        `check_tilesets` did: it tested its own `findings` while its loop
        appended here, so the break could never fire. A fifth caller gets this
        for free.
        """
        return len(self.findings) >= MAX_FINDINGS

    def check(self, name: Any, kind: str, location: Location,
              rule: str = "missing_graphic", context: str = "") -> None:
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
        lowered = text.lower()
        near = next((c for c in pool if c.lower() == lowered), None)
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


def check_assets(project: Project, map_ids: list[int],
                 budget: "WalkBudget | None" = None) -> list[Finding]:
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
    checker = _AssetChecker(project)
    _check_map_assets(project, checker, map_ids, budget)
    _check_command_assets(project, checker, map_ids, budget)
    _check_database_assets(project, checker, budget)
    return checker.findings


def _check_map_assets(project: Project, checker: _AssetChecker,
                      map_ids: list[int],
                      budget: "WalkBudget | None" = None) -> None:
    """Map, event and page graphic and audio references.

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
                graphic = page.get("graphic")
                if isinstance(graphic, RubyObject):
                    checker.check(graphic.get("character_name"), "characters",
                                  _event_at(map_id, event_id, name, page_index))


def _check_command_assets(project: Project, checker: _AssetChecker,
                          map_ids: list[int],
                          budget: "WalkBudget | None" = None) -> None:
    if not project.command_layouts_modelled:
        # An unmodelled engine's parameter layouts are different: XP's Show
        # Text keeps the message where Ace keeps the face graphic, so these
        # rules would invent a missing file for every line of dialogue.
        return
    for location, commands in iter_command_lists(project, map_ids, budget):
        if len(checker.findings) >= MAX_FINDINGS:
            break
        for index, command in enumerate(commands):
            if len(checker.findings) >= MAX_FINDINGS:
                break
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", CODE_BLANK)
            if code == CODE_SHOW_TEXT:
                params = _seq(command.get("parameters"))
                if params:
                    checker.check(params[0], "faces", location.at(index))
            elif _int(code) in AUDIO_COMMAND_KINDS:
                params = _seq(command.get("parameters"))
                track = params[0] if params else None
                if isinstance(track, RubyObject):
                    checker.check(track.get("name"), AUDIO_COMMAND_KINDS[code],
                                  location.at(index), "missing_audio")


def _check_database_assets(project: Project, checker: _AssetChecker,
                           budget: "WalkBudget | None" = None) -> None:
    # The fourth asset walk, and the one of the four with neither the
    # budget its siblings take nor a `checker.full` test. `_AssetChecker`
    # returns before it counts anything for a name that is empty or
    # present, so the collector ceiling cannot stop this either -- an
    # array of a million links to one row was a million free visits.
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
    def room() -> bool:
        return findings.room

    class_ids = tables.ids("classes")
    if class_ids is not None and room():
        for entry_id, actor in tables.rows_of("actors"):
            if budget is not None and not budget.spend(1):
                break
            if not isinstance(actor, RubyObject) or not actor.has("class_id"):
                continue
            class_id = _int(actor.get("class_id"))
            if class_id is not None and class_id not in class_ids:
                # The noun from `TABLE_NOUNS`, like the two loops below.
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
        if not room():
            break
        target_ids = tables.ids(ref.target)
        if target_ids is None:
            continue
        noun = TABLE_NOUNS[ref.target]
        for entry_id, row in tables.rows_of(ref.owner):
            if not room():
                # Inside the row loop as well as outside it: one owner table
                # with a million rows is one iteration of the loop above.
                break
            if budget is not None and not budget.spend(1):
                break
            if not isinstance(row, RubyObject):
                continue
            for child in _seq(row.get(ref.field)):
                # Per child, because `room()` is tested per owner row and a
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

    findings.extend(_check_system(project, tables.ids("actors"), budget))
    return findings.items


def _check_system(project: Project, actor_ids: "set[int] | None",
                  budget: "WalkBudget | None" = None) -> list[Finding]:
    findings = []
    try:
        system = project.load_data("system")
    except (ProjectError, ValueError, OSError) as exc:
        return [Finding(ERROR, "system_unreadable",
                        "System could not be read (%s), so the starting party "
                        "and start position were not checked." % exc,
                        _db_at("system", 0))]
    if not isinstance(system, RubyObject):
        return findings
    if actor_ids is not None:
        for raw_id in _seq(system.get("party_members")):
            # The only lint loop that appended findings with neither a
            # budget nor a ceiling: @party_members is an Array out of the
            # System file, so five million two-byte Fixnums in a 10 MB file
            # were five million Finding objects, which `_report` then
            # sorted and counted three times.
            if len(findings) >= MAX_FINDINGS:
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
        on_disk = set(project.map_ids_on_disk())
        if start_map not in on_disk:
            findings.append(_missing(
                "missing_start_map",
                "the party starts on map %s" % start_map,
                "Set the start position to a map that exists, or the game will "
                "not boot.", _db_at("system", 0, "start_map_id")))
        else:
            findings += _check_start_position(project, system, start_map)
    return findings


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
