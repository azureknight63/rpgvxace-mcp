"""Event commands: the code table, a readable rendering, and an authoring DSL.

Command codes come from the ``Game_Interpreter`` that ships with each engine, so
:data:`ENGINE_COMMANDS` holds exactly the codes that engine dispatches, plus the
continuation codes that only ever appear in data files. VX Ace's set is VX's
plus fifteen additions (see :data:`ACE_ONLY_COMMANDS`).

Parameter layouts are given in :data:`PARAM_SPECS` for the commands that are
worth authoring directly. Codes missing from that table are still read, listed
and written -- their parameters are simply passed through untouched. When you
need one of them, read a working example out of the project first
(``event_get`` on an event that already uses it) rather than guessing.
"""

from __future__ import annotations

from typing import Any, Callable

from .convert import TextCodec, brief, decoded_text
from .rubytypes import RubyObject, RubyString, as_int, as_list, rendered
from .schema import new_event_command, new_move_command

# Every code the VX interpreter handles, plus the continuation codes that live
# only in data. 0 terminates a command list. VX Ace dispatches all of these too.
BASE_COMMAND_NAMES: dict[int, str] = {
    0: "Blank",
    101: "Show Text",
    102: "Show Choices",
    103: "Input Number",
    108: "Comment",
    111: "Conditional Branch",
    112: "Loop",
    113: "Break Loop",
    115: "Exit Event Processing",
    117: "Call Common Event",
    118: "Label",
    119: "Jump to Label",
    121: "Control Switches",
    122: "Control Variables",
    123: "Control Self Switch",
    124: "Control Timer",
    125: "Change Gold",
    126: "Change Items",
    127: "Change Weapons",
    128: "Change Armor",
    129: "Change Party Member",
    132: "Change Battle BGM",
    133: "Change Battle End ME",
    134: "Change Save Access",
    135: "Change Menu Access",
    136: "Change Encounter",
    201: "Transfer Player",
    202: "Set Vehicle Location",
    203: "Set Event Location",
    204: "Scroll Map",
    205: "Set Move Route",
    206: "Get on/off Vehicle",
    211: "Change Transparency",
    212: "Show Animation",
    213: "Show Balloon Icon",
    214: "Erase Event",
    221: "Fadeout Screen",
    222: "Fadein Screen",
    223: "Tint Screen",
    224: "Flash Screen",
    225: "Shake Screen",
    230: "Wait",
    231: "Show Picture",
    232: "Move Picture",
    233: "Rotate Picture",
    234: "Tint Picture",
    235: "Erase Picture",
    236: "Set Weather Effects",
    241: "Play BGM",
    242: "Fadeout BGM",
    245: "Play BGS",
    246: "Fadeout BGS",
    249: "Play ME",
    250: "Play SE",
    251: "Stop SE",
    301: "Battle Processing",
    302: "Shop Processing",
    303: "Name Input Processing",
    311: "Change HP",
    312: "Change MP",
    313: "Change State",
    314: "Recover All",
    315: "Change Experience",
    316: "Change Level",
    317: "Change Parameters",
    318: "Change Skills",
    319: "Change Equipment",
    320: "Change Actor Name",
    321: "Change Class",
    322: "Change Actor Graphic",
    323: "Change Vehicle Graphic",
    331: "Change Enemy HP",
    332: "Change Enemy MP",
    333: "Change Enemy State",
    334: "Enemy Recover All",
    335: "Enemy Appear",
    336: "Enemy Transform",
    337: "Show Battle Animation",
    339: "Force Action",
    340: "Abort Battle",
    351: "Open Menu Screen",
    352: "Open Save Screen",
    353: "Game Over",
    354: "Return to Title Screen",
    355: "Script",
    # Continuation codes: extra lines and branch markers.
    401: "Text Line",
    402: "When [choice]",
    403: "When Cancel",
    404: "End Choices",
    408: "Comment Line",
    411: "Else",
    412: "End Branch",
    413: "Repeat Above",
    505: "Move Command",
    601: "If Win",
    602: "If Escape",
    603: "If Lose",
    604: "End Battle",
    605: "Shop Goods",
    655: "Script Line",
}

# Codes RPG Maker VX Ace adds. VX's interpreter has no handler for these, so
# they only appear in Ace projects. Taken by comparing the command_* methods of
# each engine's own Game_Interpreter, not inferred.
ACE_ONLY_COMMANDS: dict[int, str] = {
    104: "Select Item",
    105: "Show Scrolling Text",
    137: "Change Formation Access",
    138: "Change Window Color",
    216: "Change Player Followers",
    217: "Gather Followers",
    243: "Save BGM",
    244: "Resume BGM",
    261: "Play Movie",
    281: "Change Map Name Display",
    282: "Change Tileset",
    283: "Change Battle Back",
    284: "Change Parallax",
    285: "Get Location Info",
    324: "Change Nickname",
    405: "Scrolling Text Line",
}


ACE_COMMAND_NAMES: dict[int, str] = {**BASE_COMMAND_NAMES, **ACE_ONLY_COMMANDS}


ENGINE_COMMANDS: dict[str, dict[int, str]] = {
    "vx_ace": ACE_COMMAND_NAMES,
    "vx": BASE_COMMAND_NAMES,
}

# A superset used for rendering: naming a code that this engine happens not to
# dispatch is harmless, whereas failing to name one that it does is not.
COMMAND_NAMES: dict[int, str] = ACE_COMMAND_NAMES

# Codes that continue the command above them rather than standing alone.
# 405 carries the lines of Ace's Show Scrolling Text.
CONTINUATION_CODES = {401, 402, 403, 404, 405, 408, 411, 412, 413, 505,
                      601, 602, 603, 604, 605, 655}

# A parameter spec ending in this is partial: the leading parameters are
# confirmed and the rest are not, so the renderer does not name any of them.
VARIADIC = "..."

# VX Ace and VX both cap Show Choices at four.
MAX_CHOICES = 4

# The largest value RPG::EventCommand 102's parameters[1] takes: one
# past the four choices the editor allows, which is the When Cancel
# branch. Named because `_compile_choices` states the range in a comment
# and now also enforces it, and two spellings of one range is how the
# comment came to be the only thing holding it.
MAX_CANCEL_KIND = MAX_CHOICES + 1

# Indentation is read from the file, so it is clamped before being used to build
# a string: a corrupt command can carry an indent of 2**30.
MAX_RENDER_INDENT = 64
MAX_AUTHORED_INDENT = 99


PARAM_SPECS: dict[int, list[str]] = {
    101: ["face_name", "face_index", "background", "position"],
    102: ["choices", "cancel_type"],
    103: ["variable_id", "digits"],
    108: ["text"],
    111: ["type", VARIADIC],
    117: ["common_event_id"],
    118: ["label"],
    119: ["label"],
    121: ["switch_id_start", "switch_id_end", "operation"],
    122: ["variable_id_start", "variable_id_end", "operation",
          "operand_type", "operand", "operand2"],
    123: ["self_switch_ch", "operation"],
    124: ["operation", "seconds"],
    125: ["operation", "operand_type", "value"],
    126: ["item_id", "operation", "operand_type", "value"],
    127: ["weapon_id", "operation", "operand_type", "value", "include_equipped"],
    128: ["armor_id", "operation", "operand_type", "value", "include_equipped"],
    129: ["actor_id", "operation", "initialize"],
    201: ["designation", "map_id", "x", "y", "direction", "fade"],
    203: ["character_id", "designation", "x", "y", "direction"],
    205: ["character_id", "move_route"],
    212: ["character_id", "animation_id", "wait"],
    213: ["character_id", "balloon_id", "wait"],
    214: [],
    230: ["frames"],
    241: ["audio"],
    245: ["audio"],
    249: ["audio"],
    250: ["audio"],
    301: ["designation", "troop_id", "can_escape", "can_lose"],
    302: ["goods_type", "goods_id", "price_type", "price"],
    303: ["actor_id", "max_characters"],
    314: ["designation", "actor_id"],
    340: [],
    351: [],
    352: [],
    353: [],
    354: [],
    355: ["script"],
    401: ["text"],
    402: ["index", "text"],
    408: ["text"],
    605: ["goods_type", "goods_id", "price_type", "price"],
    655: ["script"],
}

# Layouts that differ in VX Ace, or belong to commands only it has.
ACE_PARAM_SPECS: dict[int, list[str]] = {
    # Ace adds a "purchase only" flag to shop processing.
    302: ["goods_type", "goods_id", "price_type", "price", "purchase_only"],
    605: ["goods_type", "goods_id", "price_type", "price", "purchase_only"],
    282: ["tileset_id"],
    324: ["actor_id", "nickname"],
    405: ["text"],
}


# Rendering uses every layout we know; a name is only shown for a parameter
# that is actually present.
ALL_PARAM_SPECS: dict[int, list[str]] = {**PARAM_SPECS, **ACE_PARAM_SPECS}


def command_table(engine: str) -> dict[int, str]:
    """The codes one engine's interpreter dispatches."""
    return ENGINE_COMMANDS.get(engine, ACE_COMMAND_NAMES)


def param_specs(engine: str) -> dict[int, list[str]]:
    """Confirmed parameter layouts for one engine."""
    return PARAM_SPECS if engine == "vx" else ALL_PARAM_SPECS

# Operation codes shared by several commands.
SWITCH_OPERATIONS = {0: "ON", 1: "OFF"}
VARIABLE_OPERATIONS = {0: "=", 1: "+=", 2: "-=", 3: "*=", 4: "/=", 5: "%="}
# Operand kinds for Control Variables (122). 0-2 are the same on both engines.
# The higher numbers are VX's; VX Ace reorganised them, and this server does not
# claim to know that layout -- read a working 122 with event_get include_raw.
# Named because `_compile_variable` writes these three numbers and
# `_variables_line` reads them back, so the meaning of 1 lived in this
# table, in a bare assignment in the writer and in a bare comparison in
# the reader -- three places for one fact.
OPERAND_CONSTANT, OPERAND_VARIABLE, OPERAND_RANDOM = 0, 1, 2
SHARED_VARIABLE_OPERANDS = {OPERAND_CONSTANT: "constant",
                            OPERAND_VARIABLE: "variable",
                            OPERAND_RANDOM: "random"}
VX_VARIABLE_OPERANDS = {**SHARED_VARIABLE_OPERANDS,
                        3: "item", 4: "actor", 5: "enemy", 6: "character",
                        7: "other"}

# Names accepted for a variable operation in the authoring DSL.
VARIABLE_OP_CODES = {"set": 0, "=": 0, "add": 1, "+": 1, "sub": 2, "-": 2,
                     "mul": 3, "*": 3, "div": 4, "/": 4, "mod": 5, "%": 5}


COMPARISONS = {0: "==", 1: ">=", 2: "<=", 3: ">", 4: "<", 5: "!="}

# Which audio folder each "play this" command draws from. The keys are the
# command codes, so anything needing the set of audio commands can use them.
AUDIO_COMMAND_KINDS = {241: "bgm", 245: "bgs", 249: "me", 250: "se"}

# The codes other modules test for by name rather than by number.
CODE_BLANK = 0          # the empty command every list ends with
CODE_SHOW_TEXT = 101    # parameters[0] is the face graphic name
CODE_LABEL = 118
CODE_JUMP_TO_LABEL = 119
CODE_TRANSFER_PLAYER = 201
CODE_CALL_COMMON_EVENT = 117
CODE_SCRIPT, CODE_SCRIPT_MORE = 355, 655
CODE_COMMENT = 108
# The codes `compile_commands` emits, `summarize` dispatches on, or
# another module reads by name. Named because those tables spelled most of
# them as bare integers beside a few that had names, and a module that
# names some of its codes and not others is one where the next one gets a
# name only if the author happens to look --
# `TestEveryCommandCodeConstantIsRead` is what holds the other direction,
# that a name here is a name something uses.
#
# CODE_ERASE_EVENT, CODE_GAME_OVER and CODE_RETURN_TO_TITLE are here for
# the third reason: `pages.CONTROL_RELEASING_CODES` reads them as a set
# beside CODE_TRANSFER_PLAYER. This comment used to name CODE_EXIT_EVENT
# as the third member of that set and omit CODE_ERASE_EVENT, which is a
# prose copy of a set that lives in another module, wrong in both
# directions -- so it does not describe the membership any more.
CODE_EXIT_EVENT = 115
CODE_LOOP = 112
CODE_BREAK_LOOP = 113
CODE_REPEAT_ABOVE = 413
CODE_CONTROL_SWITCHES = 121
CODE_CONTROL_SELF_SWITCH = 123
CODE_WAIT = 230
CODE_ERASE_EVENT = 214
CODE_GAME_OVER = 353
CODE_RETURN_TO_TITLE = 354
# The two continuation codes `summarize` renders as bare text -- the
# quoted line and nothing else, because the command they continue carries
# the name.
CODE_TEXT_LINE = 401
CODE_COMMENT_LINE = 408

# The rest of the codes `_SUMMARY_RENDERERS` dispatches on, and the four
# `compile_commands` emits to close a structure it opened. This block held
# a header written for the two above it, which said "the continuation codes
# rendered as bare text" over nine constants of which seven are neither,
# and two counts that matched nothing -- so it says what each group is
# instead of how many there are.
CODE_SHOW_CHOICES = 102
CODE_CHOICE_BRANCH = 402
CODE_WHEN_CANCEL = 403
CODE_END_CHOICES = 404
CODE_CONDITIONAL_BRANCH = 111
CODE_ELSE = 411
CODE_END_BRANCH = 412
CODE_CONTROL_VARIABLES = 122
CODE_SET_MOVE_ROUTE = 205
CODE_BATTLE_PROCESSING = 301
CODE_MOVE_ROUTE_STEP = 505

# Transfer Player (201): designation, map_id, x, y, direction, fade.
TRANSFER_MAP, TRANSFER_X, TRANSFER_Y = 1, 2, 3

# What `parameters[0]` says the three numbers after it are. 0 is the
# destination itself; 1 means they are *variable ids* to read it from.
# `references.REFERENCE_RULES` already pins the map-id rule to
# `requires: {0: 0}` for this reason -- checking a variable id against the
# map list would report a missing map that is not missing -- while the
# renderer read them as a destination either way, so a Transfer Player
# through variables rendered as "map 3 at (5, 6)".
DESIGNATION_DIRECT, DESIGNATION_VARIABLE = 0, 1

# Battle Processing (301) uses the same parameter for the same job, with one
# more kind: a random encounter from the map's troop list, which names no
# troop at all.
BATTLE_DESIGNATION_RANDOM = 2

# Conditional Branch (111) parameter 0: what kind of thing is being tested.
COND_SWITCH = 0
COND_VARIABLE = 1
COND_SELF_SWITCH = 2
COND_TIMER = 3
COND_ACTOR = 4
COND_ENEMY = 5
COND_CHARACTER = 6
COND_GOLD = 7
COND_ITEM = 8
COND_WEAPON = 9
COND_ARMOR = 10
COND_BUTTON = 11
COND_SCRIPT = 12

# What each one tests, named once. `describe_condition` dispatches on the
# constants above and `docs.py` builds its reference from this, so the numbers
# and their meanings exist in one place -- they used to exist in three, and
# eight of the thirteen constants were dead while the function that should
# have used them spelled their numbers out.
CONDITION_KINDS = {
    COND_SWITCH: "switch",
    COND_VARIABLE: "variable",
    COND_SELF_SWITCH: "self switch",
    COND_TIMER: "timer",
    COND_ACTOR: "actor",
    COND_ENEMY: "enemy",
    COND_CHARACTER: "character",
    COND_GOLD: "gold",
    COND_ITEM: "item",
    COND_WEAPON: "weapon",
    COND_ARMOR: "armor",
    COND_BUTTON: "button",
    COND_SCRIPT: "script",
}
COMPARISON_CODES = {v: k for k, v in COMPARISONS.items()}

# Move route command codes (advisory: taken from the shared XP/VX/Ace move
# command set, not from the VX reference manual).
MOVE_COMMAND_NAMES: dict[int, str] = {
    0: "Blank", 1: "Move Down", 2: "Move Left", 3: "Move Right", 4: "Move Up",
    5: "Move Lower Left", 6: "Move Lower Right", 7: "Move Upper Left",
    8: "Move Upper Right", 9: "Move at Random", 10: "Move toward Player",
    11: "Move away from Player", 12: "1 Step Forward", 13: "1 Step Backward",
    14: "Jump", 15: "Wait", 16: "Turn Down", 17: "Turn Left", 18: "Turn Right",
    19: "Turn Up", 20: "Turn 90 deg Right", 21: "Turn 90 deg Left",
    22: "Turn 180 deg", 23: "Turn 90 deg Random", 24: "Turn at Random",
    25: "Turn toward Player", 26: "Turn away from Player", 27: "Switch ON",
    28: "Switch OFF", 29: "Change Speed", 30: "Change Frequency",
    31: "Walking Animation ON", 32: "Walking Animation OFF",
    33: "Stepping Animation ON", 34: "Stepping Animation OFF",
    35: "Direction Fix ON", 36: "Direction Fix OFF", 37: "Through ON",
    38: "Through OFF", 39: "Always on Top ON", 40: "Always on Top OFF",
    41: "Change Graphic", 42: "Change Opacity", 43: "Change Blending",
    44: "Play SE", 45: "Script",
}

# Character targets used by 203/205/212 and friends.
CHARACTER_TARGETS = {-1: "player", 0: "this event"}


class CommandError(ValueError):
    """Raised when a command entry cannot be compiled."""


def command_name(code: Any) -> str:
    """The editor's name for a command code, whatever the file holds there.

    `Any`, not `int`: `@code` is read from the file and every other reader
    of it coerces (`command_rules`, `references` and `assets` all go
    through `as_int`). This did not, and built its default eagerly, so a
    Ruby String in that slot raised `TypeError: %d format` and an Array
    raised `unhashable type` at the lookup -- making a whole map unreadable
    through `event_get` while `validate` reported nothing wrong with it.

    The default is built only when it is needed, which is the other half:
    every named code paid for a discarded `%d` format.
    """
    number = as_int(code)
    if number is None:
        # `brief`, because this value is out of the file: a repr of a
        # million-link Array is not a thing to put in a line of output.
        return "Unknown (%s)" % brief(code)
    known = COMMAND_NAMES.get(number)
    return known if known is not None else "Unknown (%d)" % number


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _text(value: Any, codec: TextCodec) -> str:
    """One value from a command's parameters, ready to put in a line.

    A wrapper over `decoded_text` rather than a decode of its own, and that is
    the fix: `analysis._text` and `registry.decoded` both went through it and
    this did not, so a rendered command line was the one text in the project
    with no bound on its length -- and `WalkBudget` charges a command list one
    step per command whatever those commands carry.
    """
    if isinstance(value, RubyObject):
        # The Ruby class is the useful answer for a parameter that is an
        # object. `decoded_text` would say `<RubyObject>`, which names this
        # reader's wrapper rather than anything that is in the file.
        return "<%s>" % decoded_text(codec, value.cls)
    return decoded_text(codec, value)


def _id_range(start: Any, end: Any, singular: str, plural: str) -> str:
    """``switch 3`` or ``switches 3..7``: these commands take an id range."""
    if start == end:
        return "%s %s" % (singular, start)
    return "%s %s..%s" % (plural, start, end)


def _param(params: list[Any], index: int, default: Any = None) -> Any:
    """One parameter, or ``default`` when the command does not carry it."""
    return params[index] if len(params) > index else default


class _Params:
    """One command's parameters, read by position with a default.

    A small callable object rather than the closure the `if` chain used, so
    that a renderer can take one argument and still reach the whole list:
    `describe_condition` wants the list, and the choices renderer wants
    `as_list(p(0))`.
    """

    __slots__ = ("params",)

    def __init__(self, params: list[Any]):
        self.params = params

    def __call__(self, index: int, default: Any = None) -> Any:
        return _param(self.params, index, default)


def _show_text_line(p: "_Params", codec: TextCodec, name: str) -> str:
    face = _text(p(0, ""), codec)
    detail = "face %s[%s]" % (face, p(1, 0)) if face else "no face"
    return "%s (%s)" % (name, detail)


def _choices_line(p: "_Params", codec: TextCodec, name: str) -> str:
    # `MAX_CHOICES` is the *authoring* ceiling; this list's length comes out
    # of the file, so it is not bounded by it. Rendering is capped rather
    # than the result, because a crafted Array of a million links to one
    # string is three megabytes on disk.
    return "%s: %s" % (name, rendered(as_list(p(0)),
                                      lambda c: _text(c, codec),
                                      separator=" / "))


def _variables_line(p: "_Params", codec: TextCodec, name: str) -> str:
    target = _id_range(p(0, 0), p(1, 0), "variable", "variables")
    kind = p(3)
    if kind == OPERAND_RANDOM:
        value = "random %s..%s" % (p(4), p(5))
    elif kind == OPERAND_VARIABLE:
        value = "variable %s" % p(4)
    elif kind in SHARED_VARIABLE_OPERANDS:
        value = _value(p(4), codec)
    else:
        # A kind this server does not claim to know: show it rather than
        # label the parameter as though the layout were confirmed.
        value = "operand kind %s: %s" % (kind, _value(p(4), codec))
    return "%s: %s %s %s" % (name, target,
                             VARIABLE_OPERATIONS.get(p(2), "?"), value)


def _transfer_line(p: "_Params", codec: TextCodec, name: str) -> str:
    if p(0) == DESIGNATION_VARIABLE:
        return ("%s: map from variable %s at (variable %s, variable %s)"
                % (name, p(TRANSFER_MAP), p(TRANSFER_X), p(TRANSFER_Y)))
    return ("%s: map %s at (%s, %s)"
            % (name, p(TRANSFER_MAP), p(TRANSFER_X), p(TRANSFER_Y)))


def _battle_line(p: "_Params", codec: TextCodec, name: str) -> str:
    if p(0) == BATTLE_DESIGNATION_RANDOM:
        # No troop id at all: the engine picks from the map's encounter list.
        return "%s: random encounter" % name
    if p(0) == DESIGNATION_VARIABLE:
        return "%s: troop from variable %s" % (name, p(1))
    return "%s: troop %s" % (name, p(1))


def _move_step_line(p: "_Params", codec: TextCodec, name: str) -> str:
    move = p(0)
    if isinstance(move, RubyObject):
        move_code = move.get("code", CODE_BLANK)
        return "> %s" % MOVE_COMMAND_NAMES.get(move_code,
                                               "Move %s" % move_code)
    return "> Move Command"


# The codes `summarize` renders specially, and how. Keyed by code, which is
# what the ninety-eight-line `if` chain this replaced dispatched on by hand
# -- and seven of those tests named a bare integer in the module that
# defines the constants. As table keys they are named once, and
# `TestACodeWithAConstantIsSpelledWithIt` reads the keys.
#
# Every renderer takes the same three arguments and ignores what it does not
# need, so the table can be walked without asking which shape each has --
# the arrangement `_COMPILERS` uses for the compiler, with the same rule for
# lambda versus named function: a lambda when the whole rendering is one
# expression, a named function as soon as it needs a statement.
#
# The two set-membership arms and the spec fallback are deliberately not
# here: a renderer is chosen by equality, while "any audio command" and "any
# code with a declared parameter spec" are different questions, asked in
# `summarize` in the order they were asked before.
_SUMMARY_RENDERERS: "dict[int, Callable[..., str]]" = {
    CODE_BLANK: lambda p, codec, name: "",
    CODE_TEXT_LINE: lambda p, codec, name: '"%s"' % _text(p(0, ""), codec),
    CODE_COMMENT_LINE: lambda p, codec, name: '"%s"' % _text(p(0, ""), codec),
    CODE_SCRIPT_MORE: lambda p, codec, name: '"%s"' % _text(p(0, ""), codec),
    CODE_SHOW_TEXT: _show_text_line,
    CODE_COMMENT: lambda p, codec, name: (
        "Comment: %s" % _text(p(0, ""), codec)),
    CODE_SHOW_CHOICES: _choices_line,
    CODE_CHOICE_BRANCH: lambda p, codec, name: (
        'When "%s"' % _text(p(1, ""), codec)),
    CODE_CONDITIONAL_BRANCH: lambda p, codec, name: (
        "%s: %s" % (name, describe_condition(p.params, codec))),
    CODE_CONTROL_SWITCHES: lambda p, codec, name: (
        "%s: %s = %s" % (name,
                         _id_range(p(0, 0), p(1, 0), "switch", "switches"),
                         SWITCH_OPERATIONS.get(p(2), p(2)))),
    CODE_CONTROL_VARIABLES: _variables_line,
    CODE_CONTROL_SELF_SWITCH: lambda p, codec, name: (
        "%s: %s = %s" % (name, _text(p(0, ""), codec),
                         SWITCH_OPERATIONS.get(p(1), p(1)))),
    CODE_CALL_COMMON_EVENT: lambda p, codec, name: "%s: %s" % (name, p(0)),
    CODE_TRANSFER_PLAYER: _transfer_line,
    CODE_SET_MOVE_ROUTE: lambda p, codec, name: (
        "%s: %s" % (name, _character(p(0)))),
    CODE_WAIT: lambda p, codec, name: "%s: %s frames" % (name, p(0)),
    CODE_BATTLE_PROCESSING: _battle_line,
    CODE_SCRIPT: lambda p, codec, name: (
        "%s: %s" % (name, _text(p(0, ""), codec))),
    CODE_MOVE_ROUTE_STEP: _move_step_line,
    CODE_LABEL: lambda p, codec, name: (
        "%s: %s" % (name, _text(p(0, ""), codec))),
    CODE_JUMP_TO_LABEL: lambda p, codec, name: (
        "%s: %s" % (name, _text(p(0, ""), codec))),
}


def summarize(command: RubyObject, codec: TextCodec) -> str:
    """One readable line describing a single command.

    Deliberately engine-independent: rendering names a code from the superset,
    because naming a code the open engine happens not to dispatch is harmless
    while failing to name one it does is not.
    """
    # Coerced before it is used as a key: dispatch is equality on an int,
    # and `dict.get` raises for an unhashable value rather than missing.
    # A code that is not a whole number gets named and nothing else --
    # `command_name` is the one function here that takes anything.
    raw = command.get("code", CODE_BLANK)
    code = CODE_BLANK if raw is None else as_int(raw)
    if code is None:
        return command_name(raw)
    # `as_list`, not `or []`: a non-empty RubyHash here is truthy and
    # reaches `params[index]` as a TypeError.
    params = as_list(command.get("parameters"))
    name = command_name(code)
    p = _Params(params)

    render = _SUMMARY_RENDERERS.get(code)
    if render is not None:
        return render(p, codec, name)
    if code in AUDIO_COMMAND_KINDS:
        audio = p(0)
        audio_name = (_text(audio.get("name"), codec)
                      if isinstance(audio, RubyObject) else "?")
        return "%s: %s" % (name, audio_name)

    spec = ALL_PARAM_SPECS.get(code)
    if spec and spec[-1] != VARIADIC:
        pairs = ", ".join(
            "%s=%s" % (field, _text(params[i], codec))
            for i, field in enumerate(spec) if len(params) > i
        )
        return "%s (%s)" % (name, pairs) if pairs else name
    if params:
        # Numbers and flags read better unquoted than in a Python list repr.
        # Capped for the same reason as the choices above: a command with no
        # declared spec passes its parameters through untouched, so the count
        # is whatever the file says.
        return "%s (%s)" % (name, rendered(params,
                                           lambda v: _value(v, codec)))
    return name


def _value(value: Any, codec: TextCodec) -> str:
    if isinstance(value, RubyString):
        return '"%s"' % decoded_text(codec, value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _text(value, codec)


def describe_condition(params: list[Any], codec: TextCodec) -> str:
    """Readable form of a Conditional Branch (code 111) parameter list."""
    if not params:
        return "?"
    kind = params[0]

    def p(index: int, default: Any = None) -> Any:
        return _param(params, index, default)

    if kind == COND_SWITCH:
        return "switch %s is %s" % (p(1), SWITCH_OPERATIONS.get(p(2), p(2)))
    if kind == COND_VARIABLE:
        operand = "variable %s" % p(3) if p(2) == 1 else p(3)
        return "variable %s %s %s" % (p(1), COMPARISONS.get(p(4), "?"), operand)
    if kind == COND_SELF_SWITCH:
        return "self switch %s is %s" % (_text(p(1, ""), codec),
                                         SWITCH_OPERATIONS.get(p(2), p(2)))
    if kind == COND_TIMER:
        return "timer %s %ss" % (">=" if p(2) == 0 else "<=", p(1))
    if kind == COND_ACTOR:
        return "actor %s (check type %s)" % (p(1), p(2))
    if kind == COND_ENEMY:
        return "enemy %s (check type %s)" % (p(1), p(2))
    if kind == COND_CHARACTER:
        return "character %s faces %s" % (_character(p(1)), p(2))
    if kind == COND_GOLD:
        return "gold %s %s" % (">=" if p(2) == 0 else "<=", p(1))
    if kind == COND_ITEM:
        return "party has item %s" % p(1)
    if kind == COND_WEAPON:
        return "party has weapon %s" % p(1)
    if kind == COND_ARMOR:
        return "party has armor %s" % p(1)
    if kind == COND_BUTTON:
        return "button %s pressed" % _text(p(1, ""), codec)
    if kind == COND_SCRIPT:
        return "script: %s" % _text(p(1, ""), codec)
    # `brief`, not `%s`: this renders the rest of a list whose length and
    # whose elements both come out of the file, and `%s` on a list calls
    # `repr` on every element with nothing capping either. The kind is what
    # selects this branch, so a crafted map picks it.
    return "condition type %s %s" % (brief(kind), brief(params[1:]))


def _character(value: Any) -> str:
    if isinstance(value, int):
        return CHARACTER_TARGETS.get(value, "event %d" % value)
    return str(value)


def render_script(commands: list[Any], codec: TextCodec) -> list[str]:
    """Render a command list as numbered, indented lines for reading."""
    lines = []
    for index, command in enumerate(commands):
        if not isinstance(command, RubyObject):
            # `brief`, for the reason the branch exists: `@list` is an
            # Array out of the file and nothing says its elements are
            # commands, so one element can be an Array of a million links.
            lines.append("%3d| <not an RPG::EventCommand: %s>"
                         % (index, brief(command)))
            continue
        indent = command.get("indent", 0) or 0
        if not isinstance(indent, int) or isinstance(indent, bool):
            indent = 0
        # Read from the file, so clamp: a corrupt indent of 2**30 would build a
        # two-gigabyte string for one line of output.
        indent = max(0, min(indent, MAX_RENDER_INDENT))
        text = summarize(command, codec)
        lines.append("%3d| %s%s" % (index, "  " * indent, text))
    return lines


# ---------------------------------------------------------------------------
# Authoring DSL
# ---------------------------------------------------------------------------


MESSAGE_LINES_PER_BOX = 4

# Authored entries nest through if/loop/choices bodies. Real events are a few
# levels deep; the cap keeps a hostile payload from exhausting the stack.
MAX_DSL_DEPTH = 32


def compile_commands(entries: list[dict[str, Any]], codec: TextCodec,
                     indent: int = 0, depth: int = 0) -> list[RubyObject]:
    """Compile DSL entries into an ``RPG::EventCommand`` list.

    The result deliberately does *not* include the trailing blank command; the
    caller decides, because a common event's list and an event page's list are
    assembled differently.
    """
    if depth > MAX_DSL_DEPTH:
        raise CommandError(
            "command entries nest more than %d levels deep; that is deeper than "
            "any event needs" % MAX_DSL_DEPTH
        )
    if not isinstance(entries, list):
        raise CommandError("commands must be an array, got %r" % (entries,))
    out: list[RubyObject] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CommandError("each command entry must be an object, got %r" % (entry,))
        recognised = sorted(SUPPORTED_KEYS & set(entry))
        if len(recognised) > 1:
            # First-key-wins would silently compile one and drop the other,
            # which is a trap for a machine writing these.
            raise CommandError(
                "command entry names more than one command: %s. Split it into "
                "one entry per command." % ", ".join(recognised)
            )
        out.extend(_compile_entry(entry, codec, indent, depth))
    return out


def _cmd(code: int, indent: int, params: list[Any] | None = None) -> RubyObject:
    return new_event_command(code, indent, params or [])


def _compile_entry(entry: dict[str, Any], enc: TextCodec, indent: int,
                   depth: int = 0) -> list[RubyObject]:
    for key, compile_one in _COMPILERS.items():
        if key in entry:
            return compile_one(entry, enc, indent, depth)

    raise CommandError(
        "unrecognised command entry with keys %s; supported keys are %s, or use "
        '{"raw": {"code": .., "parameters": [..]}} for any other command'
        % (sorted(entry), ", ".join(sorted(SUPPORTED_KEYS)))
    )


def _compile_raw(entry: dict[str, Any], enc: TextCodec, indent: int,
                 depth: int) -> list[RubyObject]:
    """Any command at all, by code and parameters.

    The escape hatch for a command this DSL has no form for. An entry may
    not carry `raw` *and* another recognised key -- `compile_commands`
    refuses that rather than choosing -- so this being first in the table
    means nothing.
    """
    raw = _needs_object(entry["raw"], "raw")
    if "code" not in raw:
        raise CommandError('a "raw" entry needs a "code"')
    code = _needs_int(raw["code"], "raw.code")
    given = raw.get("indent", indent)
    raw_indent = _needs_int(given, "raw.indent")
    if not 0 <= raw_indent <= MAX_AUTHORED_INDENT:
        raise CommandError(
            "raw.indent must be between 0 and %d, got %d"
            % (MAX_AUTHORED_INDENT, raw_indent))
    params = raw.get("parameters", [])
    if not isinstance(params, list):
        raise CommandError("raw.parameters must be an array, got %r" % (params,))
    return [_cmd(code, raw_indent, [_literal(v, enc) for v in params])]


def _compile_switch(entry: dict[str, Any], enc: TextCodec, indent: int,
                    depth: int) -> list[RubyObject]:
    """Control Switches, over one switch or a range of them."""
    state = _switch_state(entry)
    switch_id = _needs_int(entry["switch"], "switch")
    end = _needs_int(entry.get("switch_end", switch_id), "switch_end")
    return [_cmd(CODE_CONTROL_SWITCHES, indent, [switch_id, end, state])]


def _compile_self_switch(entry: dict[str, Any], enc: TextCodec, indent: int,
                         depth: int) -> list[RubyObject]:
    """Control Self Switch: A, B, C or D on this event."""
    state = _switch_state(entry)
    return [_cmd(CODE_CONTROL_SELF_SWITCH, indent,
                 [enc.make_string(str(entry["self_switch"])), state])]


def _compile_loop(entry: dict[str, Any], enc: TextCodec, indent: int,
                  depth: int) -> list[RubyObject]:
    """A loop, and the Repeat Above that closes it."""
    body = compile_commands(entry["loop"], enc, indent + 1, depth + 1)
    return [_cmd(CODE_LOOP, indent, []), *body,
            _cmd(CODE_REPEAT_ABOVE, indent, [])]


def _compile_transfer(entry: dict[str, Any], enc: TextCodec, indent: int,
                      depth: int) -> list[RubyObject]:
    """Transfer Player, with every parameter checked as an integer."""
    t = _needs_object(entry["transfer"], "transfer")
    # `_needs_int` for all six, not just the ones that read as ids: a
    # Transfer Player destination is what this command *is*, and
    # `int(2.9)` is 2 -- so a wrong-typed y moved the player one tile
    # from where the caller said and the reply reported success.
    return [_cmd(CODE_TRANSFER_PLAYER, indent, [
        _needs_int(t.get("designation", 0), "transfer.designation"),
        _needs_int(t["map_id"], "transfer.map_id"),
        _needs_int(t["x"], "transfer.x"),
        _needs_int(t["y"], "transfer.y"),
        _needs_int(t.get("direction", 0), "transfer.direction"),
        _needs_int(t.get("fade", 0), "transfer.fade"),
    ])]


def _multiline(first_code: int, continuation_code: int, text: str,
               enc: TextCodec, indent: int) -> list[RubyObject]:
    """A command whose extra lines are separate continuation commands.

    Comments (108 then 408) and scripts (355 then 655) share this shape. No
    trailing newline is added: every one of the 65 script commands in a real
    editor-written project stores its line without one.
    """
    lines = text.split("\n")
    out = [_cmd(first_code, indent, [enc.make_string(lines[0])])]
    out += [_cmd(continuation_code, indent, [enc.make_string(line)])
            for line in lines[1:]]
    return out


def _compile_text(entry: dict[str, Any], enc: TextCodec,
                  indent: int) -> list[RubyObject]:
    text = entry["text"]
    if isinstance(text, str):
        lines = text.split("\n")
    elif isinstance(text, list):
        lines = [str(line) for line in text]
    else:
        raise CommandError("text must be a string or an array of lines, got %r"
                           % (text,))
    face_name = str(entry.get("face_name", ""))
    face = (_needs_int(entry.get("face_index", 0), "face_index"),
            _needs_int(entry.get("background", 0), "background"),
            _needs_int(entry.get("position", 2), "position"))
    out: list[RubyObject] = []
    # The message window shows four lines; longer text becomes several boxes.
    for start in range(0, max(len(lines), 1), MESSAGE_LINES_PER_BOX):
        chunk = lines[start:start + MESSAGE_LINES_PER_BOX] or [""]
        # A fresh string per box: sharing one would make the writer emit an
        # object link and the engine load one shared, mutable String.
        out.append(_cmd(CODE_SHOW_TEXT, indent,
                        [enc.make_string(face_name), *face]))
        out += [_cmd(CODE_TEXT_LINE, indent, [enc.make_string(line)])
                for line in chunk]
    return out


def _compile_variable(entry: dict[str, Any], indent: int) -> list[RubyObject]:
    """Control Variables (122) from a DSL entry.

    Every id here goes through `_needs_int`, not `int()`. `int(True)` is 1 and
    `int(2.9)` is 2, so a wrong-typed id addressed a different game variable
    than the caller named -- and quietly, because the command was then written
    and reported as a success against the coerced id. `registry.as_int` says
    the same thing about database ids; this file already applied the rule to
    switch, self_switch, face_index and indent, and not to the variable ids.
    """
    variable_id = _needs_int(entry["variable"], "variable")
    end = _needs_int(entry.get("variable_end", variable_id), "variable_end")
    operation = entry.get("op", entry.get("operation", "set"))
    op_code = VARIABLE_OP_CODES.get(operation)
    if op_code is None:
        raise CommandError(
            "unknown variable operation %r; use one of %s"
            % (operation, ", ".join(sorted(VARIABLE_OP_CODES)))
        )
    if "from_variable" in entry:
        operand_type = OPERAND_VARIABLE
        operand = _needs_int(entry["from_variable"], "from_variable")
        operand2 = 0
    elif "random" in entry:
        bounds = entry["random"]
        # Shape-checked before unpacking: `low, high = entry["random"]` raised
        # a bare ValueError on anything that was not a pair, and server.py
        # reports an unexpected exception as "an argument was not the shape
        # the tool expected" -- true, but it names neither the argument nor
        # the shape.
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise CommandError(
                "random takes two whole numbers, [low, high]; got %r"
                % (bounds,))
        operand_type = OPERAND_RANDOM
        operand = _needs_int(bounds[0], "random low")
        operand2 = _needs_int(bounds[1], "random high")
    else:
        operand_type = OPERAND_CONSTANT
        operand = _needs_int(entry.get("value", 0), "value")
        operand2 = 0
    return [_cmd(CODE_CONTROL_VARIABLES, indent,
                 [variable_id, end, op_code, operand_type, operand,
                  operand2])]


def _compile_if(entry: dict[str, Any], enc: TextCodec, indent: int,
                depth: int = 0) -> list[RubyObject]:
    params = _compile_condition(_needs_object(entry["if"], "if"), enc)
    out = [_cmd(CODE_CONDITIONAL_BRANCH, indent, params)]
    out += compile_commands(entry.get("then", []), enc, indent + 1, depth + 1)
    if entry.get("else"):
        out.append(_cmd(CODE_ELSE, indent, []))
        out += compile_commands(entry["else"], enc, indent + 1, depth + 1)
    out.append(_cmd(CODE_END_BRANCH, indent, []))
    return out


def _compile_condition(cond: dict[str, Any], enc: TextCodec) -> list[Any]:
    if "switch" in cond:
        return [COND_SWITCH, _needs_int(cond["switch"], "switch"),
                _switch_state(cond)]
    if "self_switch" in cond:
        return [COND_SELF_SWITCH, enc.make_string(str(cond["self_switch"])),
                _switch_state(cond)]
    if "variable" in cond:
        comparison = COMPARISON_CODES.get(str(cond.get("compare", ">=")))
        if comparison is None:
            raise CommandError(
                "unknown comparison %r; use one of %s"
                % (cond.get("compare"), ", ".join(COMPARISON_CODES))
            )
        if "from_variable" in cond:
            # In the same statement as the `_needs_int` two lines down, and
            # bare: `{"variable": 3, "from_variable": 2.9}` compared against
            # variable 2 and answered "rewrote page 0 of event 1".
            operand_type, operand = 1, _needs_int(cond["from_variable"],
                                                  "from_variable")
        else:
            operand_type, operand = 0, _needs_int(cond.get("value", 0),
                                                  "value")
        return [COND_VARIABLE, _needs_int(cond["variable"], "variable"),
                operand_type, operand, comparison]
    if "script" in cond:
        return [COND_SCRIPT, enc.make_string(str(cond["script"]))]
    raise CommandError(
        'condition must use switch, self_switch, variable or script; for other '
        'condition types build the 111 command with {"raw": ...}'
    )


def _compile_choices(entry: dict[str, Any], enc: TextCodec, indent: int,
                     depth: int = 0) -> list[RubyObject]:
    if not isinstance(entry["choices"], list):
        raise CommandError("choices must be an array, got %r" % (entry["choices"],))
    choices = list(entry["choices"])
    if not 1 <= len(choices) <= MAX_CHOICES:
        raise CommandError("the engine allows 1 to %d choices, got %d"
                           % (MAX_CHOICES, len(choices)))
    # Tested before `as_list`, which answers `[]` for anything that is not
    # a list -- so this refusal was unreachable and
    # `{"choices": ["a"], "branches": "nope"}` compiled as though no
    # branches had been asked for, reporting the same success as a page
    # that got the branches it named.
    given = entry.get("branches")
    if given is not None and not isinstance(given, list):
        raise CommandError("branches must be an array of command lists, "
                           "got %r" % (given,))
    branches = as_list(given)
    if len(branches) > len(choices):
        # Refused, not truncated. `body = branches[index] if index <
        # len(branches) else []` compiled the first len(choices) and dropped
        # the rest, and the reply then said the page had been rewritten -- the
        # same success a caller gets when nothing was dropped, so it will not
        # retry what it believes already happened. Fewer branches than choices
        # is fine: a choice with an empty body is a real thing to author.
        raise CommandError(
            "there are %d branches for %d choices, and branches pair with "
            "choices by position, so %d of them would be dropped. Give one "
            "branch per choice (an empty list for a choice that does "
            "nothing), or use cancel_branch for the cancel path."
            % (len(branches), len(choices), len(branches) - len(choices)))
    cancel_branch = entry.get("cancel_branch")
    # parameters[1] selects cancel behaviour: 0 disallows cancelling, 1..4
    # picks that choice, 5 routes to a When Cancel branch. An explicit value
    # wins over the default this picks from `cancel_branch`.
    #
    # `_needs_int`, not `int()`: this was the one caller-supplied number in
    # this file still coerced, and `int(2.9)` is 2 while `int(True)` is 1 --
    # so a wrong-typed cancel selected a different choice than the caller
    # named and the reply said the page had been rewritten. `int("x")` was
    # worse: a bare ValueError, which the server reports as "an argument was
    # not the shape the tool expected" without naming the argument.
    #
    # And range-checked, because the sentence above has always said what the
    # range is while nothing held it: 99 wrote a value the interpreter has no
    # branch for, and reported success.
    cancel = _needs_int(entry.get("cancel", 5 if cancel_branch is not None
                                  else 0), "cancel")
    if not 0 <= cancel <= MAX_CANCEL_KIND:
        raise CommandError(
            "cancel must be 0 to %d: 0 disallows cancelling, 1 to %d picks "
            "that choice, and %d routes to the cancel_branch. Got %r."
            % (MAX_CANCEL_KIND, MAX_CHOICES, MAX_CANCEL_KIND, cancel))
    out = [_cmd(CODE_SHOW_CHOICES, indent,
                [[enc.make_string(str(c)) for c in choices], cancel])]
    for index, choice in enumerate(choices):
        out.append(_cmd(CODE_CHOICE_BRANCH, indent,
                        [index, enc.make_string(str(choice))]))
        body = branches[index] if index < len(branches) else []
        out += compile_commands(body, enc, indent + 1, depth + 1)
    if cancel_branch is not None:
        out.append(_cmd(CODE_WHEN_CANCEL, indent, []))
        out += compile_commands(cancel_branch, enc, indent + 1, depth + 1)
    out.append(_cmd(CODE_END_CHOICES, indent, []))
    return out


# Every entry key the DSL accepts, and what compiles it. One table rather
# than a chain of seventeen `if "key" in entry` tests with the same
# seventeen literals written again as a set below it: nothing held the two
# equal, so a branch added without the set under-reported what works, and a
# set entry added without the branch advertised a key that raises
# "unrecognised". Neither was caught.
#
# The order is immaterial, and saying otherwise here was wrong: an entry
# carrying two recognised keys is refused by `compile_commands` before this
# table is consulted, so no entry can ever match twice. The order is source
# order because a table reads better grouped than sorted.
#
# Every value takes the same four arguments and ignores what it does not
# need, so the table can be walked without asking which shape each has. A
# named compiler appears bare when it takes those four as they come; a
# lambda is what adapts one taking fewer, or spells out a body short enough
# that a named function would only give it a second name. Six were lambdas
# for neither reason: the table used to sit above half the compilers, so a
# lambda was standing in for the file's layout, and the rule it looked like
# it followed was not one anybody had chosen. It lives below all of them
# now.
_COMPILERS: "dict[str, Callable[..., list[RubyObject]]]" = {
    "raw": _compile_raw,
    "text": lambda entry, enc, indent, depth: _compile_text(
        entry, enc, indent),
    "comment": lambda entry, enc, indent, depth: _multiline(
        CODE_COMMENT, CODE_COMMENT_LINE, str(entry["comment"]), enc, indent),
    "script": lambda entry, enc, indent, depth: _multiline(
        CODE_SCRIPT, CODE_SCRIPT_MORE, str(entry["script"]), enc, indent),
    "switch": _compile_switch,
    "self_switch": _compile_self_switch,
    "variable": lambda entry, enc, indent, depth: _compile_variable(
        entry, indent),
    "wait": lambda entry, enc, indent, depth: [
        _cmd(CODE_WAIT, indent, [_needs_int(entry["wait"], "wait")])],
    "common_event": lambda entry, enc, indent, depth: [
        _cmd(CODE_CALL_COMMON_EVENT, indent,
             [_needs_int(entry["common_event"], "common_event")])],
    "label": lambda entry, enc, indent, depth: [
        _cmd(CODE_LABEL, indent, [enc.make_string(str(entry["label"]))])],
    "jump": lambda entry, enc, indent, depth: [
        _cmd(CODE_JUMP_TO_LABEL, indent,
             [enc.make_string(str(entry["jump"]))])],
    "transfer": _compile_transfer,
    "exit": lambda entry, enc, indent, depth: [
        _cmd(CODE_EXIT_EVENT, indent, [])],
    "if": _compile_if,
    "loop": _compile_loop,
    "break_loop": lambda entry, enc, indent, depth: [
        _cmd(CODE_BREAK_LOOP, indent, [])],
    "choices": _compile_choices,
}

# Derived, so it cannot disagree with what is dispatched.
SUPPORTED_KEYS = frozenset(_COMPILERS)


def _switch_state(mapping: dict[str, Any], default: str = "on") -> int:
    """The engine's switch operation code for a DSL entry or condition.

    Both ``value`` and ``is`` are accepted for the same thing, with ``value``
    winning; the two used to disagree about which took precedence depending on
    where they were read. The engine's convention is inverted from the obvious
    one: 0 turns a switch ON and 1 turns it OFF.
    """
    value = mapping.get("value", mapping.get("is", default))
    if isinstance(value, bool):
        return 0 if value else 1
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text in ("on", "true", "yes"):
        return 0
    if text in ("off", "false", "no"):
        return 1
    raise CommandError("expected on or off, got %r" % (value,))


def _needs_object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CommandError("%s must be an object, got %r" % (what, value))
    return value


def _needs_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError("%s must be a whole number, got %r" % (what, value))
    return value


def _literal(value: Any, enc: TextCodec) -> Any:
    """Convert a JSON literal from a ``raw`` entry into a Ruby value."""
    if isinstance(value, str):
        return enc.make_string(value)
    if isinstance(value, list):
        return [_literal(v, enc) for v in value]
    if isinstance(value, dict):
        return _literal_object(value, enc)
    return value


def _literal_object(value: dict[str, Any], enc: TextCodec) -> Any:
    """Build a Ruby object from ``{"__class__": "RPG::AudioFile", ...}``."""
    cls = value.get("__class__")
    if not cls:
        raise CommandError(
            'object parameters need a "__class__" key naming the Ruby class, got %s'
            % sorted(value)
        )
    if cls == "RPG::MoveRoute":
        return _build_move_route(value, enc)
    obj = RubyObject(cls)
    for key, item in value.items():
        if key != "__class__":
            obj.set(key, _literal(item, enc))
    return obj


def _build_move_route(value: dict[str, Any], enc: TextCodec) -> RubyObject:
    """Build an RPG::MoveRoute from a JSON object.

    The steps may be given as ``list`` (the field's real name) or ``commands``;
    both are accepted because the former reads oddly in hand-written JSON.
    """
    route = RubyObject("RPG::MoveRoute", {
        "@repeat": bool(value.get("repeat", False)),
        "@skippable": bool(value.get("skippable", False)),
        "@wait": bool(value.get("wait", False)),
        "@list": [],
    })
    steps = value.get("list", value.get("commands", []))
    if not isinstance(steps, list):
        raise CommandError("a move route's list must be an array, got %r" % (steps,))
    commands = [
        new_move_command(_needs_int(c.get("code", 0), "move code"),
                         [_literal(p, enc) for p in c.get("parameters", [])])
        for c in steps
    ]
    # A route ends with a blank command. Only add one if the caller has not --
    # a read-modify-write of an existing route already carries it.
    if not commands or (commands[-1].get("code") or 0) != 0:
        commands.append(new_move_command(0, []))
    route.set("list", commands)
    return route


def move_route_steps(route: RubyObject, indent: int) -> list[RubyObject]:
    """The 505 commands the editor writes beside a Set Move Route command."""
    steps = []
    for move in as_list(route.get("list")):
        if isinstance(move, RubyObject) and (move.get("code", 0) or 0) != 0:
            steps.append(_cmd(CODE_MOVE_ROUTE_STEP, indent, [move]))
    return steps


# How to write a command list for `compile_commands`, in the words two
# tools serve to the model: `event_edit`'s description, and `command_reference`'s
# "authoring" key. Here rather than in either of them because it describes
# what the compiler below accepts, so a form added there and not here is a
# form no caller is told about -- and a form described here and not there is
# worse, because a caller writes it and is refused.
DSL_HELP = (
    "Commands are written as a list of small objects, one per command. "
    'Supported forms: {"text": "line one\\nline two", "face_name": "Actor1"}; '
    '{"comment": "..."}; {"script": "ruby"}; {"switch": 5, "value": "on"}; '
    '{"switch": 5, "switch_end": 9, "value": "on"} sets a range of switches; '
    '{"self_switch": "A", "value": "on"}; '
    '{"variable": 3, "op": "add", "value": 1} (also from_variable, random); '
    '{"wait": 60}; {"common_event": 2}; {"label": "top"}; {"jump": "top"}; '
    '{"transfer": {"map_id": 2, "x": 5, "y": 6, "direction": 2, "fade": 0}}; '
    '{"exit": true}; {"break_loop": true}; '
    '{"if": {"switch": 1, "is": "on"}, "then": [...], "else": [...]}; '
    '{"loop": [...]}; '
    '{"choices": ["Yes", "No"], "branches": [[...], [...]], "cancel_branch": [...]}. '
    'Conditions accept switch, self_switch, variable (with compare and value or '
    'from_variable) and script. For any other command, use '
    '{"raw": {"code": 231, "parameters": [...]}} -- see the command_reference '
    "tool for codes, and read a working example from the project before "
    "guessing a parameter layout.\n\n"
    'Limits the compiler enforces: Show Choices takes 1 to 4 choices, and '
    '"branches" pairs with them by position. "text" may be a string with \\n '
    "or an array of lines, and is split into four-line message boxes "
    "automatically. Entries nest through then/else/loop/branches to a bounded "
    'depth. A "raw" entry inherits the surrounding indent unless it sets one.'
)
