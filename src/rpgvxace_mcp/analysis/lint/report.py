"""`validate`: the scopes it offers, the rules it runs, the report it builds.

The top of this package, and the only module in it that knows every
rule. A finding says what is wrong, where, and what the rule
inspected -- all three, because "map 3 has a problem" is not
something a caller can act on.
"""

from __future__ import annotations

from typing import Any

from ...project import Project
from ..walk import (
    WalkBudget,
    _int,
    _map_at,
)
from .findings import (
    DEFAULT_FINDING_LIMIT,
    Finding,
    MAX_FINDINGS,
    SEVERITIES,
    WARNING,
    _Findings,
    _Tables,
)
from .maps import (
    check_map_tree,
    check_maps,
)
from .pages import check_event_pages
from .command_rules import check_commands
from .assets import (
    _AssetChecker,
    check_assets,
)
from .database import (
    check_database,
    check_tilesets,
)


# The checks `validate` knows how to run. It implements them, so it owns the
# list; the tool layer advertises this rather than keeping its own copy.
VALIDATE_SCOPES = ("maps", "events", "assets", "database")


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

    # One checker across the two scopes that both want the command lists.
    # Built only when both are selected, so the assets branch below is the
    # only place that reads it into `findings` -- which is what it has
    # always been.
    shared_checker = (_AssetChecker(project)
                      if {"events", "assets"} <= set(scopes) else None)
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
        check_commands(project, ids, tables, walk_budget, findings,
                       shared_checker)
        check_event_pages(project, ids, walk_budget, findings)
    if "assets" in scopes and findings.room:
        ran.append("graphic and audio file references")
        findings.extend(check_assets(
            project, ids, walk_budget, shared_checker,
            commands_done=shared_checker is not None))
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
