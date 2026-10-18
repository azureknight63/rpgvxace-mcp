"""`RubyHash` keeps Ruby's key semantics and does not scan per insert.

The hash stores ordered pairs (so duplicate and unhashable keys round trip) and
indexes the keys it can safely index. These tests pin both halves: the scale
claim, counted rather than timed where it can be, and every equality rule the
linear scan had.
"""

import copy
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyFloat, RubyHash, RubyObject, RubyString, RubySymbol)


class TestScale(unittest.TestCase):

    def test_inserting_distinct_keys_does_not_compare_against_every_key(self):
        """Counted, so a loaded machine cannot make it flaky.

        `RubySymbol.__eq__` is counted for the duration. A per-insert scan
        makes ~N^2/2 comparisons; an index makes a handful of hash
        collisions. The bound is two orders of magnitude under the scan.
        """
        calls = [0]
        original = RubySymbol.__eq__

        def counting_eq(self, other):
            calls[0] += 1
            return original(self, other)

        n = 2000
        names = ["k%d" % i for i in range(n)]
        RubySymbol.__eq__ = counting_eq
        try:
            table = RubyHash()
            for name in names:
                table.set(RubySymbol(name), 1)
            for name in names:
                self.assertIn(RubySymbol(name), table)
        finally:
            RubySymbol.__eq__ = original
        self.assertEqual(len(table), n)
        self.assertLess(calls[0], 20 * n,
                        "%d key comparisons for %d inserts" % (calls[0], n))

    def test_twenty_thousand_inserts_finish_quickly(self):
        started = time.perf_counter()
        table = RubyHash()
        for key in range(20_000):
            table.set(key, key)
        for key in range(20_000):
            self.assertEqual(table.get(key), key)
        elapsed = time.perf_counter() - started
        self.assertEqual(len(table), 20_000)
        # Linear takes well under a second; the scan took tens of seconds.
        self.assertLess(elapsed, 5.0)

    def test_doubling_the_size_does_not_quadruple_the_time(self):
        def build(n):
            started = time.perf_counter()
            table = RubyHash()
            for key in range(n):
                table.set(key, key)
            return time.perf_counter() - started
        small = min(build(4000) for _ in range(3))
        large = min(build(8000) for _ in range(3))
        # Quadratic is a ratio of 4; linear is 2. 3.2 leaves room for noise.
        self.assertLess(large, max(small, 1e-4) * 3.2 + 0.05)


class TestSemantics(unittest.TestCase):

    def test_insertion_order_and_replacement_keeps_position(self):
        table = RubyHash()
        for key in (3, 1, 2):
            table.set(key, key * 10)
        table.set(1, "new")
        self.assertEqual(table.items(), [(3, 30), (1, "new"), (2, 20)])

    def test_python_equal_numbers_are_one_key(self):
        table = RubyHash([(1, "a")])
        self.assertEqual(table.get(1.0), "a")
        self.assertEqual(table.get(True), "a")
        table.set(1.0, "b")
        self.assertEqual(len(table), 1)
        self.assertEqual(table.items(), [(1.0, "b")])
        self.assertEqual(table.get(RubyFloat(1.0, b"1")), "b")

    def test_symbol_and_str_keys_match_as_they_always_did(self):
        table = RubyHash()
        table.set(RubySymbol("a"), 1)
        self.assertEqual(table.get("a"), 1)
        table.set("a", 2)
        self.assertEqual(len(table), 1)

    def test_nan_keys_never_match_anything_including_themselves(self):
        nan = float("nan")
        table = RubyHash()
        table.set(nan, 1)
        table.set(nan, 2)
        self.assertEqual(len(table), 2)
        self.assertNotIn(nan, table)
        self.assertFalse(table.delete(nan))

    def test_ruby_string_keys_compare_by_bytes_and_encoding(self):
        utf8 = RubyString(b"x", {"E": True})
        bare = RubyString(b"x")
        table = RubyHash([(utf8, 1), (bare, 2)])
        self.assertEqual(len(table), 2)
        table.set(RubyString(b"x", {"E": True}), 3)
        self.assertEqual(table.get(utf8), 3)
        self.assertEqual(table.get(bare), 2)
        self.assertEqual(table.get(b"x"), 3)  # RubyString == bytes; first wins

    def test_bytes_lookup_finds_a_ruby_string_entry_and_the_reverse(self):
        table = RubyHash([(RubyString(b"x"), 1), (7, 2)])
        self.assertEqual(table.get(b"x"), 1)
        table = RubyHash([(b"x", 1), (7, 2)])
        self.assertEqual(table.get(RubyString(b"x")), 1)

    def test_mutating_a_key_after_insertion_is_seen(self):
        key = RubyString(b"a")
        table = RubyHash([(1, "one"), (key, "v")])
        key.data = b"b"
        self.assertIn(RubyString(b"b"), table)
        self.assertNotIn(RubyString(b"a"), table)
        obj = RubyObject("K", {"@k": 1})
        table.set(obj, "o")
        obj.ivars["@k"] = 2
        self.assertEqual(table.get(RubyObject("K", {"@k": 2})), "o")
        self.assertIsNone(table.get(RubyObject("K", {"@k": 1})))

    def test_unhashable_keys_use_value_equality(self):
        table = RubyHash()
        table.set([1, 2], "a")
        table.set([1, 2], "b")
        self.assertEqual(table.items(), [([1, 2], "b")])
        self.assertEqual(table.get([1, 2]), "b")

    def test_a_loose_key_before_an_indexed_one_wins_first_match(self):
        class Anything:
            def __eq__(self, other):
                return True
            __hash__ = None
        wild = Anything()
        table = RubyHash([(wild, "wild"), (5, "five")])
        self.assertEqual(table.get(5), "wild")  # first match, as the scan did
        table.set(5, "x")
        self.assertEqual(len(table), 2)

    def test_duplicate_keys_read_from_a_file_resolve_to_the_first(self):
        table = RubyHash([(1, "a"), (2, "b"), (1, "c")])
        self.assertEqual(table.get(1), "a")
        self.assertTrue(table.delete(1))
        self.assertEqual(table.get(1), "c")
        self.assertEqual(table.keys(), [2, 1])

    def test_deletion_then_reinsert_goes_to_the_end(self):
        table = RubyHash([(i, i) for i in range(5)])
        self.assertTrue(table.delete(2))
        self.assertFalse(table.delete(2))
        table.set(2, "back")
        self.assertEqual(table.keys(), [0, 1, 3, 4, 2])
        self.assertEqual(table.get(4), 4)
        self.assertEqual(table.get(2), "back")

    def test_direct_edits_to_pairs_are_not_missed(self):
        table = RubyHash([(1, "a")])
        self.assertIn(1, table)
        table.pairs.append((2, "b"))
        self.assertEqual(table.get(2), "b")
        table.pairs[0] = (3, "c")
        self.assertNotIn(1, table)
        self.assertEqual(table.get(3), "c")
        table.pairs.sort(key=lambda kv: -kv[0])
        self.assertEqual(table.get(2), "b")
        table.pairs = [(9, "z")]
        self.assertEqual(table.items(), [(9, "z")])
        self.assertEqual(table.get(9), "z")
        self.assertNotIn(3, table)

    def test_default_and_equality_and_copies(self):
        table = RubyHash([(1, 2)], default=0)
        self.assertEqual(table.default, 0)
        self.assertEqual(table, RubyHash([(1, 2)], default=0))
        self.assertNotEqual(table, RubyHash([(1, 2)], default=1))
        self.assertEqual(table.pairs, [(1, 2)])
        clone = copy.deepcopy(table)
        clone.set(5, 6)
        self.assertEqual(len(table), 1)
        self.assertEqual(clone.get(5), 6)
        self.assertEqual(clone.get(1), 2)

    def test_a_hash_holding_itself_still_works(self):
        table = RubyHash([(1, 1)])
        table.set(2, table)
        self.assertIs(table.get(2), table)

    def test_marshal_round_trip_is_byte_identical_with_odd_keys(self):
        table = RubyHash([
            (1, 0), (RubySymbol("s"), 2), (2.5, 3),
            (RubyString(b"k", {"E": True}), 4),
            ([1, 2], 5), (RubyObject("K", {"@k": 1}), 6), (None, 7),
        ], default=0)
        encoded = marshal_rb.dump(table)
        again = marshal_rb.dump(marshal_rb.load(encoded))
        self.assertEqual(encoded, again)


if __name__ == "__main__":
    unittest.main()
