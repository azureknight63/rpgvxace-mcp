"""Deciding whether a caller-supplied regular expression is safe to run here.

Python's ``re`` backtracks, and a match in progress cannot be interrupted: no
timeout, no signal and no thread kill reaches it, because the matcher holds the
GIL for the whole of a ``str`` match. So one search wedges this single-threaded
server for the life of the process, and the user's only recovery is to kill it.

That is not hypothetical. ``("a?" * 24) + "b"`` is 49 characters, contains no
parentheses, and takes **19 seconds against a 60-character subject** -- against
the 2000-character subject `search` matches, it does not finish. The gate this
module replaces refused a quantifier applied to a group, which that pattern
walks straight past.

So the pattern is analysed before it is allowed to run, and refused unless the
backtracking it can force is small. The analysis is deliberately structural,
using the standard library's own parser rather than a scan of the pattern text:
the hand-written scan that came before could not see repeat bounds, alternation
branches or nesting, which is exactly where the cost lives.

What this bounds, stated plainly, because the last version of this claimed more
than it delivered: it bounds the product of the *freedoms* of backtrack points
that can compete for the same characters. It is a budget, checked against a
measured corpus of both adversarial and ordinary patterns
(``TestSearchPatternsStayFast``), not a proof of a time bound. A pattern that
passes can still be slower than a plain substring search; what it cannot be is
exponential in the pattern's length.
"""

from __future__ import annotations

from typing import Any

try:                                        # pragma: no cover - version shim
    from re import _parser as _sre_parse    # 3.11 and later
except ImportError:                         # pragma: no cover
    import sre_parse as _sre_parse          # 3.10 and earlier

# The most backtracking paths a pattern may be able to force. Chosen by
# measurement, not taste: at this budget the slowest pattern that still passes
# ("a?" fourteen times, against a subject of 2000 matching characters) takes
# 0.73 seconds, while every ordinary search pattern in the corpus test costs
# under 4096 and the chains that grow with the pattern's length are refused.
# One notch higher measured about three seconds for the same shape, which is
# more than a read-only search should ever be able to cost.
MAX_BACKTRACK_PATHS = 1 << 14

# What one unbounded quantifier (``*``, ``+``, ``{n,}``) is counted as. Not the
# subject cap: a single greedy quantifier over one class of character is walked
# back one position at a time, which is linear, and charging it 2000 would
# refuse `Fire.*Sword` -- an ordinary search. What actually explodes is several
# variable atoms competing over the same characters: at this weight two of
# them pass (64 * 64 = 4096, inside the budget) and three do not
# (64 ** 3 = 262144, well past it).
UNBOUNDED_WEIGHT = 64

# Bounded repeats are charged their real freedom, capped at the same weight, so
# `a{0,500}a{0,500}a{0,500}` is refused (it is genuinely quadratic per extra
# atom) while `\d{1,3}` costs almost nothing.
MAX_BOUNDED_WEIGHT = 64

_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
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
        # A literal against a category: assume they can meet. Deciding it
        # properly means reimplementing the category tables, and guessing
        # "disjoint" would be the direction that lets a real chain through.
        if (self.literals and other.categories) \
                or (self.categories and other.literals):
            return True
        return False


_ANYTHING = _Charset(anything=True)


class _Atom:
    """One element of a flattened pattern: what it matches, and how many ways.

    ``freedom`` is the number of lengths this atom can match -- 1 for a
    literal, 2 for ``a?``, ``UNBOUNDED_WEIGHT`` for ``a+``. Multiplying the
    freedoms of atoms that can compete for the same characters is what
    estimates the backtracking.
    """

    __slots__ = ("freedom", "charset")

    def __init__(self, freedom: int, charset: _Charset):
        self.freedom = freedom
        self.charset = charset


def check_pattern(pattern: str) -> None:
    """Refuse a pattern whose backtracking is not bounded. Compiles nothing.

    Raises :class:`PatternTooCostly` with a message naming the shape found and
    a way to rephrase it. A pattern the parser rejects is left alone: the
    caller's ``re.compile`` reports a syntax error far better than this could.
    """
    try:
        parsed = _sre_parse.parse(pattern)
    except Exception:
        return
    # _flatten raises for the shapes that are refused outright rather than
    # weighed, so its own message survives instead of the budget's.
    atoms = _flatten(parsed)
    worst, span = _worst_run(atoms)
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
        elif name == "SUBPATTERN":
            atoms.extend(_flatten(av[-1]))
        elif name == "ATOMIC_GROUP":
            atoms.extend(_flatten(av))
        elif name in _REPEATS:
            atoms.append(_repeat_atom(av))
        elif name == "BRANCH":
            atoms.append(_branch_atom(av[1]))
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
    return _Atom(min(freedom, MAX_BACKTRACK_PATHS + 1), charset)


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

    Atoms multiply while each consecutive pair can match the same character:
    that is the condition under which one atom's choices force the next to be
    retried. ``Gold\\s+\\d+`` therefore costs nothing -- a space and a digit
    cannot compete -- while ``a?a?a?...`` multiplies all the way along.

    Returns the product and how many atoms produced it, so the refusal can say
    where in the pattern the cost is.
    """
    worst, worst_span = 1, 0
    product, span = 1, 0
    previous: _Charset | None = None
    for atom in atoms:
        if previous is not None and not previous.intersects(atom.charset):
            product, span = 1, 0
        product *= atom.freedom
        span += 1
        if product > worst:
            worst, worst_span = product, span
        previous = atom.charset
    return worst, worst_span
