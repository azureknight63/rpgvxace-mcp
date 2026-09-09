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

from typing import Any

from .convert import TextCodec
from .rubytypes import RubyObject, RubyString, printable_text
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
SHARED_VARIABLE_OPERANDS = {0: "constant", 1: "variable", 2: "random"}
VX_VARIABLE_OPERANDS = {**SHARED_VARIABLE_OPERANDS,
                        3: "item", 4: "actor", 5: "enemy", 6: "character",
                        7: "other"}
VARIABLE_OPERANDS = SHARED_VARIABLE_OPERANDS

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

# Transfer Player (201): designation, map_id, x, y, direction, fade.
TRANSFER_MAP, TRANSFER_X, TRANSFER_Y = 1, 2, 3

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


def command_name(code: int) -> str:
    return COMMAND_NAMES.get(code, "Unknown (%d)" % code)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _text(value: Any, codec: TextCodec) -> str:
    if isinstance(value, RubyString):
        return printable_text(codec.decode(value.data))
    if isinstance(value, RubyObject):
        return "<%s>" % printable_text(value.cls)
    return str(value)


def _id_range(start: Any, end: Any, singular: str, plural: str) -> str:
    """``switch 3`` or ``switches 3..7``: these commands take an id range."""
    if start == end:
        return "%s %s" % (singular, start)
    return "%s %s..%s" % (plural, start, end)


def _param(params: list[Any], index: int, default: Any = None) -> Any:
    """One parameter, or ``default`` when the command does not carry it."""
    return params[index] if len(params) > index else default


def summarize(command: RubyObject, codec: TextCodec) -> str:
    """One readable line describing a single command.

    Deliberately engine-independent: rendering names a code from the superset,
    because naming a code the open engine happens not to dispatch is harmless
    while failing to name one it does is not.
    """
    code = command.get("code", 0) or 0
    params = command.get("parameters") or []
    name = command_name(code)

    def p(index: int, default: Any = None) -> Any:
        return _param(params, index, default)

    if code == 0:
        return ""
    if code in (401, 408, 655):
        return '"%s"' % _text(p(0, ""), codec)
    if code == 101:
        face = _text(p(0, ""), codec)
        detail = "face %s[%s]" % (face, p(1, 0)) if face else "no face"
        return "%s (%s)" % (name, detail)
    if code == 108:
        return "Comment: %s" % _text(p(0, ""), codec)
    if code == 102:
        choices = [_text(c, codec) for c in (p(0) or [])]
        return "%s: %s" % (name, " / ".join(choices))
    if code == 402:
        return 'When "%s"' % _text(p(1, ""), codec)
    if code == 111:
        return "%s: %s" % (name, describe_condition(params, codec))
    if code == 121:
        target = _id_range(p(0, 0), p(1, 0), "switch", "switches")
        return "%s: %s = %s" % (name, target, SWITCH_OPERATIONS.get(p(2), p(2)))
    if code == 122:
        target = _id_range(p(0, 0), p(1, 0), "variable", "variables")
        kind = p(3)
        if kind == 2:  # random range
            value = "random %s..%s" % (p(4), p(5))
        elif kind == 1:
            value = "variable %s" % p(4)
        elif kind in SHARED_VARIABLE_OPERANDS:
            value = _value(p(4), codec)
        else:
            # A kind this server does not claim to know: show it rather than
            # label the parameter as though the layout were confirmed.
            value = "operand kind %s: %s" % (kind, _value(p(4), codec))
        return "%s: %s %s %s" % (name, target, VARIABLE_OPERATIONS.get(p(2), "?"), value)
    if code == 123:
        return "%s: %s = %s" % (name, _text(p(0, ""), codec),
                                SWITCH_OPERATIONS.get(p(1), p(1)))
    if code == 117:
        return "%s: %s" % (name, p(0))
    if code == 201:
        return "%s: map %s at (%s, %s)" % (name, p(1), p(2), p(3))
    if code == 205:
        return "%s: %s" % (name, _character(p(0)))
    if code == 230:
        return "%s: %s frames" % (name, p(0))
    if code in AUDIO_COMMAND_KINDS:
        audio = p(0)
        audio_name = _text(audio.get("name"), codec) if isinstance(audio, RubyObject) else "?"
        return "%s: %s" % (name, audio_name)
    if code == 301:
        return "%s: troop %s" % (name, p(1))
    if code == 355:
        return "%s: %s" % (name, _text(p(0, ""), codec))
    if code == 505:
        move = p(0)
        if isinstance(move, RubyObject):
            move_code = move.get("code", 0)
            return "> %s" % MOVE_COMMAND_NAMES.get(move_code, "Move %s" % move_code)
        return "> Move Command"
    if code in (118, 119):
        return "%s: %s" % (name, _text(p(0, ""), codec))

    spec = ALL_PARAM_SPECS.get(code)
    if spec and spec[-1] != VARIADIC:
        pairs = ", ".join(
            "%s=%s" % (field, _text(params[i], codec))
            for i, field in enumerate(spec) if len(params) > i
        )
        return "%s (%s)" % (name, pairs) if pairs else name
    if params:
        # Numbers and flags read better unquoted than in a Python list repr.
        return "%s (%s)" % (name, ", ".join(_value(v, codec) for v in params))
    return name


def _value(value: Any, codec: TextCodec) -> str:
    if isinstance(value, RubyString):
        return '"%s"' % codec.decode(value.data)
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
    if kind == 3:
        return "timer %s %ss" % (">=" if p(2) == 0 else "<=", p(1))
    if kind == 4:
        return "actor %s (check type %s)" % (p(1), p(2))
    if kind == 5:
        return "enemy %s (check type %s)" % (p(1), p(2))
    if kind == 6:
        return "character %s faces %s" % (_character(p(1)), p(2))
    if kind == 7:
        return "gold %s %s" % (">=" if p(2) == 0 else "<=", p(1))
    if kind == 8:
        return "party has item %s" % p(1)
    if kind == 9:
        return "party has weapon %s" % p(1)
    if kind == 10:
        return "party has armor %s" % p(1)
    if kind == 11:
        return "button %s pressed" % _text(p(1, ""), codec)
    if kind == COND_SCRIPT:
        return "script: %s" % _text(p(1, ""), codec)
    return "condition type %s %s" % (kind, params[1:])


def _character(value: Any) -> str:
    if isinstance(value, int):
        return CHARACTER_TARGETS.get(value, "event %d" % value)
    return str(value)


def render_script(commands: list[Any], codec: TextCodec) -> list[str]:
    """Render a command list as numbered, indented lines for reading."""
    lines = []
    for index, command in enumerate(commands):
        if not isinstance(command, RubyObject):
            lines.append("%3d| <not an RPG::EventCommand: %r>" % (index, command))
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
    if "raw" in entry:
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

    if "text" in entry:
        return _compile_text(entry, enc, indent)

    if "comment" in entry:
        return _multiline(108, 408, str(entry["comment"]), enc, indent)

    if "script" in entry:
        return _multiline(355, 655, str(entry["script"]), enc, indent)

    if "switch" in entry:
        state = _switch_state(entry)
        switch_id = _needs_int(entry["switch"], "switch")
        end = _needs_int(entry.get("switch_end", switch_id), "switch_end")
        return [_cmd(121, indent, [switch_id, end, state])]

    if "self_switch" in entry:
        state = _switch_state(entry)
        return [_cmd(123, indent, [enc.make_string(str(entry["self_switch"])), state])]

    if "variable" in entry:
        return _compile_variable(entry, indent)

    if "wait" in entry:
        return [_cmd(230, indent, [int(entry["wait"])])]

    if "common_event" in entry:
        return [_cmd(117, indent, [int(entry["common_event"])])]

    for key, code in (("label", 118), ("jump", 119)):
        if key in entry:
            return [_cmd(code, indent, [enc.make_string(str(entry[key]))])]

    if "transfer" in entry:
        t = _needs_object(entry["transfer"], "transfer")
        return [_cmd(201, indent, [
            int(t.get("designation", 0)), int(t["map_id"]), int(t["x"]), int(t["y"]),
            int(t.get("direction", 0)), int(t.get("fade", 0)),
        ])]

    if "exit" in entry:
        return [_cmd(115, indent, [])]

    if "if" in entry:
        return _compile_if(entry, enc, indent, depth)

    if "loop" in entry:
        body = compile_commands(entry["loop"], enc, indent + 1, depth + 1)
        return [_cmd(112, indent, []), *body, _cmd(413, indent, [])]

    if "break_loop" in entry:
        return [_cmd(113, indent, [])]

    if "choices" in entry:
        return _compile_choices(entry, enc, indent, depth)

    raise CommandError(
        "unrecognised command entry with keys %s; supported keys are %s, or use "
        '{"raw": {"code": .., "parameters": [..]}} for any other command'
        % (sorted(entry), ", ".join(sorted(SUPPORTED_KEYS)))
    )


SUPPORTED_KEYS = {
    "raw", "text", "comment", "script", "switch", "self_switch", "variable",
    "wait", "common_event", "label", "jump", "transfer", "exit", "if", "loop",
    "break_loop", "choices",
}


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
        out.append(_cmd(101, indent, [enc.make_string(face_name), *face]))
        out += [_cmd(401, indent, [enc.make_string(line)]) for line in chunk]
    return out


def _compile_variable(entry: dict[str, Any], indent: int) -> list[RubyObject]:
    variable_id = int(entry["variable"])
    end = int(entry.get("variable_end", variable_id))
    operation = entry.get("op", entry.get("operation", "set"))
    op_code = VARIABLE_OP_CODES.get(operation)
    if op_code is None:
        raise CommandError(
            "unknown variable operation %r; use one of %s"
            % (operation, ", ".join(sorted(VARIABLE_OP_CODES)))
        )
    if "from_variable" in entry:
        operand_type, operand, operand2 = 1, int(entry["from_variable"]), 0
    elif "random" in entry:
        low, high = entry["random"]
        operand_type, operand, operand2 = 2, int(low), int(high)
    else:
        operand_type, operand, operand2 = 0, int(entry.get("value", 0)), 0
    return [_cmd(122, indent, [variable_id, end, op_code, operand_type, operand, operand2])]


def _compile_if(entry: dict[str, Any], enc: TextCodec, indent: int,
                depth: int = 0) -> list[RubyObject]:
    params = _compile_condition(_needs_object(entry["if"], "if"), enc)
    out = [_cmd(111, indent, params)]
    out += compile_commands(entry.get("then", []), enc, indent + 1, depth + 1)
    if entry.get("else"):
        out.append(_cmd(411, indent, []))
        out += compile_commands(entry["else"], enc, indent + 1, depth + 1)
    out.append(_cmd(412, indent, []))
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
            operand_type, operand = 1, int(cond["from_variable"])
        else:
            operand_type, operand = 0, int(cond.get("value", 0))
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
    branches = entry.get("branches") or []
    cancel_branch = entry.get("cancel_branch")
    # parameters[1] selects cancel behaviour: 0 disallows cancelling, 1..4 picks
    # that choice, 5 routes to a When Cancel branch. Passed through verbatim so
    # an explicit value always wins.
    cancel = entry.get("cancel", 5 if cancel_branch is not None else 0)
    out = [_cmd(102, indent, [[enc.make_string(str(c)) for c in choices], int(cancel)])]
    for index, choice in enumerate(choices):
        out.append(_cmd(402, indent, [index, enc.make_string(str(choice))]))
        body = branches[index] if index < len(branches) else []
        out += compile_commands(body, enc, indent + 1, depth + 1)
    if cancel_branch is not None:
        out.append(_cmd(403, indent, []))
        out += compile_commands(cancel_branch, enc, indent + 1, depth + 1)
    out.append(_cmd(404, indent, []))
    return out


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
    for move in route.get("list") or []:
        if isinstance(move, RubyObject) and (move.get("code", 0) or 0) != 0:
            steps.append(_cmd(505, indent, [move]))
    return steps
