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
from .rubytypes import RubyObject, RubyString
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
# they only appear in Ace projects.
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

# Commands that open a nested block: the commands inside sit one indent deeper.
BLOCK_OPENERS = {111, 112, 102, 402, 403, 301, 601, 602, 603}

PARAM_SPECS: dict[int, list[str]] = {
    101: ["face_name", "face_index", "background", "position"],
    102: ["choices", "cancel_type"],
    103: ["variable_id", "digits"],
    108: ["text"],
    111: ["type", "..."],
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
    if engine == "vx":
        return PARAM_SPECS
    return {**PARAM_SPECS, **ACE_PARAM_SPECS}

# Operation codes shared by several commands.
SWITCH_OPERATIONS = {0: "ON", 1: "OFF"}
VARIABLE_OPERATIONS = {0: "=", 1: "+=", 2: "-=", 3: "*=", 4: "/=", 5: "%="}
VARIABLE_OPERANDS = {0: "constant", 1: "variable", 2: "random", 3: "item",
                     4: "actor", 5: "enemy", 6: "character", 7: "other"}
COMPARISONS = {0: "==", 1: ">=", 2: "<=", 3: ">", 4: "<", 5: "!="}
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
        return codec.decode(value.data)
    if isinstance(value, RubyObject):
        return "<%s>" % value.cls
    return str(value)


def summarize(command: RubyObject, codec: TextCodec) -> str:
    """One readable line describing a single command."""
    code = command.get("code", 0) or 0
    params = command.get("parameters") or []
    name = command_name(code)

    def p(index: int, default: Any = None) -> Any:
        return params[index] if len(params) > index else default

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
        start, end = p(0, 0), p(1, 0)
        target = "switch %s" % start if start == end else "switches %s..%s" % (start, end)
        return "%s: %s = %s" % (name, target, SWITCH_OPERATIONS.get(p(2), p(2)))
    if code == 122:
        start, end = p(0, 0), p(1, 0)
        target = "variable %s" % start if start == end else "variables %s..%s" % (start, end)
        operand = VARIABLE_OPERANDS.get(p(3), p(3))
        value = p(4)
        if p(3) == 2:  # random range
            value = "random %s..%s" % (p(4), p(5))
        elif p(3) == 1:
            value = "variable %s" % p(4)
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
    if code in (241, 245, 249, 250):
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
    if spec is not None and spec and spec[-1] != "...":
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
        return params[index] if len(params) > index else default

    if kind == 0:
        return "switch %s is %s" % (p(1), SWITCH_OPERATIONS.get(p(2), p(2)))
    if kind == 1:
        operand = "variable %s" % p(3) if p(2) == 1 else p(3)
        return "variable %s %s %s" % (p(1), COMPARISONS.get(p(4), "?"), operand)
    if kind == 2:
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
    if kind == 12:
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
        text = summarize(command, codec)
        lines.append("%3d| %s%s" % (index, "  " * indent, text))
    return lines


# ---------------------------------------------------------------------------
# Authoring DSL
# ---------------------------------------------------------------------------

MESSAGE_LINES_PER_BOX = 4


def compile_commands(entries: list[dict[str, Any]], codec: TextCodec,
                     indent: int = 0) -> list[RubyObject]:
    """Compile DSL entries into an ``RPG::EventCommand`` list.

    The result deliberately does *not* include the trailing blank command; the
    caller decides, because a common event's list and an event page's list are
    assembled differently.
    """
    out: list[RubyObject] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise CommandError("each command entry must be an object, got %r" % (entry,))
        out.extend(_compile_entry(entry, codec, indent))
    return out


def _cmd(code: int, indent: int, params: list[Any] | None = None) -> RubyObject:
    return new_event_command(code, indent, params or [])


def _compile_entry(entry: dict[str, Any], enc: TextCodec,
                   indent: int) -> list[RubyObject]:
    if "raw" in entry:
        raw = entry["raw"]
        code = int(raw.get("code", 0))
        params = [_literal(v, enc) for v in raw.get("parameters", [])]
        return [_cmd(code, int(raw.get("indent", indent)), params)]

    if "text" in entry:
        return _compile_text(entry, enc, indent)

    if "comment" in entry:
        lines = str(entry["comment"]).split("\n")
        out = [_cmd(108, indent, [enc.make_string(lines[0])])]
        out += [_cmd(408, indent, [enc.make_string(line)]) for line in lines[1:]]
        return out

    if "script" in entry:
        lines = str(entry["script"]).split("\n")
        out = [_cmd(355, indent, [enc.make_string(lines[0] + "\n")])]
        out += [_cmd(655, indent, [enc.make_string(line + "\n")]) for line in lines[1:]]
        return out

    if "switch" in entry:
        state = _on_off(entry.get("value", entry.get("is", "on")))
        switch_id = int(entry["switch"])
        end = int(entry.get("switch_end", switch_id))
        return [_cmd(121, indent, [switch_id, end, state])]

    if "self_switch" in entry:
        state = _on_off(entry.get("value", entry.get("is", "on")))
        return [_cmd(123, indent, [enc.make_string(str(entry["self_switch"])), state])]

    if "variable" in entry:
        return _compile_variable(entry, indent)

    if "wait" in entry:
        return [_cmd(230, indent, [int(entry["wait"])])]

    if "common_event" in entry:
        return [_cmd(117, indent, [int(entry["common_event"])])]

    if "label" in entry:
        return [_cmd(118, indent, [enc.make_string(str(entry["label"]))])]

    if "jump" in entry:
        return [_cmd(119, indent, [enc.make_string(str(entry["jump"]))])]

    if "transfer" in entry:
        t = entry["transfer"]
        return [_cmd(201, indent, [
            int(t.get("designation", 0)), int(t["map_id"]), int(t["x"]), int(t["y"]),
            int(t.get("direction", 0)), int(t.get("fade", 0)),
        ])]

    if "exit" in entry:
        return [_cmd(115, indent, [])]

    if "if" in entry:
        return _compile_if(entry, enc, indent)

    if "loop" in entry:
        body = compile_commands(entry["loop"], enc, indent + 1)
        return [_cmd(112, indent, []), *body, _cmd(413, indent, [])]

    if "break_loop" in entry:
        return [_cmd(113, indent, [])]

    if "choices" in entry:
        return _compile_choices(entry, enc, indent)

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


def _compile_text(entry: dict[str, Any], enc: TextCodec,
                  indent: int) -> list[RubyObject]:
    lines = entry["text"].split("\n") if isinstance(entry["text"], str) else list(entry["text"])
    header = [
        enc.make_string(str(entry.get("face_name", ""))),
        int(entry.get("face_index", 0)),
        int(entry.get("background", 0)),
        int(entry.get("position", 2)),
    ]
    out: list[RubyObject] = []
    # VX shows four lines per message box; longer text becomes several boxes.
    for start in range(0, max(len(lines), 1), MESSAGE_LINES_PER_BOX):
        chunk = lines[start:start + MESSAGE_LINES_PER_BOX] or [""]
        out.append(_cmd(101, indent, list(header)))
        out += [_cmd(401, indent, [enc.make_string(line)]) for line in chunk]
    return out


def _compile_variable(entry: dict[str, Any], indent: int) -> list[RubyObject]:
    variable_id = int(entry["variable"])
    end = int(entry.get("variable_end", variable_id))
    operation = entry.get("op", entry.get("operation", "set"))
    op_code = {"set": 0, "=": 0, "add": 1, "+": 1, "sub": 2, "-": 2,
               "mul": 3, "*": 3, "div": 4, "/": 4, "mod": 5, "%": 5}.get(operation)
    if op_code is None:
        raise CommandError(
            "unknown variable operation %r; use set/add/sub/mul/div/mod" % (operation,)
        )
    if "from_variable" in entry:
        operand_type, operand, operand2 = 1, int(entry["from_variable"]), 0
    elif "random" in entry:
        low, high = entry["random"]
        operand_type, operand, operand2 = 2, int(low), int(high)
    else:
        operand_type, operand, operand2 = 0, int(entry.get("value", 0)), 0
    return [_cmd(122, indent, [variable_id, end, op_code, operand_type, operand, operand2])]


def _compile_if(entry: dict[str, Any], enc: TextCodec,
                indent: int) -> list[RubyObject]:
    params = _compile_condition(entry["if"], enc)
    out = [_cmd(111, indent, params)]
    out += compile_commands(entry.get("then", []), enc, indent + 1)
    if entry.get("else"):
        out.append(_cmd(411, indent, []))
        out += compile_commands(entry["else"], enc, indent + 1)
    out.append(_cmd(412, indent, []))
    return out


def _compile_condition(cond: dict[str, Any], enc: TextCodec) -> list[Any]:
    if "switch" in cond:
        return [0, int(cond["switch"]), _on_off(cond.get("is", cond.get("value", "on")))]
    if "self_switch" in cond:
        return [2, enc.make_string(str(cond["self_switch"])),
                _on_off(cond.get("is", cond.get("value", "on")))]
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
        return [1, int(cond["variable"]), operand_type, operand, comparison]
    if "script" in cond:
        return [12, enc.make_string(str(cond["script"]))]
    raise CommandError(
        'condition must use switch, self_switch, variable or script; for other '
        'condition types build the 111 command with {"raw": ...}'
    )


def _compile_choices(entry: dict[str, Any], enc: TextCodec,
                     indent: int) -> list[RubyObject]:
    choices = list(entry["choices"])
    if not 1 <= len(choices) <= 4:
        raise CommandError("VX allows 1 to 4 choices, got %d" % len(choices))
    branches = entry.get("branches") or []
    cancel_branch = entry.get("cancel_branch")
    # parameters[1] selects cancel behaviour: 0 disallows cancelling, 1..4 picks
    # that choice, 5 routes to a When Cancel branch. Passed through verbatim so
    # an explicit value always wins.
    cancel = entry.get("cancel", 5 if cancel_branch else 0)
    out = [_cmd(102, indent, [[enc.make_string(str(c)) for c in choices], int(cancel)])]
    for index, choice in enumerate(choices):
        out.append(_cmd(402, indent, [index, enc.make_string(str(choice))]))
        body = branches[index] if index < len(branches) else []
        out += compile_commands(body, enc, indent + 1)
    if cancel_branch is not None:
        out.append(_cmd(403, indent, []))
        out += compile_commands(cancel_branch, enc, indent + 1)
    out.append(_cmd(404, indent, []))
    return out


def _on_off(value: Any) -> int:
    if isinstance(value, bool):
        return 0 if value else 1
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    if text in ("on", "true", "yes"):
        return 0
    if text in ("off", "false", "no"):
        return 1
    raise CommandError("expected on/off, got %r" % (value,))


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
    route = RubyObject("RPG::MoveRoute", {
        "@repeat": bool(value.get("repeat", False)),
        "@skippable": bool(value.get("skippable", False)),
        "@wait": bool(value.get("wait", False)),
        "@list": [],
    })
    commands = [
        new_move_command(int(c.get("code", 0)),
                         [_literal(p, enc) for p in c.get("parameters", [])])
        for c in value.get("list", value.get("commands", []))
    ]
    commands.append(new_move_command(0, []))  # routes are terminated by a blank
    route.set("list", commands)
    return route


def move_route_steps(route: RubyObject, indent: int) -> list[RubyObject]:
    """The 505 commands the editor writes beside a Set Move Route command."""
    steps = []
    for move in route.get("list") or []:
        if isinstance(move, RubyObject) and (move.get("code", 0) or 0) != 0:
            steps.append(_cmd(505, indent, [move]))
    return steps
