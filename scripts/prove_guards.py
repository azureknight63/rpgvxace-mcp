"""Prove that each hardening guard in this codebase still checks something.

A passing test suite is not evidence that a guard works: a guard that has
stopped matching reads exactly like a guard that passes. So for every guard
in `guard_table.py`, this script

  1. copies the source tree to a temporary directory,
  2. reverts that one guard to the code that preceded it,
  3. runs the test that is supposed to catch the regression,
  4. and requires that test to FAIL.

Three things can go wrong, and they read alike from the outside:

* VACUOUS -- the test still passes with the guard reverted, so it is checking
  something else.
* STALE -- the guard's anchor is no longer in the file, so nothing was
  reverted. This happens whenever the code a guard watches is edited, which is
  normal; leaving the guard behind is not.
* AMBIGUOUS -- the anchor matches more than one place, so the revert hits
  whichever comes first, which may not be the one the test walks. A guard like
  this can report PROVEN for the wrong reason.

Proving them all takes minutes, which is long enough to be tempting to skip
after an edit. ``--check`` validates every anchor against its file in well
under a second and catches STALE and AMBIGUOUS immediately, so run that after
touching anything a guard watches.

    python scripts/prove_guards.py            # prove them all
    python scripts/prove_guards.py --check    # anchors only, seconds
    python scripts/prove_guards.py --list     # just list what is proven

The guards themselves are next door in `guard_table.py`, which is data. This
file is the engine, and is the half worth reading when a verdict surprises
you.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from guard_table import GUARDS, Guard, ROOT


TEST_TIMEOUT = 300


def run_tests(tree: Path, tests: list[str]) -> subprocess.CompletedProcess | None:
    """Run one guard's tests in the scratch tree, or None if they hung.

    A hang counts as a failure, not as an error to report. Some of what these
    guards protect is a client being left waiting -- an unanswered batch, a
    message never framed -- and with such a guard reverted the test does not
    fail, it simply never finishes. Treating the timeout as anything but a
    failure would report those guards as unprovable.
    """
    try:
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", *tests],
            # UTF-8 both ways, named: left to the platform, the child wrote
            # in one code page and this side decoded in another, and a
            # failure message neither could hold left stdout as None.
            cwd=tree, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            env={**dict(__import__("os").environ),
                 "PYTHONPATH": str(tree / "src"),
                 "PYTHONIOENCODING": "utf-8"},
            timeout=TEST_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None


def prove(guard: Guard, run=None) -> tuple[bool, str]:
    """Revert one guard in a scratch copy and require its tests to fail.

    Failing with the guard reverted is only half of it: the tests have to pass
    with the guard in place, or their failure says nothing about the guard.
    This checked only the first half, so a test broken for an unrelated reason
    made its guard report PROVEN -- which is precisely the false assurance
    this whole script exists to prevent, in the script itself. Found when
    `TestMapReferenceCountsDoNotUnderstate` broke on a constant that had moved
    and its guard went on reporting PROVEN.

    The baseline runs only when the reverted run failed, so the cost lands on
    the guards that would otherwise be believed. ``run`` is injected by the
    tests, which drive every verdict without copying a tree.
    """
    run = run or run_tests
    with tempfile.TemporaryDirectory(prefix="prove-guard-") as tmp:
        tree = Path(tmp) / "tree"
        shutil.copytree(ROOT, tree, ignore=shutil.ignore_patterns(
            "__pycache__", ".git", ".pytest_cache", "build", "dist", "*.egg-info"))

        target = tree / guard.file
        source = target.read_text(encoding="utf-8")
        if any(present not in source for present, _ in guard.anchors):
            # Distinct from VACUOUS, and a different thing to fix: the guard is
            # still describing code that has since moved or been reworded, so it
            # has silently stopped watching anything at all.
            return False, ("STALE: this guard's source text is no longer in %s, "
                           "so nothing was reverted. Re-anchor it to the code "
                           "as it now reads." % guard.file)
        edited = source
        for present, reverted in guard.anchors:
            edited = edited.replace(present, reverted, 1)
        target.write_text(edited, encoding="utf-8")

        result = run(tree, guard.tests)
        if result is not None and result.returncode == 0:
            # No baseline needed: tests that pass without the guard are
            # checking something else whether or not they also pass with it.
            return False, ("VACUOUS: the tests still pass with the guard "
                           "reverted, so they are checking something else")

        # The tests failed or hung. Put the guard back and make sure that was
        # the guard's absence talking.
        target.write_text(source, encoding="utf-8")
        baseline = run(tree, guard.tests)
        if baseline is None:
            return False, ("BROKEN: these tests hang with the guard in place "
                           "too, so their behaviour without it proves nothing. "
                           "Fix the tests, then prove the guard.")
        if baseline.returncode != 0:
            return False, ("BROKEN: these tests fail with the guard in place "
                           "too, so their failure without it proves nothing "
                           "-- fix the tests first. Baseline said: %s"
                           % (_first_failure(baseline) or "no detail"))

        if result is None:
            return True, ("hung for %ds with the guard reverted, which is the "
                          "failure it prevents" % TEST_TIMEOUT)
        return True, (_first_failure(result) or "failed as expected")


def _first_failure(result) -> str:
    """The most useful line of a failed test run, for the verdict to quote."""
    lines = [line for line in result.stdout.splitlines()
             if line.startswith("E ") or " - " in line]
    return lines[0].strip() if lines else ""


def check_anchors(guards: list[Guard]) -> int:
    """Report any guard whose anchor no longer matches its file exactly once.

    Editing the code a guard watches is normal; leaving the guard behind is
    not. A stale anchor reverts nothing and so proves nothing, and an anchor
    that matches twice reverts whichever copy comes first -- which may not be
    the one the test walks. Both read exactly like a guard that passes, and a
    full run takes twenty minutes to say so, which is long enough that it is
    tempting not to. This says it in seconds, so there is no excuse.
    """
    problems = []
    for guard in guards:
        source = (ROOT / guard.file).read_text(encoding="utf-8")
        for present, _ in guard.anchors:
            found = source.count(present)
            if found == 0:
                problems.append((guard.name,
                                 "STALE: no longer in %s" % guard.file))
            elif found > 1:
                problems.append((guard.name,
                                 "AMBIGUOUS: matches %d places in %s, so the "
                                 "revert may hit the wrong one"
                                 % (found, guard.file)))
    for name, detail in problems:
        print("PROBLEM  %-46s %s" % (name, detail))
    if problems:
        print()
        print("%d of %d anchors need attention. Re-anchor them to the code "
              "as it now reads, then prove them." % (len(problems), len(guards)))
        return 1
    print("All %d anchors match their file exactly once." % len(guards))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true",
                        help="List the guards without proving them.")
    parser.add_argument("--only", metavar="TEXT", action="append",
                        help="Only guards whose name contains TEXT. Repeat it "
                             "to prove several: a round's guards are proven "
                             "together, which is how they are written.")
    parser.add_argument("--check", action="store_true",
                        help="Only check that every anchor still matches its "
                             "file exactly once. Runs no tests, so it is "
                             "seconds rather than minutes.")
    args = parser.parse_args()

    wanted = [text.lower() for text in args.only or ()]
    guards = [g for g in GUARDS
              if not wanted or any(text in g.name.lower() for text in wanted)]
    if wanted and not guards:
        # Silently proving nothing reads exactly like proving everything.
        print("no guard's name contains %s" % " or ".join(args.only))
        return 1
    if args.check:
        return check_anchors(guards)
    if args.list:
        for guard in guards:
            print("%-46s %s" % (guard.name, ", ".join(guard.tests)))
        return 0

    print("Proving %d guards. Each one is reverted in a scratch copy of the "
          "tree,\nand its tests must then fail.\n" % len(guards))
    failures = []
    for index, guard in enumerate(guards, start=1):
        proven, detail = prove(guard)
        mark = "PROVEN " if proven else "PROBLEM"
        print("%s  %2d/%d  %-46s" % (mark, index, len(guards), guard.name))
        print("         without it: %s" % guard.why)
        if not proven:
            print("         %s" % detail)
            failures.append((guard.name, detail))
    print()
    if failures:
        print("%d guard(s) could not be proven:" % len(failures))
        for name, detail in failures:
            print("  %-46s %s" % (name, detail.split(":")[0]))
        return 1
    print("All %d guards proven non-vacuous." % len(guards))
    return 0


if __name__ == "__main__":
    sys.exit(main())
