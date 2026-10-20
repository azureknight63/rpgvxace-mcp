"""Event commands: the code table, a readable rendering, and an authoring DSL.

Command codes come from the ``Game_Interpreter`` that ships with each engine, so
:data:`ENGINE_COMMANDS` holds exactly the codes that engine dispatches, plus the
continuation codes that only ever appear in data files. VX Ace's set is VX's
plus fifteen interpreter commands and the continuation code 405 (see
:data:`ACE_ONLY_COMMANDS`).

Parameter layouts are given in :data:`PARAM_SPECS` for the commands that are
worth authoring directly. Codes missing from that table are still read, listed
and written -- their parameters are simply passed through untouched. When you
need one of them, read a working example out of the project first
(``event_get`` on an event that already uses it) rather than guessing.
"""

from __future__ import annotations

from typing import Any, Callable

from .convert import (MAX_JSON_DEPTH, MAX_TEXT_CHARS, TextCodec, brief,
                      decoded_text)
from .rubytypes import (
    Color,
    MAX_NAMES_LISTED,
    RGSSFloats,
    RubyObject,
    RubyString,
    RubySymbol,
    Tone,
    as_int,
    as_list,
    rendered,
)
from .schema import (
    EDITOR_IVAR_ORDER,
    in_editor_order,
    new_event_command,
    new_move_command,
    new_move_route,
)

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


# RPG Maker XP's codes: every `when` in its `Interpreter (part 2)`
# `execute_command`, named as its comments name them, plus the codes that
# only appear in data. Not VX's table with gaps: XP numbers much of it
# differently. Wait is 106 (VX 230), Set Move Route 209 with 509 step lines
# (VX 205 and 505), Set Event Location 202 (VX 203), Show Animation 207
# (VX 212), and 104 and 105 are Change Text Options and Button Input
# Processing where VX Ace has Select Item and Show Scrolling Text.
XP_COMMAND_NAMES: dict[int, str] = {
    0: "Blank",
    101: "Show Text",
    102: "Show Choices",
    103: "Input Number",
    104: "Change Text Options",
    105: "Button Input Processing",
    106: "Wait",
    108: "Comment",
    111: "Conditional Branch",
    112: "Loop",
    113: "Break Loop",
    115: "Exit Event Processing",
    116: "Erase Event",
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
    131: "Change Windowskin",
    132: "Change Battle BGM",
    133: "Change Battle End ME",
    134: "Change Save Access",
    135: "Change Menu Access",
    136: "Change Encounter",
    201: "Transfer Player",
    202: "Set Event Location",
    203: "Scroll Map",
    204: "Change Map Settings",
    205: "Change Fog Color Tone",
    206: "Change Fog Opacity",
    207: "Show Animation",
    208: "Change Transparent Flag",
    209: "Set Move Route",
    210: "Wait for Move's Completion",
    221: "Prepare for Transition",
    222: "Execute Transition",
    223: "Change Screen Color Tone",
    224: "Screen Flash",
    225: "Screen Shake",
    231: "Show Picture",
    232: "Move Picture",
    233: "Rotate Picture",
    234: "Change Picture Color Tone",
    235: "Erase Picture",
    236: "Set Weather Effects",
    241: "Play BGM",
    242: "Fade Out BGM",
    245: "Play BGS",
    246: "Fade Out BGS",
    247: "Memorize BGM/BGS",
    248: "Restore BGM/BGS",
    249: "Play ME",
    250: "Play SE",
    251: "Stop SE",
    301: "Battle Processing",
    302: "Shop Processing",
    303: "Name Input Processing",
    311: "Change HP",
    312: "Change SP",
    313: "Change State",
    314: "Recover All",
    315: "Change EXP",
    316: "Change Level",
    317: "Change Parameters",
    318: "Change Skills",
    319: "Change Equipment",
    320: "Change Actor Name",
    321: "Change Actor Class",
    322: "Change Actor Graphic",
    331: "Change Enemy HP",
    332: "Change Enemy SP",
    333: "Change Enemy State",
    334: "Enemy Recover All",
    335: "Enemy Appearance",
    336: "Enemy Transform",
    337: "Show Battle Animation",
    338: "Deal Damage",
    339: "Force Action",
    340: "Abort Battle",
    351: "Call Menu Screen",
    352: "Call Save Screen",
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
    509: "Move Command",
    601: "If Win",
    602: "If Escape",
    603: "If Lose",
    604: "End Battle",
    605: "Shop Goods",
    655: "Script Line",
}


ENGINE_COMMANDS: dict[str, dict[int, str]] = {
    "vx_ace": ACE_COMMAND_NAMES,
    "vx": BASE_COMMAND_NAMES,
    "xp": XP_COMMAND_NAMES,
}

# A superset used for rendering the VX family: naming a code that this engine
# happens not to dispatch is harmless, whereas failing to name one that it
# does is not. XP is not in it -- its 202 to 209 mean other things -- so XP
# is named from its own table (`command_name`'s engine).
COMMAND_NAMES: dict[int, str] = ACE_COMMAND_NAMES

# Codes that continue the command above them rather than standing alone.
# 405 carries the lines of Ace's Show Scrolling Text.
CONTINUATION_CODES = {401, 402, 403, 404, 405, 408, 411, 412, 413, 505,
                      601, 602, 603, 604, 605, 655}

# XP's: the same, with 509 for a route's step lines and no 405.
XP_CONTINUATION_CODES = (CONTINUATION_CODES - {405, 505}) | {509}

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
# The ceiling on the `indent` of a `raw` entry, which the caller sets by hand.
# A request's own limit, not a figure taken from the engine: the DSL's own
# forms compute their indent from how deeply they nest.
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
    # VX's command_212 and command_213 read two parameters; the wait flag
    # is VX Ace's, below. These two used to carry it here, for both.
    212: ["character_id", "animation_id"],
    213: ["character_id", "balloon_id"],
    214: [],
    221: [],
    222: [],
    223: ["tone", "duration", "wait"],
    224: ["color", "duration", "wait"],
    225: ["power", "speed", "duration", "wait"],
    230: ["frames"],
    231: ["number", "name", "origin", "designation", "x", "y", "zoom_x",
          "zoom_y", "opacity", "blend_type"],
    235: ["number"],
    241: ["audio"],
    245: ["audio"],
    249: ["audio"],
    250: ["audio"],
    301: ["designation", "troop_id", "can_escape", "can_lose"],
    # VX's command_302: `shop_purchase_only = @params[2]`, and every goods
    # entry is read as [kind, id]. This said price_type and price, which
    # are VX Ace's.
    302: ["goods_type", "goods_id", "purchase_only"],
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
    601: [],
    602: [],
    603: [],
    604: [],
    605: ["goods_type", "goods_id"],
    655: ["script"],
}

# Layouts that differ in VX Ace, or belong to commands only it has. Each
# one here was compared with editor-written VX Ace data.
ACE_PARAM_SPECS: dict[int, list[str]] = {
    # Ace gives every goods entry a price kind and a price, and moves the
    # "purchase only" flag to the end of the first. The 605 lines carry
    # four parameters: all 51 in a real project do.
    302: ["goods_type", "goods_id", "price_type", "price", "purchase_only"],
    605: ["goods_type", "goods_id", "price_type", "price"],
    212: ["character_id", "animation_id", "wait"],
    213: ["character_id", "balloon_id", "wait"],
    # parameters[1] is never read and the editor writes nil there.
    232: ["number", "unused", "origin", "designation", "x", "y", "zoom_x",
          "zoom_y", "opacity", "blend_type", "duration", "wait"],
    282: ["tileset_id"],
    324: ["actor_id", "nickname"],
    405: ["text"],
}


# Rendering uses every layout we know; a name is only shown for a parameter
# that is actually present.
ALL_PARAM_SPECS: dict[int, list[str]] = {**PARAM_SPECS, **ACE_PARAM_SPECS}

# XP's layouts, each read off the command_* method in XP's Interpreter that
# runs it, and -- where a copy of an XP game holds the command -- matching
# the parameter count of every instance there (300 Show Text with one, 130
# Transfer Player with six, 79 Set Move Route with two, 74 Wait with one, 8
# Show Picture with ten, 17 Shop Goods with two). A code missing here is
# one whose layout was not read, and `command_reference` says so.
XP_PARAM_SPECS: dict[int, list[str]] = {
    101: ["text"],
    102: ["choices", "cancel_type"],
    103: ["variable_id", "digits"],
    104: ["position", "frame"],
    105: ["variable_id"],
    106: ["frames"],
    108: ["text"],
    111: ["type", VARIADIC],
    117: ["common_event_id"],
    118: ["label"],
    119: ["label"],
    121: PARAM_SPECS[121],
    122: PARAM_SPECS[122],
    123: PARAM_SPECS[123],
    124: PARAM_SPECS[124],
    125: PARAM_SPECS[125],
    126: PARAM_SPECS[126],
    127: ["weapon_id", "operation", "operand_type", "value"],
    128: ["armor_id", "operation", "operand_type", "value"],
    129: PARAM_SPECS[129],
    131: ["windowskin_name"],
    132: ["audio"],
    133: ["audio"],
    201: PARAM_SPECS[201],
    202: ["character_id", "designation", "x", "y", "direction"],
    203: ["direction", "distance", "speed"],
    205: ["tone", "duration"],
    206: ["opacity", "duration"],
    207: ["character_id", "animation_id"],
    209: ["character_id", "move_route"],
    221: [],
    222: ["transition_name"],
    223: ["tone", "duration"],
    224: ["color", "duration"],
    225: ["power", "speed", "duration"],
    231: PARAM_SPECS[231],
    232: ["number", "duration", "origin", "designation", "x", "y", "zoom_x",
          "zoom_y", "opacity", "blend_type"],
    235: ["number"],
    241: ["audio"],
    242: ["seconds"],
    245: ["audio"],
    246: ["seconds"],
    249: ["audio"],
    250: ["audio"],
    301: ["troop_id", "can_escape", "can_lose"],
    302: ["goods_type", "goods_id"],
    303: ["actor_id", "max_characters"],
    314: ["actor_id"],
    340: [],
    351: [],
    352: [],
    353: [],
    354: [],
    355: ["script"],
    401: ["text"],
    402: ["index", "text"],
    408: ["text"],
    509: ["move_command"],
    601: [],
    602: [],
    603: [],
    604: [],
    605: ["goods_type", "goods_id"],
    655: ["script"],
}


def command_table(engine: str) -> dict[int, str]:
    """The codes one engine's interpreter dispatches."""
    return ENGINE_COMMANDS.get(engine, ACE_COMMAND_NAMES)


def param_specs(engine: str) -> dict[int, list[str]]:
    """Confirmed parameter layouts for one engine."""
    if engine == "xp":
        return XP_PARAM_SPECS
    return PARAM_SPECS if engine == "vx" else ALL_PARAM_SPECS


def continuation_codes(engine: str) -> set[int]:
    """The codes that continue the command above them, on one engine."""
    return XP_CONTINUATION_CODES if engine == "xp" else CONTINUATION_CODES

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
CODE_SHOW_TEXT = 101    # VX and Ace: parameters[0] is the face graphic name;
                        # XP's layout is just the text (XP_PARAM_SPECS[101])
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

# XP's Set Move Route and the step line its editor writes after it.
XP_SET_MOVE_ROUTE, XP_MOVE_ROUTE_STEP = 209, 509

# The codes the layout forms below compile to and `_SUMMARY_RENDERERS`
# reads back. The four Play commands are not here: `AUDIO_COMMAND_KINDS`
# already names them by kind, and the form reaches them through it.
CODE_CHANGE_GOLD = 125
CODE_CHANGE_ITEMS = 126
CODE_CHANGE_WEAPONS = 127
CODE_CHANGE_ARMOR = 128
CODE_CHANGE_PARTY = 129
CODE_SET_EVENT_LOCATION = 203
CODE_SHOW_ANIMATION = 212
CODE_SHOW_BALLOON = 213
CODE_FADEOUT_SCREEN = 221
CODE_FADEIN_SCREEN = 222
CODE_TINT_SCREEN = 223
CODE_FLASH_SCREEN = 224
CODE_SHAKE_SCREEN = 225
CODE_SHOW_PICTURE = 231
CODE_MOVE_PICTURE = 232
CODE_ERASE_PICTURE = 235
CODE_SHOP = 302
CODE_NAME_INPUT = 303
CODE_IF_WIN = 601
CODE_IF_ESCAPE = 602
CODE_IF_LOSE = 603
CODE_END_BATTLE = 604
CODE_SHOP_GOODS = 605

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

# Move route command codes. Confirmed, not advisory: these are the `when`
# arms of `Game_Character#process_move_command` in VX Ace and of
# `Game_Character#move_type_custom` in VX, which dispatch the same 46 codes
# the same way, and the 963 step lines in a real VX Ace project use them as
# named. 39 and 40 were "Always on Top" here, which is XP's meaning; both
# engines set `@transparent`.
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
    38: "Through OFF", 39: "Transparent ON", 40: "Transparent OFF",
    41: "Change Graphic", 42: "Change Opacity", 43: "Change Blending",
    44: "Play SE", 45: "Script",
}

# Character targets used by 203/205/212 and friends.
CHARACTER_TARGETS = {-1: "player", 0: "this event"}


class CommandError(ValueError):
    """Raised when a command entry cannot be compiled."""


def command_name(code: Any, engine: str = "vx_ace") -> str:
    """The editor's name for a command code, whatever the file holds there.

    `Any`, not `int`: `@code` is read from the file and every other reader
    of it coerces (`command_rules`, `references` and `assets` all go
    through `as_int`). This did not, and built its default eagerly, so a
    Ruby String in that slot raised `TypeError: %d format` and an Array
    raised `unhashable type` at the lookup -- making a whole map unreadable
    through `event_get` while `validate` reported nothing wrong with it.

    The default is built only when it is needed, which is the other half:
    every named code paid for a discarded `%d` format.

    ``engine`` picks XP's own table for an XP project; every other engine
    is named from the VX family's superset.
    """
    number = as_int(code)
    if number is None:
        # `brief`, because this value is out of the file: a repr of a
        # million-link Array is not a thing to put in a line of output.
        return "Unknown (%s)" % brief(code)
    known = (XP_COMMAND_NAMES if engine == "xp" else COMMAND_NAMES).get(number)
    # `_display_safe`: `as_int` accepts an Integer of any size, and `%d` of
    # one past 4300 digits raises.
    return known if known is not None else "Unknown (%s)" % _display_safe(
        number)


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
    return decoded_text(codec, _display_safe(value))


def _id_range(start: Any, end: Any, singular: str, plural: str) -> str:
    """``switch 3`` or ``switches 3..7``: these commands take an id range."""
    if start == end:
        return "%s %s" % (singular, start)
    return "%s %s..%s" % (plural, start, end)


def _param(params: list[Any], index: int, default: Any = None) -> Any:
    """One parameter, or ``default`` when the command does not carry it."""
    return params[index] if len(params) > index else default


# A parameter is whatever the file put in its slot, and two things Marshal
# writes in a few bytes defeat the renderers that print or look one up: an
# Integer of thousands of digits (CPython refuses to turn one into a string
# at all) and an Array (unhashable as a `dict.get` key, and as long as its
# `repr` is wide). The ones that read a slot through `p` get neither.
MAX_PARAM_BITS = 64
# How many entries of an Array out of the file a renderer can reach. The
# longest list a renderer shows is `MAX_NAMES_LISTED`; the rest is a count.
MAX_PARAM_ITEMS = 100


class _HugeInteger(int):
    """An Integer past a Fixnum, standing in for itself.

    Clamped to one past the Fixnum range, so every comparison with a table
    key or an id still comes out the way the real value would, and `%d`
    prints twenty digits; `%s` and `repr` say how big it was instead. Two of
    them are equal only if the values they stand for are: `_id_range` folds
    `start == end` into one id, and 2**70..2**80 is not one.
    """

    def __new__(cls, value: int):
        clamped = 1 << MAX_PARAM_BITS
        shown = super().__new__(cls, clamped if value > 0 else -clamped)
        shown.bits = value.bit_length()
        shown.original = value
        return shown

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, _HugeInteger):
            return self.original == other.original
        return int.__eq__(self, other)

    def __ne__(self, other: Any) -> bool:
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = int.__hash__

    def __repr__(self) -> str:
        return "<Integer, %d bits>" % self.bits

    __str__ = __repr__


class _BoundedList(list):
    """An Array out of the file, safe to print and to look up, and cheap.

    It holds a short prefix and remembers how long the whole was: Marshal
    links let one Array of a million entries sit in the slot of every command
    for a few bytes each, so a copy per command would turn one step of a walk
    budget into the length of the Array. `len` is the true length, which is
    all `rendered` and `reprlib` ask of what they do not show; indexing and
    iteration reach the prefix. A hash, so `TABLE.get(array, default)` misses
    instead of raising `unhashable`, and a `repr` that stops where `brief`
    does.
    """

    def __init__(self, value: list[Any]):
        super().__init__(value[:MAX_PARAM_ITEMS])
        self._total = len(value)

    def __len__(self) -> int:
        return self._total

    def __repr__(self) -> str:
        return brief(self)

    __str__ = __repr__

    def __hash__(self) -> int:
        return 0


# Named for what it stands in for. `decoded_text` says `<list>` of a list by
# its type's name, and `reprlib` (so `brief`) picks its bounded list renderer
# by the same name: under its own name the wrapper put `<_BoundedList>` in a
# line and truncated a long list a second time with a different marker.
_BoundedList.__name__ = "list"
_BoundedList.__qualname__ = "list"
# Python 3.12's `reprlib` also asks the module: it dispatches on a type's name
# only when the type claims to come from `builtins`, as a subclass that
# defined its own `__repr__` would not. Measured on CI (3.12.10): with the
# name alone, `brief` fell through to `<list instance at 0x...>`.
_BoundedList.__module__ = "builtins"


def _display_safe(value: Any) -> Any:
    """The value a renderer may print, or look up, without failing."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value.bit_length() > MAX_PARAM_BITS:
        return _HugeInteger(value)
    if type(value) is list:
        return _BoundedList(value)
    if isinstance(value, RubySymbol) and len(value) > MAX_TEXT_CHARS:
        # A name is a few characters; a megabyte one is a value to say the
        # length of, and `%s` would copy all of it into the line.
        return "%s... (%d characters)" % (str(value)[:MAX_TEXT_CHARS],
                                          len(value))
    return value


class _Params:
    """One command's parameters, read by position with a default.

    A small callable object rather than the closure the `if` chain used, so
    that a renderer can take one argument and still reach the whole list --
    `describe_condition` takes the list, being callable from outside this
    module. One example, not two: the choices renderer's `as_list(p(0))`
    was the other, and that goes through `__call__` like any other slot.

    Every slot is read through `_display_safe`, once, so a renderer that
    formats or looks up what it reads cannot be stopped by what the file
    put there. `params` is the file's own list, for the code that wants it.
    """

    __slots__ = ("params", "_safe")

    def __init__(self, params: list[Any]):
        self.params = params
        self._safe: dict[int, Any] = {}

    def __call__(self, index: int, default: Any = None) -> Any:
        if len(self.params) <= index:
            return default
        if index not in self._safe:
            self._safe[index] = _display_safe(self.params[index])
        return self._safe[index]


def _show_text_line(p: _Params, codec: TextCodec, name: str) -> str:
    face = _text(p(0, ""), codec)
    detail = "face %s[%s]" % (face, p(1, 0)) if face else "no face"
    return "%s (%s)" % (name, detail)


def _choices_line(p: _Params, codec: TextCodec, name: str) -> str:
    # `MAX_CHOICES` is the *authoring* ceiling; this list's length comes out
    # of the file, so it is not bounded by it. Rendering is capped rather
    # than the result, because a crafted Array of a million links to one
    # string is three megabytes on disk.
    return "%s: %s" % (name, rendered(as_list(p(0)),
                                      lambda c: _text(c, codec),
                                      separator=" / "))


def _variables_line(p: _Params, codec: TextCodec, name: str) -> str:
    target = _id_range(p(0, 0), p(1, 0), "variable", "variables")
    kind = p(3)
    if kind == OPERAND_RANDOM:
        value = "random %s..%s" % (p(4), p(5))
    elif kind == OPERAND_VARIABLE:
        value = "variable %s" % p(4)
    elif kind == OPERAND_CONSTANT:
        # The only member left: the two above are the other two, so `kind in
        # SHARED_VARIABLE_OPERANDS` read as "any operand kind we know" and
        # meant "constant".
        value = _value(p(4), codec)
    else:
        # A kind this server does not claim to know: show it rather than
        # label the parameter as though the layout were confirmed.
        value = "operand kind %s: %s" % (kind, _value(p(4), codec))
    return "%s: %s %s %s" % (name, target,
                             VARIABLE_OPERATIONS.get(p(2), "?"), value)


def _transfer_line(p: _Params, codec: TextCodec, name: str) -> str:
    if p(0) == DESIGNATION_VARIABLE:
        return ("%s: map from variable %s at (variable %s, variable %s)"
                % (name, p(TRANSFER_MAP), p(TRANSFER_X), p(TRANSFER_Y)))
    return ("%s: map %s at (%s, %s)"
            % (name, p(TRANSFER_MAP), p(TRANSFER_X), p(TRANSFER_Y)))


def _battle_line(p: _Params, codec: TextCodec, name: str) -> str:
    if p(0) == BATTLE_DESIGNATION_RANDOM:
        # No troop id at all: the engine picks from the map's encounter list.
        return "%s: random encounter" % name
    if p(0) == DESIGNATION_VARIABLE:
        return "%s: troop from variable %s" % (name, p(1))
    return "%s: troop %s" % (name, p(1))


def _move_step_line(p: _Params, codec: TextCodec, name: str) -> str:
    """A 505 line: the route step it carries, by the VX family's names."""
    return _route_step_text(p, codec, name, MOVE_COMMAND_NAMES)


def _xp_move_step_line(p: _Params, codec: TextCodec, name: str) -> str:
    """A 509 line: the same, by XP's names (39 and 40 differ)."""
    return _route_step_text(p, codec, name, XP_MOVE_COMMAND_NAMES)


def _route_step_text(p: _Params, codec: TextCodec, name: str,
                     names: dict[int, str]) -> str:
    move = p(0)
    if isinstance(move, RubyObject):
        move_code = move.get("code", CODE_BLANK)
        # `as_int` before the lookup, for the reason `summarize` gives:
        # this is out of the file, and an Array here is unhashable.
        known = names.get(as_int(move_code) or CODE_BLANK)
        line = "> %s" % (known or "Move %s" % brief(move_code))
        arguments = as_list(move.get("parameters"))
        if arguments:
            # A jump's offsets and a wait's frames are what the step *is*;
            # without them every Jump in a route reads alike.
            line += " (%s)" % rendered(arguments,
                                       lambda v: _argument(v, codec))
        return line
    # `name`, not the literal: `BASE_COMMAND_NAMES[505]` is "Move
    # Command" and this argument already holds it.
    return "> %s" % name


def _argument(value: Any, codec: TextCodec) -> str:
    """One parameter in a line: an audio file by its name, else the value."""
    if isinstance(value, RubyObject) and value.has("name"):
        return _text(value.get("name"), codec)
    return _value(value, codec)


def _number(value: Any) -> str:
    """A Tone or Color channel, which the file holds as a double."""
    if isinstance(value, float) and value.is_integer():
        return "%d" % value
    return brief(value)


def _channels(value: Any, codec: TextCodec) -> str:
    """``(34, -34, -68, 170)`` for a Tone or Color, else the value itself."""
    if isinstance(value, RGSSFloats):
        return "(%s)" % ", ".join(_number(v) for v in value.values)
    return _value(value, codec)


def _waits(flag: Any) -> str:
    return ", wait" if flag is True else ""


def _amount(operation: Any, operand_type: Any, operand: Any) -> str:
    """``+ 50`` or ``- variable 4``: what `operate_value` is given.

    Its own reading of the three parameters: 0 adds and anything else
    subtracts, and an operand type of 0 is a constant -- `operate_value` in
    both engines, word for word.
    """
    sign = "+" if operation == 0 else "-"
    if operand_type == 0:
        return "%s %s" % (sign, brief(operand))
    return "%s variable %s" % (sign, brief(operand))


def _gain_line(p: _Params, name: str, noun: str) -> str:
    """Change Items, Weapons or Armor; the table's lambdas name the noun."""
    line = "%s: %s %s %s" % (name, noun, brief(p(0)),
                             _amount(p(1), p(2), p(3)))
    return line + (" (including equipped)" if p(4) is True else "")


def _party_line(p: _Params, codec: TextCodec, name: str) -> str:
    if p(1) == 0:
        return "%s: add actor %s%s" % (name, brief(p(0)),
                                       " (initialized)" if p(2) == 1 else "")
    return "%s: remove actor %s" % (name, brief(p(0)))


def _event_location_line(p: _Params, codec: TextCodec, name: str) -> str:
    who = _character(p(0))
    if p(1) == DESIGNATION_VARIABLE:
        where = "to (variable %s, variable %s)" % (brief(p(2)), brief(p(3)))
    elif p(1) == DESIGNATION_DIRECT:
        where = "to (%s, %s)" % (brief(p(2)), brief(p(3)))
    else:
        where = "swapped with %s" % _character(p(2))
    facing = " facing %s" % brief(p(4)) if p(4) not in (0, None) else ""
    return "%s: %s %s%s" % (name, who, where, facing)


def _picture_line(p: _Params, codec: TextCodec, name: str) -> str:
    """Show Picture and Move Picture: which one, where, and how long."""
    if p(3) == DESIGNATION_VARIABLE:
        where = "(variable %s, variable %s)" % (brief(p(4)), brief(p(5)))
    else:
        where = "(%s, %s)" % (brief(p(4)), brief(p(5)))
    picture = p(1)
    if isinstance(picture, RubyString):
        return "%s %s: %s at %s" % (name, brief(p(0)),
                                    _text(picture, codec), where)
    return "%s %s: to %s over %s frames%s" % (name, brief(p(0)), where,
                                              brief(p(10)), _waits(p(11)))


def _goods_line(p: _Params, codec: TextCodec, name: str) -> str:
    """Shop Processing and each Shop Goods line, in either engine's layout.

    VX Ace's goods are four parameters long and VX's two, which is how
    this tells a price from VX's purchase-only flag in the same slot.
    """
    kind = GOODS_KINDS.get(p(0), "goods kind %s" % brief(p(0)))
    line = "%s: %s %s" % (name, kind, brief(p(1)))
    if len(p.params) >= 4 and p(2) not in (0, None):
        line += ", price %s" % brief(p(3))
    purchase_only = p(4) if len(p.params) >= 4 else p(2)
    return line + (" (purchase only)" if purchase_only is True else "")


# The codes `summarize` renders specially, and how. Keyed by code, which is
# what the ninety-eight-line `if` chain this replaced dispatched on by hand
# -- and seven of those tests named a bare integer in the module that
# defines the constants. As table keys they are named once, and
# `TestACodeWithAConstantIsSpelledWithIt` reads the keys.
#
# Every renderer takes the same three arguments and ignores what it does not
# need, so the table can be walked without asking which shape each has --
# the arrangement `_COMPILERS` uses for the compiler. Declared as a type
# rather than left as prose in this comment: `TestEveryTableEntryTakesThe`
# `TablesArguments` reads the arity off each entry, because "every renderer
# takes the same three arguments" was a sentence nothing checked.
#
# Named function or lambda: a lambda when the whole rendering is one
# expression, a named function as soon as it needs a statement *or* a
# comment of its own. `_choices_line` is the second case -- one expression,
# and three lines of comment about why the cap is on the work rather than
# on the result, which a lambda cannot carry.
#
# Three arms are deliberately not here, because a renderer is chosen by
# equality and each of those asks a different question: whether the code is
# an audio command (set membership), whether it has a declared parameter
# spec (a lookup in another table), and whether it carries any parameters
# at all (the last resort, which names the command and lists them).
# `summarize` asks all three, in the order they were asked before.
# One command's parameters, the codec, and the command's name.
_SummaryRenderer = Callable[[_Params, TextCodec, str], str]


_SUMMARY_RENDERERS: dict[int, _SummaryRenderer] = {
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
    CODE_CHANGE_GOLD: lambda p, codec, name: (
        "%s: %s" % (name, _amount(p(0), p(1), p(2)))),
    CODE_CHANGE_ITEMS: lambda p, codec, name: _gain_line(p, name, "item"),
    CODE_CHANGE_WEAPONS: lambda p, codec, name: _gain_line(p, name, "weapon"),
    CODE_CHANGE_ARMOR: lambda p, codec, name: _gain_line(p, name, "armor"),
    CODE_CHANGE_PARTY: _party_line,
    CODE_SET_EVENT_LOCATION: _event_location_line,
    CODE_SHOW_ANIMATION: lambda p, codec, name: (
        "%s: %s, animation %s%s" % (name, _character(p(0)), brief(p(1)),
                                    _waits(p(2)))),
    CODE_SHOW_BALLOON: lambda p, codec, name: (
        "%s: %s, balloon %s%s" % (name, _character(p(0)), brief(p(1)),
                                  _waits(p(2)))),
    CODE_TINT_SCREEN: lambda p, codec, name: (
        "%s: tone %s, %s frames%s" % (name, _channels(p(0), codec),
                                      brief(p(1)), _waits(p(2)))),
    CODE_FLASH_SCREEN: lambda p, codec, name: (
        "%s: color %s, %s frames%s" % (name, _channels(p(0), codec),
                                       brief(p(1)), _waits(p(2)))),
    CODE_SHAKE_SCREEN: lambda p, codec, name: (
        "%s: power %s, speed %s, %s frames%s"
        % (name, brief(p(0)), brief(p(1)), brief(p(2)), _waits(p(3)))),
    CODE_SHOW_PICTURE: _picture_line,
    CODE_MOVE_PICTURE: _picture_line,
    CODE_ERASE_PICTURE: lambda p, codec, name: "%s %s" % (name, brief(p(0))),
    CODE_SHOP: _goods_line,
    CODE_SHOP_GOODS: _goods_line,
    CODE_NAME_INPUT: lambda p, codec, name: (
        "%s: actor %s, up to %s characters" % (name, brief(p(0)),
                                               brief(p(1)))),
}


# XP's route steps: VX's, except that 39 and 40 toggle Always on Top --
# XP's Game_Character has no transparency step.
XP_MOVE_COMMAND_NAMES: dict[int, str] = {
    **MOVE_COMMAND_NAMES, 39: "Always on Top ON", 40: "Always on Top OFF"}

# Which VX renderer reads each XP command, where XP keeps the same
# parameters under its own code: XP's Set Event Location (202), Show
# Animation (207), Set Move Route (209) and Wait (106) are VX's 203, 212,
# 205 and 230 with the same layout. The rest share their code as well.
_XP_RENDERED_AS = {
    **{code: code for code in (
        CODE_BLANK, CODE_TEXT_LINE, CODE_COMMENT_LINE, CODE_SCRIPT_MORE,
        CODE_COMMENT, CODE_SHOW_CHOICES, CODE_CHOICE_BRANCH,
        CODE_CONDITIONAL_BRANCH, CODE_CONTROL_SWITCHES,
        CODE_CONTROL_VARIABLES, CODE_CONTROL_SELF_SWITCH,
        CODE_CALL_COMMON_EVENT, CODE_TRANSFER_PLAYER, CODE_SCRIPT,
        CODE_LABEL, CODE_JUMP_TO_LABEL, CODE_CHANGE_GOLD, CODE_CHANGE_ITEMS,
        CODE_CHANGE_WEAPONS, CODE_CHANGE_ARMOR, CODE_CHANGE_PARTY,
        CODE_TINT_SCREEN, CODE_FLASH_SCREEN, CODE_SHAKE_SCREEN,
        CODE_SHOW_PICTURE, CODE_ERASE_PICTURE, CODE_SHOP, CODE_SHOP_GOODS,
        CODE_NAME_INPUT)},
    202: CODE_SET_EVENT_LOCATION,
    207: CODE_SHOW_ANIMATION,
    XP_SET_MOVE_ROUTE: CODE_SET_MOVE_ROUTE,
    106: CODE_WAIT,
}

# XP's renderers: the shared ones above, and three of its own. XP's Show
# Text carries its first line, its Battle Processing has no designation
# before the troop, and its route steps are named from XP's list.
_XP_SUMMARY_RENDERERS: dict[int, _SummaryRenderer] = {
    **{xp: _SUMMARY_RENDERERS[vx] for xp, vx in _XP_RENDERED_AS.items()},
    CODE_SHOW_TEXT: lambda p, codec, name: (
        "%s: %s" % (name, _text(p(0, ""), codec))),
    CODE_BATTLE_PROCESSING: lambda p, codec, name: (
        "%s: troop %s" % (name, brief(p(0)))),
    XP_MOVE_ROUTE_STEP: _xp_move_step_line,
}


def summarize(command: RubyObject, codec: TextCodec,
              engine: str = "vx_ace") -> str:
    """One readable line describing a single command.

    The VX family is rendered from the superset of its names, because naming
    a code the open engine happens not to dispatch is harmless while failing
    to name one it does is not. XP is rendered from its own table: its codes
    202 to 209 mean other things, so the superset would name them wrongly.
    """
    # Coerced before it is used as a key: dispatch is equality on an int,
    # and `dict.get` raises for an unhashable value rather than missing.
    # A code that is not a whole number gets named and nothing else --
    # `command_name` is the one function here that takes anything.
    raw = command.get("code", CODE_BLANK)
    code = CODE_BLANK if raw is None else as_int(raw)
    if code is None:
        return command_name(raw, engine)
    # `as_list`, not `or []`: a non-empty RubyHash here is truthy and
    # reaches `params[index]` as a TypeError.
    params = as_list(command.get("parameters"))
    name = command_name(code, engine)
    p = _Params(params)
    xp = engine == "xp"

    render = (_XP_SUMMARY_RENDERERS if xp else _SUMMARY_RENDERERS).get(code)
    if render is not None:
        return render(p, codec, name)
    if code in AUDIO_COMMAND_KINDS:
        audio = p(0)
        audio_name = (_text(audio.get("name"), codec)
                      if isinstance(audio, RubyObject) else "?")
        return "%s: %s" % (name, audio_name)

    spec = (XP_PARAM_SPECS if xp else ALL_PARAM_SPECS).get(code)
    if spec and spec[-1] != VARIADIC:
        pairs = ", ".join(
            "%s=%s" % (field, _text(p(i), codec))
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
        return str(_display_safe(value))
    return _text(value, codec)


def describe_condition(params: list[Any], codec: TextCodec) -> str:
    """Readable form of a Conditional Branch (code 111) parameter list.

    Still a chain and not a table of renderers, unlike `summarize`: the
    thirteen phrasings differ in shape and not only in the noun -- "switch 3
    is ON", "party has item 4", "timer >= 90s" -- so a table would hold
    thirteen entries each used once, which is a longer way to write the same
    chain. What the chain must not do is spell the nouns again, and it did:
    all thirteen of them, in the function two screens below the
    `CONDITION_KINDS` table whose own comment records that "eight of the
    thirteen constants were dead while the function that should have used
    them spelled their numbers out". Same defect, one column over.

    `_Params` rather than a local closure, for the reason that class exists:
    it was defined to replace exactly this, and then this kept its own.
    """
    if not params:
        return "?"
    p = _Params(params)
    kind = p(0)
    # The noun, from the table that holds the nouns.
    noun = CONDITION_KINDS.get(kind, "")

    if kind == COND_SWITCH:
        return "%s %s is %s" % (noun, p(1), SWITCH_OPERATIONS.get(p(2), p(2)))
    if kind == COND_VARIABLE:
        # `parameters[2]` is the operand kind, the same 0-or-1 encoding
        # `_compile_variable` writes and `_variables_line` reads.
        operand = ("%s %s" % (noun, p(3)) if p(2) == OPERAND_VARIABLE
                   else p(3))
        return "%s %s %s %s" % (noun, p(1), COMPARISONS.get(p(4), "?"),
                                operand)
    if kind == COND_SELF_SWITCH:
        return "%s %s is %s" % (noun, _text(p(1, ""), codec),
                                SWITCH_OPERATIONS.get(p(2), p(2)))
    if kind == COND_TIMER:
        return "%s %s %ss" % (noun, ">=" if p(2) == 0 else "<=", p(1))
    if kind in (COND_ACTOR, COND_ENEMY):
        return "%s %s (check type %s)" % (noun, p(1), p(2))
    if kind == COND_CHARACTER:
        return "%s %s faces %s" % (noun, _character(p(1)), p(2))
    if kind == COND_GOLD:
        return "%s %s %s" % (noun, ">=" if p(2) == 0 else "<=", p(1))
    if kind in (COND_ITEM, COND_WEAPON, COND_ARMOR):
        return "party has %s %s" % (noun, p(1))
    if kind == COND_BUTTON:
        return "%s %s pressed" % (noun, _text(p(1, ""), codec))
    if kind == COND_SCRIPT:
        return "%s: %s" % (noun, _text(p(1, ""), codec))
    # `brief`, not `%s`: this renders the rest of a list whose length and
    # whose elements both come out of the file, and `%s` on a list calls
    # `repr` on every element with nothing capping either. The kind is what
    # selects this branch, so a crafted map picks it.
    rest = params[1:1 + MAX_NAMES_LISTED]
    more = len(params) - 1 - len(rest)
    return "condition type %s %s%s" % (
        brief(kind), brief(rest), " and %d more" % more if more > 0 else "")


def _character(value: Any) -> str:
    # `brief` for both arms: this is read out of the file, and `str` of an
    # Array or `%d` of a 5,000-digit Integer is not a bounded line.
    if isinstance(value, int):
        return CHARACTER_TARGETS.get(value, "event %s" % brief(value))
    return brief(value)


def render_script(commands: list[Any], codec: TextCodec,
                  engine: str = "vx_ace") -> list[str]:
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
        text = summarize(command, codec, engine)
        lines.append("%3d| %s%s" % (index, "  " * indent, text))
    return lines


# ---------------------------------------------------------------------------
# Authoring DSL
# ---------------------------------------------------------------------------


MESSAGE_LINES_PER_BOX = 4

# Authored entries nest through if/loop/choices bodies. Real events are a few
# levels deep; the cap keeps a hostile payload from exhausting the stack.
MAX_DSL_DEPTH = 32

# A command code is a small number (the engine's highest is in the 600s), and
# the script editor's writes stay under four digits; a raw code outside this is
# not one any interpreter dispatches on.
MAX_COMMAND_CODE = 9999

# Control Self Switch and the condition for it: the four every engine has.
SELF_SWITCH_LETTERS = ("A", "B", "C", "D")


def compile_commands(entries: list[dict[str, Any]], codec: TextCodec,
                     indent: int = 0, depth: int = 0,
                     engine: str = "vx_ace") -> list[RubyObject]:
    """Compile DSL entries into an ``RPG::EventCommand`` list.

    The result deliberately does *not* include the trailing blank command; the
    caller decides, because a common event's list and an event page's list are
    assembled differently.

    ``engine`` is the open project's: the layout forms write a different
    parameter list for VX than for VX Ace where the two interpreters read
    different ones, and refuse to guess for an engine neither describes.
    """
    if depth > MAX_DSL_DEPTH:
        raise CommandError(
            "command entries nest more than %d levels deep; that is deeper than "
            "any event needs" % MAX_DSL_DEPTH
        )
    if not isinstance(entries, list):
        raise CommandError("commands must be an array, got %s" % brief(entries))
    out: list[RubyObject] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CommandError("each command entry must be an object, got %s"
                               % brief(entry))
        recognised = sorted(SUPPORTED_KEYS & set(entry))
        if len(recognised) > 1:
            # First-key-wins would silently compile one and drop the other,
            # which is a trap for a machine writing these.
            raise CommandError(
                "command entry names more than one command: %s. Split it into "
                "one entry per command." % ", ".join(recognised)
            )
        if recognised:
            _refuse_unconfirmed_form(recognised[0], engine)
        out.extend(_compile_entry(entry, codec, indent, depth, engine))
    return out


def _refuse_unconfirmed_form(key: str, engine: str) -> None:
    """Refuse a form whose layout is not confirmed for ``engine``.

    On the VX family, the forms that predate `AUTHORED_LAYOUT_FORMS` are
    not asked: their layouts are both engines'. XP is asked about every
    form, because XP numbers several of the old ones differently too --
    its Wait is 106 -- so only `XP_FORMS` compile there.
    """
    if engine == "xp" and key not in XP_FORMS:
        raise CommandError(
            'RPG Maker XP: the "%s" form is not offered for this project\'s '
            "engine (xp), because %s. Build the command with {\"raw\": ...}, "
            "copying a working example from this project (event_get with "
            "include_raw)." % (key, XP_REFUSED_FORMS.get(
                key, "its layout is the one VX and VX Ace's interpreters "
                     "read, and XP's Interpreter was not checked for it")))
    if key not in AUTHORED_LAYOUT_FORMS:
        return
    if engine not in ENGINE_COMMANDS:
        raise CommandError(
            'the "%s" form writes a parameter layout confirmed for VX Ace and '
            "VX, and this project's engine is %s, which this server does not "
            'model. Build the command with {"raw": ...}, copying a working '
            "example from this project (event_get with include_raw)."
            % (key, engine))
    reason = VX_UNCONFIRMED_FORMS.get(key) if engine == "vx" else None
    if reason is not None:
        raise CommandError(
            'RPG Maker VX: the "%s" form is not offered, because %s. Build '
            'the command with {"raw": ...}, copying a working example from '
            "this project (event_get with include_raw)." % (key, reason))


def _cmd(code: int, indent: int, params: list[Any] | None = None) -> RubyObject:
    return new_event_command(code, indent, params or [])


def _compile_entry(entry: dict[str, Any], enc: TextCodec, indent: int,
                   depth: int, engine: str) -> list[RubyObject]:
    for key, compile_one in _COMPILERS.items():
        if key in entry:
            return compile_one(entry, enc, indent, depth, engine)

    raise CommandError(
        "unrecognised command entry with keys %s; supported keys are %s, or use "
        '{"raw": {"code": .., "parameters": [..]}} for any other command'
        % (sorted(entry), ", ".join(sorted(SUPPORTED_KEYS)))
    )


def _compile_raw(entry: dict[str, Any], enc: TextCodec, indent: int,
                 depth: int, engine: str) -> list[RubyObject]:
    """Any command at all, by code and parameters.

    The escape hatch for a command this DSL has no form for. An entry may
    not carry `raw` *and* another recognised key -- `compile_commands`
    refuses that rather than choosing -- so this being first in the table
    means nothing.
    """
    raw = _needs_object(entry["raw"], "raw")
    if "code" not in raw:
        raise CommandError('a "raw" entry needs a "code"')
    code = _bounded(raw["code"], "raw.code", 0, MAX_COMMAND_CODE)
    given = raw.get("indent", indent)
    raw_indent = _needs_int(given, "raw.indent")
    if not 0 <= raw_indent <= MAX_AUTHORED_INDENT:
        raise CommandError(
            "raw.indent must be between 0 and %d, got %d"
            % (MAX_AUTHORED_INDENT, raw_indent))
    params = raw.get("parameters", [])
    if not isinstance(params, list):
        raise CommandError("raw.parameters must be an array, got %s"
                           % brief(params))
    return [_cmd(code, raw_indent, [_literal(v, enc, 1) for v in params])]


def _compile_switch(entry: dict[str, Any], enc: TextCodec, indent: int,
                    depth: int, engine: str) -> list[RubyObject]:
    """Control Switches, over one switch or a range of them."""
    state = _switch_state(entry)
    switch_id = _needs_int(entry["switch"], "switch")
    end = _needs_int(entry.get("switch_end", switch_id), "switch_end")
    if end < switch_id:
        raise CommandError("switch_end (%d) is before switch (%d); a range "
                           "runs upward" % (end, switch_id))
    return [_cmd(CODE_CONTROL_SWITCHES, indent, [switch_id, end, state])]


def _compile_self_switch(entry: dict[str, Any], enc: TextCodec, indent: int,
                         depth: int, engine: str) -> list[RubyObject]:
    """Control Self Switch: A, B, C or D on this event."""
    state = _switch_state(entry)
    return [_cmd(CODE_CONTROL_SELF_SWITCH, indent,
                 [enc.make_string(_self_switch(entry["self_switch"])), state])]


def _compile_loop(entry: dict[str, Any], enc: TextCodec, indent: int,
                  depth: int, engine: str) -> list[RubyObject]:
    """A loop, and the Repeat Above that closes it."""
    body = compile_commands(entry["loop"], enc, indent + 1, depth + 1,
                            engine)
    return [_cmd(CODE_LOOP, indent, []), *body,
            _cmd(CODE_REPEAT_ABOVE, indent, [])]


def _compile_transfer(entry: dict[str, Any], enc: TextCodec, indent: int,
                      depth: int, engine: str) -> list[RubyObject]:
    """Transfer Player, with every parameter checked as an integer."""
    t = _needs_object(entry["transfer"], "transfer")
    # `_needs_int` for all six, not just the ones that read as ids: a
    # Transfer Player destination is what this command *is*, and
    # `int(2.9)` is 2 -- so a wrong-typed y moved the player one tile
    # from where the caller said and the reply reported success.
    return [_cmd(CODE_TRANSFER_PLAYER, indent, [
        _needs_int(t.get("designation", 0), "transfer.designation"),
        _needs_int(_required(t, "map_id", "transfer"), "transfer.map_id"),
        _needs_int(_required(t, "x", "transfer"), "transfer.x"),
        _needs_int(_required(t, "y", "transfer"), "transfer.y"),
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
                  indent: int, engine: str = "vx_ace") -> list[RubyObject]:
    text = entry["text"]
    if isinstance(text, str):
        lines = text.split("\n")
    elif isinstance(text, list):
        lines = [str(line) for line in text]
    else:
        raise CommandError("text must be a string or an array of lines, got %r"
                           % (text,))
    if engine == "xp":
        return _compile_xp_text(entry, lines, enc, indent)
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


# The keys of a text entry that only the VX family's Show Text has.
_TEXT_WINDOW_KEYS = ("face_name", "face_index", "background", "position")


def _compile_xp_text(entry: dict[str, Any], lines: list[str], enc: TextCodec,
                     indent: int) -> list[RubyObject]:
    """XP's Show Text: the first line in the 101 itself, the rest in 401s.

    XP's command_101 shows `parameters[0]` and appends each 401 after it,
    and every one of 300 Show Text commands in a copy of an XP game has that
    one parameter. There is no face and no window setting to write.
    """
    given = [key for key in _TEXT_WINDOW_KEYS if key in entry]
    if given:
        raise CommandError(
            "RPG Maker XP: Show Text has no face or window settings, so %s "
            "cannot be written. Leave %s out." % (", ".join(given),
                                                  "it" if len(given) == 1
                                                  else "them"))
    out: list[RubyObject] = []
    for start in range(0, max(len(lines), 1), MESSAGE_LINES_PER_BOX):
        chunk = lines[start:start + MESSAGE_LINES_PER_BOX] or [""]
        out.append(_cmd(CODE_SHOW_TEXT, indent, [enc.make_string(chunk[0])]))
        out += [_cmd(CODE_TEXT_LINE, indent, [enc.make_string(line)])
                for line in chunk[1:]]
    return out


def _compile_variable(entry: dict[str, Any], indent: int,
                      engine: str = "vx_ace") -> list[RubyObject]:
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
    if end < variable_id:
        raise CommandError("variable_end (%d) is before variable (%d); a "
                           "range runs upward" % (end, variable_id))
    op_code = _named(entry.get("op", entry.get("operation", "set")),
                     VARIABLE_OP_CODES, "the variable operation")
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
                "random takes two whole numbers, [low, high]; got %s"
                % brief(bounds))
        operand_type = OPERAND_RANDOM
        operand = _needs_int(bounds[0], "random low")
        operand2 = _needs_int(bounds[1], "random high")
    else:
        operand_type = OPERAND_CONSTANT
        operand = _needs_int(entry.get("value", 0), "value")
        operand2 = 0
    params = [variable_id, end, op_code, operand_type, operand, operand2]
    if engine == "xp" and operand_type != OPERAND_RANDOM:
        # XP's editor stops at the operand when nothing follows it: every
        # constant and variable Control Variables in a copy of XP project
        # data has five parameters, and command_122 reads parameters[5]
        # only for a random range.
        params.pop()
    return [_cmd(CODE_CONTROL_VARIABLES, indent, params)]


def _compile_if(entry: dict[str, Any], enc: TextCodec, indent: int,
                depth: int, engine: str) -> list[RubyObject]:
    params = _compile_condition(_needs_object(entry["if"], "if"), enc)
    out = [_cmd(CODE_CONDITIONAL_BRANCH, indent, params)]
    out += compile_commands(entry.get("then", []), enc, indent + 1, depth + 1,
                            engine)
    if entry.get("else") is not None:
        out.append(_cmd(CODE_ELSE, indent, []))
        out += compile_commands(entry["else"], enc, indent + 1, depth + 1,
                                engine)
    out.append(_cmd(CODE_END_BRANCH, indent, []))
    return out


def _compile_condition(cond: dict[str, Any], enc: TextCodec) -> list[Any]:
    if "switch" in cond:
        return [COND_SWITCH, _needs_int(cond["switch"], "switch"),
                _switch_state(cond)]
    if "self_switch" in cond:
        return [COND_SELF_SWITCH,
                enc.make_string(_self_switch(cond["self_switch"])),
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
                     depth: int, engine: str) -> list[RubyObject]:
    if not isinstance(entry["choices"], list):
        raise CommandError("choices must be an array, got %s"
                           % brief(entry["choices"]))
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
                           "got %s" % brief(given))
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
            "that choice, and %d routes to the cancel_branch. Got %d."
            % (MAX_CANCEL_KIND, MAX_CHOICES, MAX_CANCEL_KIND, cancel))
    out = [_cmd(CODE_SHOW_CHOICES, indent,
                [[enc.make_string(str(c)) for c in choices], cancel])]
    for index, choice in enumerate(choices):
        out.append(_cmd(CODE_CHOICE_BRANCH, indent,
                        [index, enc.make_string(str(choice))]))
        body = branches[index] if index < len(branches) else []
        out += compile_commands(body, enc, indent + 1, depth + 1, engine)
    if cancel_branch is not None:
        out.append(_cmd(CODE_WHEN_CANCEL, indent, []))
        out += compile_commands(cancel_branch, enc, indent + 1, depth + 1,
                                engine)
    out.append(_cmd(CODE_END_CHOICES, indent, []))
    return out


# ---------------------------------------------------------------------------
# Layout forms
# ---------------------------------------------------------------------------
#
# Friendly forms for the commands whose parameter layouts were confirmed
# twice: from the engine's own `Game_Interpreter` (and `Game_Character`, for
# a route's steps), and from editor-written VX Ace data, which every VX Ace
# layout below reproduces byte for byte -- `tests/test_event_dsl.py` holds
# the bytes. VX has no editor-written data to compare with, so a VX layout
# here is its interpreter's, and a form whose VX layout the interpreter
# leaves open is refused on VX (`VX_UNCONFIRMED_FORMS`).
#
# Every number a caller gives is a bounded whole number, and every form is
# an object whose unknown fields are refused rather than ignored: a
# misspelt "volume" silently playing at the default is the defect
# `refuse_unknown_fields` exists for one layer up.

# The keys below, as a set: `compile_commands` asks it before asking which
# engine is open, because these are the forms whose layout depends on it.
AUTHORED_LAYOUT_FORMS = frozenset({
    "move_route", "shop", "show_picture", "move_picture", "erase_picture",
    "tint_screen", "flash_screen", "shake_screen", "gold", "item", "weapon",
    "armor", "party", "battle", "name_input", "bgm", "bgs", "me", "se",
    "fadeout_screen", "fadein_screen", "event_location", "balloon",
    "animation",
})


# Forms VX cannot have, and why. Each reason is the sentence a caller is
# given, so it says what is unknown rather than only that something is.
#
# The four audio forms and the route's se step were here, because the VX
# profile said RPG::AudioFile while VX's interpreter calls `play` on the
# parameter. The profile was the wrong half: see `schema.BGM_FAMILY_FIELDS`.
VX_UNCONFIRMED_FORMS: dict[str, str] = {
    "move_picture": (
        "VX's Move Picture stores a second parameter its interpreter never "
        "reads, and no editor-written VX data was available to show what "
        "the editor puts there (VX Ace writes nil)"),
}

# The forms XP is offered: the ones that compile to a code XP's Interpreter
# runs with the same parameters VX's does (each checked against its
# command_* method), and Show Text, which has a layout of its own on XP.
# Everything else is refused on XP with the reason below, or the general
# one -- XP numbers several of these commands differently, and the rest
# were not checked against its Interpreter.
XP_FORMS = frozenset({
    "raw", "text", "comment", "script", "switch", "self_switch", "variable",
    "common_event", "label", "jump", "transfer", "exit", "if", "loop",
    "break_loop", "choices",
})

# What a caller is told about the forms on an XP project, beside the help
# text that describes them all.
XP_AUTHORING_NOTE = (
    "RPG Maker XP takes only these forms: %s. Show Text writes XP's layout "
    "(the first line in the 101, no face). Every other command is written "
    'with {"raw": {"code": ..., "parameters": [...]}} using the codes above.'
    % ", ".join(sorted(XP_FORMS)))


_XP_AUDIO = ("XP stores audio as RPG::AudioFile and plays it through "
             "Game_System, and these forms write VX's RPG::BGM family")
XP_REFUSED_FORMS: dict[str, str] = {
    "wait": "XP's Wait is command 106, not VX's 230",
    "move_route": ("XP's Set Move Route is command 209 with 509 step lines, "
                   "and its graphic step takes a hue, a direction and a "
                   "pattern"),
    "event_location": "XP's Set Event Location is command 202, not 203",
    "animation": "XP's Show Animation is command 207, not 212",
    "balloon": "XP has no balloon icons",
    "fadeout_screen": ("XP's 221 and 222 prepare and execute a transition; "
                       "they are not VX's fades"),
    "fadein_screen": ("XP's 221 and 222 prepare and execute a transition; "
                      "they are not VX's fades"),
    "battle": ("XP's Battle Processing keeps the troop in its first "
               "parameter, with no designation before it"),
    "bgm": _XP_AUDIO, "bgs": _XP_AUDIO, "me": _XP_AUDIO, "se": _XP_AUDIO,
}

# Ids of switches, variables, events, actors and database entries. The
# same ceiling `db_tools.MAX_ENTRY_ID` and `event_tools.MAX_EVENT_ID` use;
# a tool module cannot be imported from here.
MAX_AUTHORED_ID = 9999
# A picture's position in pixels and a tile coordinate: well past any
# screen or map either editor makes.
MAX_COORDINATE = 9999
# Frames for a wait, a tint, a flash, a shake or a picture's move.
MAX_EFFECT_FRAMES = 999
# The most gold VX Ace's party can hold; also the bound on item counts.
MAX_AMOUNT = 99_999_999
MAX_ROUTE_STEPS = 1000
MAX_JUMP = 100
MAX_SHOP_GOODS = 100
MAX_ZOOM = 2000
MAX_OPACITY = 255
MAX_SHAKE = 9
MAX_NAME_CHARACTERS = 16
# Balloon.png has ten rows in both engines' RTP.
MAX_BALLOON = 10
# Pictures the screen keeps: VX's Game_Screen builds `0..20` and uses 1-20;
# VX Ace's grows on demand and its editor offers 1-100.
MAX_PICTURES = {"vx_ace": 100, "vx": 20}
# Volume and pitch, as both editors' sliders offer them.
AUDIO_VOLUME = (0, 100)
AUDIO_PITCH = (50, 150)
TONE_CHANNEL = (-255, 255)
TONE_GRAY = (0, 255)
COLOR_CHANNEL = (0, 255)
# Change Speed and Change Frequency in a move route.
MOVE_SPEEDS = (1, 6)
MOVE_FREQUENCIES = (1, 5)
CHARACTER_INDEXES = (0, 7)

# The names a target can be given, beside an event id.
CHARACTER_NAMES = {"player": -1, "this": 0}
# 0 keeps the facing; the rest are the numeric-keypad directions.
DIRECTIONS = (0, 2, 4, 6, 8)
# What `operate_value` reads: 0 adds and anything else subtracts.
GAIN_OPERATIONS = {"add": 0, "+": 0, "sub": 1, "-": 1}
PARTY_OPERATIONS = {"add": 0, "remove": 1}
PICTURE_ORIGINS = {"upper_left": 0, "center": 1}
BLEND_TYPES = {"normal": 0, "add": 1, "subtract": 2}
# Shop goods and the Change Items family share this order.
GOODS_KINDS = {0: "item", 1: "weapon", 2: "armor"}
GOODS_KIND_CODES = {name: kind for kind, name in GOODS_KINDS.items()}
# A goods entry's price kind: 0 sells at the database price, 1 at the one
# given (VX Ace's Window_ShopBuy: `goods[2] == 0 ? item.price : goods[3]`).
PRICE_STANDARD, PRICE_SPECIFIED = 0, 1

# The class and the instance-variable order each audio kind is written
# with. The order is the editor's, read from real files and kept with every
# other created class's in `schema.EDITOR_IVAR_ORDER`: BGM and BGS put
# volume before pitch and ME and SE the other way round, and Marshal keeps
# whatever order it is given.
AUDIO_LAYOUTS: dict[str, tuple[str, tuple[str, ...]]] = {
    kind: (cls, EDITOR_IVAR_ORDER[cls])
    for kind, cls in (("bgm", "RPG::BGM"), ("bgs", "RPG::BGS"),
                      ("me", "RPG::ME"), ("se", "RPG::SE"))
}
_AUDIO_CODES = {kind: code for code, kind in AUDIO_COMMAND_KINDS.items()}

# Every move route step by the name a caller writes, each a code
# `MOVE_COMMAND_NAMES` names. A step in `_STEP_ARGUMENTS` takes one value
# and is written `{"name": value}`; the rest are written as the bare name.
MOVE_STEP_CODES: dict[str, int] = {
    "move_down": 1, "move_left": 2, "move_right": 3, "move_up": 4,
    "move_lower_left": 5, "move_lower_right": 6, "move_upper_left": 7,
    "move_upper_right": 8, "move_random": 9, "move_toward_player": 10,
    "move_away_from_player": 11, "step_forward": 12, "step_backward": 13,
    "jump": 14, "wait": 15, "turn_down": 16, "turn_left": 17,
    "turn_right": 18, "turn_up": 19, "turn_right_90": 20,
    "turn_left_90": 21, "turn_180": 22, "turn_90_random": 23,
    "turn_random": 24, "turn_toward_player": 25,
    "turn_away_from_player": 26, "switch_on": 27, "switch_off": 28,
    "speed": 29, "frequency": 30, "walk_anime_on": 31,
    "walk_anime_off": 32, "step_anime_on": 33, "step_anime_off": 34,
    "direction_fix_on": 35, "direction_fix_off": 36, "through_on": 37,
    "through_off": 38, "transparent_on": 39, "transparent_off": 40,
    "graphic": 41, "opacity": 42, "blending": 43, "se": 44, "script": 45,
}


def _fields(value: Any, what: str, allowed: tuple[str, ...]) -> dict[str, Any]:
    """``value`` as an object carrying only fields from ``allowed``."""
    obj = _needs_object(value, what)
    unknown = sorted(set(obj) - set(allowed))
    if unknown:
        raise CommandError(
            "%s does not take %s; it takes %s"
            % (what, ", ".join(brief(key) for key in unknown[:8]),
               ", ".join(allowed)))
    return obj


def _required(obj: dict[str, Any], field: str, what: str) -> Any:
    if field not in obj:
        raise CommandError('%s needs "%s"' % (what, field))
    return obj[field]


def _bounded(value: Any, what: str, low: int, high: int) -> int:
    """A whole number from ``low`` to ``high``, or a refusal naming both."""
    number = _needs_int(value, what)
    if not low <= number <= high:
        raise CommandError("%s must be %d to %d, got %d"
                           % (what, low, high, number))
    return number


def _needs_bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise CommandError("%s must be true or false, got %s"
                           % (what, brief(value)))
    return value


def _bare(entry: dict[str, Any], key: str, code: int,
          indent: int) -> list[RubyObject]:
    """A command with no parameters, written ``{"key": true}``."""
    if entry[key] is not True:
        raise CommandError('write %s as {"%s": true}, got %s'
                           % (key, key, brief(entry[key])))
    return [_cmd(code, indent, [])]


def _self_switch(value: Any) -> str:
    """A self switch's letter: the engine has A, B, C and D."""
    if value not in SELF_SWITCH_LETTERS:
        raise CommandError("self_switch must be one of %s, got %s"
                           % (", ".join(SELF_SWITCH_LETTERS), brief(value)))
    return value


def _needs_text(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise CommandError("%s must be a string, got %s"
                           % (what, brief(value)))
    return value


def _named(value: Any, table: dict[str, int], what: str) -> int:
    """One of a table's names, as the number the engine stores."""
    if not isinstance(value, str) or value not in table:
        raise CommandError("%s must be one of %s, got %s"
                           % (what, ", ".join(table), brief(value)))
    return table[value]


def _one_of(obj: dict[str, Any], choices: tuple[str, ...], what: str) -> str:
    """The one field of ``choices`` an object gives."""
    given = [field for field in choices if field in obj]
    if len(given) != 1:
        raise CommandError("%s takes exactly one of %s, got %s"
                           % (what, ", ".join(choices),
                              ", ".join(given) or "none"))
    return given[0]


def _target(value: Any, what: str, player: bool = True) -> int:
    """A character: "player", "this", or an event id."""
    if isinstance(value, str):
        if value in CHARACTER_NAMES and (player or value != "player"):
            return CHARACTER_NAMES[value]
    elif isinstance(value, int) and not isinstance(value, bool):
        if 1 <= value <= MAX_AUTHORED_ID or value == CHARACTER_NAMES["this"] \
                or (player and value == CHARACTER_NAMES["player"]):
            return value
    raise CommandError(
        '%s must be %s"this" or an event id from 1 to %d, got %s'
        % (what, '"player", ' if player else "", MAX_AUTHORED_ID,
           brief(value)))


def _engine_lacks(feature: str, form: str) -> CommandError:
    """The refusal for a part of a form that VX's interpreter does not have."""
    return CommandError(
        'RPG Maker VX: %s has no %s -- its interpreter does not read one. '
        'Leave it out, or build the command with {"raw": ...}.'
        % (form, feature))


def _audio(kind: str, value: Any, enc: TextCodec, what: str) -> RubyObject:
    """An RPG::BGM, BGS, ME or SE in the editor's own field order."""
    cls, order = AUDIO_LAYOUTS[kind]
    obj = _fields(value, what, ("name", "volume", "pitch"))
    built = {
        "name": enc.make_string(_needs_text(_required(obj, "name", what),
                                            what + ".name")),
        "volume": _bounded(obj.get("volume", 100), what + ".volume",
                           *AUDIO_VOLUME),
        "pitch": _bounded(obj.get("pitch", 100), what + ".pitch",
                          *AUDIO_PITCH),
    }
    return RubyObject(cls, {"@" + field: built[field] for field in order})


def _compile_audio(entry: dict[str, Any], enc: TextCodec, indent: int,
                   depth: int, engine: str) -> list[RubyObject]:
    """Play BGM, BGS, ME or SE: whichever of the four keys the entry has."""
    kind = next(key for key in AUDIO_LAYOUTS if key in entry)
    return [_cmd(_AUDIO_CODES[kind], indent,
                 [_audio(kind, entry[kind], enc, kind)])]


def _channels_of(value: Any, what: str, names: tuple[str, ...],
                 bounds: tuple[tuple[int, int], ...]) -> list[int]:
    """Four whole numbers for a Tone or a Color, each in its range."""
    if not isinstance(value, list) or len(value) != len(names):
        raise CommandError("%s takes four whole numbers, [%s]; got %s"
                           % (what, ", ".join(names), brief(value)))
    return [_bounded(channel, "%s %s" % (what, name), low, high)
            for channel, name, (low, high) in zip(value, names, bounds)]


def _effect(obj: dict[str, Any], what: str, duration: int,
            wait: bool) -> list[Any]:
    """The duration and wait flag every screen effect ends with."""
    return [_bounded(obj.get("duration", duration), what + ".duration",
                     1, MAX_EFFECT_FRAMES),
            _needs_bool(obj.get("wait", wait), what + ".wait")]


def _compile_tint(entry: dict[str, Any], enc: TextCodec, indent: int,
                  depth: int, engine: str) -> list[RubyObject]:
    """Tint Screen: [Tone, duration, wait]."""
    obj = _fields(entry["tint_screen"], "tint_screen",
                  ("tone", "duration", "wait"))
    tone = _channels_of(_required(obj, "tone", "tint_screen"),
                        "tint_screen.tone", Tone.FIELDS,
                        (TONE_CHANNEL,) * 3 + (TONE_GRAY,))
    return [_cmd(CODE_TINT_SCREEN, indent,
                 [Tone(tone), *_effect(obj, "tint_screen", 60, True)])]


def _compile_flash(entry: dict[str, Any], enc: TextCodec, indent: int,
                   depth: int, engine: str) -> list[RubyObject]:
    """Flash Screen: [Color, duration, wait]."""
    obj = _fields(entry["flash_screen"], "flash_screen",
                  ("color", "duration", "wait"))
    color = _channels_of(_required(obj, "color", "flash_screen"),
                         "flash_screen.color", Color.FIELDS,
                         (COLOR_CHANNEL,) * 4)
    return [_cmd(CODE_FLASH_SCREEN, indent,
                 [Color(color), *_effect(obj, "flash_screen", 8, False)])]


def _compile_shake(entry: dict[str, Any], enc: TextCodec, indent: int,
                   depth: int, engine: str) -> list[RubyObject]:
    """Shake Screen: [power, speed, duration, wait]."""
    obj = _fields(entry["shake_screen"], "shake_screen",
                  ("power", "speed", "duration", "wait"))
    return [_cmd(CODE_SHAKE_SCREEN, indent, [
        _bounded(obj.get("power", 5), "shake_screen.power", 1, MAX_SHAKE),
        _bounded(obj.get("speed", 5), "shake_screen.speed", 1, MAX_SHAKE),
        *_effect(obj, "shake_screen", 60, False)])]


_PICTURE_FIELDS = ("number", "origin", "x", "y", "by_variables", "zoom_x",
                   "zoom_y", "opacity", "blend")


def _picture_number(value: Any, what: str, engine: str) -> int:
    return _bounded(value, what, 1, MAX_PICTURES.get(engine, 1))


def _placement(obj: dict[str, Any], what: str) -> list[Any]:
    """Origin, designation, x, y, zoom, opacity and blend, in that order.

    The eight parameters Show Picture and Move Picture share after the
    picture's number and name.
    """
    by_variables = _needs_bool(obj.get("by_variables", False),
                               what + ".by_variables")
    low, high = ((1, MAX_AUTHORED_ID) if by_variables
                 else (-MAX_COORDINATE, MAX_COORDINATE))
    return [
        _named(obj.get("origin", "upper_left"), PICTURE_ORIGINS,
               what + ".origin"),
        DESIGNATION_VARIABLE if by_variables else DESIGNATION_DIRECT,
        _bounded(obj.get("x", 0), what + ".x", low, high),
        _bounded(obj.get("y", 0), what + ".y", low, high),
        _bounded(obj.get("zoom_x", 100), what + ".zoom_x", 0, MAX_ZOOM),
        _bounded(obj.get("zoom_y", 100), what + ".zoom_y", 0, MAX_ZOOM),
        _bounded(obj.get("opacity", MAX_OPACITY), what + ".opacity", 0,
                 MAX_OPACITY),
        _named(obj.get("blend", "normal"), BLEND_TYPES, what + ".blend"),
    ]


def _compile_show_picture(entry: dict[str, Any], enc: TextCodec, indent: int,
                          depth: int, engine: str) -> list[RubyObject]:
    """Show Picture: [number, name, origin, designation, x, y, zoom_x,
    zoom_y, opacity, blend_type]."""
    obj = _fields(entry["show_picture"], "show_picture",
                  ("name",) + _PICTURE_FIELDS)
    number = _picture_number(_required(obj, "number", "show_picture"),
                             "show_picture.number", engine)
    name = _needs_text(_required(obj, "name", "show_picture"),
                       "show_picture.name")
    return [_cmd(CODE_SHOW_PICTURE, indent,
                 [number, enc.make_string(name),
                  *_placement(obj, "show_picture")])]


def _compile_move_picture(entry: dict[str, Any], enc: TextCodec, indent: int,
                          depth: int, engine: str) -> list[RubyObject]:
    """Move Picture: Show Picture's layout with nil for the name, then
    duration and wait. VX Ace only (see `VX_UNCONFIRMED_FORMS`)."""
    obj = _fields(entry["move_picture"], "move_picture",
                  _PICTURE_FIELDS + ("duration", "wait"))
    number = _picture_number(_required(obj, "number", "move_picture"),
                             "move_picture.number", engine)
    return [_cmd(CODE_MOVE_PICTURE, indent,
                 [number, None, *_placement(obj, "move_picture"),
                  *_effect(obj, "move_picture", 60, True)])]


def _amount_of(obj: dict[str, Any], what: str) -> list[int]:
    """[operation, operand type, operand]: what `operate_value` reads."""
    operation = obj.get("op", "add")
    if operation not in GAIN_OPERATIONS:
        raise CommandError("%s.op must be one of %s, got %s"
                           % (what, ", ".join(GAIN_OPERATIONS),
                              brief(operation)))
    source = _one_of(obj, ("value", "from_variable"), what)
    if source == "value":
        return [GAIN_OPERATIONS[operation], OPERAND_CONSTANT,
                _bounded(obj["value"], what + ".value", 0, MAX_AMOUNT)]
    return [GAIN_OPERATIONS[operation], OPERAND_VARIABLE,
            _bounded(obj["from_variable"], what + ".from_variable", 1,
                     MAX_AUTHORED_ID)]


def _compile_gold(entry: dict[str, Any], enc: TextCodec, indent: int,
                  depth: int, engine: str) -> list[RubyObject]:
    """Change Gold: [operation, operand type, operand]."""
    obj = _fields(entry["gold"], "gold", ("op", "value", "from_variable"))
    return [_cmd(CODE_CHANGE_GOLD, indent, _amount_of(obj, "gold"))]


def _gain(entry: dict[str, Any], kind: str, code: int,
          indent: int) -> list[RubyObject]:
    """Change Items (126), Weapons (127) or Armor (128).

    Weapons and armor carry a fifth parameter, "include equipped", which
    both interpreters pass to `gain_item`. Written always, `false` when not
    asked for: the editor does, in every five-parameter command in a real
    project.
    """
    equippable = kind != "item"
    allowed = ("id", "op", "value", "from_variable") + (
        ("include_equipped",) if equippable else ())
    obj = _fields(entry[kind], kind, allowed)
    params: list[Any] = [_bounded(_required(obj, "id", kind), kind + ".id", 1,
                                  MAX_AUTHORED_ID),
                         *_amount_of(obj, kind)]
    if equippable:
        included = _needs_bool(obj.get("include_equipped", False),
                               kind + ".include_equipped")
        if included and params[1] == GAIN_OPERATIONS["add"]:
            raise CommandError(
                "%s.include_equipped only applies when removing (op sub): "
                "it lets the removal take equipped ones too" % kind)
        params.append(included)
    return [_cmd(code, indent, params)]


def _compile_party(entry: dict[str, Any], enc: TextCodec, indent: int,
                   depth: int, engine: str) -> list[RubyObject]:
    """Change Party Member: [actor, 0 add / 1 remove, 1 to initialize]."""
    obj = _fields(entry["party"], "party", ("actor", "op", "initialize"))
    actor = _bounded(_required(obj, "actor", "party"), "party.actor", 1,
                     MAX_AUTHORED_ID)
    operation = _named(_required(obj, "op", "party"), PARTY_OPERATIONS,
                       "party.op")
    initialize = _needs_bool(obj.get("initialize", False), "party.initialize")
    if initialize and operation != PARTY_OPERATIONS["add"]:
        raise CommandError("party.initialize only applies when adding an "
                           "actor: it resets them to their starting state")
    return [_cmd(CODE_CHANGE_PARTY, indent,
                 [actor, operation, 1 if initialize else 0])]


def _compile_event_location(entry: dict[str, Any], enc: TextCodec,
                            indent: int, depth: int,
                            engine: str) -> list[RubyObject]:
    """Set Event Location: [event, designation, x, y, direction].

    The swap-with-another-event designation (2) is not offered: no real
    command uses it, so what the editor writes in the unread y is unknown.
    """
    obj = _fields(entry["event_location"], "event_location",
                  ("event", "x", "y", "by_variables", "direction"))
    event = _target(_required(obj, "event", "event_location"),
                    "event_location.event", player=False)
    by_variables = _needs_bool(obj.get("by_variables", False),
                               "event_location.by_variables")
    low, high = (1, MAX_AUTHORED_ID) if by_variables else (0, MAX_COORDINATE)
    direction = obj.get("direction", 0)
    if direction not in DIRECTIONS or isinstance(direction, bool):
        raise CommandError(
            "event_location.direction must be one of %s (0 keeps the "
            "facing; 2 down, 4 left, 6 right, 8 up), got %s"
            % (", ".join(map(str, DIRECTIONS)), brief(direction)))
    return [_cmd(CODE_SET_EVENT_LOCATION, indent, [
        event,
        DESIGNATION_VARIABLE if by_variables else DESIGNATION_DIRECT,
        _bounded(_required(obj, "x", "event_location"), "event_location.x",
                 low, high),
        _bounded(_required(obj, "y", "event_location"), "event_location.y",
                 low, high),
        direction])]


def _character_effect(entry: dict[str, Any], key: str, code: int,
                      high: int, indent: int, engine: str) -> list[RubyObject]:
    """Show Animation or Show Balloon Icon: [target, id] and, on VX Ace, wait.

    VX's command_212 and command_213 read two parameters; VX Ace's read a
    third and wait while it is true.
    """
    obj = _fields(entry[key], key, ("target", key, "wait"))
    params: list[Any] = [
        _target(_required(obj, "target", key), key + ".target"),
        _bounded(_required(obj, key, key), "%s.%s" % (key, key), 1, high)]
    if engine == "vx":
        if "wait" in obj:
            raise _engine_lacks("wait flag", key)
    else:
        params.append(_needs_bool(obj.get("wait", True), key + ".wait"))
    return [_cmd(code, indent, params)]


def _compile_name_input(entry: dict[str, Any], enc: TextCodec, indent: int,
                        depth: int, engine: str) -> list[RubyObject]:
    """Name Input Processing: [actor, max characters]."""
    obj = _fields(entry["name_input"], "name_input",
                  ("actor", "max_characters"))
    return [_cmd(CODE_NAME_INPUT, indent, [
        _bounded(_required(obj, "actor", "name_input"), "name_input.actor",
                 1, MAX_AUTHORED_ID),
        _bounded(_required(obj, "max_characters", "name_input"),
                 "name_input.max_characters", 1, MAX_NAME_CHARACTERS)])]


def _compile_shop(entry: dict[str, Any], enc: TextCodec, indent: int,
                  depth: int, engine: str) -> list[RubyObject]:
    """Shop Processing, and one Shop Goods line per goods entry after the first.

    VX Ace: each entry is [kind, id, price kind, price] and the first also
    carries "purchase only". VX: each is [kind, id], and the first carries
    "purchase only" in its third slot -- `$game_temp.shop_purchase_only =
    @params[2]` -- with no price at all.
    """
    obj = _fields(entry["shop"], "shop", ("goods", "purchase_only"))
    goods = _required(obj, "goods", "shop")
    if not isinstance(goods, list) or not 1 <= len(goods) <= MAX_SHOP_GOODS:
        raise CommandError("shop.goods must be an array of 1 to %d goods, "
                           "got %s" % (MAX_SHOP_GOODS, brief(goods)))
    purchase_only = _needs_bool(obj.get("purchase_only", False),
                                "shop.purchase_only")
    out = []
    for index, good in enumerate(goods):
        what = "shop.goods[%d]" % index
        item = _fields(good, what, tuple(GOODS_KIND_CODES) + ("price",))
        kind = _one_of(item, tuple(GOODS_KIND_CODES), what)
        params: list[Any] = [GOODS_KIND_CODES[kind],
                             _bounded(item[kind], "%s.%s" % (what, kind), 1,
                                      MAX_AUTHORED_ID)]
        if engine == "vx":
            if "price" in item:
                raise _engine_lacks("price", "a shop's goods")
        elif "price" in item:
            params += [PRICE_SPECIFIED,
                       _bounded(item["price"], what + ".price", 0,
                                MAX_AMOUNT)]
        else:
            params += [PRICE_STANDARD, 0]
        if index == 0:
            out.append(_cmd(CODE_SHOP, indent, params + [purchase_only]))
        else:
            out.append(_cmd(CODE_SHOP_GOODS, indent, params))
    return out


def _compile_battle(entry: dict[str, Any], enc: TextCodec, indent: int,
                    depth: int, engine: str) -> list[RubyObject]:
    """Battle Processing, and its win, escape and lose branches.

    Laid out as the editor lays it out, which is not how `if` and `choices`
    are compiled: every branch body ends with a blank command one indent
    deeper, as in all 162 real battles read. With neither escape nor
    defeat possible the editor writes no branches at all, so a "win" body
    then has nowhere to go and is refused -- put those commands after the
    battle instead.
    """
    obj = _fields(entry["battle"], "battle",
                  ("troop", "from_variable", "random", "can_escape",
                   "can_lose"))
    source = _one_of(obj, ("troop", "from_variable", "random"), "battle")
    if source == "random":
        if engine == "vx":
            raise _engine_lacks("random encounter", "Battle Processing")
        if obj["random"] is not True:
            raise CommandError("battle.random must be true, got %s"
                               % brief(obj["random"]))
        head = [BATTLE_DESIGNATION_RANDOM, 0]
    elif source == "troop":
        head = [DESIGNATION_DIRECT, _bounded(obj["troop"], "battle.troop", 1,
                                             MAX_AUTHORED_ID)]
    else:
        head = [DESIGNATION_VARIABLE,
                _bounded(obj["from_variable"], "battle.from_variable", 1,
                         MAX_AUTHORED_ID)]
    can_escape = _needs_bool(obj.get("can_escape", False), "battle.can_escape")
    can_lose = _needs_bool(obj.get("can_lose", False), "battle.can_lose")
    branches = [(CODE_IF_WIN, "win", can_escape or can_lose),
                (CODE_IF_ESCAPE, "escape", can_escape),
                (CODE_IF_LOSE, "lose", can_lose)]
    for _, key, possible in branches:
        if key in entry and not possible:
            raise CommandError(
                'a "%s" branch needs %s: the editor writes no such branch '
                "otherwise. Put the commands after the battle instead."
                % (key, "can_escape or can_lose" if key == "win"
                   else "can_" + key))
    out = [_cmd(CODE_BATTLE_PROCESSING, indent,
                [*head, can_escape, can_lose])]
    if not (can_escape or can_lose):
        return out
    for code, key, possible in branches:
        if not possible:
            continue
        out.append(_cmd(code, indent, []))
        out += compile_commands(entry.get(key, []), enc, indent + 1,
                                depth + 1, engine)
        out.append(_cmd(CODE_BLANK, indent + 1, []))
    out.append(_cmd(CODE_END_BATTLE, indent, []))
    return out


def _step_arguments(code: int, value: Any, enc: TextCodec,
                    what: str) -> list[Any]:
    """The parameters of one move route step that takes a value.

    A chain, not a table, for the reason `describe_condition` gives: each
    step's value has its own shape, so a table would hold eleven entries
    each used once.
    """
    if code == MOVE_STEP_CODES["jump"]:
        if not isinstance(value, list) or len(value) != 2:
            raise CommandError("%s takes [x offset, y offset], got %s"
                               % (what, brief(value)))
        return [_bounded(offset, what, -MAX_JUMP, MAX_JUMP)
                for offset in value]
    if code == MOVE_STEP_CODES["wait"]:
        return [_bounded(value, what, 1, MAX_EFFECT_FRAMES)]
    if code in (MOVE_STEP_CODES["switch_on"], MOVE_STEP_CODES["switch_off"]):
        return [_bounded(value, what, 1, MAX_AUTHORED_ID)]
    if code == MOVE_STEP_CODES["speed"]:
        return [_bounded(value, what, *MOVE_SPEEDS)]
    if code == MOVE_STEP_CODES["frequency"]:
        return [_bounded(value, what, *MOVE_FREQUENCIES)]
    if code == MOVE_STEP_CODES["graphic"]:
        graphic = _fields(value, what, ("name", "index"))
        return [enc.make_string(_needs_text(_required(graphic, "name", what),
                                            what + ".name")),
                _bounded(graphic.get("index", 0), what + ".index",
                         *CHARACTER_INDEXES)]
    if code == MOVE_STEP_CODES["opacity"]:
        return [_bounded(value, what, 0, MAX_OPACITY)]
    if code == MOVE_STEP_CODES["blending"]:
        return [_named(value, BLEND_TYPES, what)]
    if code == MOVE_STEP_CODES["se"]:
        # Both engines' Game_Character: `command.parameters[0].play`.
        return [_audio("se", value, enc, what)]
    return [enc.make_string(_needs_text(value, what))]


# The steps written `{"name": value}`; every other step is a bare name.
_STEP_ARGUMENTS = frozenset({"jump", "wait", "switch_on", "switch_off",
                             "speed", "frequency", "graphic", "opacity",
                             "blending", "se", "script"})


def _move_step(step: Any, enc: TextCodec, what: str) -> RubyObject:
    """One route step: ``"move_down"`` or ``{"jump": [0, -1]}``."""
    if isinstance(step, dict):
        if len(step) != 1:
            raise CommandError("%s: a step object names one step, got %s"
                               % (what, brief(sorted(step))))
        name, value = next(iter(step.items()))
    else:
        name, value = step, None
    code = MOVE_STEP_CODES.get(name) if isinstance(name, str) else None
    if code is None:
        raise CommandError("%s: no step is called %s; the steps are %s"
                           % (what, brief(name), ", ".join(MOVE_STEP_CODES)))
    if name in _STEP_ARGUMENTS:
        if not isinstance(step, dict):
            raise CommandError('%s: the %s step takes a value; write it as '
                               '{"%s": ...}' % (what, name, name))
        return new_move_command(code, _step_arguments(
            code, value, enc, "%s.%s" % (what, name)))
    if isinstance(step, dict):
        raise CommandError('%s: the %s step takes no value; write it as '
                           'the string "%s"' % (what, name, name))
    return new_move_command(code, [])


def _compile_move_route(entry: dict[str, Any], enc: TextCodec, indent: int,
                        depth: int, engine: str) -> list[RubyObject]:
    """Set Move Route, and the 505 line the editor writes for each step.

    Each 505 line carries the route's own step object rather than a copy:
    every one of the 1,037 step lines in the real projects read is a
    Marshal link to the command in the route, and `move_route_steps` is
    what builds them that way.
    """
    obj = _fields(entry["move_route"], "move_route",
                  ("target", "steps", "repeat", "skippable", "wait"))
    target = _target(_required(obj, "target", "move_route"),
                     "move_route.target")
    steps = obj.get("steps", [])
    if not isinstance(steps, list) or len(steps) > MAX_ROUTE_STEPS:
        raise CommandError("move_route.steps must be an array of at most %d "
                           "steps, got %s" % (MAX_ROUTE_STEPS,
                                              brief(steps)
                                              if not isinstance(steps, list)
                                              else "%d" % len(steps)))
    route = new_move_route()
    route.set("repeat", _needs_bool(obj.get("repeat", False),
                                    "move_route.repeat"))
    route.set("skippable", _needs_bool(obj.get("skippable", False),
                                       "move_route.skippable"))
    route.set("wait", _needs_bool(obj.get("wait", True), "move_route.wait"))
    route.set("list", [
        *(_move_step(step, enc, "move_route.steps[%d]" % index)
          for index, step in enumerate(steps)),
        new_move_command(CODE_BLANK, [])])
    return [_cmd(CODE_SET_MOVE_ROUTE, indent, [target, route]),
            *move_route_steps(route, indent)]


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
# Every value takes the same five arguments and ignores what it does not
# need, so the table can be walked without asking which shape each has --
# declared as a type, for the reason `_SummaryRenderer` is. The fifth, the
# engine, arrived with the layout forms, whose parameter lists differ
# between VX and VX Ace; the forms before them ignore it, and pass it on to
# the bodies they nest. A named compiler appears bare when it takes those
# five as they come; a lambda is
# what adapts one taking fewer, or spells out a body short enough that a
# named function would only give it a second name. Six were lambdas for
# neither reason: the table used to sit above half the compilers, so a
# lambda was standing in for the file's layout, and the rule it looked like
# it followed was not one anybody had chosen. It lives below all of them
# now.
# One DSL entry, the codec, the indent it compiles at, how deep the
# nesting already is, and the engine whose layout to write.
_Compiler = Callable[[dict, TextCodec, int, int, str], list[RubyObject]]


_COMPILERS: dict[str, _Compiler] = {
    "raw": _compile_raw,
    "text": lambda entry, enc, indent, depth, engine: _compile_text(
        entry, enc, indent, engine),
    "comment": lambda entry, enc, indent, depth, engine: _multiline(
        CODE_COMMENT, CODE_COMMENT_LINE, str(entry["comment"]), enc, indent),
    "script": lambda entry, enc, indent, depth, engine: _multiline(
        CODE_SCRIPT, CODE_SCRIPT_MORE, str(entry["script"]), enc, indent),
    "switch": _compile_switch,
    "self_switch": _compile_self_switch,
    "variable": lambda entry, enc, indent, depth, engine: _compile_variable(
        entry, indent, engine),
    "wait": lambda entry, enc, indent, depth, engine: [
        _cmd(CODE_WAIT, indent, [_needs_int(entry["wait"], "wait")])],
    "common_event": lambda entry, enc, indent, depth, engine: [
        _cmd(CODE_CALL_COMMON_EVENT, indent,
             [_needs_int(entry["common_event"], "common_event")])],
    "label": lambda entry, enc, indent, depth, engine: [
        _cmd(CODE_LABEL, indent, [enc.make_string(str(entry["label"]))])],
    "jump": lambda entry, enc, indent, depth, engine: [
        _cmd(CODE_JUMP_TO_LABEL, indent,
             [enc.make_string(str(entry["jump"]))])],
    "transfer": _compile_transfer,
    "exit": lambda entry, enc, indent, depth, engine: [
        _cmd(CODE_EXIT_EVENT, indent, [])],
    "if": _compile_if,
    "loop": _compile_loop,
    "break_loop": lambda entry, enc, indent, depth, engine: [
        _cmd(CODE_BREAK_LOOP, indent, [])],
    "choices": _compile_choices,
    # The layout forms: `AUTHORED_LAYOUT_FORMS` names these same keys.
    "move_route": _compile_move_route,
    "shop": _compile_shop,
    "show_picture": _compile_show_picture,
    "move_picture": _compile_move_picture,
    "erase_picture": lambda entry, enc, indent, depth, engine: [
        _cmd(CODE_ERASE_PICTURE, indent, [_picture_number(
            entry["erase_picture"], "erase_picture", engine)])],
    "tint_screen": _compile_tint,
    "flash_screen": _compile_flash,
    "shake_screen": _compile_shake,
    "gold": _compile_gold,
    "item": lambda entry, enc, indent, depth, engine: _gain(
        entry, "item", CODE_CHANGE_ITEMS, indent),
    "weapon": lambda entry, enc, indent, depth, engine: _gain(
        entry, "weapon", CODE_CHANGE_WEAPONS, indent),
    "armor": lambda entry, enc, indent, depth, engine: _gain(
        entry, "armor", CODE_CHANGE_ARMOR, indent),
    "party": _compile_party,
    "battle": _compile_battle,
    "name_input": _compile_name_input,
    "bgm": _compile_audio,
    "bgs": _compile_audio,
    "me": _compile_audio,
    "se": _compile_audio,
    "fadeout_screen": lambda entry, enc, indent, depth, engine: _bare(
        entry, "fadeout_screen", CODE_FADEOUT_SCREEN, indent),
    "fadein_screen": lambda entry, enc, indent, depth, engine: _bare(
        entry, "fadein_screen", CODE_FADEIN_SCREEN, indent),
    "event_location": _compile_event_location,
    "balloon": lambda entry, enc, indent, depth, engine: _character_effect(
        entry, "balloon", CODE_SHOW_BALLOON, MAX_BALLOON, indent, engine),
    "animation": lambda entry, enc, indent, depth, engine: _character_effect(
        entry, "animation", CODE_SHOW_ANIMATION, MAX_AUTHORED_ID, indent,
        engine),
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
        if value not in (0, 1):
            raise CommandError("a switch state is 0 (on) or 1 (off), got %d"
                               % value)
        return value
    text = str(value).strip().lower()
    if text in ("on", "true", "yes"):
        return 0
    if text in ("off", "false", "no"):
        return 1
    raise CommandError("expected on or off, got %s" % brief(value))


def _needs_object(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CommandError("%s must be an object, got %s" % (what, brief(value)))
    return value


def _needs_int(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CommandError("%s must be a whole number, got %s"
                           % (what, brief(value)))
    return value


def _literal(value: Any, enc: TextCodec, depth: int = 0) -> Any:
    """Convert a JSON literal from a ``raw`` entry into a Ruby value.

    Bounded in depth: the writer has no limit and the reader refuses past 192,
    so a literal nested a few hundred levels was written and the file then
    could not be opened through the server.
    """
    if depth > MAX_JSON_DEPTH:
        raise CommandError("a parameter is nested more than %d levels deep"
                           % MAX_JSON_DEPTH)
    if isinstance(value, str):
        return enc.make_string(value)
    if isinstance(value, list):
        return [_literal(v, enc, depth + 1) for v in value]
    if isinstance(value, dict):
        return _literal_object(value, enc, depth)
    return value


def _literal_object(value: dict[str, Any], enc: TextCodec,
                    depth: int = 0) -> Any:
    """Build a Ruby object from ``{"__class__": "RPG::AudioFile", ...}``."""
    cls = value.get("__class__")
    if not cls or not isinstance(cls, str):
        raise CommandError(
            'object parameters need a "__class__" key naming the Ruby class, got %s'
            % brief(sorted(value, key=str))
        )
    if cls == "RPG::MoveRoute":
        return _build_move_route(value, enc, depth)
    obj = RubyObject(cls)
    for key, item in value.items():
        if key != "__class__":
            obj.set(key, _literal(item, enc, depth + 1))
    # A JSON object's keys come in whatever order the caller typed them.
    return in_editor_order(obj)


def _build_move_route(value: dict[str, Any], enc: TextCodec,
                      depth: int = 0) -> RubyObject:
    """Build an RPG::MoveRoute from a JSON object.

    The steps may be given as ``list`` (the field's real name) or ``commands``;
    both are accepted because the former reads oddly in hand-written JSON.
    """
    route = RubyObject("RPG::MoveRoute", {
        "@repeat": _needs_bool(value.get("repeat", False), "repeat"),
        "@skippable": _needs_bool(value.get("skippable", False), "skippable"),
        "@wait": _needs_bool(value.get("wait", False), "wait"),
        "@list": [],
    })
    steps = value.get("list", value.get("commands", []))
    if not isinstance(steps, list):
        raise CommandError("a move route's list must be an array, got %s"
                           % brief(steps))
    commands = [_raw_route_step(step, enc, depth) for step in steps]
    # A route ends with a blank command. Only add one if the caller has not --
    # a read-modify-write of an existing route already carries it.
    if not commands or (commands[-1].get("code") or 0) != 0:
        commands.append(new_move_command(0, []))
    route.set("list", commands)
    return route


def _raw_route_step(step: Any, enc: TextCodec, depth: int) -> RubyObject:
    """One step of a hand-built move route: ``{"code": 1, "parameters": []}``."""
    step = _needs_object(step, "a move route step")
    given = step.get("parameters", [])
    if not isinstance(given, list):
        raise CommandError("a move route step's parameters must be an array, "
                           "got %s" % brief(given))
    return new_move_command(_needs_int(step.get("code", 0), "move code"),
                            [_literal(p, enc, depth + 2) for p in given])


def move_route_steps(route: RubyObject, indent: int,
                     step_code: int = CODE_MOVE_ROUTE_STEP) -> list[RubyObject]:
    """The 505 (XP: 509) commands the editor writes beside a Set Move Route."""
    steps = []
    for move in as_list(route.get("list")):
        if isinstance(move, RubyObject) and (move.get("code", 0) or 0) != 0:
            steps.append(_cmd(step_code, indent, [move]))
    return steps


def route_codes(engine: str) -> tuple[int, int]:
    """Set Move Route and its step line, on one engine: (205, 505) or XP's."""
    if engine == "xp":
        return XP_SET_MOVE_ROUTE, XP_MOVE_ROUTE_STEP
    return CODE_SET_MOVE_ROUTE, CODE_MOVE_ROUTE_STEP


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
    'from_variable) and script.\n\n'
    "Layout forms, each written to the parameter layout the open engine's "
    "editor writes (fields in [] are optional, defaults after =):\n"
    '* {"move_route": {"target": "player"|"this"|event id, "steps": [...], '
    '[repeat=false], [skippable=false], [wait=true]}}. A step is a name -- '
    "move_down/left/right/up, move_lower_left, move_lower_right, "
    "move_upper_left, move_upper_right, move_random, move_toward_player, "
    "move_away_from_player, step_forward, step_backward, turn_down/left/"
    "right/up, turn_right_90, turn_left_90, turn_180, turn_90_random, "
    "turn_random, turn_toward_player, turn_away_from_player, walk_anime_on/"
    "off, step_anime_on/off, direction_fix_on/off, through_on/off, "
    'transparent_on/off -- or one of {"jump": [dx, dy]} (-100 to 100), '
    '{"wait": frames}, {"switch_on": id}, {"switch_off": id}, {"speed": 1-6}, '
    '{"frequency": 1-5}, {"graphic": {"name": "Actor1", "index": 0-7}}, '
    '{"opacity": 0-255}, {"blending": "normal"|"add"|"subtract"}, '
    '{"se": {audio}}, {"script": "ruby"}. At most 1000 steps; '
    "the editor's step lines are written for you.\n"
    '* {"shop": {"goods": [{"item": id}, {"weapon": id, "price": 500}, '
    '{"armor": id}], [purchase_only=false]}}: 1 to 100 goods; a price '
    "override is VX Ace only.\n"
    '* {"show_picture": {"number": n, "name": "file", [origin="upper_left"|'
    '"center"], [x=0], [y=0], [by_variables=false], [zoom_x=100], '
    '[zoom_y=100], [opacity=255], [blend="normal"|"add"|"subtract"]}}; '
    '{"move_picture": {the same without name, [duration=60], [wait=true]}} '
    '(VX Ace only); {"erase_picture": n}. Pictures are 1-100 on VX Ace and '
    "1-20 on VX; coordinates -9999 to 9999, zoom 0 to 2000.\n"
    '* {"tint_screen": {"tone": [red, green, blue, gray], [duration=60], '
    '[wait=true]}} (-255 to 255, gray 0 to 255); {"flash_screen": {"color": '
    '[red, green, blue, alpha], [duration=8], [wait=false]}} (0 to 255); '
    '{"shake_screen": {[power=5], [speed=5], [duration=60], [wait=false]}} '
    "(1 to 9); durations 1 to 999 frames. "
    '{"fadeout_screen": true}; {"fadein_screen": true}.\n'
    '* {"gold": {[op="add"|"sub"], "value": n}} or with "from_variable": id '
    'instead of value; {"item": {"id": n, ...}}, {"weapon": {...}} and '
    '{"armor": {...}} the same, and weapon and armor take '
    '[include_equipped=false] when removing. Amounts run from 0 to '
    "99,999,999 inclusive.\n"
    '* {"party": {"actor": id, "op": "add"|"remove", [initialize=false]}}.\n'
    '* {"battle": {"troop": id | "from_variable": id | "random": true (VX Ace '
    'only), [can_escape=false], [can_lose=false]}, "win": [...], '
    '"escape": [...], "lose": [...]}: a branch exists only when escaping or '
    "losing is allowed, as in the editor.\n"
    '* {"name_input": {"actor": id, "max_characters": 1-16}}.\n'
    '* {"bgm": {"name": "Town1", [volume=100], [pitch=100]}}, and "bgs", '
    '"me", "se" the same (volume 0 to 100, pitch 50 to 150; '
    'name "" is the editor\'s "(None)").\n'
    '* {"event_location": {"event": "this"|id, "x": n, "y": n, '
    '[by_variables=false], [direction=0|2|4|6|8]}}.\n'
    '* {"balloon": {"target": ..., "balloon": 1-10, [wait=true]}} and '
    '{"animation": {"target": ..., "animation": id, [wait=true]}}; VX has '
    "no wait flag on either.\n"
    "Ids are 1 to 9999. A form this engine's layout is not confirmed for is "
    "refused and says why.\n\n"
    'For any other command, use {"raw": {"code": 236, "parameters": [...]}} '
    "-- see the command_reference tool for codes, and read a working example "
    "from the project before guessing a parameter layout.\n\n"
    'Limits the compiler enforces: Show Choices takes 1 to 4 choices, and '
    '"branches" pairs with them by position. "text" may be a string with \\n '
    "or an array of lines, and is split into four-line message boxes "
    "automatically. Entries nest through then/else/loop/branches to a bounded "
    'depth. A "raw" entry inherits the surrounding indent unless it sets one.'
)
