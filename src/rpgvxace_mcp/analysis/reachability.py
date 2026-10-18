"""Which maps the party can actually reach, from the start position.

`map_list` reports the *editor's* map tree: MapInfos parent/child, which
groups maps for the project window and says nothing about how a player
moves between them. This is the other graph -- an edge from map A to map B
exists when an event on A carries a Transfer Player command that names B
directly -- walked from the party's start map to answer the question
`map_critique` exists for: which map on disk nobody can reach, and why a
door never opens onto anywhere real.

Two shapes of transfer are deliberately not graph edges, because turning
either into one would be a claim this server cannot back up:

* a variable-indirect transfer (``designation`` reads DESIGNATION_VARIABLE)
  names its destination by reading a game variable at runtime, which a
  static reader cannot resolve. `references.REFERENCE_RULES` skips the same
  shape for the same reason. Counted rather than silently dropped, so a
  project that reaches most of its maps this way is not told they are
  unreachable.
* a transfer inside a common event is reachable from wherever the common
  event can be called or triggered -- any map's "Call Common Event", or a
  parallel-process common event with no calling map at all -- so it is
  treated as reachable as soon as the party's start map is, rather than
  from the one place the transfer happens to be written down. This can
  under-report unreachability (a common event gated behind a switch nothing
  ever sets is still treated as reachable) but it never over-reports it,
  which is the direction that matters for a finding a caller might act on.

A third shape is followed, with the same honesty about its limits: a Script
command (code 355, plus its 655 continuation lines) that calls
``reserve_transfer(map_id, x, y, d)`` -- the method Transfer Player itself
ends in, usually written ``$game_player.reserve_transfer(...)`` -- or, in VX,
assigns ``$game_temp.player_new_map_id = N``. When the first argument is a
bare integer literal it is a direct edge exactly like a designation-0
Transfer Player command (and a script transfer inside a common event or
troop page is treated like any other transfer there); anything else -- a
variable, an expression, a constant -- is counted under
``unresolved_script_transfers`` and is not an edge.

This is **pattern matching on script text, not Ruby parsing**. It does not
see: an alias or wrapper under another name, ``eval`` of a built-up string,
a transfer made inside a script in the Scripts file that an event merely
calls, a condition-branch or move-route script, heredocs / ``%q`` strings /
regex literals (which can hold a quote or ``#`` the scanner takes for code),
or whether the code around the call can ever run -- a call guarded by a
switch nothing sets still counts as an edge, which under-reports
unreachability rather than over-reporting it. Any receiver is accepted for
``reserve_transfer(``, since a project may name its player something else.
The VX ``player_new_map_id`` assignment only takes effect when
``player_transferring`` is also set; that is not checked.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from ..commands import (
    CODE_SCRIPT,
    CODE_SCRIPT_MORE,
    CODE_TRANSFER_PLAYER,
    DESIGNATION_VARIABLE,
    TRANSFER_MAP,
)
from ..project import Project, ProjectError
from ..rubytypes import RubyObject, RubyString
from .walk import WalkBudget, _int, _seq, iter_command_lists

# Bounds on reading script text, the one thing this module reads that a
# project can make arbitrarily long. `WalkBudget` charges a command list one
# step per command whatever the command carries, so the text is bounded here.
_MAX_SCRIPT_LINE_BYTES = 8192
_MAX_SCRIPT_BLOCK_CHARS = 65536


_NEWLINE = "\n"
_BACKSLASH = "\\"
# Bounded: `int()` of a literal past 4,300 digits raises, and no map id is
# longer than a few. A longer one fails to match and counts as unresolved.
_INT_ARG = r"-?\d{1,12}(?:_\d{1,12}){0,4}"
_RESERVE = re.compile(r"(?<![\w:])reserve_transfer\b(?!\s*=)")
_RESERVE_OPEN = re.compile(r"[ \t]*\(\s*|[ \t]+(?=[\w$@(-])")
_RESERVE_LITERAL = re.compile(r"(" + _INT_ARG + r")\s*[,)]")
_DEFINITION = re.compile(r"\b(?:def|undef|alias)\b[^\n;]*$")
_NEW_MAP = re.compile(r"(?<!\w)player_new_map_id[ \t]*=(?![=~])[ \t]*")
_NEW_MAP_LITERAL = re.compile(r"(" + _INT_ARG + r")[ \t]*(?:[;\n]|$)")


class _ScriptScan(NamedTuple):
    literals: list[int]     # map ids named by an integer literal
    unresolved: int         # transfer calls whose map id is not a literal


def _strip_comments_and_strings(text: str) -> str:
    """The script with comments removed and string bodies blanked.

    One character scan: a ``#`` outside a quote ends the line, ``=begin`` at
    the start of a line skips to ``=end``, and the inside of a ``'...'`` or
    ``"..."`` literal is replaced by spaces so a call quoted in a message is
    not read as code. Escapes are honoured. Not a Ruby lexer -- it only
    removes the things most likely to make a harmless line look like a
    transfer.
    """
    out: list[str] = []
    quote = ""
    i, n = 0, len(text)
    at_line_start = True
    while i < n:
        ch = text[i]
        if quote:
            if ch == _BACKSLASH and i + 1 < n:
                out.append("  ")
                i += 2
                continue
            if ch == quote:
                quote = ""
                out.append(ch)
            else:
                out.append(ch if ch == _NEWLINE else " ")
            i += 1
            continue
        if at_line_start and text.startswith("=begin", i):
            end = text.find(_NEWLINE + "=end", i)
            if end < 0:
                break
            nxt = text.find(_NEWLINE, end + 1)
            i = n if nxt < 0 else nxt
            continue
        at_line_start = ch == _NEWLINE
        if ch == "#":
            nxt = text.find(_NEWLINE, i)
            i = n if nxt < 0 else nxt
            continue
        if ch in "'\"":
            quote = ch
        out.append(ch)
        i += 1
    return "".join(out)


def _scan_script(text: str) -> _ScriptScan:
    """Transfer calls in one reassembled script block. See the module docs."""
    code = _strip_comments_and_strings(text)
    literals: list[int] = []
    unresolved = 0
    for match in _RESERVE.finditer(code):
        line_start = code.rfind(_NEWLINE, 0, match.start()) + 1
        if _DEFINITION.search(code[line_start:match.start()]):
            continue
        opened = _RESERVE_OPEN.match(code, match.end())
        if opened is None:
            continue
        arg = _RESERVE_LITERAL.match(code, opened.end())
        if arg is None:
            unresolved += 1
        else:
            literals.append(int(arg.group(1).replace("_", "")))
    for match in _NEW_MAP.finditer(code):
        arg = _NEW_MAP_LITERAL.match(code, match.end())
        if arg is None:
            unresolved += 1
        else:
            literals.append(int(arg.group(1).replace("_", "")))
    return _ScriptScan(literals, unresolved)


def _script_line(project: Project, command: RubyObject) -> "tuple[str, bool]":
    """One script line's text, decoded under a byte bound of its own.

    Not `decoded_text`: that caps at 200 characters and appends a marker,
    which would cut a call in half and write ``... (N characters)`` into the
    argument list being read. Also says whether the line was cut.
    """
    params = _seq(command.get("parameters"))
    value = params[0] if params else None
    if not isinstance(value, RubyString):
        return "", False
    return (project.codec.decode(value.data[:_MAX_SCRIPT_LINE_BYTES]),
            len(value.data) > _MAX_SCRIPT_LINE_BYTES)


def _script_blocks(project: Project, commands: list[Any]
                   ) -> "list[tuple[str, bool]]":
    """Each 355 command and its 655 lines, as ``(text, was_truncated)``.

    A 655 with no 355 before it is ignored: nothing says what it continues.
    Text past `_MAX_SCRIPT_BLOCK_CHARS` is not read, and the block says so.
    """
    blocks: list[tuple[str, bool]] = []
    lines: list[str] = []
    size = 0
    truncated = False
    in_block = False

    def flush() -> None:
        if in_block:
            blocks.append((_NEWLINE.join(lines), truncated))

    for command in commands:
        code = _int(command.get("code")) if isinstance(
            command, RubyObject) else None
        if code == CODE_SCRIPT or code != CODE_SCRIPT_MORE:
            flush()
            lines, size, truncated, in_block = [], 0, False, False
            if code != CODE_SCRIPT:
                continue
            in_block = True
        elif not in_block:
            continue
        if size >= _MAX_SCRIPT_BLOCK_CHARS:
            truncated = True
            continue
        line, cut = _script_line(project, command)
        truncated = truncated or cut
        lines.append(line)
        size += len(line) + 1
    flush()
    return blocks


def _transfer_target(code: Any, params: list[Any]) -> "int | str | None":
    """The map a Transfer Player command names, or why it cannot be read.

    Three outcomes, not two: `None` for a command that is not a transfer at
    all, the string ``"variable"`` for one whose destination is read from a
    game variable at runtime, and the target map id otherwise. A caller
    counting how many transfers it could not resolve must be able to tell
    "not a transfer" from "a transfer this could not follow", which a bare
    `None` cannot say on its own.
    """
    if _int(code) != CODE_TRANSFER_PLAYER or not params:
        return None
    if _int(params[0]) == DESIGNATION_VARIABLE:
        return "variable"
    if len(params) <= TRANSFER_MAP:
        return None
    return _int(params[TRANSFER_MAP])


class _TransferCensus(NamedTuple):
    """Everything one walk learned about transfers.

    `_transfer_graph` returns the first three fields only, which is the shape
    its other consumers were written against.
    """
    map_edges: dict[int, set[int]]
    common_targets: set[int]
    unresolved: int                 # variable-indirect Transfer Player
    script_transfers: int           # script calls naming a literal map id
    unresolved_script_transfers: int
    script_blocks_clipped: int    # script blocks longer than the scan bound


def _transfer_census(project: Project, budget: WalkBudget) -> _TransferCensus:
    """Every direct transfer edge, from commands and from script text.

    ``map_edges[m]`` is every map a transfer written on map ``m`` names
    directly. ``common_targets`` is every map named by a transfer inside a
    common event or a troop battle page -- see the module docstring for why
    those are not attributed to a single map. Script transfers with an
    integer-literal map id are edges in the same two collections.

    One pass over `iter_command_lists`, the same walk `validate` and the
    delete-safety previews share, so this pays what they pay and no more.
    """
    map_edges: dict[int, set[int]] = {}
    common_targets: set[int] = set()
    unresolved = script_edges = script_unresolved = truncated_blocks = 0

    def add_edge(location: Any, target: int) -> None:
        if location.kind == "map_event" and location.map_id is not None:
            map_edges.setdefault(location.map_id, set()).add(target)
        else:
            common_targets.add(target)

    for location, commands in iter_command_lists(project, None, budget):
        for command in commands:
            if not isinstance(command, RubyObject):
                continue
            target = _transfer_target(command.get("code"),
                                      _seq(command.get("parameters")))
            if target is None:
                continue
            if target == "variable":
                unresolved += 1
                continue
            add_edge(location, target)
        for text, truncated in _script_blocks(project, commands):
            scan = _scan_script(text)
            truncated_blocks += truncated
            script_unresolved += scan.unresolved
            for target in scan.literals:
                script_edges += 1
                add_edge(location, target)
    return _TransferCensus(map_edges, common_targets, unresolved, script_edges,
                          script_unresolved, truncated_blocks)


def _transfer_graph(project: Project, budget: WalkBudget
                    ) -> tuple[dict[int, set[int]], set[int], int]:
    """``(map_edges, common_targets, unresolved)`` -- the original shape.

    `unresolved` counts variable-indirect Transfer Player commands only;
    script-text counts are on `_transfer_census`. Script transfers with a
    literal map id are included in the edges, since they are real ones.
    """
    census = _transfer_census(project, budget)
    return census.map_edges, census.common_targets, census.unresolved


def _reachable_from(start_map: "int | None", map_edges: dict[int, set[int]],
                    common_targets: set[int], on_disk: set[int]) -> set[int]:
    """Every map id reachable from ``start_map`` by walking `map_edges`.

    `common_targets` is seeded once, up front, rather than re-added at every
    map visited: the start map is in play from the first frame, so whatever
    a common event can transfer to is reachable from the moment the party
    exists, not only from the map that happens to define the event.

    `on_disk` bounds both the seed and every edge followed, so a transfer
    naming a map id nothing on disk holds -- `check_commands`'s own finding
    -- cannot inflate the reachable set with an id nothing can load.
    """
    if start_map is None or start_map not in on_disk:
        return set()
    seen = {start_map}
    frontier = [start_map]
    for target in common_targets & on_disk:
        if target not in seen:
            seen.add(target)
            frontier.append(target)
    while frontier:
        current = frontier.pop()
        for target in map_edges.get(current, set()) & on_disk:
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


def _start_map_id(project: Project) -> "int | None":
    try:
        system = project.load_data("system")
    except (ProjectError, ValueError):
        return None
    if not isinstance(system, RubyObject):
        return None
    return _int(system.get("start_map_id"))


def map_reachability(project: Project, budget: "WalkBudget | None" = None
                     ) -> dict[str, Any]:
    """Which maps the party can reach, and which nobody can, from play start.

    ``budget`` is shared with the caller's other command walks in the same
    call, the same rule `validate` states for itself: without one a fresh
    `WalkBudget` is used. A project whose System file cannot be read reports
    every map as unreachable and says why, rather than answering silently
    from a start position it does not have -- `start_map` is `None` in that
    case and `unreachable` still lists every map on disk, since "nobody has
    told this server where the party begins" is not the same claim as
    "these maps have no path from that beginning", but it is the honest
    answer to "can this be reached" when the beginning itself is unknown.

    Counts, kept apart so a caller can tell the kinds of blind spot:
    ``unresolved_transfers`` (variable-indirect Transfer Player commands),
    ``script_transfers`` (script calls with an integer-literal map id, which
    are edges), ``unresolved_script_transfers`` (script calls this could not
    read a literal from, not edges) and ``script_blocks_clipped`` (script
    blocks longer than the scan bound, whose tail was not read).
    """
    budget = WalkBudget() if budget is None else budget
    on_disk = set(project.map_ids_on_disk())
    start_map = _start_map_id(project)
    census = _transfer_census(project, budget)
    reachable = _reachable_from(start_map, census.map_edges,
                                census.common_targets, on_disk)
    result: dict[str, Any] = {
        "start_map": start_map,
        "reachable": sorted(reachable),
        "unreachable": sorted(on_disk - reachable),
        "unresolved_transfers": census.unresolved,
        "script_transfers": census.script_transfers,
        "unresolved_script_transfers": census.unresolved_script_transfers,
        "script_blocks_clipped": census.script_blocks_clipped,
    }
    if start_map is None:
        result["reachability_note"] = (
            "the project's System data could not be read, so there is no "
            "start position to walk from; every map on disk is listed as "
            "unreachable for that reason rather than because a transfer to "
            "it is missing.")
    stopped = budget.note("reachability")
    if stopped:
        result["reachability_stopped"] = stopped
    return result
