"""Round 6 Major: `search` had no bound on its total matching work.

TDD: the replacement tests go in first, then the fix.
"""
import pathlib

ROOT = pathlib.Path(r"C:\Users\azure\Desktop\dev\rpgmvx-mcp")


def safe_write(path, text):
    text.encode("utf-8")            # fail before truncating, not after
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def patch(rel, edits):
    p = ROOT / rel
    t = p.read_text(encoding="utf-8")
    for label, old, new in edits:
        if old not in t:
            raise SystemExit("NOT FOUND in %s (%s):\n%s" % (rel, label, old[:200]))
        t = t.replace(old, new, 1)
        print("   landed: %s" % label)
    safe_write(p, t)


# ---------------------------------------------------------------- the tests
TESTS_OLD = '''class TestSearchBoundsItsTotalWork(ToolTest):
    """A pattern measured against 2 KB then runs over the whole project.

    `measure_cost` allows up to two seconds against ten subjects of 2000
    characters -- about 20 KB. `search` then applies the pattern once per
    candidate string, over every script line and every string field in the
    project, which is megabytes. A pattern chosen to sit just under the clock
    passes and then costs that much again per candidate, and `limit` does not
    help because a pattern picked to be slow is one that matches nothing and
    so never fills the collector.

    So the characters fed to the matcher are counted, and the search stops and
    says so -- the same shape as `CommandBudget`.
    """

    def test_the_matcher_stops_at_its_ceiling(self):
        from rpgvxace_mcp.tools import search_tools
        matcher = search_tools._Matcher(lambda text: False, whole=True)
        chunk = "x" * 1000
        spent = 0
        while not matcher.exhausted:
            matcher(chunk)
            spent += len(chunk)
            self.assertLess(spent, search_tools.MAX_SEARCH_CHARS * 2,
                            "the matcher never stopped")
        self.assertGreaterEqual(spent, search_tools.MAX_SEARCH_CHARS)

    def test_a_search_that_stopped_early_says_so(self):
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_CHARS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_CHARS", original)
        # Small enough that the demo project's own text exhausts it.
        search_tools.MAX_SEARCH_CHARS = 200

        out = self.call("search", query="zzz-nothing-matches-this")
        self.assertIn("text_scan_stopped", out)
        self.assertTrue(out["may_have_more"],
                        "a scan that stopped must not read as 'no hits'")

    def test_an_ordinary_search_does_not_stop(self):
        out = self.call("search", query="Potion")
        self.assertNotIn("text_scan_stopped", out)
'''

TESTS_NEW = '''class TestSearchBoundsItsTotalWork(ToolTest):
    """A pattern measured against 2 KB then runs over the whole project.

    `measure_cost` bounds *one* match: up to two seconds across ten subjects
    of 2000 characters, so about 0.2s against any one candidate. Nothing
    bounded how many candidates there are. `search` applies the pattern once
    per script line and once per string field in the project -- of the order of
    a hundred thousand candidates -- so a pattern chosen to sit just under that
    clock passed the gate and then cost that much again per candidate. `limit`
    does not help: a pattern picked to be slow is one that matches nothing, so
    the collector never fills and every scope runs to the end.

    A character budget cannot fix this. To be a real worst-case bound it would
    have to be about ten thousand characters, which is not a usable search; to
    be usable it would have to be megabytes, which bounds nothing. Time
    composes where characters do not -- one match is bounded by the
    measurement, so the sum can be bounded by a deadline checked between
    matches, which is the one place it *can* be checked, since `re` cannot be
    interrupted mid-match.

    The clock is injected so these tests do not race a real one.
    """

    def matcher_with_clock(self, ticks, seconds=1.0):
        """A matcher whose clock returns each of `ticks` in turn."""
        from rpgvxace_mcp.tools import search_tools
        readings = iter(ticks)
        return search_tools._Matcher(lambda text: False, whole=True,
                                     clock=lambda: next(readings),
                                     seconds=seconds)

    def test_the_matcher_stops_when_its_time_is_spent(self):
        # First reading sets the deadline at 0.0 + 1.0; the third is past it.
        matcher = self.matcher_with_clock([0.0, 0.5, 1.5, 2.0])
        self.assertFalse(matcher("x"))
        self.assertFalse(matcher.exhausted, "stopped while it still had time")
        matcher("x")
        self.assertTrue(matcher.exhausted, "ran past its deadline")

    def test_an_exhausted_matcher_does_not_test_again(self):
        """Once stopped it must stay stopped, and must not read the clock.

        The iterator runs out after the reading that exhausts it, so a later
        call reaching the clock raises StopIteration rather than passing
        quietly.
        """
        matcher = self.matcher_with_clock([0.0, 1.5])
        matcher("x")
        self.assertTrue(matcher.exhausted)
        self.assertFalse(matcher("x"))
        self.assertFalse(matcher("x"))

    def test_a_search_that_stopped_early_says_so(self):
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        # No time at all, so the first candidate string exhausts it.
        search_tools.MAX_SEARCH_SECONDS = 0.0

        out = self.call("search", query="zzz-nothing-matches-this")
        self.assertIn("text_scan_stopped", out)
        self.assertTrue(out["may_have_more"],
                        "a scan that stopped must not read as 'no hits'")
        self.assertEqual(out["count"], 0)

    def test_a_stopped_search_keeps_what_it_found(self):
        """Stopping is not failing: the hits already collected are real."""
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        search_tools.MAX_SEARCH_SECONDS = 0.0
        out = self.call("search", query="Potion")
        self.assertIn("text_scan_stopped", out)
        self.assertIsInstance(out["hits"], list)

    def test_an_ordinary_search_does_not_stop(self):
        out = self.call("search", query="Potion")
        self.assertNotIn("text_scan_stopped", out)
        self.assertTrue(out["hits"], "the fixture project has a Potion")
'''

p = ROOT / "tests" / "test_tools.py"
t = p.read_text(encoding="utf-8")
if TESTS_OLD not in t:
    raise SystemExit("test class not found as written")
safe_write(p, t.replace(TESTS_OLD, TESTS_NEW, 1))
print("   tests replaced with the clock-injected version")

# ------------------------------------------------------------------ the fix
DESC_OLD = (
    '"of them could not be read at all, and -- as \\"may_have_more\\" -- whether "\n'
    '    "the scan stopped on reaching the limit rather than running out of "\n'
    '    "project. That last one is not the same as validate\'s \\"truncated\\": this "\n'
    '    "scan stops at the limit and so never drops a hit it found, while "\n'
    '    "validate collects first and then cuts.",'
)
DESC_NEW = (
    '"of them could not be read at all, and -- as \\"may_have_more\\" -- whether "\n'
    '    "the scan stopped on reaching the limit rather than running out of "\n'
    '    "project. That last one is not the same as validate\'s \\"truncated\\": this "\n'
    '    "scan stops at the limit and so never drops a hit it found, while "\n'
    '    "validate collects first and then cuts. A search also gives up after "\n'
    '    "%g seconds of matching, and after %s commands walked, saying so as "\n'
    '    "\\"text_scan_stopped\\" or \\"command_walk_stopped\\". Either one means the "\n'
    '    "hits are real but the absences are not."\n'
    '    % (MAX_SEARCH_SECONDS, "{:,}".format(MAX_COMMANDS_WALKED)),'
)

patch("src/rpgvxace_mcp/tools/search_tools.py", [
    ("time imported for the deadline",
     "import re\nfrom typing import Any",
     "import re\nimport time\nfrom typing import Any"),

    ("MAX_COMMANDS_WALKED imported so the description can name it",
     "from ..analysis import CommandBudget, Location, iter_command_lists",
     "from ..analysis import (\n"
     "    MAX_COMMANDS_WALKED,\n"
     "    CommandBudget,\n"
     "    Location,\n"
     "    iter_command_lists,\n"
     ")"),

    ("MAX_SEARCH_SECONDS declared beside the other ceilings",
     "# How much of one candidate string a pattern is matched against. A minified\n"
     "# script line can be tens of thousands of characters, and match cost grows with\n"
     "# the subject; a hit past this point is not one a person was looking for.\n"
     "MAX_SUBJECT_CHARS = 2000",

     "# How much of one candidate string a pattern is matched against. A minified\n"
     "# script line can be tens of thousands of characters, and match cost grows with\n"
     "# the subject; a hit past this point is not one a person was looking for.\n"
     "MAX_SUBJECT_CHARS = 2000\n"
     "\n"
     "# How long one search may spend applying its pattern.\n"
     "#\n"
     "# `pattern_safety.measure_cost` bounds one match -- about 0.2s against a\n"
     "# 2000-character subject. It says nothing about how many matches there are,\n"
     "# and this walks every script line and every string field in the project. A\n"
     "# pattern sitting just under the measurement's clock therefore passed the gate\n"
     "# and then cost that much again per candidate, with `limit` no help: a pattern\n"
     "# picked to be slow matches nothing, so the collector never fills.\n"
     "#\n"
     "# A character budget cannot bound that product -- usable means megabytes, and\n"
     "# megabytes bounds nothing. Time does compose: one match is bounded above, so\n"
     "# the sum is bounded by a deadline. It is checked *between* matches, which is\n"
     "# the only place it can be checked, because `re` holds the GIL for the length\n"
     "# of a match and no timeout or signal reaches it. So a search overruns this by\n"
     "# at most one match, and the reply says when it stopped.\n"
     "MAX_SEARCH_SECONDS = 10.0"),

    ("the description declares the deadline", DESC_OLD, DESC_NEW),
])
print("done")
