"""The authoring DSL's friendly forms, held to what the editor writes.

Every form here was added after reading the commands it builds out of
editor-written VX Ace data: the user's own game and the sample game that
ships with the editor's farm tiles DLC. The hex strings below are
`Marshal.dump` of a real command's parameter list, copied out of those
files, so a form that drifts by one type -- an int where the editor writes
`false`, a Tone written as an Array, `@volume` before `@pitch` -- fails
here rather than in somebody's editor.

VX has no editor-written data to compare against, so its layouts are taken
from VX's own `Game_Interpreter` and `Game_Character`, and the VX tests pin
the places the two engines differ: the shop's goods carry no price, Show
Animation and Show Balloon Icon have no wait flag, Battle Processing has no
random encounter. A form whose VX layout cannot be confirmed is refused on
VX, and that refusal is tested too.

`TestEveryRealCommandRebuilds` runs only when RPGVXACE_TEST_PROJECT names a
real VX Ace project: it reads every command of every supported code out of
that project, writes the DSL entry that should build it, and requires the
compiled parameters to be byte-identical to the editor's.
"""

import os
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.commands import (  # noqa: E402
    CommandError,
    MOVE_COMMAND_NAMES,
    MOVE_STEP_CODES,
    command_table,
    compile_commands,
    param_specs,
    summarize,
)
from rpgvxace_mcp.convert import TextCodec  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    Color,
    RubyObject,
    RubyString,
    Tone,
)
from rpgvxace_mcp.tools.registry import ToolError  # noqa: E402

from tool_case import ToolTest  # noqa: E402


ACE = TextCodec("utf-8", {"E": True})
VX = TextCodec("cp1252")


def compiled(entry, engine="vx_ace", codec=None):
    """One entry's commands, compiled for one engine."""
    codec = codec or (ACE if engine == "vx_ace" else VX)
    return compile_commands([entry], codec, engine=engine)


def params_hex(command):
    return marshal_rb.dump(command.get("parameters")).hex()


# (DSL entry, the code it opens with, Marshal hex of a real command's
# parameters that the entry must reproduce). Each hex is copied from an
# editor-written VX Ace file; the comment names the parameters it holds.
REAL_ACE_COMMANDS = [
    # 125 [0, 0, 99999]
    ({"gold": {"op": "add", "value": 99999}}, 125,
     "04085b086900690069039f8601"),
    # 125 [0, 0, 50]
    ({"gold": {"op": "add", "value": 50}}, 125, "04085b08690069006937"),
    # 126 [6, 0, 0, 5]
    ({"item": {"id": 6, "op": "add", "value": 5}}, 126,
     "04085b09690b69006900690a"),
    # 127 [61, 0, 0, 1, false]
    ({"weapon": {"id": 61, "op": "add", "value": 1}}, 127,
     "04085b0a694269006900690646"),
    # 128 [31, 0, 0, 1, false]
    ({"armor": {"id": 31, "op": "add", "value": 1}}, 128,
     "04085b0a692469006900690646"),
    # 129 [2, 0, 0]
    ({"party": {"actor": 2, "op": "add"}}, 129, "04085b08690769006900"),
    # 129 [1, 0, 1]
    ({"party": {"actor": 1, "op": "add", "initialize": True}}, 129,
     "04085b08690669006906"),
    # 203 [8, 0, 3, 49, 0]
    ({"event_location": {"event": 8, "x": 3, "y": 49}}, 203,
     "04085b0a690d6900690869366900"),
    # 205 [0, MoveRoute(repeat=false, skippable=true, wait=false,
    #      [42 [0], 15 [1], 42 [55], 15 [1], 42 [155], 15 [1], 42 [255],
    #       15 [1], 0 []])]
    ({"move_route": {"target": "this", "skippable": True, "wait": False,
                     "steps": [{"opacity": 0}, {"wait": 1},
                               {"opacity": 55}, {"wait": 1},
                               {"opacity": 155}, {"wait": 1},
                               {"opacity": 255}, {"wait": 1}]}}, 205,
     "04085b0769006f3a135250473a3a4d6f7665526f757465093a0c4072657065617446"
     "3a0f40736b69707061626c65543a0a4077616974463a0a406c6973745b0e6f3a1552"
     "50473a3a4d6f7665436f6d6d616e64073a0a40636f6465692f3a1040706172616d65"
     "746572735b0669006f3b0a073b0b69143b0c5b0669066f3b0a073b0b692f3b0c5b06"
     "693c6f3b0a073b0b69143b0c5b0669066f3b0a073b0b692f3b0c5b0669019b6f3b0a"
     "073b0b69143b0c5b0669066f3b0a073b0b692f3b0c5b066901ff6f3b0a073b0b6914"
     "3b0c5b0669066f3b0a073b0b69003b0c5b00"),
    # 205 [-1, MoveRoute(repeat=false, skippable=true, wait=true,
    #      [44 [SE("Darkness1", pitch 150, volume 100)], 32 [], 13 [],
    #       31 [], 0 []])]
    ({"move_route": {"target": "player", "skippable": True,
                     "steps": [{"se": {"name": "Darkness1", "pitch": 150,
                                       "volume": 100}},
                               "walk_anime_off", "step_backward",
                               "walk_anime_on"]}}, 205,
     "04085b0769fa6f3a135250473a3a4d6f7665526f757465093a0c4072657065617446"
     "3a0f40736b69707061626c65543a0a4077616974543a0a406c6973745b0a6f3a1552"
     "50473a3a4d6f7665436f6d6d616e64073a0a40636f646569313a1040706172616d65"
     "746572735b066f3a0c5250473a3a5345083a0a406e616d6549220e4461726b6e6573"
     "7331063a0645543a0b4070697463686901963a0c40766f6c756d6569696f3b0a073b"
     "0b69253b0c5b006f3b0a073b0b69123b0c5b006f3b0a073b0b69243b0c5b006f3b0a"
     "073b0b69003b0c5b00"),
    # 212 [0, 113, false]
    ({"animation": {"target": "this", "animation": 113, "wait": False}},
     212, "04085b086900697646"),
    # 212 [7, 118, false]
    ({"animation": {"target": 7, "animation": 118, "wait": False}}, 212,
     "04085b08690c697b46"),
    # 213 [0, 5, true]
    ({"balloon": {"target": "this", "balloon": 5}}, 213,
     "04085b086900690a54"),
    # 223 [Tone(0, 0, 0, 0), 300, false]
    ({"tint_screen": {"tone": [0, 0, 0, 0], "duration": 300,
                      "wait": False}}, 223,
     "04085b08753a09546f6e65250000000000000000000000000000000000000000000000"
     "00000000000000000069022c0146"),
    # 223 [Tone(34, -34, -68, 170), 1, true]
    ({"tint_screen": {"tone": [34, -34, -68, 170], "duration": 1}}, 223,
     "04085b08753a09546f6e6525000000000000414000000000000041c000000000000051"
     "c00000000000406540690654"),
    # 224 [Color(255, 255, 255, 255), 180, false]
    ({"flash_screen": {"color": [255, 255, 255, 255], "duration": 180}},
     224,
     "04085b08753a0a436f6c6f72250000000000e06f400000000000e06f400000000000e0"
     "6f400000000000e06f406901b446"),
    # 224 [Color(255, 255, 255, 170), 240, false]
    ({"flash_screen": {"color": [255, 255, 255, 170], "duration": 240,
                       "wait": False}}, 224,
     "04085b08753a0a436f6c6f72250000000000e06f400000000000e06f400000000000e0"
     "6f4000000000004065406901f046"),
    # 225 [2, 9, 60, false]
    ({"shake_screen": {"power": 2, "speed": 9, "duration": 60}}, 225,
     "04085b096907690e694146"),
    # 231 [20, "window_01", 0, 0, 0, 300, 100, 100, 0, 0]
    ({"show_picture": {"number": 20, "name": "window_01", "x": 0,
                       "y": 300, "opacity": 0}}, 231,
     "04085b0f691949220e77696e646f775f3031063a06455469006900690069022c0169"
     "69696969006900"),
    # 232 [20, nil, 0, 0, 0, 300, 100, 100, 0, 0, 25, true]
    ({"move_picture": {"number": 20, "x": 0, "y": 300, "opacity": 0,
                       "duration": 25}}, 232,
     "04085b1169193069006900690069022c016969696969006900691e54"),
    # 232 [20, nil, 0, 0, 0, 0, 100, 100, 255, 0, 25, true]
    ({"move_picture": {"number": 20, "x": 0, "y": 0, "duration": 25,
                       "wait": True}}, 232,
     "04085b116919306900690069006900696969696901ff6900691e54"),
    # 235 [20]
    ({"erase_picture": 20}, 235, "04085b066919"),
    # 241 [BGM("Dungeon2", volume 100, pitch 100)]
    ({"bgm": {"name": "Dungeon2"}}, 241,
     "04085b066f3a0d5250473a3a42474d083a0a406e616d6549220d44756e67656f6e3206"
     "3a0645543a0c40766f6c756d6569693a0b4070697463686969"),
    # 241 [BGM("", volume 100, pitch 100)] -- the editor's "(None)"
    ({"bgm": {"name": ""}}, 241,
     "04085b066f3a0d5250473a3a42474d083a0a406e616d65492200063a0645543a0c4076"
     "6f6c756d6569693a0b4070697463686969"),
    # 245 [BGS("Sea", volume 70, pitch 100)]
    ({"bgs": {"name": "Sea", "volume": 70}}, 245,
     "04085b066f3a0d5250473a3a424753083a0a406e616d65492208536561063a064554"
     "3a0c40766f6c756d65694b3a0b4070697463686969"),
    # 250 [SE("Earth4", pitch 50, volume 100)]
    ({"se": {"name": "Earth4", "pitch": 50}}, 250,
     "04085b066f3a0c5250473a3a5345083a0a406e616d6549220b456172746834063a06"
     "45543a0b40706974636869373a0c40766f6c756d656969"),
    # 250 [SE("Open1", pitch 100, volume 80)]
    ({"se": {"name": "Open1", "volume": 80}}, 250,
     "04085b066f3a0c5250473a3a5345083a0a406e616d6549220a4f70656e31063a0645"
     "543a0b40706974636869693a0c40766f6c756d656955"),
    # 301 [2, 0, true, false]
    ({"battle": {"random": True, "can_escape": True}, "win": []}, 301,
     "04085b09690769005446"),
    # 301 [0, 37, false, true]
    ({"battle": {"troop": 37, "can_lose": True}}, 301,
     "04085b096900692a4654"),
    # 302 [0, 1, 0, 0, false]
    ({"shop": {"goods": [{"item": 1}]}}, 302, "04085b0a690069066900690046"),
    # 302 [2, 7, 0, 0, false]
    ({"shop": {"goods": [{"armor": 7}]}}, 302,
     "04085b0a6907690c6900690046"),
]


class TestFormsMatchEditorData(unittest.TestCase):
    """Each form, compiled for VX Ace, against a real command's bytes."""

    def test_every_form_reproduces_a_real_command(self):
        for entry, code, expected in REAL_ACE_COMMANDS:
            with self.subTest(entry=entry):
                out = compiled(entry)
                self.assertEqual(out[0].get("code"), code)
                self.assertEqual(params_hex(out[0]), expected)

    def test_every_new_form_has_a_real_example_or_is_named_here(self):
        """So a form cannot be added without being held to real data.

        Four are not in the table, each for a reason: the two screen fades
        carry no parameters at all, Name Input Processing does not occur in
        any editor-written file available, and the only real Play ME stores
        its name without an encoding. Each has a test of its own below.
        """
        from rpgvxace_mcp.commands import AUTHORED_LAYOUT_FORMS
        held = {next(iter(set(entry) & AUTHORED_LAYOUT_FORMS))
                for entry, _, _ in REAL_ACE_COMMANDS}
        unheld = {"fadeout_screen", "fadein_screen", "name_input", "me"}
        self.assertEqual(sorted(AUTHORED_LAYOUT_FORMS - held), sorted(unheld))

    def test_shop_goods_after_the_first_are_605_lines(self):
        # 302 [0, 1, 0, 0, false] then 605 [0, 6, 0, 0], 605 [0, 17, 0, 0]
        out = compiled({"shop": {"goods": [{"item": 1}, {"item": 6},
                                           {"item": 17}]}})
        self.assertEqual([c.get("code") for c in out], [302, 605, 605])
        self.assertEqual(params_hex(out[1]), "04085b096900690b69006900")
        self.assertEqual(params_hex(out[2]), "04085b096900691669006900")

    def test_a_shop_price_is_the_specified_price_kind(self):
        # Window_ShopBuy: `goods[2] == 0 ? item.price : goods[3]`.
        out = compiled({"shop": {"goods": [{"weapon": 3, "price": 500},
                                           {"armor": 2}],
                                 "purchase_only": True}})
        self.assertEqual(out[0].get("parameters"), [1, 3, 1, 500, True])
        self.assertEqual(out[1].get("parameters"), [2, 2, 0, 0])

    def test_change_armor_writes_the_five_parameter_layout(self):
        # Change Weapons' real five-parameter layout, which Ace's
        # interpreter reads for both: `gain_item(..., value, @params[4])`.
        out = compiled({"armor": {"id": 31, "op": "sub",
                                  "from_variable": 4,
                                  "include_equipped": True}})
        self.assertEqual(out[0].get("code"), 128)
        self.assertEqual(out[0].get("parameters"), [31, 1, 1, 4, True])

    def test_me_has_the_ivar_order_the_editor_writes(self):
        # ME and SE put @pitch before @volume; BGM and BGS the other way.
        # The real ME carries a name the editor wrote without an encoding,
        # so it is compared by class and order rather than by bytes.
        me = compiled({"me": {"name": "Inn"}})[0].get("parameters")[0]
        self.assertEqual(me.cls, "RPG::ME")
        self.assertEqual(list(me.ivars), ["@name", "@pitch", "@volume"])
        bgm = compiled({"bgm": {"name": "Town1"}})[0].get("parameters")[0]
        self.assertEqual(list(bgm.ivars), ["@name", "@volume", "@pitch"])

    def test_screen_fades_carry_no_parameters(self):
        self.assertEqual([(c.get("code"), c.get("parameters"))
                          for c in compiled({"fadeout_screen": True})
                          + compiled({"fadein_screen": True})],
                         [(221, []), (222, [])])

    def test_name_input_is_actor_then_length(self):
        # Ace: `SceneManager.scene.prepare(@params[0], @params[1])`.
        out = compiled({"name_input": {"actor": 1, "max_characters": 8}})
        self.assertEqual((out[0].get("code"), out[0].get("parameters")),
                         (303, [1, 8]))


class TestMoveRouteLines(unittest.TestCase):
    """The 505 lines beside a route are the route's own step objects."""

    def test_each_step_has_a_505_line_sharing_its_object(self):
        out = compiled({"move_route": {"target": 3,
                                       "steps": ["move_up", {"jump": [0, -2]},
                                                 "turn_left"]}},
                       codec=ACE)
        self.assertEqual([c.get("code") for c in out], [205, 505, 505, 505])
        route = out[0].get("parameters")[1]
        steps = route.get("list")
        self.assertEqual([m.get("code") for m in steps], [4, 14, 17, 0])
        # Shared, not copied: all 1,037 step lines in the real projects
        # are Marshal links to the route's own commands.
        for line, step in zip(out[1:], steps):
            self.assertIs(line.get("parameters")[0], step)
            self.assertEqual(line.get("indent"), out[0].get("indent"))

    def test_the_route_flags_default_to_the_common_case(self):
        route = compiled({"move_route": {"target": "player",
                                         "steps": []}})[0].get("parameters")[1]
        self.assertEqual((route.get("repeat"), route.get("skippable"),
                          route.get("wait")), (False, False, True))
        self.assertEqual([m.get("code") for m in route.get("list")], [0])

    def test_every_move_code_has_exactly_one_step_name(self):
        named = sorted(MOVE_STEP_CODES.values())
        self.assertEqual(named, sorted(set(MOVE_COMMAND_NAMES) - {0}))

    def test_39_and_40_are_transparency_in_both_engines(self):
        # Game_Character in both: `when 39 ... @transparent = true`.
        self.assertEqual(MOVE_COMMAND_NAMES[39], "Transparent ON")
        self.assertEqual(MOVE_COMMAND_NAMES[40], "Transparent OFF")
        self.assertEqual(MOVE_STEP_CODES["transparent_on"], 39)

    def test_a_step_with_arguments_builds_them(self):
        route = compiled({"move_route": {"target": 0, "steps": [
            {"graphic": {"name": "Actor3", "index": 0}},
            {"speed": 5}, {"frequency": 2}, {"switch_on": 57},
            {"switch_off": 3}, {"blending": "add"},
            {"script": "p 1"},
        ]}})[0].get("parameters")[1]
        built = [(m.get("code"), m.get("parameters")) for m in route.get("list")]
        self.assertEqual(built[0][0], 41)
        self.assertEqual(built[0][1][0].data, b"Actor3")
        self.assertEqual(built[0][1][1], 0)
        self.assertEqual(built[1:6], [(29, [5]), (30, [2]), (27, [57]),
                                      (28, [3]), (43, [1])])
        self.assertEqual(built[6][0], 45)
        self.assertIsInstance(built[6][1][0], RubyString)


class TestBattleBranches(unittest.TestCase):
    """601..604 in the editor's arrangement, blank line closing each body."""

    def codes(self, out):
        return [(c.get("code"), c.get("indent")) for c in out]

    def test_escape_branch_matches_the_editor(self):
        # 155 of 162 real battles: '601 blank 602 blank 604'.
        out = compile_commands([{"battle": {"troop": 1, "can_escape": True},
                                 "win": [{"switch": 1, "value": "on"}],
                                 "escape": []}], ACE, indent=1,
                               engine="vx_ace")
        self.assertEqual(self.codes(out), [
            (301, 1), (601, 1), (121, 2), (0, 2), (602, 1), (0, 2), (604, 1)])

    def test_lose_branch_without_escape(self):
        out = compiled({"battle": {"troop": 1, "can_lose": True},
                        "lose": [{"gold": {"op": "sub", "value": 10}}]})
        self.assertEqual(self.codes(out), [
            (301, 0), (601, 0), (0, 1), (603, 0), (125, 1), (0, 1), (604, 0)])

    def test_no_branches_when_neither_can_happen(self):
        self.assertEqual(self.codes(compiled({"battle": {"troop": 4}})),
                         [(301, 0)])

    def test_a_branch_for_an_outcome_that_cannot_happen_is_refused(self):
        for entry in ({"battle": {"troop": 1}, "win": []},
                      {"battle": {"troop": 1, "can_lose": True},
                       "escape": []},
                      {"battle": {"troop": 1, "can_escape": True},
                       "lose": []}):
            with self.subTest(entry=entry):
                with self.assertRaises(CommandError):
                    compiled(entry)

    def test_troop_from_a_variable(self):
        out = compiled({"battle": {"from_variable": 12}})
        self.assertEqual(out[0].get("parameters"), [1, 12, False, False])


class TestVXLayouts(unittest.TestCase):
    """Where VX's interpreter reads a different layout from VX Ace's."""

    def test_vx_shop_goods_carry_no_price(self):
        # VX command_302: `shop_purchase_only = @params[2]`, and the goods
        # are `[@params]` plus each 605's parameters.
        out = compiled({"shop": {"goods": [{"item": 1}, {"weapon": 2}],
                                 "purchase_only": True}}, engine="vx")
        self.assertEqual([c.get("parameters") for c in out],
                         [[0, 1, True], [1, 2]])

    def test_vx_shop_spec_is_what_vx_reads(self):
        # What command_reference serves for a VX project.
        self.assertEqual(param_specs("vx")[302],
                         ["goods_type", "goods_id", "purchase_only"])
        self.assertEqual(param_specs("vx")[605], ["goods_type", "goods_id"])

    def test_vx_refuses_a_shop_price(self):
        with self.assertRaises(CommandError) as caught:
            compiled({"shop": {"goods": [{"item": 1, "price": 5}]}},
                     engine="vx")
        self.assertIn("price", str(caught.exception))

    def test_vx_animation_and_balloon_have_no_wait_flag(self):
        # VX command_212/213 read @params[0] and [1] only.
        self.assertEqual(compiled({"animation": {"target": "player",
                                                 "animation": 4}},
                                  engine="vx")[0].get("parameters"), [-1, 4])
        self.assertEqual(compiled({"balloon": {"target": 2, "balloon": 1}},
                                  engine="vx")[0].get("parameters"), [2, 1])
        with self.assertRaises(CommandError):
            compiled({"balloon": {"target": 2, "balloon": 1, "wait": True}},
                     engine="vx")

    def test_vx_has_no_random_encounter_battle(self):
        with self.assertRaises(CommandError):
            compiled({"battle": {"random": True}}, engine="vx")

    def test_vx_pictures_stop_at_twenty(self):
        out = compiled({"show_picture": {"number": 20, "name": "a"}},
                       engine="vx")
        params = out[0].get("parameters")
        self.assertEqual(params[0], 20)
        self.assertEqual(params[1].data, b"a")
        self.assertEqual(params[2:], [0, 0, 0, 0, 100, 100, 255, 0])
        # Bare bytes on VX: Ruby 1.8 strings carry no encoding.
        self.assertFalse(out[0].get("parameters")[1].ivars)
        with self.assertRaises(CommandError):
            compiled({"show_picture": {"number": 21, "name": "a"}},
                     engine="vx")
        compiled({"show_picture": {"number": 100, "name": "a"}})

    def test_forms_unconfirmed_on_vx_are_refused_and_say_so(self):
        # The four audio forms and the route's se step were here until the
        # RGSS2 class question was settled: see the test below.
        for entry in ({"move_picture": {"number": 1}},):
            with self.subTest(entry=entry):
                with self.assertRaises(CommandError) as caught:
                    compiled(entry, engine="vx")
                # Refused *because* this is VX, and told what to do instead:
                # an "unrecognised entry" refusal names raw as well.
                self.assertIn("RPG Maker VX", str(caught.exception))
                self.assertIn("raw", str(caught.exception))
                compiled(entry)

    def test_vx_audio_forms_write_the_bgm_family(self):
        # RGSS2's command_241/245/249/250 are each `@params[0].play`, and
        # Game_Character's route step 44 is `command.parameters[0].play`:
        # only RPG::BGM, BGS, ME and SE have `play`, so that is what the
        # editor stores. Strings are bare, as everywhere on VX.
        for kind, code in (("bgm", 241), ("bgs", 245), ("me", 249),
                           ("se", 250)):
            with self.subTest(kind=kind):
                out = compiled({kind: {"name": "Town1", "volume": 90}},
                               engine="vx")
                self.assertEqual(out[0].get("code"), code)
                audio = out[0].get("parameters")[0]
                self.assertEqual(audio.cls, "RPG::" + kind.upper())
                self.assertEqual(audio.get("name").data, b"Town1")
                self.assertEqual(audio.get("name").ivars, {})
                self.assertEqual((audio.get("volume"), audio.get("pitch")),
                                 (90, 100))
        route = compiled({"move_route": {"target": 0, "steps": [
            {"se": {"name": "Jump1"}}]}}, engine="vx")[0]
        step = route.get("parameters")[1].get("list")[0]
        self.assertEqual(step.get("code"), 44)
        self.assertEqual(step.get("parameters")[0].cls, "RPG::SE")

    def test_vx_forms_that_share_the_ace_layout(self):
        cases = [
            ({"gold": {"op": "sub", "value": 5}}, [1, 0, 5]),
            ({"item": {"id": 2, "op": "add", "from_variable": 9}},
             [2, 0, 1, 9]),
            ({"weapon": {"id": 2, "op": "add", "value": 1}},
             [2, 0, 0, 1, False]),
            ({"party": {"actor": 3, "op": "remove"}}, [3, 1, 0]),
            ({"event_location": {"event": "this", "x": 4, "y": 5,
                                 "direction": 8}}, [0, 0, 4, 5, 8]),
            ({"shake_screen": {"power": 9, "speed": 1, "duration": 5,
                               "wait": True}}, [9, 1, 5, True]),
            ({"erase_picture": 3}, [3]),
            ({"name_input": {"actor": 2, "max_characters": 6}}, [2, 6]),
            ({"battle": {"troop": 2}}, [0, 2, False, False]),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(compiled(entry, engine="vx")[0]
                                 .get("parameters"), expected)
        tone = compiled({"tint_screen": {"tone": [1, 2, 3, 4]}},
                        engine="vx")[0].get("parameters")
        self.assertEqual(tone, [Tone([1, 2, 3, 4]), 60, True])
        color = compiled({"flash_screen": {"color": [1, 2, 3, 4]}},
                         engine="vx")[0].get("parameters")
        self.assertEqual(color, [Color([1, 2, 3, 4]), 8, False])

    def test_a_vx_route_is_built_like_an_ace_one(self):
        out = compiled({"move_route": {"target": 1, "steps": ["move_down"]}},
                       engine="vx")
        self.assertEqual([c.get("code") for c in out], [205, 505])

    def test_an_unmodelled_engine_gets_no_layout_form(self):
        # An engine this server has no table for. XP used to stand for it
        # here; XP is a profile now and is refused earlier, by its own
        # message, so this never reached the branch it is named for.
        with self.assertRaises(CommandError) as caught:
            compiled({"gold": {"op": "add", "value": 1}}, engine="mv",
                     codec=ACE)
        self.assertIn("does not model", str(caught.exception))
        self.assertIn("raw", str(caught.exception))
        compiled({"gold": {"op": "add", "value": 1}})
        # The forms that predate this table are unchanged.
        compiled({"switch": 1}, engine="mv", codec=ACE)


# The codes XP's `Interpreter (part 2)` dispatches in `execute_command`,
# copied from its `when` lines, and the continuation and branch-marker codes
# that only appear in data (509 follows every one of 79 Set Move Route
# commands in a copy of an XP game's maps).
XP_DISPATCHED = [
    101, 102, 402, 403, 103, 104, 105, 106, 111, 411, 112, 413, 113, 115,
    116, 117, 118, 119, 121, 122, 123, 124, 125, 126, 127, 128, 129, 131,
    132, 133, 134, 135, 136, 201, 202, 203, 204, 205, 206, 207, 208, 209,
    210, 221, 222, 223, 224, 225, 231, 232, 233, 234, 235, 236, 241, 242,
    245, 246, 247, 248, 249, 250, 251, 301, 601, 602, 603, 302, 303, 311,
    312, 313, 314, 315, 316, 317, 318, 319, 320, 321, 322, 331, 332, 333,
    334, 335, 336, 337, 338, 339, 340, 351, 352, 353, 354, 355]
XP_DATA_ONLY = [0, 108, 401, 404, 408, 412, 509, 604, 605, 655]


class TestXPCommands(unittest.TestCase):
    """XP's command table, and the forms that compile for it.

    XP numbers its commands differently from VX: Wait is 106 (VX 230), Set
    Move Route 209 with 509 step lines (VX 205 and 505), Set Event Location
    202 (VX 203), and 104 and 105 are Change Text Options and Button Input
    Processing rather than VX Ace's Select Item and Scrolling Text.
    """

    def test_the_table_is_xp_s_interpreter(self):
        table = command_table("xp")
        self.assertEqual(sorted(table), sorted(XP_DISPATCHED + XP_DATA_ONLY))
        self.assertEqual(table[106], "Wait")
        self.assertEqual(table[209], "Set Move Route")
        self.assertEqual(table[205], "Change Fog Color Tone")
        self.assertEqual(table[202], "Set Event Location")
        self.assertEqual(table[104], "Change Text Options")
        self.assertEqual(table[509], "Move Command")
        for code in (230, 505, 213, 214, 261, 285, 405):
            self.assertNotIn(code, table)

    def test_the_specs_are_xp_s(self):
        specs = param_specs("xp")
        self.assertEqual(specs[101], ["text"])
        self.assertEqual(specs[106], ["frames"])
        self.assertEqual(specs[209], ["character_id", "move_route"])
        self.assertEqual(specs[301], ["troop_id", "can_escape", "can_lose"])
        self.assertEqual(specs[127], ["weapon_id", "operation",
                                      "operand_type", "value"])
        # XP's 205 is a fog tone, not VX's move route.
        self.assertEqual(specs[205], ["tone", "duration"])

    def test_text_is_written_xp_s_way(self):
        # XP's command_101 shows `parameters[0]` as the first line and
        # appends each following 401: no face, no window settings. Every
        # one of 300 Show Text commands in a copy of an XP game has one
        # parameter.
        out = compiled({"text": "one\ntwo\nthree\nfour\nfive"}, engine="xp",
                       codec=VX)
        self.assertEqual([(c.get("code"), [p.data for p in
                                           c.get("parameters")])
                          for c in out],
                         [(101, [b"one"]), (401, [b"two"]), (401, [b"three"]),
                          (401, [b"four"]), (101, [b"five"])])
        with self.assertRaises(CommandError) as caught:
            compiled({"text": "x", "face_name": "Actor1"}, engine="xp",
                     codec=VX)
        self.assertIn("RPG Maker XP", str(caught.exception))

    def test_the_shared_forms_compile_alike(self):
        for entry in ({"switch": 3}, {"self_switch": "B", "value": "off"},
                      {"variable": 2, "op": "add", "random": [1, 6]},
                      {"common_event": 4}, {"label": "top"}, {"jump": "top"},
                      {"exit": True}, {"script": "p 1\np 2"},
                      {"comment": "a\nb"},
                      {"transfer": {"map_id": 2, "x": 3, "y": 4}},
                      {"if": {"switch": 1}, "then": [{"switch": 2}]},
                      {"loop": [{"break_loop": True}]},
                      {"choices": ["a", "b"], "branches": [[], []]}):
            with self.subTest(entry=entry):
                self.assertEqual(
                    [marshal_rb.dump(c) for c in
                     compiled(entry, engine="xp", codec=VX)],
                    [marshal_rb.dump(c) for c in
                     compiled(entry, engine="vx", codec=VX)])

    def test_the_xp_tables_name_real_forms(self):
        from rpgvxace_mcp.commands import (
            SUPPORTED_KEYS,
            XP_FORMS,
            XP_REFUSED_FORMS,
        )
        self.assertLessEqual(XP_FORMS, SUPPORTED_KEYS)
        self.assertLessEqual(set(XP_REFUSED_FORMS), SUPPORTED_KEYS)
        self.assertEqual(XP_FORMS & set(XP_REFUSED_FORMS), set())

    def test_xp_s_control_variables_stops_where_its_operand_does(self):
        # In a copy of an XP project's data, every Control Variables with a
        # constant or a variable operand has five parameters, and those
        # with a random range six: XP's command_122 reads parameters[5]
        # only for a random range (and actor, enemy and character data).
        self.assertEqual(compiled({"variable": 1, "value": 20}, engine="xp",
                                  codec=VX)[0].get("parameters"),
                         [1, 1, 0, 0, 20])
        self.assertEqual(compiled({"variable": 2, "from_variable": 7},
                                  engine="xp", codec=VX)[0].get("parameters"),
                         [2, 2, 0, 1, 7])
        self.assertEqual(compiled({"variable": 8, "random": [-40, 40]},
                                  engine="xp", codec=VX)[0].get("parameters"),
                         [8, 8, 0, 2, -40, 40])

    def test_the_other_forms_are_refused_and_say_why(self):
        for entry in ({"wait": 10}, {"gold": {"op": "add", "value": 1}},
                      {"bgm": {"name": "x"}},
                      {"move_route": {"target": 0, "steps": ["move_up"]}}):
            with self.subTest(entry=entry):
                with self.assertRaises(CommandError) as caught:
                    compiled(entry, engine="xp", codec=VX)
                self.assertIn("RPG Maker XP", str(caught.exception))
                self.assertIn("raw", str(caught.exception))

    def test_xp_commands_render_with_xp_s_names(self):
        def line(code, params):
            return summarize(RubyObject("RPG::EventCommand", {
                "@indent": 0, "@code": code, "@parameters": params}), VX,
                engine="xp")
        self.assertEqual(line(106, [10]), "Wait: 10 frames")
        self.assertIn("Change Fog Color Tone", line(205, [Tone([0, 0, 0, 0]),
                                                          20]))
        self.assertEqual(line(101, [RubyString(b"Hello")]), "Show Text: Hello")
        self.assertIn("troop 3", line(301, [3, True, False]))
        self.assertIn("player", line(209, [-1, RubyObject("RPG::MoveRoute")]))
        self.assertIn("Always on Top ON", line(509, [RubyObject(
            "RPG::MoveCommand", {"@code": 39, "@parameters": []})]))
        # VX Ace's reading of the same command is unchanged.
        self.assertIn("Set Move Route", summarize(RubyObject(
            "RPG::EventCommand", {"@indent": 0, "@code": 205,
                                  "@parameters": [0, None]}), ACE))


class TestTheSpecsSayWhatTheFormsWrite(unittest.TestCase):
    """`command_reference` serves these layouts, so they must agree."""

    def test_spec_lengths_match_compiled_lengths(self):
        cases = [({"shop": {"goods": [{"item": 1}, {"item": 2}]}}, (302, 605)),
                 ({"animation": {"target": 0, "animation": 1}}, (212,)),
                 ({"balloon": {"target": 0, "balloon": 1}}, (213,)),
                 ({"show_picture": {"number": 1, "name": "a"}}, (231,)),
                 ({"tint_screen": {"tone": [0, 0, 0, 0]}}, (223,)),
                 ({"flash_screen": {"color": [0, 0, 0, 0]}}, (224,)),
                 ({"shake_screen": {}}, (225,)),
                 ({"erase_picture": 1}, (235,))]
        for engine in ("vx_ace", "vx"):
            specs = param_specs(engine)
            for entry, codes in cases:
                with self.subTest(engine=engine, entry=entry):
                    out = compiled(entry, engine=engine)
                    self.assertEqual(len(out), len(codes))
                    for command, code in zip(out, codes):
                        self.assertEqual(command.get("code"), code)
                        self.assertEqual(len(specs[code]),
                                         len(command.get("parameters")))

    def test_move_picture_is_specified_for_ace_only(self):
        self.assertEqual(len(param_specs("vx_ace")[232]), 12)
        self.assertNotIn(232, param_specs("vx"))
        self.assertIn(232, command_table("vx"))


class TestACallersWrongInputIsARefusalNotACrash(unittest.TestCase):
    """Every shape a caller can get wrong ends in a `CommandError`.

    A bare `TypeError` or `ValueError` reaches `server.py` as "an argument was
    not the shape the tool expected", which names neither the argument nor the
    shape, and a value that was coerced instead was written and reported as a
    success.
    """

    BAD = [
        ("an unhashable variable op", {"variable": 1, "op": ["add"]}),
        ("a reversed variable range", {"variable": 5, "variable_end": 2}),
        ("a reversed switch range", {"switch": 5, "switch_end": 2}),
        ("a self switch that is not A to D", {"self_switch": "Z"}),
        ("a self switch that is not text", {"self_switch": ["A"]}),
        ("a switch state of 7", {"switch": 1, "value": 7}),
        ("a transfer with no map", {"transfer": {"x": 1, "y": 1}}),
        ("a transfer with no x", {"transfer": {"map_id": 1, "y": 1}}),
        ("a raw code that is not a number", {"raw": {"code": "x"}}),
        ("a raw code below zero", {"raw": {"code": -1}}),
        ("a raw object with a list for a class",
         {"raw": {"code": 108, "parameters": [{"__class__": ["x"]}]}}),
        ("a move route step that is not an object",
         {"raw": {"code": 205, "parameters": [0, {
             "__class__": "RPG::MoveRoute", "list": ["up"]}]}}),
        ("a move route whose repeat is a string",
         {"raw": {"code": 205, "parameters": [0, {
             "__class__": "RPG::MoveRoute", "repeat": "false"}]}}),
    ]

    def test_each_is_a_command_error(self):
        for what, entry in self.BAD:
            with self.subTest(what=what), self.assertRaises(CommandError):
                compiled(entry)

    def test_nesting_a_raw_parameter_deeply_is_refused(self):
        value = 1
        for _ in range(300):
            value = [value]
        with self.assertRaises(CommandError):
            compiled({"raw": {"code": 108, "parameters": [value]}})

    def test_an_empty_else_is_still_written(self):
        codes = [c.get("code") for c in compiled(
            {"if": {"switch": 1}, "then": [], "else": []})]
        self.assertIn(411, codes)

    def test_a_message_quotes_a_huge_value_briefly(self):
        with self.assertRaises(CommandError) as caught:
            compiled({"switch": "x" * 100000})
        self.assertLess(len(str(caught.exception)), 2000)

    def test_ordinary_forms_still_compile(self):
        for entry in (
            {"variable": 1, "op": "add", "value": 3},
            {"variable": 2, "variable_end": 4},
            {"switch": 3, "switch_end": 3},
            {"self_switch": "D", "value": "off"},
            {"switch": 1, "value": 1},
            {"transfer": {"map_id": 2, "x": 1, "y": 1}},
            {"choices": ["a", "b"], "cancel": 2},
            {"raw": {"code": 108, "parameters": ["note"]}},
        ):
            with self.subTest(entry=entry):
                self.assertTrue(compiled(entry))


class TestRefusals(unittest.TestCase):
    """Bounded arguments, and errors that name the argument."""

    def refused(self, entry, words, engine="vx_ace"):
        with self.assertRaises(CommandError) as caught:
            compiled(entry, engine=engine)
        message = str(caught.exception)
        for word in words:
            self.assertIn(word, message)
        return message

    def test_out_of_range_numbers(self):
        cases = [
            ({"balloon": {"target": 0, "balloon": 11}}, ["balloon", "10"]),
            ({"show_picture": {"number": 0, "name": "a"}}, ["number"]),
            ({"show_picture": {"number": 101, "name": "a"}}, ["100"]),
            ({"show_picture": {"number": 1, "name": "a", "opacity": 256}},
             ["opacity", "255"]),
            ({"tint_screen": {"tone": [256, 0, 0, 0]}}, ["tone"]),
            ({"tint_screen": {"tone": [0, 0, 0, -1]}}, ["gray"]),
            ({"flash_screen": {"color": [0, 0, 0, 300]}}, ["color"]),
            ({"shake_screen": {"power": 10}}, ["power", "9"]),
            ({"shake_screen": {"speed": 0}}, ["speed"]),
            ({"tint_screen": {"tone": [0, 0, 0, 0], "duration": 0}},
             ["duration"]),
            ({"gold": {"op": "add", "value": -5}}, ["value"]),
            ({"name_input": {"actor": 1, "max_characters": 17}},
             ["max_characters", "16"]),
            ({"move_route": {"target": 0, "steps": [{"speed": 7}]}},
             ["speed", "6"]),
            ({"move_route": {"target": 0, "steps": [{"jump": [0, 101]}]}},
             ["jump"]),
            ({"move_route": {"target": 0,
                             "steps": ["move_up"] * 1001}}, ["steps"]),
            ({"shop": {"goods": []}}, ["goods"]),
            ({"bgm": {"name": "a", "volume": 101}}, ["volume", "100"]),
            ({"se": {"name": "a", "pitch": 49}}, ["pitch", "50"]),
            ({"event_location": {"event": 1, "x": 0, "y": 0,
                                 "direction": 3}}, ["direction"]),
            ({"animation": {"target": -2, "animation": 1}}, ["target"]),
        ]
        for entry, words in cases:
            with self.subTest(entry=entry):
                self.refused(entry, words)

    def test_wrong_types(self):
        cases = [
            ({"gold": {"op": "add", "value": 2.5}}, ["value"]),
            ({"gold": {"op": "add", "value": True}}, ["value"]),
            ({"gold": {"op": "times", "value": 2}}, ["op", "add", "sub"]),
            ({"item": {"id": "6", "op": "add", "value": 1}}, ["id"]),
            ({"party": {"actor": 1, "op": "add", "initialize": 1}},
             ["initialize"]),
            ({"tint_screen": {"tone": [0, 0, 0]}}, ["tone", "four"]),
            ({"tint_screen": {"tone": "dark"}}, ["tone"]),
            ({"show_picture": {"number": 1, "name": 5}}, ["name"]),
            ({"show_picture": {"number": 1, "name": "a",
                               "origin": "middle"}}, ["origin", "center"]),
            ({"move_route": {"target": 0, "steps": ["fly"]}},
             ["fly", "move_down"]),
            ({"move_route": {"target": 0, "steps": [{"move_down": True}]}},
             ["move_down"]),
            ({"move_route": {"target": 0, "steps": [{"wait": 1, "jump": 1}]}},
             ["one"]),
            ({"move_route": {"target": 0, "steps": "move_down"}}, ["steps"]),
            ({"shop": {"goods": [{"item": 1, "weapon": 2}]}}, ["one of"]),
            ({"erase_picture": "1"}, ["erase_picture"]),
            ({"fadeout_screen": False}, ["fadeout_screen", "true"]),
            ({"battle": {"troop": 1, "random": True}}, ["one of"]),
            ({"battle": 3}, ["battle", "object"]),
        ]
        for entry, words in cases:
            with self.subTest(entry=entry):
                self.refused(entry, words)

    def test_an_unknown_field_inside_a_form_is_refused_not_ignored(self):
        message = self.refused({"bgm": {"naem": "Town1"}}, ["naem", "name"])
        self.assertIn("volume", message)

    def test_include_equipped_only_when_removing(self):
        self.refused({"weapon": {"id": 1, "op": "add", "value": 1,
                                 "include_equipped": True}},
                     ["include_equipped"])

    def test_initialize_only_when_adding(self):
        self.refused({"party": {"actor": 1, "op": "remove",
                                "initialize": True}}, ["initialize"])


class TestRendering(unittest.TestCase):
    """What event_get shows for each form says what the form was given."""

    def lines(self, entry, engine="vx_ace"):
        codec = ACE if engine == "vx_ace" else VX
        return [summarize(c, codec) for c in compiled(entry, engine=engine)]

    def test_each_form_reads_back(self):
        cases = [
            ({"gold": {"op": "add", "value": 50}}, ["Change Gold", "+ 50"]),
            ({"gold": {"op": "sub", "from_variable": 4}},
             ["Change Gold", "- variable 4"]),
            ({"item": {"id": 6, "op": "add", "value": 5}},
             ["Change Items", "item 6", "+ 5"]),
            ({"weapon": {"id": 2, "op": "sub", "value": 1,
                         "include_equipped": True}},
             ["weapon 2", "- 1", "including equipped"]),
            ({"armor": {"id": 3, "op": "add", "value": 1}}, ["armor 3"]),
            ({"party": {"actor": 4, "op": "add", "initialize": True}},
             ["add actor 4", "initialized"]),
            ({"party": {"actor": 4, "op": "remove"}}, ["remove actor 4"]),
            ({"event_location": {"event": 8, "x": 3, "y": 49}},
             ["event 8", "(3, 49)"]),
            ({"event_location": {"event": "this", "x": 3, "y": 4,
                                 "by_variables": True}},
             ["this event", "variable 3", "variable 4"]),
            ({"animation": {"target": "player", "animation": 7}},
             ["player", "animation 7", "wait"]),
            ({"balloon": {"target": 3, "balloon": 1, "wait": False}},
             ["event 3", "balloon 1"]),
            ({"tint_screen": {"tone": [34, -34, -68, 170], "duration": 30}},
             ["(34, -34, -68, 170)", "30 frames"]),
            ({"flash_screen": {"color": [255, 0, 0, 128]}},
             ["(255, 0, 0, 128)", "8 frames"]),
            ({"shake_screen": {"power": 3, "speed": 4, "duration": 20}},
             ["power 3", "speed 4", "20 frames"]),
            ({"show_picture": {"number": 2, "name": "logo", "x": 10,
                               "y": 20}},
             ["Show Picture", "2", "logo", "(10, 20)"]),
            ({"move_picture": {"number": 2, "x": 5, "y": 6,
                               "duration": 40}},
             ["Move Picture", "2", "(5, 6)", "40 frames"]),
            ({"erase_picture": 2}, ["Erase Picture", "2"]),
            ({"name_input": {"actor": 1, "max_characters": 8}},
             ["actor 1", "8"]),
            ({"bgm": {"name": "Town1"}}, ["Play BGM", "Town1"]),
            ({"shop": {"goods": [{"item": 1}, {"weapon": 3, "price": 9}]}},
             ["item 1", "weapon 3", "price 9"]),
            ({"fadeout_screen": True}, ["Fadeout Screen"]),
            ({"battle": {"troop": 5, "can_escape": True}},
             ["troop 5", "If Win", "If Escape", "End Battle"]),
            ({"move_route": {"target": "player",
                             "steps": [{"jump": [1, -2]}, {"wait": 4},
                                       "turn_up"]}},
             ["player", "Jump (1, -2)", "Wait (4)", "Turn Up"]),
        ]
        for entry, words in cases:
            with self.subTest(entry=entry):
                text = "\n".join(self.lines(entry))
                for word in words:
                    self.assertIn(word, text)

    def test_vx_renders_its_shorter_layouts(self):
        text = "\n".join(self.lines({"shop": {"goods": [{"item": 1},
                                                        {"armor": 2}]}},
                                    engine="vx"))
        self.assertIn("item 1", text)
        self.assertIn("armor 2", text)
        self.assertNotIn("price", text)


class TestFormsThroughEventEdit(ToolTest):
    """Written by event_edit, read by event_get, and stable in the file."""

    def entries(self):
        common = [
            {"gold": {"op": "add", "value": 10}},
            {"item": {"id": 1, "op": "add", "value": 2}},
            {"party": {"actor": 1, "op": "add"}},
            {"event_location": {"event": "this", "x": 1, "y": 2}},
            {"move_route": {"target": "player",
                            "steps": ["move_down", {"wait": 3}]}},
            {"animation": {"target": 0, "animation": 1}},
            {"balloon": {"target": -1, "balloon": 2}},
            {"tint_screen": {"tone": [0, 0, 0, 0]}},
            {"flash_screen": {"color": [255, 255, 255, 170]}},
            {"shake_screen": {}},
            {"show_picture": {"number": 1, "name": "Logo"}},
            {"erase_picture": 1},
            {"fadeout_screen": True},
            {"fadein_screen": True},
            {"name_input": {"actor": 1, "max_characters": 8}},
            {"shop": {"goods": [{"item": 1}, {"weapon": 1}]}},
            {"battle": {"troop": 1, "can_escape": True},
             "win": [{"text": "Won."}], "escape": [{"text": "Fled."}]},
        ]
        if self.ace:
            common += [{"move_picture": {"number": 1, "x": 4, "y": 4}},
                       {"bgm": {"name": "Town1"}},
                       {"se": {"name": "Cursor1"}}]
        return common

    def test_the_forms_write_read_and_round_trip(self):
        self.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 9, "y": 9, "commands": self.entries()}])
        raw = self.data_bytes("Map001")
        self.assertEqual(marshal_rb.dump(marshal_rb.load(raw)), raw)
        event = self.call("event_get", map_id=1, name_contains="EV",
                          include_raw=True)["events"][0]
        page = event["pages"][0]
        codes = [c["code"] for c in page["commands"]]
        for code in (125, 126, 129, 203, 205, 505, 212, 213, 223, 224, 225,
                     231, 235, 221, 222, 303, 302, 605, 301, 601, 602, 604):
            self.assertIn(code, codes)
        # The step line was not added twice: the compiler wrote it, and
        # event_edit's own insertion saw it there.
        self.assertEqual(codes.count(505), 2)
        self.assertEqual(codes[-1], 0)
        script = "\n".join(page["script"])
        self.assertIn("Change Gold: + 10", script)
        self.assertIn("Won.", script)

    def test_a_form_this_engine_cannot_take_names_the_engine(self):
        entry = {"balloon": {"target": 0, "balloon": 1, "wait": True}}
        if self.ace:
            self.call("event_edit", map_id=1, ops=[{
                "op": "create", "x": 9, "y": 8, "commands": [entry]}])
            return
        with self.assertRaises(ToolError) as caught:
            self.call("event_edit", map_id=1, ops=[{
                "op": "create", "x": 9, "y": 8, "commands": [entry]}])
        self.assertIn("VX", str(caught.exception))


class TestFormsThroughEventEditVX(TestFormsThroughEventEdit):
    engine = "vx"


class TestTheServedDocsNameTheForms(ToolTest):
    """What the model is told agrees with what the compiler takes."""

    def test_the_reference_lists_this_engines_forms(self):
        from rpgvxace_mcp.docs import command_reference_document
        text = command_reference_document(self.project)
        section = text.split("## Authoring forms", 1)[1]
        self.assertIn("`shop`", section)
        self.assertIn("`move_route`", section)
        # Offered on VX Ace; named as not offered, with the reason, on VX.
        self.assertEqual("`move_picture` is not offered" in section,
                         not self.ace)
        self.assertIn("| 39 | Transparent ON | transparent_on |", text)

    def test_the_tool_serves_the_forms(self):
        authoring = self.call("command_reference")["authoring"]
        for key in ("move_route", "battle", "tint_screen", "name_input"):
            self.assertIn('"%s"' % key, authoring)

    def test_the_workflow_mentions_the_forms(self):
        from rpgvxace_mcp.docs import workflow_document
        text = workflow_document(self.project)
        self.assertIn('"move_route"', text)
        self.assertIn('"battle"', text)


class TestTheServedDocsNameTheFormsVX(TestTheServedDocsNameTheForms):
    engine = "vx"


class TestEveryRealCommandRebuilds(unittest.TestCase):
    """Every supported command in a real project, rebuilt from the DSL.

    Opt-in: set RPGVXACE_TEST_PROJECT to a VX Ace project folder. The
    project is opened read-only. For each command this writes the entry a
    caller would have written, compiles it, and requires the parameters to
    dump to the editor's exact bytes. Commands whose layout the forms
    deliberately do not write are counted and skipped: Change Weapons and
    Change Armor in the four-parameter layout an older editor wrote, and
    any string the editor stored without an encoding.
    """

    TARGETS = {-1: "player", 0: "this"}

    def setUp(self):
        path = os.environ.get("RPGVXACE_TEST_PROJECT")
        if not path:
            self.skipTest("set RPGVXACE_TEST_PROJECT to a project folder")
        self.project = Project(path, read_only=True)
        if self.project.engine != "vx_ace":
            self.skipTest("the real-data comparison is for VX Ace projects")

    def lists(self):
        for map_id in self.project.map_ids_on_disk():
            for _, event in self.project.load_map(map_id).get("events").items():
                for page in event.get("pages"):
                    yield page.get("list")
        for _, common in self.project.entries("common_events"):
            if common is not None:
                yield common.get("list")
        for _, troop in self.project.entries("troops"):
            if troop is not None:
                for page in troop.get("pages"):
                    yield page.get("list")

    def text(self, value):
        return value.data.decode("utf-8")

    def target(self, value):
        return self.TARGETS.get(value, value)

    def audio(self, value):
        return {"name": self.text(value.get("name")),
                "volume": value.get("volume"), "pitch": value.get("pitch")}

    def gain(self, p, base):
        op = "add" if p[base] == 0 else "sub"
        entry = {"op": op}
        if p[base + 1] == 0:
            entry["value"] = p[base + 2]
        else:
            entry["from_variable"] = p[base + 2]
        return entry

    def step(self, move):
        from rpgvxace_mcp.commands import MOVE_STEP_CODES
        names = {code: name for name, code in MOVE_STEP_CODES.items()}
        code, p = move.get("code"), move.get("parameters")
        name = names[code]
        if not p:
            return name
        if code == 14:
            return {name: list(p)}
        if code == 41:
            return {name: {"name": self.text(p[0]), "index": p[1]}}
        if code == 43:
            return {name: ["normal", "add", "subtract"][p[0]]}
        if code == 44:
            return {name: self.audio(p[0])}
        if code == 45:
            return {name: self.text(p[0])}
        return {name: p[0]}

    def picture(self, p, moving):
        entry = {"number": p[0], "origin": ["upper_left", "center"][p[2]],
                 "by_variables": p[3] == 1, "x": p[4], "y": p[5],
                 "zoom_x": p[6], "zoom_y": p[7], "opacity": p[8],
                 "blend": ["normal", "add", "subtract"][p[9]]}
        if moving:
            entry.update(duration=p[10], wait=p[11])
        else:
            entry["name"] = self.text(p[1])
        return entry

    def entry_for(self, command, following):
        """The DSL entry for one real command, or None to skip it."""
        code, p = command.get("code"), command.get("parameters")
        kinds = ["item", "weapon", "armor"]
        if code == 125:
            return {"gold": self.gain(p, 0)}
        if code in (126, 127, 128):
            if code != 126 and len(p) < 5:
                return None
            entry = {"id": p[0], **self.gain(p, 1)}
            if code != 126 and p[4]:
                entry["include_equipped"] = True
            return {kinds[code - 126]: entry}
        if code == 129:
            entry = {"actor": p[0], "op": "add" if p[1] == 0 else "remove"}
            if p[2] == 1:
                entry["initialize"] = True
            return {"party": entry}
        if code == 203:
            return {"event_location": {
                "event": self.target(p[0]), "by_variables": p[1] == 1,
                "x": p[2], "y": p[3], "direction": p[4]}}
        if code == 205:
            route = p[1]
            return {"move_route": {
                "target": self.target(p[0]), "repeat": route.get("repeat"),
                "skippable": route.get("skippable"),
                "wait": route.get("wait"),
                "steps": [self.step(m) for m in route.get("list")[:-1]]}}
        if code in (212, 213):
            key = "animation" if code == 212 else "balloon"
            return {key: {"target": self.target(p[0]), key: p[1],
                          "wait": p[2]}}
        if code == 223:
            return {"tint_screen": {"tone": [int(v) for v in p[0].values],
                                    "duration": p[1], "wait": p[2]}}
        if code == 224:
            return {"flash_screen": {"color": [int(v) for v in p[0].values],
                                     "duration": p[1], "wait": p[2]}}
        if code == 225:
            return {"shake_screen": {"power": p[0], "speed": p[1],
                                     "duration": p[2], "wait": p[3]}}
        if code == 231:
            return {"show_picture": self.picture(p, False)}
        if code == 232:
            return {"move_picture": self.picture(p, True)}
        if code == 235:
            return {"erase_picture": p[0]}
        if code in (241, 245, 249, 250):
            if not p[0].get("name").ivars:
                return None
            key = {241: "bgm", 245: "bgs", 249: "me", 250: "se"}[code]
            return {key: self.audio(p[0])}
        if code == 301:
            battle = {"can_escape": p[2], "can_lose": p[3]}
            if p[0] == 0:
                battle["troop"] = p[1]
            elif p[0] == 1:
                battle["from_variable"] = p[1]
            else:
                battle["random"] = True
            return {"battle": battle}
        if code == 302:
            goods = [command, *following]
            return {"shop": {"purchase_only": p[4], "goods": [
                {kinds[g.get("parameters")[0]]: g.get("parameters")[1],
                 **({"price": g.get("parameters")[3]}
                    if g.get("parameters")[2] else {})}
                for g in goods]}}
        if code == 303:
            return {"name_input": {"actor": p[0], "max_characters": p[1]}}
        return None

    def test_every_supported_command_rebuilds_byte_for_byte(self):
        codec = self.project.codec
        compared, skipped, wrong = 0, 0, []
        supported = {125, 126, 127, 128, 129, 203, 205, 212, 213, 223, 224,
                     225, 231, 232, 235, 241, 245, 249, 250, 301, 302, 303}
        for commands in self.lists():
            for index, command in enumerate(commands or []):
                code = command.get("code")
                if code not in supported:
                    continue
                following = []
                for later in commands[index + 1:]:
                    if later.get("code") != 605:
                        break
                    following.append(later)
                entry = self.entry_for(command, following)
                if entry is None:
                    skipped += 1
                    continue
                out = compile_commands([entry], codec, engine="vx_ace")
                built = [c for c in out if c.get("code") in (code, 605)]
                real = [command, *following]
                if len(built) != len(real):
                    # `zip` stops at the shorter list, so a compiler that
                    # dropped or added a line (the goods of a shop) compared
                    # nothing and still counted as compared.
                    wrong.append((code, entry))
                for mine, theirs in zip(built, real):
                    if (marshal_rb.dump(mine.get("parameters"))
                            != marshal_rb.dump(theirs.get("parameters"))):
                        wrong.append((code, entry))
                compared += 1
        self.assertGreater(compared, 100, "compared almost nothing")
        self.assertEqual(wrong[:5], [], "%d of %d differ" % (len(wrong),
                                                             compared))
        print("\nrebuilt %d real commands byte-for-byte, skipped %d"
              % (compared, skipped))


if __name__ == "__main__":
    unittest.main()
