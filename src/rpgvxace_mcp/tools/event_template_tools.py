"""Event templates: the events people build over and over, from a few parameters.

A treasure chest, a door, a sign, a shopkeeper, a save point, an inn and a lever
are each a page or two of the same commands with a handful of numbers and words
changed. This tool holds them as DATA -- a table of parameterised command lists
in the DSL `event_edit` already speaks -- and writes the event through the same
constructors `event_edit` create / add_page use, so the bytes in the map file
are the bytes that tool would have written for the same pages. (They are not
the same *function*: a tool module is a leaf and may not import `event_tools`.
`TestTheBytesAreEventEdits` writes a template's event and the equivalent
`event_edit` ops into two fresh projects and compares the files, which is what
holds the two paths together.)

A template is a record:

    summary      one sentence
    engines      which engines it is offered on, and why not the others
    parameters   what a caller may pass: name, type, bounds, default
    events       the events it creates; each a name and pages, and a page is
                 a condition, a graphic, a trigger, a priority and `commands`

Everything a caller passes is read by a typed reader below (bounded, strict,
named in its refusal), then substituted into the record wherever it says
`{"$": "name"}`, `{"$fmt": "text {name}"}` or `{"$when": "name", "then": [...]}`.
Nothing a caller types is ever evaluated or used as a format string: the
format strings are this file's own.

The DSL does the engine check. A template that needs a form the open engine
does not confirm -- an item reward on XP, a shop on XP -- is refused by
`compile_commands` with the sentence it already uses; the table's `engines`
field only says so up front, and a test creates every template on every engine
to hold the claim.
"""

from __future__ import annotations

import copy
import unicodedata
from typing import Any

from ..analysis import WalkBudget
from ..commands import (
    AUDIO_PITCH,
    AUDIO_VOLUME,
    CHARACTER_INDEXES,
    CODE_BLANK,
    CommandError,
    MAX_AMOUNT,
    MAX_AUTHORED_ID,
    MAX_COORDINATE,
    MAX_SHOP_GOODS,
    compile_commands,
    render_script,
)
from ..convert import ConversionError, apply_patch, normalize_strings
from ..project import ASSET_DIRS, MAX_MAP_ID, ProjectError
from ..rubytypes import RubyHash, RubyObject, RubyString, as_list
from ..schema import new_event_command, on_map
from .registry import (
    BOOL,
    Context,
    DRY_RUN,
    INT,
    OBJ,
    STR,
    ToolError,
    check_op_args,
    decoded,
    echo,
    known_fields,
    need_int,
    one_of,
    opt_bool,
    opt_dict,
    opt_str,
    prop,
    refuse_unknown_fields,
    schema,
    tool,
    write_reply,
)

# The most lines a template's message parameter takes. The message window
# shows four, so this is ten boxes: more than any one event's speech.
MAX_TEMPLATE_LINES = 40

# The longest single line. The default message window is about 54 characters
# wide at the default font; escape codes such as \C[2] count here and not
# there, so the ceiling is generous rather than exact.
MAX_TEMPLATE_LINE_CHARS = 100

# The longest event name a template writes. The editor's own field takes more;
# the point is a name a map's event list can show.
MAX_TEMPLATE_NAME_CHARS = 32

# The longest graphic, face or sound file stem. A Windows file name is far
# longer; no asset in the RTP is near this.
MAX_TEMPLATE_ASSET_CHARS = 64

# How many parameters one call may pass. The largest template takes fifteen;
# more than this is a call built for some other tool.
_MAX_PARAMETER_KEYS = 40

# How many events a map-wide scan visits before it gives up. The same
# allowance every other walk of a map's events spends.
_SCAN_STEPS = 200_000


# What each op reads. `list` and `describe` never touch the project; `preview`
# is `create` that cannot write, so it has no `dry_run` of its own.
TEMPLATE_OP_ARGS = {
    "list": {"op"},
    "describe": {"op", "template"},
    "create": {"op", "template", "map_id", "x", "y", "parameters", "name",
               "allow_overlap", "dry_run"},
    "preview": {"op", "template", "map_id", "x", "y", "parameters", "name",
                "allow_overlap"},
}


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

# The engines a template is offered on. XP is refused wholesale where a form
# it needs is outside `commands.XP_FORMS`.
_VX_FAMILY = ("vx_ace", "vx")
_ALL_ENGINES = ("vx_ace", "vx", "xp")
_XP_WHY = ("RPG Maker XP: %s is not a form XP's interpreter is confirmed for "
           "(see command_reference), so this template is not offered there")

# Facing names a caller writes, and the numbers the engine stores. 0 keeps the
# player's current facing in Transfer Player, and means nothing on a graphic.
_FACINGS = {"down": 2, "left": 4, "right": 6, "up": 8}
_ARRIVAL_FACINGS = {"keep": 0, **_FACINGS}
_FADES = {"black": 0, "white": 1, "none": 2}
_TRIGGERS = {"action": 0, "touch": 1}
_SELF_SWITCHES = ("A", "B", "C", "D")
_REWARDS = ("item", "weapon", "armor", "gold")
_DATABASE_TABLES = {"item": "items", "weapon": "weapons", "armor": "armors"}


def P(name: str) -> dict[str, str]:
    """A hole in a template, filled with the parameter or derived value."""
    return {"$": name}


def _param(name: str, kind: str, description: str, *, required: bool = False,
           default: Any = None, **bounds: Any) -> dict[str, Any]:
    return {"name": name, "type": kind, "required": required,
            "default": default, "description": description, **bounds}


def _graphic_params(default: "str | None" = None) -> list[dict[str, Any]]:
    return [
        _param("graphic", "character",
               "The event's sprite sheet: a file in Graphics/Characters "
               "without its extension, or \"\" for no sprite."
               + ("" if default is None else " Default %r." % default),
               default=default),
        _param("graphic_index", "index",
               "Which of the sheet's %d characters, %d to %d. Not offered on "
               "XP, whose sheets hold one character."
               % (CHARACTER_INDEXES[1] + 1, *CHARACTER_INDEXES),
               minimum=CHARACTER_INDEXES[0], maximum=CHARACTER_INDEXES[1]),
    ]


def _page_graphic(name: str = "graphic", index: str = "graphic_index",
                  facing: "Any" = None) -> dict[str, Any]:
    out: dict[str, Any] = {"character_name": P(name), "character_index": P(index)}
    if facing is not None:
        out["direction"] = facing
    return out


def _sound_param(what: str) -> dict[str, Any]:
    return _param(
        "sound", "sound",
        "A sound effect played %s: {\"name\": file in Audio/SE, \"volume\": "
        "%d-%d (default 100), \"pitch\": %d-%d (default 100)}. Omit for "
        "silence. Not offered on XP." % ((what,) + AUDIO_VOLUME + AUDIO_PITCH))


def _speech(key: str = "lines", face: bool = False) -> dict[str, Any]:
    """A Show Text entry reading `key`, with the face when asked for."""
    entry: dict[str, Any] = {"text": P(key)}
    if face:
        entry.update({"face_name": P("face"), "face_index": P("face_index")})
    return entry


TEMPLATES: dict[str, dict[str, Any]] = {
    "treasure_chest": {
        "summary": "A chest that gives an item, weapon, armor or gold once, "
                   "then shows open.",
        "engines": _VX_FAMILY,
        "why_not_others": _XP_WHY % "Change Items, Change Gold and the sound effect",
        "parameters": [
            _param("reward_type", "choice", "What the chest holds.",
                   required=True, choices=list(_REWARDS)),
            _param("reward_id", "integer",
                   "The item, weapon or armor id, checked against the "
                   "database. Not used for gold.",
                   minimum=1, maximum=MAX_AUTHORED_ID),
            _param("amount", "integer",
                   "How many, or how much gold (required for gold).",
                   default=1, minimum=1, maximum=MAX_AMOUNT),
            _param("message", "lines",
                   "What the message box says. Default \"Found <name> xN!\" "
                   "from the database name.",
                   max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            _param("show_message", "boolean",
                   "False for a chest that gives its reward without a "
                   "message.", default=True),
            _sound_param("when the chest is opened"),
            *_graphic_params("!Chest"),
            _param("opened_index", "index",
                   "The sheet character shown once opened (default: the "
                   "same one). Not offered on XP.",
                   minimum=CHARACTER_INDEXES[0], maximum=CHARACTER_INDEXES[1]),
            _param("closed_facing", "choice",
                   "Which way the closed sprite faces.", default="down",
                   choices=list(_FACINGS)),
            _param("opened_facing", "choice",
                   "Which way the opened sprite faces. The RTP chest sheets "
                   "show a chest opening as the facing turns, so 'up' is the "
                   "fully open row -- unverified here; check it in the "
                   "editor.", default="up", choices=list(_FACINGS)),
            _param("self_switch", "choice",
                   "The self switch that remembers the chest was opened.",
                   default="A", choices=list(_SELF_SWITCHES)),
        ],
        "events": [{
            "at": "here",
            "name": P("event_name"),
            "pages": [
                {"graphic": _page_graphic(facing=P("closed_facing_code")),
                 "trigger": 0, "priority_type": 1,
                 "commands": [
                     {"$when": "sound", "then": [{"se": P("sound")}]},
                     P("reward_entry"),
                     {"$when": "show_message",
                      "then": [{"text": P("message_lines")}]},
                     {"self_switch": P("self_switch"), "value": "on"},
                 ]},
                {"condition": {"self_switch_valid": True,
                               "self_switch_ch": P("self_switch")},
                 "graphic": _page_graphic(
                     index="opened_index_value",
                     facing=P("opened_facing_code")),
                 "trigger": 0, "priority_type": 1, "commands": []},
            ],
        }],
        "default_name": "Chest",
    },

    "door": {
        "summary": "A door or stairway: stepping on it (or pressing the action "
                   "button) moves the player to a place on any map.",
        "engines": _ALL_ENGINES,
        "parameters": [
            _param("target_map_id", "map", "The map the player arrives on.",
                   required=True, minimum=1, maximum=MAX_MAP_ID),
            _param("target_x", "integer", "Arrival tile X, inside that map.",
                   required=True, minimum=0, maximum=MAX_COORDINATE),
            _param("target_y", "integer", "Arrival tile Y, inside that map.",
                   required=True, minimum=0, maximum=MAX_COORDINATE),
            _param("direction", "choice", "Which way the player faces on "
                   "arrival; keep leaves the facing alone.", default="keep",
                   choices=list(_ARRIVAL_FACINGS)),
            _param("fade", "choice", "The screen transition. XP: leave at "
                   "the default.", default="black", choices=list(_FADES)),
            _sound_param("as the player goes through"),
            _param("trigger", "choice", "touch: walking onto it. action: "
                   "pressing the action button on it.", default="touch",
                   choices=list(_TRIGGERS)),
            *_graphic_params(),
        ],
        "events": [{
            "at": "here",
            "name": P("event_name"),
            "pages": [{
                "graphic": _page_graphic(),
                "trigger": P("trigger_code"), "priority_type": P("priority"),
                "commands": [
                    {"$when": "sound", "then": [{"se": P("sound")}]},
                    {"transfer": {"map_id": P("target_map_id"),
                                  "x": P("target_x"), "y": P("target_y"),
                                  "direction": P("direction_code"),
                                  "fade": P("fade_code")}},
                ],
            }],
        }],
        "default_name": "Door",
    },

    "door_pair": {
        "summary": "Both sides of a doorway in one call: a door here and a "
                   "door on another map, each taking the player to the other "
                   "door's own tile.",
        "engines": _ALL_ENGINES,
        "parameters": [
            _param("to_map_id", "map", "The map the second door goes on.",
                   required=True, minimum=1, maximum=MAX_MAP_ID),
            _param("to_x", "integer", "The second door's tile X.",
                   required=True, minimum=0, maximum=MAX_COORDINATE),
            _param("to_y", "integer", "The second door's tile Y.",
                   required=True, minimum=0, maximum=MAX_COORDINATE),
            _param("direction", "choice", "Which way the player faces "
                   "arriving at the second door.", default="keep",
                   choices=list(_ARRIVAL_FACINGS)),
            _param("return_direction", "choice", "Which way the player faces "
                   "arriving back at the first door.", default="keep",
                   choices=list(_ARRIVAL_FACINGS)),
            _param("fade", "choice", "The screen transition, both ways. XP: "
                   "leave at the default.", default="black",
                   choices=list(_FADES)),
            _sound_param("as the player goes through, both ways"),
            _param("trigger", "choice", "touch: walking onto it. action: "
                   "pressing the action button on it.", default="touch",
                   choices=list(_TRIGGERS)),
            *_graphic_params(),
        ],
        "events": [
            {"at": "here", "name": P("event_name"), "pages": [{
                "graphic": _page_graphic(),
                "trigger": P("trigger_code"), "priority_type": P("priority"),
                "commands": [
                    {"$when": "sound", "then": [{"se": P("sound")}]},
                    {"transfer": {"map_id": P("to_map_id"), "x": P("to_x"),
                                  "y": P("to_y"),
                                  "direction": P("direction_code"),
                                  "fade": P("fade_code")}},
                ]}]},
            {"at": "there", "name": {"$fmt": "{event_name} (return)"},
             "pages": [{
                 "graphic": _page_graphic(),
                 "trigger": P("trigger_code"), "priority_type": P("priority"),
                 "commands": [
                     {"$when": "sound", "then": [{"se": P("sound")}]},
                     {"transfer": {"map_id": P("here_map_id"),
                                   "x": P("here_x"), "y": P("here_y"),
                                   "direction": P("return_direction_code"),
                                   "fade": P("fade_code")}},
                 ]}]},
        ],
        "default_name": "Door",
    },

    "sign": {
        "summary": "A sign or readable object: the action button shows text.",
        "engines": _ALL_ENGINES,
        "parameters": [
            _param("lines", "lines", "What the sign says: a list of lines, or "
                   "one string with \\n between them. Four lines to a box.",
                   required=True, max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            *_graphic_params(),
        ],
        "events": [{
            "at": "here", "name": P("event_name"),
            "pages": [{"graphic": _page_graphic(), "trigger": 0,
                       "priority_type": 1,
                       "commands": [_speech()]}],
        }],
        "default_name": "Sign",
    },

    "npc_dialogue": {
        "summary": "A person who talks, with an optional face picture and an "
                   "optional second page that says something else once a "
                   "switch is on.",
        "engines": _ALL_ENGINES,
        "parameters": [
            _param("lines", "lines", "What they say.", required=True,
                   max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            _param("face", "face", "A face sheet in Graphics/Faces, without "
                   "its extension. Not offered on XP."),
            _param("face_index", "integer", "Which face on the sheet, 0 to 7 "
                   "(default 0).", minimum=0, maximum=7),
            *_graphic_params(),
            _param("after_switch", "switch", "A switch id. Once it is ON the "
                   "person says after_lines instead.", minimum=1,
                   maximum=MAX_AUTHORED_ID),
            _param("after_lines", "lines", "What they say once after_switch is "
                   "on (required with it).", max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
        ],
        "events": [{
            "at": "here", "name": P("event_name"),
            "pages": [
                {"graphic": _page_graphic(), "trigger": 0, "priority_type": 1,
                 "commands": [
                     {"$when": "face", "then": [_speech("lines", True)],
                      "else": [_speech("lines")]}]},
                {"$when": "after_switch", "then": [
                    {"condition": {"switch1_valid": True,
                                   "switch1_id": P("after_switch")},
                     "graphic": _page_graphic(), "trigger": 0,
                     "priority_type": 1,
                     "commands": [
                         {"$when": "face",
                          "then": [_speech("after_lines", True)],
                          "else": [_speech("after_lines")]}]}]},
            ],
        }],
        "default_name": "NPC",
    },

    "shop_npc": {
        "summary": "A shopkeeper: an optional greeting, then the shop screen "
                   "with the goods you list.",
        "engines": _VX_FAMILY,
        "why_not_others": _XP_WHY % "Shop Processing",
        "parameters": [
            _param("goods", "goods", "What is for sale: up to %d entries, each "
                   "exactly one of {\"item\": id}, {\"weapon\": id}, "
                   "{\"armor\": id}, checked against the database, and "
                   "optionally \"price\" (0 to %d; VX Ace only) to override "
                   "the database price." % (MAX_SHOP_GOODS, MAX_AMOUNT),
                   required=True, max_items=MAX_SHOP_GOODS),
            _param("purchase_only", "boolean", "True for a shop that does not "
                   "buy from the player.", default=False),
            _param("greeting", "lines", "Said before the shop opens (default: "
                   "nothing).", max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            *_graphic_params(),
        ],
        "events": [{
            "at": "here", "name": P("event_name"),
            "pages": [{"graphic": _page_graphic(), "trigger": 0,
                       "priority_type": 1,
                       "commands": [
                           {"$when": "greeting",
                            "then": [{"text": P("greeting")}]},
                           {"shop": {"goods": P("goods"),
                                     "purchase_only": P("purchase_only")}},
                       ]}],
        }],
        "default_name": "Shop",
    },

    "save_point": {
        "summary": "A save point: shows a prompt, then opens the save screen.",
        "engines": _ALL_ENGINES,
        "parameters": [
            _param("prompt", "lines", "What it says first.",
                   default=["Would you like to save?"],
                   max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            *_graphic_params("!Crystal"),
        ],
        "events": [{
            "at": "here", "name": P("event_name"),
            "pages": [{"graphic": _page_graphic(), "trigger": 0,
                       "priority_type": 1,
                       "commands": [
                           {"text": P("prompt")},
                           {"raw": {"code": 352, "parameters": []}},
                       ]}],
        }],
        "default_name": "Save Point",
    },

    "inn": {
        "summary": "An innkeeper: asks to stay for a price; if the party can "
                   "pay, takes the gold, fades out, heals everyone and fades "
                   "back in.",
        "engines": _VX_FAMILY,
        "why_not_others": _XP_WHY % "Change Gold, the screen fades and Wait",
        "parameters": [
            _param("price", "integer", "Gold for a night.", required=True,
                   minimum=1, maximum=MAX_AMOUNT),
            _param("prompt", "lines", "The question. Default: asks whether to "
                   "stay for <price> gold.", max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            _param("message", "lines", "Said after the party has rested.",
                   default=["You feel well rested."],
                   max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            _param("poor_message", "lines", "Said when the party cannot pay.",
                   default=["You do not have enough gold."],
                   max_items=MAX_TEMPLATE_LINES,
                   max_chars=MAX_TEMPLATE_LINE_CHARS),
            *_graphic_params(),
        ],
        "events": [{
            "at": "here", "name": P("event_name"),
            "pages": [{"graphic": _page_graphic(), "trigger": 0,
                       "priority_type": 1,
                       "commands": [
                           {"text": P("prompt_lines")},
                           {"choices": ["Stay", "Leave"], "cancel": 2,
                            "branches": [
                                [{"if": {"script": P("can_pay")},
                                  "then": [
                                      {"gold": {"op": "sub",
                                                "value": P("price")}},
                                      {"fadeout_screen": True},
                                      {"wait": 60},
                                      {"raw": {"code": 314,
                                               "parameters": [0, 0]}},
                                      {"fadein_screen": True},
                                      {"text": P("message")}],
                                  "else": [{"text": P("poor_message")}]}],
                                [],
                            ]},
                       ]}],
        }],
        "default_name": "Inn",
    },

    "switch_toggle": {
        "summary": "A lever: each pull flips a game switch, and the lever's "
                   "sprite flips with it (two pages driven by a self switch).",
        "engines": _ALL_ENGINES,
        "parameters": [
            _param("switch_id", "switch", "The game switch the lever flips.",
                   required=True, minimum=1, maximum=MAX_AUTHORED_ID),
            _sound_param("on every pull"),
            *_graphic_params("!Switch"),
            _param("off_facing", "choice", "Which way the sprite faces while "
                   "the switch is off.", default="down",
                   choices=list(_FACINGS)),
            _param("on_facing", "choice", "Which way the sprite faces while "
                   "the switch is on. Which row of a sheet is the pulled "
                   "lever depends on the sheet: check it in the editor.",
                   default="up", choices=list(_FACINGS)),
            _param("self_switch", "choice", "The self switch that remembers "
                   "which way the lever is.", default="A",
                   choices=list(_SELF_SWITCHES)),
        ],
        "events": [{
            "at": "here", "name": P("event_name"),
            "pages": [
                {"graphic": _page_graphic(facing=P("off_facing_code")),
                 "trigger": 0, "priority_type": 1,
                 "commands": [
                     {"$when": "sound", "then": [{"se": P("sound")}]},
                     {"switch": P("switch_id"), "value": "on"},
                     {"self_switch": P("self_switch"), "value": "on"}]},
                {"condition": {"self_switch_valid": True,
                               "self_switch_ch": P("self_switch")},
                 "graphic": _page_graphic(facing=P("on_facing_code")),
                 "trigger": 0, "priority_type": 1,
                 "commands": [
                     {"$when": "sound", "then": [{"se": P("sound")}]},
                     {"switch": P("switch_id"), "value": "off"},
                     {"self_switch": P("self_switch"), "value": "off"}]},
            ],
        }],
        "default_name": "Lever",
    },
}

# The names, in the order a caller reads them. A schema enum and `one_of` both
# take this, so the tool cannot offer a template the table lacks.
TEMPLATE_NAMES = list(TEMPLATES)


# ---------------------------------------------------------------------------
# Reading parameters
# ---------------------------------------------------------------------------


class _Reading:
    """What reading one call's parameters needs and collects."""

    __slots__ = ("ctx", "engine", "warnings")

    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.engine = ctx.project.engine
        self.warnings: list[str] = []


def _int(value: Any, label: str, low: int, high: int) -> int:
    """A whole number in range. A bool, a float and a string are not one."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ToolError("parameter %s must be a whole number, got %s"
                        % (label, echo(value)))
    if not low <= value <= high:
        raise ToolError("parameter %s must be %d to %d, got %s"
                        % (label, low, high, echo(value)))
    return value


def _clean(text: Any, label: str, reading: _Reading, limit: int) -> str:
    """One line of text a caller wrote, or the refusal that names it.

    Refused rather than edited: a stripped control character reports the same
    success as one that was never there. Control characters are refused
    because a line is a unit of the structure (the DSL splits a string on
    newlines, so one embedded in a "line" would silently become two), and
    because a message window draws them as nothing. Length is checked before
    anything walks the string.
    """
    if not isinstance(text, str):
        raise ToolError("parameter %s must be text, got %s"
                        % (label, echo(text)))
    if len(text) > limit:
        raise ToolError("parameter %s is %d characters; at most %d"
                        % (label, len(text), limit))
    for character in text:
        if unicodedata.category(character) in ("Cc", "Cs", "Zl", "Zp"):
            raise ToolError(
                "parameter %s holds a control character (U+%04X). Give each "
                "line as its own entry; control characters are not "
                "written." % (label, ord(character)))
    try:
        reading.ctx.codec.encode_strict(text)
    except ConversionError as exc:
        raise ToolError("parameter %s: %s" % (label, exc)) from exc
    return text


def _read_text(value: Any, spec: dict[str, Any], reading: _Reading) -> str:
    text = _clean(value, spec["name"], reading, MAX_TEMPLATE_LINE_CHARS)
    if not text.strip():
        raise ToolError("parameter %s is empty" % spec["name"])
    return text


def _read_lines(value: Any, spec: dict[str, Any], reading: _Reading
                ) -> list[str]:
    """Lines of speech: a list, or one string split on newlines."""
    name = spec["name"]
    limit, chars = spec["max_items"], spec["max_chars"]
    if isinstance(value, str):
        # Bounded before it is split, so the split is the bounded work.
        if len(value) > limit * (chars + 1):
            raise ToolError(
                "parameter %s is %d characters; at most %d lines of %d fit"
                % (name, len(value), limit, chars))
        value = value.split("\n")
    if not isinstance(value, list):
        raise ToolError("parameter %s must be a list of lines or one string, "
                        "got %s" % (name, echo(value)))
    if not 1 <= len(value) <= limit:
        raise ToolError("parameter %s takes 1 to %d lines, got %d"
                        % (name, limit, len(value)))
    lines = [_clean(line, "%s[%d]" % (name, index), reading, chars)
             for index, line in enumerate(value)]
    if not any(line.strip() for line in lines):
        raise ToolError("parameter %s has no text in it" % name)
    return lines


def _read_choice(value: Any, spec: dict[str, Any], reading: _Reading) -> str:
    if not isinstance(value, str) or value not in spec["choices"]:
        raise ToolError("parameter %s must be one of %s, got %s"
                        % (spec["name"], ", ".join(spec["choices"]),
                           echo(value)))
    return value


def _read_bool(value: Any, spec: dict[str, Any], reading: _Reading) -> bool:
    if not isinstance(value, bool):
        raise ToolError("parameter %s must be true or false, got %s"
                        % (spec["name"], echo(value)))
    return value


def _read_integer(value: Any, spec: dict[str, Any], reading: _Reading) -> int:
    return _int(value, spec["name"], spec["minimum"], spec["maximum"])


def _read_index(value: Any, spec: dict[str, Any], reading: _Reading) -> int:
    if reading.engine == "xp":
        raise ToolError(
            "parameter %s is not offered on RPG Maker XP, whose character "
            "sheets hold one character each" % spec["name"])
    return _int(value, spec["name"], spec["minimum"], spec["maximum"])


def _read_switch(value: Any, spec: dict[str, Any], reading: _Reading) -> int:
    number = _int(value, spec["name"], spec["minimum"], spec["maximum"])
    try:
        names = reading.ctx.project.load_data("system").get("switches")
    except (ProjectError, AttributeError):
        names = None
    if isinstance(names, list) and number >= len(names):
        reading.warnings.append(
            "parameter %s: switch %d is past the %d switches this project "
            "declares, so the editor's switch list will not show it; raise "
            "the switch count in the database (System)."
            % (spec["name"], number, max(len(names) - 1, 0)))
    return number


def _read_map(value: Any, spec: dict[str, Any], reading: _Reading) -> int:
    number = _int(value, spec["name"], spec["minimum"], spec["maximum"])
    if number not in reading.ctx.project.map_ids_present():
        raise ToolError("parameter %s: there is no map %d in this project"
                        % (spec["name"], number))
    return number


def _asset(kind: str, value: Any, name: str, reading: _Reading) -> str:
    """A file stem, checked against the folder it names.

    Refused when the project can see every asset it could use and the file is
    not among them; warned when it cannot (the RTP was not found, so the file
    may live there). Never a path: a separator is refused, so nothing a
    caller types is looked up as one.
    """
    stem = _clean(value, name, reading, MAX_TEMPLATE_ASSET_CHARS)
    if any(character in stem for character in '/\\:*?"<>|'):
        raise ToolError("parameter %s is a file name without its extension "
                        "or folder; %s has a path character in it"
                        % (name, echo(stem)))
    project = reading.ctx.project
    pool = project.asset_names(kind)
    if stem in pool:
        return stem
    folder = ASSET_DIRS[kind]
    folded = {entry.lower(): entry for entry in pool}
    near = folded.get(stem.lower())
    if near is not None:
        reading.warnings.append(
            "parameter %s: %s is spelled %s in %s. Windows ignores the "
            "difference; a case-sensitive filesystem does not."
            % (name, echo(stem), near, folder))
        return stem
    if project.assets_complete:
        raise ToolError(
            "parameter %s: there is no %s in %s (%d files here). Pass a file "
            "that exists, or add the file." % (name, echo(stem), folder,
                                               len(pool)))
    reading.warnings.append(
        "parameter %s: %s is not in %s; it may come from %s, which could not "
        "be located. Pass --rtp with that folder to check it."
        % (name, echo(stem), folder, " or ".join(project.rtp_unresolved)))
    return stem


def _read_character(value: Any, spec: dict[str, Any], reading: _Reading
                    ) -> "str | None":
    if value == "":
        return None
    return _asset("characters", value, spec["name"], reading)


def _read_face(value: Any, spec: dict[str, Any], reading: _Reading) -> str:
    if reading.engine == "xp":
        raise ToolError("parameter %s is not offered on RPG Maker XP, whose "
                        "Show Text has no face" % spec["name"])
    return _asset("faces", value, spec["name"], reading)


def _read_sound(value: Any, spec: dict[str, Any], reading: _Reading
                ) -> dict[str, Any]:
    name = spec["name"]
    if reading.engine == "xp":
        raise ToolError("parameter %s is not offered on RPG Maker XP, which "
                        "plays audio through RPG::AudioFile (see "
                        "command_reference)" % name)
    if not isinstance(value, dict):
        raise ToolError("parameter %s must be an object {name, volume, "
                        "pitch}, got %s" % (name, echo(value)))
    unknown = sorted(set(value) - {"name", "volume", "pitch"}, key=str)
    if unknown:
        raise ToolError("parameter %s takes name, volume and pitch; %s is "
                        "not one of them" % (name, echo(unknown[0])))
    if "name" not in value:
        raise ToolError("parameter %s needs a name" % name)
    return {"name": _asset("se", value["name"], name + ".name", reading),
            "volume": _int(value.get("volume", 100), name + ".volume",
                           *AUDIO_VOLUME),
            "pitch": _int(value.get("pitch", 100), name + ".pitch",
                          *AUDIO_PITCH)}


def _read_goods(value: Any, spec: dict[str, Any], reading: _Reading
                ) -> list[dict[str, Any]]:
    """Goods as the DSL's shop form takes them, each id checked to exist."""
    name = spec["name"]
    if not isinstance(value, list):
        raise ToolError("parameter %s must be a list, got %s"
                        % (name, echo(value)))
    if not 1 <= len(value) <= MAX_SHOP_GOODS:
        raise ToolError("parameter %s takes 1 to %d goods, got %d"
                        % (name, MAX_SHOP_GOODS, len(value)))
    out = []
    for index, good in enumerate(value):
        label = "%s[%d]" % (name, index)
        if not isinstance(good, dict):
            raise ToolError("parameter %s must be an object, got %s"
                            % (label, echo(good)))
        unknown = sorted(set(good) - set(_DATABASE_TABLES) - {"price"},
                         key=str)
        if unknown:
            raise ToolError("parameter %s takes item, weapon, armor and "
                            "price; %s is not one of them"
                            % (label, echo(unknown[0])))
        kinds = [kind for kind in _DATABASE_TABLES if kind in good]
        if len(kinds) != 1:
            raise ToolError("parameter %s takes exactly one of item, weapon "
                            "or armor, got %s"
                            % (label, ", ".join(kinds) or "none"))
        kind = kinds[0]
        entry_id = _int(good[kind], "%s.%s" % (label, kind), 1,
                        MAX_AUTHORED_ID)
        _database_name(reading, kind, entry_id, "%s.%s" % (label, kind))
        row: dict[str, Any] = {kind: entry_id}
        if "price" in good:
            row["price"] = _int(good["price"], label + ".price", 0, MAX_AMOUNT)
        out.append(row)
    return out


def _database_name(reading: _Reading, kind: str, entry_id: int,
                   label: str) -> str:
    """An item's, weapon's or armor's name, or the refusal that it is absent."""
    entry = reading.ctx.project.get_entry(_DATABASE_TABLES[kind], entry_id)
    if not isinstance(entry, RubyObject):
        raise ToolError("parameter %s: there is no %s %d in this project's "
                        "database" % (label, kind, entry_id))
    # A name is one line of a message: whitespace of any kind collapses to a
    # space, and a name that is not text at all (an Array in a damaged file
    # decodes to "<Array>") is replaced rather than quoted.
    raw = entry.get("name")
    name = (" ".join(decoded(reading.ctx, raw).split())
            if isinstance(raw, RubyString) else "")
    return name or "%s %d" % (kind, entry_id)


_READERS = {
    "integer": _read_integer, "index": _read_index, "text": _read_text,
    "lines": _read_lines, "choice": _read_choice, "boolean": _read_bool,
    "character": _read_character, "face": _read_face, "sound": _read_sound,
    "goods": _read_goods, "switch": _read_switch, "map": _read_map,
}


def _read_parameters(reading: _Reading, template: dict[str, Any],
                     given: dict[str, Any]) -> dict[str, Any]:
    """Every parameter of one template: defaults filled, each one validated."""
    specs = template["parameters"]
    known = {spec["name"] for spec in specs}
    if len(given) > _MAX_PARAMETER_KEYS:
        raise ToolError("parameters has %d entries; at most %d"
                        % (len(given), _MAX_PARAMETER_KEYS))
    unknown = sorted((key for key in given if key not in known), key=str)
    if unknown:
        raise ToolError(
            "this template has no parameter %s. Its parameters are %s."
            % (echo(unknown[0]), ", ".join(sorted(known))))
    values: dict[str, Any] = {}
    for spec in specs:
        name = spec["name"]
        value = given.get(name)
        if value is None:
            if spec["required"]:
                raise ToolError("parameter %s is required" % name)
            value = copy.deepcopy(spec["default"])
        values[name] = (None if value is None
                        else _READERS[spec["type"]](value, spec, reading))
    return values


# ---------------------------------------------------------------------------
# Deriving what a template needs beyond its parameters
# ---------------------------------------------------------------------------


def _reward(reading: _Reading, values: dict[str, Any]) -> dict[str, Any]:
    kind, amount = values["reward_type"], values["amount"]
    if kind == "gold":
        if values["reward_id"] is not None:
            raise ToolError("parameter reward_id is not used for a gold "
                            "reward; give amount")
        if "amount" not in values["_given"]:
            raise ToolError("parameter amount is required for a gold reward")
        return {"reward_entry": {"gold": {"value": amount}},
                "message_lines": ["Found %d gold!" % amount]}
    if values["reward_id"] is None:
        raise ToolError("parameter reward_id is required for a%s %s reward"
                        % ("n" if kind in ("item", "armor") else "", kind))
    name = _database_name(reading, kind, values["reward_id"], "reward_id")
    entry = {kind: {"id": values["reward_id"], "value": amount}}
    shown = name[:MAX_TEMPLATE_NAME_CHARS * 2]
    return {"reward_entry": entry,
            "message_lines": ["Found %s%s!" % (
                shown, " x%d" % amount if amount > 1 else "")]}


def _facing_codes(values: dict[str, Any], names: tuple[str, ...]
                  ) -> dict[str, int]:
    """`<name>_code` for each facing parameter, from the names a caller writes."""
    return {name + "_code": _ARRIVAL_FACINGS[values[name]] for name in names}


def _derive_chest(reading: _Reading, values: dict[str, Any]) -> dict[str, Any]:
    out = _reward(reading, values)
    if values["message"] is not None:
        out["message_lines"] = values["message"]
    out.update(_facing_codes(values, ("closed_facing", "opened_facing")))
    opened = values["opened_index"]
    out["opened_index_value"] = (values["graphic_index"] if opened is None
                                 else opened)
    return out


def _door_common(values: dict[str, Any]) -> dict[str, Any]:
    return {"trigger_code": _TRIGGERS[values["trigger"]],
            "priority": 1 if values["graphic"] else 0,
            "fade_code": _FADES[values["fade"]],
            **_facing_codes(values, ("direction",))}


def _check_xp_door(reading: _Reading, values: dict[str, Any]) -> None:
    # XP's sixth Transfer Player parameter is read as a transition choice the
    # DSL cannot say anything confirmed about, so only the default is written.
    if reading.engine == "xp" and values["fade"] != "black":
        raise ToolError("parameter fade is not offered on RPG Maker XP: "
                        "leave it at black")


def _check_arrival(reading: _Reading, values: dict[str, Any],
                   map_key: str, x_key: str, y_key: str) -> None:
    """The arrival tile has to be on the map it names."""
    game_map = reading.ctx.project.load_map(values[map_key])
    width, height = game_map.get("width"), game_map.get("height")
    if (not isinstance(width, int) or not isinstance(height, int)
            or not on_map(values[x_key], values[y_key], width, height)):
        raise ToolError(
            "parameters %s and %s: (%d, %d) is outside map %d, which is "
            "%sx%s. Tile coordinates start at (0, 0) in the top-left corner."
            % (x_key, y_key, values[x_key], values[y_key], values[map_key],
               width, height))


def _derive_door(reading: _Reading, values: dict[str, Any]) -> dict[str, Any]:
    _check_xp_door(reading, values)
    _check_arrival(reading, values, "target_map_id", "target_x", "target_y")
    return _door_common(values)


def _derive_door_pair(reading: _Reading, values: dict[str, Any]
                      ) -> dict[str, Any]:
    _check_xp_door(reading, values)
    _check_arrival(reading, values, "to_map_id", "to_x", "to_y")
    out = _door_common(values)
    out.update(_facing_codes(values, ("return_direction",)))
    return out


def _derive_npc(reading: _Reading, values: dict[str, Any]) -> dict[str, Any]:
    if (values["after_switch"] is None) != (values["after_lines"] is None):
        raise ToolError(
            "parameters after_switch and after_lines go together: the second "
            "page needs the switch that selects it and the words it says")
    return {}


def _derive_inn(reading: _Reading, values: dict[str, Any]) -> dict[str, Any]:
    price = values["price"]
    prompt = values["prompt"] or ["Stay the night for %d gold?" % price]
    return {"prompt_lines": prompt,
            "can_pay": "$game_party.gold >= %d" % price}


def _derive_lever(reading: _Reading, values: dict[str, Any]) -> dict[str, Any]:
    return _facing_codes(values, ("off_facing", "on_facing"))


def _derive_shop(reading: _Reading, values: dict[str, Any]) -> dict[str, Any]:
    return {}


_DERIVE = {
    "treasure_chest": _derive_chest, "door": _derive_door,
    "door_pair": _derive_door_pair, "sign": lambda r, v: {},
    "npc_dialogue": _derive_npc, "shop_npc": _derive_shop,
    "save_point": lambda r, v: {}, "inn": _derive_inn,
    "switch_toggle": _derive_lever,
}


# ---------------------------------------------------------------------------
# Expanding a template
# ---------------------------------------------------------------------------


def _present(value: Any) -> bool:
    return value is not None and value is not False and value != "" \
        and value != []


def _expand(node: Any, values: dict[str, Any]) -> Any:
    """A template's data with every hole filled. Pure; nothing is evaluated."""
    if isinstance(node, dict):
        if "$" in node:
            return copy.deepcopy(values[node["$"]])
        if "$fmt" in node:
            return node["$fmt"].format_map(values)
        out = {}
        for key, item in node.items():
            expanded = _expand(item, values)
            if expanded is not None:
                out[key] = expanded
        return out
    if isinstance(node, list):
        out_list: list[Any] = []
        for item in node:
            if isinstance(item, dict) and "$when" in item:
                chosen = item["then"] if _present(values.get(item["$when"])) \
                    else item.get("else", [])
                out_list.extend(_expand(chosen, values))
                continue
            expanded = _expand(item, values)
            if expanded is not None:
                out_list.append(expanded)
        return out_list
    return node


# ---------------------------------------------------------------------------
# Placing events
# ---------------------------------------------------------------------------


class _MapState:
    """One map being written: its events, the ids and tiles they use."""

    def __init__(self, ctx: Context, map_id: int, allow_overlap: bool):
        self.map_id = map_id
        self.game_map = ctx.project.load_map(map_id)
        events = self.game_map.get("events")
        if not isinstance(events, RubyHash):
            if events is not None:
                raise ToolError(
                    "map %d holds its events as %s rather than a Hash, so "
                    "this tool cannot add to them without discarding what is "
                    "there. Run validate to see what is wrong with the file."
                    % (map_id, type(events).__name__))
            events = RubyHash()
            self.game_map.set("events", events)
        self.events = events
        self.width = self.game_map.get("width") or 0
        self.height = self.game_map.get("height") or 0
        self.used: set[int] = set()
        self.at: dict[tuple[int, int], int] = {}
        budget = WalkBudget(_SCAN_STEPS)
        for key, event in events.items():
            if not budget.spend(1):
                raise ToolError(
                    "map %d has more events than this tool will scan (%d), "
                    "so it cannot tell which ids and tiles are free. Clear "
                    "the crafted events with validate's help, or place the "
                    "event with event_edit." % (map_id, _SCAN_STEPS))
            if isinstance(key, int) and not isinstance(key, bool):
                self.used.add(key)
            if isinstance(event, RubyObject):
                x, y = event.get("x"), event.get("y")
                if isinstance(x, int) and isinstance(y, int):
                    self.at.setdefault((x, y), key)
        self.allow_overlap = allow_overlap

    def next_id(self) -> int:
        for candidate in range(1, MAX_AUTHORED_ID + 1):
            if candidate not in self.used:
                return candidate
        raise ToolError("map %d already has %d events, which is as many as "
                        "it can hold" % (self.map_id, MAX_AUTHORED_ID))

    def check_tile(self, x: int, y: int) -> None:
        if (not isinstance(self.width, int) or not isinstance(self.height, int)
                or self.width < 1 or self.height < 1):
            raise ToolError(
                "map %d does not report a usable size (%r x %r), so an event "
                "position cannot be checked against it. Run validate to see "
                "what is wrong with the map."
                % (self.map_id, self.width, self.height))
        if not on_map(x, y, self.width, self.height):
            raise ToolError(
                "(%d, %d) is outside map %d, which is %dx%d. Tile coordinates "
                "start at (0, 0) in the top-left corner."
                % (x, y, self.map_id, self.width, self.height))
        taken = self.at.get((x, y))
        if taken is not None and not self.allow_overlap:
            raise ToolError(
                "map %d already has event %s at (%d, %d). Pick another tile, "
                "or pass allow_overlap=true to stack them on purpose."
                % (self.map_id, taken, x, y))


def _build_list(ctx: Context, entries: list[Any]) -> list[RubyObject]:
    """DSL entries compiled, with the blank command every list ends in."""
    try:
        commands = compile_commands(entries, ctx.codec,
                                    engine=ctx.project.engine)
    except CommandError as exc:
        raise ToolError(str(exc)) from exc
    commands.append(new_event_command(CODE_BLANK, 0, []))
    return commands


def _fill_page(ctx: Context, page: RubyObject, spec: dict[str, Any]
               ) -> list[RubyObject]:
    """Set one page's condition, graphic, trigger and priority, then its list."""
    patch = {key: spec[key] for key in ("condition", "graphic", "trigger",
                                        "priority_type")
             if spec.get(key) not in (None, {})}
    # XP's page has no priority_type (it has always_on_top, which a blank
    # page already has right): leave the field out rather than invent it.
    if "priority_type" in patch \
            and "priority_type" not in known_fields(ctx, page,
                                                    "RPG::Event::Page"):
        del patch["priority_type"]
    refuse_unknown_fields(patch, known_fields(ctx, page, "RPG::Event::Page"),
                          "a template page")
    if patch:
        apply_patch(page, patch, ctx.codec)
    commands = as_list(spec.get("commands"))
    if commands:
        page.set("list", _build_list(ctx, commands))
    return page.get("list")


def _make_event(ctx: Context, event_id: int, x: int, y: int,
                spec: dict[str, Any]) -> "tuple[RubyObject, list[list[Any]]]":
    """An event built the way event_edit builds one: create, then add_page."""
    profile = ctx.project.profile
    name = spec["name"]
    event = normalize_strings(profile.new_event(event_id, name, x, y),
                              ctx.codec)
    # Again through apply_patch, as event_edit's create does: the name has to
    # be refused, not mangled, if the project's code page cannot hold it.
    apply_patch(event, {"name": name}, ctx.codec)
    pages = event.get("pages")
    lists = []
    for index, page_spec in enumerate(spec["pages"]):
        if index == 0:
            page = pages[0]
        else:
            page = normalize_strings(profile.new_page(), ctx.codec)
        lists.append(_fill_page(ctx, page, page_spec))
        if index:
            pages.append(page)
    event.set("pages", pages)
    return event, lists


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------


def _template(args: dict[str, Any]) -> "tuple[str, dict[str, Any]]":
    name = one_of(args, "template", TEMPLATE_NAMES)
    return name, TEMPLATES[name]


def _parameter_row(spec: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(spec)


def _engine_note(template: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"engines": list(template["engines"])}
    if template.get("why_not_others"):
        out["not_offered_elsewhere"] = template["why_not_others"]
    return out


def _list_templates() -> dict[str, Any]:
    return {"templates": [
        {"template": name, "summary": record["summary"],
         "creates": len(record["events"]),
         "required": [s["name"] for s in record["parameters"]
                      if s["required"]],
         "parameters": [s["name"] for s in record["parameters"]],
         **_engine_note(record)}
        for name, record in TEMPLATES.items()]}


def _describe(name: str, record: dict[str, Any]) -> dict[str, Any]:
    """One template, as data and stable: nothing here reads a project.

    `events` is the table itself, with `{"$": parameter}` where a caller's
    value goes and `{"$when": parameter, "then": [...]}` for a part that is
    present only when that parameter is given. The values the tool derives
    beyond the parameters (a database name, a facing's number) are named in
    `derived`. The exact command list for one set of parameters is `preview`.
    """
    return {
        "template": name,
        "summary": record["summary"],
        **_engine_note(record),
        "default_event_name": record["default_name"],
        "parameters": [_parameter_row(s) for s in record["parameters"]],
        "events": copy.deepcopy(record["events"]),
        "notes": (
            "The event name is `name`, default %r. A `{\"$\": X}` hole is "
            "filled with parameter X or a value the tool derives from the "
            "parameters; `{\"$when\": X, \"then\": [...]}` keeps its entries "
            "only when X is given (and `else` otherwise). The commands are "
            "the DSL event_edit takes, compiled for the open engine."
            % record["default_name"]),
    }


def _plan(reading: _Reading, name: str, record: dict[str, Any],
          args: dict[str, Any], map_id: int, x: int, y: int
          ) -> list[dict[str, Any]]:
    """The events a call would create, as expanded data. Writes nothing."""
    given = opt_dict(args, "parameters")
    values = _read_parameters(reading, record, given)
    values["_given"] = {key for key in given if given[key] is not None}
    event_name = opt_str(args, "name")
    if event_name is None:
        event_name = record["default_name"]
    else:
        event_name = _clean(event_name, "name", reading,
                            MAX_TEMPLATE_NAME_CHARS)
        if not event_name.strip():
            raise ToolError("name is empty")
    values["event_name"] = event_name
    values["here_map_id"], values["here_x"], values["here_y"] = map_id, x, y
    values.update(_DERIVE[name](reading, values))
    del values["_given"]
    events = _expand(copy.deepcopy(record["events"]), values)
    for event in events:
        if event["at"] == "here":
            event["map_id"], event["x"], event["y"] = map_id, x, y
        else:
            event["map_id"] = values["to_map_id"]
            event["x"], event["y"] = values["to_x"], values["to_y"]
    return events


def _event_row(placed: dict[str, Any], detail: bool, ctx: Context
               ) -> dict[str, Any]:
    row: dict[str, Any] = {
        "map_id": placed["map_id"], "event_id": placed["event_id"],
        "x": placed["x"], "y": placed["y"], "name": placed["name"],
        "page_count": len(placed["pages"])}
    if detail:
        row["pages"] = [
            {"page": index,
             **{key: page[key] for key in ("condition", "graphic", "trigger",
                                           "priority_type") if key in page},
             "commands": page.get("commands", []),
             "script": render_script(placed["lists"][index], ctx.codec,
                                     ctx.project.engine)}
            for index, page in enumerate(placed["pages"])]
    return row


def _write_events(ctx: Context, plan: list[dict[str, Any]],
                  allow_overlap: bool, dry_run: bool, detail: bool,
                  label: str) -> "tuple[list[dict[str, Any]], list[dict[str, Any]]]":
    files = list(dict.fromkeys(
        ctx.project.map_file(event["map_id"]) for event in plan))
    rows: list[dict[str, Any]] = []
    with ctx.staged(*files) as landed:
        states: dict[int, _MapState] = {}
        for event in plan:
            map_id = event["map_id"]
            if map_id not in states:
                states[map_id] = _MapState(ctx, map_id, allow_overlap)
            state = states[map_id]
            state.check_tile(event["x"], event["y"])
            event_id = state.next_id()
            built, lists = _make_event(ctx, event_id, event["x"], event["y"],
                                       event)
            state.events.set(event_id, built)
            state.used.add(event_id)
            state.at.setdefault((event["x"], event["y"]), event_id)
            rows.append(_event_row({**event, "event_id": event_id,
                                    "lists": lists}, detail, ctx))
        summary = "%s: %s" % (label, ", ".join(
            "event %d on map %d" % (r["event_id"], r["map_id"])
            for r in rows))
        writes: list[dict[str, Any]] = []
        for filename in files:
            try:
                writes.append(landed.save(
                    filename, dry_run=dry_run, tool="event_template",
                    summary=summary))
            except ProjectError as exc:
                if not writes:
                    raise
                # A later file refused after an earlier one landed: the
                # caller has to be told which is which, because the
                # exception alone reads as though nothing was written.
                raise ToolError(
                    "%s was written, but %s was not: %s. The first file's "
                    "event is in place and the rest are not; remove it with "
                    "event_edit, or restore the file from the backup the "
                    "write took (backups)."
                    % (", ".join(w["file"] for w in writes), filename,
                       exc)) from exc
    return rows, writes


@tool(
    "event_template",
    "Create events from built-in templates",
    "Create a whole event from a template: treasure_chest, door, door_pair "
    "(both sides of a doorway on two maps), sign, npc_dialogue, shop_npc, "
    "save_point, inn and switch_toggle (a lever). Each is a page or two of the "
    "same commands event_edit would write, with the names, ids and words as "
    "parameters; the event is placed through the same constructors, so the "
    "bytes equal what event_edit create / add_page would write. Ops: list "
    "(the templates), describe (one template: its parameters and the "
    "command table it fills in), preview (the pages and commands a call "
    "would write, with the events' ids; writes nothing) and create (writes). "
    "Place with map_id, x and y; the event takes the map's next free id, must "
    "be on the map, and is refused on another event's tile unless "
    "allow_overlap. A parameter that is missing, unknown, out of range or "
    "names something absent from the database or the asset folders is refused "
    "by name (a graphic or sound the project cannot see because the RTP was "
    "not found is warned about instead, in \"warnings\"). Text is at most "
    "%d lines of %d characters, with no control characters; an event name "
    "at most %d; an asset name at most %d; goods at most %d. A template whose "
    "commands the open engine is not confirmed for (items and shops on XP) is "
    "refused by the same check event_edit makes. It does not edit an existing "
    "event (use event_edit), does not draw the map around the event, and "
    "takes no user-defined templates. A door_pair writes two maps in one call; "
    "if the second write is refused after the first landed, the error says "
    "which file was written." % (
        MAX_TEMPLATE_LINES, MAX_TEMPLATE_LINE_CHARS, MAX_TEMPLATE_NAME_CHARS,
        MAX_TEMPLATE_ASSET_CHARS, MAX_SHOP_GOODS),
    schema({
        "op": prop(STR, "list, describe, preview or create.",
                   enum=list(TEMPLATE_OP_ARGS)),
        "template": prop(STR, "Which template (describe, preview, create).",
                         enum=TEMPLATE_NAMES),
        "map_id": prop(INT, "The map to place the event on (preview, "
                            "create).", minimum=1, maximum=MAX_MAP_ID),
        "x": prop(INT, "Tile X of the event (preview, create).", minimum=0,
                  maximum=MAX_COORDINATE),
        "y": prop(INT, "Tile Y of the event (preview, create).", minimum=0,
                  maximum=MAX_COORDINATE),
        "parameters": prop(OBJ, "The template's parameters by name; describe "
                                "lists them with their types, bounds and "
                                "defaults. At most 40 keys."),
        "name": prop(STR, "The event's name in the editor (default: the "
                          "template's), at most %d characters. A door_pair's "
                          "second door is named \"<name> (return)\"."
                     % MAX_TEMPLATE_NAME_CHARS,
                     maxLength=MAX_TEMPLATE_NAME_CHARS),
        "allow_overlap": prop(BOOL, "Allow the event on a tile another "
                                    "event already stands on."),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def event_template(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """List, describe, preview or create an event from a built-in template."""
    op = one_of(args, "op", list(TEMPLATE_OP_ARGS))
    check_op_args(op, args, TEMPLATE_OP_ARGS, "event_template")
    if op == "list":
        return _list_templates()
    if args.get("template") is None:
        raise ToolError("template is required; choose one of %s"
                        % ", ".join(TEMPLATE_NAMES))
    name, record = _template(args)
    if op == "describe":
        return _describe(name, record)

    map_id = need_int(args, "map_id", 1, MAX_MAP_ID)
    x = need_int(args, "x", 0, MAX_COORDINATE)
    y = need_int(args, "y", 0, MAX_COORDINATE)
    preview = op == "preview"
    dry_run = True if preview else opt_bool(args, "dry_run")
    reading = _Reading(ctx)
    if map_id not in ctx.project.map_ids_present():
        raise ToolError("there is no map %d in this project" % map_id)
    plan = _plan(reading, name, record, args, map_id, x, y)
    rows, writes = _write_events(
        ctx, plan, opt_bool(args, "allow_overlap"), dry_run, preview,
        "event_template %s" % name)
    reply: dict[str, Any] = {"template": name, "created": rows,
                             "writes": writes}
    if reading.warnings:
        reply["warnings"] = reading.warnings
    return write_reply(reply, dry_run)
