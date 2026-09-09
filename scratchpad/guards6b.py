"""Re-anchor the subject-coverage guard, and register the search deadline.

The subject guard was anchored to `test_every_costly_pattern_is_refused`, which
cannot decide it: with the nullable fix in place that attack is already refused
by `check_shape`, so removing the every-character subject changes nothing and
the guard reported PROBLEM. The tests that *do* decide it read `_subjects`
directly and assert the expensive character is in what gets measured.
"""
import pathlib

P = pathlib.Path(r"C:\Users\azure\Desktop\dev\rpgmvx-mcp\scripts\prove_guards.py")
t = P.read_text(encoding="utf-8")
landed = []


def sub(label, old, new):
    global t
    if old not in t:
        raise SystemExit("NOT FOUND (%s):\n%s" % (label, old[:220]))
    t = t.replace(old, new, 1)
    landed.append(label)


# The file constant for the new test classes, beside the existing ones.
sub("test-class constants for round 6b",
    'FAILCLOSED = ',
    'SUBJECTS = ("tests/test_tools.py::TestEveryNamedCharacterIsMeasured::")\n'
    'SEARCHWORK = ("tests/test_tools.py::TestSearchBoundsItsTotalWork::")\n'
    'FAILCLOSED = ')

sub("the subject guard points at the tests that decide it",
    """        [MEASURED + "test_every_costly_pattern_is_refused"],
        'a pattern naming nine or more characters pushes its dangerous one out of every synthetic subject, so the child returns in milliseconds and the pass is cached',
    ),
]""",
    """        [SUBJECTS + "test_a_hidden_character_still_reaches_a_subject",
         SUBJECTS + "test_every_character_of_a_wide_alphabet_reaches_one"],
        'a pattern naming nine or more characters pushes its dangerous one out of every synthetic subject, so the child returns in milliseconds and the pass is cached',
    ),
    Guard(
        'one search cannot outrun its deadline', SEARCH_TOOLS,
        '        if self._clock() >= self._deadline:',
        '        if False:',
        [SEARCHWORK + "test_the_matcher_stops_when_its_time_is_spent",
         SEARCHWORK + "test_a_search_that_stopped_early_says_so"],
        'a pattern measured at 0.2s against one 2000-character subject is then applied to every string in the project -- a hundred thousand of them -- and nothing stops it, while `limit` cannot help because a pattern picked to be slow matches nothing',
    ),
    Guard(
        'a search that gave up says so', SEARCH_TOOLS,
        '''        spent = matcher.note() if matcher is not None else None
        if spent:''',
        '''        spent = None
        if spent:''',
        [SEARCHWORK + "test_a_search_that_stopped_early_says_so"],
        'a search that stopped on its deadline returns the same shape as one that read the whole project, so "no hits" reads as "there are none"',
    ),
    Guard(
        'a walk stops on the deadline, not only on the limit', SEARCH_TOOLS,
        '''        return self.full or (self.matcher is not None
                             and self.matcher.exhausted)''',
        '''        return self.full''',
        [SEARCHWORK + "test_a_stopped_search_keeps_what_it_found"],
        'the text test answers False for the rest of the project while every scope still loads and decodes all of it, so the deadline bounds the matching and nothing bounds the walk',
    ),
]""")

t.encode("utf-8")
tmp = P.with_suffix(".py.tmp")
tmp.write_text(t, encoding="utf-8", newline="")
tmp.replace(P)
print("landed:")
for line in landed:
    print("   " + line)
