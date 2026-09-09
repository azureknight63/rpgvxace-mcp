"""Rules that walk a command list: indent, targets, labels, terminator.

Named `command_rules` rather than `commands` because `rpgvxace_mcp.commands`
is the command tables this module reads, and two files called `commands.py`
one import line apart is a mistake waiting to be made.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any

from ...commands import (
    AUDIO_COMMAND_KINDS,
    CODE_BLANK,
    CODE_JUMP_TO_LABEL,
    CODE_LABEL,
    CODE_SHOW_TEXT,
    TRANSFER_X,
    TRANSFER_Y,
    command_name,
)
from ...project import Project
from ...rubytypes import RubyObject
from ...schema import on_map
from ..references import (
    MAP_TARGET,
    REFERENCE_RULES,
    TABLE_NOUNS,
    _referenced_ids,
)
from ..walk import (
    Location,
    WalkBudget,
    _int,
    _seq,
    _text,
    iter_command_lists,
)
from .findings import (
    ERROR,
    Finding,
    WARNING,
    _Findings,
    _Tables,
    _missing,
)
from .assets import _AssetChecker
from .maps import _MapSizes


def check_commands(project: Project, map_ids: list[int], tables: _Tables,
                   budget: "WalkBudget | None" = None,
                   collected: "_Findings | None" = None,
                   checker: "_AssetChecker | None" = None) -> list[Finding]:
    """Every event command list: structure, and the ids the commands name.

    ``budget`` is shared with the asset walk, so one validate call spends one
    allowance across both -- see :class:`WalkBudget`.

    ``checker`` makes this the asset walk too. `validate` passes one when it
    was asked for both the events and the assets scope, which the default
    run is: the two rules that need a command list read a strict subset of
    what this loop already holds, and a second traversal is charged exactly
    what the first was.
    """
    findings = _Findings.given(collected)
    # A table absent from this mapping is one that could not be read, so
    # the rules comparing ids against it are skipped rather than answered
    # wrongly.
    existing = {name: ids for name in REFERENCE_RULES if name != MAP_TARGET
                for ids in [tables.ids(name)] if ids is not None}

    # Every map is *reachable*, because a transfer on map 1 can target any
    # map in the project -- but reachable is not the same as loaded. This was
    # a dict comprehension over `iter_maps(project)` with no ids, so it
    # parsed every Map### file even when the caller had narrowed the run, and
    # parsing the maps is most of what a validate costs: `validate
    # map_ids=[5] scopes=["events"]` measured at 74% of a whole-project run,
    # which is the argument buying nothing. `_MapSizes` parses a map the
    # first time a Transfer Player actually names it, so the check sees the
    # same answers and a narrow run stays narrow.
    map_sizes = _MapSizes(project)
    on_disk = set(project.map_ids_on_disk())
    check_parameters = project.command_layouts_modelled

    for location, commands in iter_command_lists(project, map_ids, budget):
        if findings.full:
            break
        # One per command list, because both are facts about the list rather
        # than about a command: the labels it declares, and the indent the
        # previous command left behind.
        labels: "set[str] | None" = None
        indent_previous = 0
        for index, command in enumerate(commands):
            if findings.full:
                # The ceiling is a collection ceiling, so it is checked here
                # rather than at the end: one page of a million Jump to Label
                # commands with no matching Label is a million findings.
                break
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", CODE_BLANK)
            params = _seq(command.get("parameters"))
            here = location.at(index)

            findings.extend(_check_indent(index, command,
                                          indent_previous, here))
            indent_previous = _int(command.get("indent")) or 0

            if not check_parameters:
                continue
            findings.extend(_check_references(code, params, existing,
                                              on_disk, map_sizes, here))
            if checker is not None:
                _command_assets(checker, code, params, here)
            if code == CODE_JUMP_TO_LABEL and params:
                if labels is None:
                    # Built on first use: most pages have no labels at all,
                    # and this is a second pass over the whole command list.
                    labels = _labels_in(project, commands)
                findings.extend(_check_jump(project, params, labels,
                                            here))

        findings.extend(check_list_terminator(location, commands))
    return findings.items


def _command_assets(checker: _AssetChecker, code: Any, params: list[Any],
                    here: Location) -> None:
    """The two asset references a command can carry.

    Here rather than in its own walk over the same lists. It was
    `_check_command_assets` in `assets.py`, iterating every command of every
    page a second time and charging the same shared allowance -- and the
    charge comes from the file rather than from the rule, so the second walk
    cost exactly what the first did. Command walking was twice its minimum
    on the default run, whatever the project.

    Everything it read -- the code, the parameters, the location -- the loop
    above already has in a local, so this costs nothing per command beyond
    the two tests. `checker.full` is `_AssetChecker`'s own business: it
    returns before it counts anything.
    """
    if code == CODE_SHOW_TEXT:
        if params:
            checker.check(params[0], "faces", here)
    elif _int(code) in AUDIO_COMMAND_KINDS:
        track = params[0] if params else None
        if isinstance(track, RubyObject):
            checker.check(track.get("name"), AUDIO_COMMAND_KINDS[code],
                          here, "missing_audio")


def _check_indent(index: int, command: RubyObject, previous: int,
                  here: Location) -> list[Finding]:
    """One rule: the branch structure the indents describe is continuous.

    Extracted with the two below because `check_commands` was three unrelated
    rules at four levels of nesting, so the loop read as one long procedure
    rather than as "for every command, apply these three rules".
    """
    indent = _int(command.get("indent")) or 0
    if indent <= previous + 1:
        return []
    return [Finding(
        WARNING, "indent_jump",
        "command %s jumps from indent %s to %s, which means the branch "
        "structure is inconsistent. Re-check the branches around it in the "
        "editor." % (index, previous, indent), here)]


def _check_references(code: Any, params: list[Any],
                      existing: "dict[str, AbstractSet[int]]",
                      on_disk: "set[int]", map_sizes: "_MapSizes",
                      here: Location) -> list[Finding]:
    """One rule: every id a command names is an id that exists.

    A table absent from ``existing`` could not be read, so its rules are
    skipped rather than answered wrongly -- which is a different outcome from
    "the id is missing" and is why the caller builds that mapping.
    """
    findings = []
    for table, value in _referenced_ids(code, params):
        if table == MAP_TARGET:
            findings += _check_transfer(value, params, on_disk, map_sizes,
                                        here)
        elif table in existing and value not in existing[table]:
            noun = TABLE_NOUNS[table]
            findings.append(_missing(
                "missing_%s" % noun.replace(" ", "_"),
                "%s refers to %s id %s" % (command_name(code), noun, value),
                "Point it at an existing entry, or add that entry to the "
                "database.", here,
                "command parameters against the %s table" % table))
    return findings


def _check_jump(project: Project, params: list[Any], labels: "set[str]",
                here: Location) -> list[Finding]:
    """One rule: a Jump to Label names a Label in the same command list."""
    target = _text(project, params[0])
    if target in labels:
        return []
    return [Finding(
        ERROR, "missing_label",
        'Jump to Label "%s" has no matching Label in this command list, so '
        "the jump does nothing. Add a Label command with that name, or "
        "remove the jump." % target, here)]


def _labels_in(project: Project, commands: list[Any]) -> set[str]:
    return {
        _text(project, command.get("parameters")[0])
        for command in commands
        if isinstance(command, RubyObject) and command.get("code") == CODE_LABEL
        and _seq(command.get("parameters"))
    }


def _check_transfer(map_id: int, params: list[Any], on_disk: AbstractSet[int],
                    map_sizes: "_MapSizes",
                    here: Location) -> list[Finding]:
    """Transfer Player: does the target map exist, and are the coordinates on it?

    Existence is decided by the files present, not by which maps happened to
    load: a map that exists but will not parse is a different problem, reported
    by ``check_maps``, and saying "does not exist" about it would send an agent
    recreating a file that is already there.
    """
    if map_id not in on_disk:
        return [_missing(
            "transfer_to_missing_map",
            "Transfer Player targets map %s" % map_id,
            "Point the transfer at an existing map.", here,
            "every Transfer Player against the map files present")]
    width, height = map_sizes.get(map_id)
    if width is None or height is None:
        return []       # unreadable or size-less; check_maps reports that
    if len(params) <= max(TRANSFER_X, TRANSFER_Y):
        return []
    x, y = _int(params[TRANSFER_X]), _int(params[TRANSFER_Y])
    if x is None or y is None:
        return []
    if not on_map(x, y, width, height):
        return [Finding(
            ERROR, "transfer_outside_map",
            "Transfer Player sends the party to (%s, %s) on map %s, which is "
            "only %sx%s, so the party would land off the map. Move the "
            "destination inside it." % (x, y, map_id, width, height), here,
            "Transfer Player coordinates against the target map's size")]
    return []


def check_list_terminator(location: Location, commands: list[Any]) -> list[Finding]:
    """Flag command lists whose final command is not the blank end marker.

    The interpreter reads a code-0 command as "this page is finished"; a list
    that ends on a real command runs off the end of the array instead. An
    entirely empty list is fine -- there is no marker to be missing, and the
    interpreter stops immediately.
    """
    if not commands:
        return []
    last = commands[-1]
    if isinstance(last, RubyObject) and last.get("code") != CODE_BLANK:
        return [Finding(
            WARNING, "missing_list_terminator",
            "this command list does not end with a blank command, which the "
            "interpreter uses to know the page is finished. Append an empty "
            "command (code 0) at indent 0.",
            location.at(len(commands) - 1))]
    return []
