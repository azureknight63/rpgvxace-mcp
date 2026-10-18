"""What lives in an RPG Maker VX Ace (or VX, or XP) project, and how to build new pieces.

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
Map tile layers      4 (3 tiles, shadow+region)     3 (3 tiles)
Audio references     ``RPG::BGM``/``BGS``/``ME``/   the same: RGSS2 calls
                     ``SE``                         ``play`` on every one
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

XP (RGSS1, ``.rxdata``) differs from both more than they differ from each
other: its own page, condition, graphic and move route, flat RGSS1 stats,
``RPG::AudioFile`` everywhere, a tileset of one graphic and seven autotiles,
and a command table numbered its own way. `XP_CLASS_FIELDS` and
`XP_PROFILE` hold it, and say where each fact came from.
"""

from __future__ import annotations

from typing import Any, Literal

from .rubytypes import (
    RubyData,
    RubyHash,
    RubyObject,
    RubyString,
    RubyStruct,
    RubyUserMarshal,
    Table,
    _bare_name,
    ruby_children,
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
             "The script editor contents: an array of [id, name, zlib code] "
             "rows, one per slot"),
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

# Map fields a patch must not touch, because something else owns the
# invariants they are part of: setting @width alone leaves a map claiming a
# size its @data does not have, which is a file the editor refuses to open.
#
# "events" is here because a patch merges field by field, so
# `patch={"events": {"1": {"x": 99999}}}` reached straight into the event
# object and moved it off the map, past event_edit's bounds check.
#
# Here rather than in `map_tools`, which is a leaf that registers tools:
# `raw_tools` needs this list too and was importing it from that leaf, which
# is what made the import graph among the tools unreadable and `map_tools`
# un-splittable. Same argument as TRIGGER_NAMES below.
STRUCTURAL_MAP_FIELDS = ("width", "height", "data", "events")


def unreadable_events_note(events: "Any") -> "str | None":
    """What to say about an @events that is not a Hash, or None if it is fine.

    `event_edit` refuses such a map by name; the three read tools returned an
    empty list, so "we could not read this" and "there are none" looked
    identical and a model concluded the map has no events. One sentence, in
    one place, because three tools were reporting the same fact three ways --
    which is to say, not reporting it at all.

    Here rather than in `map_tools` for the same reason as the list above:
    `event_tools` needs it and was reaching into a sibling leaf for it.
    """
    if events is None or isinstance(events, RubyHash):
        return None
    return ("this map holds its events as %s rather than a Hash, so none "
            "could be read. Run validate to see what is wrong with the file."
            % type(events).__name__)


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

# A common event's @trigger is a different, shorter set: it has no position on
# a map, so there is nothing to touch. This was inlined in `event_get` as a
# literal dict while the same three names sat in this file as prose -- the one
# trigger table that did not get the treatment the page triggers got.
COMMON_EVENT_TRIGGER_NAMES = {
    0: "none",
    1: "autorun",
    2: "parallel",
}


def numbered(mapping: "dict[int, str]", separator: str = ": ") -> str:
    """``0: none, 1: autorun, 2: parallel`` from a table like the ones above.

    Here rather than only in `docs`, because the field descriptions in this
    file were the *other* copy: the page-trigger names, the common-event
    trigger names and the nine tileset sheet names each appeared once as a
    table this module exports and once as a sentence a served document
    renders, so a reword in one made two documents disagree.

    The separator is a parameter because `docs` had its own implementation
    of this and the two disagreed on it -- so one of them had to keep its
    spelling, and a parameter is cheaper than a second function.
    """
    return ", ".join("%d%s%s" % (code, separator, name)
                     for code, name in sorted(mapping.items()))

# The nine tileset sheets an RPG::Tileset names, in @tileset_names order.
TILESET_SHEET_NAMES = ("A1", "A2", "A3", "A4", "A5", "B", "C", "D", "E")

# Classes whose structure is identical in both engines. This sentence spent
# several rounds sixty lines further up, above PAGE_CONDITION_FLAGS, where it
# read as a description of that -- which is how a comment goes wrong with
# nothing wrong in the code.
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
        "trigger": numbered(TRIGGER_NAMES),
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
        "trigger": numbered(COMMON_EVENT_TRIGGER_NAMES),
        "switch_id": "Switch that enables an autorun or parallel common event",
        "list": "Array of RPG::EventCommand",
    },
}


# Of those, the five XP lays out the same way. The page and its condition,
# graphic and move route took this shape in VX; XP's are in
# `XP_CLASS_FIELDS`.
FAMILY_CLASS_FIELDS = {
    cls: SHARED_CLASS_FIELDS[cls] for cls in (
        "RPG::MapInfo", "RPG::Event", "RPG::EventCommand", "RPG::MoveCommand",
        "RPG::CommonEvent")
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


# The audio classes VX and VX Ace store. Both define them as subclasses of
# RPG::AudioFile that can play themselves, and both runtimes call `play` on
# every stored one: RGSS2's `Game_Map#autoplay` is `@map.bgm.play`, `Sound`
# is `$data_system.sounds[n].play`, `Sprite_Base` plays `timing.se`, and
# command_241/245/249/250 are each `@params[0].play` -- an RPG::AudioFile in
# any of those slots is a NoMethodError the first time the cursor moves. The
# RGSS2 reference's per-attribute "(RPG::AudioFile)" names the superclass;
# its BGM page lists Map, System, Vehicle and EventCommand as referrers, and
# its SE page Animation::Timing, System, EventCommand and MoveCommand.
BGM_FAMILY_FIELDS: dict[str, dict[str, str]] = {
    "RPG::BGM": {"name": "File name in Audio/BGM", "volume": "0..100",
                 "pitch": "50..150"},
    "RPG::BGS": {"name": "File name in Audio/BGS", "volume": "0..100",
                 "pitch": "50..150"},
    "RPG::ME": {"name": "File name in Audio/ME", "volume": "0..100",
                "pitch": "50..150"},
    "RPG::SE": {"name": "File name in Audio/SE", "volume": "0..100",
                "pitch": "50..150"},
}

# The class each audio kind is stored as, on both engines of the VX family.
BGM_FAMILY_AUDIO = {"bgm": "RPG::BGM", "bgs": "RPG::BGS",
                    "me": "RPG::ME", "se": "RPG::SE"}


ACE_CLASS_FIELDS: dict[str, dict[str, str]] = {
    "RPG::Map": {**_MAP_FIELDS_SHARED, **{
        "tileset_id": "Which RPG::Tileset this map draws with",
        "display_name": "Name shown to the player on entry (blank for none)",
        "note": "The map's Note field, free text for the developer or scripts",
        "specify_battleback": "Whether this map overrides the battle background",
        "battleback1_name": "Battle background floor graphic",
        "battleback2_name": "Battle background wall graphic",
        "data": "Table, width x height x 4: three layers of tile ids and a "
                "fourth of region * 256 + shadow bits",
        "encounter_list": "Array of RPG::Map::Encounter (troop, weight, region set)",
    }},
    "RPG::Tileset": {
        "id": "Tileset id",
        "name": "Tileset name in the database",
        "mode": "Observed values are 0 and 1, understood as field type and area "
                "type; not confirmed against the reference",
        "note": "Free text Note field",
        "tileset_names": ("%d graphic file names, in order: %s"
                          % (len(TILESET_SHEET_NAMES),
                             ", ".join(TILESET_SHEET_NAMES))),
        "flags": "Table of 8192 per-tile flag words, one per tile id; see the "
                 "advisory flag-bit table for what the bits mean",
    },
    **BGM_FAMILY_FIELDS,
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
    **BGM_FAMILY_FIELDS,
    "RPG::Area": {
        "id": "Area id",
        "name": "Area name",
        "map_id": "The map the area belongs to",
        "rect": "Rect covering the area",
        "encounter_list": "Array of troop ids encountered inside the area",
        "order": "Position in the map tree (editor bookkeeping)",
    },
}


# RPG Maker XP (RGSS1). Shares the five classes above with VX and VX Ace
# field for field; everything else is its own, from the RGSS1 reference
# that ships with XP. Every field set here matches the instances in copies
# of XP editor data (a new project and a small game, both hosted in the
# borisblizzard/arcreator repository) -- not, though, an XP install or
# project on this machine, of which there is none.
_XP_STATS = ("str", "dex", "agi", "int")


def _xp_flags(*names: str) -> dict[str, str]:
    """The ``*_valid`` style truth fields, described alike."""
    return {name: "Whether the [%s] option is set" % name.replace("_", " ")
            for name in names}


XP_CLASS_FIELDS: dict[str, dict[str, str]] = {
    **FAMILY_CLASS_FIELDS,
    "RPG::Event::Page": {
        **{field: text for field, text in
           SHARED_CLASS_FIELDS["RPG::Event::Page"].items()
           if field != "priority_type"},
        "move_frequency": "1: lowest ... 6: highest",
        "always_on_top": "[Always on Top] option (XP has no priority_type)",
    },
    "RPG::Event::Page::Condition": {
        field: text for field, text in
        SHARED_CLASS_FIELDS["RPG::Event::Page::Condition"].items()
        if not field.startswith(("item_", "actor_"))
    },
    "RPG::Event::Page::Graphic": {
        "tile_id": "Tile id when the event is drawn as a tile, else 0",
        "character_name": "Character sprite file in Graphics/Characters (one "
                          "character per file)",
        "character_hue": "Hue shift, 0..360",
        "direction": "2: down, 4: left, 6: right, 8: up",
        "pattern": "Animation frame (0..3)",
        "opacity": "0..255",
        "blend_type": "0: normal, 1: add, 2: subtract",
    },
    "RPG::MoveRoute": {
        "repeat": "[Repeat Action] option",
        "skippable": "[Ignore If Can't Move] option",
        "list": "Array of RPG::MoveCommand (XP has no wait flag)",
    },
    "RPG::Map": {
        "tileset_id": "Which RPG::Tileset this map draws with",
        "width": "Map width in tiles",
        "height": "Map height in tiles",
        "autoplay_bgm": "Whether BGM switches automatically on entry",
        "bgm": "RPG::AudioFile played when autoplay_bgm is true",
        "autoplay_bgs": "Whether BGS switches automatically on entry",
        "bgs": "RPG::AudioFile played when autoplay_bgs is true",
        "encounter_list": "Array of troop ids that can be encountered",
        "encounter_step": "Average number of steps between encounters",
        "data": "Table of tile ids, width x height x 3 layers",
        "events": "Hash of event id => RPG::Event",
    },
    "RPG::Tileset": {
        "id": "Tileset id",
        "name": "Tileset name in the database",
        "tileset_name": "Tileset graphic in Graphics/Tilesets, 8 tiles wide",
        "autotile_names": "7 autotile graphics in Graphics/Autotiles",
        "panorama_name": "Panorama (parallax) graphic",
        "panorama_hue": "Panorama hue shift, 0..360",
        "fog_name": "Fog graphic",
        "fog_hue": "Fog hue shift, 0..360",
        "fog_opacity": "Fog opacity",
        "fog_blend_type": "Fog blending: 0 normal, 1 add, 2 subtract",
        "fog_zoom": "Fog zoom, percent",
        "fog_sx": "Fog automatic X scroll",
        "fog_sy": "Fog automatic Y scroll",
        "battleback_name": "Battle background graphic",
        "passages": "Table indexed by tile id: 1 blocks down, 2 left, 4 right, "
                    "8 up, 0x40 bush, 0x80 counter",
        "priorities": "Table indexed by tile id: 0 drawn under characters, "
                      "1-5 above them, higher drawn later",
        "terrain_tags": "Table indexed by tile id: terrain tag, 0-7",
    },
    "RPG::System": {
        "magic_number": "Changed by the editor on every save (update checks)",
        "party_members": "Starting party, an array of actor ids",
        "elements": "Element names, indexed by id; index 0 is nil",
        "switches": "Switch names, indexed by id; index 0 is nil",
        "variables": "Variable names, indexed by id; index 0 is nil",
        "windowskin_name": "Window skin graphic",
        "title_name": "Title screen graphic",
        "gameover_name": "Game over graphic",
        "battle_transition": "Transition graphic shown entering battle",
        **{name: "RPG::AudioFile" for name in (
            "title_bgm", "battle_bgm", "battle_end_me", "gameover_me",
            "cursor_se", "decision_se", "cancel_se", "buzzer_se", "equip_se",
            "shop_se", "save_se", "load_se", "battle_start_se", "escape_se",
            "actor_collapse_se", "enemy_collapse_se")},
        "words": "RPG::System::Words: the game's terms",
        "start_map_id": "Map the player starts on",
        "start_x": "Starting X",
        "start_y": "Starting Y",
        "test_battlers": "Array of RPG::System::TestBattler",
        "test_troop_id": "Troop for battle tests",
        "battleback_name": "Battle background for battle tests",
        "battler_name": "Editor bookkeeping",
        "battler_hue": "Editor bookkeeping",
        "edit_map_id": "The map open in the editor",
        # Not in the reference, but in every copy of XP editor data read
        # (and in VX Ace's System too): an integer the editor keeps.
        "_": "Editor bookkeeping, not in the reference",
    },
    "RPG::System::Words": {
        name: "The term for %s" % name for name in (
            "gold", "hp", "sp", *_XP_STATS, "atk", "pdef", "mdef", "weapon",
            "armor1", "armor2", "armor3", "armor4", "attack", "skill", "guard",
            "item", "equip")
    },
    "RPG::System::TestBattler": {
        "actor_id": "Actor id", "level": "Level", "weapon_id": "Weapon id",
        **{"armor%d_id" % n: "Armor id in slot %d" % n for n in range(1, 5)},
    },
    "RPG::AudioFile": {
        "name": "File name, in the Audio folder the slot plays from",
        "volume": "0..100",
        "pitch": "50..150",
    },
    "RPG::Actor": {
        "id": "Actor id",
        "name": "Actor name",
        "class_id": "Class id",
        "initial_level": "Starting level",
        "final_level": "Level cap",
        "exp_basis": "Experience curve basis, 10..50",
        "exp_inflation": "Experience curve inflation, 10..50",
        "character_name": "Character graphic",
        "character_hue": "Character hue shift",
        "battler_name": "Battler graphic",
        "battler_hue": "Battler hue shift",
        "parameters": "Table [kind, level]: 0 max HP, 1 max SP, 2 str, 3 dex, "
                      "4 agi, 5 int",
        "weapon_id": "Starting weapon",
        **{"armor%d_id" % n: "Starting armor in slot %d" % n
           for n in range(1, 5)},
        "weapon_fix": "Whether the weapon cannot be removed",
        **{"armor%d_fix" % n: "Whether slot %d cannot be removed" % n
           for n in range(1, 5)},
    },
    "RPG::Class": {
        "id": "Class id",
        "name": "Class name",
        "position": "0: front, 1: middle, 2: rear",
        "weapon_set": "Array of equippable weapon ids",
        "armor_set": "Array of equippable armor ids",
        "element_ranks": "Table indexed by element id: 0 A ... 5 F",
        "state_ranks": "Table indexed by state id: 0 A ... 5 F",
        "learnings": "Array of RPG::Class::Learning",
    },
    "RPG::Class::Learning": {
        "level": "Level the skill is learned at",
        "skill_id": "Skill learned",
    },
    "RPG::Skill": {
        "id": "Skill id", "name": "Skill name", "icon_name": "Icon graphic",
        "description": "Help text",
        "scope": "0 none, 1 one enemy, 2 all enemies, 3 one ally, 4 all "
                 "allies, 5 one ally at 0 HP, 6 all allies at 0 HP, 7 user",
        "occasion": "0 always, 1 battle only, 2 menu only, 3 never",
        "animation1_id": "User animation", "animation2_id": "Target animation",
        "menu_se": "RPG::AudioFile played when used from the menu",
        "common_event_id": "Common event run on use",
        "sp_cost": "SP consumed", "power": "Power",
        **{"%s_f" % stat: "%s F rating" % stat for stat in (
            "atk", "eva", *_XP_STATS, "pdef", "mdef")},
        "hit": "Hit chance", "variance": "Variance",
        "element_set": "Element ids", "plus_state_set": "States added",
        "minus_state_set": "States removed",
    },
    "RPG::Item": {
        "id": "Item id", "name": "Item name", "icon_name": "Icon graphic",
        "description": "Help text", "scope": "As a skill's scope",
        "occasion": "As a skill's occasion",
        "animation1_id": "User animation", "animation2_id": "Target animation",
        "menu_se": "RPG::AudioFile played when used from the menu",
        "common_event_id": "Common event run on use", "price": "Price",
        "consumable": "Whether the item is used up",
        "parameter_type": "0 none, 1 max HP, 2 max SP, 3 str, 4 dex, 5 agi, "
                          "6 int",
        "parameter_points": "Amount the parameter grows",
        "recover_hp_rate": "HP recovered, percent",
        "recover_hp": "HP recovered",
        "recover_sp_rate": "SP recovered, percent",
        "recover_sp": "SP recovered", "hit": "Hit chance",
        "pdef_f": "pdef F rating", "mdef_f": "mdef F rating",
        "variance": "Variance", "element_set": "Element ids",
        "plus_state_set": "States added", "minus_state_set": "States removed",
    },
    "RPG::Weapon": {
        "id": "Weapon id", "name": "Weapon name", "icon_name": "Icon graphic",
        "description": "Help text", "animation1_id": "User animation",
        "animation2_id": "Target animation", "price": "Price",
        "atk": "Attack", "pdef": "Physical defense", "mdef": "Magic defense",
        **{"%s_plus" % stat: "%s bonus" % stat for stat in _XP_STATS},
        "element_set": "Element ids", "plus_state_set": "States added",
        "minus_state_set": "States removed",
    },
    "RPG::Armor": {
        "id": "Armor id", "name": "Armor name", "icon_name": "Icon graphic",
        "description": "Help text",
        "kind": "0 shield, 1 helmet, 2 body armor, 3 accessory",
        "auto_state_id": "State applied while worn", "price": "Price",
        "pdef": "Physical defense", "mdef": "Magic defense",
        "eva": "Evasion bonus",
        **{"%s_plus" % stat: "%s bonus" % stat for stat in _XP_STATS},
        "guard_element_set": "Elements guarded against",
        "guard_state_set": "States guarded against",
    },
    "RPG::Enemy": {
        "id": "Enemy id", "name": "Enemy name",
        "battler_name": "Battler graphic", "battler_hue": "Battler hue shift",
        "maxhp": "Max HP", "maxsp": "Max SP",
        **{stat: stat for stat in _XP_STATS},
        "atk": "Attack", "pdef": "Physical defense", "mdef": "Magic defense",
        "eva": "Evasion", "animation1_id": "Attack animation",
        "animation2_id": "Target animation",
        "element_ranks": "Table indexed by element id: 0 A ... 5 F",
        "state_ranks": "Table indexed by state id: 0 A ... 5 F",
        "actions": "Array of RPG::Enemy::Action", "exp": "Experience",
        "gold": "Gold", "item_id": "Treasure item",
        "weapon_id": "Treasure weapon", "armor_id": "Treasure armor",
        "treasure_prob": "Treasure chance, percent",
    },
    "RPG::Enemy::Action": {
        "kind": "0 basic, 1 skill",
        "basic": "0 attack, 1 defend, 2 escape, 3 do nothing",
        "skill_id": "Skill used when kind is 1",
        "condition_turn_a": "Turn condition a in a + bx",
        "condition_turn_b": "Turn condition b in a + bx",
        "condition_hp": "HP percent condition (100 when unused)",
        "condition_level": "Level condition (1 when unused)",
        "condition_switch_id": "Switch condition (0 when unused)",
        "rating": "1..10",
    },
    "RPG::Troop": {
        "id": "Troop id", "name": "Troop name",
        "members": "Array of RPG::Troop::Member",
        "pages": "Array of RPG::Troop::Page",
    },
    "RPG::Troop::Member": {
        "enemy_id": "Enemy id", "x": "Screen X", "y": "Screen Y",
        "hidden": "[Appear Midway] option", "immortal": "[Immortal] option",
    },
    "RPG::Troop::Page": {
        "condition": "RPG::Troop::Page::Condition",
        "span": "0: battle, 1: turn, 2: moment",
        "list": "Array of RPG::EventCommand",
    },
    "RPG::Troop::Page::Condition": {
        **_xp_flags("turn_valid", "enemy_valid", "actor_valid",
                    "switch_valid"),
        "turn_a": "Turn a in a + bx", "turn_b": "Turn b in a + bx",
        "enemy_index": "Troop member, 0..7", "enemy_hp": "Enemy HP percent",
        "actor_id": "Actor id", "actor_hp": "Actor HP percent",
        "switch_id": "Switch id",
    },
    "RPG::State": {
        "id": "State id", "name": "State name",
        "animation_id": "Animation while afflicted",
        "restriction": "0 none, 1 no magic, 2 attack enemies, 3 attack "
                       "allies, 4 cannot move",
        "nonresistance": "[Nonresistance] option",
        "zero_hp": "[Regard as HP 0] option",
        "cant_get_exp": "[Can't Get EXP] option",
        "cant_evade": "[Can't Evade] option",
        "slip_damage": "[Slip Damage] option", "rating": "0..10",
        **{"%s_rate" % stat: "%s, percent" % stat for stat in (
            "hit", "maxhp", "maxsp", *_XP_STATS, "atk", "pdef", "mdef")},
        "eva": "Evasion bonus",
        "battle_only": "Whether it wears off when battle ends",
        "hold_turn": "Turns before it may wear off",
        "auto_release_prob": "Chance to wear off after hold_turn",
        "shock_release_prob": "Chance to wear off on physical damage",
        "guard_element_set": "Elements guarded against",
        "plus_state_set": "States added", "minus_state_set": "States removed",
    },
    "RPG::Animation": {
        "id": "Animation id", "name": "Animation name",
        "animation_name": "Animation graphic", "animation_hue": "Hue shift",
        "position": "0 top, 1 middle, 2 bottom, 3 screen",
        "frame_max": "Frame count", "frames": "Array of RPG::Animation::Frame",
        "timings": "Array of RPG::Animation::Timing",
    },
    "RPG::Animation::Frame": {
        "cell_max": "Number of cells",
        "cell_data": "Table [cell, 0..7]: pattern, x, y, zoom, angle, mirror, "
                     "opacity, blend",
    },
    "RPG::Animation::Timing": {
        "frame": "Frame, one less than the editor shows",
        "se": "RPG::AudioFile",
        "flash_scope": "0 none, 1 target, 2 screen, 3 hide target",
        "flash_color": "Color", "flash_duration": "Frames",
        "condition": "0 none, 1 hit, 2 miss",
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


# What a map's tile table holds, layer by layer. This used to say Ace's layer
# 2 was a four-bit shadow mask and layer 3 a bare region id, and VX's layer 2
# a shadow mask too. Neither engine reads them that way:
#
# * RGSS3 `Game_Map#layered_tiles` is `[2, 1, 0].collect {|z| tile_id(x, y,
#   z) }` and `region_id` is `@map.data[x, y, 3] >> 8`; the tilemap draws
#   layers 0-2 as tiles and the shadow from layer 3's low four bits.
# * RGSS2 `Game_Map#passable?` looks `@map.data[x, y, i]` for i in
#   `[2, 1, 0]` up in the passage table, so all three of VX's layers are
#   tile ids.
# * A real VX Ace project (the author's game, 39 maps) agrees: layer 0 holds A
#   tiles, layer 1 only A1-A4 autotiles, layer 2 only B-E ids (4,932 cells),
#   and layer 3 only region * 256 + shadow words (337 cells of region 1, 556
#   shadow-only, no other bit set).
#
# The layers that hold tile ids, on both engines.
TILE_LAYER_COUNT = 3

# VX Ace's fourth layer: one word per cell, the region id in the byte above
# four shadow bits (one per quarter of the cell). The editor numbers regions
# 0 to 63; bits 4-7 and 14-15 are read by nothing.
META_LAYER = 3
MAX_SHADOW = 15
MAX_REGION_ID = 63
REGION_SHIFT = 8
META_LAYER_BITS = (MAX_REGION_ID << REGION_SHIFT) | MAX_SHADOW


def region_of(word: int) -> int:
    """The region id in a layer-3 word, as RGSS3's ``Game_Map#region_id``."""
    return word >> REGION_SHIFT


def shadow_of(word: int) -> int:
    """The four shadow-quarter bits of a layer-3 word."""
    return word & MAX_SHADOW


def with_region(word: int, region: int) -> int:
    """``word`` with its region replaced and its low byte (the shadow) kept."""
    return (word & 0xFF) | (region << REGION_SHIFT)


def with_shadow(word: int, shadow: int) -> int:
    """``word`` with its shadow bits replaced and everything above them kept."""
    return (word & ~MAX_SHADOW) | shadow


ACE_MAP_LAYERS = {
    0: "lower tile layer (A tiles: the ground)",
    1: "middle tile layer (A tiles drawn over layer 0)",
    2: "upper tile layer (B-E tiles placed over the ground)",
    3: "shadow and region layer (one word per cell: region * 256 + shadow, "
       "a region id 0-63 in the byte above four shadow-quarter bits 0-15; "
       "map_tiles view=region or view=shadow reads and writes one part)",
}


# Advisory beyond "all three are tile ids": see VX_LAYER_NOTE.
VX_MAP_LAYERS = {
    0: "lower tile layer (the ground)",
    1: "middle tile layer",
    2: "upper tile layer",
}


VX_LAYER_NOTE = (
    "Advisory: RGSS2's Game_Map#passable? reads all three VX layers as tile "
    "ids, so layer 2 is a tile layer and not the shadow mask earlier versions "
    "of this server described. Which sheets the editor puts on which layer, "
    "and where VX keeps the editor's automatic wall shadows, have not been "
    "checked against a real VX project. VX has no region layer.")


# XP's three layers all hold tile ids, read top first: `Game_Map#passable?`,
# `bush?`, `counter?` and `terrain_tag` each look `data[x, y, i]` up for i
# in `[2, 1, 0]`. What draws over a character is not the layer but the
# tileset's priorities table, which the RGSS1 Tilemap reads per tile id.
XP_MAP_LAYERS = {
    0: "lower tile layer (the ground)",
    1: "middle tile layer",
    2: "upper tile layer",
}


XP_LAYER_NOTE = (
    "XP reads all three layers as tile ids, top layer first, and draws a "
    "tile over characters by its tileset's priorities entry (0 under them, "
    "1-5 above), not by the layer it is on. A cell holds 0 (nothing), an "
    "autotile id from 48 to 383 or a tileset tile id from 384 up: the only "
    "values in copies of XP editor data, where Sprite_Character draws tile "
    "id - 384 from the tileset graphic, eight tiles to a row. XP has no "
    "region layer and no shadow layer.")

# Seven autotiles of 48 ids each, then the tileset graphic from 384. The
# 48 patterns within an autotile are chosen by the built-in Tilemap, which
# the reference does not document -- hence advisory.
XP_TILE_ID_RANGES_NOTE = (
    "Advisory: 0 and 48-383 and 384 up are the only values in copies of XP "
    "editor data, and 384 is where XP's Sprite_Character starts the tileset "
    "graphic; which of an autotile's 48 patterns an id selects is the "
    "built-in Tilemap's and is not documented. The last tileset id is one "
    "less than the length of the tileset's passages table.")
XP_AUTOTILES = 7
XP_AUTOTILE_PATTERNS = 48
XP_FIRST_TILESET_ID = XP_AUTOTILE_PATTERNS * (XP_AUTOTILES + 1)
XP_TILE_ID_RANGES = {
    **{"autotile %d" % n: (XP_AUTOTILE_PATTERNS * n,
                           XP_AUTOTILE_PATTERNS * (n + 1) - 1)
       for n in range(1, XP_AUTOTILES + 1)},
    # A Table cell is 16 bits, so that is the ceiling on any tile id.
    "tileset": (XP_FIRST_TILESET_ID, 0xFFFF),
}

# The bits of an XP tileset's passages entry. Not advisory: the RGSS1
# reference's RPG::Tileset page lists them, and XP's Game_Map reads exactly
# these -- `passable?` the low four by direction, `bush?` 0x40, `counter?`
# 0x80. An XP tile's draw height is the priorities table and its terrain
# tag the terrain_tags table, not bits here.
XP_PASSAGE_BITS = {
    "0x01": "blocks moving down",
    "0x02": "blocks moving left",
    "0x04": "blocks moving right",
    "0x08": "blocks moving up",
    "0x40": "bush",
    "0x80": "counter",
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


# The order the editor writes each class's instance variables in, for every
# class this server builds from scratch. Marshal keeps the order it is given
# and reads ivars back by name, so any order loads -- but only this one is
# the file the editor itself writes. `new_event_command` wrote @code before
# @indent, while all 13,900 commands in the author's game and the RuralFarm
# sample store @indent first.
#
# Read off every instance of each class in those two VX Ace projects, which
# agree with each other and hold exactly one order per class.
#
# VX and XP have no such order to match. Their editors write through Ruby
# 1.8, whose instance-variable table is a hash: a copy of XP's new-project
# data holds its eight actors in three different orders and its 46 event
# commands as @parameters, @indent, @code. VX runs the same Ruby but no
# editor-written VX file has been read, so this is unconfirmed for VX. On
# both, this table's order is used: it loads, as any order does.
EDITOR_IVAR_ORDER: dict[str, tuple[str, ...]] = {
    "RPG::EventCommand": ("indent", "code", "parameters"),
    "RPG::MoveCommand": ("code", "parameters"),
    "RPG::MoveRoute": ("repeat", "skippable", "wait", "list"),
    "RPG::Event": ("id", "name", "x", "y", "pages"),
    "RPG::Event::Page": (
        "condition", "graphic", "move_type", "move_speed", "move_frequency",
        "move_route", "walk_anime", "step_anime", "direction_fix", "through",
        "priority_type", "trigger", "list"),
    "RPG::Event::Page::Condition": (
        *PAGE_CONDITION_FLAGS, "switch1_id", "switch2_id", "variable_id",
        "variable_value", "self_switch_ch", "item_id", "actor_id"),
    "RPG::Event::Page::Graphic": (
        "tile_id", "character_name", "character_index", "direction",
        "pattern"),
    "RPG::MapInfo": ("scroll_x", "name", "expanded", "order", "scroll_y",
                     "parent_id"),
    "RPG::Map": (
        "parallax_name", "height", "events", "parallax_sx", "bgm",
        "tileset_id", "encounter_step", "width", "data", "bgs",
        "parallax_loop_y", "autoplay_bgm", "encounter_list", "autoplay_bgs",
        "parallax_show", "scroll_type", "parallax_loop_x", "disable_dashing",
        "parallax_sy", "display_name", "specify_battleback", "note",
        "battleback1_name", "battleback2_name"),
    # ME and SE put @pitch before @volume -- the opposite of BGM and BGS.
    "RPG::BGM": ("name", "volume", "pitch"),
    "RPG::BGS": ("name", "volume", "pitch"),
    "RPG::ME": ("name", "pitch", "volume"),
    "RPG::SE": ("name", "pitch", "volume"),
}


def ordered_like(ivars: dict[str, Any], order: "list[str] | tuple[str, ...]"
                 ) -> dict[str, Any]:
    """``ivars`` with the fields ``order`` names first, in that order.

    ``order`` holds bare names. Fields it does not name keep their own
    relative order after it, so nothing is dropped and a field a script
    added stays where it was relative to its peers.
    """
    wanted = ["@" + name for name in order]
    out = {key: ivars[key] for key in wanted if key in ivars}
    out.update((key, value) for key, value in ivars.items() if key not in out)
    return out


def in_editor_order(obj: RubyObject) -> RubyObject:
    """``obj`` with its ivars in the editor's order, if this class has one."""
    order = EDITOR_IVAR_ORDER.get(obj.cls)
    if order is not None:
        obj.ivars = ordered_like(obj.ivars, order)
    return obj


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
        "@indent": indent, "@code": code, "@parameters": list(parameters or []),
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
        "@scroll_x": 0,
        "@name": rstr(name),
        "@expanded": False,
        "@order": order,
        "@scroll_y": 0,
        "@parent_id": parent_id,
    })


# XP's page and its parts, with the RGSS1 reference's defaults and in its
# `initialize` order: XP's editor writes through Ruby 1.8, so it has no
# order of its own to match (see `EDITOR_IVAR_ORDER`).
def new_xp_condition() -> RubyObject:
    return RubyObject("RPG::Event::Page::Condition", {
        "@switch1_valid": False,
        "@switch2_valid": False,
        "@variable_valid": False,
        "@self_switch_valid": False,
        "@switch1_id": 1,
        "@switch2_id": 1,
        "@variable_id": 1,
        "@variable_value": 0,
        "@self_switch_ch": rstr("A"),
    })


def new_xp_graphic() -> RubyObject:
    return RubyObject("RPG::Event::Page::Graphic", {
        "@tile_id": 0,
        "@character_name": rstr(""),
        "@character_hue": 0,
        "@direction": 2,
        "@pattern": 0,
        "@opacity": 255,
        "@blend_type": 0,
    })


def new_xp_move_route() -> RubyObject:
    return RubyObject("RPG::MoveRoute", {
        "@repeat": True,
        "@skippable": False,
        "@list": [new_move_command(0)],
    })


def new_xp_page() -> RubyObject:
    return RubyObject("RPG::Event::Page", {
        "@condition": new_xp_condition(),
        "@graphic": new_xp_graphic(),
        "@move_type": 0,
        "@move_speed": 3,
        "@move_frequency": 3,
        "@move_route": new_xp_move_route(),
        "@walk_anime": True,
        "@step_anime": False,
        "@direction_fix": False,
        "@through": False,
        "@always_on_top": False,
        "@trigger": 0,
        "@list": [new_event_command(0, 0, [])],
    })


# ---------------------------------------------------------------------------
# Engine profiles
# ---------------------------------------------------------------------------


class EngineProfile:
    """Everything about a project that depends on which engine made it."""

    __slots__ = ("key", "label", "project_file", "extension", "data_files",
                 "class_fields", "map_layers", "map_table_depth", "audio_classes",
                 "string_ivars", "notes", "tileset_layout", "tile_id_ranges",
                 "tile_id_ranges_note", "layer_note", "vx_command_layouts",
                 "absence_hints", "_page_template")

    def __init__(self, key: str, label: str, project_file: str, extension: str,
                 data_files: dict[str, DataFile], class_fields: dict[str, dict[str, str]],
                 map_layers: dict[int, str], audio_classes: dict[str, str],
                 string_ivars: dict[str, Any], notes: list[str], *,
                 tileset_layout: "str | None",
                 tile_id_ranges: dict[str, tuple[int, int]],
                 tile_id_ranges_note: str, layer_note: "str | None",
                 vx_command_layouts: bool,
                 page_template: Any = None,
                 absence_hints: "dict[str, str] | None" = None):
        self.key = key
        self.label = label
        self.project_file = project_file
        self.extension = extension
        self.data_files = data_files
        self.class_fields = class_fields
        self.map_layers = map_layers
        self.map_table_depth = len(map_layers)
        self.audio_classes = audio_classes
        # The ivar record this engine's Ruby puts on every string.
        self.string_ivars = dict(string_ivars)
        self.notes = notes
        # What a row of the tilesets table is: "ace" for nine sheets and a
        # flag word per tile, "xp" for one graphic, seven autotiles and
        # passage, priority and terrain tables, None for no table at all.
        self.tileset_layout = tileset_layout
        self.tile_id_ranges = tile_id_ranges
        self.tile_id_ranges_note = tile_id_ranges_note
        # What is and is not known about the tile layers, beyond the table.
        self.layer_note = layer_note
        # Whether the VX family's command parameter layouts -- the ones
        # `validate`'s command rules and the authoring forms are written
        # against -- are this engine's. XP numbers and lays out its own.
        self.vx_command_layouts = vx_command_layouts
        self.absence_hints = {**_TABLE_ABSENCE_HINTS, **(absence_hints or {})}
        self._page_template = page_template or new_page

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
        return self.map_table_depth > META_LAYER

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
        return self.absence_hints.get(
            key, "It belongs to a different engine in the RGSS family.")

    # Three methods used to sit here -- `filename_for`, `map_filename` and
    # `database_tables` -- and nothing in the project or its tests called any
    # of them. `Project` answers the first two for the open project (and is
    # what every caller uses), and the tools build their table lists from
    # `ALL_DATABASE_TABLES`, which is a union across engines because a schema
    # is built before a project is open. A public method with no caller is
    # something a reader has to understand and cannot check.

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
        return in_editor_order(RubyObject(cls, {
            "@name": rstr(name), "@volume": volume, "@pitch": pitch,
        }))

    def new_page(self) -> RubyObject:
        """A blank event page in this engine's layout."""
        return self._page_template()

    def new_event(self, event_id: int, name: str = "", x: int = 0,
                  y: int = 0) -> RubyObject:
        """A new event whose one page is this engine's blank page.

        `new_event` builds the VX family's page; XP's has always_on_top
        where they have priority_type, and a smaller condition and graphic.
        """
        event = new_event(event_id, name, x, y)
        event.set("pages", [self.new_page()])
        return event

    def new_map(self, width: int = 20, height: int = 15,
                tileset_id: int = 1) -> RubyObject:
        """A blank ``RPG::Map`` carrying exactly this engine's fields.

        Built as the full field set of every engine, then reduced to the
        fields this engine's class documents: so the class field table is
        the single place that records the difference. It was a separate
        tuple of Ace-only fields, which could not say that XP lacks the
        parallax and scroll fields both VX engines have.
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
            "@data": Table(width, height, self.map_table_depth),
            "@events": RubyHash(),
        }
        documented = self.class_fields["RPG::Map"]
        ivars = {key: value for key, value in ivars.items()
                 if key[1:] in documented}
        return in_editor_order(RubyObject("RPG::Map", ivars))


# Why an engine lacks a table, stated once and reused by the profile, the tool
# error messages and the reference documents.
_TABLE_ABSENCE_HINTS = {
    "tilesets": ("VX has a single global tileset in Graphics/System instead of a "
                 "tileset database, so maps have no tileset_id."),
    "areas": ("VX Ace replaced encounter areas with map regions: paint region ids "
              "with map_tiles view=region (they live in the byte above the "
              "shadow bits of tile layer 3), and reference them from a map's "
              "encounter_list."),
}


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
    audio_classes=dict(BGM_FAMILY_AUDIO),
    # RGSS3 runs Ruby 1.9, which tags every string with its encoding.
    string_ivars={"E": True},
    tileset_layout="ace",
    tile_id_ranges=TILE_ID_RANGES,
    tile_id_ranges_note=TILE_ID_RANGES_NOTE,
    layer_note=None,
    vx_command_layouts=True,
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
    audio_classes=dict(BGM_FAMILY_AUDIO),
    # RGSS2 runs Ruby 1.8: strings are bare bytes.
    string_ivars={},
    tileset_layout=None,
    tile_id_ranges=TILE_ID_RANGES,
    tile_id_ranges_note=TILE_ID_RANGES_NOTE,
    layer_note=VX_LAYER_NOTE,
    vx_command_layouts=True,
    notes=[
        "VX has no Tilesets database: one global tileset lives in "
        "Graphics/System (TileA1..A5, TileB..E), so maps have no tileset_id.",
        _layer_note(VX_MAP_LAYERS),
        VX_LAYER_NOTE,
        *_SHARED_NOTES,
        "Areas.rvdata holds encounter areas; VX Ace replaced them with regions.",
        "Entries carry flat stat fields and parameter curves as Tables, not VX "
        "Ace's features/params arrays or its damage formula strings.",
        _editor_note("RPG Maker VX"),
    ],
)


# The data files XP has: the VX family's shared set and a Tilesets table,
# but an XP tileset is one graphic, seven autotiles and three per-tile
# tables rather than VX Ace's nine sheets and a flag word.
XP_DATA_FILES = _data_file_registry(
    *_SHARED_FILES,
    DataFile("tilesets", "Tilesets", ARRAY, "RPG::Tileset",
             "Tilesets: a tileset graphic, seven autotiles, and per-tile "
             "passages, priorities and terrain tags"),
)


XP_PROFILE = EngineProfile(
    key="xp",
    label="RPG Maker XP",
    project_file="Game.rxproj",
    extension=".rxdata",
    data_files=XP_DATA_FILES,
    class_fields=XP_CLASS_FIELDS,
    map_layers=XP_MAP_LAYERS,
    # One class for every kind: RGSS1 plays audio through Game_System
    # (`$game_system.bgm_play(@parameters[0])`), not through the object.
    audio_classes={kind: "RPG::AudioFile" for kind in BGM_FAMILY_AUDIO},
    # RGSS1 runs Ruby 1.8 too, so its strings are bare bytes as well.
    string_ivars={},
    tileset_layout="xp",
    tile_id_ranges=XP_TILE_ID_RANGES,
    tile_id_ranges_note=XP_TILE_ID_RANGES_NOTE,
    layer_note=XP_LAYER_NOTE,
    vx_command_layouts=False,
    page_template=new_xp_page,
    absence_hints={
        "areas": ("XP has no encounter areas: each map lists the troops it "
                  "can meet in its own encounter_list."),
    },
    notes=[
        _layer_note(XP_MAP_LAYERS),
        XP_LAYER_NOTE,
        "Each map picks a tileset with tileset_id; an XP tileset is one "
        "graphic, seven autotiles, and passage, priority and terrain tables.",
        *_SHARED_NOTES,
        "Event commands are numbered differently from VX and VX Ace (Wait is "
        "106, Set Move Route 209 with 509 step lines), so validate's command "
        "rules and most authoring forms do not apply; see command_reference.",
        "Entries carry XP's flat stats (str, dex, agi, int, pdef, mdef), SP "
        "rather than MP, and icon file names rather than icon indexes.",
        _editor_note("RPG Maker XP"),
    ],
)


PROFILES = {profile.key: profile
            for profile in (ACE_PROFILE, VX_PROFILE, XP_PROFILE)}


# ---------------------------------------------------------------------------
# Observed structure
# ---------------------------------------------------------------------------


# Real project data is about a dozen levels deep at its worst, so the depth
# cap only guards pathological input.
#
# What bounds the *graph* is the identity memo in `_observe`, not this
# budget: a value reachable by several paths is returned on before it is
# descended into, so a file whose containers link to a shared child at every
# level costs linear work rather than exponential. This comment used to
# credit the budget with that, which is a second answer to a question
# `_observe`'s own docstring already answers.
#
# The budget bounds the *size*: it is charged per inbound link and per leaf,
# before the memo is consulted, so an Array of three million distinct
# Fixnums -- no children, never memoized -- costs one unit each and stops.
# Ten times `MAX_WALK_STEPS` because this walks the whole project once to
# describe it, where a walk budget covers one reply.
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

    # The *tally* dispatches by kind, because which wrappers carry a class
    # name and a set of field names is a different question from which hold
    # other values. The descent below does not: `ruby_children` answers
    # that, and this was the call site it was extracted to replace --
    # `project.collect_strings` was converted and this one was not, so the
    # two walks still disagreed (no `tuple` here, and a RubyString's ivar
    # record is a container there and was not here).
    children = ruby_children(value)
    if children:
        # The memo comes before the tally, not after: Marshal resolves a
        # link to the same Python object, so a value reachable by many
        # paths would otherwise be counted once per path -- which is what
        # `test_shared_object_is_tallied_once` asks.
        if id(value) in seen:
            return
        seen.add(id(value))

    if isinstance(value, RubyObject):
        _tally(fields, value.cls, value.ivars)
    elif isinstance(value, RubyStruct):
        _tally(fields, value.cls, value.members)
    elif isinstance(value, (RubyUserMarshal, RubyData)):
        # These name a class but keep their state in one nested value.
        fields.setdefault(value.cls, {})

    # The wrapper kinds this used to dispatch on one by one -- RubyUClass,
    # RubyExtended, RubyIvars, RubyUserDef, RubySymbol -- are all container
    # kinds `ruby_children` already knows, so the descent is one loop.
    for item in children:
        _observe(item, fields, depth + 1, seen, budget)


def _tally(fields: dict[str, dict[str, int]], cls: str,
           names: dict[str, Any]) -> None:
    bucket = fields.setdefault(cls, {})
    for name in names:
        bare = _bare_name(name)
        bucket[bare] = bucket.get(bare, 0) + 1


def on_map(x: int, y: int, width: int, height: int) -> bool:
    """Whether a tile coordinate is inside a map of this size.

    One business rule, written four times: twice in `analysis` correctly,
    once in `event_tools` with two dead `and` clauses left from a version
    where an unmeasurable size meant "unbounded", and once in
    `map_edit_tools` with no lower bound at all -- so an event at x = -1
    survived a resize unreported, and a non-integer x out of a crafted file
    reached `>= width` as a TypeError.

    Here because `schema` sits below everything that needs it and imports
    nothing itself. Callers keep their own refusals for an unusable size and
    for a coordinate that is not a number: this answers the question it is
    named for and nothing else.
    """
    return 0 <= x < width and 0 <= y < height
