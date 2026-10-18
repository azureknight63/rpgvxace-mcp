"""`command_reference`: the command codes, and how to write them.

Its own module because it writes nothing and reads no project data
beyond the engine's name: it sat in `event_tools`, which is the event
write path, and a reader after the compiler or the ops walked past a
hundred lines of documentation tool to reach them. The two share only
`DSL_HELP`, which is in `commands` now, beside the compiler it
describes.
"""

from __future__ import annotations

from typing import Any

from ..commands import (
    DSL_HELP,
    MOVE_COMMAND_NAMES,
    XP_AUTHORING_NOTE,
    XP_MOVE_COMMAND_NAMES,
    command_name,
    command_table,
    continuation_codes,
    param_specs,
)
from .registry import (
    BOOL,
    Context,
    INT,
    STR,
    opt_bool,
    opt_int,
    opt_str,
    prop,
    refuse_inert,
    schema,
    tool,
)


@tool(
    "command_reference",
    "Look up event command codes",
    "The event command codes this project's engine dispatches, and how to "
    "write them with event_edit. Search by name or look up a single code to "
    "see its parameter layout. XP has a table of its own (its Wait is 106 and "
    "its Set Move Route 209). For an engine this server does not model (only "
    "reachable with --allow-other-engines) the codes and layouts reported are "
    "VX Ace's, which are not that engine's -- every reply says which it is in "
    "\"layouts_are_this_engines\", because a parameter layout believed to be "
    "confirmed is how a caller writes a command the interpreter misreads.",
    schema({
        "code": prop(INT, "Look up one command code."),
        "search": prop(STR, "Find commands whose name contains this text."),
        "move_commands": prop(BOOL, "List move route command codes instead."),
    }),
)
def command_reference(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """The command codes this project's engine dispatches. Reads only.

    `command_table` and `param_specs` fall back to VX Ace's tables for an
    engine this server does not model. That is right for reading -- an
    unmodelled engine is still mostly describable -- and wrong to present as
    confirmed, which is what this tool did: it never asked. So every reply now
    says whether the layouts really are this engine's, from the same property
    analysis.py and _create_map ask.
    """
    engine = ctx.project.engine
    specs = param_specs(engine)
    table = command_table(engine)
    # Whether the table and layouts are this engine's own -- true for XP,
    # whose are, though they are not the VX family's that
    # `command_layouts_modelled` asks about.
    modelled = engine == ctx.project.profile.key
    borrowed = None if modelled else (
        "This is a %s project, which this server does not model. The codes "
        "and parameter layouts below are VX Ace's and are not confirmed for "
        "this engine -- XP's Show Text, for one, keeps the message text where "
        "Ace keeps the face graphic. Read a command out of an event that "
        "already works (event_get with include_raw) before authoring one."
        % engine)

    if opt_bool(args, "move_commands"):
        refuse_inert(args, ("code", "search"),
                     "move_commands lists the move route codes, which are a "
                     "separate set from the command codes, so it",
                     remedy="Drop them, or ask without move_commands.")
        # Below `modelled`, not above it: this branch used to return first and
        # so was the one reply that omitted the key the description promises
        # every reply carries. A move route's codes are borrowed for an
        # unmodelled engine exactly as the command codes are.
        names = XP_MOVE_COMMAND_NAMES if engine == "xp" else MOVE_COMMAND_NAMES
        moves: dict[str, Any] = {
            "move_commands": [{"code": code, "name": name}
                              for code, name in sorted(names.items())],
            "layouts_are_this_engines": modelled,
            "note": "Move command codes are the ones Game_Character "
                    "dispatches, identical in VX and VX Ace; XP's 39 and 40 "
                    "toggle Always on Top instead of transparency. "
                    "event_edit's move_route form takes each step by name "
                    "(not on XP); see \"authoring\" in a reply without "
                    "move_commands.",
        }
        if borrowed:
            moves["engine_warning"] = borrowed
        return moves

    code = opt_int(args, "code")
    if code is not None:
        refuse_inert(args, ("search",),
                     "a lookup of one code answers for that code alone, so it",
                     remedy="Ask for one or the other.")
        entry: dict[str, Any] = {"code": code,
                                 "name": command_name(code, engine)}
        # A list, because both notes can apply at once: a code this engine
        # does not dispatch AND whose layout is unrecorded used to lose the
        # first message to the second, leaving the model hunting for an example
        # of a command its engine cannot run.
        notes: list[str] = []
        if code not in table:
            notes.append(
                "%s does not dispatch this code, so it should not appear in "
                "this project's events." % ctx.project.profile.label)
        if code in specs:
            entry["parameters"] = specs[code]
        else:
            entry["parameters"] = None
            notes.append("This command's parameter layout is not recorded here. "
                         "Read an event that already uses it (event_get with "
                         "include_raw) and copy the shape.")
        entry["layouts_are_this_engines"] = modelled
        if borrowed:
            notes.append(borrowed)
        if notes:
            # `layout_notes`, because `notes` was also a *sentence* in
            # `search`'s reply -- one key, two types, and a caller reading
            # either had to know which tool it came from. These are notes
            # about one command's parameter layout, so the key says so.
            entry["layout_notes"] = notes
        return entry

    needle = (opt_str(args, "search") or "").lower()
    continuing = continuation_codes(engine)
    rows = [
        {"code": code, "name": name,
         "parameters": specs.get(code),
         "continuation": code in continuing}
        for code, name in sorted(table.items())
        if not needle or needle in name.lower()
    ]
    return {
        "engine": ctx.project.profile.label,
        "layouts_are_this_engines": modelled,
        "commands": rows,
        # `search` filters these, so this is the number that matched and not
        # the size of the table. Same rename, same reason as `event_get` in `event_tools`.
        "matching_commands": len(rows),
        **({"engine_warning": borrowed} if borrowed else {}),
        **({"authoring_note": XP_AUTHORING_NOTE} if engine == "xp" else {}),
        "authoring": DSL_HELP,
        "indent_rule": "Commands inside a branch or loop sit one indent deeper. "
                       "event_edit handles indentation for if/loop/choices; if you "
                       "write branch commands with raw, set indent yourself.",
    }
