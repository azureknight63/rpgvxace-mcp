"""Searching a whole project: text, command codes, and where each hit lives.

Split out of misc_tools, which had grown to six unrelated tools in one file.
Everything here serves one question -- "where in this project does X appear?"
-- and the answer has to say where it did *not* look as well as what it found,
because an empty result means nothing otherwise.
"""

from __future__ import annotations

import re
from typing import Any

from ..analysis import Location, iter_command_lists
from ..commands import command_name, summarize
from ..project import ProjectError
from ..rubytypes import RubyObject, RubyString, _bare_name
from ..schema import ARRAY, HASH
from .pattern_safety import PatternTooCostly, check_pattern
from .registry import (
    ARR,
    BOOL,
    Context,
    INT,
    STR,
    ToolError,
    opt_bool,
    opt_int,
    opt_str,
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

SEARCH_SCOPES = ["events", "database", "maps", "scripts"]


@tool(
    "search",
    "Search the whole project",
    "Find text or command codes anywhere in the project: dialogue and comments "
    "in event command lists, database entry names and descriptions, map names, "
    "and the Ruby scripts. Every hit says exactly where it lives so you can go "
    "straight to it with event_get or db_get (or the scripts tool, for a "
    "script hit). The reply also says which scopes were actually read, which "
    "of them could not be read at all, and -- as \"may_have_more\" -- whether "
    "the scan stopped on reaching the limit rather than running out of "
    "project. That last one is not the same as validate's \"truncated\": this "
    "scan stops at the limit and so never drops a hit it found, while "
    "validate collects first and then cuts.",
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
                           % (MAX_PATTERN_CHARS, MAX_SUBJECT_CHARS)),
        "regex": prop(BOOL, "Treat query as a Python regular expression."),
        "command_code": prop(INT, "Find every event command with this code, "
                                  "instead of searching text."),
        "scopes": prop(ARR, "Where to look (default all): " + ", ".join(SEARCH_SCOPES),
                       items={"type": STR, "enum": SEARCH_SCOPES}),
        "limit": prop(INT, "Maximum hits to return (default 100, at most "
                           "%d)." % MAX_HITS, minimum=1, maximum=MAX_HITS),
    }),
)
def search(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Find text or a command code across the project.

    Reports which scopes it actually read, because an empty result means
    nothing without knowing where the search went.
    """
    scopes = scopes_arg(args, "scopes", SEARCH_SCOPES)
    limit = opt_int(args, "limit", 100, minimum=1, maximum=MAX_HITS) or 100
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

    matcher = _matcher(args, query)
    found = _Hits(limit)

    # A command_code search only has anything to look at in event command
    # lists, so the other scopes are skipped for it. Each scope stops of its
    # own accord once the collector is full, so reaching the limit in the
    # first scope does not read the rest.
    if "events" in scopes:
        _search_events(ctx, found, matcher, code_filter)
    if code_filter is None:
        for scope, walk in (("database", _search_database),
                            ("maps", _search_maps),
                            ("scripts", _search_scripts)):
            if scope in scopes and not found.full:
                walk(ctx, found, matcher)
    return found.reply(matcher)

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

    __slots__ = ("rows", "limit", "searched", "unsearchable", "unreadable")

    def __init__(self, limit: int):
        self.rows: list[dict[str, Any]] = []
        self.limit = limit
        self.searched: list[str] = []
        self.unsearchable: dict[str, str] = {}
        self.unreadable: list[int] = []

    @property
    def full(self) -> bool:
        return len(self.rows) >= self.limit

    def searching(self, scope: str) -> None:
        """Record a scope as read. Called once its file has actually loaded."""
        if scope not in self.searched:
            self.searched.append(scope)

    def add(self, location: Location, text: str,
            extra: "dict[str, Any] | None" = None) -> None:
        # Capped like a finding's text: one minified script line or crafted
        # note would otherwise be reflected whole into the response.
        if len(text) > MAX_HIT_CHARS:
            text = text[:MAX_HIT_CHARS] + "... (%d characters)" % len(text)
        row = {"where": location.describe(), "location": location.as_dict(),
               "text": text}
        if extra:
            row.update(extra)
        self.rows.append(row)

    def reply(self, matcher: "_Matcher | None") -> dict[str, Any]:
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
        if matcher is not None and matcher.clipped:
            out["strings_clipped_to_first_chars"] = {
                "characters": MAX_SUBJECT_CHARS, "count": matcher.clipped}
            out["notes"] = (
                "%d string%s longer than %d characters, and a regular "
                "expression is matched against that much of each. A hit "
                "further in was not found and is not reported as absent. "
                "Search for a plain substring (leave regex off) to read every "
                "string in full."
                % (matcher.clipped, "s were" if matcher.clipped > 1 else " was",
                   MAX_SUBJECT_CHARS))
        if self.unreadable:
            out["scripts_not_searched"] = self.unreadable
        if self.unsearchable:
            out["could_not_read"] = self.unsearchable
        return out

def _search_events(ctx: Context, found: _Hits, matcher: "_Matcher | None",
                   code_filter: "int | None") -> None:
    """Every event command list on every map, plus common events and troops."""
    found.searching("events")
    for location, commands in iter_command_lists(ctx.project):
        if found.full:
            return
        for index, command in enumerate(commands):
            if found.full:
                return
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", 0)
            named = {"code": code, "command": command_name(code)}
            if code_filter is not None:
                if code == code_filter:
                    found.add(location.at(index),
                              summarize(command, ctx.codec), named)
                continue
            for text in _command_texts(ctx, command):
                if matcher and matcher(text):
                    found.add(location.at(index), text, named)
                    if found.full:
                        return

def _search_database(ctx: Context, found: _Hits,
                     matcher: "_Matcher | None") -> None:
    """Every string field of every entry of every id-indexed table."""
    for key, entry in ctx.project.data_files.items():
        if found.full:
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
            if found.full:
                return
            if not isinstance(item, RubyObject):
                continue
            for field, value in item.ivars.items():
                if not isinstance(value, RubyString):
                    continue
                text = ctx.codec.decode(value.data)
                if text and matcher and matcher(text):
                    found.add(Location("database", table=key,
                                       entry_id=entry_id,
                                       field=_bare_name(field)), text)
                    if found.full:
                        return

def _search_maps(ctx: Context, found: _Hits,
                 matcher: "_Matcher | None") -> None:
    """The map names in the tree. The maps themselves are the events scope."""
    try:
        rows = ctx.project.entries("map_infos")
    except (ProjectError, ValueError) as exc:
        found.unsearchable["map_infos"] = str(exc)
        return
    # Once the read succeeds: before it, a file that would not load claimed to
    # have been searched.
    found.searching("maps")
    for map_id, info in rows:
        if found.full:
            return
        if not isinstance(info, RubyObject):
            continue
        name = info.get("name")
        text = ctx.codec.decode(name.data) if isinstance(name, RubyString) else ""
        if text and matcher and matcher(text):
            found.add(Location("map", map_id=map_id, field="name"), text)

def _search_scripts(ctx: Context, found: _Hits,
                    matcher: "_Matcher | None") -> None:
    """Every line of every script slot that inflates."""
    try:
        scripts_list = ctx.project.load_scripts()
    except (ProjectError, ValueError) as exc:
        found.unsearchable["scripts"] = str(exc)
        return
    found.searching("scripts")
    for script in scripts_list:
        if found.full:
            return
        if "code" not in script:
            # A slot whose body would not inflate was not searched, and saying
            # so beats reporting no hits in it.
            found.unreadable.append(script["index"])
            continue
        for line_number, line in enumerate(script["code"].splitlines(), start=1):
            if matcher and matcher(line):
                found.add(_script_at(script, line_number), line.strip(),
                          {"script_name": script.get("name")})
                if found.full:
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
    if not opt_bool(args, "regex"):
        needle = query.lower()
        return _Matcher(lambda text: needle in text.lower(), whole=True)
    if len(query) > MAX_PATTERN_CHARS:
        raise ToolError(
            "the pattern is %d characters, above the %d this server compiles. "
            "Search for a plain substring instead, or narrow the pattern."
            % (len(query), MAX_PATTERN_CHARS))
    try:
        check_pattern(query)
    except PatternTooCostly as exc:
        raise ToolError(str(exc))
    try:
        pattern = re.compile(query, re.IGNORECASE)
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
    """One search's text test, and how much text it did not look at.

    A regex is matched against the first MAX_SUBJECT_CHARS of each candidate
    while a plain substring search reads all of it. That difference used to be
    invisible: a regex search of a project with long script lines or note
    fields reported "no hits" for text that was there, and reported it in the
    same shape as a search that had genuinely looked everywhere. So the
    shortfall is counted and `search` reports it, because a caller that cannot
    tell those apart will trust the wrong one.
    """

    __slots__ = ("_test", "whole", "clipped")

    def __init__(self, test, whole: bool):
        self._test = test
        self.whole = whole
        self.clipped = 0

    def __call__(self, text: str) -> bool:
        if not self.whole and len(text) > MAX_SUBJECT_CHARS:
            self.clipped += 1
        return self._test(text)

def _command_texts(ctx: Context, command: RubyObject) -> list[str]:
    out = []
    for value in command.get("parameters") or []:
        if isinstance(value, RubyString):
            out.append(ctx.codec.decode(value.data))
        elif isinstance(value, list):
            out.extend(ctx.codec.decode(v.data) for v in value
                       if isinstance(v, RubyString))
    return [t for t in out if t]
