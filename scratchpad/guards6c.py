"""Make the fourth guard decidable, and let one run prove a whole round.

`test_a_stopped_search_keeps_what_it_found` cannot decide whether `done` sees
the deadline: with `done` reverted to `full`, the walk carries on but the
matcher answers False, so the reply is the same. What *is* observable is
`scopes_searched` -- each scope records itself before it tests anything, so a
search that has given up either stops and claims one scope, or walks on and
claims four it never actually applied the pattern in.

That is the honest reading too, which is why it is worth a guard: claiming a
scope was searched when nothing in it was tested is the same defect
`searching()` was introduced to fix.
"""
import pathlib

ROOT = pathlib.Path(r"C:\Users\azure\Desktop\dev\rpgmvx-mcp")
landed = []


def safe_write(path, text):
    text.encode("utf-8")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def patch(rel, edits):
    p = ROOT / rel
    t = p.read_text(encoding="utf-8")
    for label, old, new in edits:
        if old not in t:
            raise SystemExit("NOT FOUND in %s (%s):\n%s" % (rel, label, old[:220]))
        t = t.replace(old, new, 1)
        landed.append("%s: %s" % (rel, label))
    safe_write(p, t)


patch("tests/test_tools.py", [
    ("a test that can actually decide the walk-stop guard",
     '''    def test_a_stopped_search_keeps_what_it_found(self):
        """Stopping is not failing: the hits already collected are real."""
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        search_tools.MAX_SEARCH_SECONDS = 0.0
        out = self.call("search", query="Potion")
        self.assertIn("text_scan_stopped", out)
        self.assertIsInstance(out["hits"], list)
''',
     '''    def test_a_stopped_search_keeps_what_it_found(self):
        """Stopping is not failing: the hits already collected are real."""
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        search_tools.MAX_SEARCH_SECONDS = 0.0
        out = self.call("search", query="Potion")
        self.assertIn("text_scan_stopped", out)
        self.assertIsInstance(out["hits"], list)

    def test_a_search_that_gave_up_does_not_claim_the_later_scopes(self):
        """A scope records itself before it tests anything in it.

        So a search that has spent its deadline must *stop*, not walk on
        answering False: walking on would load and decode the rest of the
        project and then report all four scopes as searched, which is the
        defect `searching()` exists to prevent -- one step further along.
        """
        from rpgvxace_mcp.tools import search_tools
        original = search_tools.MAX_SEARCH_SECONDS
        self.addCleanup(setattr, search_tools, "MAX_SEARCH_SECONDS", original)
        search_tools.MAX_SEARCH_SECONDS = 0.0

        out = self.call("search", query="Potion")
        self.assertEqual(out["scopes_searched"], ["events"],
                         "a search that gave up in the first scope reported "
                         "reading the ones it never tested: %s"
                         % out["scopes_searched"])
'''),
])

patch("scripts/prove_guards.py", [
    ("--only repeats, so one run proves a round",
     '''    parser.add_argument("--only", metavar="TEXT",
                        help="Only guards whose name contains TEXT.")''',
     '''    parser.add_argument("--only", metavar="TEXT", action="append",
                        help="Only guards whose name contains TEXT. Repeat it "
                             "to prove several: a round's guards are proven "
                             "together, which is how they are written.")'''),
    ("the filter matches any of the given texts",
     '''        [SEARCHWORK + "test_a_stopped_search_keeps_what_it_found"],''',
     '''        [SEARCHWORK + "test_a_search_that_gave_up_does_not_claim_the_later_scopes"],'''),
])

# The filter itself. Its shape is unknown here, so report it rather than guess.
p = ROOT / "scripts" / "prove_guards.py"
t = p.read_text(encoding="utf-8")
for line_number, line in enumerate(t.splitlines(), start=1):
    if "args.only" in line:
        print("   filter at prove_guards.py:%d  %s" % (line_number, line.strip()))
print("landed:")
for line in landed:
    print("   " + line)
