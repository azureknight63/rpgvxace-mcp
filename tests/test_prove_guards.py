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
            "a test fixture", prove_guards.LINT,
            "MAX_FINDINGS = 5000", "MAX_FINDINGS = 1",
            ["tests/test_guards_bounds.py::TestNoWalkCollectsPastTheCeiling"],
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
        stale = Guard("a stale fixture", prove_guards.LINT,
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
            key = (guard.file, tuple(guard.present))
            if key in seen:
                duplicates.append("%r and %r" % (seen[key], guard.name))
            seen[key] = guard.name
        self.assertEqual(duplicates, [],
                         "these guards watch the same line, so merge them "
                         "and keep both test lists")

    def test_every_named_test_exists(self):
        """A guard whose test cannot be collected is a guard proving nothing.

        The full run reports these as BROKEN, which is correct but costs forty
        minutes. `--check` cannot see them at all: it counts anchor
        occurrences in the *source* and never looks at the test side. Four
        guards were in this state at once, three of them naming methods that
        had been renamed and one naming a class it never belonged to -- the
        result of two module-level aliases sharing a name, so the second
        assignment silently redirected the first's guards.
        """
        import ast

        root = Path(__file__).resolve().parent.parent
        methods: dict[str, set[str]] = {}
        for path in sorted((root / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    key = "%s::%s" % (path.name, node.name)
                    methods[key] = {
                        child.name for child in node.body
                        if isinstance(child, (ast.FunctionDef,
                                              ast.AsyncFunctionDef))}

        def inherited(file_name: str, class_name: str) -> set[str]:
            """Methods on a class, including those it inherits in-file.

            A suite that shares a fixture through a mixin -- SharedObjectMap,
            EmptyPageMap -- keeps its test methods on the subclass, but a
            guard may legitimately name one defined on a base.
            """
            found = set(methods.get("%s::%s" % (file_name, class_name), ()))
            for key, names in methods.items():
                if key.endswith("::%s" % class_name):
                    found |= names
            return found

        missing = []
        for guard in prove_guards.GUARDS:
            for test in guard.tests:
                parts = test.split("::")
                file_name = parts[0].rsplit("/", 1)[-1]
                if not (root / "tests" / file_name).exists():
                    missing.append("%s -> no such file: %s"
                                   % (guard.name, test))
                    continue
                if len(parts) < 2:
                    continue
                class_name = parts[1]
                if not any(key.endswith("::%s" % class_name)
                           for key in methods):
                    missing.append("%s -> no such class: %s"
                                   % (guard.name, test))
                    continue
                if len(parts) < 3:
                    continue
                if parts[2] not in inherited(file_name, class_name):
                    missing.append("%s -> no such test: %s"
                                   % (guard.name, test))
        self.assertEqual(
            missing, [],
            "these guards name tests that cannot be collected, so a full run "
            "reports them BROKEN and --check reports nothing:\n  "
            + "\n  ".join(missing))

    def test_reverting_a_guard_actually_changes_the_file(self):
        """A guard whose reverted text equals its present text reverts nothing."""
        for guard in prove_guards.GUARDS:
            for present, reverted in guard.anchors:
                with self.subTest(guard=guard.name):
                    self.assertNotEqual(present, reverted)

    def test_no_two_aliases_name_one_thing(self):
        """Two names for one test class or one file read as two subjects.

        `T` and `SYMDEPTH` held the same test class, `FAST` and `MEASURED`
        another, and `TYPES` and `RUBYTYPES` the same source file: renaming
        any of them needs two edits, and nothing said the guards using each
        name were on one thing. Same shape as the alias collision that made
        a guard watch the wrong tests.

        Both kinds, because the first version of this test looked only at
        test-class aliases and the file-path pair went on standing.
        """
        by_value: dict[str, list[str]] = {}
        for name, value in vars(prove_guards).items():
            if (name.isupper() and isinstance(value, str)
                    and (value.startswith("tests/")
                         or value.startswith("src/") or value.startswith("scripts/"))):
                by_value.setdefault(value, []).append(name)
        shared = ["%s: %s" % (value, ", ".join(sorted(names)))
                  for value, names in sorted(by_value.items())
                  if len(names) > 1]
        self.assertEqual(shared, [],
                         "these aliases name one thing each, so "
                         "collapse them:\n  " + "\n  ".join(shared))


class MultipleAnchorTest(unittest.TestCase):
    """A property held in two places needs both of them reverted.

    `event_get`'s negative page is refused twice over -- by the `minimum` its
    schema declares, which `check_args` enforces, and by the `opt_int` call
    that reads it. Reverting either left the other, so the guard reported
    VACUOUS while the property was in fact doubly held. That is a real answer
    about the code and a wrong answer about the guard, which is the failure
    mode this whole script exists to remove.
    """

    def guard(self, present, reverted):
        return prove_guards.Guard(
            "two anchors", "src/rpgvxace_mcp/analysis/lint.py", present, reverted,
            ["tests/test_tools.py::Fake::test_one"], "why")

    def test_a_single_anchor_still_reads_as_one(self):
        guard = self.guard("a", "b")
        self.assertEqual(guard.anchors, [("a", "b")])

    def test_both_anchors_are_reverted_together(self):
        guard = self.guard(["a", "c"], ["b", "d"])
        self.assertEqual(guard.anchors, [("a", "b"), ("c", "d")])

    def test_a_mismatched_pair_is_refused_at_import_time(self):
        """Better than reverting the first and silently skipping the second."""
        with self.assertRaises(ValueError):
            self.guard(["a", "c"], ["b"])



class SuiteFilesRunAloneTest(unittest.TestCase):
    """Every test file passes when it is the only one collected.

    `prove_guards` names a test as `file::Class::test` and runs pytest on
    exactly that, so a suite that only passes as part of the whole run makes
    every guard in it report BROKEN -- a hundred guards saying "fix the
    tests first" and none of them saying which test or why.

    That is not hypothetical. Splitting `test_tools.py` moved tool
    registration into `tool_case`, the repair put the import block inside
    that module's docstring (the anchor matched the word "class" in prose),
    and the whole run passed because some *other* file had imported the tool
    modules first. 90 of 206 guards came back BROKEN, from two baseline
    failures that the full run could not show.

    One subprocess per file, so this is the slowest test in the project by
    some distance. It is worth it: the alternative is a forty-minute proving
    run that reports nothing usable.
    """

    def suite_files(self):
        tests = Path(__file__).resolve().parent
        return sorted(path for path in tests.glob("test_*.py")
                      if path.name != Path(__file__).name)

    def test_every_suite_file_passes_alone(self):
        import os
        import subprocess

        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(root / "src"), str(root / "tests" / "fixtures")])
        failed = []
        for path in self.suite_files():
            done = subprocess.run(
                [sys.executable, "-m", "pytest", str(path), "-q",
                 "--no-header", "-p", "no:cacheprovider"],
                cwd=str(root), capture_output=True, text=True, env=env,
                timeout=600)
            if done.returncode != 0:
                tail = [line for line in done.stdout.split("\n")
                        if "failed" in line or "error" in line]
                failed.append("%s: %s" % (path.name,
                                          tail[-1] if tail else "no detail"))
        self.assertEqual(failed, [],
                         "these files do not pass on their own, so every "
                         "guard naming them would report BROKEN: "
                         + "; ".join(failed))


class ProcessStateIsRestoredTest(unittest.TestCase):
    """A test that touches process-global state must put it back.

    `SuiteFilesRunAloneTest` catches a file that only passes as part of the
    whole run. This is the mirror: a file that only passes when run
    *first*, because it left process state behind -- or because it depended
    on state some other test had left.

    Found in `pattern_safety`: `measure_cost` remembers passes in `_PASSED`
    for the life of the process, so the test that fakes a failing child for
    the pattern "Potion" was served from the cache whenever an earlier test
    had measured that pattern, and the fake child never ran. It failed in
    the whole-suite run and passed alone, which reads exactly like a timing
    flake and is not one.

    Runs the suite twice in one process, in opposite order. Anything that
    depends on or leaks module state fails the second pass.
    """

    def test_the_suite_passes_in_reverse_order(self):
        import os
        import subprocess

        root = Path(__file__).resolve().parent.parent
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(root / "src"), str(root / "tests" / "fixtures")])
        # `-p no:randomly` is not needed -- this asks for one specific
        # order, the reverse of collection, which is the cheapest way to
        # find a test that only passes after another one.
        done = subprocess.run(
            [sys.executable, "-m", "pytest", str(root / "tests"), "-q",
             "--no-header", "-p", "no:cacheprovider", "--reverse"]
            if _has_reverse() else
            [sys.executable, "-m", "pytest",
             str(root / "tests" / "test_guards_patterns.py"),
             str(root / "tests" / "test_guards_replies.py"),
             str(root / "tests" / "test_tools.py"), "-q", "--no-header",
             "-p", "no:cacheprovider"],
            cwd=str(root), capture_output=True, text=True, env=env,
            timeout=900)
        tail = [line for line in done.stdout.splitlines()
                if "passed" in line or "failed" in line]
        self.assertEqual(done.returncode, 0,
                         "the suite does not pass in this order, so some "
                         "test depends on another having run: %s"
                         % (tail[-1] if tail else done.stdout[-400:]))


def _has_reverse() -> bool:
    """Whether pytest-reverse is installed. It is not a dependency."""
    try:
        import pytest_reverse   # noqa: F401
    except ImportError:
        return False
    return True


# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so `python tests/test_prove_guards.py`
# called `unittest.main()` (which exits) before the classes below
# it existed, and reported success having skipped them. pytest
# collects either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()
