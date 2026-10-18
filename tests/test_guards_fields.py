"""Guards on inventing a field the editor will not read.

RGSS subscripts its objects by instance-variable name, so a field this
server invents is one nothing in the game ever looks at -- and the reply
says the edit landed. Every path that accepts a patch or a wholesale value
is checked here, including the containers that used to hide one.
"""

import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root


from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
)
from rpgvxace_mcp import convert  # noqa: E402
from rpgvxace_mcp.convert import ConversionError  # noqa: E402
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    ToolError,
)

from tool_case import (  # noqa: E402
    ToolTest,
)


class TestAtPrefixedFieldsAreRefused(ToolTest):
    """Ruby ivars are written @width, and these patches accept either
    spelling, so a refusal list checked against the raw key let the @ form
    through every one of the three guards that shared that shape."""

    def test_a_map_patch_cannot_reach_a_structural_field(self):
        for key in ("width", "@width", "data", "@data"):
            with self.subTest(key=key):
                with self.assertRaises(ToolError) as caught:
                    self.call("map_edit", op="update", map_id=1,
                              patch={key: 99})
                self.assertIn("rest of the map", str(caught.exception))

    def test_a_page_patch_cannot_reach_the_command_list(self):
        game_map = self.project.load_map(1)
        event_id = game_map.get("events").sorted_items()[0][0]
        for key in ("list", "@list"):
            with self.subTest(key=key):
                with self.assertRaises(ToolError) as caught:
                    self.call("event_edit", map_id=1, ops=[
                        {"op": "set_page", "event_id": event_id, "page": 0,
                         "patch": {key: []}}])
                self.assertIn("set_commands", str(caught.exception))

    def test_raw_data_cannot_reach_a_structural_field(self):
        for path in ("width", "@width", "events", "@events"):
            with self.subTest(path=path):
                with self.assertRaises(ToolError) as caught:
                    self.call("raw_data", op="set", map_id=1, path=path,
                              value={"value": 0})
                self.assertIn("agree with it", str(caught.exception))


class TestAPatchCannotInventAField(ToolTest):
    """apply_patch only checks that a name is a valid Ruby ivar, so a
    plausible typo used to add an instance variable and be reported back as a
    successful change -- while raw_data, the deliberately unvalidated escape
    hatch, refused exactly that on the grounds that the editor rejects an
    object carrying ivars it does not expect."""

    def test_a_misspelled_field_is_refused_and_the_real_one_listed(self):
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items",
                      ops=[{"op": "update", "id": 1, "patch": {"pirce": 900}}])
        message = str(caught.exception)
        self.assertIn("no field", message)
        self.assertIn("price", message, "the message should list the real one")

    def test_the_real_field_still_works_in_either_spelling(self):
        for key in ("price", "@price"):
            with self.subTest(key=key):
                self.call("db_edit", table="items",
                          ops=[{"op": "update", "id": 1, "patch": {key: 900}}])
        self.assertEqual(self.reopen().project.get_entry("items", 1).get("price"),
                         900)

    def test_a_blank_create_is_judged_against_a_sibling(self):
        """A blank entry has no fields of its own, so what the table's other
        entries carry is the only authority for this class."""
        with self.assertRaises(ToolError) as caught:
            self.call("db_edit", table="items",
                      ops=[{"op": "create", "patch": {"nmae": "X"}}])
        self.assertIn("no field", str(caught.exception))
        self.call("db_edit", table="items",
                  ops=[{"op": "create", "patch": {"name": "Fine"}}])

    def test_a_map_patch_is_judged_too(self):
        with self.assertRaises(ToolError) as caught:
            self.call("map_edit", op="update", map_id=1,
                      patch={"disable_dashng": True})
        self.assertIn("no field", str(caught.exception))
        self.call("map_edit", op="update", map_id=1,
                  patch={"disable_dashing": True})


class TestANestedPatchCannotInventAField(ToolTest):
    """A typo one level down must be refused like a typo at the top.

    `refuse_unknown_fields` compares the patch's top-level keys, and
    `apply_patch` merges nested dicts field by field, so
    {"bgm": {"nmae": "Town"}} added @nmae to the RPG::BGM and reported
    `bgm.nmae: <unset> -> "Town"` as the change asked for. The editor rejects
    an object carrying instance variables it does not expect, so that write
    produces a project that will not open.
    """

    def test_a_nested_typo_is_refused(self):
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("map_edit", op="update", map_id=1,
                      patch={"bgm": {"nmae": "Town1"}})
        message = str(caught.exception)
        self.assertIn("nmae", message)
        self.assertIn("RPG::BGM", message)

    def test_the_field_is_not_left_behind(self):
        with self.assertRaises((ToolError, ConversionError)):
            self.call("map_edit", op="update", map_id=1,
                      patch={"bgm": {"nmae": "Town1"}})
        bgm = self.reopen().project.load_map(1).get("bgm")
        self.assertFalse(bgm.has("nmae"))

    def test_a_real_nested_field_still_merges(self):
        out = self.call("map_edit", op="update", map_id=1,
                        patch={"bgm": {"volume": 55}})
        self.assertIn("bgm.volume: 100 -> 55", out["changes"])
        self.assertEqual(self.reopen().project.load_map(1).get("bgm").get("volume"), 55)


class TestAWholesaleObjectCannotInventFields(ToolTest):
    """A value carrying __class__ replaces an object, and its own field names
    were never checked.

    `refuse_unknown_fields` inspects the patch's key *names* against the
    entry's fields, so the top-level key passes; `apply_patch` then sees a
    `__class__` and takes the replacement path, which never reaches
    `_refuse_invented_fields`; `from_json` builds the object with nothing but
    `check_ivar_name` on each key. So a typo inside a replacement object is
    written into the file and reported as the change the caller asked for --
    and the editor then refuses to open the project.

    Reachable three ways, all of them through tools that advertise validation:
    a field's value, an element of an array, and raw_data's whole value.
    """

    def bgm_fields(self):
        return set(self.reopen().project.load_map(1).get("bgm").fields())

    def test_a_typo_in_a_replacement_object_is_refused(self):
        before = self.bgm_fields()
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("map_edit", op="update", map_id=1, patch={
                "bgm": {"__class__": "RPG::BGM", "name": "Town1",
                        "volume": 100, "pitch": 100, "pich": 100}})
        self.assertIn("pich", str(caught.exception))
        self.assertEqual(self.bgm_fields(), before)

    def test_a_typo_inside_an_array_element_is_refused(self):
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("db_edit", table="classes", dry_run=True, ops=[
                {"op": "update", "id": 1, "patch": {"learnings": [
                    {"__class__": "RPG::Class::Learning", "skill_id": 1,
                     "levl": 5}]}}])
        self.assertIn("levl", str(caught.exception))

    def test_raw_data_refuses_one_too(self):
        with self.assertRaises((ToolError, ConversionError)) as caught:
            self.call("raw_data", op="set", map_id=1, path="bgm", value={
                "value": {"__class__": "RPG::BGM", "name": "Town1",
                          "volume": 100, "pitch": 100, "naem": "x"}})
        self.assertIn("naem", str(caught.exception))

    def test_a_correct_replacement_still_works(self):
        out = self.call("map_edit", op="update", map_id=1, patch={
            "bgm": {"__class__": "RPG::BGM", "name": "Cave", "volume": 70,
                    "pitch": 100}})
        self.assertTrue(out["changes"])
        self.assertEqual(self.reopen().project.load_map(1).get("bgm").get("name").data,
                         b"Cave")


class TestEveryNameFromJsonIsChecked(ToolTest):
    """A class or field name reaching the file must be one Ruby would accept.

    `check_ivar_name` and `check_class_name` say a name outside their shape
    "produces a file the editor cannot open". Both were called on exactly one
    of the fourteen forms `_dict_from_json` accepts -- the plain object -- so
    every other form wrote whatever it was given: a struct name, a wrapped
    class name, an extended module list, and the __ivars__ dicts on the string
    forms.
    """

    # A name Ruby cannot resolve, and one Ruby cannot parse as an ivar.
    BAD_CLASS = "not a class!"
    BAD_IVAR = "not an ivar!"

    def convert(self, value):
        return convert.from_json(value, self.project.codec)

    def refuses(self, value, what):
        with self.assertRaises(ConversionError, msg=what) as caught:
            self.convert(value)
        return str(caught.exception)

    def test_a_plain_object_is_still_checked(self):
        """The one form that was already checked, so a fix cannot lose it."""
        self.refuses({"__class__": self.BAD_CLASS}, "plain object class")
        self.refuses({"__class__": "RPG::Item", self.BAD_IVAR: 1},
                     "plain object field")

    def test_a_struct_name_is_checked(self):
        self.refuses({"__struct__": self.BAD_CLASS, "members": {}},
                     "struct name")

    def test_a_struct_member_name_is_checked(self):
        self.refuses({"__struct__": "Foo", "members": {self.BAD_IVAR: 1}},
                     "struct member")

    def test_a_class_ref_is_checked(self):
        self.refuses({"__class_ref__": self.BAD_CLASS}, "class ref")

    def test_a_uclass_name_is_checked(self):
        self.refuses({"__uclass__": self.BAD_CLASS, "inner": []},
                     "uclass name")

    def test_an_extended_module_name_is_checked(self):
        self.refuses({"__extended__": [self.BAD_CLASS], "inner": 1},
                     "extended module")

    def test_a_data_class_name_is_checked(self):
        self.refuses({"__data__": self.BAD_CLASS, "value": 1}, "data class")

    def test_a_strings_ivars_are_checked(self):
        self.refuses({"__string__": "hi", "__ivars__": {self.BAD_IVAR: 1}},
                     "string ivars")

    def test_a_byte_strings_ivars_are_checked(self):
        self.refuses({"__bytes_base64__": "aGk=",
                      "__ivars__": {self.BAD_IVAR: 1}},
                     "byte string ivars")

    def test_an_ivar_wrappers_names_are_checked(self):
        self.refuses({"__ivar_wrapper__": 1, "__ivars__": {self.BAD_IVAR: 1}},
                     "ivar wrapper")

    def test_every_good_form_still_converts(self):
        """The check must not refuse what the reader itself produces.

        Each of these is a shape `to_json` emits, so refusing one would break
        reading a project and writing it back unchanged.
        """
        for value in (
                {"__struct__": "Foo", "members": {"bar": 1}},
                {"__class_ref__": "RPG::Item"},
                {"__class_ref__": "Comparable", "kind": "module"},
                {"__uclass__": "MyArray", "inner": []},
                {"__extended__": ["Comparable"], "inner": 1},
                {"__data__": "MyData", "value": 1},
                {"__string__": "hi", "__ivars__": {"@encoding": 1}},
                {"__string__": "hi", "__ivars__": {"plain": 1}},
                {"__bytes_base64__": "aGk=", "__ivars__": {"@e": 1}},
                {"__ivar_wrapper__": 1, "__ivars__": {"@a": 2}},
                {"__class__": "RPG::Item", "name": "x"},
        ):
            with self.subTest(value=value):
                self.assertIsNotNone(self.convert(value))

    def test_the_real_project_still_round_trips_through_json(self):
        """The strongest form of the previous test: real editor output."""
        raw = self.project.load_data("items")
        rendered = convert.to_json(raw, self.project.codec, "full",
                                   convert.new_walk())
        self.assertIsNotNone(self.convert(rendered))


class TestAnInventedFieldCannotHideInAContainer(ToolTest):
    """The check reaches objects and lists. The format has hashes too.

    `refuse_invented_in_replacement`'s docstring says a wholesale replacement
    is "compared against the value being replaced", with the only stated
    escape being that there is nothing to compare against. A RubyHash was a
    second, unstated escape: the walk recursed into RubyObject fields and into
    list elements that were RubyObjects, and returned silently for everything
    else. A map's `events` is a RubyHash keyed by event id, so
    `{"1": {"__class__": "RPG::Event", "naem": ...}}` went to disk unexamined
    -- and a list holding lists was skipped the same way.
    """

    def refuse(self, existing, replacement):
        return convert.refuse_invented_in_replacement(
            existing, replacement, "the value")

    def event_like(self, **fields):
        """An RPG::Event with the fields given, as the editor writes them."""
        obj = RubyObject("RPG::Event")
        for name, value in fields.items():
            obj.set(name, value)
        return obj

    def test_a_typo_inside_a_hash_value_is_refused(self):
        existing = RubyHash([(1, self.event_like(id=1, name="A", x=0, y=0))])
        replacement = RubyHash([(1, self.event_like(id=1, naem="A", x=0, y=0))])
        with self.assertRaises(ConversionError) as caught:
            self.refuse(existing, replacement)
        self.assertIn("naem", str(caught.exception))

    def test_a_correct_hash_value_is_allowed(self):
        existing = RubyHash([(1, self.event_like(id=1, name="A", x=0, y=0))])
        replacement = RubyHash([(1, self.event_like(id=1, name="B", x=1, y=0))])
        self.assertIsNone(self.refuse(existing, replacement))

    def test_a_hash_key_with_no_twin_is_not_judged(self):
        """The stated escape still applies: no authority, no refusal."""
        existing = RubyHash([(1, self.event_like(id=1, name="A"))])
        replacement = RubyHash([(7, self.event_like(id=7, naem="A"))])
        self.assertIsNone(self.refuse(existing, replacement))

    def test_a_typo_inside_a_nested_list_is_refused(self):
        existing = [[self.event_like(id=1, name="A")]]
        replacement = [[self.event_like(id=1, naem="A")]]
        with self.assertRaises(ConversionError) as caught:
            self.refuse(existing, replacement)
        self.assertIn("naem", str(caught.exception))

    def test_a_typo_inside_a_hash_inside_a_list_is_refused(self):
        existing = [RubyHash([(1, self.event_like(id=1, name="A"))])]
        replacement = [RubyHash([(1, self.event_like(id=1, naem="A"))])]
        with self.assertRaises(ConversionError) as caught:
            self.refuse(existing, replacement)
        self.assertIn("naem", str(caught.exception))

    def test_the_one_reachable_hash_is_closed_by_a_narrower_guard(self):
        """Why this is a latent gap and not a live hole, recorded as a test.

        The format has two hash locations: a map's `events` and the MapInfos
        file. raw_data refuses to replace `events` at all, for a reason that
        has nothing to do with invented fields -- the rest of the map has to
        agree with it -- and no patch path reaches MapInfos whole. So the hash
        branch above was covered by coincidence rather than by the rule its
        docstring states. If that narrower refusal is ever relaxed, this test
        fails and says which other layer has to hold.
        """
        broken = {"__hash__": [[1, {"__class__": "RPG::Event", "naem": "x"}]]}
        with self.assertRaises(ToolError) as caught:
            self.call("raw_data", op="set", map_id=1, path="events",
                      value={"value": broken}, dry_run=True)
        self.assertIn("cannot be replaced through raw_data",
                      str(caught.exception))

    def test_the_depth_limit_still_holds(self):
        """A deep structure must stop, not recurse until the stack goes."""
        existing = replacement = None
        for _ in range(convert.MAX_JSON_DEPTH + 50):
            existing = RubyHash([(1, existing)])
            replacement = RubyHash([(1, replacement)])
        self.assertIsNone(self.refuse(existing, replacement))


class TestEveryConversionBranchIsCallable(unittest.TestCase):
    """A branch that raises TypeError when reached is not a handled case.

    `_to_json_value` handles a bare `bytes` by calling
    `_string_to_json(RubyString(bytes(value)), codec)` -- two arguments to a
    function that takes four. Nothing in the reader produces a bare `bytes`
    today, so the line has never run; it reads as a handled case and is a
    crash waiting for the first caller that makes one.
    """

    def test_a_bare_bytes_value_converts(self):
        codec = convert.TextCodec("utf-8")
        rendered = convert.to_json(b"hello", codec, "full", convert.new_walk())
        self.assertIn("hello", str(rendered))

    def test_a_bytearray_value_converts(self):
        codec = convert.TextCodec("utf-8")
        rendered = convert.to_json(bytearray(b"hi"), codec, "full",
                                   convert.new_walk())
        self.assertIn("hi", str(rendered))

    def test_it_agrees_with_the_ruby_string_branch(self):
        """A bare bytes and the RubyString the reader would have built."""
        codec = convert.TextCodec("utf-8")
        from rpgvxace_mcp.rubytypes import RubyString
        bare = convert.to_json(b"hello", codec, "full", convert.new_walk())
        wrapped = convert.to_json(RubyString(b"hello"), codec, "full",
                                  convert.new_walk())
        self.assertEqual(bare, wrapped)
