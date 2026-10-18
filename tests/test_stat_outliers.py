"""`map_critique` scope `stat_outliers`: a field against its own table.

Synthetic tables only. Rows are built by cloning an enemy the demo project
already holds and overwriting its numbers in memory -- the analysis reads
`project.entries`, which serves the cached tables, so nothing is written.
"""

import copy
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp.analysis import stat_outliers as so  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyString  # noqa: E402
from rpgvxace_mcp.tools.registry import ToolError  # noqa: E402

from tool_case import ToolTest  # noqa: E402


class TestQuantileMath(unittest.TestCase):
    def test_linear_interpolation_between_ranks(self):
        data = [1, 2, 3, 4]
        self.assertEqual(so._quantile(data, 0.5), 2.5)
        self.assertEqual(so._quantile(data, 0.25), 1.75)
        self.assertEqual(so._quantile(data, 0.75), 3.25)

    def test_ends_and_singleton(self):
        self.assertEqual(so._quantile([7], 0.25), 7)
        self.assertEqual(so._quantile([1, 9], 0.0), 1)
        self.assertEqual(so._quantile([1, 9], 1.0), 9)


class StatOutlierCase(ToolTest):
    """Helpers: fill a table with enemies whose first stat is given."""

    def fill_enemies(self, values, field_values=None):
        """Replace the enemies table's contents with one row per value."""
        proto = self.project.get_entry("enemies", 1)
        rows = self.project.load_data("enemies")
        del rows[1:]
        for index, value in enumerate(values, start=1):
            row = copy.deepcopy(proto)
            row.set("id", index)
            row.set("name", RubyString(("E%d" % index).encode("utf-8")))
            self.set_hp(row, value)
            rows.append(row)

    def set_hp(self, row, value):
        params = list(row.get("params"))
        params[0] = value
        row.set("params", params)

    def run_scope(self):
        return self.call("map_critique", scopes=["stat_outliers"])["stat_outliers"]

    def field(self, report, table, name):
        for found in report["fields"]:
            if found["table"] == table and found["field"] == name:
                return found
        return None

    def skipped(self, report, table, name=None):
        return [s for s in report["skipped"]
                if s["table"] == table and s.get("field") == name]


class TestStatOutliers(StatOutlierCase):
    def test_a_high_outlier_is_found_with_its_fences(self):
        self.fill_enemies([100, 105, 110, 115, 120, 125, 130, 135, 140, 5000])
        report = self.run_scope()
        hp = self.field(report, "enemies", "mhp")
        self.assertIsNotNone(hp, report["skipped"])
        self.assertEqual(hp["n"], 10)
        self.assertEqual(hp["median"], 122.5)
        self.assertEqual(hp["q1"], 111.25)
        self.assertEqual(hp["q3"], 133.75)
        self.assertEqual(hp["lower_fence"], 77.5)
        self.assertEqual(hp["upper_fence"], 167.5)
        self.assertEqual([o["id"] for o in hp["outliers"]], [10])
        top = hp["outliers"][0]
        self.assertEqual((top["name"], top["value"], top["direction"]),
                         ("E10", 5000, "high"))

    def test_a_low_outlier_says_low(self):
        self.fill_enemies([1, 100, 105, 110, 115, 120, 125, 130, 135, 140])
        hp = self.field(self.run_scope(), "enemies", "mhp")
        self.assertEqual([(o["id"], o["direction"]) for o in hp["outliers"]],
                         [(1, "low")])

    def test_an_even_table_reports_no_outliers_and_says_so(self):
        self.fill_enemies([100 + i for i in range(10)])
        hp = self.field(self.run_scope(), "enemies", "mhp")
        self.assertEqual(hp["outliers"], [])
        self.assertEqual(hp["outliers_total"], 0)

    def test_a_small_table_is_skipped_not_judged(self):
        self.fill_enemies([100, 110, 5000])
        report = self.run_scope()
        self.assertIsNone(self.field(report, "enemies", "mhp"))
        reasons = self.skipped(report, "enemies", "mhp")
        self.assertEqual(len(reasons), 1)
        self.assertIn("fewer than 8", reasons[0]["reason"])

    def test_zero_iqr_is_skipped_rather_than_flagging_the_odd_one(self):
        self.fill_enemies([50] * 9 + [51])
        report = self.run_scope()
        self.assertIsNone(self.field(report, "enemies", "mhp"))
        self.assertIn("zero", self.skipped(report, "enemies", "mhp")[0]["reason"])

    def test_nil_and_malformed_values_are_tolerated(self):
        values = [100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150]
        self.fill_enemies(values)
        rows = self.project.load_data("enemies")
        rows.insert(3, None)                       # a nil slot
        rows[5].set("params", None)                # no params at all
        rows[6].set("params", [])                  # a short array
        rows[7].set("params", [None, "x", True])   # junk, bool is not a number
        report = self.run_scope()
        hp = self.field(report, "enemies", "mhp")
        self.assertEqual(hp["n"], len(values) - 3)
        self.assertEqual(hp["outliers"], [])

    def test_blank_named_slots_are_left_out_and_counted(self):
        self.fill_enemies([100 + i for i in range(10)] + [0] * 30)
        rows = self.project.load_data("enemies")
        for row in rows[11:]:
            row.set("name", RubyString(b""))
        report = self.run_scope()
        hp = self.field(report, "enemies", "mhp")
        self.assertEqual(hp["n"], 10)
        self.assertEqual(hp["outliers"], [])
        self.assertEqual(report["unnamed_entries_ignored"]["enemies"], 30)

    def test_identifiers_and_flags_are_never_analysed(self):
        self.fill_enemies([100 + i for i in range(10)])
        report = self.run_scope()
        considered = {label for labels
                      in report["method"]["fields_considered"].values()
                      for label in labels}
        banned = {"icon_index", "animation_id", "id", "scope", "occasion",
                  "hit_type", "wtype_id", "etype_id", "battler_hue"}
        self.assertEqual(considered & banned, set())
        self.assertIn("mhp", considered)
        self.assertIn("price", considered)

    def test_reply_is_capped_and_says_when_truncated(self):
        # 50 equal-ish rows and 15 spread far above them.
        values = [100 + (i % 5) for i in range(50)] + [1000 + 10 * i
                                                      for i in range(15)]
        self.fill_enemies(values)
        report = self.run_scope()
        hp = self.field(report, "enemies", "mhp")
        self.assertEqual(hp["outliers_total"], 15)
        self.assertEqual(len(hp["outliers"]), so._MAX_OUTLIERS_PER_FIELD)
        self.assertTrue(hp["truncated"])
        # Worst first.
        self.assertEqual(hp["outliers"][0]["value"], 1140)

    def test_the_summary_names_the_worst_outlier(self):
        self.fill_enemies([100, 105, 110, 115, 120, 125, 130, 135, 140, 5000])
        self.assertEqual(summary_outliers(self)[0][:3], ("enemies", 10, 5000))

    def test_modified_z_is_reported_but_not_the_criterion(self):
        self.fill_enemies([100, 105, 110, 115, 120, 125, 130, 135, 140, 5000])
        top = self.field(self.run_scope(), "enemies", "mhp")["outliers"][0]
        self.assertGreater(top["modified_z"], 3.5)

    def test_unlisted_tables_do_not_raise(self):
        # The untouched demo project: every table is tiny, so everything is
        # skipped, and the call still answers.
        report = self.run_scope()
        self.assertEqual(report["fields"], [])
        self.assertTrue(report["skipped"])
        self.assertIn("method", report)

    def test_scope_is_listed_and_selectable(self):
        out = self.call("map_critique", scopes=["stat_outliers"])
        self.assertEqual(out["checks_run"], ["stat_outliers"])
        self.assertNotIn("reachability", out)
        with self.assertRaises(ToolError):
            self.call("map_critique", scopes=["nonsense"])


class TestStatOutliersOnVx(StatOutlierCase):
    engine = "vx"

    def set_hp(self, row, value):
        row.set("maxhp", value)

    def test_flat_fields_are_read_on_vx(self):
        self.fill_enemies([100, 105, 110, 115, 120, 125, 130, 135, 140, 5000])
        hp = self.field(self.run_scope(), "enemies", "maxhp")
        self.assertIsNotNone(hp)
        self.assertEqual([o["id"] for o in hp["outliers"]], [10])

    def test_the_summary_names_the_worst_outlier(self):
        self.fill_enemies([100, 105, 110, 115, 120, 125, 130, 135, 140, 5000])
        self.assertEqual(summary_outliers(self)[0][:3], ("enemies", 10, 5000))


class TestStatOutliersOnXP(StatOutlierCase):
    engine = "xp"

    def set_hp(self, row, value):
        row.set("maxhp", value)
        # A stat only XP has: VX's allowlist, which XP fell back to, has no
        # pdef, and called XP's spirit-less enemies by VX's field names.
        row.set("pdef", value // 10)

    def test_xp_s_own_fields_are_read(self):
        self.fill_enemies([100, 105, 110, 115, 120, 125, 130, 135, 140, 5000])
        report = self.run_scope()
        for name in ("maxhp", "pdef"):
            with self.subTest(field=name):
                found = self.field(report, "enemies", name)
                self.assertIsNotNone(found, report["skipped"])
                self.assertEqual([o["id"] for o in found["outliers"]], [10])


def summary_outliers(case):
    """`(table, id, value, field)` for each outlier the default summary names."""
    rows = [r for r in case.call("map_critique")["summary"]["findings"]
            if r["scope"] == "stat_outliers"]
    case.assertEqual(len(rows), 1, "outliers are one finding")
    case.assertEqual(rows[0]["severity"], "low")
    return [(e["table"], e["id"], e["value"], e["field"])
            for e in rows[0]["entries"]]


if __name__ == "__main__":
    unittest.main()
