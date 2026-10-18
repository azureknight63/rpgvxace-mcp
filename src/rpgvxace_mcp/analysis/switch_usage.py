"""Game switches and variables that are read and never set, or set and never read.

The classic reason a gated door or event can never fire is a page condition
on a switch nothing in the project ever turns on. The mirror image is dead
state: a switch or variable that is written and that nothing consults. Both
are questions about *numbered* game switches and game variables, and both
are answered here from the same command walk the other analyses share.

What counts as a read
    * an event page's switch 1, switch 2 and variable conditions;
    * a Conditional Branch (111) on a switch or a variable, and a variable
      compared *against* another variable;
    * Control Variables (122) whose operand is another variable;
    * a troop battle page's switch condition;
    * a common event's trigger switch (autorun and parallel only);
    * an enemy action pattern conditioned on a switch;
    * the commands that can take a variable in place of a constant
      (`_VARIABLE_OPERAND_READS`) and `\\V[n]` in message text;
    * a literal `$game_switches[N]` / `$game_variables[N]` read in a
      script, and `v[N]` in a damage formula.

What counts as a write
    * Control Switches (121) and Control Variables (122), over their whole
      id range -- a range is kept as an interval and never expanded, so
      `1..2000000000` costs the same as `1..2`;
    * Input Number (103), Select Key Item (104) and Get Location Info (285),
      which store their result in a variable;
    * a Switch ON / OFF step in a Set Move Route command or in a page's own
      autonomous route;
    * a literal `$game_switches[N] = ...` or `$game_variables[N] += ...`
      in a script.

Self switches are a separate namespace (`(map, event, A..D)`, not a number)
and are *excluded*: they are counted under `self_switches` and take no part
in either finding. Reading one in the middle of a numbered-switch report
would be a claim about a different object.

Honesty about scripts
    A script can touch any switch. Literal indices are recognised by pattern;
    anything else -- `$game_switches[Config::DOOR]`, `$game_switches[i]`, an
    alias such as `s = $game_switches` -- is counted as `script_access_unknown`
    and downgrades the confidence of every claim it could invalidate: a
    non-literal *write* makes every "never set" claim low confidence, a
    non-literal *read* makes every "never read" claim low confidence. The
    scan is by line and by regular expression, not a Ruby parser, so an
    access hidden behind `send`, `eval` or another variable is invisible, and
    that is stated in the report's notes rather than implied away. Dynamic
    accesses inside the stock engine scripts (`Game_Interpreter`,
    `Game_Event` and so on, which index by parameter) are not counted -- they
    would make every project low confidence -- but literal accesses in them
    are.

Initial values are false and zero, so "never set" means "constant false/0
for the whole game", and each read says whether its own condition holds
under that constant (`holds_while_unset`).
"""

from __future__ import annotations

import bisect
import re
from typing import Any

from ..commands import (
    CODE_BLANK,
    CODE_CONDITIONAL_BRANCH,
    CODE_CONTROL_SELF_SWITCH,
    CODE_CONTROL_SWITCHES,
    CODE_CONTROL_VARIABLES,
    CODE_SCRIPT,
    CODE_SCRIPT_MORE,
    COMPARISONS,
    COND_SCRIPT,
    COND_SELF_SWITCH,
    COND_SWITCH,
    COND_VARIABLE,
    OPERAND_VARIABLE,
    route_codes,
)
from ..project import Project, ProjectError
from ..rubytypes import RubyHash, RubyObject, RubyString
from .walk import (
    Location,
    WalkBudget,
    _event_at,
    _int,
    _safe_entries,
    _seq,
    _text,
    iter_command_lists,
    iter_maps,
)


_SWITCHES = "switches"
_VARIABLES = "variables"

# Findings listed per category, and locations listed per finding. The total
# is always given beside them: a claim needs its count, not all its evidence.
_MAX_SHOWN = 25
_MAX_LOCATIONS = 3

# VX and VX Ace both ignore a write to an id above this (`Game_Switches#[]=`
# and `Game_Variables#[]=` guard on it), so an interval is clamped to it.
# That is also what makes a crafted `1..2000000000` range cheap to count.
_MAX_ID = 5000

# A script line this long is not scanned in full. It is counted as an
# opaque access of both kinds rather than guessed at.
_MAX_SCRIPT_LINE = 2000


_CODE_INPUT_NUMBER = 103
_CODE_SELECT_KEY_ITEM = 104
_CODE_GET_LOCATION_INFO = 285
_CODE_SHOW_CHOICES = 102
_CODE_SHOW_TEXT = 101
_CODE_SCROLL_TEXT_LINE = 405
_CODE_TEXT_LINE = 401
_MOVE_SWITCH_ON, _MOVE_SWITCH_OFF = 27, 28
_MOVE_SCRIPT = 45
_ENEMY_ACTION_SWITCH = 6
# Control Variables operand type "script" (VX Ace); in VX the same slot is
# an integer, which is why the scan also requires a string.
_OPERAND_SCRIPT = 4

# Commands that take a variable in place of a constant: (code, index of the
# selector parameter, the selector value meaning "from variables", indices
# of the parameters that then hold variable ids). Only layouts read from the
# engines' own interpreters (`operate_value`, `iterate_actor_var`,
# `command_201`, `command_231`, ...); a command left out is a read this
# analysis cannot see, which the report's notes say.
_VARIABLE_OPERAND_READS = (
    (125, 1, 1, (2,)),            # Change Gold
    (126, 2, 1, (3,)),            # Change Items
    (127, 2, 1, (3,)),            # Change Weapons
    (128, 2, 1, (3,)),            # Change Armor
    (201, 0, 1, (1, 2, 3)),       # Transfer Player
    (203, 1, 1, (2, 3)),          # Set Event Location
    (231, 3, 1, (4, 5)),          # Show Picture
    (232, 3, 1, (4, 5)),          # Move Picture
    (301, 0, 1, (1,)),            # Battle Processing
    (311, 3, 1, (4,)),            # Change HP
    (312, 3, 1, (4,)),            # Change MP
    (315, 3, 1, (4,)),            # Change EXP
    (316, 3, 1, (4,)),            # Change Level
    (317, 4, 1, (5,)),            # Change Parameters
    (331, 2, 1, (3,)),            # Change Enemy HP
    (332, 2, 1, (3,)),            # Change Enemy MP
)
_OPERAND_READS_BY_CODE: dict[int, list[tuple[int, int, tuple[int, ...]]]] = {}
for _code, _selector, _value, _positions in _VARIABLE_OPERAND_READS:
    _OPERAND_READS_BY_CODE.setdefault(_code, []).append(
        (_selector, _value, _positions))


# XP's, from its own Interpreter: Set Event Location is 202 (XP's 203
# scrolls the map), Battle Processing takes no troop from a variable, and
# XP's Change HP, SP, EXP and Level call `operate_value(@parameters[1],
# @parameters[2], @parameters[3])` and Change Parameters
# `operate_value(@parameters[2], ...)` -- one place earlier than VX's.
_XP_VARIABLE_OPERAND_READS = (
    *(row for row in _VARIABLE_OPERAND_READS
      if row[0] in (125, 126, 127, 128, 201, 231, 232, 331, 332)),
    (202, 1, 1, (2, 3)),          # Set Event Location
    (311, 2, 1, (3,)),            # Change HP
    (312, 2, 1, (3,)),            # Change SP
    (315, 2, 1, (3,)),            # Change EXP
    (316, 2, 1, (3,)),            # Change Level
    (317, 3, 1, (4,)),            # Change Parameters
)
_XP_OPERAND_READS_BY_CODE: dict[int, list[tuple[int, int, tuple[int, ...]]]] = {}
for _code, _selector, _value, _positions in _XP_VARIABLE_OPERAND_READS:
    _XP_OPERAND_READS_BY_CODE.setdefault(_code, []).append(
        (_selector, _value, _positions))

# The commands whose result is written to the variable in parameters[0]:
# Input Number on both families, and VX Ace's Select Key Item and Get
# Location Info. XP's 104 is Change Text Options and writes no variable.
_RESULT_CODES = frozenset({_CODE_INPUT_NUMBER, _CODE_SELECT_KEY_ITEM,
                           _CODE_GET_LOCATION_INFO})
_XP_RESULT_CODES = frozenset({_CODE_INPUT_NUMBER})

# The default Script Editor slots. A dynamic `$game_switches[param]` in one
# of these is the engine doing its job, not a project script setting a switch
# this analysis cannot name; see the module docstring.
_STOCK_SCRIPT_SLOTS = frozenset({
    "Game_Temp", "Game_System", "Game_Timer", "Game_Message", "Game_Switches",
    "Game_Variables", "Game_SelfSwitches", "Game_Screen", "Game_Picture",
    "Game_Pictures", "Game_BaseItem", "Game_Action", "Game_ActionResult",
    "Game_BattlerBase", "Game_Battler", "Game_Actor", "Game_Enemy",
    "Game_Actors", "Game_Unit", "Game_Party", "Game_Troop", "Game_Map",
    "Game_CommonEvent", "Game_CharacterBase", "Game_Character",
    "Game_Player", "Game_Follower", "Game_Followers", "Game_Vehicle",
    "Game_Event", "Game_Interpreter", "Scene_Map", "Scene_Battle",
    "Scene_Title", "Scene_Base", "Scene_File", "Scene_Save", "Scene_Load",
    "Scene_Menu", "Scene_Gameover", "Scene_Debug", "DataManager",
    "BattleManager", "SceneManager", "Window_Selectable",
})

# A slot named like a stock engine class (`Window_NumberInput`,
# `Scene_Map`): the default library is ~100 such slots and the exact list
# above is only the ones known to touch switches. A project script given a
# stock-style name is treated as stock for *dynamic* accesses only -- its
# literal ones still count -- and the count of ignored accesses is reported.
_STOCK_NAME = re.compile(r"^(?:Game|Window|Scene|Sprite|Spriteset)_\w+$")


_ACCESS = re.compile(
    r"\$game_(switches|variables)\b"
    r"(?:\s*\[([^\]\n]{0,64})\]\s*"
    r"((?:\|\||&&|<<|>>|\*\*|[-+*/%|&^])?=(?![=~>]))?)?")
_FORMULA_VARIABLE = re.compile(r"(?<![\w$@.])v\s*\[([^\]\n]{0,64})\]")
_TEXT_VARIABLE = re.compile(r"\\[Vv]\[(\d+)\]")
_LITERAL = re.compile(r"^\s*(\d+)\s*$")


class _Sightings:
    """How often one id was seen, and the first few places."""

    __slots__ = ("count", "rows")

    def __init__(self) -> None:
        self.count = 0
        self.rows: list[dict[str, Any]] = []

    def add(self, row: dict[str, Any]) -> None:
        self.count += 1
        if len(self.rows) < _MAX_LOCATIONS:
            self.rows.append(row)


class _Tally:
    """Everything seen about one namespace (switches or variables)."""

    def __init__(self) -> None:
        self.reads: dict[int, _Sightings] = {}
        self.single_writes: dict[int, _Sightings] = {}
        # (lo, hi, where, how) for every write that names more than one id.
        self.range_writes: list[tuple[int, int, str, str]] = []
        self.dynamic_reads = 0
        self.dynamic_writes = 0
        self.opaque = 0
        self.stock_dynamic = 0
        self.unknown_where: list[str] = []

    def read(self, ident: int, row: dict[str, Any]) -> None:
        # Bounded like `write`: a Bignum id out of a crafted file otherwise
        # becomes a key, and str() of it raises in the report.
        if 1 <= ident <= _MAX_ID:
            self.reads.setdefault(ident, _Sightings()).add(row)

    def write(self, lo: int, hi: int, where: str, how: str) -> None:
        lo, hi = max(lo, 1), min(hi, _MAX_ID)
        if lo > hi:
            return
        if lo == hi:
            self.single_writes.setdefault(lo, _Sightings()).add(
                {"where": where, "how": how})
        else:
            self.range_writes.append(
                (lo, hi, where, "%s %d..%d" % (how, lo, hi)))

    def unknown(self, where: str) -> None:
        if len(self.unknown_where) < _MAX_LOCATIONS + 2:
            self.unknown_where.append(where)

    def intervals(self) -> list[tuple[int, int]]:
        """Every written id as merged, sorted, closed intervals."""
        spans = [(i, i) for i in self.single_writes]
        spans += [(lo, hi) for lo, hi, _, _ in self.range_writes]
        spans.sort()
        merged: list[tuple[int, int]] = []
        for lo, hi in spans:
            if merged and lo <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
            else:
                merged.append((lo, hi))
        return merged


def _covered(starts: list[int], merged: list[tuple[int, int]],
             ident: int) -> bool:
    position = bisect.bisect_right(starts, ident) - 1
    return position >= 0 and merged[position][0] <= ident <= merged[position][1]


def _holds(comparison: Any, operand: Any) -> "bool | None":
    """Whether `0 <comparison> operand` is true; None when unknowable."""
    operand = _int(operand)
    symbol = COMPARISONS.get(comparison)
    if operand is None or symbol is None:
        return None
    return {"==": 0 == operand, ">=": 0 >= operand, "<=": 0 <= operand,
            ">": 0 > operand, "<": 0 < operand, "!=": 0 != operand}[symbol]


class _Scan:
    """One project, walked once, into two tallies."""

    def __init__(self, project: Project, budget: WalkBudget) -> None:
        self.project = project
        self.budget = budget
        # Which commands mean what: XP numbers its route 209 and lays out
        # several operand reads its own way.
        xp = project.engine == "xp"
        self.route_code = route_codes(project.engine)[0]
        self.result_codes = _XP_RESULT_CODES if xp else _RESULT_CODES
        self.operand_reads = (_XP_OPERAND_READS_BY_CODE if xp
                              else _OPERAND_READS_BY_CODE)
        # The commands that carry a line of message text: XP's Show Text
        # holds its own first line, and XP has no scrolling text.
        self.text_codes = ((_CODE_SHOW_TEXT, _CODE_TEXT_LINE) if xp
                           else (_CODE_TEXT_LINE, _CODE_SCROLL_TEXT_LINE))
        self.tallies = {_SWITCHES: _Tally(), _VARIABLES: _Tally()}
        self.self_reads = 0
        self.self_writes = 0
        self.scripts_note: "str | None" = None
        self.slots_scanned = 0
        self.slots_unreadable = 0

    # -- low-level sightings ------------------------------------------------
    def _read(self, kind: str, ident: Any, where: str, how: str,
              holds: "bool | None" = None) -> None:
        ident = _int(ident)
        if ident is None:
            return
        row: dict[str, Any] = {"where": where, "how": how}
        if holds is not None:
            row["holds_while_unset"] = holds
        self.tallies[kind].read(ident, row)

    def _write(self, kind: str, lo: Any, hi: Any, where: str, how: str) -> None:
        low = _int(lo)
        if low is None:
            return
        high = _int(hi)
        self.tallies[kind].write(low, low if high is None else high,
                                 where, how)

    # -- scripts -----------------------------------------------------------
    def _script(self, text: str, where: str, how: str,
                stock: bool = False, formula: bool = False) -> None:
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if len(line) > _MAX_SCRIPT_LINE:
                for tally in self.tallies.values():
                    tally.opaque += 0 if stock else 1
                    if not stock:
                        tally.unknown(where)
                continue
            for match in _ACCESS.finditer(line):
                self._access(match, where, how, stock)
            if formula:
                for match in _FORMULA_VARIABLE.finditer(line):
                    literal = _LITERAL.match(match.group(1))
                    if literal:
                        self._read(_VARIABLES, int(literal.group(1)), where,
                                   how + " (damage formula)")
                    elif not stock:
                        self.tallies[_VARIABLES].dynamic_reads += 1
                        self.tallies[_VARIABLES].unknown(where)

    def _access(self, match: "re.Match[str]", where: str, how: str,
                stock: bool) -> None:
        kind = match.group(1)
        tally = self.tallies[kind]
        index, assignment = match.group(2), match.group(3)
        if index is None:
            # The bare object: aliased, passed on, or iterated. Anything
            # could then be read or written through it.
            if stock:
                tally.stock_dynamic += 1
            else:
                tally.opaque += 1
                tally.unknown(where)
            return
        literal = _LITERAL.match(index)
        if literal:
            ident = int(literal.group(1))
            if assignment:
                tally.write(ident, ident, where, how + " (script)")
            else:
                tally.read(ident, {"where": where, "how": how + " (script)"})
            return
        if stock:
            tally.stock_dynamic += 1
            return
        if assignment:
            tally.dynamic_writes += 1
        else:
            tally.dynamic_reads += 1
        tally.unknown(where)

    def _string(self, value: Any, where: str = "") -> "str | None":
        """A script string's text for scanning, or None if it is not one.

        A string too long to scan is returned as None too, but counted as an
        opaque access of both kinds first: silence would read as "no access".
        """
        if not isinstance(value, RubyString):
            return None
        if len(value.data) > _MAX_SCRIPT_LINE * 4:
            for tally in self.tallies.values():
                tally.opaque += 1
                tally.unknown(where)
            return None
        return self.project.codec.decode(value.data)

    # -- commands ------------------------------------------------------------
    def _commands(self, location: Location, commands: list[Any]) -> None:
        for index, command in enumerate(commands):
            if not isinstance(command, RubyObject):
                continue
            code = _int(command.get("code"))
            if code is None or code == CODE_BLANK:
                continue
            params = _seq(command.get("parameters"))
            where = location.at(index).describe()
            self._command(code, params, where)

    def _command(self, code: int, params: list[Any], where: str) -> None:
        def p(i: int) -> Any:
            return params[i] if i < len(params) else None

        if code == CODE_CONDITIONAL_BRANCH:
            kind = _int(p(0))
            if kind == COND_SWITCH:
                needs_off = _int(p(2)) == 1
                self._read(_SWITCHES, p(1), where,
                           "Conditional Branch: switch is %s"
                           % ("OFF" if needs_off else "ON"), needs_off)
            elif kind == COND_VARIABLE:
                holds = (_holds(_int(p(4)), p(3))
                         if _int(p(2)) == 0 else None)
                self._read(_VARIABLES, p(1), where,
                           "Conditional Branch: variable", holds)
                if _int(p(2)) == 1:
                    self._read(_VARIABLES, p(3), where,
                               "Conditional Branch: compared variable")
            elif kind == COND_SELF_SWITCH:
                self.self_reads += 1
            elif kind == COND_SCRIPT:
                text = self._string(p(1), where)
                if text is not None:
                    self._script(text, where, "Conditional Branch script")
            return
        if code == CODE_CONTROL_SWITCHES:
            self._write(_SWITCHES, p(0), p(1), where, "Control Switches")
            return
        if code == CODE_CONTROL_VARIABLES:
            self._write(_VARIABLES, p(0), p(1), where, "Control Variables")
            operand_type = _int(p(3))
            if operand_type == OPERAND_VARIABLE:
                self._read(_VARIABLES, p(4), where,
                           "Control Variables operand")
            elif operand_type == _OPERAND_SCRIPT:
                text = self._string(p(4), where)
                if text is not None:
                    self._script(text, where, "Control Variables script")
            return
        if code == CODE_CONTROL_SELF_SWITCH:
            self.self_writes += 1
            return
        if code in self.result_codes:
            self._write(_VARIABLES, p(0), None, where,
                        "command %d result" % code)
            return
        if code in (CODE_SCRIPT, CODE_SCRIPT_MORE):
            text = self._string(p(0), where)
            if text is not None:
                self._script(text, where, "script")
            return
        if code == self.route_code:
            self._route(p(1), where)
            return
        if code in self.text_codes:
            self._escapes(p(0), where)
            return
        if code == _CODE_SHOW_CHOICES:
            for choice in _seq(p(0)):
                self._escapes(choice, where)
            return
        for selector, wanted, positions in self.operand_reads.get(code, ()):
            if _int(p(selector)) == wanted:
                for position in positions:
                    self._read(_VARIABLES, p(position), where,
                               "command %d variable operand" % code)

    def _escapes(self, value: Any, where: str) -> None:
        if not isinstance(value, RubyString) or b"\\" not in value.data:
            return
        text = self.project.codec.decode(value.data[:_MAX_SCRIPT_LINE])
        for match in _TEXT_VARIABLE.finditer(text):
            self._read(_VARIABLES, int(match.group(1)), where,
                       "message text \\V[n]")

    def _route(self, route: Any, where: str) -> None:
        if not isinstance(route, RubyObject):
            return
        steps = _seq(route.get("list"))
        steps = steps[:self.budget.take(len(steps))]
        for step in steps:
            if not isinstance(step, RubyObject):
                continue
            code = _int(step.get("code"))
            params = _seq(step.get("parameters"))
            first = params[0] if params else None
            if code in (_MOVE_SWITCH_ON, _MOVE_SWITCH_OFF):
                self._write(_SWITCHES, first, None, where, "move route")
            elif code == _MOVE_SCRIPT:
                text = self._string(first, where)
                if text is not None:
                    self._script(text, where, "move route script")

    # -- non-command sources -------------------------------------------------
    def _page_headers(self) -> None:
        for map_id, game_map in iter_maps(self.project):
            events = game_map.get("events")
            if not isinstance(events, RubyHash):
                continue
            for event_id, event in events.sorted_items():
                if not self.budget.spend(1):
                    return
                if not isinstance(event, RubyObject):
                    continue
                name = _text(self.project, event.get("name"))
                for page_index, page in enumerate(_seq(event.get("pages"))):
                    if not self.budget.spend(1):
                        return
                    if not isinstance(page, RubyObject):
                        continue
                    where = _event_at(map_id, event_id, name,
                                      page_index).describe()
                    self._condition(page.get("condition"), where)
                    self._route(page.get("move_route"), where)

    def _condition(self, cond: Any, where: str) -> None:
        if not isinstance(cond, RubyObject):
            return
        if cond.get("switch1_valid") is True:
            self._read(_SWITCHES, cond.get("switch1_id"), where,
                       "page condition: switch 1 ON", False)
        if cond.get("switch2_valid") is True:
            self._read(_SWITCHES, cond.get("switch2_id"), where,
                       "page condition: switch 2 ON", False)
        if cond.get("variable_valid") is True:
            self._read(_VARIABLES, cond.get("variable_id"), where,
                       "page condition: variable >= value",
                       _holds(1, cond.get("variable_value")))
        if cond.get("self_switch_valid") is True:
            self.self_reads += 1

    def _database_reads(self) -> None:
        for entry_id, common in _safe_entries(self.project, "common_events"):
            if not self.budget.spend(1):
                return
            if isinstance(common, RubyObject) \
                    and (_int(common.get("trigger")) or 0) != 0:
                self._read(_SWITCHES, common.get("switch_id"),
                           Location("common_event", entry_id=entry_id)
                           .describe(),
                           "common event trigger switch ON", False)
        for entry_id, troop in _safe_entries(self.project, "troops"):
            if not self.budget.spend(1):
                return
            if not isinstance(troop, RubyObject):
                continue
            for page_index, page in enumerate(_seq(troop.get("pages"))):
                if not self.budget.spend(1):
                    return
                cond = page.get("condition") \
                    if isinstance(page, RubyObject) else None
                if isinstance(cond, RubyObject) \
                        and cond.get("switch_valid") is True:
                    self._read(_SWITCHES, cond.get("switch_id"),
                               Location("troop_page", entry_id=entry_id,
                                        page=page_index).describe(),
                               "troop page condition: switch ON", False)
        for entry_id, enemy in _safe_entries(self.project, "enemies"):
            if not self.budget.spend(1):
                return
            if not isinstance(enemy, RubyObject):
                continue
            actions = _seq(enemy.get("actions"))
            actions = actions[:self.budget.take(len(actions))]
            for action in actions:
                if isinstance(action, RubyObject) and _int(
                        action.get("condition_type")) == _ENEMY_ACTION_SWITCH:
                    self._read(_SWITCHES, action.get("condition_param1"),
                               "enemies %s field actions" % entry_id,
                               "enemy action condition: switch ON", False)
        for table in ("skills", "items"):
            for entry_id, entry in _safe_entries(self.project, table):
                if not self.budget.spend(1):
                    return
                damage = entry.get("damage") \
                    if isinstance(entry, RubyObject) else None
                if isinstance(damage, RubyObject):
                    text = self._string(
                        damage.get("formula"),
                        "%s %s field damage.formula" % (table, entry_id))
                    if text:
                        self._script(text, "%s %s field damage.formula"
                                     % (table, entry_id),
                                     "%s %s" % (table, entry_id),
                                     formula=True)

    def _script_slots(self) -> None:
        try:
            slots = self.project.load_scripts()
        except (ProjectError, ValueError, OSError) as exc:
            self.scripts_note = ("the Scripts data could not be read (%s), so "
                                 "script access is entirely unaccounted for"
                                 % exc)
            return
        for slot in slots:
            if not self.budget.spend(1):
                return
            if "code" not in slot:
                self.slots_unreadable += 1
                continue
            self.slots_scanned += 1
            name = str(slot.get("name") or "")
            stock = (name.strip() in _STOCK_SCRIPT_SLOTS
                     or bool(_STOCK_NAME.match(name.strip())))
            code = slot["code"]
            for line_number, line in enumerate(code.split("\n"), start=1):
                if "$game_" not in line:
                    continue
                self._script(line, Location("script", entry_id=slot["index"],
                                            index=line_number).describe()
                             + " (%s)" % name, "script slot", stock=stock)

    def run(self) -> None:
        for location, commands in iter_command_lists(self.project, None,
                                                      self.budget):
            self._commands(location, commands)
        self._page_headers()
        self._database_reads()
        self._script_slots()


def _names(project: Project) -> dict[str, dict[int, str]]:
    out: dict[str, dict[int, str]] = {_SWITCHES: {}, _VARIABLES: {}}
    try:
        system = project.load_data("system")
    except (ProjectError, ValueError, OSError):
        return out
    if not isinstance(system, RubyObject):
        return out
    for kind in (_SWITCHES, _VARIABLES):
        for index, value in enumerate(_seq(system.get(kind))):
            if isinstance(value, RubyString):
                name = _text(project, value)
                if name:
                    out[kind][index] = name
    return out


def _uncertainty(tally: _Tally, scan: _Scan, *, side: str) -> list[str]:
    """Why a claim of this side ("write" or "read") may be wrong."""
    reasons = []
    if side == "write" and tally.dynamic_writes:
        reasons.append("%d script write(s) with a non-literal index"
                       % tally.dynamic_writes)
    if side == "read" and tally.dynamic_reads:
        reasons.append("%d script read(s) with a non-literal index"
                       % tally.dynamic_reads)
    if tally.opaque:
        reasons.append("%d script reference(s) to the whole object"
                       % tally.opaque)
    if scan.scripts_note:
        reasons.append(scan.scripts_note)
    if scan.slots_unreadable:
        reasons.append("%d script slot(s) could not be read"
                       % scan.slots_unreadable)
    if scan.budget.exhausted:
        reasons.append("the walk stopped early")
    return reasons


def _kind_report(kind: str, tally: _Tally, names: dict[int, str],
                 scan: _Scan) -> dict[str, Any]:
    merged = tally.intervals()
    starts = [lo for lo, _ in merged]
    write_low = bool(_uncertainty(tally, scan, side="write"))
    read_low = bool(_uncertainty(tally, scan, side="read"))

    # Read but never set.
    never_set = sorted(i for i in tally.reads
                       if not _covered(starts, merged, i))
    never_set_rows = []
    for ident in never_set[:_MAX_SHOWN]:
        seen = tally.reads[ident]
        row: dict[str, Any] = {"id": ident}
        if ident in names:
            row["name"] = names[ident]
        row.update({"reads": seen.count, "read_at": seen.rows,
                    "locations_truncated": seen.count > len(seen.rows),
                    "confidence": "low" if write_low else "high"})
        never_set_rows.append(row)

    # Set but never read: counted arithmetically over the intervals, listed
    # lazily, so a range is never expanded past what is shown.
    read_ids = sorted(tally.reads)
    unread_total = 0
    shown_ids: list[int] = []
    for lo, hi in merged:
        inside = bisect.bisect_right(read_ids, hi) \
            - bisect.bisect_left(read_ids, lo)
        unread_total += (hi - lo + 1) - inside
        if len(shown_ids) < _MAX_SHOWN:
            for ident in range(lo, hi + 1):
                if ident not in tally.reads:
                    shown_ids.append(ident)
                    if len(shown_ids) >= _MAX_SHOWN:
                        break
    never_read_rows = []
    for ident in shown_ids:
        rows: list[dict[str, Any]] = []
        count = 0
        single = tally.single_writes.get(ident)
        if single:
            count += single.count
            rows += single.rows
        for lo, hi, where, how in tally.range_writes:
            if lo <= ident <= hi:
                count += 1
                if len(rows) < _MAX_LOCATIONS:
                    rows.append({"where": where, "how": how})
        row = {"id": ident}
        if ident in names:
            row["name"] = names[ident]
        row.update({"writes": count, "set_at": rows[:_MAX_LOCATIONS],
                    "locations_truncated": count > len(rows[:_MAX_LOCATIONS]),
                    "confidence": "low" if read_low else "high"})
        never_read_rows.append(row)

    return {
        "read_but_never_set": {
            "total": len(never_set), "shown": never_set_rows,
            "truncated": len(never_set) > len(never_set_rows)},
        "set_but_never_read": {
            "total": unread_total, "shown": never_read_rows,
            "truncated": unread_total > len(never_read_rows)},
        "script_access_unknown": {
            "non_literal_writes": tally.dynamic_writes,
            "non_literal_reads": tally.dynamic_reads,
            "opaque_references": tally.opaque,
            "stock_script_accesses_ignored": tally.stock_dynamic,
            "where": tally.unknown_where[:_MAX_LOCATIONS + 2],
        },
        "distinct_read": len(tally.reads),
        "distinct_written": sum(hi - lo + 1 for lo, hi in merged),
    }


def map_switch_usage(project: Project, budget: "WalkBudget | None" = None
                 ) -> dict[str, Any]:
    """Switches and variables read but never set, or set but never read.

    ``budget`` bounds the walk exactly as the other analyses' does; without
    one a fresh allowance is used. A walk that stopped says so under
    ``switch_usage_stopped`` and downgrades every claim to low confidence.
    """
    budget = WalkBudget() if budget is None else budget
    scan = _Scan(project, budget)
    scan.run()
    names = _names(project)
    result: dict[str, Any] = {
        _SWITCHES: _kind_report(_SWITCHES, scan.tallies[_SWITCHES],
                               names[_SWITCHES], scan),
        _VARIABLES: _kind_report(_VARIABLES, scan.tallies[_VARIABLES],
                                names[_VARIABLES], scan),
        "self_switches": {
            "excluded": True, "reads": scan.self_reads,
            "writes": scan.self_writes,
            "note": "self switches (A-D, per event) are a separate "
                    "namespace and take no part in the findings"},
        "scripts": {"slots_scanned": scan.slots_scanned,
                    "slots_unreadable": scan.slots_unreadable},
        "limits": {"max_findings_shown": _MAX_SHOWN,
                   "max_locations_per_finding": _MAX_LOCATIONS,
                   "max_id": _MAX_ID},
        "notes": [
            "initial value is false/0, so 'never set' means constant "
            "false/0 for the whole game; holds_while_unset says what that "
            "does to each read",
            "scripts are scanned by line and pattern: literal indices are "
            "followed, `send`/`eval`/aliasing are not, and non-literal "
            "indices are counted under script_access_unknown",
            "reads not modelled: variables used by commands outside "
            "_VARIABLE_OPERAND_READS, `\\V[n]` in database text, and "
            "plug-in scripts; a 'never read' variable may still be shown",
        ],
    }
    if scan.scripts_note:
        result["scripts"]["unavailable"] = scan.scripts_note
    stopped = budget.note("switch usage")
    if stopped:
        result["switch_usage_stopped"] = stopped
    return result
