"""`event_template`: whole events from a few parameters, held to what the editor writes.

Every expectation below is written out by hand -- the codes, indents and
parameter lists of the commands each template must produce, the condition and
graphic of each page -- rather than read back from the table that generates
them. A template that drifts (an item id in the wrong slot, a page that stops
hiding itself behind a self switch) fails here against a literal, not against
itself.

`CASES` is the one table the structure, byte, validate and round-trip tests
share, so a template cannot be covered by three of the four.
"""

import copy
import json
import shutil
import sys
import unittest
from collections import Counter
from unittest import mock
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp.project import Project, ProjectError  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyObject, RubyString  # noqa: E402
from rpgvxace_mcp.tools import event_template_tools as templates  # noqa: E402
from rpgvxace_mcp.tools.registry import Context, REGISTRY, ToolError  # noqa: E402

from tool_case import ToolTest, ValidateTest  # noqa: E402


# ---------------------------------------------------------------------------
# What a created event is compared with: plain data, written by hand.
# ---------------------------------------------------------------------------


def plain(value, codec):
    """A loaded Ruby value as plain data, so a test can compare it to a literal."""
    if isinstance(value, RubyString):
        return codec.decode(value.data)
    if isinstance(value, RubyObject):
        out = {"__class__": value.cls}
        out.update({name: plain(value.get(name), codec)
                    for name in value.fields()})
        return out
    if isinstance(value, list):
        return [plain(item, codec) for item in value]
    return value


def shape(event, codec):
    """An event as (id, name, x, y, pages), each page's commands as tuples."""
    pages = []
    for page in event.get("pages"):
        pages.append({
            "condition": plain(page.get("condition"), codec),
            "graphic": plain(page.get("graphic"), codec),
            "trigger": page.get("trigger"),
            "priority_type": page.get("priority_type"),
            "commands": [(c.get("indent"), c.get("code"),
                          plain(c.get("parameters"), codec))
                         for c in page.get("list")],
        })
    return {"id": event.get("id"), "name": plain(event.get("name"), codec),
            "x": event.get("x"), "y": event.get("y"), "pages": pages}


ACE_CONDITION = {
    "__class__": "RPG::Event::Page::Condition",
    "switch1_valid": False, "switch2_valid": False, "variable_valid": False,
    "self_switch_valid": False, "item_valid": False, "actor_valid": False,
    "switch1_id": 1, "switch2_id": 1, "variable_id": 1, "variable_value": 0,
    "self_switch_ch": "A", "item_id": 1, "actor_id": 1,
}


def cond(**over):
    """The blank Ace condition with these fields changed."""
    return {**ACE_CONDITION, **over}


def gfx(name="", index=0, direction=2):
    return {"__class__": "RPG::Event::Page::Graphic", "tile_id": 0,
            "character_name": name, "character_index": index,
            "direction": direction, "pattern": 0}


BLANK = (0, 0, [])


def show(*lines, indent=0, face="", face_index=0):
    """Show Text on VX and VX Ace: one 101 and one 401 per line."""
    return [(indent, 101, [face, face_index, 0, 2])] + [
        (indent, 401, [line]) for line in lines]


def page(commands, *, condition=None, graphic=None, trigger=0, priority=1):
    return {"condition": condition or cond(), "graphic": graphic or gfx(),
            "trigger": trigger, "priority_type": priority,
            "commands": commands + [BLANK]}


def se(name, volume=100, pitch=100):
    return {"__class__": "RPG::SE", "name": name, "pitch": pitch,
            "volume": volume}


# ---------------------------------------------------------------------------
# The cases: a call, the event it must write, and the event_edit ops that
# write the same event by hand.
# ---------------------------------------------------------------------------

# Files the cases need that the fixture does not ship. Empty files, as the
# fixture's own are.
EXTRA_ASSETS = {"Graphics/Characters": ["!Crystal.png", "!Switch.png",
                                        "!Door1.png"]}


def chest_ops(event_id, x, y, commands, closed, opened):
    return [
        {"op": "create", "x": x, "y": y, "name": "Chest",
         "patch": {"trigger": 0, "priority_type": 1, "graphic": closed},
         "commands": commands},
        {"op": "add_page", "event_id": event_id,
         "patch": {"condition": {"self_switch_valid": True,
                                 "self_switch_ch": "A"},
                   "trigger": 0, "priority_type": 1, "graphic": opened}},
    ]


CASES = {
    "chest_item": dict(
        template="treasure_chest", map_id=2, x=5, y=5, event_id=2,
        parameters={"reward_type": "item", "reward_id": 1, "amount": 3},
        pages=[
            page([(0, 126, [1, 0, 0, 3])] + show("Found Potion x3!")
                 + [(0, 123, ["A", 0])],
                 graphic=gfx("!Chest", 0, 2)),
            page([], condition=cond(self_switch_valid=True),
                 graphic=gfx("!Chest", 0, 8)),
        ],
        ops=chest_ops(
            2, 5, 5,
            [{"item": {"id": 1, "value": 3}},
             {"text": ["Found Potion x3!"]},
             {"self_switch": "A", "value": "on"}],
            {"character_name": "!Chest", "direction": 2},
            {"character_name": "!Chest", "direction": 8})),
    "chest_gold_sound": dict(
        template="treasure_chest", map_id=2, x=6, y=5, event_id=2,
        parameters={"reward_type": "gold", "amount": 100,
                    "sound": {"name": "Move1", "volume": 80},
                    "self_switch": "B"},
        pages=[
            page([(0, 250, [se("Move1", 80)]), (0, 125, [0, 0, 100])]
                 + show("Found 100 gold!") + [(0, 123, ["B", 0])],
                 graphic=gfx("!Chest", 0, 2)),
            page([], condition=cond(self_switch_valid=True,
                                    self_switch_ch="B"),
                 graphic=gfx("!Chest", 0, 8)),
        ],
        ops=[
            {"op": "create", "x": 6, "y": 5, "name": "Chest",
             "patch": {"trigger": 0, "priority_type": 1,
                       "graphic": {"character_name": "!Chest",
                                   "direction": 2}},
             "commands": [{"se": {"name": "Move1", "volume": 80}},
                          {"gold": {"value": 100}},
                          {"text": ["Found 100 gold!"]},
                          {"self_switch": "B", "value": "on"}]},
            {"op": "add_page", "event_id": 2,
             "patch": {"condition": {"self_switch_valid": True,
                                     "self_switch_ch": "B"},
                       "trigger": 0, "priority_type": 1,
                       "graphic": {"character_name": "!Chest",
                                   "direction": 8}}}]),
    "chest_weapon_quiet": dict(
        template="treasure_chest", map_id=2, x=7, y=5, event_id=2,
        parameters={"reward_type": "weapon", "reward_id": 2,
                    "show_message": False, "graphic_index": 3,
                    "opened_index": 4},
        pages=[
            page([(0, 127, [2, 0, 0, 1, False]), (0, 123, ["A", 0])],
                 graphic=gfx("!Chest", 3, 2)),
            page([], condition=cond(self_switch_valid=True),
                 graphic=gfx("!Chest", 4, 8)),
        ],
        ops=chest_ops(
            2, 7, 5,
            [{"weapon": {"id": 2, "value": 1}},
             {"self_switch": "A", "value": "on"}],
            {"character_name": "!Chest", "character_index": 3,
             "direction": 2},
            {"character_name": "!Chest", "character_index": 4,
             "direction": 8})),
    "door": dict(
        template="door", map_id=1, x=12, y=6, event_id=3,
        parameters={"target_map_id": 2, "target_x": 4, "target_y": 5},
        pages=[page([(0, 201, [0, 2, 4, 5, 0, 0])], trigger=1, priority=0)],
        ops=[{"op": "create", "x": 12, "y": 6, "name": "Door",
              "patch": {"trigger": 1, "priority_type": 0},
              "commands": [{"transfer": {"map_id": 2, "x": 4, "y": 5,
                                         "direction": 0, "fade": 0}}]}]),
    "door_full": dict(
        template="door", map_id=1, x=13, y=6, event_id=3,
        name="Cave mouth",
        parameters={"target_map_id": 2, "target_x": 4, "target_y": 5,
                    "direction": "up", "fade": "white", "trigger": "action",
                    "sound": {"name": "Move1"}, "graphic": "!Door1",
                    "graphic_index": 2},
        pages=[page([(0, 250, [se("Move1")]), (0, 201, [0, 2, 4, 5, 8, 1])],
                    graphic=gfx("!Door1", 2, 2), trigger=0, priority=1)],
        ops=[{"op": "create", "x": 13, "y": 6, "name": "Cave mouth",
              "patch": {"trigger": 0, "priority_type": 1,
                        "graphic": {"character_name": "!Door1",
                                    "character_index": 2}},
              "commands": [{"se": {"name": "Move1"}},
                           {"transfer": {"map_id": 2, "x": 4, "y": 5,
                                         "direction": 8, "fade": 1}}]}]),
    "sign": dict(
        template="sign", map_id=1, x=3, y=3, event_id=3,
        parameters={"lines": ["Riverbend", "Population: 12", "Please keep",
                              "off the grass", "Thank you"]},
        pages=[page(show("Riverbend", "Population: 12", "Please keep",
                         "off the grass") + show("Thank you"))],
        ops=[{"op": "create", "x": 3, "y": 3, "name": "Sign",
              "patch": {"trigger": 0, "priority_type": 1},
              "commands": [{"text": ["Riverbend", "Population: 12",
                                     "Please keep", "off the grass",
                                     "Thank you"]}]}]),
    "npc_two_pages": dict(
        template="npc_dialogue", map_id=1, x=9, y=9, event_id=3,
        parameters={"lines": "Hello there.\nNice day.", "face": "Actor1",
                    "face_index": 2, "graphic": "People1",
                    "graphic_index": 1, "after_switch": 2,
                    "after_lines": ["Thanks again!"]},
        pages=[
            page(show("Hello there.", "Nice day.", face="Actor1",
                      face_index=2), graphic=gfx("People1", 1, 2)),
            page(show("Thanks again!", face="Actor1", face_index=2),
                 condition=cond(switch1_valid=True, switch1_id=2),
                 graphic=gfx("People1", 1, 2)),
        ],
        ops=[
            {"op": "create", "x": 9, "y": 9, "name": "NPC",
             "patch": {"trigger": 0, "priority_type": 1,
                       "graphic": {"character_name": "People1",
                                   "character_index": 1}},
             "commands": [{"text": ["Hello there.", "Nice day."],
                           "face_name": "Actor1", "face_index": 2}]},
            {"op": "add_page", "event_id": 3,
             "patch": {"condition": {"switch1_valid": True,
                                     "switch1_id": 2},
                       "trigger": 0, "priority_type": 1,
                       "graphic": {"character_name": "People1",
                                   "character_index": 1}},
             "commands": [{"text": ["Thanks again!"], "face_name": "Actor1",
                           "face_index": 2}]}]),
    "npc_plain": dict(
        template="npc_dialogue", map_id=1, x=9, y=10, event_id=3,
        parameters={"lines": ["Mind the bridge."]},
        pages=[page(show("Mind the bridge."))],
        ops=[{"op": "create", "x": 9, "y": 10, "name": "NPC",
              "patch": {"trigger": 0, "priority_type": 1},
              "commands": [{"text": ["Mind the bridge."]}]}]),
    "shop": dict(
        template="shop_npc", map_id=1, x=10, y=10, event_id=3,
        parameters={"goods": [{"item": 1}, {"weapon": 2, "price": 500},
                              {"armor": 1}],
                    "purchase_only": True, "greeting": ["Welcome!"]},
        pages=[page(show("Welcome!") + [
            (0, 302, [0, 1, 0, 0, True]), (0, 605, [1, 2, 1, 500]),
            (0, 605, [2, 1, 0, 0])])],
        ops=[{"op": "create", "x": 10, "y": 10, "name": "Shop",
              "patch": {"trigger": 0, "priority_type": 1},
              "commands": [{"text": ["Welcome!"]},
                           {"shop": {"goods": [{"item": 1},
                                               {"weapon": 2, "price": 500},
                                               {"armor": 1}],
                                     "purchase_only": True}}]}]),
    "save_point": dict(
        template="save_point", map_id=1, x=11, y=11, event_id=3,
        parameters={},
        pages=[page(show("Would you like to save?") + [(0, 352, [])],
                    graphic=gfx("!Crystal", 0, 2))],
        ops=[{"op": "create", "x": 11, "y": 11, "name": "Save Point",
              "patch": {"trigger": 0, "priority_type": 1,
                        "graphic": {"character_name": "!Crystal"}},
              "commands": [{"text": ["Would you like to save?"]},
                           {"raw": {"code": 352, "parameters": []}}]}]),
    "inn": dict(
        template="inn", map_id=1, x=14, y=10, event_id=3,
        parameters={"price": 50},
        pages=[page(
            show("Stay the night for 50 gold?") + [
                (0, 102, [["Stay", "Leave"], 2]),
                (0, 402, [0, "Stay"]),
                (1, 111, [12, "$game_party.gold >= 50"]),
                (2, 125, [1, 0, 50]),
                (2, 221, []),
                (2, 230, [60]),
                (2, 314, [0, 0]),
                (2, 222, []),
            ] + show("You feel well rested.", indent=2) + [
                (1, 411, []),
            ] + show("You do not have enough gold.", indent=2) + [
                (1, 412, []),
                (0, 402, [1, "Leave"]),
                (0, 404, []),
            ])],
        ops=[{"op": "create", "x": 14, "y": 10, "name": "Inn",
              "patch": {"trigger": 0, "priority_type": 1},
              "commands": [
                  {"text": ["Stay the night for 50 gold?"]},
                  {"choices": ["Stay", "Leave"], "cancel": 2, "branches": [
                      [{"if": {"script": "$game_party.gold >= 50"},
                        "then": [{"gold": {"op": "sub", "value": 50}},
                                 {"fadeout_screen": True}, {"wait": 60},
                                 {"raw": {"code": 314,
                                          "parameters": [0, 0]}},
                                 {"fadein_screen": True},
                                 {"text": ["You feel well rested."]}],
                        "else": [{"text": ["You do not have enough "
                                           "gold."]}]}],
                      []]}]}]),
    "lever": dict(
        template="switch_toggle", map_id=1, x=15, y=3, event_id=3,
        parameters={"switch_id": 5},
        pages=[
            page([(0, 121, [5, 5, 0]), (0, 123, ["A", 0])],
                 graphic=gfx("!Switch", 0, 2)),
            page([(0, 121, [5, 5, 1]), (0, 123, ["A", 1])],
                 condition=cond(self_switch_valid=True),
                 graphic=gfx("!Switch", 0, 8)),
        ],
        ops=[
            {"op": "create", "x": 15, "y": 3, "name": "Lever",
             "patch": {"trigger": 0, "priority_type": 1,
                       "graphic": {"character_name": "!Switch",
                                   "direction": 2}},
             "commands": [{"switch": 5, "value": "on"},
                          {"self_switch": "A", "value": "on"}]},
            {"op": "add_page", "event_id": 3,
             "patch": {"condition": {"self_switch_valid": True,
                                     "self_switch_ch": "A"},
                       "trigger": 0, "priority_type": 1,
                       "graphic": {"character_name": "!Switch",
                                   "direction": 8}},
             "commands": [{"switch": 5, "value": "off"},
                          {"self_switch": "A", "value": "off"}]}]),
}

# The default event name each case ends up with, written out.
CASE_NAMES = {"chest_item": "Chest", "chest_gold_sound": "Chest",
              "chest_weapon_quiet": "Chest", "door": "Door",
              "door_full": "Cave mouth", "sign": "Sign",
              "npc_two_pages": "NPC", "npc_plain": "NPC", "shop": "Shop",
              "save_point": "Save Point", "inn": "Inn", "lever": "Lever"}


SIGN_DESCRIPTION = {
    "op": "describe",
    "template": "sign",
    "summary": "A sign or readable object: the action button shows "
               "text.",
    "engines": ["vx_ace", "vx", "xp"],
    "default_event_name": "Sign",
    "parameters": [
        {"name": "lines", "type": "lines",
         "required": True, "default": None,
         "description": "What the sign says: a list of lines, or one "
                        "string with \\n between them. Four lines to "
                        "a box.",
         "max_items": 40, "max_chars": 100},
        {"name": "graphic", "type": "character", "required": False,
         "default": None,
         "description": "The event's sprite sheet: a file in "
                        "Graphics/Characters without its extension, "
                        "or \"\" for no sprite."},
        {"name": "graphic_index", "type": "index",
         "required": False, "default": None,
         "description": "Which of the sheet's 8 characters, 0 to 7. "
                        "Not offered on XP, whose sheets hold one "
                        "character.",
         "minimum": 0, "maximum": 7},
    ],
    "events": [{
        "at": "here", "name": {"$": "event_name"},
        "pages": [{
            "graphic": {"character_name": {"$": "graphic"},
                        "character_index": {"$": "graphic_index"}},
            "trigger": 0, "priority_type": 1,
            "commands": [{"text": {"$": "lines"}}]}]}],
    "notes": "The event name is `name`, default 'Sign'. A "
             "`{\"$\": X}` hole is filled with parameter X or a "
             "value the tool derives from the parameters; "
             "`{\"$when\": X, \"then\": [...]}` keeps its entries "
             "only when X is given (and `else` otherwise). The "
             "commands are the DSL event_edit takes, compiled for "
             "the open engine.",
}


EVERY_TEMPLATE = ["treasure_chest", "door", "door_pair", "sign",
                  "npc_dialogue", "shop_npc", "save_point", "inn",
                  "switch_toggle"]


def tag(value):
    """A short label for a subtest, safe for a 5,000-digit integer."""
    if isinstance(value, int) and value.bit_length() > 64:
        return "an integer of %d bits" % value.bit_length()
    return repr(value)[:20]


def add_assets(root):
    """Put the fixture-missing sprites in a project folder."""
    for folder, names in EXTRA_ASSETS.items():
        directory = Path(root) / "project" / folder
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_bytes(b"")


class TemplateCase(ToolTest):
    """A project with the sprites the cases name, and helpers to create."""

    def setUp(self):
        super().setUp()
        add_assets(self.tmp)
        self.project.forget_assets()

    def reset_to_fixture(self):
        """Back to the built project, between two cases of one test."""
        from tool_case import _Workspace
        for space in _Workspace._by_engine.values():
            if space.root == self.tmp:
                space.restore()
        add_assets(self.tmp)
        self.ctx = self.reopen()
        self.project = self.ctx.project

    def make(self, case, **over):
        """Create one CASES entry and return the reply."""
        spec = {**CASES[case], **over}
        arguments = {"op": "create", "template": spec["template"],
                     "map_id": spec["map_id"], "x": spec["x"], "y": spec["y"],
                     "parameters": copy.deepcopy(spec["parameters"])}
        if "name" in spec:
            arguments["name"] = spec["name"]
        return self.call("event_template", **arguments)

    def stored(self, map_id, event_id):
        """The event as the file holds it, read through a fresh project."""
        reopened = self.reopen()
        event = reopened.project.load_map(map_id).get("events").get(event_id)
        self.assertIsInstance(event, RubyObject,
                              "map %d has no event %d" % (map_id, event_id))
        return shape(event, reopened.codec)

    def refused(self, *needles, **arguments):
        arguments.setdefault("op", "create")
        with self.assertRaises(ToolError) as caught:
            self.call("event_template", **arguments)
        for needle in needles:
            self.assertIn(needle, str(caught.exception))
        return str(caught.exception)

    def map_bytes(self, map_id):
        return self.data_bytes("Map%03d" % map_id)

    def valid_call(self, template):
        """Arguments that create each template on map 1 at a free tile."""
        return {
            "treasure_chest": dict(parameters={"reward_type": "item",
                                               "reward_id": 1}),
            "door": dict(parameters={"target_map_id": 2, "target_x": 1,
                                     "target_y": 1}),
            "door_pair": dict(parameters={"to_map_id": 2, "to_x": 2,
                                          "to_y": 2}),
            "sign": dict(parameters={"lines": ["Hi"]}),
            "npc_dialogue": dict(parameters={"lines": ["Hi"]}),
            "shop_npc": dict(parameters={"goods": [{"item": 1}]}),
            "save_point": dict(parameters={}),
            "inn": dict(parameters={"price": 10}),
            "switch_toggle": dict(parameters={"switch_id": 1}),
        }[template]


# ---------------------------------------------------------------------------
# The table, as a caller reads it
# ---------------------------------------------------------------------------


class TestListAndDescribe(TemplateCase):

    def test_list_names_every_template_in_order(self):
        out = self.call("event_template", op="list")
        self.assertEqual([row["template"] for row in out["templates"]],
                         EVERY_TEMPLATE)

    def test_list_says_what_each_needs(self):
        rows = {row["template"]: row
                for row in self.call("event_template",
                                     op="list")["templates"]}
        self.assertEqual(rows["door"]["required"],
                         ["target_map_id", "target_x", "target_y"])
        self.assertEqual(rows["sign"]["required"], ["lines"])
        self.assertEqual(rows["save_point"]["required"], [])
        self.assertEqual(rows["door_pair"]["creates"], 2)
        self.assertEqual(rows["inn"]["engines"], ["vx_ace", "vx"])
        self.assertIn("XP", rows["inn"]["not_offered_elsewhere"])

    def test_every_template_describes_itself(self):
        for name in EVERY_TEMPLATE:
            with self.subTest(template=name):
                out = self.call("event_template", op="describe",
                                template=name)
                self.assertEqual(out["template"], name)
                self.assertTrue(out["summary"])
                self.assertTrue(out["parameters"])
                self.assertTrue(out["events"])
                for row in out["parameters"]:
                    self.assertEqual(
                        set(row) & {"name", "type", "required", "default",
                                    "description"},
                        {"name", "type", "required", "default",
                         "description"})

    def test_describe_is_stable_for_a_call_and_for_a_second_call(self):
        first = self.call("event_template", op="describe", template="inn")
        second = self.call("event_template", op="describe", template="inn")
        self.assertEqual(json.dumps(first, sort_keys=False),
                         json.dumps(second, sort_keys=False))

    def test_describe_sign_is_pinned(self):
        """The whole description of the smallest template, as a literal."""
        self.assertEqual(
            self.call("event_template", op="describe", template="sign"),
            SIGN_DESCRIPTION)

    def test_describe_lever_is_pinned_where_it_matters(self):
        out = self.call("event_template", op="describe",
                        template="switch_toggle")
        self.assertEqual([p["name"] for p in out["parameters"]],
                         ["switch_id", "sound", "graphic", "graphic_index",
                          "off_facing", "on_facing", "self_switch"])
        self.assertEqual(
            [p["default"] for p in out["parameters"]],
            [None, None, "!Switch", None, "down", "up", "A"])
        pages = out["events"][0]["pages"]
        self.assertEqual(len(pages), 2)
        self.assertEqual(
            pages[1]["condition"],
            {"self_switch_valid": True, "self_switch_ch": {"$": "self_switch"}})
        self.assertEqual(pages[0]["commands"][1:],
                         [{"switch": {"$": "switch_id"}, "value": "on"},
                          {"self_switch": {"$": "self_switch"},
                           "value": "on"}])

    def test_every_hole_names_a_parameter_or_a_derived_value(self):
        derived = {
            "event_name", "here_map_id", "here_x", "here_y", "reward_entry",
            "message_lines", "closed_facing_code", "opened_facing_code",
            "opened_index_value", "trigger_code", "priority", "fade_code",
            "direction_code", "return_direction_code", "prompt_lines",
            "can_pay", "off_facing_code", "on_facing_code",
        }

        def holes(node):
            if isinstance(node, dict):
                if "$" in node:
                    yield node["$"]
                if "$when" in node:
                    yield node["$when"]
                for item in node.values():
                    yield from holes(item)
            elif isinstance(node, list):
                for item in node:
                    yield from holes(item)

        for name in EVERY_TEMPLATE:
            with self.subTest(template=name):
                described = self.call("event_template", op="describe",
                                      template=name)
                known = {p["name"] for p in described["parameters"]} | derived
                self.assertLessEqual(set(holes(described["events"])), known)

    def test_describe_needs_a_known_template(self):
        message = self.refused("template must be one of", "treasure_chest",
                               op="describe", template="gazebo")
        self.assertNotIn("gazebo\x00", message)
        self.refused("template is required", op="describe")

    def test_list_and_describe_refuse_arguments_they_do_not_read(self):
        self.refused("does not read", "map_id", op="list", map_id=1)
        self.refused("does not read", "parameters", op="describe",
                     template="sign", parameters={})
        self.refused("does not read", "dry_run", op="preview",
                     template="sign", map_id=1, x=1, y=1, dry_run=True,
                     parameters={"lines": ["a"]})


# ---------------------------------------------------------------------------
# What each template writes
# ---------------------------------------------------------------------------


class TestEachTemplateWritesItsPages(TemplateCase):
    """Created, then read back from the file, against a hand-written event."""

    def check_case(self, key):
        spec = CASES[key]
        out = self.make(key)
        self.assertEqual(out["created"][0]["event_id"], spec["event_id"])
        expected = {"id": spec["event_id"], "name": CASE_NAMES[key],
                    "x": spec["x"], "y": spec["y"], "pages": spec["pages"]}
        self.assertEqual(self.stored(spec["map_id"], spec["event_id"]),
                         expected)

    def test_a_chest_with_an_item(self):
        self.check_case("chest_item")

    def test_a_chest_with_gold_a_sound_and_another_self_switch(self):
        self.check_case("chest_gold_sound")

    def test_a_chest_with_a_weapon_no_message_and_sheet_characters(self):
        self.check_case("chest_weapon_quiet")

    def test_a_chest_with_armor(self):
        self.make("chest_item", parameters={"reward_type": "armor",
                                            "reward_id": 1})
        commands = self.stored(2, 2)["pages"][0]["commands"]
        self.assertEqual(commands[0], (0, 128, [1, 0, 0, 1, False]))
        self.assertEqual(commands[2], (0, 401, ["Found Leather Shield!"]))

    def test_a_door_that_fires_on_touch(self):
        self.check_case("door")

    def test_a_door_with_every_option(self):
        self.check_case("door_full")

    def test_a_sign_splits_five_lines_into_two_boxes(self):
        self.check_case("sign")

    def test_an_npc_with_a_face_and_a_second_page(self):
        self.check_case("npc_two_pages")

    def test_an_npc_with_one_page_and_no_face(self):
        self.check_case("npc_plain")

    def test_a_shop_with_a_price_override_and_a_greeting(self):
        self.check_case("shop")

    def test_a_save_point_opens_the_save_screen(self):
        self.check_case("save_point")

    def test_an_inn_charges_heals_and_fades(self):
        self.check_case("inn")

    def test_a_lever_flips_the_switch_and_itself(self):
        self.check_case("lever")

    def test_every_case_is_written_by_a_case(self):
        """So a template added to the table cannot go untested here."""
        covered = {spec["template"] for spec in CASES.values()}
        self.assertEqual(covered | {"door_pair"}, set(EVERY_TEMPLATE))

    def test_a_database_name_is_one_clean_line_in_the_message(self):
        item = self.project.get_entry("items", 1)
        item.set("name", self.project.codec.make_string("Pot\nion\t X"))
        self.make("chest_item")
        commands = self.stored(2, 2)["pages"][0]["commands"]
        self.assertEqual(commands[2], (0, 401, ["Found Pot ion X x3!"]))

    def test_a_database_name_that_is_not_text_is_replaced(self):
        item = self.project.get_entry("items", 1)
        item.set("name", [1, 2])
        self.make("chest_item")
        commands = self.stored(2, 2)["pages"][0]["commands"]
        self.assertEqual(commands[2], (0, 401, ["Found item 1 x3!"]))

    def test_a_given_name_replaces_the_default(self):
        self.make("sign", name="Notice board")
        self.assertEqual(self.stored(1, 3)["name"], "Notice board")

    def test_a_door_pair_writes_both_sides_on_two_maps(self):
        out = self.call("event_template", op="create", template="door_pair",
                        map_id=1, x=12, y=6, name="Cave door",
                        parameters={"to_map_id": 2, "to_x": 4, "to_y": 5,
                                    "direction": "down",
                                    "return_direction": "up",
                                    "fade": "none"})
        self.assertEqual(
            [(r["map_id"], r["event_id"], r["x"], r["y"], r["name"])
             for r in out["created"]],
            [(1, 3, 12, 6, "Cave door"), (2, 2, 4, 5, "Cave door (return)")])
        self.assertEqual([w["file"] for w in out["writes"]],
                         ["Map001.rvdata2", "Map002.rvdata2"])
        first = self.stored(1, 3)
        self.assertEqual(
            first["pages"],
            [page([(0, 201, [0, 2, 4, 5, 2, 2])], trigger=1, priority=0)])
        second = self.stored(2, 2)
        self.assertEqual(
            second["pages"],
            [page([(0, 201, [0, 1, 12, 6, 8, 2])], trigger=1, priority=0)])

    def test_a_second_file_that_cannot_be_written_says_what_landed(self):
        real = self.project.save_file
        calls = []

        def flaky(filename, **keywords):
            calls.append(filename)
            if len(calls) == 2:
                raise ProjectError("the disk is full")
            return real(filename, **keywords)

        arguments = dict(op="create", template="door_pair", map_id=1, x=12,
                         y=6, parameters={"to_map_id": 2, "to_x": 4,
                                          "to_y": 5})
        with mock.patch.object(self.project, "save_file", flaky):
            self.refused("Map001.rvdata2 was written, but Map002.rvdata2 was "
                         "not: the disk is full", **arguments)
        self.assertEqual(calls, ["Map001.rvdata2", "Map002.rvdata2"])
        self.assertEqual(self.stored(1, 3)["name"], "Door")
        events = self.reopen().project.load_map(2).get("events")
        self.assertEqual([k for k, _ in events.sorted_items()], [1])

    def test_a_first_file_that_cannot_be_written_is_the_plain_error(self):
        def refuse(filename, **keywords):
            raise ProjectError("the disk is full")

        with mock.patch.object(self.project, "save_file", refuse):
            with self.assertRaises(ProjectError) as caught:
                self.call("event_template", op="create", template="sign",
                          map_id=1, x=1, y=1, parameters={"lines": ["a"]})
        self.assertEqual(str(caught.exception), "the disk is full")

    def test_a_door_pair_on_one_map_writes_one_file_two_events(self):
        out = self.call("event_template", op="create", template="door_pair",
                        map_id=1, x=1, y=1,
                        parameters={"to_map_id": 1, "to_x": 15, "to_y": 12})
        self.assertEqual([(r["event_id"], r["x"], r["y"])
                          for r in out["created"]], [(3, 1, 1), (4, 15, 12)])
        self.assertEqual(len(out["writes"]), 1)
        self.assertEqual(self.stored(1, 4)["pages"][0]["commands"][0],
                         (0, 201, [0, 1, 1, 1, 0, 0]))


class TestBytesAreEventEdits(TemplateCase):
    """The template's file is the file event_edit's own ops write."""

    def test_each_case_matches_the_hand_written_event_edit_ops(self):
        for key, spec in sorted(CASES.items()):
            with self.subTest(case=key):
                self.reset_to_fixture()
                other = self.second_project()
                self.make(key)
                other.call("event_edit", map_id=spec["map_id"],
                           ops=copy.deepcopy(spec["ops"]))
                name = "Map%03d.rvdata2" % spec["map_id"]
                self.assertEqual(
                    (self.tmp / "project" / "Data" / name).read_bytes(),
                    (other_root(other) / "Data" / name).read_bytes())

    def test_a_door_pair_matches_two_event_edit_calls(self):
        other = self.second_project()
        self.call("event_template", op="create", template="door_pair",
                  map_id=1, x=12, y=6,
                  parameters={"to_map_id": 2, "to_x": 4, "to_y": 5})
        other.call("event_edit", map_id=1, ops=[{
            "op": "create", "x": 12, "y": 6, "name": "Door",
            "patch": {"trigger": 1, "priority_type": 0},
            "commands": [{"transfer": {"map_id": 2, "x": 4, "y": 5,
                                       "direction": 0, "fade": 0}}]}])
        other.call("event_edit", map_id=2, ops=[{
            "op": "create", "x": 4, "y": 5, "name": "Door (return)",
            "patch": {"trigger": 1, "priority_type": 0},
            "commands": [{"transfer": {"map_id": 1, "x": 12, "y": 6,
                                       "direction": 0, "fade": 0}}]}])
        for name in ("Map001.rvdata2", "Map002.rvdata2"):
            self.assertEqual(
                (self.tmp / "project" / "Data" / name).read_bytes(),
                (other_root(other) / "Data" / name).read_bytes())

    def second_project(self):
        """A copy of the project as it is now, opened as its own context."""
        destination = Path(self.tmp) / "second"
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(Path(self.tmp) / "project", destination,
                        ignore=shutil.ignore_patterns(".rpgvxace-mcp"))
        return CopyOfProject(destination, self.ctx)


class CopyOfProject:
    """A second context over a copy, so two writers can be compared."""

    def __init__(self, root, like):
        self.root = Path(root)
        self.ctx = Context(Project(self.root))

    def call(self, tool_name, /, **arguments):
        return REGISTRY[tool_name].run(self.ctx, arguments)


def other_root(copy_of):
    return copy_of.root


# ---------------------------------------------------------------------------
# validate must have nothing to add
# ---------------------------------------------------------------------------


class TestValidateFindsNothingNew(TemplateCase):

    def findings(self):
        return Counter(json.dumps(f, sort_keys=True)
                       for f in self.call("validate", refresh=True)["findings"])

    def test_the_fixture_starts_clean(self):
        self.assertEqual(self.findings(), Counter())

    def test_every_case_adds_no_finding(self):
        before = self.findings()
        for key in sorted(CASES):
            with self.subTest(case=key):
                self.make(key)
                self.assertEqual(self.findings() - before, Counter(),
                                 "validate found something new after %s"
                                 % key)

    def test_a_door_pair_adds_no_finding(self):
        before = self.findings()
        self.call("event_template", op="create", template="door_pair",
                  map_id=1, x=12, y=6,
                  parameters={"to_map_id": 2, "to_x": 4, "to_y": 5,
                              "sound": {"name": "Move1"}})
        self.assertEqual(self.findings() - before, Counter())

    def test_a_template_created_with_every_case_at_once_is_still_clean(self):
        before = self.findings()
        for key in sorted(CASES):
            self.make(key, x=CASES[key]["x"] + 0)
        after = self.findings()
        self.assertEqual(after - before, Counter())


# ---------------------------------------------------------------------------
# Round trip through event_get
# ---------------------------------------------------------------------------


class TestRoundTripsThroughEventGet(TemplateCase):

    def raw_ops(self, map_id, event_id):
        """event_get's raw view, written back as DSL `raw` entries and patches."""
        view = self.call("event_get", map_id=map_id, event_ids=[event_id],
                         include_raw=True)["events"][0]
        ops = []
        for index, page_view in enumerate(view["pages"]):
            patch = {"trigger": page_view["trigger_code"],
                     "priority_type": page_view["priority_type"],
                     "condition": self.strip_class(page_view["condition"]),
                     "graphic": self.strip_class(page_view["graphic"])}
            entries = [{"raw": {"code": c["code"], "indent": c["indent"],
                                "parameters": c["parameters"]}}
                       for c in page_view["commands"][:-1]]
            if index == 0:
                ops.append({"op": "create", "x": view["x"], "y": view["y"],
                            "name": view["name"], "patch": patch,
                            **({"commands": entries} if entries else {})})
            else:
                ops.append({"op": "add_page", "event_id": "NEW",
                            "patch": patch,
                            **({"commands": entries} if entries else {})})
        return ops

    def strip_class(self, value):
        return {k: v for k, v in value.items() if not k.startswith("__")}

    def test_the_script_text_of_a_chest_is_what_the_editor_shows(self):
        self.make("chest_item")
        view = self.call("event_get", map_id=2, event_ids=[2])["events"][0]
        self.assertEqual(view["pages"][0]["script"], [
            "  0| Change Items: item 1 + 3",
            "  1| Show Text (no face)",
            "  2| \"Found Potion x3!\"",
            "  3| Control Self Switch: A = ON",
            "  4| ",
        ])
        self.assertEqual(view["pages"][1]["script"], ["  0| "])
        self.assertEqual(view["pages"][0]["trigger"], "action button")

    def test_every_case_rebuilds_from_event_get_to_the_same_bytes(self):
        for key, spec in sorted(CASES.items()):
            with self.subTest(case=key):
                self.reset_to_fixture()
                self.make(key)
                made = (self.tmp / "project" / "Data"
                        / ("Map%03d.rvdata2" % spec["map_id"])).read_bytes()
                ops = self.raw_ops(spec["map_id"], spec["event_id"])
                for op in ops:
                    if op.get("event_id") == "NEW":
                        op["event_id"] = spec["event_id"]
                self.reset_to_fixture()
                self.call("event_edit", map_id=spec["map_id"], ops=ops)
                rebuilt = (self.tmp / "project" / "Data"
                           / ("Map%03d.rvdata2" % spec["map_id"])).read_bytes()
                self.assertEqual(rebuilt, made)


# ---------------------------------------------------------------------------
# Where an event goes
# ---------------------------------------------------------------------------


class TestPlacement(TemplateCase):

    def arguments(self, over):
        arguments = dict(op="create", template="sign", map_id=1, x=3, y=3,
                         parameters={"lines": ["Hi"]})
        arguments.update(over)
        return arguments

    def sign(self, **over):
        return self.call("event_template", **self.arguments(over))

    def refused(self, *needles, **over):
        return super().refused(*needles, **self.arguments(over))

    def test_the_event_takes_the_next_free_id(self):
        self.assertEqual(self.sign()["created"][0]["event_id"], 3)
        self.assertEqual(self.sign(x=4)["created"][0]["event_id"], 4)

    def test_a_gap_in_the_ids_is_filled(self):
        self.call("event_edit", map_id=1, ops=[
            {"op": "delete", "event_id": 1}])
        self.assertEqual(self.sign()["created"][0]["event_id"], 1)

    def test_an_empty_map_starts_at_one(self):
        self.call("event_edit", map_id=2, ops=[
            {"op": "delete", "event_id": 1}])
        out = self.sign(map_id=2)
        self.assertEqual(out["created"][0]["event_id"], 1)

    def test_a_tile_outside_the_map_is_refused_and_nothing_is_written(self):
        before = self.map_bytes(1)
        self.refused("(20, 3) is outside map 1", "20x15", x=20)
        self.refused("(3, 15) is outside map 1", y=15)
        self.assertEqual(self.map_bytes(1), before)

    def test_a_tile_beyond_the_ceiling_is_refused_by_the_schema(self):
        self.refused("x must be at most 9999", x=10000)
        self.refused("x must be at least 0", x=-1)

    def test_another_events_tile_is_refused_naming_it(self):
        self.refused("already has event 1 at (5, 4)", "allow_overlap",
                     x=5, y=4)

    def test_allow_overlap_stacks_them(self):
        out = self.sign(x=5, y=4, allow_overlap=True)
        self.assertEqual((out["created"][0]["x"], out["created"][0]["y"]),
                         (5, 4))

    def test_two_events_of_one_door_pair_cannot_share_a_tile(self):
        before = self.map_bytes(1)
        self.refused("already has event 3 at (1, 1)",
                     template="door_pair", map_id=1, x=1, y=1,
                     parameters={"to_map_id": 1, "to_x": 1, "to_y": 1})
        self.assertEqual(self.map_bytes(1), before)

    def test_a_map_that_does_not_exist_is_refused(self):
        self.refused("there is no map 7", map_id=7)

    def test_a_dry_run_writes_nothing_and_reuses_the_id(self):
        before = self.map_bytes(1)
        out = self.sign(dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertEqual(out["would_create"][0]["event_id"], 3)
        self.assertEqual(self.map_bytes(1), before)
        self.assertEqual(self.sign()["created"][0]["event_id"], 3)

    def test_a_dry_run_leaves_nothing_in_the_cache_for_a_later_write(self):
        self.sign(dry_run=True)
        self.call("event_edit", map_id=1, ops=[
            {"op": "update", "event_id": 1, "name": "Elder2"}])
        names = [e["name"] for e in self.call("event_get", map_id=1)["events"]]
        self.assertEqual(names, ["Elder2", "Cave Entrance"])

    def test_a_preview_is_a_dry_run_that_shows_the_pages(self):
        before = self.map_bytes(1)
        out = self.call("event_template", op="preview", template="sign",
                        map_id=1, x=3, y=3, parameters={"lines": ["Hi"]})
        self.assertTrue(out["dry_run"])
        row = out["would_create"][0]
        self.assertEqual(row["pages"][0]["commands"],
                         [{"text": ["Hi"]}])
        self.assertEqual(row["pages"][0]["script"], [
            "  0| Show Text (no face)", "  1| \"Hi\"", "  2| "])
        self.assertEqual(self.map_bytes(1), before)

    def test_a_preview_names_the_same_event_a_create_then_writes(self):
        out = self.call("event_template", op="preview", template="sign",
                        map_id=1, x=3, y=3, parameters={"lines": ["Hi"]})
        made = self.sign()
        row, real = out["would_create"][0], made["created"][0]
        for key in ("map_id", "event_id", "x", "y", "name", "page_count"):
            self.assertEqual(row[key], real[key])

    def test_a_preview_refuses_what_a_create_would(self):
        self.refused("(20, 3) is outside map 1", op="preview", x=20)
        self.refused("already has event 1", op="preview", x=5, y=4)

    def test_a_refusal_after_the_first_event_of_a_pair_writes_neither_map(self):
        first, second = self.map_bytes(1), self.map_bytes(2)
        self.refused("parameters to_x and to_y: (12, 3) is outside map 2",
                     "12x10", template="door_pair", map_id=1, x=1, y=1,
                     parameters={"to_map_id": 2, "to_x": 12, "to_y": 3})
        self.assertEqual((self.map_bytes(1), self.map_bytes(2)),
                         (first, second))

    def test_a_map_holding_its_events_in_a_non_hash_is_refused_whole(self):
        game_map = self.project.load_map(1)
        game_map.set("events", [1, 2, 3])
        self.project.save_file("Map001.rvdata2", tool="test")
        self.ctx = self.reopen()
        self.project = self.ctx.project
        self.refused("holds its events as list rather than a Hash")


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


class TestParametersAreRefusedByName(TemplateCase):

    def chest(self, **parameters):
        return dict(op="create", template="treasure_chest", map_id=2, x=1,
                    y=1, parameters={"reward_type": "item", "reward_id": 1,
                                     **parameters})

    def test_a_missing_required_parameter_is_named(self):
        self.refused("parameter reward_type is required",
                     op="create", template="treasure_chest", map_id=2, x=1,
                     y=1, parameters={})
        self.refused("parameter lines is required", op="create",
                     template="sign", map_id=1, x=1, y=1)

    def test_an_unknown_parameter_is_named_with_the_real_ones(self):
        message = self.refused("no parameter 'amout'", "amount",
                               **self.chest(amout=2))
        self.assertIn("reward_id", message)

    def test_what_an_integer_parameter_will_not_take(self):
        huge = 10 ** 5000
        for bad in (True, 2.5, "3", "²", [1], {"a": 1}, huge, -huge, 0,
                    10 ** 12):
            with self.subTest(value=tag(bad)):
                message = self.refused("parameter amount", **self.chest(
                    amount=bad))
                self.assertLess(len(message), 400)

    def test_an_id_that_is_not_in_the_database_is_named(self):
        self.refused("parameter reward_id: there is no item 99",
                     **self.chest(reward_id=99))
        self.refused("there is no weapon 3",
                     **self.chest(reward_type="weapon", reward_id=3))
        self.refused("there is no armor 3",
                     **self.chest(reward_type="armor", reward_id=3))

    def test_a_reward_id_is_required_for_goods_and_refused_for_gold(self):
        self.refused("parameter reward_id is required for an item reward",
                     op="create", template="treasure_chest", map_id=2, x=1,
                     y=1, parameters={"reward_type": "item"})
        self.refused("parameter reward_id is not used for a gold reward",
                     **self.chest(reward_type="gold", amount=5))
        self.refused("parameter amount is required for a gold reward",
                     **self.chest(reward_type="gold", reward_id=None))

    def test_a_choice_must_be_one_of_its_choices(self):
        self.refused("parameter reward_type must be one of item, weapon, "
                     "armor, gold, got 'ring'",
                     **self.chest(reward_type="ring"))
        self.refused("parameter self_switch must be one of A, B, C, D",
                     **self.chest(self_switch="E"))
        self.refused("parameter self_switch must be one of",
                     **self.chest(self_switch=1))

    def test_a_boolean_must_be_a_boolean(self):
        self.refused("parameter show_message must be true or false",
                     **self.chest(show_message="no"))

    def test_text_that_is_not_text_is_refused(self):
        for bad in (5, True, {"a": 1}, [[1]], [None]):
            with self.subTest(value=repr(bad)):
                self.refused("parameter lines", op="create", template="sign",
                             map_id=1, x=1, y=1, parameters={"lines": bad})

    def test_lines_are_bounded_in_count_and_length(self):
        self.refused("parameter lines takes 1 to 40 lines, got 41",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a"] * 41})
        self.refused("parameter lines[1] is 101 characters; at most 100",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a", "b" * 101]})
        self.refused("parameter lines takes 1 to 40 lines",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": []})

    def test_one_huge_string_is_refused_before_it_is_split(self):
        message = self.refused("parameter lines is 4100 characters",
                               op="create", template="sign", map_id=1, x=1,
                               y=1, parameters={"lines": "a\n" * 2050})
        self.assertLess(len(message), 300)
        message = self.refused("parameter lines", op="create",
                               template="sign", map_id=1, x=1, y=1,
                               parameters={"lines": "z" * 1_000_000})
        self.assertLess(len(message), 300)

    def test_a_string_of_lines_is_split_on_newlines(self):
        self.call("event_template", op="create", template="sign", map_id=1,
                  x=1, y=1, parameters={"lines": "one\ntwo"})
        self.assertEqual(self.stored(1, 3)["pages"][0]["commands"][:3],
                         show("one", "two"))

    def test_control_characters_are_refused_not_stripped(self):
        for bad in ("a\x00b", "a\x07", "tab\there", "ret\rurn", "a\x7fb",
                    "x y"):
            with self.subTest(value=repr(bad)):
                self.refused("parameter lines[0] holds a control character",
                             op="create", template="sign", map_id=1, x=1,
                             y=1, parameters={"lines": [bad]})

    def test_a_lone_surrogate_is_refused(self):
        self.refused("parameter lines[0] holds a control character",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["\ud800"]})

    def test_empty_text_is_refused(self):
        self.refused("parameter lines has no text in it", op="create",
                     template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["", "  "]})

    def test_braces_and_percents_in_text_are_written_as_typed(self):
        """Nothing a caller types is a format string."""
        line = "{event_name} {0} %s %d {here_x!r}"
        self.call("event_template", op="create", template="sign", map_id=1,
                  x=1, y=1, parameters={"lines": [line]})
        self.assertEqual(self.stored(1, 3)["pages"][0]["commands"][1],
                         (0, 401, [line]))

    def test_the_event_name_is_bounded_and_cleaned(self):
        self.refused("name takes at most 32 characters", name="n" * 33,
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a"]})
        self.refused("parameter name holds a control character",
                     name="a\nb", op="create", template="sign", map_id=1,
                     x=1, y=1, parameters={"lines": ["a"]})
        self.refused("name is empty", name="   ", op="create",
                     template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a"]})
        self.refused("name must be a string", name=5, op="create",
                     template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a"]})

    def test_parameters_that_are_not_an_object_are_refused(self):
        for bad in ([], "x", 5, True):
            with self.subTest(value=repr(bad)):
                self.refused("parameters must be an object", op="create",
                             template="sign", map_id=1, x=1, y=1,
                             parameters=bad)

    def test_a_huge_parameter_name_is_not_quoted_whole(self):
        message = self.refused("this template has no parameter", op="create",
                               template="sign", map_id=1, x=1, y=1,
                               parameters={"k" * 1_000_000: 1})
        self.assertLess(len(message), 400)

    def test_allow_overlap_must_be_a_boolean(self):
        self.refused("allow_overlap must be true or false", op="create",
                     template="sign", map_id=1, x=1, y=1, allow_overlap="yes",
                     parameters={"lines": ["a"]})

    def test_too_many_parameters_are_refused(self):
        many = {"p%d" % i: 1 for i in range(41)}
        self.refused("parameters has 41 entries; at most 40", op="create",
                     template="sign", map_id=1, x=1, y=1, parameters=many)

    def test_a_nested_value_is_not_walked_past_its_bound(self):
        deep = []
        for _ in range(3000):
            deep = [deep]
        self.refused("parameter lines", op="create", template="sign",
                     map_id=1, x=1, y=1, parameters={"lines": deep})

    def test_sound_shapes(self):
        base = dict(op="create", template="door", map_id=1, x=1, y=1)
        params = {"target_map_id": 2, "target_x": 1, "target_y": 1}
        self.refused("parameter sound must be an object", **base,
                     parameters={**params, "sound": "Move1"})
        self.refused("parameter sound needs a name", **base,
                     parameters={**params, "sound": {}})
        self.refused("parameter sound takes name, volume and pitch; "
                     "'loud' is not one of them", **base,
                     parameters={**params,
                                 "sound": {"name": "Move1", "loud": 1}})
        self.refused("parameter sound.volume must be 0 to 100, got 101",
                     **base, parameters={
                         **params, "sound": {"name": "Move1", "volume": 101}})
        self.refused("parameter sound.pitch must be 50 to 150, got 49",
                     **base, parameters={
                         **params, "sound": {"name": "Move1", "pitch": 49}})
        self.refused("parameter sound.volume must be a whole number",
                     **base, parameters={
                         **params, "sound": {"name": "Move1", "volume": 9.5}})
        self.refused("parameter sound.name: there is no 'Nope' in Audio/SE",
                     **base, parameters={**params,
                                         "sound": {"name": "Nope"}})

    def test_goods_shapes(self):
        base = dict(op="create", template="shop_npc", map_id=1, x=1, y=1)
        self.refused("parameter goods must be a list", **base,
                     parameters={"goods": {"item": 1}})
        self.refused("parameter goods takes 1 to 100 goods, got 0", **base,
                     parameters={"goods": []})
        self.refused("parameter goods takes 1 to 100 goods, got 101", **base,
                     parameters={"goods": [{"item": 1}] * 101})
        self.refused("parameter goods[0] must be an object", **base,
                     parameters={"goods": [1]})
        self.refused("parameter goods[0] takes exactly one of item, weapon "
                     "or armor, got none", **base,
                     parameters={"goods": [{}]})
        self.refused("parameter goods[1] takes exactly one of item, weapon "
                     "or armor, got item, weapon", **base,
                     parameters={"goods": [{"item": 1},
                                           {"item": 1, "weapon": 1}]})
        self.refused("parameter goods[0] takes item, weapon, armor and "
                     "price; 'skill' is not one of them", **base,
                     parameters={"goods": [{"skill": 1}]})
        self.refused("parameter goods[0].item: there is no item 9", **base,
                     parameters={"goods": [{"item": 9}]})
        self.refused("parameter goods[0].price must be 0 to 99999999",
                     **base, parameters={"goods": [{"item": 1,
                                                    "price": -1}]})
        self.refused("parameter goods[0].item must be a whole number",
                     **base, parameters={"goods": [{"item": True}]})

    def test_a_door_must_arrive_on_its_map(self):
        base = dict(op="create", template="door", map_id=1, x=1, y=1)
        self.refused("there is no map 9", "parameter target_map_id", **base,
                     parameters={"target_map_id": 9, "target_x": 1,
                                 "target_y": 1})
        self.refused("parameters target_x and target_y: (12, 1) is outside "
                     "map 2, which is 12x10", **base,
                     parameters={"target_map_id": 2, "target_x": 12,
                                 "target_y": 1})
        self.refused("parameter target_x must be 0 to 9999", **base,
                     parameters={"target_map_id": 2, "target_x": -1,
                                 "target_y": 1})

    def test_an_npcs_second_page_needs_both_halves(self):
        base = dict(op="create", template="npc_dialogue", map_id=1, x=1, y=1)
        self.refused("after_switch and after_lines go together", **base,
                     parameters={"lines": ["a"], "after_switch": 2})
        self.refused("after_switch and after_lines go together", **base,
                     parameters={"lines": ["a"], "after_lines": ["b"]})

    def test_an_inn_price_is_bounded(self):
        self.refused("parameter price must be 1 to 99999999, got 0",
                     op="create", template="inn", map_id=1, x=1, y=1,
                     parameters={"price": 0})

    def test_the_arguments_of_the_call_are_read_strictly(self):
        for key in ("map_id", "x", "y"):
            for bad in (True, 2.5, "1", [1], 10 ** 5000, -10 ** 5000):
                with self.subTest(key=key, value=tag(bad)):
                    arguments = dict(op="create", template="sign", map_id=1,
                                     x=1, y=1, parameters={"lines": ["a"]})
                    arguments[key] = bad
                    with self.assertRaises(ToolError):
                        self.call("event_template", **arguments)
        self.refused("map_id is required", op="create", template="sign",
                     x=1, y=1, parameters={"lines": ["a"]})


class TestAssetsAreCheckedAgainstTheProject(TemplateCase):

    def test_a_missing_graphic_is_refused_naming_the_parameter(self):
        message = self.refused(
            "parameter graphic: there is no 'Nobody' in Graphics/Characters",
            op="create", template="sign", map_id=1, x=1, y=1,
            parameters={"lines": ["a"], "graphic": "Nobody"})
        self.assertIn("files here", message)

    def test_a_missing_default_graphic_is_refused_too(self):
        (Path(self.tmp) / "project" / "Graphics" / "Characters"
         / "!Switch.png").unlink()
        self.project.forget_assets()
        self.refused("parameter graphic: there is no '!Switch'",
                     op="create", template="switch_toggle", map_id=1, x=1,
                     y=1, parameters={"switch_id": 1})

    def test_an_empty_graphic_means_no_sprite(self):
        self.call("event_template", op="create", template="switch_toggle",
                  map_id=1, x=1, y=1,
                  parameters={"switch_id": 1, "graphic": ""})
        self.assertEqual(self.stored(1, 3)["pages"][0]["graphic"],
                         gfx("", 0, 2))

    def test_a_face_is_checked_in_the_faces_folder(self):
        self.refused("parameter face: there is no 'Nobody' in Graphics/Faces",
                     op="create", template="npc_dialogue", map_id=1, x=1,
                     y=1, parameters={"lines": ["a"], "face": "Nobody"})

    def test_a_graphic_that_differs_only_in_case_is_warned_about(self):
        out = self.call("event_template", op="create", template="sign",
                        map_id=1, x=1, y=1,
                        parameters={"lines": ["a"], "graphic": "people1"})
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("parameter graphic: 'people1' is spelled People1 in "
                      "Graphics/Characters", out["warnings"][0])

    def test_a_path_is_not_a_file_name(self):
        for bad in ("../Evil", "a/b", "a\\b", "C:x", "a*", "a|b"):
            with self.subTest(value=bad):
                self.refused("path character", op="create", template="sign",
                             map_id=1, x=1, y=1,
                             parameters={"lines": ["a"], "graphic": bad})

    def test_a_graphic_name_is_bounded(self):
        self.refused("parameter graphic is 65 characters; at most 64",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a"], "graphic": "g" * 65})

    def test_a_switch_past_the_declared_count_is_a_warning(self):
        out = self.call("event_template", op="create",
                        template="switch_toggle", map_id=1, x=1, y=1,
                        parameters={"switch_id": 5000})
        self.assertEqual(len(out["warnings"]), 1)
        self.assertIn("parameter switch_id: switch 5000 is past the", out[
            "warnings"][0])


class TestAMissingRtpWarnsInsteadOfRefusing(ValidateTest):

    def setUp(self):
        super().setUp()
        add_assets(self.tmp)
        self.declare_missing_rtp()

    def test_an_unseen_graphic_and_sound_are_warned_about(self):
        out = self.call("event_template", op="create", template="door",
                        map_id=1, x=1, y=1,
                        parameters={"target_map_id": 2, "target_x": 1,
                                    "target_y": 1, "graphic": "FromRtp",
                                    "sound": {"name": "RtpSound"}})
        self.assertEqual(len(out["warnings"]), 2)
        joined = " | ".join(out["warnings"])
        self.assertIn("parameter graphic: 'FromRtp' is not in "
                      "Graphics/Characters; it may come from "
                      "NoSuchRtpAnywhere", joined)
        self.assertIn("parameter sound.name: 'RtpSound' is not in Audio/SE",
                      joined)
        reopened = self.reopen().project.load_map(1).get("events").get(3)
        self.assertEqual(
            self.project.codec.decode(
                reopened.get("pages")[0].get("graphic")
                .get("character_name").data), "FromRtp")

    def test_a_graphic_the_project_has_is_not_warned_about(self):
        out = self.call("event_template", op="create", template="sign",
                        map_id=1, x=1, y=1,
                        parameters={"lines": ["a"], "graphic": "People1"})
        self.assertNotIn("warnings", out)


# ---------------------------------------------------------------------------
# Engines
# ---------------------------------------------------------------------------


class EngineMatrix:
    """Every template, created on the engine this case runs."""

    # What each template needs on each engine, kept apart from the table in
    # the tool so a wrong `engines` entry fails here.
    OFFERED = {
        "treasure_chest": {"vx_ace", "vx"},
        "door": {"vx_ace", "vx", "xp"},
        "door_pair": {"vx_ace", "vx", "xp"},
        "sign": {"vx_ace", "vx", "xp"},
        "npc_dialogue": {"vx_ace", "vx", "xp"},
        "shop_npc": {"vx_ace", "vx"},
        "save_point": {"vx_ace", "vx", "xp"},
        "inn": {"vx_ace", "vx"},
        "switch_toggle": {"vx_ace", "vx", "xp"},
    }

    def test_the_table_claims_what_this_engine_really_does(self):
        rows = {row["template"]: row for row in self.call(
            "event_template", op="list")["templates"]}
        for name in EVERY_TEMPLATE:
            with self.subTest(template=name):
                claimed = self.engine in rows[name]["engines"]
                self.assertEqual(claimed, self.engine in self.OFFERED[name])
                arguments = dict(op="create", template=name, map_id=1,
                                 x=EVERY_TEMPLATE.index(name) + 1, y=14,
                                 **self.valid_call(name))
                if name == "save_point" or name == "switch_toggle":
                    arguments["parameters"] = {
                        **arguments["parameters"], "graphic": ""}
                if claimed:
                    out = self.call("event_template", **arguments)
                    self.assertTrue(out["created"])
                else:
                    with self.assertRaises(ToolError) as caught:
                        self.call("event_template", **arguments)
                    self.assertIn("RPG Maker XP", str(caught.exception))

    def test_every_engine_validates_clean_after_every_offered_template(self):
        before = self.call("validate", refresh=True)["findings"]
        for name in EVERY_TEMPLATE:
            if self.engine not in self.OFFERED[name]:
                continue
            arguments = dict(op="create", template=name, map_id=1,
                             x=EVERY_TEMPLATE.index(name) + 1, y=14,
                             **self.valid_call(name))
            if name in ("save_point", "switch_toggle"):
                arguments["parameters"] = {**arguments["parameters"],
                                           "graphic": ""}
            self.call("event_template", **arguments)
        after = self.call("validate", refresh=True)["findings"]
        self.assertEqual(after, before)


class TestVXAceMatrix(EngineMatrix, TemplateCase):
    engine = "vx_ace"


class TestVXMatrix(EngineMatrix, TemplateCase):
    engine = "vx"

    def use_code_page(self, name):
        """Reopen the project reading and writing this code page."""
        self.project = Project(self.tmp / "project", encoding=name)
        self.ctx = Context(self.project)

    def test_a_vx_shop_has_no_prices_and_refuses_one(self):
        self.call("event_template", op="create", template="shop_npc",
                  map_id=1, x=10, y=10,
                  parameters={"goods": [{"item": 1}, {"weapon": 2}],
                              "purchase_only": True})
        codec = self.project.codec
        commands = shape(self.reopen().project.load_map(1).get("events")
                         .get(3), codec)["pages"][0]["commands"]
        self.assertEqual(commands, [(0, 302, [0, 1, True]),
                                    (0, 605, [1, 2]), BLANK])
        self.refused("RPG Maker VX: a shop's goods has no price",
                     op="create", template="shop_npc", map_id=1, x=11, y=10,
                     parameters={"goods": [{"item": 1, "price": 5}]})

    def test_text_the_code_page_cannot_hold_is_refused_naming_the_parameter(
            self):
        self.use_code_page("cp1252")
        self.refused("parameter lines[0]", "cannot be written",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["日本"]})
        self.refused("parameter name", "cannot be written", name="日",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a"]})

    def test_every_case_but_the_shop_writes_the_same_pages_as_ace(self):
        """VX's layouts for these commands are the VX Ace ones."""
        for key, spec in sorted(CASES.items()):
            if key == "shop":
                continue
            with self.subTest(case=key):
                self.reset_to_fixture()
                self.make(key)
                stored = self.stored(spec["map_id"], spec["event_id"])
                self.assertEqual(
                    [(p["trigger"], p["priority_type"], p["commands"])
                     for p in stored["pages"]],
                    [(p["trigger"], p["priority_type"], p["commands"])
                     for p in spec["pages"]])
                self.assertEqual(stored["name"], CASE_NAMES[key])

    def test_a_vx_chest_writes_the_same_commands_as_ace(self):
        self.call("event_template", op="create", template="treasure_chest",
                  map_id=2, x=5, y=5,
                  parameters={"reward_type": "item", "reward_id": 1,
                              "amount": 3})
        stored = shape(self.reopen().project.load_map(2).get("events").get(2),
                       self.project.codec)
        self.assertEqual(stored["pages"][0]["commands"],
                         [(0, 126, [1, 0, 0, 3])] + show("Found Potion x3!")
                         + [(0, 123, ["A", 0]), BLANK])


class TestXPMatrix(EngineMatrix, TemplateCase):
    engine = "xp"

    def test_a_description_does_not_depend_on_the_engine(self):
        self.assertEqual(
            self.call("event_template", op="describe", template="sign"),
            SIGN_DESCRIPTION)

    def test_an_xp_sign_writes_xps_show_text(self):
        self.call("event_template", op="create", template="sign", map_id=1,
                  x=1, y=1, parameters={"lines": ["One", "Two"]})
        page_ = shape(self.reopen().project.load_map(1).get("events").get(3),
                      self.project.codec)["pages"][0]
        self.assertEqual(page_["commands"],
                         [(0, 101, ["One"]), (0, 401, ["Two"]), BLANK])
        self.assertEqual(page_["trigger"], 0)

    def test_an_xp_door_writes_six_transfer_parameters(self):
        self.call("event_template", op="create", template="door", map_id=1,
                  x=1, y=1, parameters={"target_map_id": 2, "target_x": 3,
                                        "target_y": 4, "direction": "down"})
        page_ = shape(self.reopen().project.load_map(1).get("events").get(3),
                      self.project.codec)["pages"][0]
        self.assertEqual(page_["commands"],
                         [(0, 201, [0, 2, 3, 4, 2, 0]), BLANK])
        self.assertEqual(page_["trigger"], 1)

    def test_an_xp_save_point_is_the_raw_352(self):
        self.call("event_template", op="create", template="save_point",
                  map_id=1, x=1, y=1, parameters={"graphic": ""})
        page_ = shape(self.reopen().project.load_map(1).get("events").get(3),
                      self.project.codec)["pages"][0]
        self.assertEqual(page_["commands"],
                         [(0, 101, ["Would you like to save?"]), (0, 352, []),
                          BLANK])

    def test_what_xp_has_no_field_for_is_refused_by_name(self):
        base = dict(op="create", template="sign", map_id=1, x=1, y=1)
        self.refused("parameter graphic_index is not offered on RPG Maker XP",
                     **base, parameters={"lines": ["a"], "graphic_index": 1})
        self.refused("parameter face is not offered on RPG Maker XP",
                     op="create", template="npc_dialogue", map_id=1, x=1,
                     y=1, parameters={"lines": ["a"], "face": "Actor1"})
        self.refused("parameter sound is not offered on RPG Maker XP",
                     op="create", template="door", map_id=1, x=1, y=1,
                     parameters={"target_map_id": 2, "target_x": 1,
                                 "target_y": 1,
                                 "sound": {"name": "Move1"}})
        self.refused("parameter fade is not offered on RPG Maker XP",
                     op="create", template="door", map_id=1, x=1, y=1,
                     parameters={"target_map_id": 2, "target_x": 1,
                                 "target_y": 1, "fade": "white"})

    def test_an_xp_page_is_not_given_a_priority_it_has_no_field_for(self):
        self.call("event_template", op="create", template="sign", map_id=1,
                  x=1, y=1, parameters={"lines": ["a"]})
        page_ = self.reopen().project.load_map(1).get("events").get(3) \
            .get("pages")[0]
        self.assertFalse(page_.has("priority_type"))

    def test_an_item_reward_is_refused_by_the_dsls_own_xp_sentence(self):
        self.refused("RPG Maker XP", "item", op="create",
                     template="treasure_chest", map_id=2, x=1, y=1,
                     parameters={"reward_type": "item", "reward_id": 1})


# ---------------------------------------------------------------------------
# The template engine on its own
# ---------------------------------------------------------------------------


class TestExpansion(unittest.TestCase):

    def test_a_hole_is_filled_with_a_copy(self):
        values = {"lines": ["a"]}
        out = templates._expand({"text": {"$": "lines"}}, values)
        out["text"].append("b")
        self.assertEqual(values, {"lines": ["a"]})

    def test_a_member_that_resolves_to_nothing_is_left_out(self):
        self.assertEqual(
            templates._expand({"a": {"$": "x"}, "b": 1}, {"x": None}),
            {"b": 1})

    def test_when_keeps_its_entries_only_for_a_present_value(self):
        node = [{"$when": "x", "then": [1, 2], "else": [3]}, 4]
        for value, expected in ((None, [3, 4]), (False, [3, 4]),
                                ("", [3, 4]), ([], [3, 4]),
                                (0, [1, 2, 4]), (True, [1, 2, 4]),
                                ("a", [1, 2, 4])):
            with self.subTest(value=value):
                self.assertEqual(templates._expand(node, {"x": value}),
                                 expected)

    def test_when_without_else_leaves_nothing(self):
        self.assertEqual(
            templates._expand([{"$when": "x", "then": [1]}], {"x": None}), [])

    def test_fmt_fills_from_values_and_does_not_reformat_a_value(self):
        out = templates._expand({"$fmt": "{a} (return)"}, {"a": "{b} %s"})
        self.assertEqual(out, "{b} %s (return)")

    def test_expanding_does_not_alter_the_table(self):
        before = json.dumps(templates.TEMPLATES, sort_keys=True)
        templates._expand(copy.deepcopy(templates.TEMPLATES["sign"]["events"]),
                          {"event_name": "x", "graphic": None,
                           "graphic_index": None, "lines": ["a"]})
        self.assertEqual(json.dumps(templates.TEMPLATES, sort_keys=True),
                         before)


class TestTheToolIsRegistered(unittest.TestCase):

    def test_it_is_a_writing_tool_with_the_four_ops(self):
        tool = REGISTRY["event_template"]
        self.assertTrue(tool.writes)
        self.assertEqual(tool.schema["properties"]["op"]["enum"],
                         ["list", "describe", "create", "preview"])
        self.assertEqual(tool.schema["properties"]["template"]["enum"],
                         EVERY_TEMPLATE)

    def test_the_description_advertises_its_ceilings(self):
        text = REGISTRY["event_template"].description
        for number in (templates.MAX_TEMPLATE_LINES,
                       templates.MAX_TEMPLATE_LINE_CHARS,
                       templates.MAX_TEMPLATE_NAME_CHARS,
                       templates.MAX_TEMPLATE_ASSET_CHARS):
            self.assertIn(str(number), text)
        self.assertIn("40 keys",
                      REGISTRY["event_template"]
                      .schema["properties"]["parameters"]["description"])

    def test_the_op_table_names_every_schema_argument(self):
        declared = set(REGISTRY["event_template"].schema["properties"])
        self.assertEqual(set().union(*templates.TEMPLATE_OP_ARGS.values()),
                         declared)


class TestAMapWithTooManyEventsIsRefusedNotWalkedForever(TemplateCase):

    def test_the_scan_stops_and_says_so(self):
        original = templates._SCAN_STEPS
        self.addCleanup(setattr, templates, "_SCAN_STEPS", original)
        templates._SCAN_STEPS = 1
        before = self.map_bytes(1)
        self.refused("map 1 has more events than this tool will scan (1)",
                     op="create", template="sign", map_id=1, x=1, y=1,
                     parameters={"lines": ["a"]})
        self.assertEqual(self.map_bytes(1), before)

    def test_a_crafted_event_table_is_read_without_crashing(self):
        game_map = self.project.load_map(1)
        events = game_map.get("events")
        text = self.project.codec.make_string("a")
        events.set(text, RubyObject("RPG::Event", {}))
        events.set(7, self.project.codec.make_string("not an event"))
        events.set(8, RubyObject("RPG::Event", {"@x": text, "@y": 2}))
        out = self.call("event_template", op="create", template="sign",
                        map_id=1, x=1, y=1, parameters={"lines": ["a"]})
        self.assertEqual(out["created"][0]["event_id"], 3)


if __name__ == "__main__":
    unittest.main()
