"""Marshal codec tests.

The fixtures below are the exact byte strings Ruby's ``Marshal.dump`` produces
for each value. They are the closest thing to a conformance suite available
without a Ruby interpreter on the machine, and they pin down the parts of the
format that are easy to get subtly wrong: the variable-width integer packing,
the separate symbol and object link tables, and Ruby 1.8's bare strings.
"""

import math
import unittest

from rpgvxace_mcp.marshal_rb import MarshalError, dump, load
from rpgvxace_mcp.rubytypes import (
    Color,
    RubyFloat,
    RubyHash,
    RubyObject,
    RubyString,
    RubySymbol,
    RubyUserDef,
    Table,
)

# value -> exactly what Ruby writes for it
CANONICAL = [
    (None, b"\x04\x080"),
    (True, b"\x04\x08T"),
    (False, b"\x04\x08F"),
    (0, b"\x04\x08i\x00"),
    (1, b"\x04\x08i\x06"),
    (-1, b"\x04\x08i\xfa"),
    (122, b"\x04\x08i\x7f"),
    (123, b"\x04\x08i\x01{"),
    (-123, b"\x04\x08i\x80"),
    (-124, b"\x04\x08i\xff\x84"),
    (256, b"\x04\x08i\x02\x00\x01"),
    (0x3FFFFFFF, b"\x04\x08i\x04\xff\xff\xff?"),
    (RubySymbol("foo"), b"\x04\x08:\x08foo"),
    (RubyString(b"hi"), b'\x04\x08"\x07hi'),
    ([1, 2, 3], b"\x04\x08[\x08i\x06i\x07i\x08"),
    (RubyHash(), b"\x04\x08{\x00"),
    (RubyHash([(1, 2)]), b"\x04\x08{\x06i\x06i\x07"),
    (RubyFloat(1.5), b"\x04\x08f\x081.5"),
    (RubyObject("Foo", {"@a": 1}), b"\x04\x08o:\x08Foo\x06:\x07@ai\x06"),
]


class TestCanonicalBytes(unittest.TestCase):
    def test_dump_matches_ruby(self):
        for value, expected in CANONICAL:
            with self.subTest(value=value):
                self.assertEqual(dump(value), expected)

    def test_load_matches_ruby(self):
        for expected, encoded in CANONICAL:
            with self.subTest(encoded=encoded):
                self.assertEqual(load(encoded), expected)

    def test_integer_boundaries_round_trip(self):
        # The five branches of Ruby's packed-integer encoding, plus the Fixnum
        # ceiling where Ruby switches to Bignum.
        for value in (0, 1, 122, 123, 255, 256, 65535, 65536, 0x3FFFFFFF,
                      -1, -123, -124, -255, -256, -65536, -(2 ** 30)):
            with self.subTest(value=value):
                self.assertEqual(load(dump(value)), value)

    def test_bignum_round_trips(self):
        for value in (2 ** 30, 2 ** 40, -(2 ** 30) - 1, 2 ** 64 + 12345):
            with self.subTest(value=value):
                self.assertEqual(load(dump(value)), value)

    def test_bignum_uses_bignum_tag(self):
        self.assertEqual(dump(2 ** 30)[2:3], b"l")
        self.assertEqual(dump(2 ** 30 - 1)[2:3], b"i")


class TestLinkTables(unittest.TestCase):
    def test_repeated_symbol_becomes_symlink(self):
        self.assertEqual(dump([RubySymbol("a"), RubySymbol("a")]),
                         b"\x04\x08[\x07:\x06a;\x00")

    def test_repeated_object_becomes_link(self):
        shared = RubyString(b"x")
        self.assertEqual(dump([shared, shared]), b'\x04\x08[\x07"\x06x@\x06')

    def test_link_preserves_sharing_on_load(self):
        loaded = load(b'\x04\x08[\x07"\x06x@\x06')
        self.assertIs(loaded[0], loaded[1])

    def test_floats_occupy_link_slots(self):
        # Ruby gives Floats object ids, so a link after one must resolve past it.
        shared = RubyString(b"s")
        loaded = load(dump([RubyFloat(1.0), shared, shared]))
        self.assertIs(loaded[1], loaded[2])
        self.assertEqual(loaded[1].data, b"s")

    def test_symbol_and_object_tables_are_independent(self):
        shared = RubyString(b"v")
        value = [RubySymbol("k"), shared, RubySymbol("k"), shared]
        loaded = load(dump(value))
        self.assertEqual(loaded[0], RubySymbol("k"))
        self.assertIs(loaded[1], loaded[3])


class TestStrings(unittest.TestCase):
    def test_strings_are_written_bare_for_ruby_18(self):
        # An 'I' wrapper here would make RGSS2 (Ruby 1.8) refuse the file.
        self.assertEqual(dump(RubyString(b"abc"))[2:3], b'"')

    def test_ivar_wrapper_is_preserved_when_present(self):
        # Ruby 1.9+ style: I"...:E T
        encoded = b'\x04\x08I"\x07hi\x06:\x06ET'
        loaded = load(encoded)
        self.assertEqual(loaded.data, b"hi")
        self.assertEqual(loaded.ivars, {"E": True})
        self.assertEqual(dump(loaded), encoded)

    def test_arbitrary_bytes_survive(self):
        raw = bytes(range(256))
        self.assertEqual(load(dump(RubyString(raw))).data, raw)

    def test_python_str_is_refused(self):
        with self.assertRaises(MarshalError):
            dump("ambiguous encoding")


class TestFloats(unittest.TestCase):
    def test_original_text_is_reused_when_unchanged(self):
        # Ruby 1.8 sometimes wrote extra mantissa bits after a NUL; echoing the
        # bytes back keeps untouched files byte-identical.
        encoded = b"\x04\x08f\x0d1.5\x00\x8f\xc2\xf5("
        loaded = load(encoded)
        self.assertEqual(float(loaded), 1.5)
        self.assertEqual(dump(loaded), encoded)

    def test_edited_float_is_reformatted(self):
        self.assertEqual(dump(RubyFloat(2.5, b"1.5")), b"\x04\x08f\x082.5")

    def test_specials(self):
        self.assertEqual(dump(float("inf")), b"\x04\x08f\x08inf")
        self.assertEqual(dump(float("-inf")), b"\x04\x08f\x09-inf")
        self.assertTrue(math.isnan(load(dump(float("nan")))))


class TestRGSSTypes(unittest.TestCase):
    def test_table_payload_layout(self):
        # dim, xsize, ysize, zsize, size as 5 little-endian uint32, then uint16 cells.
        table = Table(2, 2, 1, data=[1, 2, 3, 4])
        self.assertEqual(
            table.to_payload(),
            b"\x02\x00\x00\x00\x02\x00\x00\x00\x02\x00\x00\x00\x01\x00\x00\x00"
            b"\x04\x00\x00\x00\x01\x00\x02\x00\x03\x00\x04\x00",
        )

    def test_table_round_trips_through_marshal(self):
        table = Table(3, 4, 3)
        table.set(1, 2, 1, 1234)
        encoded = dump(table)
        self.assertIn(b"Table", encoded)
        loaded = load(encoded)
        self.assertEqual((loaded.xsize, loaded.ysize, loaded.zsize), (3, 4, 3))
        self.assertEqual(loaded.get(1, 2, 1), 1234)
        self.assertEqual(dump(loaded), encoded)

    def test_table_addressing_is_column_major_per_layer(self):
        table = Table(4, 3, 2)
        table.set(3, 2, 1, 7)
        self.assertEqual(table.data[3 + 2 * 4 + 1 * 12], 7)

    def test_table_signed_view(self):
        table = Table(1, 1, 1, data=[0xFFFF])
        self.assertEqual(table.get(0, 0, 0), 0xFFFF)
        self.assertEqual(table.get(0, 0, 0, signed=True), -1)

    def test_table_rejects_inconsistent_header(self):
        bad = b"\x03\x00\x00\x00\x02\x00\x00\x00\x02\x00\x00\x00\x01\x00\x00\x00\x09\x00\x00\x00"
        with self.assertRaises(ValueError):
            Table.from_payload(bad)

    def test_corrupt_table_degrades_to_opaque_bytes(self):
        # A file we cannot fully understand must still be readable and rewritable.
        encoded = dump(RubyUserDef("Table", b"\x01\x02\x03"))
        value, warnings = load(encoded, with_warnings=True)
        self.assertIsInstance(value, RubyUserDef)
        self.assertTrue(warnings)
        self.assertEqual(dump(value), encoded)

    def test_color_round_trips(self):
        color = Color([255.0, 128.0, 0.0, 255.0])
        loaded = load(dump(color))
        self.assertEqual(loaded.as_dict(),
                         {"red": 255.0, "green": 128.0, "blue": 0.0, "alpha": 255.0})

    def test_table_resize_keeps_overlap(self):
        table = Table(3, 3, 1, data=[1, 2, 3, 4, 5, 6, 7, 8, 9])
        bigger = table.resize(4, 4, 1)
        self.assertEqual(bigger.get(2, 2, 0), 9)
        self.assertEqual(bigger.get(3, 3, 0), 0)
        smaller = table.resize(2, 2, 1)
        self.assertEqual(smaller.data, [1, 2, 4, 5])


class TestUnknownConstructs(unittest.TestCase):
    def test_unknown_userdef_passes_through(self):
        encoded = dump(RubyUserDef("Mystery::Thing", b"\x00\xff\x10"))
        self.assertEqual(dump(load(encoded)), encoded)

    def test_nested_objects_round_trip(self):
        actor = RubyObject("RPG::Actor", {
            "@id": 1,
            "@name": RubyString(b"Ralph"),
            "@parameters": Table(6, 100, 1),
            "@equips": [1, 2, 0, 0, 0],
        })
        encoded = dump(RubyHash([(1, actor), (2, None)]))
        self.assertEqual(dump(load(encoded)), encoded)

    def test_hash_with_default_uses_its_own_tag(self):
        encoded = dump(RubyHash([(1, 2)], default=0))
        self.assertEqual(encoded[2:3], b"}")
        self.assertEqual(load(encoded).default, 0)

    def test_rejects_wrong_version(self):
        with self.assertRaises(MarshalError):
            load(b"\x04\x09i\x06")

    def test_rejects_truncated_data(self):
        with self.assertRaises(MarshalError):
            load(b"\x04\x08[\x08i\x06")

    def test_reports_trailing_bytes(self):
        _, warnings = load(b"\x04\x08i\x06\xff\xff", with_warnings=True)
        self.assertTrue(any("trailing" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
