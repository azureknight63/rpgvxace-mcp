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
"""

from __future__ import annotations

from typing import Any

from ..commands import (
    CODE_TRANSFER_PLAYER,
    DESIGNATION_VARIABLE,
    TRANSFER_MAP,
)
from ..project import Project, ProjectError
from ..rubytypes import RubyObject
from .walk import WalkBudget, _int, _seq, iter_command_lists


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


def _transfer_graph(project: Project, budget: WalkBudget
                    ) -> tuple[dict[int, set[int]], set[int], int]:
    """Every direct transfer edge in the project, sorted by where it lives.

    Returns ``(map_edges, common_targets, unresolved)``. ``map_edges[m]`` is
    every map a Transfer Player command written on map ``m`` names directly.
    ``common_targets`` is every map named by a transfer inside a common
    event or a troop battle page -- see the module docstring for why those
    are not attributed to a single map. ``unresolved`` counts the
    variable-indirect transfers seen, which name no edge at all.

    One pass over `iter_command_lists`, the same walk `validate` and the
    delete-safety previews share, so this pays what they pay and no more.
    """
    map_edges: dict[int, set[int]] = {}
    common_targets: set[int] = set()
    unresolved = 0
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
            if location.kind == "map_event" and location.map_id is not None:
                map_edges.setdefault(location.map_id, set()).add(target)
            else:
                common_targets.add(target)
    return map_edges, common_targets, unresolved


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
    """
    budget = WalkBudget() if budget is None else budget
    on_disk = set(project.map_ids_on_disk())
    start_map = _start_map_id(project)
    map_edges, common_targets, unresolved = _transfer_graph(project, budget)
    reachable = _reachable_from(start_map, map_edges, common_targets, on_disk)
    result: dict[str, Any] = {
        "start_map": start_map,
        "reachable": sorted(reachable),
        "unreachable": sorted(on_disk - reachable),
        "unresolved_transfers": unresolved,
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
