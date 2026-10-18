"""Maps the party can enter and cannot walk back out of.

`reachability` answers "can the party get *to* this map". This is the
converse question, asked of the same direct-transfer graph: once there, can
the party get anywhere else. A map with no way out is a softlock candidate
-- or a one-way story beat, and the two are indistinguishable to a static
reader unless the shape of the graph says which.

Three findings, from the strongly connected components of the graph
restricted to the maps the party can reach:

* ``no_exit`` -- a reachable map with no outgoing transfer to another map.
  A transfer back to the same map does not count: it is a reposition, not
  an exit. A transfer to a map id nothing on disk holds does not count
  either, since nothing can load it; those ids are listed beside the finding
  so a "door to nowhere" is not mistaken for a wall.
* ``closed_regions`` -- two or more maps that transfer among themselves and
  to nothing outside the set (a sink component), not containing the start
  map. Every map in one has an exit, so none is ``no_exit``, and the party
  still cannot leave the set.
* ``one_way`` -- reachable maps that have an exit and still cannot get back
  to the start map: the party can only move further on. This is ordinary
  story progression more often than a defect and is reported so a caller can
  tell it from the two above, not as a problem.

What a static reader cannot see is exactly what would rescue the party, so
every ``no_exit`` and ``closed_regions`` finding says how far to trust it.
It is marked ``uncertain`` with its ``reasons`` when:

* any transfer in the project reads its destination from a variable
  (``unresolved_transfers`` > 0) -- it may be the exit;
* a common event or troop page contains a Transfer Player command
  (``common_event_transfers``) -- those can be called from any map, so they
  are exits the graph does not attribute to a map;
* a script that **may transfer the player** can run while the party is on
  the finding's maps: on one of their own events (``map_script``), or in a
  common event those events call -- followed through further calls -- or
  in an autorun or parallel common event or a troop page, which can run
  anywhere (``common_event_script``). Up to three such places are listed
  under ``may_exit_via``.

"May transfer" is decided from the script text, by the same pattern
matching `reachability` uses, never by Ruby parsing. A script may transfer
when it calls ``reserve_transfer`` or assigns ``player_new_map_id`` with a
map id that is not an integer literal (a literal one is already a graph
edge, so it is an exit the graph sees, not an uncertainty); when it calls
``eval``, ``send`` or one of their relatives, which can call anything; when
it names a method or class that the Scripts file defines around such a
call (``go_home`` defined as ``$game_player.reserve_transfer(...)``, or the
stock ``command_201``); when its text was too long to read in full; or,
for every script, when the Scripts file cannot be read. Any other script
cannot transfer as far as this reader can see -- ``$game_variables[1] = 2``
does not make a map less of a dead end. Not followed: a helper that calls
another helper (only the method holding the call is named), aliases, and a
class body that ``def`` lines do not mark.

A vehicle (boat, airship) can also carry the party off a map without a
Transfer Player command at all; none of that is visible in event data, and
this module does not pretend otherwise.

If the walk was stopped by its budget the graph is partial, and a partial
graph invents dead ends, so no findings are reported in that case.
"""

from __future__ import annotations

import re
from typing import Any

from ..commands import CODE_CALL_COMMON_EVENT
from ..project import Project, ProjectError
from ..rubytypes import RubyObject
from .reachability import (
    _MAX_SCRIPT_LINE_BYTES,
    _reachable_from,
    _scan_script,
    _script_blocks,
    _start_map_id,
    _strip_comments_and_strings,
    _transfer_graph,
)
from .walk import WalkBudget, _int, _safe_entries, _seq, iter_command_lists


# A common event's @trigger: 1 autorun, 2 parallel. Both run on whatever
# map the party is on, so a script in one is reachable from every map.
_RUNS_ANYWHERE = (1, 2)

# Places named under `may_exit_via`, per finding.
_MAX_EXIT_PLACES = 3

# A call that can reach any method by name, so any transfer.
_DYNAMIC_CALL = re.compile(
    r"(?<![\w$@])(?:eval|instance_eval|class_eval|module_eval|instance_exec"
    r"|class_exec|send|__send__|public_send)\b(?![?!=])"
    r"|(?<![\w$@])method\s*\(")
_NAME = re.compile(r"[A-Za-z_]\w*[?!]?")
_DEF_LINE = re.compile(r"^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!]?)")
_CLASS_LINE = re.compile(r"^\s*(?:class|module)\s+(?:[A-Z]\w*::)*([A-Z]\w*)")
_TRANSFER_WORDS = ("reserve_transfer", "player_new_map_id")
_RESERVE_COMMON = re.compile(
    r"(?<![\w$@])reserve_common_event\b\s*\(?\s*(\d+)?")


def _transfer_helpers(project: Project, budget: WalkBudget
                      ) -> "frozenset[str] | None":
    """Names in the Scripts file that hold a transfer call, or None.

    The method around each ``reserve_transfer`` / ``player_new_map_id``
    call the scanner recognises, and the class or module around that --
    ``SceneManager.call(Scene_Teleport)`` names the class, not the method.
    "Around" is the nearest ``def`` or ``class`` line above, which is what a
    line scan can know without `end` matching. None when the file, or any
    slot of it, cannot be read: then nothing can be ruled out.
    """
    try:
        slots = project.load_scripts()
    except (ProjectError, ValueError, OSError):
        return None
    names: set[str] = set()
    for slot in slots:
        if not budget.spend(1):
            return None
        code = slot.get("code")
        if not isinstance(code, str):
            return None
        if not any(word in code for word in _TRANSFER_WORDS):
            continue
        owner_def = owner_class = None
        for line in code.split("\n"):
            defined = _DEF_LINE.match(line)
            if defined:
                owner_def = defined.group(1)
            opened = _CLASS_LINE.match(line)
            if opened:
                owner_class, owner_def = opened.group(1), None
            if not any(word in line for word in _TRANSFER_WORDS):
                continue
            scan = _scan_script(line[:_MAX_SCRIPT_LINE_BYTES])
            if scan.literals or scan.unresolved:
                names.update(n for n in (owner_def, owner_class) if n)
    return frozenset(names)


def _may_transfer(text: str, clipped: bool,
                  helpers: "frozenset[str] | None") -> bool:
    """Whether one script block might move the party to another map."""
    if clipped or helpers is None:
        return True
    if _scan_script(text).unresolved:
        return True
    code = _strip_comments_and_strings(text)
    if _DYNAMIC_CALL.search(code):
        return True
    return any(name in helpers for name in _NAME.findall(code))


def _calls(project: Project, commands: list[Any]) -> "set[int] | None":
    """Common event ids this list calls; None when one is not a literal."""
    called: set[int] = set()
    for command in commands:
        if not isinstance(command, RubyObject):
            continue
        if _int(command.get("code")) == CODE_CALL_COMMON_EVENT:
            params = _seq(command.get("parameters"))
            target = _int(params[0]) if params else None
            if target is not None:
                called.add(target)
    for text, _ in _script_blocks(project, commands):
        for match in _RESERVE_COMMON.finditer(
                _strip_comments_and_strings(text)):
            # A run of more than nine digits is not an id any project has
            # (and `int()` of 4,300 would raise): treat it as "any".
            if match.group(1) is None or len(match.group(1)) > 9:
                return None
            called.add(int(match.group(1)))
    return called


def _union(calls: "set[int] | None", into: "set[int] | None"
           ) -> "set[int] | None":
    """Two call sets joined, where None ("any common event") absorbs."""
    return None if calls is None or into is None else into | calls


class _ScriptExits:
    """Where a script that may transfer can run, and what calls what.

    A call set of None means "any common event": a call whose id is not a
    literal. `anywhere` is what runs whatever map the party is on --
    autorun and parallel common events, and whatever a troop page calls.
    """

    __slots__ = ("map_places", "map_calls", "common_places", "common_calls",
                 "anywhere", "anywhere_places", "scripts_readable")

    def __init__(self) -> None:
        self.map_places: dict[int, list[str]] = {}
        self.map_calls: dict[int, "set[int] | None"] = {}
        self.common_places: dict[int, str] = {}
        self.common_calls: dict[int, "set[int] | None"] = {}
        self.anywhere: "set[int] | None" = set()
        self.anywhere_places: list[str] = []
        self.scripts_readable = True

    def called_from(self, maps: list[int]) -> set[int]:
        """Every common event that can run while the party is on `maps`.

        What their events call, what runs anywhere, and everything those
        call in turn.
        """
        roots = self.anywhere
        for m in maps:
            roots = _union(self.map_calls.get(m, set()), roots)
        every = set(self.common_calls)
        if roots is None:
            return every
        seen: set[int] = set()
        frontier = list(roots)
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            calls = self.common_calls.get(current, set())
            if calls is None:
                # Any common event can run from here, so the closure is all
                # of them. Pushing `every` again for each such event was
                # N * N entries for N events that each call a non-literal id.
                return seen | every
            frontier.extend(calls)
        return seen


def _script_exits(project: Project, budget: WalkBudget) -> _ScriptExits:
    """One walk: every script that may transfer, and every call between lists."""
    helpers = _transfer_helpers(project, budget)
    exits = _ScriptExits()
    exits.scripts_readable = helpers is not None
    for location, commands in iter_command_lists(project, None, budget):
        risky = any(_may_transfer(text, clipped, helpers)
                    for text, clipped in _script_blocks(project, commands))
        calls = _calls(project, commands)
        where = location.describe()
        if location.kind == "map_event" and location.map_id is not None:
            m = location.map_id
            exits.map_calls[m] = _union(calls, exits.map_calls.get(m, set()))
            if risky:
                exits.map_places.setdefault(m, []).append(where)
        elif location.kind == "common_event" and location.entry_id is not None:
            e = location.entry_id
            exits.common_calls[e] = _union(calls,
                                           exits.common_calls.get(e, set()))
            if risky:
                exits.common_places.setdefault(e, where)
        else:
            # A troop page runs in battle, and a battle can be fought on
            # any map: what it calls is called from everywhere.
            exits.anywhere = _union(calls, exits.anywhere)
            if risky:
                exits.anywhere_places.append(where)
    for entry_id, common in _safe_entries(project, "common_events"):
        if not budget.spend(1):
            break
        if isinstance(common, RubyObject) \
                and _int(common.get("trigger")) in _RUNS_ANYWHERE:
            exits.anywhere = _union({entry_id}, exits.anywhere)
    return exits


def _components(nodes: set[int], edges: dict[int, set[int]]
                ) -> list[list[int]]:
    """Strongly connected components (iterative Tarjan), each sorted."""
    index: dict[int, int] = {}
    low: dict[int, int] = {}
    on_stack: set[int] = set()
    stack: list[int] = []
    out: list[list[int]] = []
    counter = 0
    for root in sorted(nodes):
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(sorted(edges.get(root, ()))))]
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(edges.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                out.append(sorted(component))
    return out


def map_dead_ends(project: Project, budget: "WalkBudget | None" = None
                  ) -> dict[str, Any]:
    """Reachable maps the party cannot leave, or cannot return from."""
    budget = WalkBudget() if budget is None else budget
    on_disk = set(project.map_ids_on_disk())
    start = _start_map_id(project)
    map_edges, common_targets, unresolved = _transfer_graph(project, budget)
    exits = _script_exits(project, budget)
    reachable = _reachable_from(start, map_edges, common_targets, on_disk)
    result: dict[str, Any] = {
        "start_map": start,
        "no_exit": [],
        "closed_regions": [],
        "one_way": [],
        "unresolved_transfers": unresolved,
    }
    stopped = budget.note("dead_ends")
    if stopped:
        result["dead_ends_stopped"] = stopped
        return result
    if start is None or not reachable:
        result["dead_ends_note"] = (
            "there is no readable start position to walk from, so no map "
            "can be called reachable and none is reported as a dead end.")
        return result

    edges = {m: (map_edges.get(m, set()) & reachable) - {m}
             for m in reachable}
    came_from: dict[int, set[int]] = {m: set() for m in reachable}
    for source, targets in edges.items():
        for target in targets:
            came_from[target].add(source)

    global_reasons = []
    if unresolved:
        global_reasons.append("unresolved_transfers")
    if common_targets:
        global_reasons.append("common_event_transfers")

    def judge(maps: list[int]) -> dict[str, Any]:
        reasons = list(global_reasons)
        places = [where for m in maps
                  for where in exits.map_places.get(m, ())]
        if places:
            reasons.append("map_script")
        shared = [exits.common_places[e]
                  for e in sorted(exits.called_from(maps))
                  if e in exits.common_places]
        shared += exits.anywhere_places
        if shared:
            reasons.append("common_event_script")
        info: dict[str, Any] = {
            "entered_from": sorted(set().union(
                *(came_from[m] for m in maps)) - set(maps)),
            "uncertain": bool(reasons),
        }
        if reasons:
            info["reasons"] = reasons
        if places or shared:
            info["may_exit_via"] = (places + shared)[:_MAX_EXIT_PLACES]
        return info

    can_return: set[int] = set()
    frontier = [start]
    while frontier:
        current = frontier.pop()
        if current in can_return:
            continue
        can_return.add(current)
        frontier.extend(came_from[current])

    classified: set[int] = set()
    for component in _components(reachable, edges):
        members = set(component)
        if start in members or any(edges[m] - members for m in component):
            continue
        classified |= members
        if len(component) == 1:
            m = component[0]
            entry = {"map_id": m, **judge(component)}
            dangling = sorted((map_edges.get(m, set()) - on_disk) - {m})
            if dangling:
                entry["transfers_to_missing_maps"] = dangling
            result["no_exit"].append(entry)
        else:
            result["closed_regions"].append(
                {"maps": component, **judge(component)})
    # The start map itself, when the party cannot leave it at all.
    if not edges[start]:
        entry = {"map_id": start, **judge([start])}
        dangling = sorted((map_edges.get(start, set()) - on_disk) - {start})
        if dangling:
            entry["transfers_to_missing_maps"] = dangling
        result["no_exit"].append(entry)
        classified.add(start)
    result["no_exit"].sort(key=lambda e: e["map_id"])
    result["closed_regions"].sort(key=lambda e: e["maps"])
    result["one_way"] = sorted(reachable - can_return - classified)
    result["dead_ends_note"] = (
        "static finding over direct Transfer Player edges only. A variable "
        "or script transfer, a common event, or a vehicle can leave a map "
        "this graph cannot; `uncertain` findings name which of those apply, "
        "counting only scripts that may transfer and can run on that map. "
        "`one_way` maps have an exit and cannot return to the start map: "
        "usually story progression, not a defect."
        + ("" if exits.scripts_readable else
           " The Scripts file could not be read in full, so no script "
           "could be ruled out as a transfer."))
    return result
