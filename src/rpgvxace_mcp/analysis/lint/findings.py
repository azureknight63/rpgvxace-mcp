"""What a finding is, how many there may be, and the tables to read.

The bottom of this package. Every rule module reads this one, none
of them reads another except along the three edges this package's
`__init__` names, and nothing here reads a rule.
"""

from __future__ import annotations

from typing import Any

from ...project import (
    Project,
    ProjectError,
)
from ..walk import (
    Location,
    _db_at,
)


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
# Enforced in every walk that appends inside a loop. Which walks those are
# was a list here and the list was wrong twice, so it is not a list any more
# -- but be precise about what replaced it, because an earlier version of
# this comment was not. `TestEveryLintWalkTakesAnAllowance` scans every
# module of this package, and what it scans for is the *walk budget*, not
# this ceiling. This ceiling is held by `TestNoWalkCollectsPastTheCeiling`
# and `TestEveryCollectorIsShared` below it, the second of which is the scan.
#
# Bound in each module that reads it, which matters when writing a test:
# patching it on the package changes nothing that runs, and
# `ToolTest.lower_the_finding_ceiling` is what finds every binding.
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
    """The commonest finding in this package: a reference with nothing behind it.

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
        # The id sets, held rather than rebuilt per call. See `ids`.
        self._ids: dict[str, set[int]] = {}

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

        Held rather than rebuilt. This is "loaded once and asked many
        times", which is what this class is for, and it rebuilt the set on
        every call: `actors` is asked for by the command rules and by the
        database rules, and `troops` by the map rules and the command rules,
        so one run made a pass over each table's rows twice over.
        """
        self._load(name)
        if name in self._failures:
            return None
        if name not in self._ids:
            self._ids[name] = {i for i, _ in self._rows[name]}
        return self._ids[name]

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
