"""map_critique: design-quality analysis, as distinct from validate's correctness.

`validate` catches broken references and event-logic traps -- a transfer to
a map that does not exist, an autorun page that can never stop. Those are
questions of whether the data is *sound*. This tool starts on a different
kind of question, whether the data is *any good*: can the player ever reach
this map at all.

Each scope is one design-quality question over data this server already
parses: reachability (can the player ever reach a map), dead_ends (can they
leave it), dead_space and tile_variety (what is on it), stat_outliers (which
database entries sit far from their table) and switch_usage (which gate can
never open). `summary` reads all six and ranks what is worth a look.
`scopes` lets a caller narrow a request the same way `validate` does, so a
later scope can be added without either tool learning a new shape.

A call naming no scope runs the three whose answers are findings, plus the
summary. The other three answer with a row per map, per layer or per field
-- 60 KB of a 64 KB default reply on a real project, nearly all of it about
maps with nothing wrong -- so their full tables run only when named; the
summary still reads them and lists what stands out.
"""

from __future__ import annotations

from typing import Any

from ..analysis import WalkBudget
from .registry import (
    ARR,
    Context,
    REFRESH,
    STR,
    opt_bool,
    partial,
    prop,
    schema,
    scopes_arg,
    tool,
)


MAP_CRITIQUE_SCOPES = ("reachability", "dead_ends", "switch_usage", "summary",
                       "dead_space", "tile_variety", "stat_outliers")


# What a call naming no scope runs: the findings, not the tables.
DEFAULT_MAP_CRITIQUE_SCOPES = ("reachability", "dead_ends", "switch_usage",
                               "summary")


# The scopes `summary` reads, whether or not they are shown.
_SUMMARY_INPUTS = ("reachability", "dead_ends", "switch_usage", "dead_space",
                   "tile_variety", "stat_outliers")


# Findings one summary lists. The total is always given beside them.
MAX_SUMMARY_FINDINGS = 20


def _annotate_blank(reach: dict[str, Any], shapes: dict[str, Any]) -> None:
    """Add `unreachable_detail`: which unreachable maps are simply blank.

    `unreachable` stays a list of ints. A map with no tile on any of the
    three tile layers and no event is usually a folder-like placeholder, not
    a design finding, so each entry says which it is. Shadows and region ids
    on VX Ace's layer 3 are not tiles and do not make a map un-blank, and
    `nonzero_cells` counts tiles only; the `dead_space` table has
    `region_cells` and `shadow_cells` for a caller who wants layer 3.
    `blank` is None when the map's tile data could not be read -- unknown,
    not "has content".
    """
    by_id = {row["map_id"]: row for row in shapes["maps"]}
    reach["unreachable_detail"] = [
        {"map_id": m, "blank": by_id.get(m, {}).get("blank"),
         "width": by_id.get(m, {}).get("width"),
         "height": by_id.get(m, {}).get("height"),
         "nonzero_cells": by_id.get(m, {}).get("nonzero_cells"),
         "event_count": by_id.get(m, {}).get("event_count")}
        for m in reach["unreachable"]]


def _summary(results: dict[str, Any]) -> dict[str, Any]:
    """The ranked findings, capped, saying whether that is all of them."""
    from ..analysis import critique_summary  # on first call: see tests/test_startup.py

    built = critique_summary(
        results["reachability"], results["dead_ends"],
        results["switch_usage"], results["dead_space"],
        results["tile_variety"], results["stat_outliers"])
    findings = built["findings"]
    incomplete = built["incomplete"]
    stopped = None
    if incomplete:
        stopped = ("%s did not finish, so findings from %s are missing or "
                   "partial: %s" % (", ".join(incomplete),
                                    "it" if len(incomplete) == 1 else "them",
                                    next(iter(incomplete.values()))))
    out: dict[str, Any] = {
        "findings_total": len(findings),
        "findings": findings[:MAX_SUMMARY_FINDINGS],
    }
    out.update(partial("findings",
                       truncated=len(findings) > MAX_SUMMARY_FINDINGS,
                       stopped=stopped))
    out["map_fences"] = built["map_fences"]
    out["note"] = built["note"]
    return out


@tool(
    "map_critique",
    "Analyze map design quality",
    "Design-quality checks that validate does not run. With no scopes it "
    "runs reachability, dead_ends and switch_usage and adds `summary`: "
    "every finding worth a look, ranked high / medium / low, each with a "
    "prompt_to_look -- a prompt, not a verdict -- at most %d listed beside "
    "findings_total. The summary flags maps with unusual dead space or low "
    "ground-tile variety by Tukey fences over this project's own non-blank "
    "maps (the stat_outliers method; fences under map_fences), with "
    "absolute floors beneath them: a map whose ground is exactly one tile "
    "id is flagged as uniform_ground (a map with no tile is dead space), "
    "and when "
    "most maps have no empty ground the dead-space fence falls back to "
    "the maps that do, or to 50%% empty when fewer than 8 do; the most "
    "extreme stat outliers, unreachable maps (blank folder maps marked "
    "apart), dead ends and gates nothing opens. The full tables of "
    "dead_space, tile_variety and stat_outliers are returned only when "
    "named in scopes. Scopes: dead_space "
    "(per map: width, height, fraction of ground-layer cells with tile id 0, "
    "and the bounding box of the occupied ground cells -- numbers, no "
    "verdict; a cell empty on layer 0 may still carry an upper-layer tile, "
    "a shadow or region id on layer 3, or an event; blank and "
    "nonzero_cells count tiles on layers 0-2 only, region_cells and "
    "shadow_cells count layer 3) and reachability, "
    "walked from the party's start position across every Transfer Player "
    "command in the project, reporting which maps on disk nothing leads to. "
    "A transfer whose destination is read from a game variable at runtime "
    "cannot be resolved statically and is counted separately rather than "
    "guessed at; Script commands calling reserve_transfer with a literal "
    "map id are followed by text pattern, not Ruby parsing (non-literal "
    "ones are counted as unresolved_script_transfers; script text is read "
    "up to 8192 bytes a line and 65536 characters a block, and a longer "
    "block is counted in script_blocks_clipped); "
    "a transfer inside a common event is treated as reachable "
    "from anywhere, since a common event can be called from any map or run "
    "on its own as a parallel process. dead_ends: reachable maps the "
    "party can enter and not leave -- no_exit (no outgoing transfer), "
    "closed_regions (maps that only lead to each other) and one_way (an "
    "exit exists but none leads back to the start). Findings are marked "
    "uncertain when a variable transfer or a common-event transfer exists, "
    "or when a script that may transfer the player (a non-literal "
    "reserve_transfer, eval/send, or a method the Scripts file defines "
    "around a transfer) can run on that map: on its events, in a common "
    "event they call, or in an autorun or parallel common event or troop "
    "page; may_exit_via names up to three. Vehicles are not "
    "modelled. tile_variety: Shannon entropy (bits) of tile ids per "
    "tile layer (0-2) per map, over non-empty cells, with a "
    "size-independent normalised evenness -- a descriptive figure for "
    "ordering maps, not a quality verdict (low entropy is not a bad map); "
    "VX Ace's layer 3 is not tiles and is counted by region id and shadow "
    "mask under regions and shadows. stat_outliers "
    "compares the balance-relevant numeric fields of each database table "
    "against that table's own distribution by Tukey's IQR fences, "
    "skipping fields with too few values or no spread; an outlier is a "
    "prompt to look, not a defect. switch_usage: the numbered game "
    "switches and variables that are read but never set (a gate that can "
    "never open) or set but never read (dead state), each with bounded "
    "where-found locations and a confidence that drops when a script "
    "uses a non-literal index; self switches are excluded."
    % MAX_SUMMARY_FINDINGS,
    schema({
        "scopes": prop(ARR, "Which checks to run (default "
                            + ", ".join(DEFAULT_MAP_CRITIQUE_SCOPES)
                            + "; the full tables are opt-in): "
                            + ", ".join(MAP_CRITIQUE_SCOPES),
                       items={"type": STR, "enum": MAP_CRITIQUE_SCOPES}),
        "refresh": REFRESH,
    }),
)
def map_critique(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Design-quality analyses over data already in memory. Reads only."""
    # Taken on the first call rather than at import: these are what the
    # server's start-up would otherwise load for every session.
    from ..analysis import (map_dead_ends, map_reachability, map_shapes,
                            map_switch_usage, map_tile_variety,
                            stat_outlier_report)

    scopes = scopes_arg(args, "scopes", MAP_CRITIQUE_SCOPES,
                        DEFAULT_MAP_CRITIQUE_SCOPES)
    if opt_bool(args, "refresh"):
        ctx.project.forget_cached()
    needed = set(scopes)
    if "summary" in needed:
        needed.update(_SUMMARY_INPUTS)
    # One allowance for every command walk this call makes, the rule
    # `WalkBudget` states and `validate` keeps for itself: however many
    # scopes are named, a crafted file buys one allowance's work per call.
    # A scope the shared allowance runs out under says so in its own
    # `_stopped` key, and the summary repeats it.
    budget = WalkBudget()
    results: dict[str, Any] = {}
    if "reachability" in needed:
        results["reachability"] = map_reachability(ctx.project, budget)
    if "dead_ends" in needed:
        results["dead_ends"] = map_dead_ends(ctx.project, budget)
    if "dead_space" in needed or "reachability" in needed:
        # reachability's blank-map annotation and dead_space read the same
        # per-map shape, so it is computed once.
        results["dead_space"] = map_shapes(ctx.project, budget)
        if "reachability" in needed:
            _annotate_blank(results["reachability"], results["dead_space"])
    if "tile_variety" in needed:
        results["tile_variety"] = map_tile_variety(ctx.project)
    if "stat_outliers" in needed:
        results["stat_outliers"] = stat_outlier_report(ctx.project)
    if "switch_usage" in needed:
        results["switch_usage"] = map_switch_usage(ctx.project, budget)
    reply: dict[str, Any] = {"checks_run": scopes}
    for scope in scopes:
        reply[scope] = (_summary(results) if scope == "summary"
                        else results[scope])
    return reply
