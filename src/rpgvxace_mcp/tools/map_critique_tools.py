"""map_critique: design-quality analysis, as distinct from validate's correctness.

`validate` catches broken references and event-logic traps -- a transfer to
a map that does not exist, an autorun page that can never stop. Those are
questions of whether the data is *sound*. This tool starts on a different
kind of question, whether the data is *any good*: can the player ever reach
this map at all.

Reachability is the first of several design-quality checks proposed for
this tool (see the project's issue tracker for dead space, tile variety and
stat-outlier analyses still to come); it is the one slice that is pure graph
traversal over data this server already parses, which is why it comes
first. `scopes` exists from the start so a caller narrows a request the same
way `validate` already lets it, and so a later scope can be added without
either tool learning a new shape.
"""

from __future__ import annotations

from typing import Any

from ..analysis import WalkBudget, map_reachability
from .registry import (
    ARR,
    Context,
    REFRESH,
    STR,
    opt_bool,
    prop,
    schema,
    scopes_arg,
    tool,
)


MAP_CRITIQUE_SCOPES = ("reachability",)


@tool(
    "map_critique",
    "Analyze map design quality",
    "Design-quality checks that validate does not run. Today: reachability, "
    "walked from the party's start position across every Transfer Player "
    "command in the project, reporting which maps on disk nothing leads to. "
    "A transfer whose destination is read from a game variable at runtime "
    "cannot be resolved statically and is counted separately rather than "
    "guessed at; a transfer inside a common event is treated as reachable "
    "from anywhere, since a common event can be called from any map or run "
    "on its own as a parallel process.",
    schema({
        "scopes": prop(ARR, "Which checks to run (default all): "
                            + ", ".join(MAP_CRITIQUE_SCOPES),
                       items={"type": STR, "enum": MAP_CRITIQUE_SCOPES}),
        "refresh": REFRESH,
    }),
)
def map_critique(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Design-quality analyses over data already in memory. Reads only."""
    scopes = scopes_arg(args, "scopes", MAP_CRITIQUE_SCOPES)
    if opt_bool(args, "refresh"):
        ctx.project.forget_cached()
    reply: dict[str, Any] = {"checks_run": scopes}
    if "reachability" in scopes:
        # One allowance, shared with whatever other command walk this call
        # makes -- there is only the one today, but `validate` states the
        # same rule for itself and this is the same walk one tool over.
        reply["reachability"] = map_reachability(ctx.project, WalkBudget())
    return reply
