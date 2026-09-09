"""Tests for the guard prover itself.

`prove_guards.py` exists to catch a guard that reports success for the wrong
reason, and it had that bug: it reverted a guard, ran the guard's tests, and
called the guard PROVEN when they failed -- without ever checking that they
passed to begin with. A test broken for an unrelated reason therefore made its
guard look proven, which is the one thing this script must never do. Found
when `TestMapReferenceCountsDoNotUnderstate` broke on a moved constant and its
guard still reported PROVEN.

The test runner is injected, so these drive every verdict in milliseconds
rather than copying the tree and running pytest four times.
"""

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import prove_guards  # noqa: E402
from prove_guards import Guard, prove  # noqa: E402


class Ran:
    """What an injected runner hands back, in the shape prove() reads."""

    def __init__(self, returncode: int, stdout: str = ""):
        self.returncode = returncode
        self.stdout = stdout


class Runner:
    """A fake test runner: one result per call, in order."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def __call__(self, tree, tests):
        self.calls += 1
        if not self.results:
            raise AssertionError("prove() ran the tests more times than "
                                 "this runner was given results for")
        return self.results.pop(0)


class ProveTest(unittest.TestCase):
    """Each verdict, driven by what the runner reports."""

    def setUp(self):
        # A guard whose anchor really is in a file of the project, so the
        # revert step behaves as it does in a real run.
        self.guard = Guard(
            "a test fixture", prove_guards.ANALYSIS,
            "MAX_FINDINGS = 5000", "MAX_FINDINGS = 1",
            ["tests/test_tools.py::TestNoWalkCollectsPastTheCeiling"],
            "nothing -- this is a fixture",
        )

    def test_a_guard_whose_tests_fail_only_when_reverted_is_proven(self):
        # Reverted: fail. Baseline: pass.
        runner = Runner(Ran(1, "E   AssertionError: boom"), Ran(0))
        proven, detail = prove(self.guard, run=runner)
        self.assertTrue(proven, detail)
        self.assertEqual(runner.calls, 2,
                         "the baseline was not checked, so a broken test "
                         "would read as a proven guard")

    def test_a_guard_whose_tests_pass_when_reverted_is_vacuous(self):
        runner = Runner(Ran(0))
        proven, detail = prove(self.guard, run=runner)
        self.assertFalse(proven)
        self.assertIn("VACUOUS", detail)

    def test_a_guard_whose_tests_were_already_failing_is_not_proven(self):
        """The bug this module was written for.

        Reverted: fail. Baseline: also fail. The guard is watching a test that
        does not work, and reporting it as proven is exactly the false
        assurance the whole script exists to prevent.
        """
        runner = Runner(Ran(1, "E   AttributeError: no attribute"),
                        Ran(1, "E   AttributeError: no attribute"))
        proven, detail = prove(self.guard, run=runner)
        self.assertFalse(proven,
                         "a guard whose tests fail with or without it was "
                         "reported as proven")
        self.assertIn("BROKEN", detail)

    def test_a_broken_verdict_names_the_baseline_failure(self):
        runner = Runner(Ran(1, "E   AttributeError: gone"),
                        Ran(1, "E   AttributeError: gone"))
        _, detail = prove(self.guard, run=runner)
        self.assertIn("AttributeError", detail,
                      "the verdict does not say what is broken: %s" % detail)

    def test_a_stale_anchor_is_named_as_stale(self):
        stale = Guard("a stale fixture", prove_guards.ANALYSIS,
                      "this text is not in that file anywhere", "irrelevant",
                      ["tests/test_tools.py::Nothing"], "nothing")
        runner = Runner()
        proven, detail = prove(stale, run=runner)
        self.assertFalse(proven)
        self.assertIn("STALE", detail)
        self.assertEqual(runner.calls, 0, "a stale guard ran tests anyway")

    def test_a_hang_with_the_guard_reverted_counts_as_proven(self):
        """A guard against an unbounded walk is proven by the walk hanging."""
        runner = Runner(None, Ran(0))
        proven, detail = prove(self.guard, run=runner)
        self.assertTrue(proven)
        self.assertIn("hung", detail)

    def test_a_hang_is_not_proven_if_the_baseline_hangs_too(self):
        """Otherwise a test that hangs on its own reads as a proven guard."""
        runner = Runner(None, None)
        proven, detail = prove(self.guard, run=runner)
        self.assertFalse(proven, "a test that hangs either way was reported "
                                 "as a proven guard")
        self.assertIn("BROKEN", detail)


class GuardTableTest(unittest.TestCase):
    """Properties of the registered guards that do not need a test run."""

    def test_every_guard_has_at_least_one_test(self):
        for guard in prove_guards.GUARDS:
            with self.subTest(guard=guard.name):
                self.assertTrue(guard.tests)

    def test_every_guard_names_what_goes_wrong_without_it(self):
        for guard in prove_guards.GUARDS:
            with self.subTest(guard=guard.name):
                self.assertTrue(guard.why.strip(),
                                "a guard with no stated consequence cannot "
                                "be reviewed for whether it is worth having")

    def test_no_two_guards_share_a_name(self):
        names = [guard.name for guard in prove_guards.GUARDS]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        self.assertEqual(duplicates, [],
                         "--only matches by name, so duplicates cannot be "
                         "proven separately")

    def test_no_two_guards_watch_the_same_line(self):
        """`check_anchors` cannot see this one.

        It counts how often an anchor appears in the file, so two guards
        pointing at the same single occurrence both look fine -- and two
        guards on one line means one revert proved twice, with two test lists
        that can drift apart. Found exactly that: the same anchor registered
        in two different rounds.
        """
        seen: dict[tuple[str, str], str] = {}
        duplicates = []
        for guard in prove_guards.GUARDS:
            key = (guard.file, guard.present)
            if key in seen:
                duplicates.append("%r and %r" % (seen[key], guard.name))
            seen[key] = guard.name
        self.assertEqual(duplicates, [],
                         "these guards watch the same line, so merge them "
                         "and keep both test lists")

    def test_reverting_a_guard_actually_changes_the_file(self):
        """A guard whose reverted text equals its present text reverts nothing."""
        for guard in prove_guards.GUARDS:
            with self.subTest(guard=guard.name):
                self.assertNotEqual(guard.present, guard.reverted)


if __name__ == "__main__":
    unittest.main()
