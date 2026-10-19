"""Engine-profile and schema-observation tests.

The hostile-input cases here are guards, not illustrations: each one decodes to
something plausible, or walks exponentially, or ends the session, if its guard
is removed. `scripts/prove_guards.py` reverts each and requires the matching
test to fail.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))

import real_project  # noqa: E402
from build_project import build  # noqa: E402

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.commands import compile_commands  # noqa: E402
from rpgvxace_mcp.convert import (  # noqa: E402
    TextCodec,
    normalize_strings,
    to_json,
)
from rpgvxace_mcp.docs import (  # noqa: E402
    command_reference_document,
    data_format_reference,
)
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.schema import (  # noqa: E402
    ACE_MAP_LAYERS,
    ACE_PROFILE,
    MAX_REGION_ID,
    MAX_SHADOW,
    META_LAYER,
    PROFILES,
    SchemaWalk,
    TILE_LAYER_COUNT,
    VX_MAP_LAYERS,
    VX_PROFILE,
    XP_PROFILE,
    map_filename,
    new_condition,
    new_event,
    new_event_command,
    new_graphic,
    new_map_info,
    new_move_command,
    new_move_route,
    new_page,
    observe_class_fields,
    region_of,
    shadow_of,
    with_region,
    with_shadow,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyData,
    RubyExtended,
    RubyHash,
    RubyIvars,
    RubyObject,
    RubyStruct,
    RubyUClass,
    RubyUserMarshal,
    printable_text,
    ruby_children,
)
from rpgvxace_mcp.tools import (  # noqa: E402,F401
    db_tools,
    event_tools,
    map_common,
    map_edit_tools,
    map_read_tools,
    map_tile_tools,
    misc_tools,
)
from rpgvxace_mcp.tools.registry import Context, REGISTRY, ToolError  # noqa: E402


class TestEngineProfiles(unittest.TestCase):
    def test_capabilities_match_the_engine(self):
        self.assertTrue(ACE_PROFILE.has_tileset_database)
        self.assertFalse(ACE_PROFILE.has_encounter_areas)
        self.assertTrue(ACE_PROFILE.has_region_layer)
        self.assertTrue(ACE_PROFILE.strings_carry_encoding)

        self.assertFalse(VX_PROFILE.has_tileset_database)
        self.assertTrue(VX_PROFILE.has_encounter_areas)
        self.assertFalse(VX_PROFILE.has_region_layer)
        self.assertFalse(VX_PROFILE.strings_carry_encoding)

    def test_new_map_carries_exactly_this_engine_s_fields(self):
        ace = ACE_PROFILE.new_map(20, 15)
        vx = VX_PROFILE.new_map(20, 15)
        for field in ("tileset_id", "display_name", "note", "specify_battleback",
                      "battleback1_name", "battleback2_name"):
            self.assertTrue(ace.has(field), "Ace map should have %s" % field)
            self.assertFalse(vx.has(field), "VX map should not have %s" % field)
        # The shared fields are on both.
        for field in ("width", "height", "data", "events", "encounter_step"):
            self.assertTrue(ace.has(field))
            self.assertTrue(vx.has(field))
        self.assertEqual(ace.get("data").zsize, 4)
        self.assertEqual(vx.get("data").zsize, 3)

    def test_layer_note_is_derived_from_the_layer_table(self):
        # The prose and the data cannot drift, because the prose is built from
        # the data.
        self.assertTrue(any("four layers" in note for note in ACE_PROFILE.notes))
        self.assertTrue(any("three layers" in note for note in VX_PROFILE.notes))

    def test_the_layer_note_names_three_tile_layers(self):
        note = next(n for n in ACE_PROFILE.notes if "four layers" in n)
        self.assertIn("2 upper tile layer", note)
        self.assertIn("3 shadow and region layer", note)

    def test_ace_stat_note_matches_the_real_field_layout(self):
        # An earlier note claimed actors and items carry a params array. They do
        # not: params is an array on weapons/armors/enemies and a Table on
        # classes. Asserting an engine detail we have not confirmed is the one
        # thing this project must not do.
        note = next(n for n in ACE_PROFILE.notes if "features" in n)
        self.assertIn("weapons, armors and enemies", note)
        self.assertIn("classes a params Table", note)

    def test_absence_hint_explains_the_engine_difference(self):
        self.assertIn("global tileset", VX_PROFILE.absence_hint("tilesets"))
        self.assertIn("region", ACE_PROFILE.absence_hint("areas"))

    def test_vx_audio_is_the_bgm_family(self):
        # RGSS2 plays every stored audio object itself -- `@map.bgm.play`,
        # `$data_system.sounds[0].play`, `timing.se.play`, `@params[0].play`
        # -- and only RPG::BGM, BGS, ME and SE have `play`. An
        # RPG::AudioFile there is a NoMethodError on the title screen.
        for profile in (ACE_PROFILE, VX_PROFILE):
            for kind in ("bgm", "bgs", "me", "se"):
                with self.subTest(engine=profile.key, kind=kind):
                    self.assertEqual(profile.new_audio_file(kind).cls,
                                     "RPG::" + kind.upper())
                    self.assertIn("RPG::" + kind.upper(),
                                  profile.class_fields)
        self.assertEqual(VX_PROFILE.new_map().get("bgm").cls, "RPG::BGM")
        self.assertEqual(VX_PROFILE.new_map().get("bgs").cls, "RPG::BGS")

    def test_unknown_audio_kind_is_refused(self):
        # Falling back to BGM would write an RPG::BGM into an ME or SE slot.
        with self.assertRaises(ValueError) as caught:
            ACE_PROFILE.new_audio_file("bgmm")
        self.assertIn("unknown audio kind", str(caught.exception))

    def test_map_filename_requires_a_real_id(self):
        self.assertEqual(map_filename(5, ".rvdata2"), "Map005.rvdata2")
        # A float would truncate silently and address a different map.
        for bad in (2.9, True, "3", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    map_filename(bad, ".rvdata2")
        for bad in (0, -1, 100000):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    map_filename(bad, ".rvdata2")


class TestTheXPProfile(unittest.TestCase):
    """RPG Maker XP (RGSS1), from its reference and its default scripts.

    The class fields are the RGSS1 reference's; the command table is the
    dispatch in XP's `Interpreter (part 2)`; the map facts are XP's
    `Game_Map` and `Sprite_Character` and copies of XP editor data.
    """

    def test_capabilities(self):
        self.assertEqual((XP_PROFILE.key, XP_PROFILE.extension,
                          XP_PROFILE.project_file),
                         ("xp", ".rxdata", "Game.rxproj"))
        self.assertIs(PROFILES["xp"], XP_PROFILE)
        self.assertTrue(XP_PROFILE.has_tileset_database)
        self.assertFalse(XP_PROFILE.has_encounter_areas)
        self.assertFalse(XP_PROFILE.has_region_layer)
        self.assertFalse(XP_PROFILE.strings_carry_encoding)
        self.assertEqual(XP_PROFILE.map_table_depth, 3)
        # Its tilesets are not VX Ace's nine sheets and a flag word.
        self.assertEqual(XP_PROFILE.tileset_layout, "xp")
        self.assertEqual(ACE_PROFILE.tileset_layout, "ace")
        self.assertIsNone(VX_PROFILE.tileset_layout)

    def test_the_data_files_are_xp_s(self):
        self.assertEqual(sorted(XP_PROFILE.data_files), sorted(
            ["actors", "classes", "skills", "items", "weapons", "armors",
             "enemies", "troops", "states", "animations", "tilesets",
             "common_events", "system", "map_infos", "scripts"]))
        self.assertIn("areas", XP_PROFILE.absence_hint("areas"))

    def test_the_classes_are_rgss1_s(self):
        fields = XP_PROFILE.class_fields
        for cls, expected in {
            "RPG::Tileset": {"tileset_name", "autotile_names", "passages",
                             "priorities", "terrain_tags", "panorama_name",
                             "fog_name", "battleback_name"},
            "RPG::Actor": {"parameters", "exp_basis", "exp_inflation",
                           "weapon_id", "armor4_fix", "battler_name"},
            "RPG::Class::Learning": {"level", "skill_id"},
            "RPG::System": {"words", "cursor_se", "windowskin_name",
                            "magic_number", "battle_transition"},
            "RPG::System::Words": {"gold", "sp", "pdef", "armor4"},
            "RPG::Event::Page": {"always_on_top"},
            "RPG::Event::Page::Graphic": {"character_hue", "opacity",
                                          "blend_type"},
            "RPG::AudioFile": {"name", "volume", "pitch"},
            "RPG::Map": {"tileset_id"},
        }.items():
            with self.subTest(cls=cls):
                self.assertLessEqual(expected, set(fields[cls]))
        # What XP does not have.
        self.assertNotIn("priority_type", fields["RPG::Event::Page"])
        self.assertNotIn("character_index", fields["RPG::Event::Page::Graphic"])
        self.assertNotIn("item_valid", fields["RPG::Event::Page::Condition"])
        self.assertNotIn("wait", fields["RPG::MoveRoute"])
        self.assertNotIn("parallax_name", fields["RPG::Map"])
        self.assertNotIn("RPG::BGM", fields)

    def test_a_new_map_is_xp_s(self):
        game_map = XP_PROFILE.new_map(20, 15, 2)
        self.assertEqual(sorted(game_map.fields()), sorted(
            XP_PROFILE.class_fields["RPG::Map"]))
        self.assertEqual(game_map.get("tileset_id"), 2)
        self.assertEqual(game_map.get("data").zsize, 3)
        self.assertEqual(game_map.get("bgm").cls, "RPG::AudioFile")
        self.assertEqual(game_map.get("bgs").get("volume"), 80)

    def test_a_new_page_is_xp_s(self):
        page = XP_PROFILE.new_page()
        self.assertEqual(sorted(page.fields()), sorted(
            XP_PROFILE.class_fields["RPG::Event::Page"]))
        for field, cls in (("condition", "RPG::Event::Page::Condition"),
                           ("graphic", "RPG::Event::Page::Graphic"),
                           ("move_route", "RPG::MoveRoute")):
            with self.subTest(field=field):
                self.assertEqual(sorted(page.get(field).fields()),
                                 sorted(XP_PROFILE.class_fields[cls]))
        # The reference's defaults.
        self.assertEqual(page.get("graphic").get("opacity"), 255)
        self.assertIs(page.get("always_on_top"), False)
        event = XP_PROFILE.new_event(3, "Gate", 4, 5)
        self.assertEqual(event.get("pages")[0].fields(), page.fields())
        # And VX Ace's are unchanged.
        self.assertEqual(ACE_PROFILE.new_page().fields(), new_page().fields())

    def test_tile_ids_are_autotiles_then_the_tileset(self):
        ranges = XP_PROFILE.tile_id_ranges
        self.assertEqual(ranges["autotile 1"], (48, 95))
        self.assertEqual(ranges["autotile 7"], (336, 383))
        self.assertEqual(ranges["tileset"][0], 384)
        self.assertIn("Advisory", XP_PROFILE.tile_id_ranges_note)
        self.assertEqual(sorted(XP_PROFILE.map_layers), [0, 1, 2])
        self.assertIn("priorit", XP_PROFILE.layer_note)


class TestTheTileLayerModel(unittest.TestCase):
    """What each layer of a map's tile table holds, as the engines read it.

    The table this replaces called Ace's layer 2 a four-bit shadow mask and
    layer 3 a bare region id. RGSS3's `Game_Map` says otherwise:
    `layered_tiles` is `[2, 1, 0].collect {|z| tile_id(x, y, z) }` and
    `region_id` is `@map.data[x, y, 3] >> 8`. So does a real project: the 39
    maps of the author's game hold 4,932 non-zero cells on layer 2, every one a
    B-E tile id, and layer 3 holds 337 cells of exactly 256 (region 1) and
    556 below 16 (shadow only). RGSS2's `Game_Map#passable?` looks each of
    `data[x, y, i]` for i in `[2, 1, 0]` up in the passage table, so VX's
    layer 2 is a tile layer as well.
    """

    def test_ace_layers_0_to_2_hold_tile_ids(self):
        for z in range(TILE_LAYER_COUNT):
            with self.subTest(z=z):
                self.assertIn("tile layer", ACE_MAP_LAYERS[z])
                self.assertNotIn("shadow", ACE_MAP_LAYERS[z])
                self.assertNotIn("region", ACE_MAP_LAYERS[z])
        self.assertIn("B-E", ACE_MAP_LAYERS[2])

    def test_ace_layer_3_packs_shadow_and_region(self):
        self.assertEqual(META_LAYER, 3)
        self.assertEqual(sorted(ACE_MAP_LAYERS), [0, 1, 2, 3])
        self.assertIn("shadow", ACE_MAP_LAYERS[3])
        self.assertIn("region", ACE_MAP_LAYERS[3])
        self.assertIn("256", ACE_MAP_LAYERS[3])

    def test_vx_layer_2_is_a_tile_layer_not_shadows(self):
        self.assertEqual(sorted(VX_MAP_LAYERS), [0, 1, 2])
        for z in range(TILE_LAYER_COUNT):
            with self.subTest(z=z):
                self.assertIn("tile layer", VX_MAP_LAYERS[z])
                self.assertNotIn("shadow mask", VX_MAP_LAYERS[z])

    def test_what_is_unconfirmed_about_vx_layers_says_so(self):
        # No real VX project has been read: the passable? reading is the
        # whole of the evidence, and the profile and the served reference
        # say which part is advisory.
        note = next(n for n in VX_PROFILE.notes if "passable?" in n)
        self.assertIn("Advisory", note)
        self.assertIn("no region layer", note)
        with tempfile.TemporaryDirectory() as tmp:
            build(Path(tmp) / "project", engine="vx")
            reference = data_format_reference(Project(Path(tmp) / "project"))
        self.assertIn(note, reference)

    def test_the_region_is_the_byte_above_the_shadow_bits(self):
        self.assertEqual(region_of(256), 1)
        self.assertEqual(shadow_of(256), 0)
        self.assertEqual(region_of(0x3F05), 63)
        self.assertEqual(shadow_of(0x3F05), 5)
        # A raw 1 is a shadow quarter, not region 1: the fixture stored it
        # that way and real Ace draws it as a shadow.
        self.assertEqual(region_of(1), 0)
        self.assertEqual(shadow_of(1), 1)

    def test_writing_one_part_keeps_the_other(self):
        self.assertEqual(with_region(5, 3), 0x305)
        self.assertEqual(with_region(0x3F05, 0), 5)
        self.assertEqual(with_shadow(0x300, 15), 0x30F)
        self.assertEqual(with_shadow(0x30F, 0), 0x300)

    def test_the_ceilings_are_the_editor_s(self):
        self.assertEqual(MAX_REGION_ID, 63)
        self.assertEqual(MAX_SHADOW, 15)


# Marshal hex of objects the VX Ace editor wrote, each copied from the author's
# game; the comment names the file and the values. Marshal keeps instance
# variables in the order it is given them, so a created object whose values
# equal one of these must dump to exactly these bytes.
REAL_BLANK_COMMAND = (  # CommonEvents.rvdata2: code 0, indent 0, []
    "04086f3a165250473a3a4576656e74436f6d6d616e64083a0c40696e64656e7469003a0a"
    "40636f646569003a1040706172616d65746572735b00")
REAL_SE = (  # Animations.rvdata2: "Blow3", volume 95, pitch 100
    "04086f3a0c5250473a3a5345083a0a406e616d6549220a426c6f7733063a0645543a0b40"
    "706974636869693a0c40766f6c756d656964")
REAL_ME = (  # System.rvdata2: "Victory1", volume 100, pitch 100
    "04086f3a0c5250473a3a4d45083a0a406e616d6549220d566963746f727931063a064554"
    "3a0b40706974636869693a0c40766f6c756d656969")
REAL_DEFAULT_PAGE = (  # Map007.rvdata2: a page with every default value
    "04086f3a155250473a3a4576656e743a3a50616765123a0f40636f6e646974696f6e6f3a"
    "205250473a3a4576656e743a3a506167653a3a436f6e646974696f6e123a134073776974"
    "6368315f76616c6964463a1340737769746368325f76616c6964463a1440766172696162"
    "6c655f76616c6964463a174073656c665f7377697463685f76616c6964463a1040697465"
    "6d5f76616c6964463a11406163746f725f76616c6964463a1040737769746368315f6964"
    "69063a1040737769746368325f696469063a11407661726961626c655f696469063a1440"
    "7661726961626c655f76616c756569003a144073656c665f7377697463685f6368492206"
    "41063a0645543a0d406974656d5f696469063a0e406163746f725f696469063a0d406772"
    "61706869636f3a1e5250473a3a4576656e743a3a506167653a3a477261706869630a3a0d"
    "4074696c655f696469003a14406368617261637465725f6e616d65492200063b13543a15"
    "406368617261637465725f696e64657869003a0f40646972656374696f6e69073a0d4070"
    "61747465726e69003a0f406d6f76655f7479706569003a10406d6f76655f737065656469"
    "083a14406d6f76655f6672657175656e637969083a10406d6f76655f726f7574656f3a13"
    "5250473a3a4d6f7665526f757465093a0c40726570656174543a0f40736b69707061626c"
    "65463a0a4077616974463a0a406c6973745b066f3a155250473a3a4d6f7665436f6d6d61"
    "6e64073a0a40636f646569003a1040706172616d65746572735b003a104077616c6b5f61"
    "6e696d65543a1040737465705f616e696d65463a1340646972656374696f6e5f66697846"
    "3a0d407468726f756768463a13407072696f726974795f7479706569003a0d4074726967"
    "67657269003b255b066f3a165250473a3a4576656e74436f6d6d616e64083a0c40696e64"
    "656e7469003b2769003b285b00")

# The editor's order for the classes no real object matches by value (every
# real event has a page of its own, every map has tiles), read off every
# instance in the author's game and in the RuralFarm sample game. The two agree,
# and each holds exactly one order per class.
REAL_IVAR_ORDERS = {
    "RPG::Event": ["@id", "@name", "@x", "@y", "@pages"],
    "RPG::MapInfo": ["@scroll_x", "@name", "@expanded", "@order", "@scroll_y",
                     "@parent_id"],
    "RPG::Map": [
        "@parallax_name", "@height", "@events", "@parallax_sx", "@bgm",
        "@tileset_id", "@encounter_step", "@width", "@data", "@bgs",
        "@parallax_loop_y", "@autoplay_bgm", "@encounter_list",
        "@autoplay_bgs", "@parallax_show", "@scroll_type", "@parallax_loop_x",
        "@disable_dashing", "@parallax_sy", "@display_name",
        "@specify_battleback", "@note", "@battleback1_name",
        "@battleback2_name"],
}


ACE_STRINGS = TextCodec("utf-8", {"E": True})


def ace_hex(value):
    """A created object as the Ace editor would store it, in hex."""
    return marshal_rb.dump(normalize_strings(value, ACE_STRINGS)).hex()


class TestNewObjectsAreTheEditorsBytes(unittest.TestCase):
    """What the server creates is what the editor writes, byte for byte.

    Every one of the 13,900 event commands in the author's game and the RuralFarm
    sample stores @indent before @code; `new_event_command` wrote @code
    first. The file loaded, because Marshal reads ivars by name, but it was
    not the file the editor writes.
    """

    def test_a_blank_command_is_the_editors_bytes(self):
        self.assertEqual(ace_hex(new_event_command(0, 0, [])),
                         REAL_BLANK_COMMAND)

    def test_a_new_page_is_the_editors_bytes(self):
        self.assertEqual(ace_hex(new_page()), REAL_DEFAULT_PAGE)

    def test_me_and_se_are_the_editors_bytes(self):
        self.assertEqual(ace_hex(ACE_PROFILE.new_audio_file(
            "se", "Blow3", 95, 100)), REAL_SE)
        self.assertEqual(ace_hex(ACE_PROFILE.new_audio_file(
            "me", "Victory1", 100, 100)), REAL_ME)

    def test_events_map_infos_and_maps_have_the_editors_order(self):
        built = {"RPG::Event": new_event(1, "EV001", 2, 3),
                 "RPG::MapInfo": new_map_info("MAP001", 0, 1),
                 "RPG::Map": ACE_PROFILE.new_map(20, 15)}
        for cls, value in built.items():
            with self.subTest(cls=cls):
                self.assertEqual(list(value.ivars), REAL_IVAR_ORDERS[cls])

    def test_a_raw_object_parameter_is_put_in_the_editors_order(self):
        # A caller's JSON object has whatever key order they typed.
        command = compile_commands([{"raw": {"code": 250, "parameters": [
            {"__class__": "RPG::SE", "volume": 95, "pitch": 100,
             "name": "Blow3"}]}}], ACE_STRINGS, engine="vx_ace")[0]
        self.assertEqual(
            marshal_rb.dump(command.get("parameters")[0]).hex(), REAL_SE)


@unittest.skipUnless(os.environ.get("RPGVXACE_TEST_PROJECT"),
                     "set RPGVXACE_TEST_PROJECT to a VX Ace project")
class TestNewObjectsMatchARealProject(unittest.TestCase):
    """Every real object of a class the server builds, rebuilt and compared.

    Opt-in and read-only. For each instance, the server's own constructor is
    called and its fields are given the real object's values -- assigning to
    an existing key keeps its place -- and the result must dump to the real
    object's bytes. A difference left is a difference of order alone.
    """

    def templates(self):
        return {
            "RPG::EventCommand": lambda: new_event_command(),
            "RPG::MoveCommand": lambda: new_move_command(),
            "RPG::MoveRoute": new_move_route,
            "RPG::Event::Page::Condition": new_condition,
            "RPG::Event::Page::Graphic": new_graphic,
            "RPG::Event::Page": new_page,
            "RPG::Event": lambda: new_event(1),
            "RPG::MapInfo": lambda: new_map_info(""),
            "RPG::Map": lambda: ACE_PROFILE.new_map(),
            **{"RPG::" + kind.upper(): (
                lambda kind=kind: ACE_PROFILE.new_audio_file(kind))
               for kind in ("bgm", "bgs", "me", "se")},
        }

    def test_every_constructed_class_rebuilds_to_the_real_bytes(self):
        project = Project(os.environ["RPGVXACE_TEST_PROJECT"], read_only=True)
        if project.engine != "vx_ace":
            self.skipTest("the byte comparison is for VX Ace projects")
        templates = self.templates()
        compared = {cls: 0 for cls in templates}
        values = [project.load_map(map_id)
                  for map_id in real_project.editor_written_map_ids(project)]
        values += [project.load_data(key) for key, name in
                   (("map_infos", "MapInfos"), ("common_events", "CommonEvents"),
                    ("troops", "Troops"), ("system", "System"),
                    ("animations", "Animations"))
                   if real_project.editor_written(
                       project, name + project.profile.extension)]
        seen = set()
        stack = list(values)
        while stack:
            value = stack.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            stack.extend(ruby_children(value))
            if not isinstance(value, RubyObject) or value.cls not in templates:
                continue
            built = templates[value.cls]()
            if sorted(built.ivars) != sorted(value.ivars):
                continue
            for key, item in value.ivars.items():
                built.ivars[key] = item
            compared[value.cls] += 1
            self.assertEqual(marshal_rb.dump(built).hex(),
                             marshal_rb.dump(value).hex(), value.cls)
        for cls, count in compared.items():
            with self.subTest(cls=cls):
                self.assertGreater(count, 0, "no real %s was compared" % cls)


class TestSchemaObservation(unittest.TestCase):
    def test_shared_object_is_tallied_once(self):
        # Marshal resolves a link to the same Python object, so a value reachable
        # by many paths would otherwise be counted once per path.
        shared = RubyObject("Shared", {"@a": 1})
        fields = observe_class_fields([shared, shared, [shared, shared]])
        self.assertEqual(fields["Shared"]["a"], 1)

    def test_link_shared_graph_does_not_explode(self):
        # 20 levels, each holding the same child twice -- the shape a Marshal
        # file of a few hundred bytes can describe with object links.
        #
        # This has to measure the WORK, not the tally: the tally is protected by
        # its own memo, so counting fields would pass even while the walk did
        # 2**21 visits. nodes_visited is the population that actually grows.
        node: object = RubyObject("Leaf", {"@x": 1})
        for _ in range(20):
            node = [node, node]
        walk = SchemaWalk().visit(node)
        self.assertEqual(walk.fields["Leaf"]["x"], 1)
        self.assertFalse(walk.truncated)
        self.assertLess(walk.nodes_visited, 100,
                        "the walk visited %d values for a 21-node graph"
                        % walk.nodes_visited)

    def test_a_walk_that_hits_its_budget_says_so(self):
        # An honest partial answer beats a partial answer presented as whole.
        wide = [RubyObject("Wide%d" % n, {"@i": n}) for n in range(200)]
        walk = SchemaWalk(budget=20)
        walk.visit(wide)
        self.assertTrue(walk.truncated)
        self.assertEqual(walk.nodes_visited, walk.budget)

    def test_class_variables_do_not_collapse_onto_instance_variables(self):
        fields = observe_class_fields(RubyObject("Foo", {"@x": 1, "@@x": 2}))
        self.assertEqual(sorted(fields["Foo"]), ["@x", "x"])

    def test_wrapper_types_are_walked(self):
        # Each of these used to be a traversal dead end, so anything beneath one
        # was missing from a report presented as observed authority.
        inside = RubyObject("Inside", {"@deep": 1})
        for wrapper in (
            RubyIvars([inside], {"E": True}),
            RubyStruct("AStruct", {"member": inside}),
            RubyUserMarshal("AMarshal", inside),
            RubyData("AData", inside),
            RubyUClass("ASubclass", [inside]),
            RubyExtended(["AModule"], inside),
        ):
            with self.subTest(wrapper=type(wrapper).__name__):
                fields = observe_class_fields(wrapper)
                self.assertIn("Inside", fields,
                              "%s was not walked" % type(wrapper).__name__)

    def test_struct_members_are_tallied_under_the_struct(self):
        fields = observe_class_fields(RubyStruct("Point", {"x": 1, "y": 2}))
        self.assertEqual(sorted(fields["Point"]), ["x", "y"])

    def test_hash_keys_and_values_are_both_walked(self):
        hash_obj = RubyHash([(RubyObject("Key", {"@k": 1}),
                              RubyObject("Value", {"@v": 2}))])
        fields = observe_class_fields(hash_obj)
        self.assertIn("Key", fields)
        self.assertIn("Value", fields)


class TestResponseTextSafety(unittest.TestCase):
    """A corrupt name must not be able to end the session."""

    def test_printable_text_replaces_lone_surrogates(self):
        self.assertEqual(printable_text("ok"), "ok")
        cleaned = printable_text("Caf\udce9")
        cleaned.encode("utf-8")  # must not raise

    def test_class_names_from_a_file_survive_json_encoding(self):
        # marshal decodes names with surrogateescape, so a corrupt byte in a
        # class name arrives as a lone surrogate. json.dumps emits it happily
        # and the UTF-8 transport then cannot encode it.
        obj = RubyObject("Bad\udc80Name", {"@a\udc81": 1})
        payload = to_json(obj, TextCodec("utf-8"))
        text = json.dumps(payload, ensure_ascii=False)
        text.encode("utf-8")  # must not raise
        self.assertNotIn("\udc80", text)


class TestUnmodelledEngine(unittest.TestCase):
    """An unmodelled engine opens with a flag, described with the closest model.

    This was XP until XP got a profile of its own. Every Marshal engine the
    server detects is now modelled, so the engine here is one detection is
    taught about for the test: the path it exercises is the one a future
    RGSS engine would take, and the flag still guards it.
    """

    SIGNATURE = ("rgss_test", "Game.rtproj", ".rtdata")

    def setUp(self):
        from unittest import mock
        from rpgvxace_mcp import project as project_module

        patcher = mock.patch.object(
            project_module, "ENGINE_SIGNATURES",
            project_module.ENGINE_SIGNATURES + [self.SIGNATURE])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-unknown-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "Data").mkdir(parents=True)
        (self.tmp / "Game.rtproj").write_text("?", encoding="ascii")
        (self.tmp / "Data" / "Actors.rtdata").write_bytes(marshal_rb.dump([None]))
        (self.tmp / "Data" / "MapInfos.rtdata").write_bytes(
            marshal_rb.dump(RubyHash()))
        self.project = Project(self.tmp, allow_other_engines=True)
        self.ctx = Context(self.project)

    def test_it_needs_the_flag_and_says_so(self):
        with self.assertRaises(Exception) as caught:
            Project(self.tmp)
        self.assertIn("--allow-other-engines", str(caught.exception))
        self.assertEqual(self.project.profile.key, "vx")

    def test_documents_say_which_model_is_being_used(self):
        for document in (data_format_reference(self.project),
                         command_reference_document(self.project)):
            self.assertIn("described with the RPG Maker VX model", document)
            self.assertIn("unconfirmed", document)

    def test_command_reference_uses_the_modelled_engine_table(self):
        # Keying off the detected engine fell through to Ace's table while the
        # heading said VX.
        document = command_reference_document(self.project)
        self.assertIn("| 136 | Change Encounter |", document)
        self.assertNotIn("| 285 |", document)

    def test_creating_a_map_is_refused(self):
        # Reading is fine; inventing a file whose field set we do not know is not.
        with self.assertRaises(ToolError) as caught:
            REGISTRY["map_edit"].handler(
                self.ctx, {"op": "create", "name": "Somewhere"})
        self.assertIn("field set for a new map is not known", str(caught.exception))


class TestSchemaReportProvenance(unittest.TestCase):
    # Built once for the class: nothing here writes, so the files are the
    # same for every test, and a fresh `Project` over them is still made per
    # test in `setUp`.
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="rpgvxace-prov-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        build(cls.tmp / "project")

    def setUp(self):
        self.ctx = Context(Project(self.tmp / "project"))

    def test_advisory_notes_travel_with_the_data(self):
        report = REGISTRY["project_info"].handler(
            self.ctx, {"include_schema": True})["schema"]
        self.assertIn("Advisory", report["tile_id_ranges_note"])
        self.assertIn("Advisory", report["tileset_flag_bits_note"])

    def test_the_report_names_this_project_s_engine(self):
        report = REGISTRY["project_info"].handler(
            self.ctx, {"include_schema": True})["schema"]
        self.assertIn("RPG Maker VX Ace", report["note"])
        self.assertNotIn("RGSS2", report["note"])


class TestTheReferenceAgreesWithTheTables(unittest.TestCase):
    """Every shared-value list in the document comes from the live table.

    Hand-typing them is what let one of the six drift into contradicting the
    module it describes. Generated, they cannot.
    """

    # Built once for the class, for the reason given on the class above:
    # these only read, and each test still gets its own `Project` objects.
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="docs-test-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        build(cls.tmp / "ace", "vx_ace")
        build(cls.tmp / "vx", "vx")

    def setUp(self):
        self.ace = Project(self.tmp / "ace")
        self.vx = Project(self.tmp / "vx")

    def document(self, project):
        return command_reference_document(project)

    def test_every_switch_operation_is_named(self):
        from rpgvxace_mcp.commands import SWITCH_OPERATIONS
        text = self.document(self.ace)
        for code, name in SWITCH_OPERATIONS.items():
            self.assertIn(str(code), text)
            self.assertIn(name, text, "%d -> %s missing" % (code, name))

    def test_every_variable_operation_is_named(self):
        from rpgvxace_mcp.commands import VARIABLE_OPERATIONS
        text = self.document(self.ace)
        for code, symbol in VARIABLE_OPERATIONS.items():
            # The code beside its operator, as the document writes them
            # ("2 -="). A bare "2" is in every document.
            self.assertIn("%d %s" % (code, symbol), text,
                          "variable operation %d (%s) is not documented"
                          % (code, symbol))

    def test_every_comparison_is_named(self):
        from rpgvxace_mcp.commands import COMPARISONS
        text = self.document(self.ace)
        for name in COMPARISONS.values():
            self.assertIn(name, text, "comparison %s missing" % name)

    def test_the_ace_document_does_not_claim_the_unknown_operands(self):
        """The drift, stated as a test.

        `commands.py` says VX Ace reorganised operand types above 2 and that
        this server does not claim to know the layout. The document said
        "Operand types: 0 constant, 1 variable, 2 random range." with no
        qualification, which reads as a closed set.
        """
        text = self.document(self.ace)
        section = text[text.index("Shared parameter values"):]
        marker = section[:section.index("##", 3)] if "##" in section[3:] else section
        self.assertTrue(
            "not known" in marker or "read a working" in marker
            or "confirmed" in marker,
            "the VX Ace reference states the operand types without saying "
            "the higher ones are not modelled for this engine")

    def test_the_vx_document_names_the_operands_vx_really_has(self):
        from rpgvxace_mcp.commands import VX_VARIABLE_OPERANDS
        text = self.document(self.vx)
        for name in VX_VARIABLE_OPERANDS.values():
            self.assertIn(name, text,
                          "VX operand %r is in the table and not in the VX "
                          "document" % name)

    def test_the_two_documents_differ(self):
        """An engine-keyed document whose sections are identical is not one."""
        ace, vx = self.document(self.ace), self.document(self.vx)
        self.assertNotEqual(ace, vx)
        # Different in the shared values, which is what the engines disagree
        # about -- not merely in a header that names the engine.
        marker = "Shared parameter values"
        self.assertNotEqual(ace[ace.index(marker):], vx[vx.index(marker):])

    def test_a_condition_type_is_not_typed_out_twice(self):
        """The Conditional Branch types come from the COND_* constants."""
        from rpgvxace_mcp import commands
        names = {value: name for name, value in vars(commands).items()
                 if name.startswith("COND_")}
        self.assertTrue(names, "no COND_ constants to generate from")
        text = self.document(self.ace)
        for code, name in names.items():
            # The code beside its type name ("3 timer"): a bare digit is in
            # every document, so omitting a type left this green.
            kind = name[len("COND_"):].lower().replace("_", " ")
            self.assertIn("%d %s" % (code, kind), text,
                          "conditional branch type %d (%s) is not documented"
                          % (code, kind))


class TestTheTablesKeyedOffOneListAgree(unittest.TestCase):
    """A second table of the same keys is a second thing to keep in step.

    Three of these, each found by reading and each silent when wrong:

    `_conditions` in `map_read_tools` hand-wrote the six page-condition
    flags as a run of `if`s. `PAGE_CONDITION_FLAGS` exists, its own comment
    says it is there so "the field docs, the constructor and the validate
    rule agree", and this was the fourth reader -- the one that agreed by
    coincidence. A seventh flag would have been documented, constructed and
    validated, and not described.

    `SUMMARY_FIELDS` in `db_tools` is keyed by table name beside
    `ALL_DATABASE_TABLES`, which is derived from the file registry. Its
    read is `SUMMARY_FIELDS.get(table, [])`, so a table the schema gains
    lists with no summary columns and says nothing.

    `pattern_safety` keys three tables by the same eight regex category
    names, and both readers fail open in the refuse-more direction -- so a
    ninth category in one table and not the others degrades quietly, on the
    path that decides whether a pattern is safe to run.
    """

    def test_every_page_condition_flag_has_a_sentence(self):
        from rpgvxace_mcp.schema import PAGE_CONDITION_FLAGS
        from rpgvxace_mcp.tools.map_read_tools import _CONDITION_SENTENCES
        self.assertEqual(sorted(_CONDITION_SENTENCES),
                         sorted(PAGE_CONDITION_FLAGS))

    def test_every_summary_table_is_a_table(self):
        from rpgvxace_mcp.schema import ALL_DATABASE_TABLES
        from rpgvxace_mcp.tools.db_tools import SUMMARY_FIELDS
        self.assertEqual(sorted(SUMMARY_FIELDS), sorted(ALL_DATABASE_TABLES),
                         "a table with no summary columns lists with none "
                         "and says nothing about it")

    def test_every_regex_category_is_in_every_table(self):
        from rpgvxace_mcp.tools import pattern_safety
        names = sorted(pattern_safety._CATEGORIES)
        # Not a count copied from the implementation: that the table is not
        # empty, and that what refers to its names refers to names it has.
        self.assertTrue(names, "no regex categories at all")
        self.assertTrue(pattern_safety._CATEGORY_DISJOINT)
        for pair in pattern_safety._CATEGORY_DISJOINT:
            with self.subTest(pair=sorted(pair)):
                self.assertEqual(len(pair), 2, "a pair is two categories")
                self.assertEqual(sorted(set(pair) - set(names)), [],
                                 "this pair names a category no table has")

    def test_each_category_test_and_sample_agree(self):
        """The sample has to be a character its own test accepts.

        Two tables meant `CATEGORY_SPACE` could have had a sample that is
        not a space, and the shape budget would have measured the wrong
        alphabet with nothing failing.
        """
        from rpgvxace_mcp.tools import pattern_safety
        for name, (test, sample) in pattern_safety._CATEGORIES.items():
            with self.subTest(category=name):
                self.assertTrue(test(sample),
                                "%s samples %r, which it rejects"
                                % (name, sample))


# Last in the file, deliberately. It used to sit mid-module,
# and module statements run in order -- so `python tests/test_schema.py`
# called `unittest.main()` (which exits) before the classes below
# it existed, and reported success having skipped them. pytest
# collects either way, which is why nobody noticed.
if __name__ == "__main__":
    unittest.main()
