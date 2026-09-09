r"""Deciding whether a caller-supplied regular expression is safe to run here.

Python's ``re`` backtracks, and a match in progress cannot be interrupted: no
timeout, no signal and no thread kill reaches it, because the matcher holds the
GIL for the whole of a ``str`` match. So one search wedges this single-threaded
server for the life of the process, and the user's only recovery is to kill it.

That is not hypothetical. ``("a?" * 24) + "b"`` is 49 characters, contains no
parentheses, and takes **19 seconds against a 60-character subject** -- against
the 2000-character subject `search` matches, it does not finish. The gate this
module replaces refused a quantifier applied to a group, which that pattern
walks straight past.

So a pattern is checked before it is allowed to run. The check uses the
standard library's own parser rather than a scan of the pattern text, because
the hand-written scan that came before could not see repeat bounds,
alternation branches or nesting, which is where the cost lives.

There are two gates here, and only the second one is a guarantee.

**The budget** (``check_shape``) scores a pattern by multiplying the freedoms
of backtrack points that can compete for the same characters. Three rounds of
review each found a way past it, and the measurements say the approach cannot
be repaired rather than that this one was tuned wrong: ``Gold\s+\d+`` and
``[a-z]+[a-z]+!`` score identically at 4096, and the first is instant while
the second does not finish against 2000 letters. Separating them needs to know
that ``\s`` and ``\d`` cannot meet *and* that nothing between two atoms is
required, which is automaton-level analysis. So the budget is kept for what it
demonstrably does -- it refuses every nested repeat and every homogeneous
chain without spending a process, with no false refusal in the corpus -- and
it is no longer asked to be the bound.

**The measurement** (``measure_cost``) is the bound. The pattern is compiled
and matched in a child process, against subjects built out of its own
alphabet, under a wall clock; if the child is still running when the clock
expires it is killed and the pattern is refused. That is the only way to bound
work in `re`, because a match in progress cannot be interrupted in-process:
no timeout, no signal and no thread kill reaches it, since the matcher holds
the GIL for the whole of a ``str`` match.

This still is not a proof. A subject in someone's project could be worse than
any this constructs, and the honest claim is the narrow one: a pattern that
passes has been *observed* to finish quickly on the worst text this module
knows how to build for it. What it can no longer do is pass on the strength of
a number that does not predict the cost.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from typing import Any

try:                                        # pragma: no cover - version shim
    from re import _parser as _sre_parse    # 3.11 and later
except ImportError:                         # pragma: no cover
    import sre_parse as _sre_parse          # 3.10 and earlier

# Where the cheap gate stops guessing. Not a time bound -- patterns scoring
# 4096 have been measured at both 0.000s and over fifteen seconds -- but the
# level at which no ordinary pattern in the corpus is refused while every
# nested repeat and homogeneous chain is. Anything that gets past it goes to
# the measurement, which is what actually bounds the work.
MAX_BACKTRACK_PATHS = 1 << 14

# What one unbounded quantifier (``*``, ``+``, ``{n,}``) is counted as. Not the
# subject cap: a single greedy quantifier over one class of character is walked
# back one position at a time, which is linear, and charging it 2000 would
# refuse `Fire.*Sword` -- an ordinary search. What actually explodes is several
# variable atoms competing over the same characters: at this weight two of
# them pass (64 * 64 = 4096, inside the budget) and three do not
# (64 ** 3 = 262144, well past it). Two competing unbounded quantifiers really
# can be fatal -- `[a-z]+[a-z]+!` was measured not finishing -- which is a
# reason to distrust this number as a bound, not a reason to retune it: the
# measurement is what catches that one.
UNBOUNDED_WEIGHT = 64

# Bounded repeats are charged their real freedom, capped at the same weight, so
# `a{0,500}a{0,500}a{0,500}` is refused (it is genuinely quadratic per extra
# atom) while `\d{1,3}` costs almost nothing.
MAX_BOUNDED_WEIGHT = 64

# What the parser raises for a pattern that is merely malformed, as opposed
# to one this module could not cope with. Imported from `re` rather than
# caught as `Exception`, so the two cases can be told apart.
_RE_ERROR = re.error


_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")

# How deep `_alphabet` will follow a pattern's groups before refusing it.
#
# It was 8, and reaching it returned an empty alphabet -- see `_alphabet`
# for what that cost. Raising it is half the fix: a real search nests two or
# three deep and `^(a(b(c)))$` is three, so eight was low enough that an
# ordinary-looking pattern could reach it, and a cap a caller can reach by
# accident is a cap that will be reached on purpose. Fifty is far above
# anything hand-written and far below CPython's recursion limit, which is
# the reason a cap exists at all: `_alphabet` recurses per group.
MAX_ALPHABET_DEPTH = 50
_MAXREPEAT = _sre_parse.MAXREPEAT

# Category pairs that provably cannot match the same character: a category and
# its own complement, plus the pairs that are disjoint by definition (\w
# excludes whitespace, a digit is not whitespace, a newline is neither).
#
# Listed this way round on purpose. The first version of this named the pairs
# that *overlap* and treated everything else as disjoint, which is the unsafe
# default: a pair this module had not thought of -- \n against \s, which do
# overlap -- read as unable to compete, so a chain of them scored as costing
# nothing. Anything not named here is assumed to overlap, so a pair left out
# makes this refuse more rather than less.
#
# Held as frozensets rather than tuples so the order they are written in
# cannot matter: as ordered pairs, two of these were written the opposite way
# round from how a sorted lookup spelled them, and silently never matched.
_CATEGORY_DISJOINT = frozenset(
    frozenset(pair) for pair in (
        # A category and its own complement.
        ("CATEGORY_DIGIT", "CATEGORY_NOT_DIGIT"),
        ("CATEGORY_SPACE", "CATEGORY_NOT_SPACE"),
        ("CATEGORY_WORD", "CATEGORY_NOT_WORD"),
        ("CATEGORY_LINEBREAK", "CATEGORY_NOT_LINEBREAK"),
        # Disjoint by definition: \w has no whitespace in it, a digit is
        # neither whitespace nor a newline, and a newline is whitespace and so
        # is not a word character.
        ("CATEGORY_SPACE", "CATEGORY_WORD"),
        ("CATEGORY_DIGIT", "CATEGORY_SPACE"),
        ("CATEGORY_DIGIT", "CATEGORY_NOT_WORD"),
        ("CATEGORY_LINEBREAK", "CATEGORY_WORD"),
        ("CATEGORY_DIGIT", "CATEGORY_LINEBREAK"),
    ))


class PatternTooCostly(Exception):
    """Raised for a pattern whose backtracking this server will not risk."""


class _Charset:
    """What one atom of a pattern can match, as much as is needed here.

    Only ever asked one question -- whether two atoms can match the same
    character -- so it holds literals and category names and answers
    conservatively for everything else: an atom this module does not model
    counts as matching anything, which can only make it refuse more.
    """

    __slots__ = ("anything", "literals", "categories")

    def __init__(self, anything: bool = False,
                 literals: "set[int] | None" = None,
                 categories: "set[str] | None" = None):
        self.anything = anything
        self.literals = literals or set()
        self.categories = categories or set()

    def intersects(self, other: "_Charset") -> bool:
        if self.anything or other.anything:
            return True
        if self.literals & other.literals:
            return True
        if self.categories & other.categories:
            return True
        if self.categories and other.categories:
            # Overlapping unless every cross pair is one of the known-disjoint
            # ones: an unrecognised category has to count as competing, or a
            # chain of them scores as free.
            for mine in self.categories:
                for theirs in other.categories:
                    if frozenset((mine, theirs)) not in _CATEGORY_DISJOINT:
                        return True
        # A literal against a category is decided, not assumed. Assuming they
        # meet was safe in the refusing direction but chained everything
        # together: `item\s*=\s*\d+` scored 262144, because "=" was held to
        # possibly match \s and "m" to possibly match \d, so the two \s* and
        # the \d+ all counted as competing. One character against one category
        # is a question Python answers exactly.
        if _literals_meet_categories(self.literals, other.categories):
            return True
        if _literals_meet_categories(other.literals, self.categories):
            return True
        return False


# What each category actually matches, as a test on one character. Written out
# rather than assumed, because assuming a literal might match any category made
# every literal a bridge between the atoms either side of it -- which is how an
# ordinary `item\s*=\s*\d+` came to be scored as five competing parts.
# Each character category this module models: how to test a character for
# membership, and one representative character of it.
#
# One table rather than two keyed by the same eight names. The sample is
# what builds a subject out of a pattern's own alphabet -- a homogeneous run
# of the characters a pattern mentions is what makes its atoms compete, and
# a subject of anything else makes even a fatal pattern fail fast, which is
# how a fixed list of subjects came to miss five live bypasses. Both readers
# fail open in the refuse-more direction, so a ninth category in one table
# and not the other would have degraded quietly on the path that decides
# whether a pattern is safe to run at all.
#
# `TestTheTablesKeyedOffOneListAgree` checks that every sample is a
# character its own test accepts, which two tables could not express.
_CATEGORIES = {
    "CATEGORY_DIGIT": (lambda c: c.isdigit(), "1"),
    "CATEGORY_NOT_DIGIT": (lambda c: not c.isdigit(), "a"),
    "CATEGORY_SPACE": (lambda c: c.isspace(), " "),
    "CATEGORY_NOT_SPACE": (lambda c: not c.isspace(), "a"),
    "CATEGORY_WORD": (lambda c: c.isalnum() or c == "_", "a"),
    "CATEGORY_NOT_WORD": (lambda c: not (c.isalnum() or c == "_"), "!"),
    "CATEGORY_LINEBREAK": (lambda c: c == "\n", "\n"),
    "CATEGORY_NOT_LINEBREAK": (lambda c: c != "\n", "a"),
}


def _literals_meet_categories(literals: "set[int]",
                              categories: "set[str]") -> bool:
    """Whether any of these characters falls in any of these categories.

    A category this module does not model counts as matching, which is the
    direction that refuses more.
    """
    if not literals or not categories:
        return False
    for name in categories:
        known = _CATEGORIES.get(name)
        if known is None:
            return True
        test = known[0]
        for point in literals:
            if 0 <= point < 0x110000 and test(chr(point)):
                return True
    return False


_ANYTHING = _Charset(anything=True)


class _Atom:
    r"""One element of a flattened pattern: what it matches, how many ways, and
    whether it can match nothing at all.

    ``freedom`` is the number of lengths this atom can match -- 1 for a
    literal, 2 for ``a?``, ``UNBOUNDED_WEIGHT`` for ``a+``. Multiplying the
    freedoms of atoms that can compete for the same characters is what
    estimates the backtracking.

    ``nullable`` is whether it can match the empty string. It is tracked
    separately from ``charset`` because the two answer different questions,
    and conflating them was wrong in both directions. A nullable atom does not
    *separate* its neighbours -- they stay adjacent in the subject however
    disjoint they look in the pattern, which is how ``=?~?`` repeated
    twenty-four times scored 2 and ran for over thirty seconds. But it still
    only *matches* its own body, so reporting it as matching anything refused
    ``item\s*=\s*\d+``, which is an ordinary search that costs nothing.
    """

    __slots__ = ("freedom", "charset", "nullable")

    def __init__(self, freedom: int, charset: _Charset,
                 nullable: bool = False):
        self.freedom = freedom
        self.charset = charset
        self.nullable = nullable


# How long the child gets to match the pattern against every subject built
# for it. Every pattern in the corpus test's ordinary half finishes all five
# subjects in well under a tenth of this; every attack in it was measured at
# ten seconds or more, most of them still running when killed. So the gap
# either side of this number is wide, which is what makes a wall clock a
# usable test rather than a coin toss on a loaded machine.
PREFLIGHT_SECONDS = 2.0

# The subject length the child matches against. The same cap `search` applies
# to each candidate string, so the measurement is of the worst case the tool
# can actually reach.
PREFLIGHT_SUBJECT_CHARS = 2000

# How many characters of a pattern's alphabet get a homogeneous subject of
# their own. Beyond this they share one subject that gives each a run, so a
# wide alphabet cannot push a dangerous character out of the measurement --
# which selecting the first eight by code point allowed.
MAX_SINGLE_CHARACTER_SUBJECTS = 8

# What the child runs. Kept as one string rather than a module, so there is no
# import path to get wrong and nothing of this project in the child.
#
# The pattern and its subjects arrive on stdin, one base64 line each, because
# they do not fit on a command line. Windows caps a whole command line at
# 32767 characters; base64 of ten 2000-character subjects is about 27000 for
# ASCII -- within three thousand of it -- and three times that for Japanese,
# where a character is three UTF-8 bytes. Measured: a 40-character kana class
# produced an 83101 character command line, so `subprocess.run` raised OSError
# and `measure_cost` did the right thing with the wrong question, refusing an
# ordinary Japanese search as unsafe. RPG Maker is Japanese software; that is
# not an edge case. stdin has no such limit.
# The one exit code that is not a refusal. Chosen so it cannot be confused
# with an ordinary interpreter exit (0 success, 1 traceback, 2 bad usage) or
# with a signal death, and reported by the child rather than guessed at by the
# parent -- see `measure_cost`.
COMPILE_FAILED_EXIT = 17

# The flags every measurement is made under, and the flags the caller
# has to match on. The child spelled `re.IGNORECASE` and so did the one
# caller, and nothing held the two equal -- a cost measured under one
# set of flags says nothing about a match made under another.
MEASURED_FLAGS = re.IGNORECASE


_CHILD = (
    "import base64, re, sys\n"
    "lines = sys.stdin.read().split()\n"
    "pattern = base64.b64decode(lines[0]).decode('utf-8')\n"
    "try:\n"
    "    compiled = re.compile(pattern, %d)\n"
    "except re.error:\n"
    "    sys.exit(%d)\n"
    "for encoded in lines[1:]:\n"
    "    compiled.search(base64.b64decode(encoded).decode('utf-8'))\n"
    "print('ok')\n") % (int(MEASURED_FLAGS), COMPILE_FAILED_EXIT)

# Patterns already measured and passed, in this process. A model searching for
# the same thing across several scopes would otherwise pay for a child each
# time.
#
# Passes only, deliberately. A verdict here is a *timing* observation, and the
# two refusals this can produce -- a timeout and a failure to spawn -- are both
# things a loaded machine causes once and not again; remembering one refused a
# legitimate pattern for the life of the server. A pass is safe to remember in
# the other direction: a pattern that finished in milliseconds against subjects
# built from its own characters will do so again, and `search`'s deadline
# bounds it in use regardless.
# Patterns that passed, and nothing else. `measure_cost` returns on any hit
# here, so a refusal that ever got stored would be served as a pass -- which
# is why only `_remember` writes to this, and only on a pass.
#
# A dict rather than a set purely for the insertion order: eviction has to be
# oldest-first.
_PASSED: "dict[str, None]" = {}

# A ceiling, since a caller can supply endlessly many distinct patterns.
MAX_MEASURED_CACHED = 512


def _remember(pattern: str, verdict: "str | None") -> None:
    """Remember a pass, within the ceiling. Refusals are re-measured."""
    if verdict is not None:
        return
    while len(_PASSED) >= MAX_MEASURED_CACHED:
        del _PASSED[next(iter(_PASSED))]
    _PASSED[pattern] = None


def check_pattern(pattern: str) -> None:
    """Refuse a pattern this server cannot afford to run. Compiles nothing here.

    Two gates, in cost order: the shape budget, which is arithmetic on the
    parse tree, and then the measurement, which spends a process. See the
    module docstring for which of the two is the guarantee.
    """
    check_shape(pattern)
    measure_cost(pattern)


def _children(name: str, av: Any) -> "list[Any]":
    """The sub-patterns a parsed node holds, whatever kind of node it is.

    Where each kind keeps its body is knowledge the parser owns and this
    module has to know: a SUBPATTERN's is `av[-1]`, a repeat's is `av[2]`,
    an ATOMIC_GROUP *is* its body, and a BRANCH holds a list of them at
    `av[1]`. `_alphabet` and `_flatten` each encoded all four, and
    `_repeat_atom` and `_branch_atom` encoded two of them again -- four
    copies of a fact that changes when CPython changes its parser, which it
    did between 3.10 and 3.11 (`sre_parse` became `re._parser`).

    A kind with no sub-patterns gets an empty list, so a caller can walk
    the result without asking which kind it had.
    """
    if name == "SUBPATTERN":
        return [av[-1]]
    if name in _REPEATS:
        return [av[2]]
    if name == "ATOMIC_GROUP":
        return [av]
    if name == "BRANCH":
        return list(av[1])
    return []


def _alphabet(parsed: Any, depth: int = 0) -> "set[str]":
    """Characters worth building a subject out of, taken from the pattern.

    Every literal it names, a representative for every category, and the
    endpoints of every range. `.` contributes a letter, because a run of one
    letter is the case that makes `.*` compete with what follows it.
    """
    found: set[str] = set()
    if depth > MAX_ALPHABET_DEPTH:
        # Refused, not returned empty. This used to `return found`, and an
        # empty alphabet is not "no characters" -- it is "this module could
        # not tell", which `_subjects` then turned into a run of "a".
        #
        # Measured on `((((((((([0-9]+[0-9]+!)))))))))`, 31 characters and
        # nine levels deep because CPython emits a SUBPATTERN node for
        # `(?:...)` as well as `(...)`:
        #
        #     the "a" subject the gate measured    0.0001s
        #     2,000 digits, which the gate allowed    3.9s
        #
        # a factor of about 39,000 between what was measured and what then
        # runs over every string in the project -- and `_matcher` checks
        # its deadline only between matches, because `re` holds the GIL.
        # So the child returned in milliseconds, `_remember` cached the
        # pass for the life of the process, and the server had no way out
        # but being killed. Every other way this module can fail to get an
        # answer is already a refusal; this was the one that was not.
        raise PatternTooCostly(
            "that pattern nests groups more than %d deep, which is deeper "
            "than this server reads when working out which characters to "
            "test it against -- so it cannot judge the pattern safe. "
            "Flatten the groups, or leave regex off and search for plain "
            "text, which reads every string in full."
            % MAX_ALPHABET_DEPTH)
    for op, av in parsed:
        name = str(op)
        if name == "LITERAL" and 0 < av < 0x110000:
            found.add(chr(av))
        elif name in ("ANY", "NOT_LITERAL"):
            found.add("a")
        elif name == "IN":
            for inner_op, inner_av in av:
                inner = str(inner_op)
                if inner == "LITERAL" and 0 < inner_av < 0x110000:
                    found.add(chr(inner_av))
                elif inner == "RANGE":
                    low, high = inner_av
                    found.update(chr(c) for c in (low, high) if 0 < c < 0x110000)
                elif inner == "CATEGORY":
                    known = _CATEGORIES.get(str(inner_av))
                    found.add(known[1] if known else "a")
                elif inner == "NEGATE":
                    found.add("\x00")
        else:
            # Every kind that holds sub-patterns, through the one function
            # that knows where each keeps them.
            for child in _children(name, av):
                found |= _alphabet(child, depth + 1)
    return found


def _subjects(pattern: str) -> "list[str]":
    """The texts to measure this pattern against, worst-case first.

    A run of each of the first %d characters the pattern mentions, in sort
    order, plus three more: a pair mixture, a run built from every character
    it names, and one alphanumeric mixture. A run of one character is what
    makes competing atoms backtrack, and the mixtures catch the shapes that
    need two kinds of character to alternate.

    The cap is interpolated rather than written out, because this sentence
    said "each character" against a capped eight. The count is not a fixed
    number: a run of each of the first few characters, then a pair mixture
    *if the alphabet has two*, then a run of every character it names, then
    the digits-and-spaces subject. So three subjects for a one-character
    alphabet and five for two.
    """ % MAX_SINGLE_CHARACTER_SUBJECTS
    # `re.error` only, where this caught `Exception`. A pattern the parser
    # rejects is left to `re.compile` to report in its own words, which
    # three tests hold. Anything else -- including `PatternTooCostly` from
    # the depth guard -- travels up to `check_pattern` as a refusal, because
    # a failure to read a pattern this module is about to permit is not a
    # reason to guess at its alphabet: the fallback below is a run of "a",
    # which almost any pattern survives.
    try:
        alphabet = _alphabet(_sre_parse.parse(pattern))
    except re.error:
        alphabet = set()
    # Bounded, so a pattern naming a hundred literals does not spawn a child
    # with a hundred 2000-character subjects.
    #
    # `or ["a"]` still stands, for the pattern that genuinely names no
    # character of its own -- `^$`, `\\b`, a bare backreference. That is a
    # different thing from an alphabet this module could not read, which is
    # now a refusal rather than an empty set.
    ordered = sorted(alphabet) or ["a"]
    # A run of each of the first few, individually -- one homogeneous run is
    # what makes competing atoms backtrack.
    chosen = ordered[:MAX_SINGLE_CHARACTER_SUBJECTS]
    subjects = [char * PREFLIGHT_SUBJECT_CHARS for char in chosen]
    if len(chosen) > 1:
        pair = (chosen[0] + chosen[1]) * PREFLIGHT_SUBJECT_CHARS
        subjects.append(pair[:PREFLIGHT_SUBJECT_CHARS])
    # And one subject holding a run of *every* character the pattern names,
    # because selecting the first few by code point let a pattern hide its
    # dangerous character behind eight cheaper ones: eight control characters
    # in a class pushed "=" out of every subject above, so the measurement
    # returned in milliseconds and cached a pass for a pattern that takes
    # thirty seconds on a line of "=".
    run = max(1, PREFLIGHT_SUBJECT_CHARS // max(len(ordered), 1))
    every = "".join(char * run for char in ordered)
    subjects.append(every[:PREFLIGHT_SUBJECT_CHARS])
    subjects.append(("a1 " * PREFLIGHT_SUBJECT_CHARS)[:PREFLIGHT_SUBJECT_CHARS])
    return subjects


def measure_cost(pattern: str) -> None:
    """Refuse a pattern that does not finish quickly in a process we can kill.

    This is the bound. It costs one short-lived interpreter per distinct
    pattern -- a search is user-initiated and not in a loop, so that is a
    price worth paying for the only guarantee available: `re` cannot be
    interrupted in-process, so a pattern that runs long in here would run long
    in the server with no way out but killing it.

    Every way the check can fail to produce an answer is a refusal, not a
    pass: a child that cannot be started, one that times out, and one that
    dies for any reason other than a bad pattern. Failing open on any of them
    would mean the guarantee quietly disappears -- on a machine where spawning
    is not allowed, or under an OOM killer -- which is the direction that ends
    in a wedged server. The one carve-out is a compile error, which the
    caller's own `re.compile` reports better.

    A pass is remembered and a refusal is not; see `_PASSED` for why.
    """
    import base64

    if pattern in _PASSED:
        return

    encode = (lambda text:
              base64.b64encode(text.encode("utf-8")).decode("ascii"))
    # -I isolates: it drops '' -- the process's current working directory --
    # from the child's sys.path, and ignores PYTHONPATH, sitecustomize and
    # usercustomize. Without it, `python -c` puts the CWD first, so a `re.py`
    # or `base64.py` sitting in the directory the server was started in would
    # be imported and executed by the first regex search of the session. The
    # CWD is routinely the project folder -- the README's own invocation makes
    # it so -- and the project folder is the one thing this server's threat
    # model treats as untrusted. The child needs nothing but the stdlib, so
    # isolation costs it nothing. `cwd` is set as well, so the answer does not
    # depend on how the client happened to launch us.
    # `-S` as well as `-I`: `-I` implies `-E` and `-s` but not `-S`, so the
    # child still imported `site` and built a `sys.path` for site-packages,
    # which is most of interpreter startup on Windows. `_CHILD` imports
    # `base64`, `re` and `sys` and nothing else, so the scan buys it nothing.
    command = [sys.executable, "-I", "-S", "-c", _CHILD]
    payload = "\n".join([encode(pattern)]
                        + [encode(subject) for subject in _subjects(pattern)])
    verdict: "str | None" = None
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=PREFLIGHT_SECONDS, input=payload,
                              cwd=tempfile.gettempdir())
        if done.returncode == COMPILE_FAILED_EXIT:
            # The one carve-out, deliberately: the caller's own `re.compile`
            # reports a syntax error far better than this could, and
            # `_matcher` turns that into a ToolError. Returned rather than
            # remembered -- nothing was measured, so there is no pass to cache.
            return
        if done.returncode != 0:
            # A child that died for any other reason: killed by a signal, out
            # of memory, or a `sys.executable` that is not an interpreter.
            # Refused, because this is the bound.
            #
            # This condition used to read `and "error" not in stderr.lower()`,
            # a substring test standing in for "was this a compile error?".
            # It matched MemoryError, RecursionError, ImportError and the
            # `error: unknown option -c` of a non-interpreter -- so four of
            # six abnormal exits skipped the refusal AND reached `_remember`,
            # caching an unmeasured pattern as a measured pass for the life of
            # the process. Measured, not theorised. The child now says which
            # case it is, because only the child knows.
            verdict = (
                "this server's safety check on that pattern exited abnormally "
                "(code %d), so it will not run the pattern either -- a match "
                "that cannot be interrupted is not something to risk on a "
                "check that did not complete. Search for a plain substring by "
                "leaving regex off." % done.returncode)
    except subprocess.TimeoutExpired:
        verdict = (
            "that pattern did not finish matching in %.1f seconds against "
            "text built from its own characters, so this server will not run "
            "it. A regular expression match in Python cannot be interrupted "
            "-- no timeout or signal reaches it -- so one that runs long here "
            "would run long in the server with no way to stop it. Two "
            "repeated parts that can match the same characters with nothing "
            "required between them do this: \"a*a*\", \"[a-z]+[a-z]+\", "
            "\"\\w+\\d+\". Put the literal text you are looking for "
            "between them, or leave regex off and search for a plain "
            "substring, which reads every string in full."
            % PREFLIGHT_SECONDS)
    except OSError as exc:
        verdict = (
            "this server could not run its safety check on that pattern (%s), "
            "so it will not run the pattern either -- an uninterruptible match "
            "is not something to risk on an unchecked guess. Search for a "
            "plain substring by leaving regex off." % exc)

    _remember(pattern, verdict)
    if verdict is not None:
        raise PatternTooCostly(verdict)


def check_shape(pattern: str) -> None:
    """Refuse a pattern whose backtracking is not bounded, by arithmetic alone.

    The cheap gate: no process, no matching, just the parse tree. Everything
    it refuses is genuinely costly -- no pattern in the corpus test's ordinary
    half is refused here -- but it is not the bound, because a score of 4096
    covers patterns from instant to never finishing. See the module docstring.

    A pattern the parser rejects is left alone: the caller's ``re.compile``
    reports a syntax error far better than this could.
    """
    try:
        parsed = _sre_parse.parse(pattern)
    except _RE_ERROR:
        # A syntax error, left alone on purpose: the caller's own re.compile
        # reports one far better than this could, and `_matcher` turns that
        # into a ToolError.
        return
    except Exception as exc:
        raise PatternTooCostly(_unanalysable(exc))
    try:
        # _flatten raises PatternTooCostly for the shapes that are refused
        # outright rather than weighed, so its own message survives instead of
        # the budget's -- hence the re-raise below.
        #
        # Anything *else* is a refusal too, and this used to run outside the
        # guard: both of these walk a caller-shaped tree recursively, which is
        # precisely where an unanticipated exception comes from, and the
        # promise above stopped applying one line before the code that needed
        # it. A RecursionError here reached the caller as an internal error
        # rather than as this gate declining to run.
        atoms = _flatten(parsed)
        worst, span = _worst_run(atoms)
    except PatternTooCostly:
        raise
    except Exception as exc:
        raise PatternTooCostly(_unanalysable(exc))
    if worst <= MAX_BACKTRACK_PATHS:
        return

    if span <= 1:
        # One repeat, charged past the budget on its own: an unbounded repeat
        # of something that can itself match more than one way. Named as the
        # shape it is, because "backtracks N ways" is not what a caller can
        # act on here -- the cost is in the nesting, not in a chain.
        found = ("it repeats a group that can itself match more than one way "
                 "-- \"(a+)+\", \"(a|ab)*\", \"(\\w+\\s?)*\". The time that "
                 "takes grows with the length of the text being searched, not "
                 "with the pattern, so no length limit here can bound it. "
                 "Repeat a single character or a character class instead "
                 "(\"a+\" or \"[abc]+\")")
    else:
        found = ("%d parts of it in a row can compete for the same "
                 "characters, about %s ways in total, above the %s this "
                 "server will risk. Chains of optional or repeated parts do "
                 "this -- \"a?a?a?a?...\", \"(a|ab)(a|ab)...\", "
                 "\"[a-z]+[a-z]+[a-z]+...\". Anchor the pattern with the "
                 "literal text you are actually looking for"
                 % (span, _approx(worst), MAX_BACKTRACK_PATHS))

    raise PatternTooCostly(
        "that pattern is refused, because %s. A regular expression match in "
        "Python cannot be interrupted -- no timeout or signal reaches it -- "
        "so one that backtracks like this wedges this server until it is "
        "killed, rather than merely running slowly. You can also search for a "
        "plain substring by leaving regex off." % found)


def _unanalysable(exc: Exception) -> str:
    """What to say when this gate could not run at all.

    Said in one place because it is raised from two, and the reason is the
    same both times: failing open would quietly remove the cheap half of the
    protection the moment the standard library raised something unanticipated,
    and the caller would never know it had.
    """
    return ("this server could not analyse that pattern (%s: %s), so it will "
            "not run it either. Search for a plain substring by leaving regex "
            "off." % (type(exc).__name__, exc))


def _approx(paths: int) -> str:
    """A path count said in a way that reads, rather than 18446744073709551616."""
    # Under ten thousand is said exactly. Above it, both sides of the
    # comparison used to round to the same words -- "about 16 thousand ways,
    # above the 16 thousand this server will risk" -- so the budget is now
    # always said exactly and only the pattern's own cost is rounded.
    if paths < 10000:
        return "%d" % paths
    for ceiling, divisor, suffix in ((10 ** 6, 10 ** 3, "thousand"),
                                     (10 ** 9, 10 ** 6, "million"),
                                     (10 ** 12, 10 ** 9, "billion")):
        if paths < ceiling:
            return "%d %s" % (paths // divisor, suffix)
    return "more than a trillion"


def _flatten(parsed: Any) -> "list[_Atom]":
    """One pattern as a flat list of atoms, in the order they must match.

    Groups are flattened into their parent rather than recursed into
    separately, because ``(a|ab)(a|ab)z`` only looks dangerous once the two
    branches are seen as neighbours -- which is the whole point of the
    analysis. A repeat containing another repeat or a branch is charged the
    product of the two, so the classic ``(a+)+`` still comes out far above the
    budget without needing a special case.
    """
    atoms: list[_Atom] = []
    for op, av in parsed:
        name = str(op)
        if name == "LITERAL":
            atoms.append(_Atom(1, _Charset(literals={av})))
        elif name == "NOT_LITERAL":
            atoms.append(_Atom(1, _ANYTHING))
        elif name == "ANY":
            atoms.append(_Atom(1, _ANYTHING))
        elif name == "IN":
            atoms.append(_Atom(1, _charset_of_in(av)))
        elif name in ("SUBPATTERN", "ATOMIC_GROUP"):
            # Flattened into the parent rather than charged as one atom,
            # which is what the docstring above is about. A repeat and a
            # branch are charged instead, below.
            for child in _children(name, av):
                atoms.extend(_flatten(child))
        elif name in _REPEATS:
            atoms.append(_repeat_atom(av))
        elif name == "BRANCH":
            atoms.append(_branch_atom(_children(name, av)))
        elif name in ("GROUPREF_EXISTS", "ASSERT", "ASSERT_NOT"):
            # A lookaround or conditional runs its own match at every
            # position. Refused outright rather than modelled: these are rare
            # in a search for game text, and letting one through unmeasured is
            # how the gate before this came to be wrong.
            #
            # Raised here rather than charged past the budget, because the
            # budget's refusal describes a chain or a repeated group -- which
            # a lookaround is not. `Fire(?=Sword)` was told to "repeat a
            # single character instead", and `Gold(?!\d)` to "anchor the
            # pattern with the literal text you are looking for", which it
            # already was. A caller cannot find the cause in either, so it
            # rephrases forever instead of dropping the lookaround.
            raise PatternTooCostly(
                "that pattern uses a lookahead, lookbehind or conditional "
                "group, which this server refuses outright: each one runs a "
                "match of its own at every position, and there is no way to "
                "bound that here. Drop the (?=...), (?!...), (?<=...) or "
                "(?(1)...) part -- or leave regex off and search for a plain "
                "substring, which reads every string in full.")
        # AT (anchors), GROUPREF and the rest match no characters of their own
        # and add no freedom, so they are left out of the chain entirely.
    return atoms


def _freedom_product(atoms: "list[_Atom]") -> int:
    """How many ways a run of atoms can divide the text between them.

    Stops as soon as the product is past the budget, which is what keeps this
    from building an unbounded integer out of a long pattern -- a repeat of a
    repeat of a repeat multiplies without limit otherwise. Written once
    because it had been written twice, under a repeat and under an
    alternation, and the same nested pattern would have scored differently
    depending on which one it sat inside.
    """
    product = 1
    for atom in atoms:
        product *= atom.freedom
        if product > MAX_BACKTRACK_PATHS:
            break
    return product


def _charset_of_in(items: Any) -> _Charset:
    """What a character class can match."""
    literals: set[int] = set()
    categories: set[str] = set()
    for op, av in items:
        name = str(op)
        if name == "LITERAL":
            literals.add(av)
        elif name == "RANGE":
            # Held as its endpoints. Two ranges that overlap share an
            # endpoint's neighbourhood often enough, and where they do not,
            # counting them as disjoint would be the unsafe direction -- so a
            # range is treated as matching anything.
            return _ANYTHING
        elif name == "CATEGORY":
            categories.add(str(av))
        elif name == "NEGATE":
            return _ANYTHING
        else:
            return _ANYTHING
    return _Charset(literals=literals, categories=categories)


def _repeat_atom(av: Any) -> _Atom:
    """One repeat as a single atom: its freedom, and what its body matches."""
    minimum, maximum, body = av
    unbounded = maximum is _MAXREPEAT
    inner = _flatten(body)
    inner_freedom = _freedom_product(inner)

    if unbounded and inner_freedom > 1:
        # An unbounded repeat of something that can itself match more than one
        # way is the classic catastrophe -- `(a+)+`, `(a|ab)*`, `(\w+\s?)*`
        # -- and its cost is exponential in the length of the *subject*, not
        # of the pattern. UNBOUNDED_WEIGHT stands in for one greedy quantifier
        # walking back one character at a time, which this is not, so it is
        # charged past the budget outright rather than weighed.
        return _Atom(MAX_BACKTRACK_PATHS + 1, _ANYTHING)

    if unbounded:
        freedom = UNBOUNDED_WEIGHT
    else:
        freedom = min(max(int(maximum) - int(minimum) + 1, 1), MAX_BOUNDED_WEIGHT)
        freedom *= inner_freedom
    charset = inner[0].charset if inner else _ANYTHING
    # `{0,n}` can match nothing, so it does not separate its neighbours. See
    # `_Atom`: this is reported rather than faked by widening the charset.
    return _Atom(min(freedom, MAX_BACKTRACK_PATHS + 1), charset,
                 nullable=int(minimum) == 0)


def _branch_atom(branches: Any) -> _Atom:
    """One alternation as a single atom.

    Its freedom is the number of branches, times the freedom inside the
    widest of them: `(a|ab)` can match two ways, and `(a+|b)` far more.
    """
    freedom = max(len(branches), 1)
    widest = 1
    charset = _Charset()
    for branch in branches:
        inner = _flatten(branch)
        widest = max(widest, _freedom_product(inner))
        if not inner:
            # An empty branch -- the second half of `(a|ab)`, spelled by the
            # parser as `a` then `(|b)` -- matches the empty string, so it
            # neither matches nor rules out any character and competes with
            # everything after it. Reading it as a charset that matches
            # nothing broke the chain at every group, which is how twenty
            # `(a|ab)` in a row came out as safe.
            charset = _ANYTHING
            continue
        first = inner[0].charset
        if first.anything or charset.anything:
            charset = _ANYTHING
        else:
            charset = _Charset(
                literals=charset.literals | first.literals,
                categories=charset.categories | first.categories)
    return _Atom(min(freedom * widest, MAX_BACKTRACK_PATHS + 1), charset)


def _worst_run(atoms: "list[_Atom]") -> "tuple[int, int]":
    """The largest product of freedoms over neighbouring, competing atoms.

    Atoms multiply while they can compete for the same characters: that is the
    condition under which one atom's choices force another to be retried.
    ``Gold\\s+\\d+`` therefore costs nothing -- a space and a digit cannot
    compete, and the required ``Gold`` separates them -- while ``a?a?a?...``
    multiplies all the way along.

    Two rules, and the first version of this had only half of each:

    * The run is compared against the *union* of what it has seen, not against
      the immediately preceding atom. Two atoms with a third between them are
      still competing if all three can match the same text.
    * Only a *non-nullable* atom can break the run. One that may match nothing
      does not stand between its neighbours, so ``=?~?`` repeated -- where
      every adjacent pair is literal-disjoint -- scored 2 and ran for over
      thirty seconds on a line of ``=``.

    Returns the product and how many atoms produced it, so the refusal can say
    where in the pattern the cost is.
    """
    worst, worst_span = 1, 0
    product, span = 1, 0
    seen = _Charset()
    for atom in atoms:
        if not atom.nullable and span and not seen.intersects(atom.charset):
            # A required atom that cannot match anything this run has matched:
            # the text before it and the text after it are separated by it, so
            # nothing across it competes. This is what keeps `\\s*=\\s*` cheap.
            product, span = 1, 0
            seen = _Charset()
        product *= atom.freedom
        span += 1
        if product > worst:
            worst, worst_span = product, span
        seen = _union(seen, atom.charset)
    return worst, worst_span


def _union(left: _Charset, right: _Charset) -> _Charset:
    """Everything either charset can match."""
    if left.anything or right.anything:
        return _ANYTHING
    return _Charset(literals=left.literals | right.literals,
                    categories=left.categories | right.categories)
