"""Guards on a caller-supplied regular expression.

Python's `re` cannot be interrupted mid-match: no timeout, signal or thread
kill reaches it, so a pattern that backtracks for a minute is a minute of
this server answering nothing. The gate is a refusal before the match plus
a measurement made in a child process -- and every test here is about that
measurement failing *closed*.
"""

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))


from rpgvxace_mcp.tools.search_tools import (  # noqa: E402
    MAX_SUBJECT_CHARS,
    _matcher,
)
from rpgvxace_mcp.tools import pattern_safety  # noqa: E402
from rpgvxace_mcp.tools.pattern_safety import (  # noqa: E402
    MAX_ALPHABET_DEPTH,
    PatternTooCostly,
    _REPEATS,
    _alphabet,
    _children,
    _sre_parse,
    check_pattern,
    check_shape,
)
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    ToolError,
)

from tool_case import (  # noqa: E402
    ToolTest,
)


class TestSearchRefusesTheHangingPattern(ToolTest):
    def test_a_nested_quantifier_is_refused(self):
        """Python's re backtracks exponentially on these and cannot be
        interrupted, so one would wedge this single-threaded server."""
        for pattern in ("(a+)+$", "(a*)*b", "(x|y+)+z"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ToolError) as caught:
                    self.call("search", query=pattern, regex=True)
                message = str(caught.exception)
                # The shape it found, and the reason a refusal is the answer:
                # the message names one or the other of these depending on
                # whether the cost is in the nesting or in a chain.
                self.assertIn("repeats a group", message)
                self.assertIn("cannot be interrupted", message)

    def test_an_ordinary_pattern_still_works(self):
        result = self.call("search", query="Welcome|Potion", regex=True)
        self.assertTrue(result["hits"])

    def test_a_command_code_search_needs_the_events_scope(self):
        with self.assertRaises(ToolError) as caught:
            self.call("search", command_code=101, scopes=["database"])
        self.assertIn("events", str(caught.exception))


class TestSearchPatternsStayFast(ToolTest):
    r"""The corpus pattern_safety's docstring points at.

    A cost model cannot be checked by reading it, so this measures. Every
    pattern the module allows must finish quickly against the worst subject it
    will ever see, and every pattern that can be made to backtrack must be
    refused. Python's `re` cannot be interrupted, so a pattern that is merely
    slow here is a server the user has to kill.

    **The subject is part of the case.** An earlier version of this test held
    one fixed tuple of subjects, and that is why it passed while five bypasses
    were live: `\d?\s?` repeated is 0.001s against letters and does not
    finish against digits. Each attack below therefore names the text that
    makes it fatal, measured.
    """

    # A long run of one kind of character is what makes competing atoms
    # backtrack, so the subjects are homogeneous by construction.
    LETTERS = "a" * MAX_SUBJECT_CHARS
    DIGITS = "1" * MAX_SUBJECT_CHARS
    SPACES = " " * MAX_SUBJECT_CHARS
    MIXED = ("a1 " * MAX_SUBJECT_CHARS)[:MAX_SUBJECT_CHARS]
    ALTERNATING = "ab" * (MAX_SUBJECT_CHARS // 2)

    SUBJECTS = (LETTERS, DIGITS, SPACES, MIXED, ALTERNATING)

    # (pattern, the subject it is fatal against, how it evades a cost model).
    # Every time here was measured in a killable child process; ">15s" means
    # the child was still running when it was killed.
    ATTACKS = (
        # Classic nested repeats: a quantifier over a body that can itself
        # match more than one way.
        ("(a+)+b", LETTERS, "nested repeat"),
        ("((a+))+b", LETTERS, "nested repeat, one group deeper"),
        ("(?:a+)+b", LETTERS, "nested repeat, non-capturing"),
        ("(a|a)+b", LETTERS, "repeat over an ambiguous branch"),
        ("(a{1,2})+b", LETTERS, "repeat over a bounded repeat"),
        ("(a*)*b", LETTERS, "repeat over a nullable repeat"),
        ("(a|b|ab)*c", ALTERNATING, "repeat over an ambiguous branch"),
        (r"(\w+\s?)*$", LETTERS, "repeat over a nullable body"),
        ("(x+x+)+y", "x" * MAX_SUBJECT_CHARS, "repeat over two quantifiers"),

        # Homogeneous chains: many atoms of one kind, all competing.
        ("a?" * 24 + "b", LETTERS, "optional chain"),
        ("a?" * 40 + "b", LETTERS, "optional chain, longer"),
        ("(a|ab)" * 20 + "z", LETTERS, "alternation chain"),
        ("a{0,500}a{0,500}a{0,500}b", LETTERS, "bounded repeat chain"),
        ("[a-z]+[a-z]+[a-z]+[a-z]+!", LETTERS, "four quantifiers, one class"),

        # Nullable atoms that a disjointness test reads as breaking the chain,
        # while at match time each matches empty and they all stay adjacent.
        # >15s each, measured.
        (r"\w?\W?" * 24 + "\x00", LETTERS, "nullable word/non-word chain"),
        ("a?b?" * 24 + "c", LETTERS, "nullable literal chain"),
        (r"\d?\s?" * 24 + "\x00", DIGITS, "nullable digit/space chain"),

        # Case: the match runs with IGNORECASE, so these compete even though
        # their literals differ. >15s each, measured.
        ("a+A+a+z", LETTERS, "three quantifiers, one letter in two cases"),
        ("a+A+a+A+z", LETTERS, "four quantifiers, one letter in two cases"),
        ("a?A?" * 24 + "z", LETTERS, "nullable chain, one letter in two cases"),

        # Two blocks, each on its own inside a budget, multiplying together.
        (r"\d?" * 14 + r"\s?" * 14 + "z", DIGITS, "two independent blocks"),

        # Two quantifiers over one class, with nothing required between them.
        # 14.3s and >15s, measured -- and both score the same as
        # `Gold\s+\d+`, which is instant, so no score can separate them.
        ("a*a*c", LETTERS, "two stars, one character"),
        ("[a-z]+[a-z]+!", LETTERS, "two quantifiers, one class"),
        (r"\w+\d+!", DIGITS, "two quantifiers, overlapping classes"),

        # A nullable atom does not separate its neighbours: it can match
        # empty, so the atoms either side of it stay adjacent in the subject.
        # Scoring 2 before the fix, and over thirty seconds against a run of
        # "=" -- which is what a stock VX Ace script separator line is. The
        # first shape also names eight cheap characters so that the dangerous
        # one was pushed out of every measured subject. Both measured.
        ("[]?"
         + "=?~?" * 24 + "!", "=" * MAX_SUBJECT_CHARS,
         "nullable filler, dangerous character hidden from the subjects"),
        ("=?~?" * 24 + "!", "=" * MAX_SUBJECT_CHARS,
         "nullable filler between competing atoms"),

        # Lookaround: refused outright, because each runs its own match at
        # every position and there is no way to bound that here.
        (r"Fire(?=Sword)", LETTERS, "lookahead"),
        (r"Gold(?!\d)", LETTERS, "negative lookahead"),
        (r"(?<=Name: )\w+", LETTERS, "lookbehind"),
    )

    # Patterns a caller would really write. Each must be allowed, and fast
    # against every subject above.
    ORDINARY = (
        r"\bHP\b", "Sword|Blade|Axe", r"^\s*#", "[Ff]ire.*damage",
        r"Gold\s+\d+", r"(fire|ice)\s*bolt", "Fire.*Sword.*Shield",
        r"\d{1,3} damage", "Potion", r"actor_\d+", r"[A-Z][a-z]+",
        "self_switch", r"item\s*=\s*\d+", ".*",
        "(?:Elixir|Potion)s?", r"[0-9]+\.[0-9]+", r"^\$game_",
        r"Show Text.*\\v\[\d+\]",
    )

    # Generous enough not to be flaky on a loaded machine, and far below the
    # ten-plus seconds every refused pattern above was measured at.
    BUDGET_SECONDS = 2.0

    def test_every_costly_pattern_is_refused(self):
        for pattern, _subject, why in self.ATTACKS:
            with self.subTest(shape=why, pattern=pattern[:28]):
                with self.assertRaises((PatternTooCostly, ToolError)):
                    check_pattern(pattern)

    def test_the_tool_refuses_them_too(self):
        """Through `search`, so the refusal reaches the caller as a ToolError."""
        for pattern, _subject, why in self.ATTACKS[:6]:
            with self.subTest(shape=why):
                with self.assertRaises(ToolError):
                    self.call("search", query=pattern, regex=True)

    def test_every_allowed_pattern_finishes_quickly(self):
        import re
        import time
        for pattern in self.ORDINARY:
            with self.subTest(pattern=pattern):
                check_pattern(pattern)         # must not refuse a real pattern
                compiled = re.compile(pattern, re.IGNORECASE)
                start = time.perf_counter()
                for subject in self.SUBJECTS:
                    compiled.search(subject[:MAX_SUBJECT_CHARS])
                elapsed = time.perf_counter() - start
                self.assertLess(
                    elapsed, self.BUDGET_SECONDS,
                    "%r took %.2fs against %d subjects"
                    % (pattern, elapsed, len(self.SUBJECTS)))


class TestUnknownCharacterClassesCountAsCompeting(unittest.TestCase):
    """An unrecognised pair of character categories must be assumed to overlap.

    The budget only multiplies backtrack points that can compete for the same
    characters, so "these cannot meet" is the answer that makes a chain score
    as free. The first version of this listed the overlapping pairs and
    defaulted to disjoint, so a pair it had not thought of -- a newline
    against a whitespace class, which do overlap -- read as unable to compete.
    """

    def charset(self, *categories):
        return pattern_safety._Charset(categories=set(categories))

    def test_a_category_and_its_complement_are_disjoint(self):
        for name in ("SPACE", "DIGIT", "WORD", "LINEBREAK"):
            with self.subTest(category=name):
                self.assertFalse(
                    self.charset("CATEGORY_" + name).intersects(
                        self.charset("CATEGORY_NOT_" + name)))

    def test_overlapping_categories_are_reported_as_such(self):
        for left, right in (("CATEGORY_DIGIT", "CATEGORY_WORD"),
                            ("CATEGORY_LINEBREAK", "CATEGORY_SPACE"),
                            ("CATEGORY_WORD", "CATEGORY_NOT_SPACE")):
            with self.subTest(pair=(left, right)):
                self.assertTrue(self.charset(left).intersects(self.charset(right)))

    def test_an_unlisted_pair_counts_as_overlapping(self):
        """The safe direction: refuse more, never score a real chain as free."""
        self.assertTrue(
            self.charset("CATEGORY_SOMETHING_NEW").intersects(
                self.charset("CATEGORY_ANOTHER_ONE")))

    def test_the_order_a_pair_is_written_in_does_not_matter(self):
        """Two entries were written the opposite way round from the lookup."""
        for left, right in (("CATEGORY_SPACE", "CATEGORY_NOT_SPACE"),
                            ("CATEGORY_WORD", "CATEGORY_NOT_WORD")):
            with self.subTest(pair=(left, right)):
                self.assertFalse(self.charset(left).intersects(self.charset(right)))
                self.assertFalse(self.charset(right).intersects(self.charset(left)))


class TestLookaroundIsRefusedByName(ToolTest):
    """A refusal has to name the shape it found.

    A lookaround was charged past the budget, so it came back described as a
    repeated group or a chain of competing parts -- neither of which the
    pattern has. `Gold(?!\\d)` was told to "anchor the pattern with the
    literal text you are looking for", which it already was, so a caller
    cannot find the cause and rephrases forever.
    """

    def test_a_lookaround_names_itself(self):
        for pattern in (r"Fire(?=Sword)", r"Gold(?!\d)", r"(?<=Name: )\w+"):
            with self.subTest(pattern=pattern):
                with self.assertRaises(ToolError) as caught:
                    self.call("search", query=pattern, regex=True)
                message = str(caught.exception)
                self.assertIn("lookahead", message)
                self.assertNotIn("repeats a group", message)


class TestTheShapeGateFailsClosed(unittest.TestCase):
    """A gate that cannot run must refuse, not wave the pattern through.

    `check_shape` swallowed every parser exception and returned, so anything
    the standard library's parser raised that this module had not anticipated
    silently disabled the cheap gate. It is near-unreachable today -- the same
    parser backs `re.compile`, and `_matcher` catches `re.error` -- but the one
    thing standing between a caller and an uninterruptible match should not
    fail open on a surprise.
    """

    def test_a_syntax_error_is_left_to_re_compile(self):
        """A bad pattern gets `re`'s own message, which is better than ours."""
        check_shape("(unclosed")           # must not raise here
        with self.assertRaises(ToolError):
            _matcher({"regex": True}, "(unclosed")

    def test_an_unexpected_parser_failure_is_refused(self):
        import rpgvxace_mcp.tools.pattern_safety as safety

        class Exploding:
            def parse(self, pattern):
                raise MemoryError("parser ran out of room")
            MAXREPEAT = safety._sre_parse.MAXREPEAT

        original = safety._sre_parse
        safety._sre_parse = Exploding()
        self.addCleanup(setattr, safety, "_sre_parse", original)
        with self.assertRaises(PatternTooCostly):
            check_shape("anything")


class TestTheMeasurementFailsClosed(unittest.TestCase):
    """The bound must refuse when it could not run, not pass.

    `measure_cost`'s docstring promises a child that cannot produce an answer
    is treated as a refusal. The branch for a child that died for a
    non-pattern reason assigned `verdict = None` -- a no-op on a variable
    already None -- under a comment ending "anything else is [refused]". So a
    child killed by a signal, by the OOM killer, or a `sys.executable` that is
    not an interpreter was recorded as a measured pass and cached, and the
    pattern then ran in-process where `re` cannot be interrupted.
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety
        self.addCleanup(self.safety._PASSED.clear)

    def run_with_child(self, pattern, **fields):
        """Run `measure_cost` against a child whose exit we choose.

        The cache is emptied first, and that is not tidiness: `_PASSED`
        lives for the process, so a pattern any *earlier* test measured
        successfully is served from it and the fake child never runs --
        `measure_cost` returns, nothing raises, and the assertion fails.
        Found when `test_a_child_that_merely_mentions_an_error_is_refused`
        failed in a whole-suite run and passed alone: another test had
        already measured "Potion".

        Restored rather than just cleared, so this suite cannot make a
        later one fail the same way in reverse.
        """
        import subprocess

        class Fake:
            returncode = fields.get("returncode", 0)
            stdout = fields.get("stdout", "ok")
            stderr = fields.get("stderr", "")

        remembered = dict(self.safety._PASSED)
        self.addCleanup(self.safety._PASSED.update, remembered)
        self.addCleanup(self.safety._PASSED.clear)
        self.safety._PASSED.clear()

        original = subprocess.run
        self.addCleanup(setattr, subprocess, "run", original)
        subprocess.run = lambda *a, **k: Fake()
        self.safety.measure_cost(pattern)

    def test_a_child_killed_by_a_signal_is_refused(self):
        with self.assertRaises(PatternTooCostly) as caught:
            self.run_with_child("killed-by-signal", returncode=-9, stderr="")
        self.assertIn("safety check", str(caught.exception))

    def test_a_child_that_exits_nonzero_silently_is_refused(self):
        with self.assertRaises(PatternTooCostly):
            self.run_with_child("silent-failure", returncode=1, stderr="")

    def test_a_compile_error_is_still_left_to_re_compile(self):
        """`re` reports a syntax error better than this could.

        Signalled by the exit code the child chooses, not by its stderr. This
        test used to pass `returncode=1, stderr="re.error: ..."`, which was
        the old contract -- and that contract is what let MemoryError,
        RecursionError, ImportError and a non-interpreter `sys.executable`
        through as measured passes, because all four put "error" in stderr
        too. See TestTheMeasurementFailsClosedOnEveryAbnormalExit.
        """
        self.run_with_child("(unclosed",
                            returncode=pattern_safety.COMPILE_FAILED_EXIT,
                            stderr="")

    def test_a_child_that_merely_mentions_an_error_is_refused(self):
        """The old contract, now the case it was hiding.

        Exiting 1 with "re.error" in stderr is no longer evidence of anything:
        our child reports a compile failure by its exit code, so a 1 means
        something else went wrong and the pattern was never measured.
        """
        with self.assertRaises(PatternTooCostly):
            self.run_with_child("Potion", returncode=1,
                                stderr="re.error: something surprising")

    def test_a_clean_child_still_passes(self):
        self.run_with_child("Potion", returncode=0)


class TestEveryNamedCharacterIsMeasured(unittest.TestCase):
    """No character a pattern names may be missing from its measured subjects.

    `_subjects` selected the first eight of the alphabet by code point, so a
    pattern could name eight cheap characters and hide the expensive one
    behind them: eight control characters in a class pushed "=" out of every
    subject, the child returned in milliseconds, and the pass was cached for a
    pattern that takes over thirty seconds on a line of "=".
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety

    def measured_text(self, pattern):
        return "".join(self.safety._subjects(pattern))

    def test_a_hidden_character_still_reaches_a_subject(self):
        hide = "".join(chr(c) for c in range(1, 9))
        pattern = "[" + hide + "]?=+=+!"
        text = self.measured_text(pattern)
        self.assertIn("=", text,
                      "the character that makes this pattern expensive was "
                      "not in any measured subject")

    def test_every_character_of_a_wide_alphabet_reaches_one(self):
        letters = "".join(chr(ord("a") + i) for i in range(20))
        text = self.measured_text("[" + letters + "]+!")
        for char in letters:
            self.assertIn(char, text)

    def test_a_run_long_enough_to_backtrack_is_given_to_each(self):
        """One occurrence is not enough: competing atoms need a run."""
        text = self.measured_text("[abc]+!")
        for char in "abc":
            self.assertIn(char * 8, text)


class TestTheMeasurementReachesItsChild(unittest.TestCase):
    """The pattern and its subjects travelled as command-line arguments.

    Windows caps a command line at 32767 characters. Base64 of ten 2000-
    character subjects is about 27000 for ASCII -- within three thousand of
    the limit -- and three times that for Japanese, where each character is
    three UTF-8 bytes. Measured: a 40-character kana class produced an 83101
    character command line.

    The consequence is not a crash. `measure_cost` fails closed, so the
    OSError became "this server could not run its safety check", and every
    Japanese pattern with a moderate character class was refused as unsafe
    when it was nothing of the kind. RPG Maker is Japanese software; a
    Japanese project is the ordinary case, not the edge one.
    """

    # Windows CreateProcess. The real cap on the whole command line.
    ARGV_LIMIT = 32767

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety
        pattern_safety._PASSED.clear()

    def command_for(self, pattern):
        """What would be spawned for this pattern, without spawning it."""
        seen = {}
        real_run = self.safety.subprocess.run

        def spy(command, **kwargs):
            seen["command"] = command
            seen["input"] = kwargs.get("input")
            return real_run(command, **kwargs)

        self.safety.subprocess.run = spy
        self.addCleanup(setattr, self.safety.subprocess, "run", real_run)
        try:
            self.safety.measure_cost(pattern)
        except self.safety.PatternTooCostly:
            pass
        return seen

    def test_a_wide_kana_class_is_measured_not_refused(self):
        kana = "".join(chr(0x3041 + i) for i in range(40))
        pattern = "[" + kana + "]+"
        # One unbounded repeat of one class: nothing competes with it, so this
        # is a cheap pattern and must be allowed.
        self.safety.check_pattern(pattern)

    def test_a_line_of_kanji_is_measured_not_refused(self):
        pattern = "".join(chr(0x4e00 + i) for i in range(170))
        self.safety.check_pattern(pattern)

    def test_the_command_line_stays_far_below_the_limit(self):
        """Whatever the pattern, the payload must not ride on argv."""
        for pattern in ("[" + "".join(chr(0x3041 + i) for i in range(40)) + "]+",
                        "[" + "".join(chr(ord("a") + i) for i in range(26)) + "]+!",
                        "".join(chr(0x4e00 + i) for i in range(170))):
            with self.subTest(pattern=pattern[:20]):
                seen = self.command_for(pattern)
                total = sum(len(str(part)) + 1 for part in seen["command"])
                self.assertLess(
                    total, self.ARGV_LIMIT // 4,
                    "the command line is %d characters, which is within reach "
                    "of the %d Windows allows" % (total, self.ARGV_LIMIT))

    def test_the_child_cannot_import_from_the_working_directory(self):
        """`python -c` puts the process's CWD first on sys.path.

        So a `re.py` or `base64.py` in the directory the server was started
        in would be imported and executed by the first regex search of the
        session -- and that directory is routinely the game project, which is
        the one thing this server's threat model treats as untrusted. `-I`
        drops it, and also ignores PYTHONPATH, sitecustomize and
        usercustomize.
        """
        seen = self.command_for("Gold[0-9]+")
        self.assertIn("-I", seen["command"],
                      "the child is spawned without import isolation, so the "
                      "server's working directory is on its sys.path")

    def test_the_child_runs_somewhere_neutral(self):
        """So the answer does not depend on how the client launched us."""
        import subprocess as sp
        import sys as _sys
        import tempfile
        done = sp.run([_sys.executable, "-I", "-c",
                       "import sys; print(sys.path[0])"],
                      capture_output=True, text=True,
                      cwd=tempfile.gettempdir())
        self.assertNotEqual(done.stdout.strip(), tempfile.gettempdir(),
                            "the working directory is still on sys.path")

    def test_the_subjects_travel_on_stdin(self):
        seen = self.command_for("Gold[0-9]+")
        self.assertIsNotNone(seen["input"],
                             "nothing was written to the child's stdin")


class TestTheChildRefusesAStdinItCannotParse(unittest.TestCase):
    """A pass for a pattern nobody measured is the outcome that must not happen.

    The child read its stdin with `split()`, which splits on whitespace and
    drops empty fields, so an empty pattern line shifted every line up: the
    child compiled the first *subject*, matched the rest of them, printed
    "ok" and exited 0. `_remember` then cached a pass for a pattern that had
    never been measured -- and `_PASSED`'s own comment says a stored refusal
    "would be served as a pass, which is why only `_remember` writes to
    this".

    Unreachable through `search` today: `_matcher` returns None for a falsy
    query, and `_subjects` cannot be empty because of its `or ["a"]`. Tested
    at the boundary anyway, because both of those are two calls away and
    neither says it is holding this up.
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety
        self.addCleanup(pattern_safety._PASSED.pop, "", None)

    def test_an_empty_pattern_is_refused_and_not_remembered(self):
        with self.assertRaises(self.safety.PatternTooCostly) as caught:
            self.safety.measure_cost("")
        self.assertIn("%d" % self.safety.PROTOCOL_BROKEN_EXIT,
                      str(caught.exception))
        self.assertNotIn("", self.safety._PASSED,
                         "an unmeasured pattern was cached as a pass")


class TestWhatTheMeasurementRemembers(unittest.TestCase):
    """A cached verdict is a cached *timing* observation.

    Caching a pass is sound: a pattern that finished in milliseconds against
    subjects built from its own characters will do so again, and the search
    deadline bounds it in use either way. Caching a refusal is not: the only
    refusals this can produce are a timeout and a failure to spawn, both of
    which a loaded machine can cause once and not again. Cached, a single
    transient hiccup refused a legitimate pattern for the life of the server.

    And the cache had no ceiling, so a caller with many distinct patterns grew
    it without bound.
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety
        pattern_safety._PASSED.clear()
        self.addCleanup(pattern_safety._PASSED.clear)

    def spawns_for(self, run):
        """Count children spawned while `run` executes."""
        count = []
        real_run = self.safety.subprocess.run

        def spy(command, **kwargs):
            count.append(1)
            return real_run(command, **kwargs)

        self.safety.subprocess.run = spy
        self.addCleanup(setattr, self.safety.subprocess, "run", real_run)
        run()
        self.safety.subprocess.run = real_run
        return len(count)

    def test_a_pass_is_measured_once(self):
        def twice():
            self.safety.measure_cost("Gold[0-9]+")
            self.safety.measure_cost("Gold[0-9]+")
        self.assertEqual(self.spawns_for(twice), 1)

    def test_a_refusal_is_not_remembered(self):
        """So one transient timeout does not refuse a pattern for good."""
        original = self.safety.PREFLIGHT_SECONDS
        self.addCleanup(setattr, self.safety, "PREFLIGHT_SECONDS", original)
        # No time at all, so every child times out: a refusal for a reason
        # that has nothing to do with the pattern.
        self.safety.PREFLIGHT_SECONDS = 0.001
        with self.assertRaises(self.safety.PatternTooCostly):
            self.safety.measure_cost("Gold[0-9]+")

        self.safety.PREFLIGHT_SECONDS = original

        # The hiccup is over, and the pattern must get another chance -- which
        # means a child actually runs. Asserting only that the call succeeds
        # cannot tell that apart from the failure it is guarding against: a
        # remembered refusal is served by `measure_cost`'s cache hit, which
        # returns as though the pattern had passed.
        def again():
            self.safety.measure_cost("Gold[0-9]+")
        self.assertEqual(self.spawns_for(again), 1,
                         "the refused pattern was served from the cache "
                         "instead of being measured again")

    def test_the_cache_has_a_ceiling(self):
        for index in range(self.safety.MAX_MEASURED_CACHED * 3):
            self.safety._remember("pattern-%d" % index, None)
        self.assertLessEqual(len(self.safety._PASSED),
                             self.safety.MAX_MEASURED_CACHED)


class TestTheShapeGateFailsClosedThroughout(unittest.TestCase):
    """`check_shape` promises a refusal for anything it cannot analyse.

    Its own docstring says failing open "would quietly remove the cheap half
    of the protection the moment the standard library raised something
    unanticipated, and the caller would never know it had". The guard covered
    the parse and stopped there: `_flatten` and `_worst_run` ran outside it,
    and both walk a caller-shaped tree recursively.
    """

    def setUp(self):
        from rpgvxace_mcp.tools import pattern_safety
        self.safety = pattern_safety

    def test_a_failure_flattening_the_tree_is_a_refusal(self):
        def boom(_parsed):
            raise ValueError("something unanticipated")
        self.addCleanup(setattr, self.safety, "_flatten", self.safety._flatten)
        self.safety._flatten = boom
        with self.assertRaises(self.safety.PatternTooCostly) as caught:
            self.safety.check_shape("Gold[0-9]+")
        self.assertIn("could not analyse", str(caught.exception))

    def test_a_recursion_error_is_a_refusal(self):
        def deep(_parsed):
            raise RecursionError("too deep")
        self.addCleanup(setattr, self.safety, "_flatten", self.safety._flatten)
        self.safety._flatten = deep
        with self.assertRaises(self.safety.PatternTooCostly):
            self.safety.check_shape("Gold[0-9]+")

    def test_a_refusal_from_the_analysis_keeps_its_own_message(self):
        """The outright refusals must not be reworded as "could not analyse"."""
        with self.assertRaises(self.safety.PatternTooCostly) as caught:
            self.safety.check_shape("(?=foo)bar")
        self.assertNotIn("could not analyse", str(caught.exception))


class TestTheMeasurementFailsClosedOnEveryAbnormalExit(unittest.TestCase):
    """"error" in stderr is not "the pattern would not compile".

    `measure_cost`'s docstring promises that every way the check can fail to
    produce an answer is a refusal, and names three: a child that cannot be
    started, one that times out, and one killed by a signal or the OOM killer.
    The code decided "was this a compile error?" with

        if done.returncode != 0 and "error" not in (done.stderr or "").lower():

    -- a substring test that matches MemoryError, RecursionError, OSError,
    ImportError, and `error: unknown option -c` from a `sys.executable` that
    is not an interpreter. Every one of those skipped the refusal AND reached
    `_remember(pattern, None)`, so an unmeasured pattern was cached as a
    measured pass and then run in-process for the life of the server. That is
    the entire ReDoS bound, bypassed by a child that happens to print "error".

    Measured before the fix: 4 of 6 abnormal exits allowed and cached.
    """

    def setUp(self):
        self.safety = pattern_safety
        self.real_run = pattern_safety.subprocess.run
        self.addCleanup(setattr, pattern_safety.subprocess, "run", self.real_run)
        pattern_safety._PASSED.clear()
        self.addCleanup(pattern_safety._PASSED.clear)

    def child(self, returncode, stderr="", stdout=None):
        """Make the measurement's child exit however this test needs.

        `stdout` defaults to what the real child prints when it has
        finished matching. A clean exit is not proof the measurement ran --
        the child says "ok" as its last statement for exactly that reason
        -- so a fake standing in for a *successful* child has to say it
        too, or it is standing in for a different child.
        """
        class Done:
            def __init__(self):
                self.returncode = returncode
                self.stderr = stderr
                self.stdout = (self.safety_done if stdout is None
                               else stdout)

        Done.safety_done = self.safety.CHILD_DONE
        self.safety.subprocess.run = lambda *a, **k: Done()

    # Every abnormal exit the docstring names, and the stderr each really
    # produces. None of these is a pattern this server could measure.
    ABNORMAL = (
        ("killed by a signal", -9, ""),
        ("out of memory", 1, "MemoryError: cannot allocate"),
        ("recursion blowout", 1, "RecursionError: maximum recursion depth"),
        ("not an interpreter", 2, "error: unknown option -c"),
        ("import failure", 1, "ImportError: no module named re"),
        ("stderr in another language", 1, "Fehler: unbekannte Option"),
    )

    def test_every_abnormal_exit_is_refused(self):
        for label, returncode, stderr in self.ABNORMAL:
            with self.subTest(label):
                self.safety._PASSED.clear()
                self.child(returncode, stderr)
                with self.assertRaises(self.safety.PatternTooCostly,
                                       msg="%s was allowed" % label):
                    self.safety.measure_cost("some-pattern")

    def test_an_abnormal_exit_is_never_cached_as_a_pass(self):
        """The half that outlives the call: a cached pass skips the gate."""
        for label, returncode, stderr in self.ABNORMAL:
            with self.subTest(label):
                self.safety._PASSED.clear()
                self.child(returncode, stderr)
                try:
                    self.safety.measure_cost("some-pattern")
                except self.safety.PatternTooCostly:
                    pass
                self.assertNotIn(
                    "some-pattern", self.safety._PASSED,
                    "%s was recorded as a measured pass, so the next use of "
                    "this pattern runs unmeasured in-process" % label)

    def test_a_pattern_that_will_not_compile_is_left_to_re(self):
        """The one deliberate carve-out, decided exactly rather than guessed.

        `re.compile` reports a syntax error far better than this can, and
        `_matcher` turns that into a ToolError. So a compile failure must not
        be refused here -- but it must not be cached as a pass either.
        """
        self.child(self.safety.COMPILE_FAILED_EXIT, "re.error: bad range")
        self.safety.measure_cost("[z-a]")
        self.assertNotIn("[z-a]", self.safety._PASSED,
                         "a pattern that never ran was cached as measured")

    def test_the_carve_out_is_a_code_the_child_chooses(self):
        """Not a substring of stderr, which is not evidence of anything."""
        self.assertIsInstance(self.safety.COMPILE_FAILED_EXIT, int)
        self.assertNotIn(self.safety.COMPILE_FAILED_EXIT, (0, 1, 2),
                         "the carve-out code collides with an ordinary "
                         "interpreter exit code")

    def test_a_clean_child_still_passes(self):
        self.child(0, "")
        self.safety.measure_cost("Gold[0-9]+")
        self.assertIn("Gold[0-9]+", self.safety._PASSED)

    def test_a_clean_child_that_says_nothing_is_refused(self):
        """Exit zero is not evidence the measurement happened.

        The child prints its sentinel as the last statement after matching
        every subject, so a zero exit without it means the program did not
        reach the end -- an interpreter shim that swallows `-c`, or any
        future path that returns before the loop. The parent was
        discarding that output, which is the same shape as the `"error"
        not in stderr` test this file records: a proxy for "did it finish"
        standing in for the answer.
        """
        self.child(0, "", stdout="")
        with self.assertRaises(self.safety.PatternTooCostly):
            self.safety.measure_cost("Gold[0-9]+")
        self.assertNotIn("Gold[0-9]+", self.safety._PASSED,
                         "a pattern whose check did not report finishing "
                         "was cached as measured")

    def test_a_real_child_still_measures_a_real_pattern(self):
        """End to end, with no fake: the fix must not break the gate."""
        self.safety.subprocess.run = self.real_run
        self.safety.check_pattern("Gold[0-9]+")
        with self.assertRaises(self.safety.PatternTooCostly):
            self.safety.check_pattern("(a+)+$")


class TestWhereANodeKeepsItsBody(unittest.TestCase):
    """`_children` is this module's only claim about CPython's parser.

    Where each kind of parsed node keeps its sub-patterns -- `av[-1]`,
    `av[2]`, `av[1]`, or the node itself -- was written in four places
    across `_alphabet`, `_flatten`, and the two atom builders. It is one
    function now, and it is the one thing here that a Python upgrade can
    break: `sre_parse` became `re._parser` between 3.10 and 3.11, and this
    module already carries a shim for that import.

    So it is checked against the parser rather than against a fixture. If a
    future version moves a body, these fail here -- in a test that says
    what broke -- rather than in the shape budget, where a body read as
    empty makes a fatal pattern measure as safe.
    """

    def parsed(self, pattern):
        return list(_sre_parse.parse(pattern))

    def kinds(self, pattern):
        return [(str(op), av) for op, av in self.parsed(pattern)]

    def test_a_group_body_is_found(self):
        (name, av), = self.kinds("(ab)")
        self.assertEqual(name, "SUBPATTERN")
        body, = _children(name, av)
        self.assertEqual([str(op) for op, _ in body], ["LITERAL", "LITERAL"])

    def test_a_repeat_body_is_found(self):
        (name, av), = self.kinds("a+")
        self.assertIn(name, _REPEATS)
        body, = _children(name, av)
        self.assertEqual([str(op) for op, _ in body], ["LITERAL"])

    def test_every_branch_is_found(self):
        (name, av), = self.kinds("(?:a|bb|ccc)")
        # A bare alternation parses as a BRANCH; a group of one wraps it.
        if name == "SUBPATTERN":
            inner, = _children(name, av)
            name, av = [(str(op), value) for op, value in inner][0]
        self.assertEqual(name, "BRANCH")
        self.assertEqual([len(b) for b in _children(name, av)], [1, 2, 3])

    def test_a_node_with_no_body_says_so(self):
        """So a caller can walk the result without asking which kind it had."""
        (name, av), = self.kinds("a")
        self.assertEqual(name, "LITERAL")
        self.assertEqual(_children(name, av), [])

    def test_the_kinds_that_hold_a_body_are_the_ones_that_do(self):
        """Both directions: nothing claims a body it has not got.

        Written because the alternative -- a list of kinds in a comment --
        is what this replaced four copies of.
        """
        # `(?:ab|cd)` and not `(?:a|b)`: CPython folds an alternation
        # of single characters into a character class, so the second parses
        # as IN and holds no sub-pattern at all. Found by this test against
        # my own expectation, which is the reason to check `_children`
        # against the parser rather than against a fixture.
        holds = {"(a)": True, "a+": True, "a*": True, "a{2,3}": True,
                 "(?:ab|cd)": True, "a": False, "[ab]": False,
                 ".": False, "^": False, "(?:a|b)": False,
                 "\\w": False}
        for pattern, expected in holds.items():
            with self.subTest(pattern=pattern):
                got = any(_children(str(op), av)
                          for op, av in self.parsed(pattern))
                self.assertEqual(got, expected, self.kinds(pattern))


class TestTheAlphabetFailsClosed(unittest.TestCase):
    r"""A pattern whose characters this module cannot see is refused.

    `_alphabet` walks the parse tree to learn which characters a pattern
    mentions, and `_subjects` builds the subjects `measure_cost` runs the
    child against out of them. A pattern that names none -- because the walk
    gave up -- fell through to `["a"]`, and a run of "a" makes almost any
    pattern finish instantly.

    Measured, on `((((((((([0-9]+[0-9]+!)))))))))`, which is 31 characters
    and well inside `MAX_PATTERN_CHARS`:

        the "a" subject the gate measured   0.0001s
        2,000 digits, which the gate permits  3.9s

    a factor of about 39,000 between what was measured and what then runs
    over every string in the project -- and `_matcher` checks its deadline
    only *between* matches, because `re` holds the GIL and cannot be
    interrupted mid-match. So the measurement passed, `_remember` cached the
    pass for the life of the process, and the single-threaded server had no
    way out but being killed.

    Nine levels because `_alphabet`'s depth cap is 8 and CPython emits a
    SUBPATTERN node for `(?:...)` as well as for `(...)`.
    """

    NESTED = "(" * 9 + "[0-9]+[0-9]+!" + ")" * 9

    def test_the_alphabet_sees_the_pattern_or_nothing_does(self):
        """The one assertion that is the regression test for this."""
        parsed = list(_sre_parse.parse(self.NESTED))
        self.assertNotEqual(
            _alphabet(parsed), set(),
            "the alphabet is empty, so every subject is a run of 'a' and "
            "the measurement is blind")

    def test_a_pattern_too_deep_to_read_is_refused(self):
        """Failing closed is the whole contract of this module.

        Past `MAX_ALPHABET_DEPTH`, which is 50: deeper than anything
        hand-written, and the depth at which this module stops being able
        to say which characters the pattern mentions.
        """
        too_deep = MAX_ALPHABET_DEPTH + 10
        with self.assertRaises(PatternTooCostly):
            check_pattern("(" * too_deep + "a" + ")" * too_deep)

    def test_a_pattern_at_the_cap_is_still_read(self):
        """The cap is a refusal, so it has to sit above what callers write."""
        deep = "(" * MAX_ALPHABET_DEPTH + "Potion" + ")" * MAX_ALPHABET_DEPTH
        self.assertIn("P", _alphabet(list(_sre_parse.parse(deep))))

    def test_the_nested_pattern_does_not_reach_the_matcher(self):
        """End to end: `search` refuses it rather than running it."""
        with self.assertRaises((PatternTooCostly, ToolError)):
            check_pattern(self.NESTED)

    def test_an_ordinary_nested_pattern_still_works(self):
        """Failing closed must not mean refusing what callers really write."""
        for pattern in (r"(Potion|Elixir)", r"((Fire))", r"(a(b(c)))",
                        r"^(Chapter \d+): (.*)$"):
            with self.subTest(pattern=pattern):
                check_pattern(pattern)

    def test_the_alphabet_of_a_deep_pattern_reaches_its_characters(self):
        """Nine levels down, the digits and the bang are still found."""
        found = _alphabet(list(_sre_parse.parse(self.NESTED)))
        self.assertIn("!", found)
        self.assertTrue(found & set("0123456789"),
                        "no digit in %r" % sorted(found))
