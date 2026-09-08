"""What lives in an RPG Maker VX Ace (or VX) project, and how to build new pieces.

Three kinds of knowledge live here, deliberately kept apart:

* **Documented** structure, from the RGSS reference that ships with the editor.
  Used for field descriptions and templates.
* **Engine profile.** VX Ace and VX share a serialisation format and most of
  their class model, but not all of it, so each engine gets its own file
  registry, map layer meanings and templates.
* **Observed** structure, read back from the project on disk by
  :func:`observe_class_fields`. When documentation and project disagree, the
  project wins -- a file's real instance variables are the only authority for
  what the editor will accept.

The differences that matter between the two engines:

===================  =============================  ========================
                     VX Ace (RGSS3, ``.rvdata2``)   VX (RGSS2, ``.rvdata``)
===================  =============================  ========================
Tilesets             ``Tilesets.rvdata2`` database  one global tileset, no
                     and ``RPG::Map#tileset_id``    database, no tileset_id
Encounter areas      none (regions on map layer 3)  ``Areas.rvdata``
Map tile layers      4 (2 tiles, shadow, region)    3 (2 tiles, shadow)
Audio references     ``RPG::BGM``/``BGS``/``ME``/   ``RPG::AudioFile``
                     ``SE``
Item/actor stats     ``features`` everywhere;       flat fields, parameter
                     ``params`` array on weapons,   curves as Tables
                     armors, enemies; ``params``
                     Table on classes; damage by
                     Ruby ``formula`` string
Event commands       adds 104, 105, 137, 138, 216,  --
                     217, 243, 244, 261, 281-285,
                     324 and the 405 continuation
Strings              Ruby 1.9: carry an encoding    Ruby 1.8: bare bytes
===================  =============================  ========================
"""

from __future__ import annotations

from typing import Any, Literal

from .rubytypes import (
    RubyData,
    RubyExtended,
    RubyHash,
    RubyIvars,
    RubyObject,
    RubyString,
    RubyStruct,
    RubySymbol,
    RubyUClass,
    RubyUserDef,
    RubyUserMarshal,
    Table,
    _bare_name,
)

DATA_DIR = "Data"

# Marker for how a data file's top-level container is shaped.
ARRAY, HASH, SINGLE, SCRIPTS = "array", "hash", "single", "scripts"
Container = Literal["array", "hash", "single", "scripts"]


class DataFile:
    """One file under ``Data/``."""

    __slots__ = ("key", "stem", "container", "cls", "description")

    def __init__(self, key: str, stem: str, container: Container, cls: str,
                 description: str):
        self.key = key
        self.stem = stem
        self.container = container
        self.cls = cls
        self.description = description

    def filename(self, extension: str) -> str:
        return self.stem + extension


def _data_file_registry(*entries: DataFile) -> dict[str, DataFile]:
    """Index data files by the short name the tools accept."""
    return {entry.key: entry for entry in entries}


# Tables both engines have. Class names that differ are patched per profile.
_SHARED_FILES = [
    DataFile("actors", "Actors", ARRAY, "RPG::Actor", "Playable characters"),
    DataFile("classes", "Classes", ARRAY, "RPG::Class",
             "Character classes, their skills and parameter curves"),
    DataFile("skills", "Skills", ARRAY, "RPG::Skill",
             "Skills usable by actors and enemies"),
    DataFile("items", "Items", ARRAY, "RPG::Item", "Consumable and key items"),
    DataFile("weapons", "Weapons", ARRAY, "RPG::Weapon", "Weapons"),
    DataFile("armors", "Armors", ARRAY, "RPG::Armor", "Armour and accessories"),
    DataFile("enemies", "Enemies", ARRAY, "RPG::Enemy", "Enemy definitions"),
    DataFile("troops", "Troops", ARRAY, "RPG::Troop",
             "Enemy groups used by encounters, plus their battle event pages"),
    DataFile("states", "States", ARRAY, "RPG::State",
             "Status effects (poison, sleep, ...)"),
    DataFile("animations", "Animations", ARRAY, "RPG::Animation",
             "Battle and map animations"),
    DataFile("common_events", "CommonEvents", ARRAY, "RPG::CommonEvent",
             "Shared event pages callable from anywhere"),
    DataFile("system", "System", SINGLE, "RPG::System",
             "Party start position, switch and variable names, terms, vehicles"),
    DataFile("map_infos", "MapInfos", HASH, "RPG::MapInfo",
             "The map tree: names, parents and ordering"),
    DataFile("scripts", "Scripts", SCRIPTS, "-",
             "The script editor contents, zlib-compressed"),
]

ACE_DATA_FILES = _data_file_registry(
    *_SHARED_FILES,
    DataFile("tilesets", "Tilesets", ARRAY, "RPG::Tileset",
             "Tilesets: their nine graphic files and per-tile passage flags"),
)

VX_DATA_FILES = _data_file_registry(
    *_SHARED_FILES,
    DataFile("areas", "Areas", HASH, "RPG::Area",
             "Encounter areas (VX only; VX Ace replaced these with map regions)"),
)

# Every table any supported engine knows, for tool schemas that have to be
# built before a project is open. The runtime check against the open project's
# own profile is what rejects a table this engine does not have.
_ALL_DATA_FILES = {**VX_DATA_FILES, **ACE_DATA_FILES}
ALL_TABLE_KEYS = sorted(_ALL_DATA_FILES)
ALL_DATABASE_TABLES = sorted(
    key for key, entry in _ALL_DATA_FILES.items() if entry.container in (ARRAY, HASH)
)


# The editor numbers maps from 1 and its tree tops out well below this; the
# bound exists so a bad id cannot build an absurd filename.
MAX_MAP_ID = 9999


def map_filename(map_id: int, extension: str) -> str:
    """``5`` -> ``Map005.rvdata2``. Ids above 999 keep growing, as the editor does."""
    # A float would truncate silently and address a *different* map, so require
    # a real integer rather than anything int()-able.
    if isinstance(map_id, bool) or not isinstance(map_id, int):
        raise ValueError("map id must be a whole number, got %r" % (map_id,))
    if not 1 <= map_id <= MAX_MAP_ID:
        raise ValueError(
            "map id %d is outside 1..%d" % (map_id, MAX_MAP_ID)
        )
    return "Map%03d%s" % (map_id, extension)


# ---------------------------------------------------------------------------
# Documented class fields
# ---------------------------------------------------------------------------

# Classes whose structure is identical in both engines.
# The six flags on RPG::Event::Page::Condition. A page with none of them set
# has no conditions, which matters because RPG Maker runs the LAST page whose
# conditions match: an unconditional page hides every page before it. Listed
# here so the field docs, the constructor and the validate rule agree.
PAGE_CONDITION_FLAGS = ("switch1_valid", "switch2_valid", "variable_valid",
                        "self_switch_valid", "item_valid", "actor_valid")

# RPG::Event::Page trigger values.
TRIGGER_ACTION, TRIGGER_PLAYER_TOUCH, TRIGGER_EVENT_TOUCH = 0, 1, 2
TRIGGER_AUTORUN, TRIGGER_PARALLEL = 3, 4

# What each trigger is called in a reply. Here rather than in a tool, because
# both event_get and map_get print it into their own descriptions of the same
# page: written out separately in each, a reworded name in one made the two
# tools disagree about what a page does.
TRIGGER_NAMES = {
    TRIGGER_ACTION: "action button",
    TRIGGER_PLAYER_TOUCH: "player touch",
    TRIGGER_EVENT_TOUCH: "event touch",
    TRIGGER_AUTORUN: "autorun",
    TRIGGER_PARALLEL: "parallel process",
}

# The nine tileset sheets an RPG::Tileset names, in @tileset_names order.
TILESET_SHEET_NAMES = ("A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E")

SHARED_CLASS_FIELDS: dict[str, dict[str, str]] = {
    "RPG::MapInfo": {
        "name": "Map name as shown in the map tree",
        "parent_id": "Parent map id, 0 at the tree root",
        "order": "Position in the map tree (editor bookkeeping)",
        "expanded": "Whether the tree node is expanded (editor bookkeeping)",
        "scroll_x": "Editor scroll position",
        "scroll_y": "Editor scroll position",
    },
    "RPG::Event": {
        "id": "Event id, unique within the map",
        "name": "Event name (editor only, not shown in game)",
        "x": "Tile X coordinate",
        "y": "Tile Y coordinate",
        "pages": "Array of RPG::Event::Page, evaluated last to first",
    },
    "RPG::Event::Page": {
        "condition": "RPG::Event::Page::Condition gating this page",
        "graphic": "RPG::Event::Page::Graphic shown for the event",
        "move_type": "0: fixed, 1: random, 2: approach, 3: custom",
        "move_speed": "1: x8 slower ... 4: normal ... 6: x4 faster",
        "move_frequency": "1: lowest ... 3: normal ... 5: highest",
        "move_route": "RPG::MoveRoute, used when move_type is 3 (custom)",
        "walk_anime": "[Walking Anim.] option",
        "step_anime": "[Stepping Anim.] option",
        "direction_fix": "[Direction Fix] option",
        "through": "[Through] option",
        "priority_type": "0: below characters, 1: same as characters, 2: above",
        "trigger": "0: action button, 1: player touch, 2: event touch, "
                   "3: autorun, 4: parallel process",
        "list": "Array of RPG::EventCommand",
    },
    "RPG::Event::Page::Condition": {
        "switch1_valid": "Whether the first switch condition applies",
        "switch2_valid": "Whether the second switch condition applies",
        "variable_valid": "Whether the variable condition applies",
        "self_switch_valid": "Whether the self switch condition applies",
        "item_valid": "Whether the item condition applies",
        "actor_valid": "Whether the actor condition applies",
        "switch1_id": "First switch id",
        "switch2_id": "Second switch id",
        "variable_id": "Variable id",
        "variable_value": "Page runs when the variable is this value or greater",
        "self_switch_ch": "Self switch letter, A to D",
        "item_id": "Required item id",
        "actor_id": "Required party member id",
    },
    "RPG::Event::Page::Graphic": {
        "tile_id": "Tile id when the event is drawn as a tile, else 0",
        "character_name": "Character sprite file name in Graphics/Characters",
        "character_index": "Which of the 8 sprites in the file (0..7)",
        "direction": "2: down, 4: left, 6: right, 8: up",
        "pattern": "Animation frame (0..2)",
    },
    "RPG::MoveRoute": {
        "repeat": "[Repeat Action] option",
        "skippable": "[Skip If Cannot Move] option",
        "wait": "[Wait for Completion] option",
        "list": "Array of RPG::MoveCommand",
    },
    "RPG::EventCommand": {
        "code": "Command code; 0 marks a blank line",
        "indent": "Nesting depth, driven by branch and loop commands",
        "parameters": "Array whose shape depends on code",
    },
    "RPG::MoveCommand": {
        "code": "Move command code",
        "parameters": "Array whose shape depends on code",
    },
    "RPG::CommonEvent": {
        "id": "Common event id",
        "name": "Common event name",
        "trigger": "0: none, 1: autorun, 2: parallel",
        "switch_id": "Switch that enables an autorun or parallel common event",
        "list": "Array of RPG::EventCommand",
    },
}

_MAP_FIELDS_SHARED = {
    "width": "Map width in tiles",
    "height": "Map height in tiles",
    "scroll_type": "0: no loop, 1: vertical loop, 2: horizontal loop, 3: both",
    "autoplay_bgm": "Whether BGM switches automatically on entry",
    "bgm": "Music played when autoplay_bgm is true",
    "autoplay_bgs": "Whether BGS switches automatically on entry",
    "bgs": "Ambient sound played when autoplay_bgs is true",
    "disable_dashing": "Whether the [Disable Dashing] option is set",
    "encounter_list": "Encounter entries for this map",
    "encounter_step": "Average number of steps between encounters",
    "parallax_name": "Parallax background graphic file name",
    "parallax_loop_x": "Parallax [Loop Horizontal] option",
    "parallax_loop_y": "Parallax [Loop Vertical] option",
    "parallax_sx": "Parallax automatic X scroll speed",
    "parallax_sy": "Parallax automatic Y scroll speed",
    "parallax_show": "Parallax [Show in the Editor] option",
    "data": "Table of tile ids",
    "events": "Hash of event id => RPG::Event",
}

ACE_CLASS_FIELDS: dict[str, dict[str, str]] = {
    "RPG::Map": {**_MAP_FIELDS_SHARED, **{
        "tileset_id": "Which RPG::Tileset this map draws with",
        "display_name": "Name shown to the player on entry (blank for none)",
        "note": "The map's Note field, free text for the developer or scripts",
        "specify_battleback": "Whether this map overrides the battle background",
        "battleback1_name": "Battle background floor graphic",
        "battleback2_name": "Battle background wall graphic",
        "data": "Table of tile ids, width x height x 4 layers",
        "encounter_list": "Array of RPG::Map::Encounter (troop, weight, region set)",
    }},
    "RPG::Tileset": {
        "id": "Tileset id",
        "name": "Tileset name in the database",
        "mode": "Observed values are 0 and 1, understood as field type and area "
                "type; not confirmed against the reference",
        "note": "Free text Note field",
        "tileset_names": "Nine graphic file names, in order: "
                         "A1, A2, A3, A4, A5, B, C, D, E",
        "flags": "Table of 8192 per-tile flag words, one per tile id; see the "
                 "advisory flag-bit table for what the bits mean",
    },
    "RPG::BGM": {"name": "File name in Audio/BGM", "volume": "0..100",
                 "pitch": "50..150"},
    "RPG::BGS": {"name": "File name in Audio/BGS", "volume": "0..100",
                 "pitch": "50..150"},
    "RPG::ME": {"name": "File name in Audio/ME", "volume": "0..100",
                "pitch": "50..150"},
    "RPG::SE": {"name": "File name in Audio/SE", "volume": "0..100",
                "pitch": "50..150"},
    "RPG::BaseItem::Feature": {
        "code": "What the feature affects (parameter, element rate, skill, ...)",
        "data_id": "Which thing of that kind",
        "value": "The feature's magnitude",
    },
    "RPG::UsableItem::Damage": {
        "type": "0: none, 1: HP damage, 2: MP damage, 3: HP recover, "
                "4: MP recover, 5: HP drain, 6: MP drain",
        "element_id": "Damage element",
        "formula": "Ruby expression evaluated for the damage value",
        "variance": "Random spread, as a percentage",
        "critical": "Whether critical hits apply",
    },
    "RPG::UsableItem::Effect": {
        "code": "Which effect (recover HP, add state, learn skill, ...)",
        "data_id": "Which thing of that kind",
        "value1": "First magnitude",
        "value2": "Second magnitude",
    },
    "RPG::Troop::Page": {
        "condition": "RPG::Troop::Page::Condition gating this battle event page",
        "span": "0: battle, 1: turn, 2: moment",
        "list": "Array of RPG::EventCommand",
    },
}

VX_CLASS_FIELDS: dict[str, dict[str, str]] = {
    "RPG::Map": {**_MAP_FIELDS_SHARED, **{
        "data": "Table of tile ids, width x height x 3 layers",
        "encounter_list": "Array of troop ids that can be encountered",
    }},
    "RPG::AudioFile": {
        "name": "File name without extension",
        "volume": "Volume, 0..100",
        "pitch": "Pitch, 50..150",
    },
    "RPG::Area": {
        "id": "Area id",
        "name": "Area name",
        "map_id": "The map the area belongs to",
        "rect": "Rect covering the area",
        "encounter_list": "Array of troop ids encountered inside the area",
        "order": "Position in the map tree (editor bookkeeping)",
    },
}

# Tile id ranges, shared by both engines (VX introduced the layout, Ace kept
# it). The ids between these bands (1024-1535, 1664-2047) are not used by the
# tilemap, which is why the ranges are not contiguous.
TILE_ID_RANGES_NOTE = (
    "Advisory: derived from how the RGSS tilemap addresses its sheets, not from "
    "the reference manual. Read a working map with map_tiles before relying on "
    "a specific number."
)
TILE_ID_RANGES = {
    "B": (0, 255),
    "C": (256, 511),
    "D": (512, 767),
    "E": (768, 1023),
    "A5": (1536, 1663),
    "A1": (2048, 2815),
    "A2": (2816, 4351),
    "A3": (4352, 5887),
    "A4": (5888, 8191),
}

ACE_MAP_LAYERS = {
    0: "lower layer (ground, A tiles)",
    1: "upper layer (B-E tiles placed over the ground)",
    2: "shadow layer (4-bit mask per tile, 0 for none)",
    3: "region layer (region id 0-63, 0 for none)",
}

VX_MAP_LAYERS = {
    0: "lower layer (ground, A tiles)",
    1: "upper layer (B-E tiles placed over the ground)",
    2: "shadow layer (4-bit mask per tile, 0 for none)",
}

# Bits in an RPG::Tileset flags word. The keys are rendered for people to read,
# not to mask with -- use the integer literals if you need to test a bit.
TILESET_FLAG_BITS_NOTE = (
    "Advisory: taken from how RGSS3 interprets these bits at runtime, not from "
    "the reference manual."
)
TILESET_FLAG_BITS = {
    "0x000f": "passage: 1 blocks down, 2 left, 4 right, 8 up (15 blocks all)",
    "0x0010": "star -- drawn above the character",
    "0x0020": "ladder",
    "0x0040": "bush",
    "0x0080": "counter",
    "0x0100": "damage floor",
    "0x0200": "boat cannot pass",
    "0x0400": "ship cannot pass",
    "0x0800": "airship cannot land",
    "0xf000": "terrain tag (0-7), in the high nibble",
}


# ---------------------------------------------------------------------------
# Templates for new objects
# ---------------------------------------------------------------------------


def rstr(text: str = "", encoding: str = "utf-8") -> RubyString:
    """A RubyString holding ``text``, for building object templates.

    The default encoding suits the ASCII literals templates are made of. Text
    that came from a caller must go through the project's own
    ``TextCodec.make_string`` instead, so it lands in the project's code page
    and carries the engine's string form.
    """
    return RubyString(text.encode(encoding, "surrogateescape"))


def new_event_command(code: int = 0, indent: int = 0,
                      parameters: list[Any] | None = None) -> RubyObject:
    return RubyObject("RPG::EventCommand", {
        "@code": code, "@indent": indent, "@parameters": list(parameters or []),
    })


def new_move_command(code: int = 0, parameters: list[Any] | None = None) -> RubyObject:
    return RubyObject("RPG::MoveCommand", {
        "@code": code, "@parameters": list(parameters or []),
    })


def new_move_route() -> RubyObject:
    return RubyObject("RPG::MoveRoute", {
        "@repeat": True,
        "@skippable": False,
        "@wait": False,
        "@list": [new_move_command(0)],
    })


def new_condition() -> RubyObject:
    return RubyObject("RPG::Event::Page::Condition", {
        **{"@" + flag: False for flag in PAGE_CONDITION_FLAGS},
        "@switch1_id": 1,
        "@switch2_id": 1,
        "@variable_id": 1,
        "@variable_value": 0,
        "@self_switch_ch": rstr("A"),
        "@item_id": 1,
        "@actor_id": 1,
    })


def new_graphic() -> RubyObject:
    return RubyObject("RPG::Event::Page::Graphic", {
        "@tile_id": 0,
        "@character_name": rstr(""),
        "@character_index": 0,
        "@direction": 2,
        "@pattern": 0,
    })


def new_page() -> RubyObject:
    return RubyObject("RPG::Event::Page", {
        "@condition": new_condition(),
        "@graphic": new_graphic(),
        "@move_type": 0,
        "@move_speed": 3,
        "@move_frequency": 3,
        "@move_route": new_move_route(),
        "@walk_anime": True,
        "@step_anime": False,
        "@direction_fix": False,
        "@through": False,
        "@priority_type": 0,
        "@trigger": 0,
        # A page always ends with a blank command; the interpreter relies on it.
        "@list": [new_event_command(0, 0, [])],
    })


def new_event(event_id: int, name: str = "", x: int = 0, y: int = 0) -> RubyObject:
    return RubyObject("RPG::Event", {
        "@id": event_id,
        "@name": rstr(name or "EV%03d" % event_id),
        "@x": x,
        "@y": y,
        "@pages": [new_page()],
    })


def new_map_info(name: str, parent_id: int = 0, order: int = 0) -> RubyObject:
    return RubyObject("RPG::MapInfo", {
        "@name": rstr(name),
        "@parent_id": parent_id,
        "@order": order,
        "@expanded": False,
        "@scroll_x": 0,
        "@scroll_y": 0,
    })


# ---------------------------------------------------------------------------
# Engine profiles
# ---------------------------------------------------------------------------


class EngineProfile:
    """Everything about a project that depends on which engine made it."""

    __slots__ = ("key", "label", "project_file", "extension", "data_files",
                 "class_fields", "map_layers", "tile_layer_count", "audio_classes",
                 "string_ivars", "map_only_ivars", "notes")

    def __init__(self, key: str, label: str, project_file: str, extension: str,
                 data_files: dict[str, DataFile], class_fields: dict[str, dict[str, str]],
                 map_layers: dict[int, str], audio_classes: dict[str, str],
                 string_ivars: dict[str, Any], map_only_ivars: tuple[str, ...],
                 notes: list[str]):
        self.key = key
        self.label = label
        self.project_file = project_file
        self.extension = extension
        self.data_files = data_files
        self.class_fields = class_fields
        self.map_layers = map_layers
        self.tile_layer_count = len(map_layers)
        self.audio_classes = audio_classes
        # The ivar record this engine's Ruby puts on every string.
        self.string_ivars = dict(string_ivars)
        # RPG::Map fields only this engine has, added to the shared template.
        self.map_only_ivars = tuple(map_only_ivars)
        self.notes = notes

    # -- capabilities ---------------------------------------------------------
    # Named so that nothing outside this module has to test an engine key.
    @property
    def has_tileset_database(self) -> bool:
        return "tilesets" in self.data_files

    @property
    def has_encounter_areas(self) -> bool:
        return "areas" in self.data_files

    @property
    def has_region_layer(self) -> bool:
        return self.tile_layer_count > 3

    @property
    def strings_carry_encoding(self) -> bool:
        return bool(self.string_ivars)

    # -- files ----------------------------------------------------------------
    def data_file(self, key: str) -> DataFile:
        entry = self.data_files.get(key)
        if entry is None:
            if key in ALL_TABLE_KEYS:
                raise KeyError(
                    "%s has no %s table. %s"
                    % (self.label, key, self.absence_hint(key))
                )
            raise KeyError(
                "unknown data table %r; this project has: %s"
                % (key, ", ".join(sorted(self.data_files)))
            )
        return entry

    def absence_hint(self, key: str) -> str:
        """Why this engine has no such table, and what to use instead."""
        return _TABLE_ABSENCE_HINTS.get(
            key, "It belongs to a different engine in the VX family.")

    def filename_for(self, key: str) -> str:
        return self.data_file(key).filename(self.extension)

    def map_filename(self, map_id: int) -> str:
        return map_filename(map_id, self.extension)

    def database_tables(self) -> list[str]:
        return sorted(k for k, f in self.data_files.items()
                      if f.container in (ARRAY, HASH))

    # -- objects --------------------------------------------------------------
    def new_audio_file(self, kind: str = "bgm", name: str = "",
                       volume: int = 100, pitch: int = 100) -> RubyObject:
        """Build the audio object this engine uses for ``kind``."""
        cls = self.audio_classes.get(kind)
        if cls is None:
            raise ValueError(
                "unknown audio kind %r; this engine has %s"
                % (kind, ", ".join(sorted(self.audio_classes)))
            )
        return RubyObject(cls, {
            "@name": rstr(name), "@volume": volume, "@pitch": pitch,
        })

    def new_map(self, width: int = 20, height: int = 15,
                tileset_id: int = 1) -> RubyObject:
        """A blank ``RPG::Map`` carrying exactly this engine's fields.

        Built as the full field set, then reduced to the ones this engine has,
        so ``map_only_ivars`` is the single place that records the difference.
        """
        ivars: dict[str, Any] = {
            "@display_name": rstr(""),
            "@tileset_id": tileset_id,
            "@width": width,
            "@height": height,
            "@scroll_type": 0,
            "@specify_battleback": False,
            "@battleback1_name": rstr(""),
            "@battleback2_name": rstr(""),
            "@autoplay_bgm": False,
            "@bgm": self.new_audio_file("bgm"),
            "@autoplay_bgs": False,
            "@bgs": self.new_audio_file("bgs", "", 80),
            "@disable_dashing": False,
            "@encounter_list": [],
            "@encounter_step": 30,
            "@parallax_name": rstr(""),
            "@parallax_loop_x": False,
            "@parallax_loop_y": False,
            "@parallax_sx": 0,
            "@parallax_sy": 0,
            "@parallax_show": False,
            "@note": rstr(""),
            "@data": Table(width, height, self.tile_layer_count),
            "@events": RubyHash(),
        }
        for field in _ALL_MAP_ONLY_IVARS:
            if field not in self.map_only_ivars:
                ivars.pop(field, None)
        return RubyObject("RPG::Map", ivars)


# Why an engine lacks a table, stated once and reused by the profile, the tool
# error messages and the reference documents.
_TABLE_ABSENCE_HINTS = {
    "tilesets": ("VX has a single global tileset in Graphics/System instead of a "
                 "tileset database, so maps have no tileset_id."),
    "areas": ("VX Ace replaced encounter areas with map regions: paint region ids "
              "on tile layer 3 with map_tiles, and reference them from a map's "
              "encounter_list."),
}

# Every RPG::Map field that only some engine in the family has. Each profile
# names the subset it actually carries.
_ALL_MAP_ONLY_IVARS = (
    "@tileset_id", "@display_name", "@specify_battleback",
    "@battleback1_name", "@battleback2_name", "@note",
)


def _layer_note(layers: dict[int, str]) -> str:
    """The tile-layer sentence, built from the layer table itself."""
    words = {3: "three", 4: "four"}.get(len(layers), str(len(layers)))
    parts = ", ".join("%d %s" % (z, name.split(" (")[0])
                      for z, name in sorted(layers.items()))
    return "Tile data has %s layers: %s." % (words, parts)


# Notes both engines carry. Anything engine-specific stays in the profile.
_SHARED_NOTES = [
    "An event runs the LAST page whose conditions are satisfied.",
]


def _editor_note(label: str) -> str:
    return ("Close the %s editor while editing: it holds the project in memory "
            "and rewrites every data file when it saves." % label)


ACE_PROFILE = EngineProfile(
    key="vx_ace",
    label="RPG Maker VX Ace",
    project_file="Game.rvproj2",
    extension=".rvdata2",
    data_files=ACE_DATA_FILES,
    class_fields={**SHARED_CLASS_FIELDS, **ACE_CLASS_FIELDS},
    map_layers=ACE_MAP_LAYERS,
    audio_classes={"bgm": "RPG::BGM", "bgs": "RPG::BGS",
                   "me": "RPG::ME", "se": "RPG::SE"},
    # RGSS3 runs Ruby 1.9, which tags every string with its encoding.
    string_ivars={"E": True},
    map_only_ivars=("@tileset_id", "@display_name", "@specify_battleback",
                    "@battleback1_name", "@battleback2_name", "@note"),
    notes=[
        _layer_note(ACE_MAP_LAYERS),
        "Each map picks a tileset with tileset_id; see the tilesets table.",
        *_SHARED_NOTES,
        # Field sets checked against a real VX Ace project: features is on every
        # kind of entry, but params is an array only on weapons, armors and
        # enemies, and a Table on classes.
        "Entries carry a features array; weapons, armors and enemies carry an "
        "8-slot params array, classes a params Table, and a usable item's "
        "damage is a Ruby formula string -- none of VX's flat stat fields.",
        _editor_note("RPG Maker VX Ace"),
    ],
)

VX_PROFILE = EngineProfile(
    key="vx",
    label="RPG Maker VX",
    project_file="Game.rvproj",
    extension=".rvdata",
    data_files=VX_DATA_FILES,
    class_fields={**SHARED_CLASS_FIELDS, **VX_CLASS_FIELDS},
    map_layers=VX_MAP_LAYERS,
    audio_classes={"bgm": "RPG::AudioFile", "bgs": "RPG::AudioFile",
                   "me": "RPG::AudioFile", "se": "RPG::AudioFile"},
    # RGSS2 runs Ruby 1.8: strings are bare bytes.
    string_ivars={},
    map_only_ivars=(),
    notes=[
        "VX has no Tilesets database: one global tileset lives in "
        "Graphics/System (TileA1..A5, TileB..E), so maps have no tileset_id.",
        _layer_note(VX_MAP_LAYERS),
        *_SHARED_NOTES,
        "Areas.rvdata holds encounter areas; VX Ace replaced them with regions.",
        "Entries carry flat stat fields and parameter curves as Tables, not VX "
        "Ace's features/params arrays or its damage formula strings.",
        _editor_note("RPG Maker VX"),
    ],
)

PROFILES = {profile.key: profile for profile in (ACE_PROFILE, VX_PROFILE)}


# ---------------------------------------------------------------------------
# Observed structure
# ---------------------------------------------------------------------------


# Real project data is about a dozen levels deep at its worst. The depth cap
# only guards pathological input; the node budget is what bounds total work,
# because Marshal object links make the loaded value a graph, not a tree.
_MAX_OBSERVE_DEPTH = 24
_MAX_OBSERVE_NODES = 2_000_000


class SchemaWalk:
    """Tallies which ivars each Ruby class carries, across a whole project.

    The tally maps class name -> field name -> occurrence count, which is what
    lets the tools describe a project honestly instead of asserting a schema the
    project may not share (a script-heavy project can add ivars of its own).

    One instance walks every file, so ``budget`` bounds the work for the project
    rather than per file, and :attr:`truncated` records whether the report that
    comes out is complete. Nothing here guesses: a partial answer is reported as
    partial.
    """

    __slots__ = ("fields", "budget", "_remaining", "truncated")

    def __init__(self, budget: int = _MAX_OBSERVE_NODES):
        self.fields: dict[str, dict[str, int]] = {}
        self.budget = budget
        self._remaining = [budget]
        self.truncated = False

    @property
    def nodes_visited(self) -> int:
        return self.budget - self._remaining[0]

    def visit(self, value: Any) -> "SchemaWalk":
        """Walk one file's loaded value.

        The identity memo is per file: Marshal links never cross files, and a
        memo that outlived the walk could mistake a recycled id for a value it
        had already seen.
        """
        _observe(value, self.fields, 0, set(), self._remaining)
        if self._remaining[0] <= 0:
            self.truncated = True
        return self


def observe_class_fields(value: Any, into: dict[str, dict[str, int]] | None = None
                         ) -> dict[str, dict[str, int]]:
    """Tally the ivars of one loaded value. See :class:`SchemaWalk`."""
    fields = into if into is not None else {}
    _observe(value, fields, 0, set(), [_MAX_OBSERVE_NODES])
    return fields


def _observe(value: Any, fields: dict[str, dict[str, int]], depth: int,
             seen: set[int], budget: list[int]) -> None:
    """One step of the walk.

    ``seen`` holds the id of every container already visited. Marshal resolves
    an object link to the *same* Python object, so a value reachable by several
    paths would otherwise be walked once per path -- and a file whose containers
    link to a shared child at every level costs exponential work for a few
    hundred bytes of input.
    """
    if depth > _MAX_OBSERVE_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1

    if isinstance(value, RubyObject):
        if id(value) in seen:
            return
        seen.add(id(value))
        _tally(fields, value.cls, value.ivars)
        for item in value.ivars.values():
            _observe(item, fields, depth + 1, seen, budget)
    elif isinstance(value, list):
        if id(value) in seen:
            return
        seen.add(id(value))
        for item in value:
            _observe(item, fields, depth + 1, seen, budget)
    elif isinstance(value, RubyHash):
        if id(value) in seen:
            return
        seen.add(id(value))
        for key, item in value.pairs:
            _observe(key, fields, depth + 1, seen, budget)
            _observe(item, fields, depth + 1, seen, budget)
    elif isinstance(value, RubyStruct):
        _tally(fields, value.cls, value.members)
        for item in value.members.values():
            _observe(item, fields, depth + 1, seen, budget)
    elif isinstance(value, (RubyUserMarshal, RubyData)):
        # These name a class but keep their state in one nested value.
        fields.setdefault(value.cls, {})
        _observe(value.value, fields, depth + 1, seen, budget)
    elif isinstance(value, (RubyUClass, RubyExtended)):
        _observe(value.inner, fields, depth + 1, seen, budget)
    elif isinstance(value, RubyIvars):
        _observe(value.inner, fields, depth + 1, seen, budget)
        for item in value.ivars.values():
            _observe(item, fields, depth + 1, seen, budget)
    elif isinstance(value, (RubyUserDef, RubySymbol)) and value.ivars:
        for item in value.ivars.values():
            _observe(item, fields, depth + 1, seen, budget)


def _tally(fields: dict[str, dict[str, int]], cls: str,
           names: dict[str, Any]) -> None:
    bucket = fields.setdefault(cls, {})
    for name in names:
        bare = _bare_name(name)
        bucket[bare] = bucket.get(bare, 0) + 1
