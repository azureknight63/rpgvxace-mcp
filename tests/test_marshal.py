"""Marshal codec tests.

The fixtures below are the exact byte strings Ruby's ``Marshal.dump`` produces
for each value. They are the closest thing to a conformance suite available
without a Ruby interpreter on the machine, and they pin down the parts of the
format that are easy to get subtly wrong: the variable-width integer packing,
the separate symbol and object link tables, and Ruby 1.8's bare strings.
"""

import math
import struct
import unittest

from rpgvxace_mcp.marshal_rb import (
    MarshalError,
    MarshalReader,
    dump,
    load,
    load_with_warnings,
)
from rpgvxace_mcp.rubytypes import (
    Color,
    RubyClassRef,
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
        value, warnings = load_with_warnings(encoded)
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
        self.assertEqual(list(smaller.data), [1, 2, 4, 5])


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
        _, warnings = load_with_warnings(b"\x04\x08i\x06\xff\xff")
        self.assertTrue(any("trailing" in w for w in warnings))


class TestHostileInput(unittest.TestCase):
    """A corrupt or crafted file must fail cleanly, not quietly.

    Every case here decodes to something plausible-looking, or hangs, or
    allocates without bound, if its guard is removed -- and the value would then
    be written back over the user's game. The proof that each guard still checks
    something is `scripts/prove_guards.py`, which reverts each one and requires
    the matching test to fail.
    """

    def test_rejects_negative_object_link(self):
        # Python would read index -1 as "the last entry" and hand back a
        # plausible but wrong object.
        with self.assertRaises(MarshalError) as caught:
            load(b'\x04\x08[\x07"\x06x@\xfa')
        self.assertIn("object link -1", str(caught.exception))

    def test_rejects_negative_symbol_link(self):
        with self.assertRaises(MarshalError) as caught:
            load(b"\x04\x08[\x07:\x06a;\xfa")
        self.assertIn("symbol link -1", str(caught.exception))

    def test_rejects_out_of_range_object_link(self):
        with self.assertRaises(MarshalError) as caught:
            load(b"\x04\x08@\x08")
        self.assertIn("out of range", str(caught.exception))

    def test_rejects_negative_array_count(self):
        # range(-1) is empty, so without the guard this is a silent empty array.
        with self.assertRaises(MarshalError) as caught:
            load(b"\x04\x08[\xfa")
        self.assertIn("negative array element count", str(caught.exception))

    def test_rejects_negative_hash_count(self):
        with self.assertRaises(MarshalError) as caught:
            load(b"\x04\x08{\xfa")
        self.assertIn("negative hash pair count", str(caught.exception))

    def test_rejects_negative_ivar_count(self):
        with self.assertRaises(MarshalError) as caught:
            load(b"\x04\x08o:\x08Foo\xfa")
        self.assertIn("negative instance variable count", str(caught.exception))

    def test_rejects_excessive_nesting(self):
        # 300 nested arrays. Without the depth cap this is a RecursionError
        # raised from an arbitrary point in the parse, which is not a
        # MarshalError and so escapes the caller's error handling.
        deep = b"\x04\x08" + b"[\x06" * 300 + b"0"
        with self.assertRaises(MarshalError) as caught:
            load(deep)
        self.assertIn("nested more than", str(caught.exception))

    def test_rejects_bad_bignum_sign(self):
        with self.assertRaises(MarshalError) as caught:
            load(b"\x04\x08lX\x06\x01\x00")
        self.assertIn("neither '+' nor '-'", str(caught.exception))

    def test_table_with_absurd_extents_degrades_to_opaque_bytes(self):
        # xsize 0xFFFFFFFF with ysize 0 has a product of 0, so a product-only
        # check accepts it -- and every later consumer iterates 4 billion times.
        payload = struct.pack("<5I", 3, 0xFFFFFFFF, 0, 0, 0)
        encoded = dump(RubyUserDef("Table", payload))
        value, warnings = load_with_warnings(encoded)
        self.assertIsInstance(value, RubyUserDef)
        self.assertTrue(any("could not decode Table" in w for w in warnings))
        self.assertEqual(dump(value), encoded)

    def test_table_with_impossible_dimension_degrades(self):
        payload = struct.pack("<5I", 7, 1, 1, 1, 1) + b"\x00\x00"
        value, warnings = load_with_warnings(dump(RubyUserDef("Table", payload)))
        self.assertIsInstance(value, RubyUserDef)
        self.assertTrue(any("dimensions" in w for w in warnings))

    def test_table_with_trailing_payload_bytes_degrades(self):
        # Accepting these would mean to_payload silently drops them.
        payload = struct.pack("<5I", 1, 1, 1, 1, 1) + b"\x07\x00" + b"\xff"
        encoded = dump(RubyUserDef("Table", payload))
        value, _ = load_with_warnings(encoded)
        self.assertIsInstance(value, RubyUserDef)
        self.assertEqual(dump(value), encoded)

    def test_table_refuses_an_out_of_range_axis(self):
        # Reachable from the map resize tool, which only checks for >= 1.
        with self.assertRaises(ValueError) as caught:
            Table(100000, 100000, 1)
        self.assertIn("xsize of 100000 exceeds", str(caught.exception))

    def test_table_refuses_an_absurd_cell_count(self):
        # Each axis is individually legal; the product is 3.6 billion cells.
        with self.assertRaises(ValueError) as caught:
            Table(60000, 60000, 1)
        self.assertIn("above the", str(caught.exception))



    def test_rejects_a_deeply_nested_symbol(self):
        """A symbol in a name position can carry an encoding record.

        `_read_symbol_ref` recurses on that record and did not consult
        MAX_DEPTH, so a small file raised RecursionError -- a RuntimeError,
        which is not a MarshalError and so escaped every caller between the
        reader and `main`, ending the session on a traceback.
        """
        payload = b"\x04\x08" + b"o" + b"I" * 1200 + b":\x08Foo\x00"
        with self.assertRaises(MarshalError) as caught:
            load(payload)
        self.assertIn("nested", str(caught.exception))

class TestLosslessEdgeCases(unittest.TestCase):
    """Constructs the codec does not interpret must still round trip exactly."""

    def assert_round_trips(self, encoded: bytes) -> object:
        value = load(encoded)
        self.assertEqual(dump(value), encoded)
        return value

    def test_ivar_wrapped_array_keeps_its_record(self):
        # An Array has nowhere to store instance variables, so the wrapper is
        # preserved instead of dropped.
        encoded = b"\x04\x08I[\x06i\x06\x06:\x06Ei\x07"
        value = self.assert_round_trips(encoded)
        self.assertEqual(value.inner, [1])
        self.assertEqual(value.ivars, {"E": 2})

    def test_ivar_wrapped_hash_keeps_its_record(self):
        encoded = b"\x04\x08I{\x06i\x06i\x07\x06:\x06ET"
        value = self.assert_round_trips(encoded)
        self.assertEqual(value.ivars, {"E": True})

    def test_ivar_wrapped_symbol_round_trips(self):
        # Ruby >= 1.9 wraps a non-ASCII symbol in an encoding record.
        encoded = b"\x04\x08I:\x08foo\x06:\x06ET"
        value = self.assert_round_trips(encoded)
        self.assertEqual(str(value), "foo")
        self.assertEqual(value.ivars, {"E": True})

    def test_a_symbol_in_a_name_position_is_reported(self):
        """The one shape this reader loads and cannot write back.

        A wrapped symbol read as a *value* keeps its record and round
        trips, which is what `test_ivar_wrapped_symbol_round_trips`
        above checks -- and why that test could not hold this warning.
        In a name position (a class name, an instance-variable name) the
        symbol becomes a plain string key, so the record is gone on the
        way out. The assertion below is that the file really does change
        shape, and that the reader says so rather than letting it.
        """
        for what, encoded in (
                ("a class name",
                 b"\x04\x08oI:\x0aThing\x06:\x06ET\x00"),
                ("an instance-variable name",
                 b"\x04\x08o:\x0aThing\x06I:\x07@a\x06:\x06ETi\x06")):
            with self.subTest(what):
                value, warnings = load_with_warnings(encoded)
                self.assertNotEqual(
                    dump(value), encoded,
                    "this case round trips after all, so the warning is "
                    "about nothing")
                self.assertTrue(
                    any("name position" in w for w in warnings),
                    "%s carrying an encoding record was loaded, could "
                    "not be written back, and nothing said so: %r"
                    % (what, warnings))

    def test_c_level_data_record_round_trips(self):
        # Type 'd' used to be refused outright, failing the whole file.
        encoded = b"\x04\x08d:\x0aThingi\x06"
        value = self.assert_round_trips(encoded)
        self.assertEqual(value.cls, "Thing")
        self.assertEqual(value.value, 1)

    def test_non_utf8_class_name_round_trips(self):
        encoded = b"\x04\x08c\x09Caf\xe9"
        value = self.assert_round_trips(encoded)
        self.assertEqual(value.kind, "class")

    def test_older_minor_version_can_be_reproduced(self):
        encoded = b"\x04\x07i\x06"
        reader = MarshalReader(encoded)
        self.assertEqual(reader.read(), 1)
        self.assertEqual(reader.minor_version, 7)
        self.assertEqual(dump(1, minor=reader.minor_version), encoded)

    def test_out_of_range_fixnum_is_reported(self):
        # Ruby only writes a Fixnum below 2**30, so this one will come back out
        # as a Bignum; the warning is what makes that visible.
        _, warnings = load_with_warnings(b"\x04\x08i\x04\x00\x00\x00@")
        self.assertTrue(any("Fixnum" in w for w in warnings))

    def test_separate_empty_strings_are_not_aliased(self):
        # CPython hands out one object for every b"", so an id()-keyed link
        # table would emit the second as a link and Ruby would load them as one
        # shared, mutable String.
        encoded = dump([b"", b""])
        self.assertEqual(encoded, b'\x04\x08[\x07"\x00"\x00')

    def test_integral_float_is_written_the_way_ruby_writes_it(self):
        self.assertEqual(dump(RubyFloat(1.0)), b"\x04\x08f\x061")
        self.assertEqual(dump(RubyFloat(2.5)), b"\x04\x08f\x082.5")

    def test_unknown_class_ref_kind_is_refused(self):
        with self.assertRaises(MarshalError) as caught:
            dump(RubyClassRef("Foo", "typo"))
        self.assertIn("unknown class reference kind", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
