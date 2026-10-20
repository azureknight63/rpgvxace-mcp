"""`lore_bible`: a guide to building a game's lore, and the bible it keeps.

The tool never edits the game. What these tests hold is the other half of that
sentence: the guidance is whole and honest about its sources, the bible file is
read back through a validator and never trusted, a link in the lore folder is
refused, the scan of a project is bounded whatever the project holds, and the
HTML export cannot be made to run a hostile string.
"""

import json
import os
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp import (  # noqa: E402
    lore_export,
    lore_guidance,
    lore_store,
    marshal_rb,
)
from rpgvxace_mcp.analysis import lore_baseline  # noqa: E402
from rpgvxace_mcp.paths import ProjectError, ReadOnlyError  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.rubytypes import RubyHash, RubyString  # noqa: E402
from rpgvxace_mcp.schema import new_event_command, new_map_info  # noqa: E402
from rpgvxace_mcp.tools import lore_bible_tools as lore_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
)

from tool_case import SharedObjectMap, ToolTest  # noqa: E402


class TestTheGuidanceIsWhole(unittest.TestCase):
    """The curated data is checked as data: ids, coverage, lengths, honesty."""

    def questions(self):
        return lore_guidance.QUESTIONS

    def strings(self):
        """Every string the guidance ships, however deep it is nested."""
        out = []

        def walk(value):
            if isinstance(value, str):
                out.append(value)
            elif isinstance(value, dict):
                for key, inner in value.items():
                    walk(key)
                    walk(inner)
            elif isinstance(value, (list, tuple)):
                for inner in value:
                    walk(inner)

        for name in ("QUESTIONS", "TIERS", "PITFALLS", "GROUPS",
                     "SMALL_TEAM_NOTE", "ESTIMATES_NOTE"):
            walk(getattr(lore_guidance, name))
        self.assertGreater(len(out), 400, "the walk found almost nothing")
        return out

    def test_the_bank_is_about_the_size_the_research_gave(self):
        self.assertEqual(len(self.questions()), 60)

    def test_ids_are_unique_and_readable(self):
        ids = [q["id"] for q in self.questions()]
        self.assertEqual(len(ids), len(set(ids)))
        for question in self.questions():
            with self.subTest(id=question["id"]):
                self.assertRegex(question["id"], r"^[a-z]+\.[a-z0-9_]+$")
                self.assertEqual(question["id"].split(".")[0],
                                 question["group"])

    def test_every_question_names_a_known_group_and_tier(self):
        groups = [key for key, _ in lore_guidance.GROUPS]
        for question in self.questions():
            with self.subTest(id=question["id"]):
                self.assertIn(question["group"], groups)
                self.assertIn(question["tier"], lore_guidance.TIER_ORDER)
                for field in ("text", "why", "hint"):
                    self.assertIsInstance(question[field], str)
                    self.assertTrue(question[field].strip())

    def test_every_group_is_asked_in_the_lightest_tier(self):
        """So a higher tier only deepens a group, never introduces one late."""
        for key, _ in lore_guidance.GROUPS:
            with self.subTest(group=key):
                self.assertTrue(
                    [q for q in self.questions()
                     if q["group"] == key and q["tier"] == "light"])

    def test_each_tier_asks_more_than_the_one_below(self):
        counts = [len(lore_guidance.questions_for_tier(tier))
                  for tier in lore_guidance.TIER_ORDER]
        self.assertEqual(counts, sorted(set(counts)))
        self.assertEqual(counts[-1], 60)

    def test_a_tier_includes_every_lower_tier(self):
        for lower, higher in zip(lore_guidance.TIER_ORDER,
                                 lore_guidance.TIER_ORDER[1:]):
            with self.subTest(higher=higher):
                below = {q["id"] for q in lore_guidance.questions_for_tier(lower)}
                above = {q["id"] for q in lore_guidance.questions_for_tier(higher)}
                self.assertLess(below, above)

    def test_the_light_set_is_a_short_coherent_interview(self):
        light = lore_guidance.questions_for_tier("light")
        self.assertTrue(12 <= len(light) <= 20, len(light))
        covered = {q["group"] for q in light}
        for group in ("premise", "world", "cast", "conflict", "tone",
                      "delivery"):
            self.assertIn(group, covered)

    def test_no_text_is_over_a_length(self):
        limits = {"text": 330, "why": 280, "hint": 200}
        for question in self.questions():
            for field, limit in limits.items():
                with self.subTest(id=question["id"], field=field):
                    self.assertLessEqual(len(question[field]), limit)

    def test_no_text_is_a_placeholder(self):
        banned = re.compile(r"TODO|TBD|FIXME|XXX|lorem|placeholder|\{\}|%s|"
                            r"<[a-z]+>", re.IGNORECASE)
        for text in self.strings():
            with self.subTest(text=text[:40]):
                self.assertIsNone(banned.search(text))
                self.assertEqual(text, text.strip())

    def test_nothing_unverified_is_asserted_as_fact(self):
        """The research marked these UNVERIFIED or single-source; none ships.

        A phrase list, because the check has to be able to fail: each entry is
        a claim the research could not corroborate (or a name only a weak
        source tied to a claim), and the scan reads every shipped string.
        """
        phrases = [
            "added late", "late addition", "ten times", "10x", "tenfold",
            "Miyamoto", "Miyazaki", "Sephiroth", "Aerith", "Zack",
            "Kurvitz wrote", "16-32", "16 to 32", "pacifist", "Woolsey",
            "Water Margin", "Don Carson", "Hemingway", "Sanderson",
            "30 hours", "42 hours", "hours to beat", "play time",
            "1 million words", "one million words",
        ]
        shipped = "\n".join(self.strings()).lower()
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase.lower(), shipped)

    def test_the_instrument_can_fail(self):
        """A scan that finds nothing would pass the test above for any data."""
        with mock.patch.object(lore_guidance, "SMALL_TEAM_NOTE",
                               "The pacifist route was added late."):
            shipped = "\n".join(self.strings()).lower()
        self.assertIn("pacifist", shipped)

    def test_a_question_is_not_a_slogan_it_asks(self):
        for question in self.questions():
            with self.subTest(id=question["id"]):
                self.assertTrue(
                    question["text"].rstrip().endswith(("?", ".")),
                    question["text"])

    def test_the_lookup_by_id(self):
        first = self.questions()[0]
        self.assertEqual(lore_guidance.question(first["id"]), first)
        self.assertIsNone(lore_guidance.question("no.such_question"))
        self.assertIsNone(lore_guidance.question(5000))


class TestTheTiersAreSourcedOrLabelledEstimates(unittest.TestCase):
    def test_four_tiers_in_order(self):
        self.assertEqual(lore_guidance.TIER_ORDER,
                         ("light", "moderate", "rich", "deep"))
        self.assertEqual(set(lore_guidance.TIERS), set(lore_guidance.TIER_ORDER))

    def test_every_tier_has_two_or_three_comparison_games_with_a_reason(self):
        for key in lore_guidance.TIER_ORDER:
            tier = lore_guidance.TIERS[key]
            games = tier["comparison_games"]
            with self.subTest(tier=key):
                self.assertIn(len(games), (2, 3))
                for game in games:
                    self.assertTrue(game["game"].strip())
                    self.assertGreaterEqual(len(game["reason"]), 30)
                    self.assertIn(game["basis"], lore_guidance.BASES)

    def test_every_count_the_tiers_ask_is_a_labelled_estimate_range(self):
        previous_top = {}
        for key in lore_guidance.TIER_ORDER:
            asks = lore_guidance.TIERS[key]["asks"]
            with self.subTest(tier=key):
                self.assertIs(asks["estimate"], True)
                for field in ("named_characters", "places", "factions",
                              "eras", "bible_words"):
                    low, high = asks[field]
                    self.assertLessEqual(low, high)
                    self.assertGreaterEqual(low, 0)
                    # A higher tier never asks for fewer than the one below.
                    self.assertGreaterEqual(high, previous_top.get(field, 0))
                    previous_top[field] = high

    def test_each_tier_says_where_the_player_meets_lore_and_its_risk(self):
        for key in lore_guidance.TIER_ORDER:
            with self.subTest(tier=key):
                tier = lore_guidance.TIERS[key]
                self.assertTrue(tier["label"])
                self.assertGreater(len(tier["player_meets_lore"]), 20)
                self.assertGreater(len(tier["biggest_risk"]), 20)
                self.assertGreater(len(tier["summary"]), 20)

    def test_a_figure_without_a_source_is_not_stated_as_a_fact(self):
        """Basis says how sure each comparison is; the weak ones are labelled."""
        weak = [game for key in lore_guidance.TIER_ORDER
                for game in lore_guidance.TIERS[key]["comparison_games"]
                if game["basis"] != "sourced"]
        for game in weak:
            with self.subTest(game=game["game"]):
                self.assertRegex(game["reason"],
                                 r"(?i)one source|reported|estimate|per ")

    def test_the_small_team_note_is_labelled_an_observation(self):
        self.assertIn("observation", lore_guidance.SMALL_TEAM_NOTE.lower())
        self.assertIn("estimate", lore_guidance.ESTIMATES_NOTE.lower())

    def test_every_pitfall_has_an_id_a_rule_a_reason_and_a_basis(self):
        ids = [p["id"] for p in lore_guidance.PITFALLS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 15)
        for pitfall in lore_guidance.PITFALLS:
            with self.subTest(id=pitfall["id"]):
                self.assertGreater(len(pitfall["rule"]), 20)
                self.assertGreater(len(pitfall["why"]), 20)
                self.assertIn(pitfall["basis"], ("sourced", "synthesis"))

    def test_the_lookup_of_a_pitfall(self):
        self.assertEqual(lore_guidance.pitfall("who_knows_what")["id"],
                         "who_knows_what")
        self.assertIsNone(lore_guidance.pitfall("nothing"))


class StoreTest(ToolTest):
    """The bible file against the demo project's work folder."""

    @property
    def lore(self):
        return self.tmp / "project" / ".rpgvxace-mcp" / "lore"

    @property
    def bible_path(self):
        return self.lore / "bible.json"

    def write_raw(self, data):
        """Put bytes (or an object, as JSON) where the bible lives."""
        self.lore.mkdir(parents=True, exist_ok=True)
        if not isinstance(data, (bytes, str)):
            data = json.dumps(data)
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.bible_path.write_bytes(data)

    def good(self, **changes):
        bible = lore_store.new_bible()
        bible.update(changes)
        return bible

    def pretend_these_are_links(self, *names):
        """Stand in for a link where this account cannot make a real one."""
        real = lore_store._is_reparse_point
        return mock.patch.object(
            lore_store, "_is_reparse_point",
            lambda path: Path(path).name in names or real(path))


class TestTheBibleFile(StoreTest):
    def test_a_project_with_no_bible_loads_an_empty_one(self):
        bible = lore_store.load(self.project)
        self.assertEqual(bible, lore_store.new_bible())
        self.assertIsNone(bible["tier"])
        self.assertEqual(bible["answers"], {})
        self.assertEqual(bible["entries"], [])
        self.assertFalse(self.lore.exists(), "loading must not create the folder")

    def test_a_saved_bible_is_read_back_exactly(self):
        bible = self.good(
            tier="rich", mode="custom", approach="ask me one thing a day",
            answers={"premise.seed": "A ferryman seeks his daughter.\nShe is "
                                     "across the river."},
            entries=[{"kind": "place", "name": "Silver Gate",
                      "text": "Opens at dusk."}])
        lore_store.save(self.project, bible)
        self.assertTrue(self.bible_path.is_file())
        self.assertEqual(lore_store.load(self.project), bible)

    def test_the_file_is_json_in_the_lore_folder_of_the_work_folder(self):
        lore_store.save(self.project, self.good(tier="light"))
        stored = json.loads(self.bible_path.read_text(encoding="utf-8"))
        self.assertEqual(stored["format"], "rpgvxace-mcp-lore")
        self.assertEqual(stored["version"], 1)
        self.assertEqual(stored["tier"], "light")

    def test_no_temporary_file_is_left_beside_it(self):
        lore_store.save(self.project, self.good())
        lore_store.save(self.project, self.good(tier="deep"))
        self.assertEqual(sorted(p.name for p in self.lore.iterdir()),
                         ["bible.json"])

    def test_a_failed_write_leaves_the_old_bible_and_no_litter(self):
        lore_store.save(self.project, self.good(tier="light"))
        before = self.bible_path.read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                lore_store.save(self.project, self.good(tier="deep"))
        self.assertEqual(self.bible_path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.lore.iterdir()),
                         ["bible.json"])

    def test_a_bible_over_the_ceiling_is_refused_and_the_old_one_stands(self):
        lore_store.save(self.project, self.good(tier="light"))
        before = self.bible_path.read_bytes()
        big = self.good(entries=[
            {"kind": "term", "name": "t%d" % i,
             "text": "x" * lore_store.MAX_ENTRY_TEXT_CHARS}
            for i in range(lore_store.MAX_BIBLE_ENTRIES)])
        with self.assertRaises(ProjectError) as caught:
            lore_store.save(self.project, big)
        self.assertIn("%d bytes" % lore_store.MAX_BIBLE_BYTES,
                      str(caught.exception))
        self.assertEqual(self.bible_path.read_bytes(), before)

    def test_a_read_only_server_will_not_save(self):
        self.project.read_only = True
        with self.assertRaises(ReadOnlyError):
            lore_store.save(self.project, self.good())
        self.assertFalse(self.lore.exists())

    def test_unknown_question_ids_in_a_file_are_kept_not_refused(self):
        """A newer or older server's ids are a status fact, not a corrupt file."""
        self.write_raw(self.good(answers={"premise.renamed_later": "x"}))
        self.assertEqual(
            lore_store.load(self.project)["answers"],
            {"premise.renamed_later": "x"})


class TestTheBibleFileIsUntrusted(StoreTest):
    """A hand-edited or hostile file is refused with a message, never a crash."""

    def refused(self, data, *words):
        self.write_raw(data)
        with self.assertRaises(ProjectError) as caught:
            lore_store.load(self.project)
        message = str(caught.exception)
        self.assertIn("bible.json", message)
        for word in words:
            self.assertIn(word, message)
        return message

    def test_things_that_are_not_a_bible(self):
        for data, word in [
                (b"", "JSON"), (b"\xff\xfe not text", "UTF-8"),
                (b"{not json", "JSON"), (b"[]", "object"), (b"3", "object"),
                (b"null", "object"),
                (b'{"a": NaN}', "JSON"), (b'{"a": Infinity}', "JSON"),
                (b"[" * 5000, "JSON")]:
            with self.subTest(data=data[:20]):
                self.refused(data, word)

    def test_the_wrong_format_or_version(self):
        base = self.good()
        for change, word in [({"format": "something-else"}, "format"),
                             ({"version": 2}, "version"),
                             ({"version": True}, "version"),
                             ({"version": "1"}, "version"),
                             ({"version": 1.0}, "version")]:
            with self.subTest(change=change):
                self.refused({**base, **change}, word)

    def test_an_unknown_or_missing_top_level_key(self):
        base = self.good()
        self.refused({**base, "extra": 1}, "extra")
        missing = dict(base)
        del missing["answers"]
        self.refused(missing, "answers")

    def test_tier_mode_and_approach(self):
        base = self.good()
        for change, word in [
                ({"tier": "legendary"}, "tier"), ({"tier": 3}, "tier"),
                ({"mode": "telepathy"}, "mode"), ({"mode": []}, "mode"),
                ({"approach": "x"}, "approach"),  # only custom has one
                ({"mode": "custom", "approach": 5}, "approach"),
                ({"mode": "custom", "approach": ""}, "approach"),
                ({"mode": "custom", "approach": "x" * (
                    lore_store.MAX_APPROACH_CHARS + 1)}, "approach")]:
            with self.subTest(change=change):
                self.refused({**base, **change}, word)
        self.write_raw({**base, "mode": "custom", "approach": "my way"})
        self.assertEqual(lore_store.load(self.project)["approach"], "my way")

    def test_answers(self):
        base = self.good()
        too_many = {"premise.q%d" % i: "x" for i in range(
            lore_store._STORED_ANSWERS_UPPER + 1)}
        for answers, word in [
                ([], "answers"), ("x", "answers"),
                ({"Not An Id": "x"}, "Not An Id"),
                ({"premise.seed": 5}, "premise.seed"),
                ({"premise.seed": ["x"]}, "premise.seed"),
                ({"premise.seed": None}, "premise.seed"),
                ({"premise.seed": "x" * (lore_store.MAX_ANSWER_CHARS + 1)},
                 "premise.seed"),
                ({"premise.seed": "bad \x00 byte"}, "premise.seed"),
                ({"premise.seed": "lone \ud800 surrogate"}, "premise.seed"),
                ({"a" * 200 + ".b": "x"}, "answers"),
                (too_many, "answers")]:
            with self.subTest(answers=str(answers)[:40]):
                self.refused({**base, "answers": answers}, word)

    def test_entries(self):
        base = self.good()
        ok = {"kind": "place", "name": "Gate", "text": "t"}
        too_many = [{"kind": "term", "name": "n%d" % i, "text": "t"}
                    for i in range(lore_store.MAX_BIBLE_ENTRIES + 1)]
        for entries, word in [
                ({}, "entries"), ("x", "entries"), ([5], "entries[0]"),
                ([{"kind": "place", "name": "Gate"}], "text"),
                ([{**ok, "kind": "planet"}], "kind"),
                ([{**ok, "kind": None}], "kind"),
                ([{**ok, "name": ""}], "name"),
                ([{**ok, "name": "   "}], "name"),
                ([{**ok, "name": 7}], "name"),
                ([{**ok, "name": "n" * (lore_store.MAX_ENTRY_NAME_CHARS + 1)}],
                 "name"),
                ([{**ok, "name": "two\nlines"}], "name"),
                ([{**ok, "text": "t" * (lore_store.MAX_ENTRY_TEXT_CHARS + 1)}],
                 "text"),
                ([{**ok, "text": 5}], "text"),
                ([{**ok, "extra": 1}], "extra"),
                ([ok, ok], "twice"),
                ([ok, {**ok, "name": "GATE"}], "twice"),
                (too_many, "entries")]:
            with self.subTest(entries=str(entries)[:60]):
                self.refused({**base, "entries": entries}, word)

    def test_the_file_size_is_checked_before_it_is_read(self):
        self.write_raw(b" " * (lore_store.MAX_BIBLE_BYTES + 1))
        with mock.patch("builtins.open", side_effect=AssertionError(
                "opened a file over the ceiling")):
            with self.assertRaises(ProjectError) as caught:
                lore_store.load(self.project)
        self.assertIn("%d bytes" % lore_store.MAX_BIBLE_BYTES,
                      str(caught.exception))

    def test_a_baseline_is_checked_too(self):
        base = self.good()
        deep = cur = {}
        for _ in range(lore_store.BASELINE_DEPTH + 2):
            cur["x"] = {}
            cur = cur["x"]
        for baseline, word in [
                ([], "baseline"), ("x", "baseline"), (5, "baseline"),
                ({"a": "x" * 5000}, "baseline"),
                ({"a": 10 ** 30}, "baseline"),
                ({"a": [1] * (lore_store._BASELINE_LIST_UPPER + 1)},
                 "baseline"),
                ({"a" * 300: 1}, "baseline"),
                (deep, "baseline")]:
            with self.subTest(baseline=str(baseline)[:30]):
                self.write_raw(json.dumps({**base, "baseline": baseline},
                                          allow_nan=True))
                with self.assertRaises(ProjectError) as caught:
                    lore_store.load(self.project)
                self.assertIn(word, str(caught.exception))

    def test_a_baseline_over_its_byte_ceiling_is_refused(self):
        # Each string and the count are within their own bounds; the total is not.
        baseline = {"k%d" % i: "x" * 500 for i in range(200)}
        self.write_raw({**self.good(), "baseline": baseline})
        with self.assertRaises(ProjectError) as caught:
            lore_store.load(self.project)
        self.assertIn("%d bytes" % lore_store.MAX_BASELINE_BYTES,
                      str(caught.exception))

    def test_a_baseline_with_too_many_values_is_refused(self):
        baseline = {"k%d" % i: 1 for i in range(
            lore_store._BASELINE_NODES_UPPER + 1)}
        self.write_raw({**self.good(), "baseline": baseline})
        with self.assertRaises(ProjectError) as caught:
            lore_store.load(self.project)
        self.assertIn("more than %d values" % lore_store._BASELINE_NODES_UPPER,
                      str(caught.exception))

    def test_a_baseline_number_that_is_not_finite_is_refused(self):
        text = json.dumps({**self.good(), "baseline": {"a": 1}}).replace(
            '"a": 1', '"a": 1e999')
        self.write_raw(text)
        with self.assertRaises(ProjectError) as caught:
            lore_store.load(self.project)
        self.assertIn("baseline", str(caught.exception))

    def test_a_sound_baseline_survives(self):
        baseline = {"volume": {"dialogue": {"count": 3, "words": 40}},
                    "gaps": [{"kind": "x", "text": "y"}], "ratio": 0.5,
                    "flag": True, "none": None}
        lore_store.save(self.project, self.good(baseline=baseline))
        self.assertEqual(lore_store.load(self.project)["baseline"], baseline)


class TestALinkInTheLoreFolderIsRefused(StoreTest):
    def test_the_work_folder_the_lore_folder_and_the_file(self):
        for name in (".rpgvxace-mcp", "lore", "bible.json"):
            with self.subTest(link=name):
                lore_store.save(self.project, self.good())
                with self.pretend_these_are_links(name):
                    with self.assertRaises(ProjectError) as caught:
                        lore_store.load(self.project)
                    self.assertIn("link", str(caught.exception))
                    with self.assertRaises(ProjectError) as caught:
                        lore_store.save(self.project, self.good(tier="deep"))
                    self.assertIn("link", str(caught.exception))
                self.assertIsNone(lore_store.load(self.project)["tier"])

    def test_an_export_file_that_is_a_link_is_not_written_through(self):
        lore_store.write_export(self.project, "bible.html", "<p>old</p>")
        with self.pretend_these_are_links("bible.html"):
            with self.assertRaises(ProjectError) as caught:
                lore_store.write_export(self.project, "bible.html", "<p>x</p>")
        self.assertIn("link", str(caught.exception))
        self.assertEqual((self.lore / "bible.html").read_text(encoding="utf-8"),
                         "<p>old</p>")

    def test_a_folder_that_resolves_outside_the_project_is_refused(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        work = self.tmp / "project" / ".rpgvxace-mcp"
        work.mkdir(exist_ok=True)
        try:
            os.symlink(outside, work / "lore", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("this account cannot create symlinks")
        with self.assertRaises(ProjectError):
            lore_store.save(self.project, self.good())
        self.assertEqual(list(outside.iterdir()), [])

    def test_export_names_are_a_fixed_set(self):
        for name in ("../bible.html", "bible.exe", "", "x/y.md", "bible.json"):
            with self.subTest(name=name):
                with self.assertRaises(ProjectError):
                    lore_store.write_export(self.project, name, "x")


def label(value):
    """A subtest name for a hostile value that cannot itself be `repr`ed."""
    try:
        return repr(value)[:30]
    except ValueError:
        return "something huge"


class LoreProject(ToolTest):
    """The demo project, plus the means to give it a story to read."""

    def refresh(self):
        self.project = Project(self.tmp / "project", read_only=self.read_only)
        self.ctx = Context(self.project)

    def S(self, text):
        return self.project.codec.make_string(text)

    def say(self, face, *lines, index=0):
        """One Show Text box: a 101 with the face, a 401 per line."""
        return [new_event_command(101, 0, [self.S(face), index, 0, 2]),
                *(new_event_command(401, 0, [self.S(line)]) for line in lines)]

    def event(self, event_id, name, commands, trigger=0, x=1, y=1):
        event = self.project.profile.new_event(event_id, name, x, y)
        event.set("name", self.S(name))
        page = event.get("pages")[0]
        page.set("trigger", trigger)
        page.set("list", [*commands, new_event_command(0, 0, [])])
        return event

    def put_file(self, filename, value):
        self.project.path_for(filename).write_bytes(marshal_rb.dump(value))

    def put_map(self, map_id, name, events, parent=0, display=True):
        """Write a map and its map-tree entry; `events` is a list of events."""
        game_map = self.project.profile.new_map(17, 13, 1)
        if display and game_map.has("display_name"):
            game_map.set("display_name", self.S(name))
        hash_of_events = RubyHash()
        for event in events:
            hash_of_events.set(event.get("id"), event)
        game_map.set("events", hash_of_events)
        self.put_file(self.project.map_file(map_id), game_map)
        infos = self.project.load_data("map_infos")
        info = new_map_info(name, parent, map_id)
        info.set("name", self.S(name))
        infos.set(map_id, info)
        self.put_file(self.project.filename_for("map_infos"), infos)
        self.refresh()

    def set_field(self, table, entry_id, field, text):
        entries = self.project.load_data(table)
        entries[entry_id].set(field, self.S(text))
        self.put_file(self.project.filename_for(table), entries)
        self.refresh()

    def baseline(self, **kwargs):
        corpus = lore_baseline.lore_scan(self.project, **kwargs)
        return lore_baseline.baseline_of(corpus)

    def story(self):
        """Three places of text with a name that recurs and is never explained."""
        self.set_field("items", 2, "description", "Lit by the Order of the "
                                                  "Lantern.")
        self.put_map(3, "Silver Gate", [
            self.event(1, "Warden", [
                *self.say("Actor2", "Only the Warden may open the gate.",
                          index=1),
                *self.say("Actor2", "The lock was forged by Brindle long ago.",
                          index=1),
                *self.say("Actor2", "Ask Brindle if you doubt me.", index=1),
                *self.say("Actor2", "Even Brindle's apprentices left.",
                          index=1)]),
            self.event(2, "EV002", self.say(
                "", "The Order of the Lantern watches the road.")),
            self.event(3, "Opening", self.say(
                "Actor1", "Ralph stared at the gate."), trigger=3),
            self.event(4, "Trap", self.say("", "Nothing here."), trigger=1),
        ])


class TestTheBaselineOfTheDemoProject(LoreProject):
    """Every number is a hand count of the fixture's own text."""

    def test_the_volume_of_each_kind_of_text(self):
        volume = self.baseline()["volume"]
        self.assertEqual(volume["dialogue"], {"count": 4, "words": 23})
        self.assertEqual(volume["choices"], {"count": 0, "words": 0})
        self.assertEqual(volume["scrolling"], {"count": 0, "words": 0})
        self.assertEqual(volume["comments"], {"count": 0, "words": 0})
        self.assertEqual(volume["map_names"], {"count": 1, "words": 1})
        self.assertEqual(volume["descriptions"], {"count": 2, "words": 6})
        self.assertEqual(volume["notes"], {"count": 0, "words": 0})
        # The title, the currency, six type names, and 42 terms.
        self.assertEqual(volume["system_text"], {"count": 55, "words": 62})

    def test_comments_are_counted_apart_and_are_not_prose(self):
        self.put_map(3, "Notes", [self.event(1, "Dev", [
            new_event_command(108, 0, [self.S("TODO: ask Brindle about this")]),
            new_event_command(408, 0, [self.S("and more")])])])
        baseline = self.baseline()
        self.assertEqual(baseline["volume"]["comments"],
                         {"count": 1, "words": 7})
        self.assertEqual(baseline["volume"]["dialogue"]["count"], 4)
        self.assertNotIn("Brindle", [n["name"] for n in
                                     baseline["proper_nouns"]])

    def test_choices_and_scrolling_text_are_counted(self):
        self.put_map(3, "Choice", [self.event(1, "Fork", [
            new_event_command(102, 0, [[self.S("Go left"), self.S("Stay")], 0]),
            new_event_command(105, 0, [2, False]),
            new_event_command(405, 0, [self.S("Long ago there was a king.")]),
            new_event_command(405, 0, [self.S("He ruled well.")])])])
        volume = self.baseline()["volume"]
        self.assertEqual(volume["choices"], {"count": 2, "words": 3})
        self.assertEqual(volume["scrolling"], {"count": 1, "words": 9})

    def test_the_database_share_without_a_description(self):
        database = self.baseline()["database"]
        self.assertEqual(database["entries"], 16)
        self.assertEqual(database["empty_description"], 14)
        self.assertEqual(database["empty_share"], 0.875)
        self.assertEqual(database["tables"]["items"],
                         {"entries": 2, "empty_description": 0, "with_note": 0})
        self.assertEqual(database["tables"]["skills"]["empty_description"], 2)

    def test_a_placeholder_entry_with_no_name_is_not_an_entry(self):
        self.set_field("items", 2, "name", "")
        self.assertEqual(self.baseline()["database"]["tables"]["items"],
                         {"entries": 1, "empty_description": 0, "with_note": 0})

    def test_notes_are_counted(self):
        self.set_field("items", 1, "note", "<kind: tonic> for the sick")
        baseline = self.baseline()
        self.assertEqual(baseline["volume"]["notes"], {"count": 1, "words": 5})
        self.assertEqual(baseline["database"]["tables"]["items"]["with_note"],
                         1)

    def test_the_map_design_signals(self):
        maps = self.baseline()["maps"]
        self.assertEqual(maps["count"], 2)
        self.assertEqual(maps["default_named"], 0)
        self.assertEqual(maps["no_display_name"], 1)
        self.assertEqual(maps["blank"], 0)
        self.assertEqual(maps["max_depth"], 2)
        self.assertEqual(maps["events_total"], 3)
        self.assertEqual(maps["events_per_map"], 1.5)
        self.assertEqual(maps["maps_without_events"], 0)

    def test_default_named_blank_and_nested_maps(self):
        self.put_map(3, "MAP003", [], parent=2, display=False)
        self.put_map(4, "MAP004", [], parent=3, display=False)
        maps = self.baseline()["maps"]
        self.assertEqual(maps["count"], 4)
        self.assertEqual(maps["default_named"], 2)
        self.assertEqual(maps["blank"], 2)
        self.assertEqual(maps["max_depth"], 4)
        self.assertEqual(maps["maps_without_events"], 2)

    def test_a_cycle_in_the_map_tree_ends_the_depth_count(self):
        infos = self.project.load_data("map_infos")
        infos.get(1).set("parent_id", 2)
        infos.get(2).set("parent_id", 1)
        self.put_file(self.project.filename_for("map_infos"), infos)
        self.refresh()
        # Two maps that are each other's parent: the chain ends where it
        # meets itself, with both counted once.
        self.assertEqual(self.baseline()["maps"]["max_depth"], 2)

    def test_events_and_where_the_text_sits(self):
        events = self.baseline()["events"]
        self.assertEqual(events["total"], 3)
        self.assertEqual(events["speaking"], 2)
        self.assertEqual(events["default_named_speaking"], 0)
        self.assertEqual(events["dialogue_boxes_on_action_button"], 4)
        self.assertEqual(events["dialogue_boxes_on_touch"], 0)
        self.assertEqual(events["dialogue_boxes_autorun_or_parallel"], 0)
        self.assertEqual(events["action_button_share"], 1.0)

    def test_proper_nouns_are_capitalised_mid_sentence_words(self):
        nouns = self.baseline()["proper_nouns"]
        self.assertEqual([n["name"] for n in nouns], ["Potions", "Riverbend"])
        by_name = {n["name"]: n for n in nouns}
        self.assertEqual(by_name["Riverbend"]["count"], 1)
        self.assertFalse(by_name["Riverbend"]["explained"])
        self.assertEqual(by_name["Riverbend"]["where"],
                         ["map 1 event 1 (Elder) page 0"])
        self.assertEqual(by_name["Potions"]["where"],
                         ["map 2 event 1 (Chest) page 0"])

    def test_speakers_come_from_the_face_graphic_then_the_event_name(self):
        speakers = self.baseline()["speakers"]
        self.assertEqual([s["speaker"] for s in speakers],
                         ["face Actor1 #0", "event Chest", "event Elder"])
        first = speakers[0]
        self.assertEqual((first["lines"], first["avg_words"]), (2, 9.0))
        self.assertTrue(first["samples"][0].startswith("Welcome to Riverbend"))
        self.assertEqual(speakers[1]["avg_words"], 3.0)
        self.assertEqual(speakers[2]["avg_words"], 2.0)

    def test_names_for_the_questions_to_quote(self):
        names = self.baseline()["names"]
        self.assertEqual(names["actors"], ["Ralph", "Ulrika"])
        self.assertEqual(names["maps"], ["Riverbend", "Riverbend Cave"])

    def test_the_demo_project_is_thin_in_two_named_ways(self):
        gaps = self.baseline()["gaps"]
        self.assertEqual([g["kind"] for g in gaps],
                         ["empty_descriptions", "no_display_names"])
        self.assertIn("14 of the 16", gaps[0]["text"])
        self.assertIn("1 of the 2 maps", gaps[1]["text"])

    def test_nothing_says_the_answer_is_partial(self):
        baseline = self.baseline()
        for key in baseline:
            for suffix in ("_truncated", "_stopped", "_clipped", "_not_read"):
                self.assertFalse(key.endswith(suffix), key)


class TestTheBaselineOnVX(LoreProject):
    engine = "vx"

    def test_a_vx_project_is_read_too(self):
        baseline = self.baseline()
        self.assertEqual(baseline["volume"]["dialogue"],
                         {"count": 4, "words": 23})
        self.assertEqual(baseline["volume"]["map_names"]["count"], 0)
        self.assertEqual(baseline["names"]["actors"], ["Ralph", "Ulrika"])
        self.assertEqual(baseline["database"]["tables"]["items"]["entries"], 2)
        self.assertNotIn("enemies", baseline["database"]["tables"])


class TestTheBaselineOfAProjectWithAStory(LoreProject):
    def setUp(self):
        super().setUp()
        self.story()

    def test_a_name_that_recurs_and_is_never_explained(self):
        nouns = {n["name"]: n for n in self.baseline()["proper_nouns"]}
        self.assertEqual(nouns["Brindle"]["count"], 3)
        self.assertFalse(nouns["Brindle"]["explained"])
        self.assertEqual(nouns["Brindle"]["where"],
                         ["map 3 event 1 (Warden) page 0"])

    def test_a_name_in_a_description_is_explained(self):
        nouns = {n["name"]: n for n in self.baseline()["proper_nouns"]}
        self.assertTrue(nouns["Lantern"]["explained"])
        self.assertTrue(nouns["Order"]["explained"])

    def test_the_most_frequent_name_is_first(self):
        nouns = self.baseline()["proper_nouns"]
        self.assertEqual(nouns[0]["name"], "Brindle")

    def test_text_is_sorted_by_how_the_player_reaches_it(self):
        events = self.baseline()["events"]
        self.assertEqual(events["dialogue_boxes_on_action_button"], 9)
        self.assertEqual(events["dialogue_boxes_on_touch"], 1)
        self.assertEqual(events["dialogue_boxes_autorun_or_parallel"], 1)
        self.assertEqual(events["action_button_share"], 0.82)

    def test_a_speaker_with_a_default_name_and_no_face_is_flagged(self):
        baseline = self.baseline()
        self.assertEqual(baseline["events"]["default_named_speaking"], 1)
        speakers = {s["speaker"]: s for s in baseline["speakers"]}
        self.assertEqual(speakers["unattributed"]["lines"], 1)
        self.assertEqual(speakers["face Actor2 #1"]["lines"], 4)

    def test_the_gaps_are_ranked_and_say_what_they_found(self):
        gaps = self.baseline()["gaps"]
        self.assertEqual(
            [g["kind"] for g in gaps],
            ["unexplained_name", "default_named_speakers",
             "empty_descriptions", "no_display_names"])
        self.assertIn("Brindle", gaps[0]["text"])
        self.assertIn("3 times", gaps[0]["text"])
        self.assertIn("1 ", gaps[1]["text"])
        self.assertIn("14 of the 16", gaps[2]["text"])
        self.assertIn("1 of the 3 maps", gaps[3]["text"])


class TestSpellingVariants(LoreProject):
    def variants(self, *boxes):
        self.put_map(3, "Names", [self.event(1, "Talker", [
            c for box in boxes for c in self.say("", box)])])
        return self.baseline()["spelling_variants"]

    def test_one_letter_apart(self):
        found = self.variants("Ask Brindle about it.", "Ask Brindley too.")
        self.assertIn(["Brindle", "Brindley"], found)

    def test_differing_only_in_diacritics_or_case(self):
        found = self.variants("Meet me at Café Rose.",
                              "Meet me at Cafe Rose.")
        self.assertIn(["Cafe", "Café"], found)

    def test_differing_only_in_spacing(self):
        found = self.variants("Go to Silver Gate now.",
                              "Go to Silvergate now.")
        self.assertIn(["Silver Gate", "Silvergate"], found)

    def test_short_names_one_letter_apart_are_not_variants(self):
        found = self.variants("Ask Tom now.", "Ask Tim now.", "Ask Tam now.")
        self.assertEqual(found, [])

    def test_unrelated_names_are_not(self):
        self.assertEqual(self.variants("Ask Brindle now.", "Ask Harrow now."),
                         [])


class TestAProjectWithNoText(LoreProject):
    def setUp(self):
        super().setUp()
        self.put_map(1, "MAP001", [], display=False)
        self.put_map(2, "MAP002", [], display=False)
        for item in (1, 2):
            self.set_field("items", item, "description", "")

    def test_it_is_a_clean_baseline_of_zeros_and_one_gap_that_says_so(self):
        baseline = self.baseline()
        for source in ("dialogue", "choices", "scrolling", "descriptions"):
            self.assertEqual(baseline["volume"][source],
                             {"count": 0, "words": 0})
        self.assertEqual(baseline["proper_nouns"], [])
        self.assertEqual(baseline["speakers"], [])
        self.assertEqual(baseline["gaps"][0]["kind"], "no_text")
        self.assertIn("no player-visible text", baseline["gaps"][0]["text"])

    def test_a_share_with_nothing_in_it_is_not_a_division_by_zero(self):
        events = self.baseline()["events"]
        self.assertEqual(events["action_button_share"], None)


class TestTheScanIsBoundedWhateverTheProjectHolds(LoreProject, SharedObjectMap):
    def test_a_walk_that_runs_out_says_so(self):
        self.craft_shared_map()
        baseline = self.assert_bounded("lore_bible", op="scan")["baseline"]
        self.assertIn("walk_stopped", baseline)
        self.assertIn("visits allowed", baseline["walk_stopped"])

    def test_a_huge_map_name_with_an_escape_is_clipped_and_clean(self):
        self.put_map(3, "x", [])
        game_map = self.project.load_map(3)
        game_map.set("display_name", self.S("\x1b[31m" + "A" * 1_000_006))
        self.put_file(self.project.map_file(3), game_map)
        self.refresh()
        corpus = lore_baseline.lore_scan(self.project)
        dumped = json.dumps(lore_baseline.baseline_of(corpus))
        self.assertNotIn("\x1b", dumped)
        self.assertLess(len(dumped), lore_store.MAX_BASELINE_BYTES)
        # The name was read for its start, not whole.
        self.assertLessEqual(max(len(token) for token in corpus.token_set),
                             lore_baseline._LINE_CHARS)

    def test_a_name_made_of_thousands_of_digits(self):
        self.put_map(3, "9" * 5000, [self.event(1, "8" * 5000, self.say(
            "7" * 5000, "6" * 5000))])
        baseline = self.baseline()
        self.assertLess(len(json.dumps(baseline)),
                        lore_store.MAX_BASELINE_BYTES)
        for speaker in baseline["speakers"]:
            self.assertLess(len(speaker["speaker"]), 200)

    def test_an_integer_where_a_name_belongs(self):
        game_map = self.project.load_map(2)
        game_map.set("display_name", 10 ** 5000)
        self.put_file(self.project.map_file(2), game_map)
        self.refresh()
        baseline = self.baseline()
        self.assertIn("maps", baseline)

    def test_text_that_is_not_in_the_projects_encoding(self):
        self.put_map(3, "Bytes", [self.event(1, "Raw", [
            new_event_command(101, 0, [self.S(""), 0, 0, 2]),
            new_event_command(401, 0, [RubyString(b"\xff\xfe Ask Brindle "
                                                  b"\x80now", {"E": True})])])])
        baseline = self.baseline()
        self.assertIsNone(re.search("[\ud800-\udfff]",
                                    json.dumps(baseline, ensure_ascii=False)))
        self.assertEqual(baseline["volume"]["dialogue"]["count"], 5)

    def test_a_command_list_that_contains_itself(self):
        looped = []
        looped.append(looped)
        looped.append(new_event_command(0, 0, []))
        event = self.event(1, "Loop", [])
        event.get("pages")[0].set("list", looped)
        self.put_map(3, "Loop", [event])
        self.assertEqual(self.baseline()["volume"]["dialogue"]["count"], 4)

    def test_an_event_that_is_its_own_page(self):
        event = self.event(1, "Self", self.say("", "Hello Brindle."))
        event.set("pages", [event, event.get("pages")[0]])
        self.put_map(3, "Self", [event])
        self.assertEqual(self.baseline()["volume"]["dialogue"]["count"], 5)

    def test_a_walk_is_cut_by_the_byte_budget_too(self):
        self.story()
        with mock.patch.object(lore_baseline, "_TEXT_BYTES_UPPER", 50):
            baseline = self.baseline()
        self.assertIn("walk_stopped", baseline)
        self.assertIn("bytes", baseline["walk_stopped"])

    def test_a_vocabulary_past_the_ceiling_says_so(self):
        self.story()
        with mock.patch.object(lore_baseline, "_VOCABULARY_UPPER", 10):
            corpus = lore_baseline.lore_scan(self.project)
        self.assertTrue(corpus.vocabulary_truncated)
        self.assertLessEqual(len(corpus.token_set) + len(corpus.pair_set), 10)

    def test_a_map_that_will_not_load_is_named_not_fatal(self):
        self.project.path_for(self.project.map_file(2)).write_bytes(b"\x04\x08x")
        self.refresh()
        baseline = self.baseline()
        self.assertIn("map 2", baseline["files_not_read"])
        self.assertEqual(baseline["volume"]["dialogue"]["count"], 3)


class TestTheTierTheScanSuggests(unittest.TestCase):
    def suggest(self, words):
        return lore_baseline.suggest_tier(
            {"volume": {"dialogue": {"count": 1, "words": words},
                        "scrolling": {"count": 0, "words": 0}},
             "maps": {"count": 7}})

    def test_the_thresholds(self):
        for words, tier in [(0, "light"), (1999, "light"), (2000, "moderate"),
                            (14999, "moderate"), (15000, "rich"),
                            (59999, "rich"), (60000, "deep")]:
            with self.subTest(words=words):
                self.assertEqual(self.suggest(words)["tier"], tier)

    def test_scrolling_text_counts_as_story_text(self):
        got = lore_baseline.suggest_tier(
            {"volume": {"dialogue": {"words": 1000},
                        "scrolling": {"words": 1000}}})
        self.assertEqual(got["tier"], "moderate")

    def test_the_reason_is_one_sentence_naming_the_numbers(self):
        got = self.suggest(2500)
        self.assertIn("2500 words", got["why"])
        self.assertIn("7 maps", got["why"])
        self.assertIn("estimate", got["why"])
        self.assertEqual(got["why"].count(". "), 0)
        self.assertTrue(got["why"].endswith("."))

    def test_no_baseline_means_no_suggestion(self):
        for nothing in (None, {}, [], "x", 5):
            with self.subTest(nothing=nothing):
                got = lore_baseline.suggest_tier(nothing)
                self.assertIsNone(got["tier"])
                self.assertIn("scan", got["why"])

    def test_a_hostile_baseline_is_read_as_zeros(self):
        for volume in ("x", [], {"dialogue": "x"}, {"dialogue": {"words": "9"}},
                       {"dialogue": {"words": -50000}},
                       {"dialogue": {"words": True}},
                       {"dialogue": {"words": float("nan")}},
                       {"dialogue": {"words": 10 ** 40}}):
            with self.subTest(volume=label(volume)):
                got = lore_baseline.suggest_tier({"volume": volume})
                self.assertIn(got["tier"], ("light", "deep"))


class TestReviewAgainstTheGameText(LoreProject):
    """The bible is compared with what the game really says."""

    def setUp(self):
        super().setUp()
        self.story()

    def bible(self, *entries, answers=None):
        bible = lore_store.new_bible()
        bible["entries"] = [{"kind": kind, "name": name, "text": text}
                            for kind, name, text in entries]
        bible["answers"] = dict(answers or {})
        return bible

    def review(self, bible, scopes=None, limit=60):
        corpus = lore_baseline.lore_scan(self.project)
        return lore_baseline.lore_review(corpus, bible, scopes, limit)

    def kinds(self, reply):
        return [f["kind"] for f in reply["findings"]]

    def test_an_empty_bible_has_nothing_to_compare_and_says_so(self):
        reply = self.review(lore_store.new_bible())
        self.assertEqual(reply["findings"], [])
        self.assertEqual(reply["findings_total"], 0)
        self.assertIn("empty", reply["note"])

    def test_a_name_the_game_never_says(self):
        reply = self.review(self.bible(("place", "Moonwell", "Where it began.")),
                            ["bible_to_game"])
        self.assertEqual(self.kinds(reply), ["not_in_game"])
        finding = reply["findings"][0]
        self.assertEqual(finding["where"], "bible entry (place): Moonwell")
        self.assertIn("Moonwell", finding["text"])
        self.assertTrue(finding["ask"].endswith("?"))
        self.assertIn("Moonwell", finding["ask"])

    def test_names_the_game_does_say_raise_nothing(self):
        reply = self.review(self.bible(
            ("place", "Silver Gate", "A gate."),
            ("character", "Warden", "Keeps it."),
            ("character", "Ralph", "The hero."),
            ("faction", "Order of the Lantern", "Watchers.")),
            ["bible_to_game"])
        self.assertEqual(reply["findings"], [])

    def test_a_character_the_dialogue_names_but_no_event_shows(self):
        reply = self.review(self.bible(("character", "Brindle", "A smith.")),
                            ["bible_to_game"])
        self.assertEqual(self.kinds(reply), ["character_not_an_event"])
        self.assertIn("Brindle", reply["findings"][0]["text"])

    def test_a_place_the_text_names_but_no_map_is_called(self):
        reply = self.review(self.bible(("place", "Order of the Lantern", "x")),
                            ["places"])
        self.assertEqual(sorted(self.kinds(reply)),
                         ["map_not_in_bible"] * 3 + ["place_not_a_map"])

    def test_a_map_the_bible_never_mentions(self):
        reply = self.review(self.bible(("place", "Silver Gate", "A gate.")),
                            ["places"])
        wheres = sorted(f["where"] for f in reply["findings"])
        self.assertEqual(wheres, ["map tree: Riverbend",
                                  "map tree: Riverbend Cave"])
        self.assertEqual(set(self.kinds(reply)), {"map_not_in_bible"})

    def test_a_map_the_bibles_prose_mentions_is_covered(self):
        reply = self.review(
            self.bible(("place", "Silver Gate", "A gate."),
                       ("term", "The river", "Riverbend and its cave.")),
            ["places"])
        self.assertEqual(reply["findings"], [])

    def test_a_default_named_map_is_not_asked_about(self):
        self.put_map(4, "MAP004", [], display=False)
        reply = self.review(self.bible(("place", "Silver Gate", "A gate.")),
                            ["places"])
        self.assertFalse([f for f in reply["findings"]
                          if "MAP004" in f["where"]])

    def test_a_recurring_game_name_the_bible_does_not_have(self):
        reply = self.review(self.bible(("place", "Silver Gate", "A gate.")),
                            ["game_to_bible"])
        self.assertEqual(self.kinds(reply), ["game_name_not_in_bible"])
        finding = reply["findings"][0]
        self.assertEqual(finding["where"], "map 3 event 1 (Warden) page 0")
        self.assertIn("Brindle", finding["text"])
        self.assertIn("3 times", finding["text"])
        self.assertTrue(finding["ask"].endswith("?"))

    def test_a_game_name_the_bibles_prose_covers_is_not_reported(self):
        reply = self.review(
            self.bible(("place", "Silver Gate", "Brindle made the lock.")),
            ["game_to_bible"])
        self.assertEqual(reply["findings"], [])

    def test_a_name_in_an_answer_covers_it_too(self):
        reply = self.review(
            self.bible(answers={"cast.antagonist": "Brindle, the smith."}),
            ["game_to_bible"])
        self.assertEqual(reply["findings"], [])

    def test_a_name_spelled_one_letter_differently(self):
        reply = self.review(self.bible(("character", "Brindley", "A smith.")))
        kinds = self.kinds(reply)
        self.assertIn("spelling_variant", kinds)
        self.assertNotIn("not_in_game", kinds)
        finding = [f for f in reply["findings"]
                   if f["kind"] == "spelling_variant"][0]
        self.assertIn("Brindley", finding["text"])
        self.assertIn("Brindle", finding["text"])

    def test_a_name_spelled_with_different_spacing(self):
        reply = self.review(self.bible(("place", "SilverGate", "A gate.")),
                            ["spellings"])
        self.assertEqual(self.kinds(reply), ["spelling_variant"])
        self.assertIn("Silver Gate", reply["findings"][0]["text"])

    def test_scopes_narrow_what_is_asked(self):
        bible = self.bible(("place", "Moonwell", "x"),
                           ("character", "Brindley", "x"))
        everything = set(self.kinds(self.review(bible)))
        self.assertIn("not_in_game", everything)
        self.assertIn("spelling_variant", everything)
        only = self.review(bible, ["spellings"])
        self.assertEqual(set(self.kinds(only)), {"spelling_variant"})

    def test_the_findings_are_capped_and_say_how_many_there_were(self):
        entries = [("term", "Zzxqv%s" % chr(97 + i) * 2, "x") for i in range(26)]
        entries += [("term", "Qqzxw%s" % chr(97 + i) * 2, "x")
                    for i in range(26)]
        reply = self.review(self.bible(*entries), ["bible_to_game"], 10)
        self.assertEqual(len(reply["findings"]), 10)
        self.assertEqual(reply["findings_total"], 52)
        self.assertIs(reply["findings_truncated"], True)

    def test_every_finding_carries_where_text_and_a_question(self):
        reply = self.review(self.bible(("place", "Moonwell", "x"),
                                       ("character", "Brindle", "x"),
                                       ("character", "Brindley", "x")))
        self.assertGreater(len(reply["findings"]), 2)
        for finding in reply["findings"]:
            with self.subTest(finding=finding["kind"]):
                self.assertEqual(set(finding),
                                 {"kind", "where", "text", "ask"})
                for field in finding.values():
                    self.assertIsInstance(field, str)
                    self.assertTrue(field.strip())
                self.assertTrue(finding["ask"].endswith("?"))

    def test_a_name_that_is_only_punctuation_is_skipped(self):
        reply = self.review(self.bible(("term", "!!!", "x")),
                            ["bible_to_game"])
        self.assertEqual(reply["findings"], [])

    def test_a_vocabulary_past_the_ceiling_is_said(self):
        with mock.patch.object(lore_baseline, "_VOCABULARY_UPPER", 10):
            reply = self.review(self.bible(("place", "Moonwell", "x")))
        self.assertIs(reply["vocabulary_truncated"], True)
        self.assertIn("vocabulary", reply["vocabulary_note"])

    def test_a_stopped_walk_is_said(self):
        with mock.patch.object(lore_baseline, "_TEXT_BYTES_UPPER", 50):
            reply = self.review(self.bible(("place", "Moonwell", "x")))
        self.assertIn("walk_stopped", reply)

    def test_an_unknown_scope_is_not_a_scope(self):
        with self.assertRaises(ValueError):
            self.review(self.bible(("place", "x", "x")), ["nothing"])


class TestTheQuestionsQuoteTheGame(LoreProject):
    def hooks(self, baseline=None):
        return lore_baseline.question_hooks(
            self.baseline() if baseline is None else baseline)

    def test_the_hooks_quote_names_and_counts_from_the_project(self):
        self.story()
        hooks = self.hooks()
        self.assertIn("Ralph", hooks["cast.party"])
        self.assertIn("Ulrika", hooks["cast.party"])
        self.assertIn("Silver Gate", hooks["world.places"])
        self.assertIn("Riverbend", hooks["world.places"])
        self.assertIn("Brindle", hooks["world.recent_past"])
        self.assertIn("3 times", hooks["world.recent_past"])
        self.assertIn("Warden", hooks["cast.npcs"])
        self.assertIn("3 maps", hooks["scope.length"])

    def test_every_hook_is_for_a_real_question_and_a_bounded_string(self):
        self.story()
        ids = {q["id"] for q in lore_guidance.QUESTIONS}
        for question_id, text in self.hooks().items():
            with self.subTest(question=question_id):
                self.assertIn(question_id, ids)
                self.assertIsInstance(text, str)
                self.assertLessEqual(len(text), 400)

    def test_an_unexplained_name_is_offered_once_and_only_when_it_recurs(self):
        hooks = self.hooks()   # the demo project has no recurring name
        self.assertNotIn("world.recent_past", hooks)
        self.assertNotIn("conflict.secret", hooks)

    def test_items_with_no_description_are_quoted(self):
        self.set_field("items", 1, "description", "")
        hooks = self.hooks()
        self.assertIn("1 of your 2 items", hooks["delivery.item_text"])

    def test_the_voice_question_quotes_who_talks_most(self):
        hooks = self.hooks()
        self.assertIn("face Actor1 #0", hooks["tone.voices"])
        self.assertIn("9.0", hooks["tone.voices"])

    def test_a_baseline_that_is_not_one_gives_no_hooks(self):
        for nothing in (None, {}, [], "x", 5,
                        {"names": "x", "maps": [], "proper_nouns": "x",
                         "speakers": [1, None, {"speaker": 5}],
                         "database": {"tables": {"items": "x"}}}):
            with self.subTest(nothing=label(nothing)):
                hooks = lore_baseline.question_hooks(nothing)
                self.assertIsInstance(hooks, dict)
                for text in hooks.values():
                    self.assertIsInstance(text, str)

    def test_a_hostile_name_in_a_stored_baseline_is_clipped(self):
        baseline = {"names": {"actors": ["\x1b[31mred", "A" * 5000, 5, None]},
                    "proper_nouns": [{"name": "N" * 5000, "count": 99,
                                      "explained": False}]}
        hooks = lore_baseline.question_hooks(baseline)
        for text in hooks.values():
            self.assertLessEqual(len(text), 400)
            self.assertNotIn("\x1b", text)


class _Tags(HTMLParser):
    """Every tag and attribute a page contains, read by a real HTML parser."""

    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []
        self.scripts = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend((tag, name, value) for name, value in attrs)
        if tag == "script":
            self._in_script = True
            self.scripts.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.scripts[-1] += data


HOSTILE = [
    "</script><img src=x onerror=alert(1)>",
    "<b>bold</b> & <i>italic</i>",
    '"onmouseover="alert(1)" x="',
    "&amp; &lt;script&gt;",
    "<!-- comment --> <!--[if IE]>",
    "]]> <![CDATA[ x ]]>",
    "javascript:alert(1)",
    "<svg onload=alert(1)>",
    "a b c",
    "# Heading\n## Another\n- [x](javascript:alert(1))",
    "<script>document.write('x')</script>",
]


ALLOWED_TAGS = {
    "html", "head", "meta", "title", "style", "body", "header", "h1", "p",
    "nav", "ol", "li", "a", "main", "section", "h2", "article", "h3", "div",
    "label", "textarea", "dl", "dt", "dd", "button", "span", "noscript",
    "script", "br", "footer", "em", "strong",
}


class TestTheExports(unittest.TestCase):
    def bible(self, hostile=False):
        bible = lore_store.new_bible()
        bible["tier"] = "moderate"
        bible["mode"] = "artifact"
        answers = {"premise.seed": "A ferryman seeks his daughter.\n\nShe is "
                                   "across the river.",
                   "world.traveller": "Fog, then a bell."}
        entries = [{"kind": "place", "name": "Silver Gate",
                    "text": "Opens at dusk."},
                   {"kind": "character", "name": "Brindle",
                    "text": "The smith."}]
        if hostile:
            answers["premise.theme"] = HOSTILE[0]
            answers["world.traveller"] = HOSTILE[9]
            for number, text in enumerate(HOSTILE[1:], start=1):
                entries.append({"kind": "term", "name": "t%d" % number,
                                "text": text})
            entries.append({"kind": "other", "name": HOSTILE[2][:70],
                            "text": "x"})
        bible["answers"] = answers
        bible["entries"] = entries
        return bible

    def questions(self):
        return lore_guidance.questions_for_tier("moderate")

    def html(self, bible=None, worksheet=True):
        return lore_export.render_html(bible or self.bible(),
                                       self.questions(), worksheet)

    def parsed(self, page):
        parser = _Tags()
        parser.feed(page)
        parser.close()
        return parser

    def test_the_page_is_one_self_contained_file(self):
        page = self.html()
        parsed = self.parsed(page)
        for forbidden in ("<link", "<img", "<iframe", "<object", "<embed",
                          "@import", "url(", "http://", "https://", "src="):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, page)
        self.assertEqual(parsed.tags.count("script"), 2)
        self.assertEqual(parsed.tags.count("style"), 1)
        self.assertTrue(page.startswith("<!doctype html>"))
        for tag, name, _ in parsed.attributes:
            if tag == "script":
                self.assertIn(name, ("type", "id"))

    def test_both_colour_schemes_are_styled(self):
        page = self.html()
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertRegex(page, r"body\s*\{[^}]*background")

    def test_answered_questions_are_prose_and_unanswered_are_text_areas(self):
        page = self.html()
        parsed = self.parsed(page)
        areas = {value for tag, name, value in parsed.attributes
                 if tag == "textarea" and name == "data-qid"}
        answered = {"premise.seed", "world.traveller"}
        expected = {q["id"] for q in self.questions()} - answered
        self.assertEqual(areas, expected)
        self.assertIn("<p>A ferryman seeks his daughter.</p>", page)
        self.assertIn("<p>She is across the river.</p>", page)
        self.assertIn("Fog, then a bell.", page)

    def test_a_page_without_the_worksheet_has_no_fields_and_no_button(self):
        page = self.html(worksheet=False)
        parsed = self.parsed(page)
        self.assertNotIn("textarea", parsed.tags)
        self.assertNotIn("button", parsed.tags)
        self.assertIn("A ferryman seeks his daughter.", page)

    def test_there_is_a_table_of_contents_that_points_at_real_sections(self):
        page = self.html()
        parsed = self.parsed(page)
        hrefs = [value[1:] for tag, name, value in parsed.attributes
                 if tag == "a" and name == "href"]
        ids = {value for _, name, value in parsed.attributes if name == "id"}
        self.assertGreater(len(hrefs), 5)
        for href in hrefs:
            self.assertIn(href, ids)
        self.assertIn("group-premise", hrefs)
        self.assertIn("entries", hrefs)

    def test_the_copy_button_puts_a_json_block_of_answers_on_the_clipboard(self):
        page = self.html()
        parsed = self.parsed(page)
        script = parsed.scripts[1]
        self.assertIn("Copy my answers", page)
        self.assertIn("clipboard", script)
        self.assertIn("answers", script)
        self.assertIn("JSON.stringify", script)

    def test_the_script_never_writes_markup(self):
        script = self.parsed(self.html()).scripts[1]
        for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                       "document.write", "eval(", "Function(", "setTimeout",
                       "XMLHttpRequest", "fetch(", "import(", "location"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, script)

    def test_the_data_block_is_json_with_nothing_that_can_close_a_script(self):
        page = self.html(self.bible(hostile=True))
        parsed = self.parsed(page)
        block = parsed.scripts[0]
        self.assertNotIn("<", block)
        self.assertNotIn(">", block)
        self.assertNotIn("&", block)
        data = json.loads(block)
        self.assertEqual(data["answers"]["premise.theme"], HOSTILE[0])
        self.assertIn("unanswered", data)

    def test_hostile_text_cannot_add_a_tag_or_an_attribute(self):
        page = self.html(self.bible(hostile=True))
        parsed = self.parsed(page)
        self.assertEqual(set(parsed.tags) - ALLOWED_TAGS, set())
        for tag, name, value in parsed.attributes:
            self.assertFalse(name.startswith("on"), (tag, name, value))
            self.assertNotIn(name, ("src", "srcdoc", "style", "formaction"))
        # And the strings arrive as text, escaped, rather than vanishing.
        self.assertIn("&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;", page)
        self.assertIn("&quot;onmouseover=&quot;alert(1)&quot;", page)
        self.assertEqual(page.count("<script"), 2)

    def test_the_page_is_the_same_each_time(self):
        self.assertEqual(self.html(), self.html())

    def test_markdown_is_the_plain_text_equivalent(self):
        text = lore_export.render_markdown(self.bible(), self.questions(), True)
        self.assertTrue(text.startswith("# Lore bible\n"))
        self.assertIn("## Premise and theme", text)
        self.assertIn("A ferryman seeks his daughter.", text)
        self.assertIn("**Silver Gate**", text)
        self.assertIn("not answered yet", text)
        without = lore_export.render_markdown(self.bible(), self.questions(),
                                              False)
        self.assertNotIn("not answered yet", without)
        self.assertIn("A ferryman seeks his daughter.", without)

    def test_markdown_cannot_be_made_to_add_headings_or_html(self):
        text = lore_export.render_markdown(self.bible(hostile=True),
                                           self.questions(), True)
        headings = [line for line in text.split("\n") if line.startswith("#")]
        title_and_groups = {"# Lore bible", "## Entries"} | {
            "## " + label for key, label in lore_guidance.GROUPS}
        for line in headings:
            if line.startswith("### "):
                continue
            self.assertIn(line, title_and_groups)
        self.assertNotIn("<script", text)
        self.assertNotIn("<img", text)
        self.assertNotIn("<svg", text)
        self.assertIn("&lt;script&gt;", text)

    def test_an_empty_bible_exports_cleanly(self):
        empty = lore_store.new_bible()
        page = lore_export.render_html(empty, self.questions(), True)
        fields = [1 for tag, name, _ in self.parsed(page).attributes
                  if tag == "textarea" and name == "data-qid"]
        self.assertEqual(len(fields), len(self.questions()))
        text = lore_export.render_markdown(empty, self.questions(), False)
        self.assertIn("# Lore bible", text)

    def test_an_answer_to_a_question_this_server_does_not_have_is_kept(self):
        bible = self.bible()
        bible["answers"]["premise.renamed_later"] = "Kept anyway."
        page = lore_export.render_html(bible, self.questions(), True)
        self.assertIn("Kept anyway.", page)
        self.assertIn("premise.renamed_later", page)


class ToolCase(LoreProject):
    """`lore_bible` called the way a client calls it."""

    def lore(self, **arguments):
        return self.call("lore_bible", **arguments)

    @property
    def lore_folder(self):
        return self.tmp / "project" / ".rpgvxace-mcp" / "lore"

    def refused(self, *words, **arguments):
        with self.assertRaises(ToolError) as caught:
            self.lore(**arguments)
        message = str(caught.exception)
        for word in words:
            self.assertIn(word, message)
        return message

    def data_snapshot(self):
        data = self.tmp / "project" / "Data"
        return {path.name: path.read_bytes() for path in sorted(data.iterdir())
                if path.is_file()}


class TestStatus(ToolCase):
    def test_a_fresh_project_points_at_the_scan(self):
        status = self.lore(op="status")
        self.assertIsNone(status["tier"])
        self.assertIsNone(status["mode"])
        self.assertEqual(status["answered"], 0)
        self.assertEqual(status["total"], len(
            lore_guidance.questions_for_tier("light")))
        self.assertIs(status["baseline"]["scanned"], False)
        self.assertIn("op=scan", status["next_step"])
        self.assertEqual(set(status["progress"]),
                         {key for key, _ in lore_guidance.GROUPS})

    def test_status_writes_nothing(self):
        self.lore(op="status")
        self.assertFalse(self.lore_folder.exists())

    def test_progress_counts_answers_per_group_for_the_chosen_tier(self):
        self.lore(op="choose", tier="moderate", mode="qa")
        self.lore(op="save", answers={"premise.seed": "x", "premise.theme": "y",
                                      "world.traveller": "z"})
        status = self.lore(op="status")
        total = len(lore_guidance.questions_for_tier("moderate"))
        self.assertEqual(status["total"], total)
        self.assertEqual(status["answered"], 3)
        premise = [q for q in lore_guidance.questions_for_tier("moderate")
                   if q["group"] == "premise"]
        self.assertEqual(status["progress"]["premise"],
                         {"answered": 2, "total": len(premise)})
        self.assertEqual(status["progress"]["world"]["answered"], 1)

    def test_the_next_step_follows_where_the_author_is(self):
        self.assertIn("op=scan", self.lore(op="status")["next_step"])
        self.lore(op="scan")
        self.assertIn("op=tiers", self.lore(op="status")["next_step"])
        self.lore(op="choose", tier="light", mode="qa")
        self.assertIn("op=next", self.lore(op="status")["next_step"])
        answers = {q["id"]: "answer" for q in
                   lore_guidance.questions_for_tier("light")}
        self.lore(op="save", answers=answers)
        step = self.lore(op="status")["next_step"]
        self.assertIn("op=review", step)
        self.assertIn("op=export", step)

    def test_answers_to_ids_this_server_lacks_are_counted_not_hidden(self):
        folder = self.lore_folder
        folder.mkdir(parents=True)
        bible = lore_store.new_bible()
        bible["answers"] = {"premise.renamed_later": "old"}
        (folder / "bible.json").write_text(json.dumps(bible), encoding="utf-8")
        self.assertEqual(self.lore(op="status")["unknown_answers"], 1)

    def test_entries_are_counted_by_kind(self):
        self.lore(op="save", entries=[
            {"kind": "place", "name": "A", "text": ""},
            {"kind": "place", "name": "B", "text": ""},
            {"kind": "term", "name": "C", "text": ""}])
        status = self.lore(op="status")
        self.assertEqual(status["entries"], {"place": 2, "term": 1})
        self.assertEqual(status["entries_total"], 3)

    def test_a_pitfall_is_named_when_the_scan_found_its_symptom(self):
        self.story()
        self.lore(op="scan")
        ids = [p["id"] for p in self.lore(op="status")["pitfalls"]]
        self.assertIn("lore_needs_a_character", ids)

    def test_lore_with_no_stated_delivery_is_flagged(self):
        self.lore(op="save", entries=[{"kind": "term", "name": "A",
                                       "text": "x"}])
        ids = [p["id"] for p in self.lore(op="status")["pitfalls"]]
        self.assertIn("no_delivery_surface", ids)
        self.lore(op="save", answers={"delivery.channels": "NPCs mostly"})
        ids = [p["id"] for p in self.lore(op="status")["pitfalls"]]
        self.assertNotIn("no_delivery_surface", ids)


class TestScan(ToolCase):
    def test_it_returns_the_baseline_and_the_gaps_and_saves_them(self):
        reply = self.lore(op="scan")
        self.assertIs(reply["saved"], True)
        self.assertEqual(reply["baseline"]["volume"]["dialogue"]["count"], 4)
        self.assertEqual([g["kind"] for g in reply["gaps"]],
                         ["empty_descriptions", "no_display_names"])
        self.assertNotIn("gaps", reply["baseline"])
        stored = lore_store.load(self.project)["baseline"]
        self.assertEqual(stored["volume"], reply["baseline"]["volume"])
        self.assertEqual(stored["gaps"], reply["gaps"])
        self.assertIn("op=tiers", reply["next_step"])

    def test_a_scan_changes_no_game_file(self):
        before = self.data_snapshot()
        self.lore(op="scan")
        self.assertEqual(self.data_snapshot(), before)

    def test_a_scan_keeps_what_the_author_already_said(self):
        self.lore(op="choose", tier="rich", mode="qa")
        self.lore(op="save", answers={"premise.seed": "kept"})
        self.lore(op="scan")
        bible = lore_store.load(self.project)
        self.assertEqual(bible["tier"], "rich")
        self.assertEqual(bible["answers"], {"premise.seed": "kept"})

    def test_under_read_only_it_answers_and_saves_nothing_and_says_so(self):
        self.project.read_only = True
        reply = self.lore(op="scan")
        self.assertIs(reply["saved"], False)
        self.assertEqual(reply["baseline"]["volume"]["dialogue"]["count"], 4)
        self.assertIn("read-only", reply["baseline_note"])
        self.assertFalse(self.lore_folder.exists())

    def test_refresh_drops_the_copies_in_memory_and_nothing_else_does(self):
        with mock.patch.object(Project, "forget_cached",
                               autospec=True) as dropped:
            self.lore(op="scan")
            self.assertEqual(dropped.call_count, 0)
            self.lore(op="scan", refresh=True)
            self.assertEqual(dropped.call_count, 1)

    def test_arguments_it_does_not_read_are_refused(self):
        self.refused("scan", "count", op="scan", count=3)
        self.refused("scan", "tier", op="scan", tier="light")

    def test_refresh_must_be_a_boolean(self):
        for value in ("yes", 1, [], {}, 1.5):
            with self.subTest(value=label(value)):
                self.refused("refresh", op="scan", refresh=value)


class TestScanOnXP(ToolCase):
    engine = "xp"

    def test_scan_and_review_are_refused_for_the_reason_text_gives(self):
        for op in ("scan", "review"):
            with self.subTest(op=op):
                message = self.refused(
                    self.project.profile.label, "first line", op=op)
                self.assertIn("VX Ace and VX", message)

    def test_the_ops_that_read_no_text_still_work(self):
        self.lore(op="tiers")
        self.lore(op="choose", tier="light", mode="qa")
        self.lore(op="save", answers={"premise.seed": "x"})
        self.lore(op="status")
        self.lore(op="next")
        self.lore(op="export")


class TestTiers(ToolCase):
    def test_four_tiers_with_what_the_research_gave(self):
        reply = self.lore(op="tiers")
        self.assertEqual([t["key"] for t in reply["tiers"]],
                         list(lore_guidance.TIER_ORDER))
        for tier in reply["tiers"]:
            with self.subTest(tier=tier["key"]):
                self.assertIn(len(tier["comparison_games"]), (2, 3))
                self.assertIs(tier["asks"]["estimate"], True)
                self.assertGreater(tier["interview_questions"], 10)
                self.assertTrue(tier["biggest_risk"])
                self.assertTrue(tier["player_meets_lore"])
        counts = [t["interview_questions"] for t in reply["tiers"]]
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(counts[-1], 60)
        self.assertIn("estimate", reply["estimates_note"])
        self.assertIn("observation", reply["small_team_note"])

    def test_the_three_modes_are_explained(self):
        modes = self.lore(op="tiers")["modes"]
        self.assertEqual(set(modes), {"qa", "artifact", "custom"})
        self.assertIn("approach", modes["custom"])

    def test_no_scan_means_no_suggestion_and_says_to_scan(self):
        reply = self.lore(op="tiers")
        self.assertIsNone(reply["suggested_tier"])
        self.assertIn("scan", reply["suggestion"])

    def test_after_a_scan_the_suggestion_comes_from_the_baseline(self):
        self.lore(op="scan")
        reply = self.lore(op="tiers")
        self.assertEqual(reply["suggested_tier"], "light")
        self.assertIn("23 words", reply["suggestion"])

    def test_tiers_writes_nothing_and_takes_no_arguments(self):
        self.lore(op="tiers")
        self.assertFalse(self.lore_folder.exists())
        self.refused("tiers", "tier", op="tiers", tier="light")

    def test_the_chosen_tier_is_shown(self):
        self.lore(op="choose", tier="deep", mode="qa")
        self.assertEqual(self.lore(op="tiers")["chosen_tier"], "deep")


class TestChoose(ToolCase):
    def test_a_tier_and_a_mode_are_kept(self):
        reply = self.lore(op="choose", tier="moderate", mode="artifact")
        self.assertEqual((reply["tier"], reply["mode"]),
                         ("moderate", "artifact"))
        self.assertEqual(reply["questions_in_interview"], len(
            lore_guidance.questions_for_tier("moderate")))
        bible = lore_store.load(self.project)
        self.assertEqual((bible["tier"], bible["mode"], bible["approach"]),
                         ("moderate", "artifact", None))

    def test_each_mode_says_what_happens_next(self):
        qa = self.lore(op="choose", tier="light", mode="qa")
        self.assertIn("op=next", qa["what_happens_next"])
        self.assertIn("op=save", qa["what_happens_next"])
        art = self.lore(op="choose", tier="light", mode="artifact")
        self.assertIn("op=export", art["what_happens_next"])
        self.assertIn("worksheet", art["what_happens_next"])
        self.assertIn("Copy my answers", art["what_happens_next"])
        custom = self.lore(op="choose", tier="light", mode="custom",
                           approach="Ask me one thing each morning.")
        self.assertIn("one thing each morning", custom["what_happens_next"])
        self.assertEqual(custom["approach"], "Ask me one thing each morning.")
        self.assertEqual(lore_store.load(self.project)["approach"],
                         "Ask me one thing each morning.")

    def test_a_custom_mode_needs_its_approach(self):
        self.refused("approach", op="choose", tier="light", mode="custom")
        self.refused("approach", op="choose", tier="light", mode="custom",
                     approach="   ")

    def test_an_approach_belongs_to_a_custom_mode_only(self):
        self.refused("approach", "custom", op="choose", tier="light",
                     mode="qa", approach="my way")

    def test_choosing_again_replaces_the_choice_and_drops_an_old_approach(self):
        self.lore(op="choose", tier="light", mode="custom", approach="mine")
        self.lore(op="choose", tier="rich", mode="qa")
        bible = lore_store.load(self.project)
        self.assertEqual((bible["tier"], bible["mode"], bible["approach"]),
                         ("rich", "qa", None))

    def test_choosing_keeps_answers_and_entries(self):
        self.lore(op="save", answers={"premise.seed": "x"},
                  entries=[{"kind": "term", "name": "T", "text": "t"}])
        self.lore(op="choose", tier="deep", mode="qa")
        bible = lore_store.load(self.project)
        self.assertEqual(bible["answers"], {"premise.seed": "x"})
        self.assertEqual(len(bible["entries"]), 1)

    def test_a_tier_or_mode_that_does_not_exist(self):
        for tier in ("legendary", "", None, 3, ["light"], True):
            with self.subTest(tier=label(tier)):
                self.refused("tier", op="choose", tier=tier, mode="qa")
        for mode in ("telepathy", "", None, 3, ["qa"]):
            with self.subTest(mode=label(mode)):
                self.refused("mode", op="choose", tier="light", mode=mode)

    def test_both_are_required(self):
        self.refused("tier", op="choose", mode="qa")
        self.refused("mode", op="choose", tier="light")

    def test_an_approach_over_the_ceiling(self):
        self.refused("approach", op="choose", tier="light", mode="custom",
                     approach="x" * (lore_store.MAX_APPROACH_CHARS + 1))

    def test_a_read_only_server_will_not_choose(self):
        self.project.read_only = True
        with self.assertRaises(ReadOnlyError):
            self.lore(op="choose", tier="light", mode="qa")
        self.assertFalse(self.lore_folder.exists())

    def test_choose_changes_no_game_file(self):
        before = self.data_snapshot()
        self.lore(op="choose", tier="light", mode="qa")
        self.assertEqual(self.data_snapshot(), before)


class TestNext(ToolCase):
    def setUp(self):
        super().setUp()
        self.lore(op="choose", tier="light", mode="qa")

    def test_the_first_questions_are_the_first_of_the_chosen_tier(self):
        reply = self.lore(op="next")
        wanted = lore_guidance.questions_for_tier("light")[:3]
        self.assertEqual([q["id"] for q in reply["questions"]],
                         [q["id"] for q in wanted])
        first = reply["questions"][0]
        self.assertEqual(set(first) - {"from_your_game"},
                         {"id", "group", "tier", "text", "why",
                          "good_answer"})
        self.assertEqual(first["text"], wanted[0]["text"])
        self.assertEqual(first["good_answer"], wanted[0]["hint"])
        self.assertEqual(reply["remaining"], len(
            lore_guidance.questions_for_tier("light")) - 3)

    def test_count_picks_how_many(self):
        for count in (1, 7, lore_store.MAX_QUESTIONS_PER_CALL):
            with self.subTest(count=count):
                self.assertEqual(
                    len(self.lore(op="next", count=count)["questions"]), count)

    def test_count_is_bounded_and_strict(self):
        for count in (0, -1, lore_store.MAX_QUESTIONS_PER_CALL + 1, True, 2.5,
                      "3", [3], 10 ** 5000):
            with self.subTest(count=label(count)):
                self.refused("count", op="next", count=count)

    def test_an_answered_question_is_not_asked_again(self):
        first = self.lore(op="next")["questions"][0]["id"]
        self.lore(op="save", answers={first: "done"})
        ids = [q["id"] for q in self.lore(op="next")["questions"]]
        self.assertNotIn(first, ids)
        self.assertEqual(len(ids), 3)

    def test_a_group_narrows_the_questions(self):
        reply = self.lore(op="next", group="world", count=10)
        self.assertEqual({q["group"] for q in reply["questions"]}, {"world"})
        self.assertEqual(len(reply["questions"]), 3)
        self.assertEqual(reply["remaining"], 0)
        self.refused("group", op="next", group="nonsense")

    def test_a_higher_tier_asks_more(self):
        light = self.lore(op="next", count=10, group="premise")["questions"]
        self.lore(op="choose", tier="deep", mode="qa")
        deep = self.lore(op="next", count=10, group="premise")["questions"]
        self.assertGreater(len(deep), len(light))

    def test_a_tier_is_needed_first(self):
        self.lore(op="choose", tier="light", mode="qa")
        folder = self.lore_folder
        bible = lore_store.load(self.project)
        bible["tier"] = None
        bible["mode"] = None
        lore_store.save(self.project, bible)
        self.refused("tier", "op=choose", op="next")
        self.assertTrue(folder.exists())

    def test_the_scan_gives_a_question_a_line_from_the_game(self):
        self.story()
        self.lore(op="scan")
        reply = self.lore(op="next", count=10, group="world")
        by_id = {q["id"]: q for q in reply["questions"]}
        self.assertIn("Silver Gate", by_id["world.places"]["from_your_game"])

    def test_without_a_scan_there_is_no_line_from_the_game(self):
        for question in self.lore(op="next", count=10)["questions"]:
            self.assertNotIn("from_your_game", question)

    def test_when_everything_is_answered_it_says_so(self):
        answers = {q["id"]: "a" for q in
                   lore_guidance.questions_for_tier("light")}
        self.lore(op="save", answers=answers)
        reply = self.lore(op="next")
        self.assertEqual(reply["questions"], [])
        self.assertEqual(reply["remaining"], 0)
        self.assertIn("op=review", reply["next_step"])

    def test_next_writes_nothing(self):
        before = (self.lore_folder / "bible.json").read_bytes()
        self.lore(op="next")
        self.assertEqual((self.lore_folder / "bible.json").read_bytes(), before)

    def test_arguments_it_does_not_read_are_refused(self):
        self.refused("next", "answers", op="next", answers={})


class TestSave(ToolCase):
    def test_answers_are_kept_and_reported(self):
        reply = self.lore(op="save", answers={"premise.seed": "A ferryman.",
                                              "world.traveller": "Fog."})
        self.assertEqual(reply["answers_saved"],
                         ["premise.seed", "world.traveller"])
        self.assertEqual(lore_store.load(self.project)["answers"],
                         {"premise.seed": "A ferryman.",
                          "world.traveller": "Fog."})
        self.assertEqual(reply["answered"], 2)

    def test_an_answer_can_be_replaced_and_an_empty_one_clears_it(self):
        self.lore(op="save", answers={"premise.seed": "one"})
        self.lore(op="save", answers={"premise.seed": "two"})
        self.assertEqual(lore_store.load(self.project)["answers"],
                         {"premise.seed": "two"})
        reply = self.lore(op="save", answers={"premise.seed": "  "})
        self.assertEqual(reply["answers_cleared"], ["premise.seed"])
        self.assertEqual(lore_store.load(self.project)["answers"], {})

    def test_an_answer_keeps_its_line_breaks(self):
        self.lore(op="save", answers={"premise.seed": "a\n\nb"})
        self.assertEqual(
            lore_store.load(self.project)["answers"]["premise.seed"], "a\n\nb")

    def test_a_question_this_server_does_not_ask_is_refused(self):
        message = self.refused("premise.renamed", "op=next",
                               op="save", answers={"premise.renamed": "x"})
        self.assertNotIn("x" * 50, message)
        self.assertFalse(self.lore_folder.exists())

    def test_bad_answers_are_refused_whole(self):
        for answers, word in [
                ({"premise.seed": 5}, "premise.seed"),
                ({"premise.seed": None}, "premise.seed"),
                ({"premise.seed": ["x"]}, "premise.seed"),
                ({"premise.seed": "x" * (lore_store.MAX_ANSWER_CHARS + 1)},
                 "premise.seed"),
                ({"premise.seed": "bad \x00 byte"}, "premise.seed"),
                ({"premise.seed": "lone \ud800"}, "premise.seed"),
                ([], "answers"), ("x", "answers"), (5, "answers"),
                ({5: "x"}, "answers")]:
            with self.subTest(answers=label(answers)):
                self.refused(word, op="save", answers=answers)
        self.assertFalse(self.lore_folder.exists())

    def test_one_bad_answer_saves_none_of_the_good_ones(self):
        self.refused("world.traveller", op="save",
                     answers={"premise.seed": "fine", "world.traveller": 7})
        self.assertFalse(self.lore_folder.exists())

    def test_more_answers_than_there_are_questions_is_refused_early(self):
        flood = {"premise.q%d" % i: "x" for i in range(100_000)}
        self.refused("at most", "answers", op="save", answers=flood)

    def test_entries_are_added_then_updated_by_kind_and_name(self):
        first = self.lore(op="save", entries=[
            {"kind": "place", "name": "Silver Gate", "text": "Opens at dusk."},
            {"kind": "character", "name": "Brindle", "text": "A smith."}])
        self.assertEqual(first["entries_added"], ["Silver Gate", "Brindle"])
        again = self.lore(op="save", entries=[
            {"kind": "place", "name": "silver gate", "text": "Opens at dawn."}])
        self.assertEqual(again["entries_updated"], ["silver gate"])
        self.assertEqual(again["entries_added"], [])
        entries = lore_store.load(self.project)["entries"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["text"], "Opens at dawn.")
        self.assertEqual(entries[0]["name"], "Silver Gate")

    def test_the_same_name_of_another_kind_is_another_entry(self):
        self.lore(op="save", entries=[
            {"kind": "place", "name": "Brindle", "text": "a town"},
            {"kind": "character", "name": "Brindle", "text": "a smith"}])
        self.assertEqual(len(lore_store.load(self.project)["entries"]), 2)

    def test_entries_are_deleted_by_name_and_a_missing_one_is_reported(self):
        self.lore(op="save", entries=[
            {"kind": "place", "name": "A", "text": ""},
            {"kind": "term", "name": "A", "text": ""},
            {"kind": "term", "name": "B", "text": ""}])
        reply = self.lore(op="save", delete_entries=["a", "Nope"])
        self.assertEqual(reply["entries_removed"], ["a"])
        self.assertEqual(reply["entries_not_found"], ["Nope"])
        names = [e["name"] for e in lore_store.load(self.project)["entries"]]
        self.assertEqual(names, ["B"])

    def test_deleting_and_adding_the_same_name_in_one_call_replaces_it(self):
        self.lore(op="save", entries=[{"kind": "term", "name": "A",
                                       "text": "old"}])
        self.lore(op="save", delete_entries=["A"],
                  entries=[{"kind": "term", "name": "A", "text": "new"}])
        entries = lore_store.load(self.project)["entries"]
        self.assertEqual([e["text"] for e in entries], ["new"])

    def test_bad_entries_are_refused(self):
        ok = {"kind": "place", "name": "Gate", "text": "t"}
        for entries, word in [
                ([5], "entries[0]"), ([{"kind": "place", "name": "G"}], "text"),
                ([{**ok, "kind": "planet"}], "kind"),
                ([{**ok, "name": ""}], "name"),
                ([{**ok, "name": "n" * 81}], "name"),
                ([{**ok, "name": "two\nlines"}], "name"),
                ([{**ok, "text": "t" * 2001}], "text"),
                ([{**ok, "text": 5}], "text"),
                ([{**ok, "extra": 1}], "extra"),
                ({}, "entries"), ("x", "entries")]:
            with self.subTest(entries=label(entries)):
                self.refused(word, op="save", entries=entries)

    def test_more_entries_in_one_call_than_the_ceiling(self):
        many = [{"kind": "term", "name": "n%d" % i, "text": ""}
                for i in range(lore_store.MAX_ENTRIES_PER_SAVE + 1)]
        self.refused("at most", op="save", entries=many)

    def test_the_bible_holds_at_most_so_many_entries(self):
        batch = lore_store.MAX_ENTRIES_PER_SAVE
        for start in range(0, lore_store.MAX_BIBLE_ENTRIES, batch):
            self.lore(op="save", entries=[
                {"kind": "term", "name": "n%d" % i, "text": ""}
                for i in range(start, start + batch)])
        self.refused("500", op="save", entries=[
            {"kind": "term", "name": "one more", "text": ""}])
        self.assertEqual(len(lore_store.load(self.project)["entries"]),
                         lore_store.MAX_BIBLE_ENTRIES)

    def test_two_entries_of_one_name_in_one_call(self):
        self.refused("twice", op="save", entries=[
            {"kind": "term", "name": "A", "text": ""},
            {"kind": "term", "name": "a", "text": ""}])

    def test_delete_entries_is_bounded_and_strict(self):
        self.refused("at most", op="save", delete_entries=[
            "n%d" % i for i in range(lore_store.MAX_DELETE_NAMES + 1)])
        for bad in ([5], [None], [["x"]], ["x" * 81], "x", 5):
            with self.subTest(bad=label(bad)):
                self.refused("delete_entries", op="save", delete_entries=bad)

    def test_a_save_with_nothing_in_it_is_refused(self):
        self.refused("answers", "entries", "delete_entries", op="save")
        self.refused("answers", op="save", answers={})

    def test_a_read_only_server_will_not_save(self):
        self.project.read_only = True
        with self.assertRaises(ReadOnlyError):
            self.lore(op="save", answers={"premise.seed": "x"})
        self.assertFalse(self.lore_folder.exists())

    def test_the_whole_bible_is_capped_and_the_old_one_stands(self):
        for start in range(0, 400, 100):
            self.lore(op="save", entries=[
                {"kind": "term", "name": "n%d" % i,
                 "text": "x" * lore_store.MAX_ENTRY_TEXT_CHARS}
                for i in range(start, start + 100)])
        before = (self.lore_folder / "bible.json").read_bytes()
        with self.assertRaises(ProjectError) as caught:
            self.lore(op="save", entries=[
                {"kind": "term", "name": "big%d" % i,
                 "text": "y" * lore_store.MAX_ENTRY_TEXT_CHARS}
                for i in range(100)])
        self.assertIn("%d bytes" % lore_store.MAX_BIBLE_BYTES,
                      str(caught.exception))
        self.assertEqual((self.lore_folder / "bible.json").read_bytes(), before)

    def test_save_changes_no_game_file(self):
        before = self.data_snapshot()
        self.lore(op="save", answers={"premise.seed": "x"},
                  entries=[{"kind": "term", "name": "T", "text": "t"}])
        self.assertEqual(self.data_snapshot(), before)


class TestReviewThroughTheTool(ToolCase):
    def setUp(self):
        super().setUp()
        self.story()

    def test_review_finds_what_the_bible_and_the_game_disagree_about(self):
        self.lore(op="save", entries=[
            {"kind": "place", "name": "Moonwell", "text": "Where it began."},
            {"kind": "character", "name": "Brindley", "text": "A smith."},
            {"kind": "place", "name": "Silver Gate", "text": "A gate."}])
        reply = self.lore(op="review")
        kinds = {f["kind"] for f in reply["findings"]}
        self.assertIn("not_in_game", kinds)
        self.assertIn("spelling_variant", kinds)
        self.assertIn("map_not_in_bible", kinds)
        self.assertEqual(reply["findings_total"], len(reply["findings"]))
        self.assertIn("op=export", reply["next_step"])

    def test_the_findings_name_the_pitfall_they_are_an_instance_of(self):
        self.lore(op="save", entries=[{"kind": "place", "name": "Moonwell",
                                       "text": "x"}])
        ids = [p["id"] for p in self.lore(op="review")["relevant_pitfalls"]]
        self.assertIn("no_delivery_surface", ids)

    def test_scopes_narrow_the_review(self):
        self.lore(op="save", entries=[{"kind": "place", "name": "Moonwell",
                                       "text": "x"}])
        reply = self.lore(op="review", scopes=["spellings"])
        self.assertEqual(reply["findings"], [])

    def test_scopes_are_checked(self):
        self.refused("nothing", op="review", scopes=["nothing"])
        for bad in ("spellings", 5, [5], [["spellings"]]):
            with self.subTest(bad=label(bad)):
                with self.assertRaises(ToolError):
                    self.lore(op="review", scopes=bad)

    def test_an_empty_bible_is_told_to_save_something_first(self):
        reply = self.lore(op="review")
        self.assertEqual(reply["findings"], [])
        self.assertIn("empty", reply["note"])

    def test_review_writes_nothing_and_changes_no_game_file(self):
        self.lore(op="save", answers={"premise.seed": "x"})
        before = (self.data_snapshot(),
                  (self.lore_folder / "bible.json").read_bytes())
        self.lore(op="review")
        self.assertEqual((self.data_snapshot(),
                          (self.lore_folder / "bible.json").read_bytes()),
                         before)

    def test_review_works_under_read_only(self):
        self.lore(op="save", entries=[{"kind": "place", "name": "Moonwell",
                                       "text": "x"}])
        self.project.read_only = True
        self.assertTrue(self.lore(op="review")["findings"])

    def test_a_hand_edited_bible_is_refused_not_crashed_on(self):
        self.lore(op="save", answers={"premise.seed": "x"})
        (self.lore_folder / "bible.json").write_text("[1, 2", encoding="utf-8")
        for op in ("status", "next", "review", "tiers", "export"):
            with self.subTest(op=op):
                args = {"op": op}
                with self.assertRaises(ProjectError) as caught:
                    self.lore(**args)
                self.assertIn("bible.json", str(caught.exception))


class TestExportThroughTheTool(ToolCase):
    def test_both_files_are_written_into_the_lore_folder(self):
        self.lore(op="choose", tier="light", mode="artifact")
        self.lore(op="save", answers={"premise.seed": "A ferryman."})
        reply = self.lore(op="export", worksheet=True)
        html_path = Path(reply["paths"]["html"])
        md_path = Path(reply["paths"]["markdown"])
        self.assertEqual(html_path, (self.lore_folder / "bible.html").resolve())
        self.assertEqual(md_path, (self.lore_folder / "bible.md").resolve())
        self.assertIn("A ferryman.", html_path.read_text(encoding="utf-8"))
        self.assertIn("A ferryman.", md_path.read_text(encoding="utf-8"))
        self.assertEqual(reply["bytes"]["html"], html_path.stat().st_size)
        self.assertIs(reply["worksheet"], True)

    def test_the_worksheet_asks_the_chosen_tiers_unanswered_questions(self):
        self.lore(op="choose", tier="moderate", mode="artifact")
        self.lore(op="save", answers={"premise.seed": "x"})
        reply = self.lore(op="export", worksheet=True)
        page = Path(reply["paths"]["html"]).read_text(encoding="utf-8")
        ids = set(re.findall(r'data-qid="([a-z_.0-9]+)"', page))
        wanted = {q["id"] for q in
                  lore_guidance.questions_for_tier("moderate")} - {
                      "premise.seed"}
        self.assertEqual(ids, wanted)
        self.assertEqual(reply["worksheet_fields"], len(wanted))

    def test_without_a_tier_the_lightest_is_used(self):
        reply = self.lore(op="export", worksheet=True)
        page = Path(reply["paths"]["html"]).read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"data-qid=", page)), len(
            lore_guidance.questions_for_tier("light")))

    def test_formats_choose_which_files(self):
        reply = self.lore(op="export", formats=["markdown"])
        self.assertEqual(set(reply["paths"]), {"markdown"})
        self.assertFalse((self.lore_folder / "bible.html").exists())
        self.assertTrue((self.lore_folder / "bible.md").is_file())

    def test_formats_are_checked(self):
        for bad in (["pdf"], [], "html", ["html", 5], [None], 7):
            with self.subTest(bad=label(bad)):
                with self.assertRaises(ToolError):
                    self.lore(op="export", formats=bad)
        reply = self.lore(op="export", formats=["html", "html"])
        self.assertEqual(set(reply["paths"]), {"html"})

    def test_it_works_under_read_only_and_says_it_wrote_a_cache_file(self):
        self.project.read_only = True
        reply = self.lore(op="export")
        self.assertIn("read-only", reply["export_note"])
        self.assertTrue((self.lore_folder / "bible.html").is_file())
        self.assertFalse((self.lore_folder / "bible.json").exists())

    def test_hostile_text_in_the_bible_does_not_escape_the_page(self):
        self.lore(op="save", answers={"premise.seed": HOSTILE[0]},
                  entries=[{"kind": "term", "name": "x", "text": HOSTILE[1]}])
        reply = self.lore(op="export", worksheet=True)
        page = Path(reply["paths"]["html"]).read_text(encoding="utf-8")
        self.assertNotIn("<img src=x", page)
        self.assertNotIn("<b>bold</b>", page)
        self.assertEqual(page.count("<script"), 2)

    def test_a_link_where_a_file_goes_refuses_the_whole_export(self):
        self.lore(op="export")
        first = (self.lore_folder / "bible.html").read_bytes()
        with mock.patch.object(lore_store, "_is_reparse_point",
                               lambda path: Path(path).name == "bible.md"):
            with self.assertRaises(ProjectError) as caught:
                self.lore(op="export", worksheet=True)
        self.assertIn("link", str(caught.exception))
        self.assertEqual((self.lore_folder / "bible.html").read_bytes(), first)

    def test_export_changes_no_game_file(self):
        before = self.data_snapshot()
        self.lore(op="export", worksheet=True)
        self.assertEqual(self.data_snapshot(), before)

    def test_worksheet_must_be_a_boolean(self):
        for value in ("yes", 1, [], 1.5):
            with self.subTest(value=label(value)):
                with self.assertRaises(ToolError):
                    self.lore(op="export", worksheet=value)


class TestTheWholeFlow(ToolCase):
    def test_scan_tiers_choose_ask_save_review_export(self):
        self.story()
        scan = self.lore(op="scan")
        self.assertIn("unexplained_name", [g["kind"] for g in scan["gaps"]])
        tiers = self.lore(op="tiers")
        self.assertIn(tiers["suggested_tier"], lore_guidance.TIER_ORDER)
        self.lore(op="choose", tier=tiers["suggested_tier"], mode="qa")
        asked = self.lore(op="next", count=3)
        self.assertEqual(len(asked["questions"]), 3)
        self.lore(op="save",
                  answers={asked["questions"][0]["id"]: "A ferryman."},
                  entries=[{"kind": "character", "name": "Brindle",
                            "text": "The smith who forged the lock."}])
        status = self.lore(op="status")
        self.assertEqual(status["answered"], 1)
        review = self.lore(op="review")
        self.assertNotIn("game_name_not_in_bible",
                         [f["kind"] for f in review["findings"]])
        exported = self.lore(op="export")
        self.assertTrue(Path(exported["paths"]["html"]).is_file())

    def test_the_whole_flow_changes_no_game_file(self):
        self.story()
        before = self.data_snapshot()
        for arguments in (
                {"op": "scan"}, {"op": "tiers"},
                {"op": "choose", "tier": "light", "mode": "qa"},
                {"op": "next"}, {"op": "save", "answers": {
                    "premise.seed": "x"}},
                {"op": "status"}, {"op": "review"}, {"op": "export"}):
            self.lore(**arguments)
        self.assertEqual(self.data_snapshot(), before)


class TestEveryOpRefusesWhatItDoesNotRead(ToolCase):
    def test_a_real_argument_the_op_ignores_is_refused_by_name(self):
        valid = {"refresh": True, "tier": "light", "mode": "qa",
                 "approach": "x", "count": 1, "group": "world",
                 "answers": {}, "entries": [], "delete_entries": [],
                 "scopes": [], "formats": ["html"], "worksheet": True}
        reads = lore_tools.LORE_OP_ARGS
        self.assertEqual(set(valid) | {"op"},
                         set(REGISTRY["lore_bible"].schema["properties"]))
        for op, mine in reads.items():
            for name, value in valid.items():
                if name in mine:
                    continue
                with self.subTest(op=op, argument=name):
                    message = self.refused(name, op, op=op, **{name: value})
                    self.assertNotIn("bug in this server", message)


class TestTheShapeOfTheTool(unittest.TestCase):
    def test_every_ceiling_is_in_the_description(self):
        description = REGISTRY["lore_bible"].description
        flat = re.sub(r"(?<=\d),(?=\d\d\d)", "", description)
        for name in ("MAX_BIBLE_BYTES", "MAX_ANSWER_CHARS", "MAX_BIBLE_ENTRIES",
                     "MAX_ENTRIES_PER_SAVE", "MAX_ENTRY_NAME_CHARS",
                     "MAX_ENTRY_TEXT_CHARS", "MAX_DELETE_NAMES",
                     "MAX_APPROACH_CHARS", "MAX_QUESTIONS_PER_CALL",
                     "MAX_REVIEW_FINDINGS", "MAX_BASELINE_BYTES",
                     "MAX_BASELINE_NOUNS", "MAX_BASELINE_VARIANTS",
                     "MAX_BASELINE_SPEAKERS", "MAX_BASELINE_GAPS",
                     "MAX_BASELINE_SAMPLE_CHARS"):
            value = str(getattr(lore_store, name))
            with self.subTest(ceiling=name):
                self.assertRegex(flat, r"(?<![0-9.])%s(?![0-9.])" % value)

    def test_the_description_says_what_the_tool_does_not_do(self):
        description = REGISTRY["lore_bible"].description
        self.assertIn("never edits", description)
        self.assertIn("does not interview", description)

    def test_the_schema_bounds_every_argument(self):
        properties = REGISTRY["lore_bible"].schema["properties"]
        self.assertEqual(properties["count"]["maximum"],
                         lore_store.MAX_QUESTIONS_PER_CALL)
        self.assertEqual(properties["entries"]["maxItems"],
                         lore_store.MAX_ENTRIES_PER_SAVE)
        self.assertEqual(properties["delete_entries"]["maxItems"],
                         lore_store.MAX_DELETE_NAMES)
        self.assertEqual(properties["approach"]["maxLength"],
                         lore_store.MAX_APPROACH_CHARS)
        item = properties["entries"]["items"]
        self.assertEqual(set(item["properties"]), {"kind", "name", "text"})
        self.assertEqual(properties["op"]["enum"], list(lore_tools.LORE_OP_ARGS))

    def test_each_op_is_declared_with_the_arguments_it_reads(self):
        reads = lore_tools.LORE_OP_ARGS
        self.assertEqual(set(reads), {"scan", "tiers", "choose", "next", "save",
                                      "status", "review", "export"})
        self.assertEqual(reads["tiers"], {"op"})
        self.assertEqual(reads["status"], {"op"})

    def test_importing_the_tool_does_not_load_the_scan(self):
        code = ("import sys; import rpgvxace_mcp.tools.lore_bible_tools; "
                "print('rpgvxace_mcp.analysis.lore_baseline' in sys.modules)")
        import subprocess
        done = subprocess.run([sys.executable, "-c", code],
                              env=dict(os.environ,
                                       PYTHONPATH=str(HERE.parent / "src")),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.stdout.strip(), "False", done.stderr)


@unittest.skipUnless(os.environ.get("RPGVXACE_TEST_PROJECT"),
                     "set RPGVXACE_TEST_PROJECT to a VX Ace project")
class TestAgainstARealProject(unittest.TestCase):
    """Structure only: the keys, the bounds, no crash. Never any content."""

    @classmethod
    def setUpClass(cls):
        cls.project = Project(os.environ["RPGVXACE_TEST_PROJECT"],
                              read_only=True)
        cls.ctx = Context(cls.project)

    def test_scan_is_bounded_and_has_its_keys(self):
        reply = REGISTRY["lore_bible"].run(self.ctx, {"op": "scan"})
        baseline = reply["baseline"]
        for key in ("volume", "database", "maps", "events", "proper_nouns",
                    "speakers", "names"):
            self.assertIn(key, baseline)
        self.assertIs(reply["saved"], False)
        self.assertLess(len(json.dumps(reply)), 200_000)
        self.assertLessEqual(len(baseline["proper_nouns"]),
                             lore_store.MAX_BASELINE_NOUNS)
        self.assertLessEqual(len(baseline["speakers"]),
                             lore_store.MAX_BASELINE_SPEAKERS)
        self.assertLessEqual(len(reply["gaps"]), lore_store.MAX_BASELINE_GAPS)

    def test_review_of_an_empty_bible_does_not_crash(self):
        reply = REGISTRY["lore_bible"].run(self.ctx, {"op": "review"})
        self.assertIn("findings", reply)
        self.assertLessEqual(len(reply["findings"]),
                             lore_store.MAX_REVIEW_FINDINGS)


class TestWhichWordsAreNames(LoreProject):
    """A name is a capitalised word in the middle of a sentence, mostly."""

    def nouns(self, *boxes):
        self.put_map(3, "Words", [self.event(1, "Talker", [
            command for box in boxes for command in self.say("", box)])])
        return {n["name"]: n for n in self.baseline()["proper_nouns"]}

    def test_a_word_mostly_written_in_lower_case_is_not_a_name(self):
        found = self.nouns("Take the cave road.", "The cave is deep.",
                           "Into the cave.", "Go to the Cave now.")
        self.assertNotIn("Cave", found)

    def test_the_pronoun_I_is_not_a_name(self):
        found = self.nouns("Then I left.", "Then I ran.", "Then I hid.")
        self.assertNotIn("I", found)

    def test_a_word_that_only_ever_starts_a_sentence_is_not_a_name(self):
        found = self.nouns("Welcome home.", "Welcome again.", "Welcome back.")
        self.assertNotIn("Welcome", found)

    def test_a_word_in_capitals_is_not_a_name(self):
        found = self.nouns("You need HP and HP.", "Restore HP now and HP later.")
        self.assertNotIn("HP", found)

    def test_a_possessive_is_counted_as_the_name(self):
        found = self.nouns("Take Brindle's lock.", "Ask Brindle.")
        self.assertEqual(found["Brindle"]["count"], 2)

    def test_two_mentions_are_not_yet_a_recurring_name(self):
        self.nouns("Take Brindle's lock.", "Ask Brindle.")
        kinds = [g["kind"] for g in self.baseline()["gaps"]]
        self.assertNotIn("unexplained_name", kinds)

    def test_text_codes_are_not_words(self):
        found = self.nouns("Ask \\C[2]Brindle\\C[0] about \\N[1].",
                           "Then \\V[3] and \\G left.")
        self.assertIn("Brindle", found)
        self.assertNotIn("C", found)
        self.assertNotIn("N", found)


class TestTheTablesThatGrowAreCapped(LoreProject):
    def test_distinct_speakers_are_capped_and_say_so(self):
        self.put_map(3, "Many", [
            self.event(number, "E%d" % number,
                       self.say("Face%d" % number, "Hello there."))
            for number in range(1, 6)])
        with mock.patch.object(lore_baseline, "_SPEAKERS_UPPER", 3):
            baseline = self.baseline()
        self.assertIs(baseline["speakers_truncated"], True)
        self.assertEqual(len(baseline["speakers"]), 3)

    def test_distinct_words_are_capped_and_say_so(self):
        self.story()
        with mock.patch.object(lore_baseline, "_DISTINCT_WORDS_UPPER", 5):
            corpus = lore_baseline.lore_scan(self.project)
            baseline = lore_baseline.baseline_of(corpus)
        self.assertLessEqual(len(corpus.nouns), 5)
        self.assertIs(baseline["vocabulary_truncated"], True)

    def test_the_lists_in_the_answer_are_capped_and_say_so(self):
        boxes = ["Ask %s about %s." % (name, name2) for name, name2 in
                 zip(["Aldwyn", "Berrick", "Corvane", "Dunmar", "Eskel"],
                     ["Falken", "Garrow", "Hestir", "Ilbrane", "Jorund"])]
        self.put_map(3, "Names", [self.event(1, "Talker", [
            c for box in boxes for c in self.say("", box)])])
        with mock.patch.object(lore_baseline, "MAX_BASELINE_NOUNS", 4):
            baseline = self.baseline()
        self.assertEqual(len(baseline["proper_nouns"]), 4)
        self.assertIs(baseline["proper_nouns_truncated"], True)
        with mock.patch.object(lore_baseline, "MAX_BASELINE_GAPS", 1):
            baseline = self.baseline()
        self.assertEqual(len(baseline["gaps"]), 1)


class TestTheLoreFolderIsJudgedBeforeItIsMade(StoreTest):
    def test_the_work_folder_and_the_lore_folder_are_each_contained(self):
        seen = []
        real = lore_store._contained

        def spy(path, inside, what):
            seen.append((Path(path).name, Path(inside).name,
                         self.lore.exists()))
            return real(path, inside, what)

        with mock.patch.object(lore_store, "_contained", spy):
            lore_store.save(self.project, self.good())
        # Judged against where it belongs, and before the folder exists.
        self.assertIn((".rpgvxace-mcp", "project", False), seen)
        self.assertIn(("lore", ".rpgvxace-mcp", False), seen)


if __name__ == "__main__":
    unittest.main()
