"""Rules that walk a command list: indent, targets, labels, terminator.

Named `command_rules` rather than `commands` because `rpgvxace_mcp.commands`
is the command tables this module reads, and two files called `commands.py`
one import line apart is a mistake waiting to be made.
"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any

from ...commands import (
    CODE_BLANK,
    CODE_JUMP_TO_LABEL,
    CODE_LABEL,
    TRANSFER_X,
    TRANSFER_Y,
    command_name,
)
from ...project import Project
from ...rubytypes import RubyObject
from ...schema import on_map
from ..references import (
    MAP_TARGET,
    TABLE_NOUNS,
    _REFERENCE_CODES,
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
from .assets import (
    _AssetChecker,
    command_assets,
)
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
    on_disk = project.map_ids_present()
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
            # The location is *not* built here. It used to be, on every
            # command walked, and it is read only inside a `Finding(...)`:
            # on the developer's real project that was 9,819 copies for two
            # findings. Each rule takes the list's location and the index
            # and calls `.at()` where it reports, so the cost now scales
            # with what is wrong rather than with what is read.
            indent = _int(command.get("indent")) or 0

            findings.extend(_check_indent(index, indent_previous,
                                          location, indent))
            indent_previous = indent

            if not check_parameters:
                continue
            # `_int(code)` once for the two rules below, and the reference
            # rules skipped outright for a command that can name nothing.
            # Sixteen codes carry a reference; on the developer's project
            # 9,700 of 9,819 commands do not, and each was paying a list
            # allocation, a generator frame and a dict miss. The pre-check
            # went into `find_references_for` last round and not into this
            # walk, which reads the same commands.
            code_int = _int(code)
            if code_int in _REFERENCE_CODES:
                findings.extend(_check_references(
                    code, params, tables, on_disk, map_sizes, location,
                    index))
            if checker is not None:
                command_assets(checker, code_int, code, params,
                               location, index)
            if code == CODE_JUMP_TO_LABEL and params:
                if labels is None:
                    # Built on first use: most pages have no labels at all,
                    # and this is a second pass over the whole command list.
                    labels = _labels_in(project, commands)
                findings.extend(_check_jump(project, params, labels,
                                            location, index))

        findings.extend(check_list_terminator(location, commands))
    return findings.items


def _check_indent(index: int, previous: int,
                  location: Location, indent: int) -> list[Finding]:
    """One rule: the branch structure the indents describe is continuous.

    Extracted with the two below because `check_commands` was three unrelated
    rules at four levels of nesting, so the loop read as one long procedure
    rather than as "for every command, apply these three rules".

    Takes the list's location and copies it at `index` only when it reports,
    and takes `indent` rather than reading it again: the caller needs it for
    the next command anyway, and this read it a second time from the same
    object.
    """
    if indent <= previous + 1:
        return []
    return [Finding(
        WARNING, "indent_jump",
        "command %s jumps from indent %s to %s, which means the branch "
        "structure is inconsistent. Re-check the branches around it in the "
        "editor." % (index, previous, indent), location.at(index))]


def _check_references(code: Any, params: list[Any], tables: _Tables,
                      on_disk: AbstractSet[int], map_sizes: "_MapSizes",
                      location: Location, index: int) -> list[Finding]:
    """One rule: every id a command names is an id that exists.

    `_Tables.ids` answers None for a table that could not be read, so that
    table's rules are skipped rather than answered wrongly -- a different
    outcome from "the id is missing". Asked per reference, which is why
    there is no mapping built here; the comment below says what that
    replaced.
    """
    findings = []
    for table, value in _referenced_ids(code, params):
        if table == MAP_TARGET:
            findings += _check_transfer(value, params, on_disk, map_sizes,
                                        location, index)
        else:
            # Asked per reference, not gathered up front. The caller used
            # to force `tables.ids()` for all seven referenced tables
            # before reading a command, so a run scoped to one map still
            # decoded Items, Weapons, Armors, Animations and Actors whether
            # or not anything named them -- the database twin of the
            # `_MapSizes` defect its own comment records at 74% of a
            # whole-project run. `_Tables.ids` memoises both the rows and
            # the id set, and None still means "could not be read", which
            # is skipped rather than answered wrongly.
            ids = tables.ids(table)
            if ids is None or value in ids:
                continue
            noun = TABLE_NOUNS[table]
            findings.append(_missing(
                "missing_%s" % noun.replace(" ", "_"),
                "%s refers to %s id %s" % (command_name(code), noun, value),
                "Point it at an existing entry, or add that entry to the "
                "database.", location.at(index),
                "command parameters against the %s table" % table))
    return findings


def _check_jump(project: Project, params: list[Any], labels: "set[str]",
                location: Location, index: int) -> list[Finding]:
    """One rule: a Jump to Label names a Label in the same command list."""
    target = _text(project, params[0])
    if target in labels:
        return []
    return [Finding(
        ERROR, "missing_label",
        'Jump to Label "%s" has no matching Label in this command list, so '
        "the jump does nothing. Add a Label command with that name, or "
        "remove the jump." % target, location.at(index))]


def _labels_in(project: Project, commands: list[Any]) -> set[str]:
    return {
        _text(project, command.get("parameters")[0])
        for command in commands
        if isinstance(command, RubyObject) and command.get("code") == CODE_LABEL
        and _seq(command.get("parameters"))
    }


def _check_transfer(map_id: int, params: list[Any], on_disk: AbstractSet[int],
                    map_sizes: "_MapSizes",
                    location: Location, index: int) -> list[Finding]:
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
            "Point the transfer at an existing map.", location.at(index),
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
            "destination inside it."
            % (x, y, map_id, width, height), location.at(index),
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
