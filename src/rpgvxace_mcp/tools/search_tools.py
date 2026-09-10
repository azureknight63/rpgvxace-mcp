"""Searching a whole project: text, command codes, and where each hit lives.

Split out of misc_tools, which had grown to six unrelated tools in one file.
Everything here serves one question -- "where in this project does X appear?"
-- and the answer has to say where it did *not* look as well as what it found,
because an empty result means nothing otherwise.
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..analysis import (
    Location,
    MAX_WALK_STEPS,
    WalkBudget,
    iter_command_lists,
)
from ..commands import command_name, summarize
from ..project import ProjectError
from ..rubytypes import (
    RubyObject,
    RubyString,
    _bare_name,
    as_list,
    printable_text,
)
from ..schema import ARRAY, HASH
from .pattern_safety import (
    MEASURED_FLAGS,
    PatternTooCostly,
    check_pattern,
)
from .registry import (
    ARR,
    BOOL,
    Context,
    INT,
    STR,
    ToolError,
    decoded,
    opt_bool,
    opt_int,
    opt_int_default,
    opt_str,
    partial,
    prop,
    schema,
    scopes_arg,
    tool,
)


# One search's ceiling on hits, on how much of a hit's text comes back, and on
# the size of a caller-supplied pattern.
MAX_HITS = 1000


MAX_HIT_CHARS = 300


MAX_PATTERN_CHARS = 512

# How much of one candidate string a pattern is matched against. A minified
# script line can be tens of thousands of characters, and match cost grows with
# the subject; a hit past this point is not one a person was looking for.
MAX_SUBJECT_CHARS = 2000

# The same cap in bytes, for slicing before a decode: a UTF-8 character is
# at most four bytes, so this can never cut a subject the matcher would
# have read. Named because the product was written twice, once in the
# slice and once in the test that reports the cut -- and a change to the
# factor in one of the two makes the report wrong while the slice is right.
MAX_SUBJECT_BYTES = MAX_SUBJECT_CHARS * 4

# How long one search may spend applying its pattern.
#
# `pattern_safety.measure_cost` bounds one match -- about 0.2s against a
# 2000-character subject. It says nothing about how many matches there are,
# and this walks every script line and every string field in the project. A
# pattern sitting just under the measurement's clock therefore passed the gate
# and then cost that much again per candidate, with `limit` no help: a pattern
# picked to be slow matches nothing, so the collector never fills.
#
# A character budget cannot bound that product -- usable means megabytes, and
# megabytes bounds nothing. Time does compose: one match is bounded above, so
# the sum is bounded by a deadline. It is checked *between* matches, which is
# the only place it can be checked, because `re` holds the GIL for the length
# of a match and no timeout or signal reaches it. So a search overruns this by
# at most one match, and the reply says when it stopped.
MAX_SEARCH_SECONDS = 10.0

# How many of one command's parameters are searched. A real command has at
# most six; the number comes out of the file, and every one of them is
# decoded before the matcher sees the first. Generous enough that no real
# command is clipped, small enough that a crafted one cannot make a
# gigabyte of `str` between two deadline checks.
MAX_TEXTS_PER_COMMAND = 64


SEARCH_SCOPES = ["events", "database", "maps", "scripts"]


@tool(
    "search",
    "Search the whole project",
    "Find text or command codes anywhere in the project: dialogue and comments "
    "in event command lists, database entry names and descriptions, map names, "
    "and the Ruby scripts. Every hit says exactly where it lives so you can go "
    "straight to it with event_get or db_get (or the scripts tool, for a "
    "script hit). The reply also says which scopes were actually read, which "
    "of them could not be read at all. \"may_have_more\" is true when the page "
    "filled *or* when any part of the project was not read or not reached -- "
    "\"scopes_not_read\", \"files_not_read\", \"scripts_not_read\", "
    "\"command_walk_stopped\" and "
    "\"text_scan_stopped\" say which, so read those before raising limit and "
    "asking again. It is not the same as validate's \"findings_truncated\": "
    "this scan stops at the limit and so never drops a hit it found, while "
    "validate collects first and then cuts. A search also gives up after "
    "%g seconds of matching, and after %s commands walked, saying so as "
    "\"text_scan_stopped\" or \"command_walk_stopped\". Either one means the "
    "hits are real but the absences are not."
    % (MAX_SEARCH_SECONDS, "{:,}".format(MAX_WALK_STEPS)),
    schema({
        "query": prop(STR, "Text to look for (case-insensitive substring, or "
                           "a regular expression when regex is true). At most "
                           "%d characters, and as a regular expression only "
                           "%d characters of each string are matched -- the "
                           "reply says when that clipped anything. "
                           "Lookahead, lookbehind and conditional groups are "
                           "refused outright, as are a repeated group that "
                           "can match more than one way -- (a+)+, (a|ab)* -- "
                           "and long chains of optional or repeated parts: "
                           "each can backtrack for longer than this server "
                           "can be interrupted."
                           % (MAX_PATTERN_CHARS, MAX_SUBJECT_CHARS),
                      maxLength=MAX_PATTERN_CHARS),
        "regex": prop(BOOL, "Treat query as a Python regular expression."),
        "command_code": prop(INT, "Find every event command with this code, "
                                  "instead of searching text."),
        "scopes": prop(ARR, "Where to look (default all): " + ", ".join(SEARCH_SCOPES),
                       items={"type": STR, "enum": SEARCH_SCOPES}),
        "limit": prop(INT, "Maximum hits to return (default 100, at most "
                           "%d). Each hit carries at most %d characters of "
                           "its text, and says its real length when it was "
                           "longer." % (MAX_HITS, MAX_HIT_CHARS),
                      minimum=1, maximum=MAX_HITS),
    }),
)
def search(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Find text or a command code across the project.

    Reports which scopes it actually read, because an empty result means
    nothing without knowing where the search went.
    """
    scopes = scopes_arg(args, "scopes", SEARCH_SCOPES)
    limit = opt_int_default(args, "limit", 100, minimum=1, maximum=MAX_HITS)
    code_filter = opt_int(args, "command_code")
    query = opt_str(args, "query")
    if code_filter is None and not query:
        raise ToolError("pass query (text to find) or command_code")
    if code_filter is not None:
        if query:
            raise ToolError(
                "pass query or command_code, not both: a command_code search "
                "matches on the code and ignores text.")
        if "events" not in scopes:
            raise ToolError(
                "a command_code search only has anything to look at in the "
                "events scope, because only event command lists hold commands. "
                "Include \"events\" in scopes.")
        # A scope named *beside* events is deliberately not refused, and this
        # is the difference from `regex` below. Both are arguments no path
        # reads -- but `scopes_searched` names exactly which scopes were read,
        # so `["events", "database"]` comes back saying database was not one
        # of them, while a dropped `regex` left no trace anywhere in the
        # reply. Refusing here would be the second answer to a question the
        # reply already answers, and it would break a caller that passes the
        # full scope list on every call. Written down because a review has
        # now proposed the refusal twice.
        if opt_bool(args, "regex"):
            # `regex` says how to read `query`, and there is no query here.
            # `_matcher` returns on the falsy query before it looks, so this
            # was the one argument in the tools layer that no path reads and
            # nothing refused.
            raise ToolError(
                "regex says how to read query, and a command_code search "
                "matches on the code rather than on text, so it reads "
                "neither. Drop regex, or search for text instead.")

    matcher = _matcher(args, query)
    found = _Hits(limit, matcher)

    # A command_code search only has anything to look at in event command
    # lists, so the other scopes are skipped for it. Each scope stops of its
    # own accord once the collector is full, so reaching the limit in the
    # first scope does not read the rest.
    if "events" in scopes:
        _search_events(ctx, found, code_filter)
    if code_filter is None:
        for scope, walk in (("database", _search_database),
                            ("maps", _search_maps),
                            ("scripts", _search_scripts)):
            if scope in scopes and not found.done:
                walk(ctx, found)
    return found.reply()


class _Hits:
    """One search's results, and everything the reply has to say about them.

    Holds the limit as well as the hits, so a scope stops by asking rather
    than by returning the whole reply from the middle of a walk -- which is
    what the six copies of `return _result(...)` were doing, and why one of
    them had already drifted from the others.

    ``searched`` is recorded by each scope as it goes, because reporting the
    scopes that were *asked for* told the model those had been read and had
    come back empty. ``unsearchable`` keeps a scope whose file would not load
    apart from a scope that held nothing, and ``unreadable`` does the same for
    one script slot.
    """

    __slots__ = ("rows", "limit", "matcher", "searched", "unsearchable",
                 "unreadable", "empty", "walk")

    def __init__(self, limit: int, matcher: "_Matcher | None"):
        self.rows: list[dict[str, Any]] = []
        self.limit = limit
        # Held here rather than passed to each walk as well. Threading it both
        # ways meant `done` could not see the matcher's deadline, and only the
        # reply knew the text test had given up.
        self.matcher = matcher
        self.searched: list[str] = []
        self.unsearchable: dict[str, str] = {}
        # Slot index -> why it was not searched.
        self.unreadable: dict[str, str] = {}
        # Scopes that opened nothing at all. Distinct from `unsearchable`,
        # which names a file and the error it raised: these are scopes where
        # the walk swallowed whatever went wrong, so the honest answer is
        # "nothing was read here" without a claim about why.
        self.empty: dict[str, str] = {}
        # One allowance for the command walk. A file that shares objects by
        # reference renders as far more commands than its size suggests, and
        # a search that stopped early and found nothing reads exactly like one
        # that looked everywhere.
        self.walk = WalkBudget()

    @property
    def full(self) -> bool:
        """The caller's limit is reached. Distinct from `done` on purpose.

        A full page sets `may_have_more` here, and `reply` sets it again
        for every other reason the answer is partial: an unread scope, a
        spent deadline, an exhausted walk. Each of those also gets a note of
        its own, because "I stopped early" and "here is a full page" are
        different things to tell a caller -- but both mean there may be more.

        This docstring used to say a spent deadline "does not" set the flag,
        which was the opposite of what `reply` does three screens down.
        """
        return len(self.rows) >= self.limit

    @property
    def done(self) -> bool:
        """Nothing further this search does can change the answer.

        Either the page is full, or the text test has spent its deadline and
        now answers False without looking -- in which case walking the rest of
        the project only costs the loading and decoding.
        """
        return self.full or (self.matcher is not None
                             and self.matcher.exhausted)

    def searching(self, scope: str) -> None:
        """Record a scope as read. Called once its file has actually loaded."""
        if scope not in self.searched:
            self.searched.append(scope)

    def nothing_read(self, scope: str, note: str) -> None:
        """Record that a scope was entered and produced nothing to search.

        Said rather than left as an absence from `scopes_searched`: a caller
        reading a list has to notice a missing entry, and an empty result plus
        a short list is exactly the shape that reads as "looked, found none".
        """
        self.empty[scope] = note

    def add(self, location: Location, text: str,
            extra: "dict[str, Any] | None" = None) -> None:
        # Capped like a finding's text: one minified script line or crafted
        # note would otherwise be reflected whole into the response.
        if len(text) > MAX_HIT_CHARS:
            text = text[:MAX_HIT_CHARS] + "... (%d characters)" % len(text)
        # Sanitised as well as capped: this is text out of a file this
        # server did not write, going into a reply a terminal renders, and
        # `printable_text` is what every other such path goes through.
        text = printable_text(text)
        row = {"where": location.describe(), "location": location.as_dict(),
               "text": text}
        if extra:
            row.update(extra)
        self.rows.append(row)

    def reply(self) -> dict[str, Any]:
        """One search's answer, saying what it looked at as well as what it found.

        An empty result means nothing without knowing where the search went: a
        command_code search skips the text scopes, a file that would not load
        was never read, and a regex is matched against a bounded prefix of each
        string. All three are reported rather than folded into "no hits".

        Written here rather than in a free function taking six arguments, four
        of which were this object's own attributes and two of whose defaults
        were unreachable.
        """
        out: dict[str, Any] = {
            "hits": self.rows, "count": len(self.rows),
            # `>= limit` alone reported a search that found exactly `limit`
            # hits and no more as truncated, which sends a caller looking for
            # hits that are not there. The scan stops on reaching the limit, so
            # "there may be more" is the honest reading of a full page and
            # nothing stronger.
            "may_have_more": self.full,
            "scopes_searched": self.searched,
        }
        # Whether or not anything was found: the description says
        # `may_have_more` is true "when the page filled *or* when any part
        # of the project was not read or not reached", and this asked for
        # both. Three hits and a corrupt Scripts file answered
        # `files_not_read` beside `may_have_more: false`, so a caller
        # branching on the flag read a partial search as complete.
        if (self.empty or self.unsearchable or self.unreadable):
            # Nothing found, and something was not read: a scope that opened
            # nothing, a file that would not load, or a script slot that would
            # not inflate. Any of the three makes an empty result
            # indistinguishable from a completed search of a project that does
            # not contain the text -- and the tool's own description points a
            # caller at this field as the one that says whether the scan ran
            # out of project.
            out["may_have_more"] = True
        matcher = self.matcher
        if matcher is not None and matcher.clipped:
            out["strings_clipped_to_first_chars"] = {
                "characters": MAX_SUBJECT_CHARS, "count": matcher.clipped}
            # Two windows, so two sentences: a regex is matched against
            # the first MAX_SUBJECT_CHARS *characters* of a decoded
            # subject, and a plain search reads the first
            # MAX_SUBJECT_BYTES *bytes* before decoding. This note was
            # written for the first and served for both, so it reported a
            # character figure for a byte cut and offered "leave regex off
            # to read every string in full" to a caller who had.
            if matcher.whole:
                out["strings_clipped_note"] = (
                    "%d string%s longer than %s bytes, so only the first "
                    "%s bytes of each was searched. A hit past that point "
                    "was not found. Narrow the scope, or read the file "
                    "directly with db_get or scripts."
                    % (matcher.clipped,
                       "s were" if matcher.clipped > 1 else " was",
                       "{:,}".format(MAX_SUBJECT_BYTES),
                       "{:,}".format(MAX_SUBJECT_BYTES)))
            else:
                out["strings_clipped_note"] = (
                    "%d string%s longer than %s characters, and a regular "
                    "expression is matched against that much of each. A "
                    "hit past that point was not found. Search for a plain "
                    "substring (leave regex off) to read %s bytes of each "
                    "instead."
                    % (matcher.clipped,
                       "s were" if matcher.clipped > 1 else " was",
                       "{:,}".format(MAX_SUBJECT_CHARS),
                       "{:,}".format(MAX_SUBJECT_BYTES)))
        # All three through `partial`, which owns the suffixes. One mapping
        # per subject, not one key per scope: `"%s_note" % scope` built a key
        # name out of data, so a caller could not look it up in any schema
        # and had to guess at four possible spellings.
        #
        # Three subjects because there are three key spaces, not because
        # there are three kinds of fact: `empty` is keyed by scope,
        # `unsearchable` by data file, `unreadable` by script slot. They
        # were `scopes_not_read`, `could_not_read` and
        # `scripts_not_searched` -- one inside the vocabulary, one whose
        # "subject" parsed as `could`, and one outside it altogether, so
        # `TestOneVocabularyForAPartialAnswer`'s type test could see only
        # the first.
        out.update(partial("scopes", not_read=self.empty))
        out.update(partial("files", not_read=self.unsearchable))
        # Slot index to reason, because `_not_read` is a mapping: this was a
        # bare list of indices, which says which and never says why.
        # Each slot's own reason, which `load_scripts` already recorded:
        # five causes reach here and two of them -- an unexpected row shape
        # and a body that is not a String -- are not inflate failures.
        out.update(partial("scripts", not_read=dict(self.unreadable)))
        stopped = self.walk.note("search")
        if stopped:
            out.update(partial("command_walk", stopped=stopped))
            # So an empty result cannot read as "there are none".
            out["may_have_more"] = True
        if matcher is not None and matcher.clipped:
            # A clipped subject is "part of the project not read", which
            # the tool description says this flag covers. It was set for
            # every other such fact and not this one.
            out["may_have_more"] = True
        spent = matcher.note() if matcher is not None else None
        if spent:
            out["text_scan_stopped"] = spent
            out["may_have_more"] = True
        return out


def _about(code: int) -> dict[str, Any]:
    """The two facts every command hit carries about its command."""
    return {"code": code, "command": command_name(code)}


def _search_events(ctx: Context, found: _Hits,
                   code_filter: "int | None") -> None:
    """Every event command list on every map, plus common events and troops."""
    matcher = found.matcher
    # Claimed on the first list actually read, not on entry. `iter_maps` and
    # `_safe_entries` both swallow a file that will not load, so a project
    # whose maps, common events and troops are all unreadable yielded nothing
    # and this still reported the scope as searched -- which is the exact
    # thing `searching` exists to prevent, and every other scope in this file
    # moved its own claim after a successful load for the same reason.
    read_something = False
    for location, commands in iter_command_lists(ctx.project, None, found.walk):
        if not read_something:
            read_something = True
            found.searching("events")
        if found.done:
            return
        for index, command in enumerate(commands):
            if found.done:
                return
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", 0)
            if code_filter is not None:
                if code == code_filter:
                    found.add(location.at(index),
                              summarize(command, ctx.codec), _about(code))
                continue
            for text in _command_texts(ctx, command, matcher):
                if matcher and matcher(text):
                    # Built here rather than above the branch: only these two
                    # read it, and above the branch it was one dict per
                    # command walked -- fifty thousand allocations for the
                    # hundred that reach a reply. It also shadowed the
                    # `named` helper this module now imports.
                    found.add(location.at(index), text, _about(code))
                    if found.done:
                        return
    if not read_something:
        found.nothing_read("events", (
            "no event command list could be read: every map, the common "
            "events and the troops either hold no events or would not load. "
            "This is not the same as finding nothing in them -- run validate "
            "to see which files are unreadable."))


def _search_database(ctx: Context, found: _Hits) -> None:
    """Every string field of every entry of every id-indexed table."""
    matcher = found.matcher
    for key, entry in ctx.project.data_files.items():
        if found.done:
            return
        if entry.container not in (ARRAY, HASH):
            continue
        try:
            rows = ctx.project.entries(key)
        except (ProjectError, ValueError) as exc:
            found.unsearchable[key] = str(exc)
            continue
        # On the first table that reads, so a table that will not load does
        # not claim the scope. Per-table failures go to `unsearchable`.
        found.searching("database")
        for entry_id, item in rows:
            if found.done:
                return
            if not isinstance(item, RubyObject):
                continue
            for field, value in item.ivars.items():
                # Before the decode, not inside the match. `_Matcher`
                # returns False once its deadline is spent, so the loop ran
                # on to the end of the row decoding 8 KB a field for
                # nothing -- and one row can carry as many ivars as the
                # file declares.
                if found.done:
                    return
                if not isinstance(value, RubyString):
                    continue
                text = subject_text(ctx, value, matcher)
                if text and matcher and matcher(text):
                    found.add(Location("database", table=key,
                                       entry_id=entry_id,
                                       field=_bare_name(field)), text)
                    if found.done:
                        return


def _search_maps(ctx: Context, found: _Hits) -> None:
    """The map names in the tree. The maps themselves are the events scope."""
    matcher = found.matcher
    try:
        rows = ctx.project.entries("map_infos")
    except (ProjectError, ValueError) as exc:
        found.unsearchable["map_infos"] = str(exc)
        return
    # Once the read succeeds: before it, a file that would not load claimed to
    # have been searched.
    found.searching("maps")
    for map_id, info in rows:
        if found.done:
            return
        if not isinstance(info, RubyObject):
            continue
        name = info.get("name")
        text = decoded(ctx, name)
        if text and matcher and matcher(text):
            found.add(Location("map", map_id=map_id, field="name"), text)


def _search_scripts(ctx: Context, found: _Hits) -> None:
    """Every line of every script slot that inflates."""
    matcher = found.matcher
    try:
        scripts_list = ctx.project.load_scripts()
    except (ProjectError, ValueError) as exc:
        found.unsearchable["scripts"] = str(exc)
        return
    found.searching("scripts")
    for script in scripts_list:
        if found.done:
            return
        if "code" not in script:
            # A slot whose body would not inflate was not searched, and saying
            # so beats reporting no hits in it.
            found.unreadable[str(script["index"])] = (
                script.get("error")
                or "the slot's body would not inflate")
            continue
        for line_number, line in enumerate(script["code"].splitlines(), start=1):
            if matcher and matcher(line):
                found.add(_script_at(script, line_number), line.strip(),
                          {"script_name": script.get("name")})
                if found.done:
                    return


def _script_at(script: dict[str, Any], line: int) -> Location:
    """Where a script hit is, in the fields Location already has.

    A script slot is not an event command list, but Location's two integer
    slots fit it: `entry_id` carries the slot index and `index` the line
    number, which is what `Location.describe` prints for a "script" kind. An
    earlier version of this docstring claimed the fields were named separately
    to avoid that reuse, which is not what the line below does -- and a
    comment that contradicts its own code is worse than no comment.

    Read a hit back with the scripts tool, not event_get or db_get: slot and
    line are what it takes.
    """
    return Location("script", entry_id=script["index"], index=line)


def _matcher(args: dict[str, Any], query: str | None) -> "_Matcher | None":
    """The text test one search uses, compiled once.

    A caller-supplied pattern is bounded in length and refused if its
    backtracking is not bounded: `re` runs uninterruptibly, so a pattern that
    backtracks badly wedges this single-threaded server for the life of the
    process -- and the pattern can be suggested by project text the model has
    just read. :mod:`pattern_safety` decides that, and its docstring says what
    the bound is and is not.
    """
    if not query:
        return None
    # Before the branch, because the limit is declared on `query` and not on
    # `regex`: the substring path used to return above this check, so a
    # megabyte-long substring query was accepted while the same string as a
    # regex was refused -- two spellings of one argument, one declared limit,
    # one of them enforced. `TestEveryCallerFacingCeilingIsDeclared` passed it
    # because 512 appears in the description; it checks that a ceiling is
    # declared, not that it is applied.
    if len(query) > MAX_PATTERN_CHARS:
        raise ToolError(
            "the query is %d characters, above the %d this server accepts. "
            "Search for a shorter piece of the text you are looking for."
            % (len(query), MAX_PATTERN_CHARS))
    if not opt_bool(args, "regex"):
        needle = query.lower()
        return _Matcher(lambda text: needle in text.lower(), whole=True)
    try:
        check_pattern(query)
    except PatternTooCostly as exc:
        raise ToolError(str(exc))
    try:
        # The flags the *measurement* was made under, from the module
        # that made it: both spelled `re.IGNORECASE` and nothing held
        # them equal.
        pattern = re.compile(query, MEASURED_FLAGS)
    except re.error as exc:
        raise ToolError("invalid regular expression: %s" % exc)
    except RecursionError:
        raise ToolError("that pattern nests too deeply to compile")

    # Matched against a bounded prefix of each candidate: match cost grows
    # with the subject, and the budget above is chosen against this length.
    # Counted, not silent -- see _Matcher.
    return _Matcher(lambda text: pattern.search(text[:MAX_SUBJECT_CHARS]) is not None,
                    whole=False)


class _Matcher:
    """One search's text test: how much text it skipped, and when it gave up.

    A regex is matched against the first MAX_SUBJECT_CHARS of each candidate
    while a plain substring search reads all of it. That difference used to be
    invisible: a regex search of a project with long script lines or note
    fields reported "no hits" for text that was there, and reported it in the
    same shape as a search that had genuinely looked everywhere. So the
    shortfall is counted and `search` reports it, because a caller that cannot
    tell those apart will trust the wrong one.

    The deadline is the same argument one level up. `measure_cost` bounds one
    match; the number of matches is the size of the project, so the total was
    unbounded. See MAX_SEARCH_SECONDS. Once ``exhausted``, every later test
    answers False without looking -- a search that has given up must not keep
    paying for matches whose results it will not report as absences anyway.

    ``clock`` and ``seconds`` are injected only by the tests: a bound whose
    only test is "it finished in time" is a race, so they drive a clock they
    control instead.
    """

    __slots__ = ("_test", "whole", "clipped", "_clock", "_deadline",
                 "seconds", "exhausted")

    def __init__(self, test, whole: bool, clock=time.monotonic,
                 seconds: "float | None" = None):
        self._test = test
        self.whole = whole
        self.clipped = 0
        self._clock = clock
        self.seconds = MAX_SEARCH_SECONDS if seconds is None else seconds
        self._deadline = clock() + self.seconds
        self.exhausted = False

    def __call__(self, text: str) -> bool:
        if self.exhausted:
            return False
        if self._clock() >= self._deadline:
            self.exhausted = True
            return False
        if not self.whole and len(text) > MAX_SUBJECT_CHARS:
            self.clipped += 1
        return self._test(text)

    def note(self) -> "str | None":
        """What the reply says when the deadline stopped the search."""
        if not self.exhausted:
            return None
        return (
            "this search spent its %g seconds of matching before reaching the "
            "end of the project, so it stopped there. The hits below are real; "
            "text it did not reach is not reported as absent. Narrow the "
            "pattern, or pass one scope at a time." % self.seconds)


def subject_text(ctx: Context, value: RubyString,
                 matcher: "_Matcher | None" = None) -> str:
    """One string from a file, cut to what a search will read.

    Sliced *before* decoding, because the cap is meant to bound the work as
    well as the result -- `convert.decoded_text`'s docstring states that
    rule and `_search_database` was the one place that broke it, decoding a
    whole ivar and then comparing 2,000 characters of it.

    Reports the cut to the matcher, and this is the part that was a
    correctness hole rather than a cost. `_Matcher` exists because "a regex
    reads the first 2,000 characters and a plain search reads all of it"
    was invisible; it counts `clipped` only when the pattern is a regex,
    and the command scope had already truncated the text before the matcher
    saw it. So for a plain search the difference moved out of the matcher
    and became invisible again: a needle past `MAX_SUBJECT_BYTES` of one
    database note field returned no hits and the reply said nothing about a
    subject it had cut. A *script* line does not come through here --
    `_search_scripts` matches it directly -- which is worth saying because
    the first version of this docstring used one as the example.
    """
    raw = value.data
    text = ctx.codec.decode(raw[:MAX_SUBJECT_BYTES])
    # Only for a plain search. `_Matcher.__call__` counts the regex
    # path's clip itself, on the decoded length, so counting here as well
    # reported two clipped strings where one was clipped.
    if (matcher is not None and matcher.whole
            and len(raw) > MAX_SUBJECT_BYTES):
        matcher.clipped += 1
    return text


def _command_texts(ctx: Context, command: RubyObject,
                   matcher: "_Matcher | None" = None) -> list[str]:
    """Every string one command carries, bounded before any of it is built.

    Sliced, because the whole list is materialised before the matcher sees
    the first entry and `MAX_SEARCH_SECONDS` is checked only *between*
    matches -- so this was the one decode on the search path the deadline
    could not see. Measured: one command whose @parameters is 30,000 links
    to a 30 KB string is ~150 KB of file and 900 MB of `str` per command,
    built before a single comparison.

    `MAX_TEXTS_PER_COMMAND` rather than `rendered`, because these are
    matched and not rendered: a cap on the *result* would still decode
    everything first, which is the same distinction `rendered`'s own
    docstring draws.
    """
    out = []
    for value in as_list(command.get("parameters"))[:MAX_TEXTS_PER_COMMAND]:
        if isinstance(value, RubyString):
            out.append(subject_text(ctx, value, matcher))
        elif isinstance(value, list):
            out.extend(subject_text(ctx, v, matcher)
                       for v in value[:MAX_TEXTS_PER_COMMAND]
                       if isinstance(v, RubyString))
    return [t for t in out if t]
