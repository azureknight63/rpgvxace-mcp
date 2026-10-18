"""What in a project points at a database entry or a map.

The middle layer: it walks (from `walk`) and it is read by the delete paths
of `db_edit` and `map_edit`. Worth its own file because `find_references`
runs on a *write* path and its answer is one a caller acts on
destructively -- "nothing points at this" is exactly the answer that costs a
full traversal, so an unbounded walk here reads as "safe to delete".

`find_references_for` answers a whole batch from one pass, because asking
per id meant one traversal of the same unchanged command lists per delete.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..commands import (
    CODE_BLANK,
    CODE_CALL_COMMON_EVENT,
    CODE_TRANSFER_PLAYER,
    COND_ACTOR,
    COND_ARMOR,
    COND_ITEM,
    COND_WEAPON,
    command_name,
    summarize,
)
from ..project import Project
from ..rubytypes import RubyObject
from .walk import (
    WalkBudget,
    _int,
    _seq,
    iter_command_lists,
)


DEFAULT_REFERENCE_LIMIT = 200


# Not a database table. It is a key of REFERENCE_RULES below because
# Transfer Player names a map the same way the others name an entry, and the
# walk that checks one should check the other.
MAP_TARGET = "maps"


# Which command parameters hold an id from which table. ``requires`` pins other
# parameters that must match for the rule to apply (a Conditional Branch only
# refers to an item when its type parameter says so). Only layouts confirmed
# against the engines' own interpreters are listed, and they are the layouts VX
# Ace and VX share.
REFERENCE_RULES: dict[str, list[dict[str, Any]]] = {
    "items": [
        {"code": 126, "index": 0},
        {"code": 111, "index": 1, "requires": {0: COND_ITEM}},
        {"code": 302, "index": 1, "requires": {0: 0}},
        {"code": 605, "index": 1, "requires": {0: 0}},
    ],
    "weapons": [
        {"code": 127, "index": 0},
        {"code": 111, "index": 1, "requires": {0: COND_WEAPON}},
        {"code": 302, "index": 1, "requires": {0: 1}},
        {"code": 605, "index": 1, "requires": {0: 1}},
    ],
    "armors": [
        {"code": 128, "index": 0},
        {"code": 111, "index": 1, "requires": {0: COND_ARMOR}},
        {"code": 302, "index": 1, "requires": {0: 2}},
        {"code": 605, "index": 1, "requires": {0: 2}},
    ],
    "common_events": [{"code": CODE_CALL_COMMON_EVENT, "index": 0}],
    "troops": [{"code": 301, "index": 1, "requires": {0: 0}}],
    "animations": [{"code": 212, "index": 1}, {"code": 337, "index": 1}],
    "actors": [
        {"code": 129, "index": 0},
        {"code": 303, "index": 0},
        {"code": 320, "index": 0},
        {"code": 321, "index": 0},
        {"code": 322, "index": 0},
        {"code": 111, "index": 1, "requires": {0: COND_ACTOR}},
    ],
    MAP_TARGET: [{"code": CODE_TRANSFER_PLAYER, "index": 1, "requires": {0: 0}}],
}


# What one row of each table is called, for messages and rule ids. Derived from
# the table name by hand because English is not a rule: stripping an "s" turns
# "classes" into "classe".
TABLE_NOUNS = {
    "items": "item",
    "weapons": "weapon",
    "armors": "armor",
    "troops": "troop",
    "common_events": "common event",
    "actors": "actor",
    "animations": "animation",
    "classes": "class",
    "skills": "skill",
    "enemies": "enemy",
    "tilesets": "tileset",
}


# XP's rules: the VX family's, where XP's interpreter reads the same
# parameter, and XP's own where it does not. XP's command_301 takes the troop
# from parameters[0] with no designation before it, its Show Animation is
# 207 rather than 212, and its Show Battle Animation (337) keeps the
# animation in parameters[2], after a battler kind and an index. Every other
# rule above reads the same position in XP's Interpreter (126-129, 111's
# item, weapon, armor and actor types, 117, 201, 302/605, 303 and 320-322).
XP_REFERENCE_RULES: dict[str, list[dict[str, Any]]] = {
    **REFERENCE_RULES,
    "troops": [{"code": 301, "index": 0}],
    "animations": [{"code": 207, "index": 1}, {"code": 337, "index": 2}],
}


class _RuleIndex:
    """One engine's rules, keyed the three ways the walks ask them.

    * ``by_code`` -- the rules keyed by the command code they apply to. Most
      commands match nothing, and a project has tens of thousands of them,
      so the walk asks this once per command instead of trying every rule.
    * ``codes`` -- the codes alone, for the question asked most often: does
      this command refer to anything at all? Sixteen codes carry a reference
      and the other eighty-odd do not -- 401 (a line of text), 0 (the blank
      end marker), 108 (a comment), 355 and 655 (script lines) are most of a
      real project. Asking the mapping instead builds a `dict.get` result, a
      generator frame and a `set` per command and discards all three empty,
      which is a cost paid on every delete preview: `find_references_for`'s
      ordinary answer, "nothing points at this", is the one that costs a
      full traversal.
    * ``by_table_and_code`` -- keyed by (table, code), for the caller that
      wants one table. `_referenced_ids` yields for every table a code
      touches and the caller filtered with `found == table`, so a
      Conditional Branch -- code 111, one of the commonest commands in real
      event data -- ran `_requires_met` four times (items, weapons, armors,
      actors) to answer about one.

    These were three module-level tables built from the VX family's rules;
    XP needs the same three built from its own.
    """

    __slots__ = ("by_code", "codes", "by_table_and_code")

    def __init__(self, rules: dict[str, list[dict[str, Any]]]):
        self.by_code: dict[int, list[tuple[str, dict[str, Any]]]] = {}
        self.by_table_and_code: dict[tuple[str, int],
                                     list[dict[str, Any]]] = {}
        for table, table_rules in rules.items():
            for rule in table_rules:
                self.by_code.setdefault(rule["code"], []).append(
                    (table, rule))
                self.by_table_and_code.setdefault(
                    (table, rule["code"]), []).append(rule)
        self.codes = frozenset(self.by_code)


_VX_FAMILY_INDEX = _RuleIndex(REFERENCE_RULES)
_ENGINE_INDEXES = {"xp": _RuleIndex(XP_REFERENCE_RULES)}

# The VX family's codes, under the name the linter imports.
_REFERENCE_CODES = _VX_FAMILY_INDEX.codes


def rule_index(engine: str) -> _RuleIndex:
    """The reference rules of one engine: XP's, or the VX family's."""
    return _ENGINE_INDEXES.get(engine, _VX_FAMILY_INDEX)


def _requires_met(params: list[Any], requires: dict[int, Any] | None) -> bool:
    if not requires:
        return True
    for position, expected in requires.items():
        position = int(position)
        if len(params) <= position:
            return False
        given = params[position]
        # `!=` alone would let False satisfy a requirement of 0, since Python
        # counts them equal; a command's designation is an integer.
        if isinstance(given, bool) != isinstance(expected, bool) \
                or given != expected:
            return False
    return True


def _referenced_ids(code: Any, params: list[Any],
                    index: _RuleIndex = _VX_FAMILY_INDEX
                    ) -> Iterator[tuple[str, int]]:
    """Every ``(table, id)`` one command refers to.

    The single place the rules table is interpreted. Two copies of this walk
    used to exist and had already drifted apart on whether a non-integer
    parameter counted as a reference.
    """
    code = _int(code)
    if code is None:
        return
    for table, rule in index.by_code.get(code, ()):
        yield from _from_rule(table, rule, params)


def _ids_in(table: str, code: Any, params: list[Any],
            index: _RuleIndex = _VX_FAMILY_INDEX) -> Iterator[int]:
    """Every id one command refers to *in one table*.

    `_referenced_ids` with the table asked for rather than filtered
    afterwards: a caller that wants one table used to run every rule the
    code has and discard the answers for the other three.
    """
    code = _int(code)
    if code is None:
        return
    for rule in index.by_table_and_code.get((table, code), ()):
        for _, value in _from_rule(table, rule, params):
            yield value


def _from_rule(table: str, rule: dict[str, Any],
               params: list[Any]) -> Iterator[tuple[str, int]]:
    """One rule against one command's parameters. The rule, interpreted."""
    if not _requires_met(params, rule.get("requires")):
        return
    position = rule["index"]
    if position >= len(params):
        return
    value = _int(params[position])
    if value is not None:
        yield table, value


# How many of the references to a thing being deleted come back. Enough to see
# the shape of the problem without turning a delete into a report. Defined here
# rather than in each tool, because both callers bound the same promise against
# this same function and had written the rationale out twice.
#
# A caller wanting to know whether there are *more* than this asks for one
# past it: find_references stops as soon as it has what it was asked for, so
# a full page and a page that is exactly full look identical otherwise.
REFERENCE_PREVIEW = 25


class ReferencePreview:
    """What still points at a thing about to be deleted, and how sure we are.

    Three facts, because a delete needs all three and two of them used to be
    derived separately in each caller:

    * ``rows`` -- up to REFERENCE_PREVIEW hits, enough to see the shape of the
      problem without turning a delete into a report.
    * ``truncated`` -- whether there are more than those. Found by asking for
      one past the limit, which db_edit did and map_edit did not, so a map
      referenced three hundred times was reported as referenced twenty-five
      and a caller who fixed those twenty-five believed the map was free.
    * ``incomplete`` -- whether the walk ran out before finishing. `None` when
      it finished. This is the one a caller acts on destructively: an empty
      ``rows`` from an unfinished scan reads as "nothing points at it".
    """

    __slots__ = ("rows", "truncated", "incomplete")

    def __init__(self, rows: list[dict[str, Any]], truncated: bool,
                 incomplete: "str | None"):
        self.rows = rows
        self.truncated = truncated
        self.incomplete = incomplete

    @property
    def counted(self) -> str:
        """The count as a phrase, hedged when there are more than shown."""
        return "%s%d" % ("at least " if self.truncated else "", len(self.rows))

    def as_reply(self, prefix: str, rows: "list[Any] | None" = None
                 ) -> dict[str, Any]:
        """The three facts as reply keys, spelled one way.

        This class unified how the facts are *computed* and left how they are
        *said* to each caller, and the two delete paths had already diverged:
        db_edit put whole hit dicts under `references` and mirrored the
        incompleteness into a warning, while map_edit put only `hit["where"]`
        strings under `incoming_transfers` and reported it as a field. The
        shape was written three times.

        ``rows`` lets a caller substitute its own rendering of the hits --
        map_edit wants the locations and not the whole hit -- without also
        re-deciding what the other two keys are called or when they appear.
        The scan-stopped key is present only when it happened, because a key
        that is there and null is one more thing to interpret.
        """
        reply: dict[str, Any] = {
            prefix: self.rows if rows is None else rows,
            "%s_truncated" % prefix: self.truncated,
        }
        if self.incomplete:
            reply["%s_scan_stopped" % prefix] = self.incomplete
        return reply


def reference_preview(project: Project, table: str, entry_id: int,
                      what: str,
                      budget: "WalkBudget | None" = None) -> ReferencePreview:
    """Ask what points at ``entry_id``, bounded, with the answer's own limits.

    ``what`` names the scan in the note a stopped walk produces, so the
    caller's reply says which scan gave up.

    One function because both delete paths need exactly this and each of its
    steps has been got wrong in one copy and not the other. See
    `find_references` for why the budget is not optional.
    """
    # The caller's allowance when it has one, so a db_edit carrying two
    # hundred deletes gets one walk allowance and not two hundred. `validate`
    # states the rule for itself -- "One allowance for every command walk this
    # call makes" -- and this is the same rule one tool over.
    budget = WalkBudget() if budget is None else budget
    # One past the preview, so a full page can be told from a page that
    # happens to be exactly full.
    found = find_references(project, table, entry_id, budget,
                            limit=REFERENCE_PREVIEW + 1)
    return _as_preview(found, budget.note(what))


def reference_previews(project: Project, table: str, entry_ids: "set[int]",
                       what: str, budget: "WalkBudget | None" = None
                       ) -> dict[int, ReferencePreview]:
    """:func:`reference_preview` for a batch, from one pass over the project.

    Same three facts per id, same "one past the limit" arithmetic, and the
    same note when the walk stopped -- the point is that a batch pays for one
    traversal rather than one per id. Called before any op is dispatched, so
    every delete in the batch is answered from the project as it was when the
    call arrived, which is also the answer each op would have got on its own.
    """
    budget = WalkBudget() if budget is None else budget
    found = find_references_for(project, table, entry_ids, budget,
                                limit=REFERENCE_PREVIEW + 1)
    note = budget.note(what)
    return {entry_id: _as_preview(rows, note)
            for entry_id, rows in found.items()}


def _as_preview(found: list[dict[str, Any]],
                note: "str | None") -> ReferencePreview:
    """The three facts from one id's hits, so both spellings agree."""
    return ReferencePreview(found[:REFERENCE_PREVIEW],
                            len(found) > REFERENCE_PREVIEW, note)


def find_references_for(project: Project, table: str, entry_ids: "set[int]",
                        budget: WalkBudget,
                        limit: int = DEFAULT_REFERENCE_LIMIT
                        ) -> dict[int, list[dict[str, Any]]]:
    """What points at each of ``entry_ids``, from one pass over the project.

    `find_references` answers for one id, and answering for twenty meant
    twenty passes over the same unchanged command lists. It returns early
    only on reaching ``limit`` hits, so the ordinary case -- deleting an
    entry nothing points at -- is the one that costs a full traversal of
    every map event page, every common event and every troop page. Measured
    on the developer's own 39-map project: a `db_edit` carrying twenty
    deletes performed about thirteen whole-project walks against its shared
    allowance and then reported `references_scan_stopped` for the remaining
    seven, so the caller was both waited on and told the zeroes were not
    answers. At MAX_OPS almost the whole batch came back degraded.

    One walk answers all of them, and the shared allowance then covers the
    batch instead of a fraction of it. Each id gets its own ``limit``,
    because each is reported separately; the walk stops early only when
    every id has reached it.
    """
    hits: dict[int, list[dict[str, Any]]] = {i: [] for i in entry_ids}
    if table not in REFERENCE_RULES or not entry_ids:
        return hits
    rules = rule_index(project.engine)
    wanted = set(entry_ids)
    for location, commands in iter_command_lists(project, None, budget):
        for index, command in enumerate(commands):
            if not isinstance(command, RubyObject):
                continue
            code = command.get("code", CODE_BLANK)
            # Before the parameters are read at all. Reading them is an ivar
            # lookup that builds an "@parameters" key string, and the set
            # comprehension below allocates a generator frame and a set --
            # three objects per command, for the eighty-odd codes out of a
            # hundred that can refer to nothing.
            if _int(code) not in rules.codes:
                continue
            params = _seq(command.get("parameters"))
            named = {value for value in _ids_in(table, code, params, rules)
                     if value in wanted}
            if not named:
                continue
            # Built once per matching command, not once per id: rendering a
            # command is the expensive half, and one command can name the
            # same entry in more than one parameter.
            located = location.at(index)
            row = {
                "where": located.describe(),
                "location": located.as_dict(),
                "command": command_name(code, project.engine),
                "summary": summarize(command, project.codec, project.engine),
            }
            for entry_id in named:
                rows = hits[entry_id]
                if len(rows) < limit:
                    rows.append(row)
                    if len(rows) >= limit:
                        wanted.discard(entry_id)
            if not wanted:
                return hits
    return hits


def find_references(project: Project, table: str, entry_id: int,
                    budget: WalkBudget,
                    limit: int = DEFAULT_REFERENCE_LIMIT
                    ) -> list[dict[str, Any]]:
    """Find event commands that mention ``entry_id`` from ``table``.

    ``budget`` is required, and is the caller's to read afterwards. This runs
    on a *write* path -- both `db_edit op="delete"` and `map_edit op="delete"`
    ask it what still points at the thing about to go -- and it is bounded
    only when the answer is "yes, lots": it returns as soon as it has ``limit``
    hits, so the case that walks every command in the project is the case that
    answers "nothing points at it". A crafted file makes that five million
    visits, and the caller then reports zero references with the scan marked
    as done, which reads as "safe to delete".

    Required rather than defaulted because there is no safe default here: a
    caller that forgets gets the unbounded walk back, and this is the one
    walk whose answer a caller acts on destructively.

    The batch form with one id, not a second body. It was both -- the same
    walk, the same isinstance skip, and the hit row built twice fifty lines
    apart -- in the module whose own comment warns that "two copies of this
    walk used to exist and had already drifted apart on whether a
    non-integer parameter counted as a reference". The early exit is
    identical: `wanted.discard` empties the set at `limit` and the walk
    returns on the next command.
    """
    return find_references_for(project, table, {entry_id}, budget,
                               limit)[entry_id]
