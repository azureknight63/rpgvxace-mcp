"""`convert` takes values out of untrusted files and out of a caller's JSON.

Each class here pins one way a value could be mishandled that a sweep found:
names that Ruby would refuse getting through on a trailing newline, a Shift-JIS
string whose bytes do not come back, a string linked many times costing the
size of every copy, a caller's text writing bytes it must not, and a value
nested so deep the server could write it and not read it back.
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rpgvxace_mcp import convert  # noqa: E402
from rpgvxace_mcp.convert import (  # noqa: E402
    ConversionError,
    MAX_JSON_DEPTH,
    TextCodec,
    check_class_name,
    check_ivar_name,
    from_json,
    table_from_json,
    to_json,
)
from rpgvxace_mcp.rubytypes import RubyString, RubyUserDef  # noqa: E402


class TestANameWithATrailingNewlineIsRefused(unittest.TestCase):
    """`$` matches before a final newline, so "a\\n" passed as a name.

    The name is written into the Marshal stream as given, newline and all,
    which is the file the editor cannot open that the checks exist to prevent.
    """

    def test_an_ivar_name(self):
        for name in ("a\n", "@a\n", "name\n"):
            with self.subTest(name=name), self.assertRaises(ConversionError):
                check_ivar_name(name)

    def test_a_class_name(self):
        for name in ("RPG::Item\n", "Foo\n", "RPG::\n"):
            with self.subTest(name=name), self.assertRaises(ConversionError):
                check_class_name(name)

    def test_ordinary_names_still_pass(self):
        self.assertEqual(check_ivar_name("name"), "name")
        self.assertEqual(check_ivar_name("@name"), "@name")
        self.assertEqual(check_class_name("RPG::Item"), "RPG::Item")


class TestAStringThatDoesNotComeBackTravelsAsBytes(unittest.TestCase):
    """cp932 decodes both bytes of a duplicate pair to one character.

    NEC row 13 holds characters JIS X 0208 also has: 0x8790 and 0x81E0 both
    decode to U+2252, and the encoder writes 0x81E0 back. A string holding
    the other pair came back changed by any patch that carried it.
    """

    def codec(self):
        return TextCodec("cp932")

    def test_the_other_byte_pair_is_kept_exactly(self):
        original = RubyString(bytes([0x87, 0x90]))
        shown = to_json(original, self.codec())
        self.assertIsInstance(shown, dict)
        self.assertIn("__bytes_base64__", shown)
        back = from_json(shown, self.codec())
        self.assertEqual(back.data, original.data)

    def test_ordinary_text_stays_text(self):
        for data in (b"plain ascii", "テスト".encode("cp932"),
                     bytes([0x81, 0xE0])):
            with self.subTest(data=data):
                shown = to_json(RubyString(data), self.codec())
                self.assertIsInstance(shown, str)
                back = from_json(shown, self.codec())
                self.assertEqual(back.data, data)

    def test_utf8_is_unaffected(self):
        codec = TextCodec("utf-8")
        self.assertEqual(to_json(RubyString("≒".encode("utf-8")), codec),
                         "≒")


class TestASharedStringIsChargedBySize(unittest.TestCase):
    """A Marshal link returns the same object, and each reference was decoded.

    One 4 MB string linked 5,000 times is a few megabytes on disk and built
    20 GB of reply before anything refused it: the walk budget counted nodes,
    and each of those was one.
    """

    def test_many_references_to_one_big_string_are_refused(self):
        big = RubyString(b"A" * 4_000_000)
        started = time.monotonic()
        with self.assertRaises(ConversionError):
            to_json([big] * 5000, TextCodec("utf-8"))
        self.assertLess(time.monotonic() - started, 20)

    def test_many_references_to_one_big_payload_are_refused(self):
        payload = RubyUserDef("Foo", b"B" * 4_000_000)
        with self.assertRaises(ConversionError):
            to_json([payload] * 5000, TextCodec("utf-8"))

    def test_a_reply_of_ordinary_text_is_not_newly_refused(self):
        """Japanese text at three bytes a character, well under the ceiling."""
        codec = TextCodec("utf-8")
        line = RubyString(("あ" * 30).encode("utf-8"))
        shown = to_json([line] * 20_000, codec)
        self.assertEqual(len(shown), 20_000)

    def test_one_large_string_alone_is_still_converted(self):
        codec = TextCodec("utf-8")
        text = to_json(RubyString(("あ" * 33_000).encode("utf-8")), codec)
        self.assertEqual(len(text), 33_000)


class TestACallersTextIsEncodedStrictly(unittest.TestCase):
    """`__string__` is only produced from a strict decode, and read back so."""

    def test_a_lone_surrogate_cannot_write_a_raw_byte(self):
        with self.assertRaises(ConversionError):
            from_json({"__string__": "\udcff"}, TextCodec("utf-8"))

    def test_ordinary_text_round_trips(self):
        back = from_json({"__string__": "café"}, TextCodec("utf-8"))
        self.assertEqual(back.data, "café".encode("utf-8"))


class TestANumberTheCallerSendsIsAWholeNumber(unittest.TestCase):
    """`int(...)` takes a float, a bool, a numeric string and raises on inf."""

    def cells(self, cells, **sizes):
        value = {"xsize": 2, "ysize": 1, "zsize": 1, "cells": cells}
        value.update(sizes)
        return table_from_json(value)

    def test_a_fractional_cell_is_refused_not_truncated(self):
        with self.assertRaises(ConversionError):
            self.cells([1.9, 2])

    def test_a_bool_or_a_string_is_not_a_cell(self):
        for cell in (True, "7", None):
            with self.subTest(cell=cell), self.assertRaises(ConversionError):
                self.cells([cell, 2])

    def test_infinity_is_a_conversion_error(self):
        with self.assertRaises(ConversionError):
            self.cells([1, 2], xsize=float("inf"))
        with self.assertRaises(ConversionError):
            self.cells([float("inf"), 2])

    def test_a_whole_float_is_accepted_as_json_has_no_integer_type(self):
        table = self.cells([1.0, 2.0], xsize=2.0)
        self.assertEqual(list(table.data), [1, 2])


class TestAValueNestedTooDeeplyIsRefused(unittest.TestCase):
    """The writer has no depth limit; the reader refuses past 192.

    A value nested a few hundred levels deep was written and the file then
    could not be opened through the server.
    """

    def nest(self, depth):
        value = 1
        for _ in range(depth):
            value = [value]
        return value

    def test_deep_is_refused(self):
        with self.assertRaises(ConversionError):
            from_json(self.nest(MAX_JSON_DEPTH * 4), TextCodec("utf-8"))

    def test_ordinary_nesting_is_fine(self):
        from_json(self.nest(MAX_JSON_DEPTH // 2), TextCodec("utf-8"))


class TestAMarkerHasTheShapeItPromises(unittest.TestCase):
    """The reversible forms are read from a caller: check what they hold."""

    codec = TextCodec("utf-8")

    def test_a_symbol_is_a_string(self):
        with self.assertRaises(ConversionError):
            from_json({"__symbol__": 123}, self.codec)

    def test_hash_entries_are_pairs(self):
        with self.assertRaises(ConversionError):
            from_json({"__hash__": ["ab"]}, self.codec)

    def test_extended_modules_are_a_list_of_names(self):
        with self.assertRaises(ConversionError):
            from_json({"__extended__": "Foo", "inner": 1}, self.codec)


if __name__ == "__main__":
    unittest.main()
