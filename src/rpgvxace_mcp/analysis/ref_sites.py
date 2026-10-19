"""Every place a project names a database entry, a map, a switch or a variable.

`references` answers "what points at this", per command parameter, and
only for the layouts its rules confirm. This module is the wider question
two tools ask: `references` ("where is item 12 used?") and `refactor`
("move item 12 to 40, and everything that names it"). The second is a write,
so the first duty here is to know what it does *not* know.

Three kinds of site, one scanner
    * event command parameters -- the rules in `references`, plus the
      commands that rule table lacks (`ACE_EXTRA_COMMAND_RULES`);
    * database fields -- a class learning a skill, an enemy dropping an
      item, a map's encounter list, the System start position
      (`FIELD_RULES`);
    * page and troop-page conditions, and the switches and variables
      commands read and write.

Each is a `Hit` carrying where it is, what it is, and *the list or object
and slot that holds the number*, so a caller can rewrite it. A site is
identified by `(id(holder), slot)`, not by where it was reached: Marshal
links share objects, so one command list can sit on several pages, and
visiting it three times must rewrite its parameter once, not three times
(for a swap, three times is the same as not at all).

Completeness is declared, not inferred
    `RENUMBER_COMPLETE` lists, for each engine and table, the kinds of site
    a reviewer confirmed are *all* of the places that table is named. A
    table not in it cannot be renumbered, and `RENUMBER_UNCOVERED` says what
    would be missed. The declaration is checked against the scanner
    (`scanned_labels`), so adding a rule for a table without reviewing the
    declaration fails a test rather than quietly changing what "complete"
    means.

What it cannot see
    Ruby scripts that mention a number are counted apart, as text matches
    (`Mention`), never as references: `$data_items[12]` is a good guess and
    `$data_items[x + 1]` is invisible. A command that takes its target from
    a game variable names nothing statically.
"""

from __future__ import annotations

import re
from typing import Any, Callable, NamedTuple

from ..commands import (
    CODE_BLANK,
    CODE_CONDITIONAL_BRANCH,
    CODE_CONTROL_SWITCHES,
    CODE_CONTROL_VARIABLES,
    CODE_SCRIPT,
    CODE_SCRIPT_MORE,
    COND_SCRIPT,
    COND_SWITCH,
    COND_VARIABLE,
    OPERAND_VARIABLE,
    command_name,
    route_codes,
)
from ..convert import brief
from ..project import Project, ProjectError
from ..rubytypes import RubyHash, RubyObject, RubyString
from .references import REFERENCE_RULES, _RuleIndex, _requires_met, rule_index
from .switch_usage import (
    _CODE_SCROLL_TEXT_LINE,
    _CODE_SHOW_CHOICES,
    _CODE_SHOW_TEXT,
    _CODE_TEXT_LINE,
    _FORMULA_VARIABLE,
    _MAX_SCRIPT_LINE,
    _MOVE_SCRIPT,
    _MOVE_SWITCH_OFF,
    _MOVE_SWITCH_ON,
    _OPERAND_READS_BY_CODE,
    _OPERAND_SCRIPT,
    _RESULT_CODES,
    _TEXT_VARIABLE,
    _XP_OPERAND_READS_BY_CODE,
    _XP_RESULT_CODES,
)
from .walk import (
    Location,
    WalkBudget,
    _db_at,
    _event_at,
    _int,
    _map_at,
    _seq,
    _text,
    iter_command_lists,
)


# What the two tools can be asked about: every table the command rules
# know, the ones this module adds rules for, and the two numbered
# namespaces that are not tables. Plural table names, as the database tools
# spell them.
REFERENCE_KINDS = (
    "actors", "animations", "armors", "classes", "common_events", "enemies",
    "items", "maps", "skills", "states", "switches", "tilesets", "troops",
    "variables", "weapons",
)

# Switches and variables are numbered 1..5000 by the engine; a table or map
# id is not bounded that tightly, but nothing above these exists.
SWITCH_KINDS = ("switches", "variables")

# How many script mentions a scan keeps to show. All of them are counted.
MAX_MENTIONS_LISTED = 10

# The longest block of Ruby this scans for a mention. A longer one is counted
# in `SiteScan.mentions_clipped` instead of being read to its end.
MAX_MENTION_TEXT = 65536


# -- the labels: one name per kind of site, used by the coverage report ------
_L_COMMANDS = "event commands (map event pages, common events, troop battle pages)"
_L_PAGE = "map event page conditions"
_L_ROUTE = "move routes (page autonomous routes and Set Move Route commands)"
_L_TROOP_PAGE = "troop battle page conditions"
_L_FORMULA = "damage formulas (v[n])"
_L_DROPS = "enemy drop items"
_L_LEARNINGS = "class learnings"
_L_ACTIONS = "enemy actions"
_L_ACTION_CONDITIONS = "enemy action conditions"
_L_FEATURES = "features (skill add/seal, state rates/resist/attack states)"
_L_EFFECTS = "item and skill effects"
_L_ANIMATIONS = "item, skill and weapon animations"
_L_PARTY = "System starting party"
_L_TEST_MEMBERS = "System battle-test members"
_L_START = "System start positions and last-edited map"
_L_TEST_TROOP = "System battle-test troop"
_L_ENCOUNTERS = "map encounter lists"
_L_TILESET = "map tileset"
_L_TREE = "map tree parents"
_L_MEMBERS = "troop members"
_L_CLASS = "actor class"
_L_TRIGGER = "common event trigger switch"


# Commands the VX Ace rule table in `references` does not list. Each was
# read from the engine's Game_Interpreter, as that table's own comment asks:
# 311-318 and 314 take a designation (0 = a fixed actor) and then an actor
# id, and iterate through `iterate_actor_var`; 319, 320-322 and 324-325 read
# `$game_actors[params[0]]` directly; Conditional Branch's actor type (4)
# carries a sub-kind in parameter 2 that decides which table parameter 3
# names (2 class, 3 skill, 4 weapon, 5 armor, 6 state), and its enemy type
# (5) names a state when its sub-kind is 1; Force Action (339) names an
# actor by id only when the battler kind is 1.
ACE_EXTRA_COMMAND_RULES: dict[str, list[dict[str, Any]]] = {
    "actors": [
        *({"code": code, "index": 1, "requires": {0: 0}}
          for code in (311, 312, 313, 314, 315, 316, 317, 318)),
        {"code": 319, "index": 0},
        {"code": 324, "index": 0},
        {"code": 325, "index": 0},
        {"code": 339, "index": 1, "requires": {0: 1}},
    ],
    "skills": [
        {"code": 318, "index": 3},
        {"code": 339, "index": 2},
        {"code": 111, "index": 3, "requires": {0: 4, 2: 3}},
    ],
    "states": [
        {"code": 313, "index": 3},
        {"code": 111, "index": 3, "requires": {0: 4, 2: 6}},
        {"code": 111, "index": 3, "requires": {0: 5, 2: 1}},
    ],
    "classes": [
        {"code": 321, "index": 1},
        {"code": 111, "index": 3, "requires": {0: 4, 2: 2}},
    ],
    "weapons": [{"code": 111, "index": 3, "requires": {0: 4, 2: 4}}],
    "armors": [{"code": 111, "index": 3, "requires": {0: 4, 2: 5}}],
    "tilesets": [{"code": 282, "index": 0}],
    "maps": [{"code": 202, "index": 2, "requires": {1: 0}}],
}


def _merged(base: dict[str, list[dict[str, Any]]],
            extra: dict[str, list[dict[str, Any]]]
            ) -> dict[str, list[dict[str, Any]]]:
    out = {table: list(rules) for table, rules in base.items()}
    for table, rules in extra.items():
        out.setdefault(table, []).extend(rules)
    return out


_ACE_INDEX = _RuleIndex(_merged(REFERENCE_RULES, ACE_EXTRA_COMMAND_RULES))


def site_rule_index(engine: str) -> _RuleIndex:
    """The command rules these tools scan with: `rule_index`, plus VX Ace's extras.

    `rule_index` itself is unchanged -- `validate` and the two delete
    previews read it, and widening it would change what they report.
    """
    return _ACE_INDEX if engine == "vx_ace" else rule_index(engine)


class FieldRule(NamedTuple):
    """A database or map field that names an entry of ``target``.

    ``owners`` are table keys, plus ``"system"`` (the one System object) and
    ``"maps"`` (every map file). ``path`` walks from an owner's entry: a
    name steps into an object's field, ``"*"`` into every element of a list;
    the last step is the number itself, whether an object's field or a list
    element. ``requires`` is checked on the object holding that number.
    """

    label: str
    owners: tuple[str, ...]
    path: tuple[str, ...]
    target: str
    requires: tuple[tuple[str, tuple[int, ...]], ...] = ()


_FEATURE_OWNERS = ("actors", "armors", "classes", "enemies", "items",
                   "skills", "states", "weapons")


FIELD_RULES: tuple[FieldRule, ...] = (
    FieldRule(_L_CLASS, ("actors",), ("class_id",), "classes"),
    FieldRule(_L_LEARNINGS, ("classes",), ("learnings", "*", "skill_id"),
              "skills"),
    FieldRule(_L_MEMBERS, ("troops",), ("members", "*", "enemy_id"),
              "enemies"),
    FieldRule(_L_DROPS, ("enemies",), ("drop_items", "*", "data_id"),
              "items", (("kind", (1,)),)),
    FieldRule(_L_DROPS, ("enemies",), ("drop_items", "*", "data_id"),
              "weapons", (("kind", (2,)),)),
    FieldRule(_L_DROPS, ("enemies",), ("drop_items", "*", "data_id"),
              "armors", (("kind", (3,)),)),
    FieldRule(_L_ACTIONS, ("enemies",), ("actions", "*", "skill_id"),
              "skills"),
    FieldRule(_L_ACTION_CONDITIONS, ("enemies",),
              ("actions", "*", "condition_param1"), "states",
              (("condition_type", (4,)),)),
    FieldRule(_L_ACTION_CONDITIONS, ("enemies",),
              ("actions", "*", "condition_param1"), "switches",
              (("condition_type", (6,)),)),
    FieldRule(_L_FEATURES, _FEATURE_OWNERS, ("features", "*", "data_id"),
              "skills", (("code", (43, 44)),)),
    FieldRule(_L_FEATURES, _FEATURE_OWNERS, ("features", "*", "data_id"),
              "states", (("code", (13, 14, 32)),)),
    FieldRule(_L_EFFECTS, ("items", "skills"), ("effects", "*", "data_id"),
              "states", (("code", (21, 22)),)),
    FieldRule(_L_EFFECTS, ("items", "skills"), ("effects", "*", "data_id"),
              "skills", (("code", (43,)),)),
    FieldRule(_L_EFFECTS, ("items", "skills"), ("effects", "*", "data_id"),
              "common_events", (("code", (44,)),)),
    FieldRule(_L_ANIMATIONS, ("items", "skills", "weapons"),
              ("animation_id",), "animations"),
    FieldRule(_L_TRIGGER, ("common_events",), ("switch_id",), "switches",
              (("trigger", (1, 2)),)),
    FieldRule(_L_PARTY, ("system",), ("party_members", "*"), "actors"),
    FieldRule(_L_TEST_MEMBERS, ("system",),
              ("test_battlers", "*", "actor_id"), "actors"),
    *(FieldRule(_L_START, ("system",), path, "maps") for path in (
        ("start_map_id",), ("edit_map_id",), ("boat", "start_map_id"),
        ("ship", "start_map_id"), ("airship", "start_map_id"))),
    FieldRule(_L_TEST_TROOP, ("system",), ("test_troop_id",), "troops"),
    FieldRule(_L_ENCOUNTERS, ("maps",), ("encounter_list", "*", "troop_id"),
              "troops"),
    FieldRule(_L_TILESET, ("maps",), ("tileset_id",), "tilesets"),
    FieldRule(_L_TREE, ("map_infos",), ("parent_id",), "maps"),
)

# A map event page's conditions, and a troop page's: (the flag that turns the
# condition on, the field holding the id, what the id names). An id whose flag
# is off is not a reference -- every new page carries switch 1, variable 1,
# item 1 and actor 1 in its inactive fields.
PAGE_CONDITIONS = (
    ("switch1_valid", "switch1_id", "switches"),
    ("switch2_valid", "switch2_id", "switches"),
    ("variable_valid", "variable_id", "variables"),
    ("item_valid", "item_id", "items"),
    ("actor_valid", "actor_id", "actors"),
)
TROOP_PAGE_CONDITIONS = (
    ("switch_valid", "switch_id", "switches"),
    ("actor_valid", "actor_id", "actors"),
)


# For each engine, the tables whose every reference site is modelled above,
# with the site kinds a reviewer confirmed. THIS IS A DECLARATION, not a
# derivation: `scanned_labels` must equal it (see
# `declaration_matches_scanner`), so a rule added for one of these tables
# without revisiting the review fails a test. A table missing here cannot be
# renumbered.
#
# What was checked, per table, against VX Ace's own interpreter and classes:
#   maps         Transfer Player (201), Set Vehicle Location (202); System's
#                start, edit and three vehicle start maps; MapInfos
#                parent_id. (The map's file and tree key are the map itself.)
#   common_events  Call Common Event (117); the common-event effect (44) on
#                items and skills. Nothing else in the data names one.
#   items        Change Items (126), Conditional Branch item (111), Shop
#                (302/605); a page's item condition; enemy drops of kind 1.
#   skills       Change Skills (318), Force Action (339), the learned-skill
#                branch (111); class learnings; enemy actions; skill add/seal
#                features; the learn-skill effect.
#   troops       Battle Processing (301); map encounter lists; System's
#                battle-test troop.
RENUMBER_COMPLETE: dict[str, dict[str, frozenset[str]]] = {
    "vx_ace": {
        "maps": frozenset({_L_COMMANDS, _L_START, _L_TREE}),
        "common_events": frozenset({_L_COMMANDS, _L_EFFECTS}),
        "items": frozenset({_L_COMMANDS, _L_PAGE, _L_DROPS}),
        "skills": frozenset({_L_COMMANDS, _L_LEARNINGS, _L_ACTIONS, _L_FEATURES,
                             _L_EFFECTS}),
        "troops": frozenset({_L_COMMANDS, _L_ENCOUNTERS, _L_TEST_TROOP}),
    },
}

# The ids the engine addresses by number rather than through any table, so
# moving them breaks something no scan can see: Game_BattlerBase#attack_skill_id
# is 1 and #guard_skill_id is 2.
RESERVED_IDS: dict[str, frozenset[int]] = {"skills": frozenset({1, 2})}

# What a refusal names when a table has rules but they are not declared
# complete: the sites that exist and are not covered.
RENUMBER_UNCOVERED: dict[str, tuple[str, ...]] = {
    "weapons": (
        "actors' starting equipment (Actor.equips: which table a slot names "
        "depends on the actor's slot types and dual wielding)",
        "the Change Equipment command (319), whose item parameter is a weapon "
        "or an armor by slot",
        "System battle-test members' equipment"),
    "armors": (
        "actors' starting equipment (Actor.equips)",
        "the Change Equipment command (319)",
        "System battle-test members' equipment"),
    "actors": (
        "commands that take the actor from a variable",
        "scripts addressing $game_actors by number",
        "plug-in note tags"),
    "states": (
        "commands that take the state from a variable",
        "the weapon, armor, enemy and class attack-state notes of plug-ins",
        "damage formulas that test a state by number"),
    "classes": (
        "commands that take the class from a variable",
        "plug-in note tags"),
    "enemies": (
        "battle events that address an enemy by troop position (not by id), "
        "so they need no rewrite but cannot be verified here",
        "plug-in note tags"),
    "animations": (
        "animation ids typed into plug-in note tags",
        "Show Animation with the target chosen at run time"),
    "tilesets": (
        "the Change Tileset command with the id chosen at run time",
        "plug-in note tags"),
}


# What no scan of this kind can see, for every kind.
NOT_SCANNED_EVERYWHERE = (
    "Ruby scripts (text matches are counted apart as script_mentions and "
    "are not references)",
    "plug-in scripts and note tags",
    "anything chosen at run time, such as a Transfer Player or Battle "
    "Processing whose target comes from a game variable",
)


def command_rule_tables(engine: str) -> frozenset[str]:
    """The tables that have at least one event-command rule on this engine."""
    index = site_rule_index(engine)
    return frozenset(table for table, _ in index.by_table_and_code)


def _field_rules_for(kind: str, engine: str) -> list[FieldRule]:
    # The field rules are written against VX Ace's classes. Another engine
    # may share a field name by accident, and a rule matching by accident is
    # worse than none: the coverage report would claim it.
    if engine != "vx_ace":
        return []
    return [rule for rule in FIELD_RULES if rule.target == kind]


def scanned_labels(kind: str, engine: str) -> list[str]:
    """The kinds of site a scan for ``kind`` reads on ``engine``, in a stable order."""
    labels: list[str] = []
    if kind in command_rule_tables(engine) or kind in SWITCH_KINDS:
        labels.append(_L_COMMANDS)
    if any(target == kind for _, _, target in PAGE_CONDITIONS):
        labels.append(_L_PAGE)
    if any(target == kind for _, _, target in TROOP_PAGE_CONDITIONS):
        labels.append(_L_TROOP_PAGE)
    if kind == "switches":
        labels.append(_L_ROUTE)
    if kind == "variables":
        labels.append(_L_FORMULA)
    for rule in _field_rules_for(kind, engine):
        if rule.label not in labels:
            labels.append(rule.label)
    return labels


def coverage_for(kind: str, engine: str) -> dict[str, Any]:
    """What a scan for ``kind`` reads and what it cannot, as the reply says it."""
    not_scanned = list(RENUMBER_UNCOVERED.get(kind, ()))
    if engine != "vx_ace":
        not_scanned.append(
            "database fields (class learnings, enemy drops and actions, "
            "features, effects, encounter lists, System positions): these "
            "rules are written against VX Ace's classes and are not applied "
            "to %s" % engine)
        if kind in command_rule_tables(engine):
            not_scanned.append(
                "event commands outside the shared rule table (the actor, "
                "skill, state and class commands VX Ace's extras cover)")
    return {
        "scanned": scanned_labels(kind, engine),
        "not_scanned": not_scanned + list(NOT_SCANNED_EVERYWHERE),
    }


def renumber_support(engine: str, table: str) -> "tuple[bool, str]":
    """Whether ``table`` may be renumbered on ``engine``, and why not.

    Fails closed: the answer is yes only for a table the reviewed
    declaration lists. The reason names the sites that would be missed.
    """
    declared = RENUMBER_COMPLETE.get(engine, {})
    if table in declared:
        return True, ""
    if engine not in RENUMBER_COMPLETE:
        return False, (
            "renumber is only offered on VX Ace, the one engine whose "
            "reference sites have been reviewed; this project is %s" % engine)
    if table in RENUMBER_UNCOVERED:
        return False, (
            "renumber is not offered for %s, because these places that name "
            "one are not covered, so renumbering would leave them pointing at "
            "the wrong entry: %s" % (table, "; ".join(RENUMBER_UNCOVERED[table])))
    return False, (
        "renumber is not offered for %s: no reference rules exist for it, so "
        "nothing could say what points at an entry there. It is offered for "
        "%s" % (table, ", ".join(sorted(declared))))


def declaration_matches_scanner(engine: str, table: str) -> bool:
    """Whether the reviewed list for ``table`` is exactly what the scanner reads."""
    return RENUMBER_COMPLETE[engine][table] == frozenset(
        scanned_labels(table, engine))


# -- the scan ---------------------------------------------------------------


class Hit:
    """One place a number naming the thing asked about was found.

    ``holder`` and ``slot`` are what a rewrite needs: a list and an index,
    or an object and a field name. ``None`` for a site that is read but not
    rewritable (a number inside message text, or a range such as Control
    Switches 3..50).
    """

    __slots__ = ("category", "label", "where", "what", "file", "holder",
                 "slot", "value", "span")

    def __init__(self, category: str, label: str, where: str, what: str,
                 file: "str | None", holder: Any, slot: Any, value: int,
                 span: "tuple[int, int] | None" = None):
        self.category = category
        self.label = label
        self.where = where
        self.what = what
        self.file = file
        self.holder = holder
        self.slot = slot
        self.value = value
        self.span = span

    @property
    def key(self) -> "tuple[int, Any] | None":
        """Identity of the number itself, shared by every way of reaching it."""
        return None if self.holder is None else (id(self.holder), self.slot)

    def as_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {"where": self.where, "what": self.what,
                               "site": self.category}
        if self.span is not None:
            row["range"] = list(self.span)
        return row


class Mention:
    """A line of Ruby that looks like it names the number. Text, not a reference."""

    __slots__ = ("where", "text")

    def __init__(self, where: str, text: str):
        self.where = where
        self.text = text

    def as_row(self) -> dict[str, Any]:
        return {"where": self.where, "text": self.text}


class SiteScan:
    """What one scan found, and what it could not read."""

    def __init__(self, store_limit: "int | None" = None):
        self.hits: list[Hit] = []
        self.total = 0
        self.mentions: list[Mention] = []
        self.mention_total = 0
        self.mentions_clipped = 0
        # Files and tables the scan could not read, by name. A reference in
        # one of these would be invisible, so an unreadable file makes the
        # scan incomplete for anything that acts on it.
        self.unread: dict[str, str] = {}
        self._limit = store_limit

    def note(self, build: Callable[[], Hit]) -> None:
        """Count a hit, building its row only while rows are still wanted."""
        self.total += 1
        if self._limit is None or len(self.hits) < self._limit:
            self.hits.append(build())

    def mention(self, where: str, text: str) -> None:
        self.mention_total += 1
        if len(self.mentions) < MAX_MENTIONS_LISTED:
            self.mentions.append(Mention(where, text))


_SCRIPT_CODES = frozenset({CODE_SCRIPT, CODE_SCRIPT_MORE})

# Text that names a thing by number in the way scripts usually do. Digits are
# capped at nine so the conversion below cannot meet a 5,000-digit run.
_NUM = r"\s*([0-9]{1,9})\s*"
_MENTION_PATTERNS: dict[str, tuple["re.Pattern[str]", ...]] = {
    kind: tuple(re.compile(p) for p in patterns) for kind, patterns in {
        "switches": (r"\$game_switches\s*\[" + _NUM + r"\]",),
        "variables": (r"\$game_variables\s*\[" + _NUM + r"\]",),
        "items": (r"\$data_items\s*\[" + _NUM + r"\]",),
        "weapons": (r"\$data_weapons\s*\[" + _NUM + r"\]",),
        "armors": (r"\$data_armors\s*\[" + _NUM + r"\]",),
        "skills": (r"\$data_skills\s*\[" + _NUM + r"\]",),
        "states": (r"\$data_states\s*\[" + _NUM + r"\]",),
        "classes": (r"\$data_classes\s*\[" + _NUM + r"\]",),
        "enemies": (r"\$data_enemies\s*\[" + _NUM + r"\]",),
        "animations": (r"\$data_animations\s*\[" + _NUM + r"\]",),
        "tilesets": (r"\$data_tilesets\s*\[" + _NUM + r"\]",),
        "actors": (r"\$data_actors\s*\[" + _NUM + r"\]",
                   r"\$game_actors\s*\[" + _NUM + r"\]"),
        "troops": (r"\$data_troops\s*\[" + _NUM + r"\]",
                   r"BattleManager\.setup\s*\(" + _NUM + r"[,)]"),
        "common_events": (r"\$data_common_events\s*\[" + _NUM + r"\]",
                          r"reserve_common_event\s*\(" + _NUM + r"\)"),
        "maps": (r"reserve_transfer\s*\(" + _NUM + r",",
                 r"\$game_map\.map_id\s*[!=]=" + _NUM + r"(?![0-9])",
                 r"\$data_mapinfos\s*\[" + _NUM + r"\]",
                 r"\$game_map\.setup\s*\(" + _NUM + r"\)"),
    }.items()
}


class _Scanner:
    """One pass of each kind of source, for one thing, over one project."""

    def __init__(self, project: Project, kind: str, wanted: "set[int]",
                 budget: WalkBudget, scan: SiteScan):
        self.project = project
        self.kind = kind
        self.wanted = wanted
        self.budget = budget
        self.scan = scan
        self.engine = project.engine
        self.rules = site_rule_index(self.engine)
        self.xp = self.engine == "xp"
        self.route_code = route_codes(self.engine)[0]
        self.result_codes = _XP_RESULT_CODES if self.xp else _RESULT_CODES
        self.operand_reads = (_XP_OPERAND_READS_BY_CODE if self.xp
                              else _OPERAND_READS_BY_CODE)
        self.text_codes = ((_CODE_SHOW_TEXT, _CODE_TEXT_LINE) if self.xp
                           else (_CODE_TEXT_LINE, _CODE_SCROLL_TEXT_LINE))
        self.patterns = _MENTION_PATTERNS.get(kind, ())
        self.is_sv = kind in SWITCH_KINDS

    # -- plumbing -------------------------------------------------------------
    def run(self) -> None:
        self._readable()
        self._commands()
        self._maps()
        self._troop_pages()
        self._fields()
        if self.kind == "variables":
            self._formulas()
        self._script_slots()

    def _rows(self, table: str) -> list[tuple[Any, Any]]:
        """A table's rows, noting a table that could not be read."""
        if table not in self.project.data_files:
            return []
        try:
            return self.project.entries(table)
        except (ProjectError, ValueError, OSError) as exc:
            self.scan.unread[table] = brief(str(exc), 160)
            return []

    def _readable(self) -> None:
        """Note every map and table a reference could be hiding in.

        `iter_command_lists` skips a file it cannot read, silently, because
        `validate` reports those once. Here the silence would read as "no
        reference there", on the scan a rewrite is built from.
        """
        for map_id in self.project.map_ids_on_disk():
            try:
                self.project.load_map(map_id)
            except (ProjectError, ValueError, OSError) as exc:
                name = "map %d" % map_id
                self.scan.unread[name] = brief(str(exc), 160)
        for table in ("common_events", "troops"):
            self._rows(table)

    def _file_of(self, location: Location) -> "str | None":
        if location.kind == "map_event":
            return self.project.map_file(location.map_id)
        if location.kind == "common_event":
            return self.project.filename_for("common_events")
        if location.kind == "troop_page":
            return self.project.filename_for("troops")
        return None

    def _hit(self, category: str, label: str, where: str, what: str,
             file: "str | None", holder: Any, slot: Any, value: int,
             span: "tuple[int, int] | None" = None) -> None:
        self.scan.note(lambda: Hit(category, label, where, what, file, holder,
                                   slot, value, span))

    def _wanted_in(self, low: int, high: int) -> list[int]:
        return [value for value in self.wanted if low <= value <= high]

    # -- event commands -------------------------------------------------------
    def _commands(self) -> None:
        rules = self.rules
        table_codes = {code for (table, code) in rules.by_table_and_code
                       if table == self.kind}
        for location, commands in iter_command_lists(self.project, None,
                                                     self.budget):
            file = self._file_of(location)
            for index, command in enumerate(commands):
                if not isinstance(command, RubyObject):
                    continue
                code = _int(command.get("code", CODE_BLANK))
                if code is None or code == CODE_BLANK:
                    continue
                params = command.get("parameters")
                if not isinstance(params, list):
                    continue
                if code in table_codes:
                    self._by_rule(code, params, location, index, file)
                if self.is_sv:
                    self._sv_command(code, params, location, index, file)
                if self.patterns:
                    self._command_scripts(code, params, location, index)

    def _by_rule(self, code: int, params: list[Any], location: Location,
                 index: int, file: "str | None") -> None:
        for rule in self.rules.by_table_and_code.get((self.kind, code), ()):
            if not _requires_met(params, rule.get("requires")):
                continue
            position = rule["index"]
            if position >= len(params):
                continue
            value = _int(params[position])
            if value is None or value not in self.wanted:
                continue
            where = location.at(index).describe()
            self._hit("command", _L_COMMANDS, where,
                      command_name(code, self.engine), file, params,
                      position, value)

    # -- switches and variables ----------------------------------------------
    def _sv(self, params: list[Any], slot: int, low: Any, high: Any,
            where_of: Callable[[], str], what: str,
            file: "str | None") -> None:
        """One switch or variable operand, or range of them, against the asked-for ids."""
        lo = _int(low)
        if lo is None:
            return
        hi = _int(high)
        hi = lo if hi is None else hi
        for value in self._wanted_in(lo, hi):
            self._hit("command", _L_COMMANDS, where_of(),
                      what if lo == hi else "%s %d..%d" % (what, lo, hi),
                      file, params if lo == hi else None,
                      slot if lo == hi else None, value,
                      None if lo == hi else (lo, hi))

    def _sv_command(self, code: int, params: list[Any], location: Location,
                    index: int, file: "str | None") -> None:
        def p(i: int) -> Any:
            return params[i] if i < len(params) else None

        def where() -> str:
            return location.at(index).describe()

        switches = self.kind == "switches"
        if code == CODE_CONDITIONAL_BRANCH:
            branch = _int(p(0))
            if switches and branch == COND_SWITCH:
                self._sv(params, 1, p(1), p(1), where,
                         "Conditional Branch (switch)", file)
            elif not switches and branch == COND_VARIABLE:
                self._sv(params, 1, p(1), p(1), where,
                         "Conditional Branch (variable)", file)
                if _int(p(2)) == 1:
                    self._sv(params, 3, p(3), p(3), where,
                             "Conditional Branch (compared variable)", file)
            return
        if switches and code == CODE_CONTROL_SWITCHES:
            self._sv(params, 0, p(0), p(1) if p(1) is not None else p(0),
                     where, command_name(code, self.engine), file)
            return
        if switches and code == self.route_code:
            self._route(p(1), where, file, "Set Move Route")
            return
        if switches:
            return
        if code == CODE_CONTROL_VARIABLES:
            self._sv(params, 0, p(0), p(1) if p(1) is not None else p(0),
                     where, command_name(code, self.engine), file)
            if _int(p(3)) == OPERAND_VARIABLE:
                self._sv(params, 4, p(4), p(4), where,
                         "Control Variables (operand variable)", file)
            return
        if code in self.result_codes:
            self._sv(params, 0, p(0), p(0), where,
                     "%s (result variable)" % command_name(code, self.engine),
                     file)
            return
        for selector, wanted, positions in self.operand_reads.get(code, ()):
            if _int(p(selector)) == wanted:
                for position in positions:
                    self._sv(params, position, p(position), p(position),
                             where, "%s (variable operand)"
                             % command_name(code, self.engine), file)
        if code in self.text_codes:
            self._escapes(p(0), where, file)
        elif code == _CODE_SHOW_CHOICES:
            for choice in _seq(p(0)):
                self._escapes(choice, where, file)

    def _escapes(self, value: Any, where: Callable[[], str],
                 file: "str | None") -> None:
        """`\\V[n]` in message text: a read, but not a number that can be rewritten."""
        if not isinstance(value, RubyString) or b"\\" not in value.data:
            return
        text = self.project.codec.decode(value.data[:_MAX_SCRIPT_LINE])
        for match in _TEXT_VARIABLE.finditer(text):
            number = int(match.group(1))
            if number in self.wanted:
                self._hit("text", _L_COMMANDS, where(), "message text \\V[n]",
                          file, None, None, number)

    def _route(self, route: Any, where: Callable[[], str],
               file: "str | None", what: str) -> None:
        if not isinstance(route, RubyObject):
            return
        steps = _seq(route.get("list"))
        steps = steps[:self.budget.take(len(steps))]
        for step in steps:
            if not isinstance(step, RubyObject):
                continue
            if _int(step.get("code")) not in (_MOVE_SWITCH_ON,
                                              _MOVE_SWITCH_OFF):
                continue
            params = step.get("parameters")
            if isinstance(params, list) and params:
                self._sv(params, 0, params[0], params[0], where,
                         "%s: switch step" % what, file)

    # -- script mentions -----------------------------------------------------
    def _command_scripts(self, code: int, params: list[Any],
                         location: Location, index: int) -> None:
        text: Any = None
        if code in _SCRIPT_CODES and params:
            text = params[0]
        elif code == CODE_CONDITIONAL_BRANCH and len(params) > 1 \
                and _int(params[0]) == COND_SCRIPT:
            text = params[1]
        elif code == CODE_CONTROL_VARIABLES and len(params) > 4 \
                and _int(params[3]) == _OPERAND_SCRIPT:
            text = params[4]
        elif code == self.route_code and len(params) > 1 \
                and isinstance(params[1], RubyObject):
            for step in _seq(params[1].get("list"))[:self.budget.take(
                    len(_seq(params[1].get("list"))))]:
                if isinstance(step, RubyObject) \
                        and _int(step.get("code")) == _MOVE_SCRIPT:
                    step_params = _seq(step.get("parameters"))
                    if step_params:
                        self._scan_text(step_params[0],
                                        lambda: location.at(index).describe())
            return
        if text is not None:
            self._scan_text(text, lambda: location.at(index).describe())

    def _scan_text(self, value: Any, where: Callable[[], str]) -> None:
        if not isinstance(value, RubyString):
            return
        if len(value.data) > MAX_MENTION_TEXT * 4:
            self.scan.mentions_clipped += 1
            return
        text = self.project.codec.decode(value.data)
        if len(text) > MAX_MENTION_TEXT:
            self.scan.mentions_clipped += 1
            text = text[:MAX_MENTION_TEXT]
        for pattern in self.patterns:
            for match in pattern.finditer(text):
                if int(match.group(1)) in self.wanted:
                    start = max(0, match.start() - 20)
                    self.scan.mention(where(), brief(
                        text[start:match.end() + 20].strip(), 80))

    def _script_slots(self) -> None:
        if not self.patterns:
            return
        try:
            slots = self.project.load_scripts()
        except (ProjectError, ValueError, OSError) as exc:
            self.scan.unread["scripts"] = brief(str(exc), 160)
            return
        for slot in slots:
            if not self.budget.spend(1):
                return
            code = slot.get("code")
            if not isinstance(code, str):
                continue
            for number, line in enumerate(code.split("\n"), start=1):
                if len(line) > MAX_MENTION_TEXT:
                    self.scan.mentions_clipped += 1
                    continue
                for pattern in self.patterns:
                    for match in pattern.finditer(line):
                        if int(match.group(1)) in self.wanted:
                            self.scan.mention(
                                "%s (%s)" % (Location(
                                    "script", entry_id=slot["index"],
                                    index=number).describe(),
                                    _text_of(slot.get("name"))),
                                brief(line.strip(), 80))

    # -- map pages and map fields --------------------------------------------
    def _maps(self) -> None:
        pages = (any(t == self.kind for _, _, t in PAGE_CONDITIONS)
                 or self.kind == "switches")
        map_rules = [r for r in _field_rules_for(self.kind, self.engine)
                     if "maps" in r.owners]
        if not pages and not map_rules:
            return
        for map_id in self.project.map_ids_on_disk():
            try:
                game_map = self.project.load_map(map_id)
            except (ProjectError, ValueError, OSError):
                continue        # `_readable` already noted it
            file = self.project.map_file(map_id)
            for rule in map_rules:
                self._apply_rule(rule, game_map, file,
                                 lambda field, m=map_id:
                                 _map_at(m, field).describe())
            if pages:
                self._event_pages(map_id, game_map, file)

    def _event_pages(self, map_id: int, game_map: RubyObject,
                     file: str) -> None:
        events = game_map.get("events")
        if not isinstance(events, RubyHash):
            return
        for event_id, event in events.sorted_items():
            if _int(event_id) is None:
                continue
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
                where = _event_at(map_id, event_id, name, page_index).describe()
                self._conditions(page.get("condition"), PAGE_CONDITIONS,
                                 "map event page conditions", _L_PAGE,
                                 where + " condition", file)
                if self.kind == "switches":
                    self._route(page.get("move_route"),
                                lambda w=where: w + " autonomous route", file,
                                "autonomous route")

    def _conditions(self, cond: Any,
                    table: "tuple[tuple[str, str, str], ...]", what: str,
                    label: str, where: str, file: "str | None") -> None:
        if not isinstance(cond, RubyObject):
            return
        for valid, id_field, target in table:
            if target != self.kind or cond.get(valid) is not True:
                continue
            value = _int(cond.get(id_field))
            if value is not None and value in self.wanted:
                self._hit("page_condition", label, where,
                          "%s: %s" % (what, id_field), file, cond, id_field,
                          value)

    def _troop_pages(self) -> None:
        if not any(t == self.kind for _, _, t in TROOP_PAGE_CONDITIONS):
            return
        file = self.project.filename_for("troops")
        for entry_id, troop in self._rows("troops"):
            if not self.budget.spend(1):
                return
            if not isinstance(troop, RubyObject):
                continue
            for page_index, page in enumerate(_seq(troop.get("pages"))):
                if not self.budget.spend(1):
                    return
                if isinstance(page, RubyObject):
                    self._conditions(
                        page.get("condition"), TROOP_PAGE_CONDITIONS,
                        "troop battle page conditions", _L_TROOP_PAGE,
                        Location("troop_page", entry_id=entry_id,
                                 page=page_index).describe() + " condition",
                        file)

    # -- database fields -----------------------------------------------------
    def _fields(self) -> None:
        for rule in _field_rules_for(self.kind, self.engine):
            for owner in rule.owners:
                if owner == "maps":
                    continue        # read with the maps, above
                if owner not in self.project.data_files:
                    continue
                file = self.project.filename_for(owner)
                for entry_id, entry in self._rows(owner):
                    if not self.budget.spend(1):
                        return
                    # A Hash key is whatever the file put there, and it is
                    # about to be formatted into a location.
                    if _int(entry_id) is None \
                            or not isinstance(entry, RubyObject):
                        continue
                    self._apply_rule(
                        rule, entry, file,
                        lambda field, o=owner, e=entry_id:
                        _db_at(o, e, field).describe())

    def _apply_rule(self, rule: FieldRule, entry: RubyObject,
                    file: "str | None",
                    where_of: Callable[[str], str]) -> None:
        for holder, slot, path in self._descend(entry, rule.path, 0, ""):
            if not self._required(holder, rule.requires):
                continue
            raw = holder[slot] if isinstance(holder, list) else holder.get(slot)
            value = _int(raw)
            if value is None or value not in self.wanted:
                continue
            self._hit("field", rule.label, where_of(path), path, file, holder,
                      slot, value)

    @staticmethod
    def _required(holder: Any,
                  requires: "tuple[tuple[str, tuple[int, ...]], ...]") -> bool:
        if not requires:
            return True
        if not isinstance(holder, RubyObject):
            return False
        return all(_int(holder.get(field)) in values
                   for field, values in requires)

    def _descend(self, node: Any, path: "tuple[str, ...]", depth: int,
                 rendered: str):
        """Every ``(holder, slot, rendered path)`` a field path reaches in ``node``."""
        step = path[depth]
        last = depth == len(path) - 1
        if step == "*":
            if not isinstance(node, list):
                return
            for position, item in enumerate(node):
                # Charged per element: a list is whatever the file says it is,
                # and a Marshal link can make it a product with its owner.
                if not self.budget.spend(1):
                    return
                shown = "%s[%d]" % (rendered, position)
                if last:
                    yield node, position, shown
                else:
                    yield from self._descend(item, path, depth + 1, shown)
            return
        if not isinstance(node, RubyObject):
            return
        shown = rendered + ("." if rendered else "") + step
        if last:
            yield node, step, shown
        else:
            yield from self._descend(node.get(step), path, depth + 1, shown)

    def _formulas(self) -> None:
        """`v[n]` in a damage formula: a variable read, text, never rewritten."""
        for table in ("skills", "items"):
            file = self.project.filename_for(table) \
                if table in self.project.data_files else None
            for entry_id, entry in self._rows(table):
                if not self.budget.spend(1):
                    return
                damage = entry.get("damage") \
                    if isinstance(entry, RubyObject) else None
                formula = damage.get("formula") \
                    if isinstance(damage, RubyObject) else None
                if not isinstance(formula, RubyString) \
                        or len(formula.data) > _MAX_SCRIPT_LINE:
                    continue
                for match in _FORMULA_VARIABLE.finditer(
                        self.project.codec.decode(formula.data)):
                    literal = match.group(1).strip()
                    if literal.isascii() and literal.isdigit() \
                            and len(literal) <= 9 \
                            and int(literal) in self.wanted:
                        self._hit("formula", _L_FORMULA,
                                  _db_at(table, entry_id,
                                         "damage.formula").describe(),
                                  "damage formula v[n]", file, None, None,
                                  int(literal))


def _text_of(value: Any) -> str:
    return brief(value, 40) if value is not None else ""


def scan_sites(project: Project, kind: str, wanted: "set[int]",
               budget: WalkBudget,
               store_limit: "int | None" = None) -> SiteScan:
    """Every place ``kind`` ids in ``wanted`` are named, within ``budget``.

    ``store_limit`` bounds how many `Hit` rows are kept (all are counted);
    ``None`` keeps every one, which a rewrite needs. A scan that ran out of
    budget says so on ``budget`` -- the caller reads `budget.note` -- and an
    unreadable file is in `SiteScan.unread`: either means the answer is
    not the whole project's.
    """
    scan = SiteScan(store_limit)
    if kind not in REFERENCE_KINDS or not wanted:
        return scan
    _Scanner(project, kind, set(wanted), budget, scan).run()
    return scan


def unique_hits(hits: "list[Hit]") -> "list[Hit]":
    """One hit per number, however many places reach it.

    The first location to reach a shared number is kept. Hits that cannot be
    rewritten (no holder) are all kept: each is a distinct piece of text.
    """
    seen: set[tuple[int, Any]] = set()
    out: list[Hit] = []
    for hit in hits:
        key = hit.key
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(hit)
    return out


def rewrite_hits(hits: "list[Hit]", mapping: dict[int, int]) -> int:
    """Change every hit's number through ``mapping``, once per number.

    Returns how many numbers were changed. A shared number is rewritten once:
    for a swap, a second pass over the same slot would map it straight back.
    """
    changed = 0
    for hit in unique_hits(hits):
        if hit.holder is None:
            raise ProjectError(
                "%s cannot be rewritten: it is %s, not a stored number"
                % (hit.where, hit.what))
        new = mapping.get(hit.value)
        if new is None:
            continue
        if isinstance(hit.holder, list):
            hit.holder[hit.slot] = new
        else:
            hit.holder.set(hit.slot, new)
        changed += 1
    return changed

