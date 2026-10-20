"""`text`: every player-visible string out to a file and back, and nothing else.

The properties that matter, in the order a caller meets them: an export lists
what the player reads and nothing the developer does; an import with no edits
leaves every data file byte for byte as it was; an edit lands where it was
aimed, re-wrapped as the editor would write it, and leaves its neighbours
alone; and a file or a project that lies -- stale hashes, keys that point at
commands, shared lists, control characters, links out of the project -- costs
the lie and nothing else.

Expectations are the fixture's own strings and command layouts written out as
literals (`tests/fixtures/build_project.py`), the documented hash format
recomputed here with `struct`, and the editor's rules (four lines to a message
box, a copy of each choice in its When branch) taken from `commands.py`'s
compiler -- not read back out of the code under test.
"""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp import marshal_rb  # noqa: E402
from rpgvxace_mcp.project import (  # noqa: E402
    Project,
    ProjectError,
    ReadOnlyError,
    StaleFileError,
)
from rpgvxace_mcp.rubytypes import (  # noqa: E402
    RubyHash,
    RubyObject,
    RubyString,
)
from rpgvxace_mcp.schema import new_event_command  # noqa: E402
from rpgvxace_mcp.tools import text_tools  # noqa: E402
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    REGISTRY,
    ToolError,
)

from tool_case import EmptyPageMap, SharedObjectMap, ToolTest  # noqa: E402


FIRST_MESSAGE = ("Welcome to Riverbend, traveller.\n"
                 "The bridge east has been out for weeks.")
GATE_MESSAGE = "You opened the gate! Thank you."
ELDER_KEY = "map:1/event:1/page:0/cmd:0"
GATE_KEY = "map:1/event:1/page:0/cmd:4"


def stored_hash(kind, *lines):
    """The documented original_hash: blake2b-96 of the kind, then each line
    stored-bytes-first with an eight-byte big-endian length in front."""
    digest = hashlib.blake2b(digest_size=12)
    digest.update(kind.encode("ascii"))
    for line in lines:
        data = line if isinstance(line, bytes) else line.encode("utf-8")
        digest.update(struct.pack(">Q", len(data)) + data)
    return digest.hexdigest()


class TextTest(ToolTest):
    """Helpers every class below shares."""

    def S(self, text):
        return self.project.codec.make_string(text)

    def command(self, code, indent, *params):
        return new_event_command(code, indent, list(params))

    @property
    def exports(self):
        return self.project.work_dir() / "exports"

    def export(self, **arguments):
        return self.call("text", op="export", **arguments)

    def document(self, reply):
        return json.loads(Path(reply["path"]).read_text("utf-8"))

    def entries(self, **arguments):
        """The exported entries, by key."""
        arguments.setdefault("preview", 0)
        document = self.document(self.export(**arguments))
        return {entry["key"]: entry for entry in document["entries"]}

    def texts(self, **arguments):
        return {key: entry["text"]
                for key, entry in self.entries(**arguments).items()}

    def write_file(self, document, name="edited.json"):
        self.exports.mkdir(parents=True, exist_ok=True)
        path = self.exports / name
        if isinstance(document, bytes):
            path.write_bytes(document)
        else:
            path.write_text(json.dumps(document), "utf-8")
        return str(path)

    def edit_and_import(self, changes, export_args=None, **import_args):
        """Export, change the named entries' text, import the result."""
        document = self.document(self.export(preview=0, **(export_args or {})))
        by_key = {entry["key"]: entry for entry in document["entries"]}
        for key, text in changes.items():
            by_key[key]["text"] = text
        return self.call("text", op="import", path=self.write_file(document),
                         **import_args)

    def data_files(self):
        data = self.project.root / "Data"
        return {p.name: p.read_bytes() for p in sorted(data.iterdir())
                if p.is_file()}

    def write_map(self, map_id, game_map):
        path = self.project.path_for(self.project.map_file(map_id))
        path.write_bytes(marshal_rb.dump(game_map))
        self.ctx = self.reopen()
        self.project = self.ctx.project

    def set_list(self, map_id, event_id, page, commands):
        game_map = self.project.load_map(map_id)
        event = game_map.get("events").get(event_id)
        event.get("pages")[page].set("list", commands)
        self.write_map(map_id, game_map)

    def codes(self, map_id=1, event_id=1, page=0):
        """The command codes of one page, read from disk."""
        game_map = self.reopen().project.load_map(map_id)
        pages = game_map.get("events").get(event_id).get("pages")
        return [c.get("code") for c in pages[page].get("list")]

    def page_commands(self, map_id=1, event_id=1, page=0):
        game_map = self.reopen().project.load_map(map_id)
        pages = game_map.get("events").get(event_id).get("pages")
        return pages[page].get("list")

    def set_field(self, table, entry_id, field, value):
        entry = self.project.get_entry(table, entry_id)
        entry.set(field, value if isinstance(value, RubyString)
                  else self.S(value))
        filename = self.project.filename_for(table)
        self.project.save_file(filename, tool="test", summary="field")
        self.ctx = self.reopen()
        self.project = self.ctx.project

    def dialogue_with(self, *commands):
        """Replace the Elder's first page with the given commands + the end."""
        self.set_list(1, 1, 0, [*commands, self.command(0, 0)])


class TestExportListsWhatThePlayerReads(TextTest):

    def test_a_show_text_is_one_entry_with_its_lines_joined(self):
        entry = self.entries()[ELDER_KEY]
        self.assertEqual(entry["text"], FIRST_MESSAGE)
        self.assertEqual(entry["kind"], "show_text")
        self.assertNotIn("read_only", entry)

    def test_the_face_and_window_settings_are_context_not_text(self):
        entry = self.entries()[ELDER_KEY]
        self.assertEqual(entry["context"], {
            "face_name": "Actor1", "face_index": 0, "background": 0,
            "position": 2})
        self.assertEqual(entry["max_lines"], 4)

    def test_a_branch_nested_message_and_a_later_page_have_their_own_keys(self):
        texts = self.texts()
        self.assertEqual(texts[GATE_KEY], GATE_MESSAGE)
        self.assertEqual(texts["map:1/event:1/page:1/cmd:0"], "Safe travels.")
        self.assertEqual(texts["map:2/event:1/page:0/cmd:1"],
                         "Found 2 Potions!")

    def test_the_hash_is_the_documented_one(self):
        entry = self.entries()[ELDER_KEY]
        self.assertEqual(entry["original_hash"], stored_hash(
            "show_text", "Welcome to Riverbend, traveller.",
            "The bridge east has been out for weeks."))

    def test_a_field_hash_is_keyed_by_kind_and_stored_bytes(self):
        entries = self.entries()
        self.assertEqual(entries["items:1/name"]["original_hash"],
                         stored_hash("name", "Potion"))
        self.assertEqual(entries["items:1/description"]["original_hash"],
                         stored_hash("description", "Restores 100 HP."))

    def test_lines_that_run_together_do_not_hash_alike(self):
        """'ab' + 'c' and 'a' + 'bc' are different messages."""
        self.dialogue_with(self.command(101, 0, self.S(""), 0, 0, 2),
                           self.command(401, 0, self.S("ab")),
                           self.command(401, 0, self.S("c")))
        first = self.entries()[ELDER_KEY]["original_hash"]
        self.dialogue_with(self.command(101, 0, self.S(""), 0, 0, 2),
                           self.command(401, 0, self.S("a")),
                           self.command(401, 0, self.S("bc")))
        self.assertNotEqual(self.entries()[ELDER_KEY]["original_hash"], first)

    def test_database_fields_the_player_reads(self):
        texts = self.texts()
        self.assertEqual(texts["actors:1/name"], "Ralph")
        self.assertEqual(texts["items:2/name"], "Hi-Potion")
        self.assertEqual(texts["items:1/description"], "Restores 100 HP.")
        self.assertEqual(texts["states:2/name"], "Poison")
        self.assertEqual(texts["skills:1/message1"], " attacks!")
        self.assertEqual(self.entries()["skills:1/message1"]["kind"],
                         "battle_message")

    def test_the_developers_fields_are_not_text(self):
        self.set_field("items", 1, "note", "secret developer note")
        keys = self.texts()
        self.assertFalse([k for k in keys if k.endswith("/note")], keys)
        self.assertNotIn("secret developer note", keys.values())

    def test_system_title_currency_types_and_terms(self):
        entries = self.entries()
        self.assertEqual(entries["system/game_title"]["text"], "Demo Quest")
        self.assertEqual(entries["system/currency_unit"]["text"], "G")
        self.assertEqual(entries["system/skill_types/2"]["text"], "Magic")
        self.assertEqual(entries["system/terms/basic/0"]["text"], "Level")
        self.assertEqual(entries["system/terms/commands/10"]["text"],
                         "Game End")
        self.assertEqual(entries["system/terms/etypes/0"]["kind"], "term")

    def test_a_map_display_name_is_text_and_the_tree_name_is_not(self):
        entries = self.entries()
        self.assertEqual(entries["map:1/display_name"]["text"], "Riverbend")
        self.assertEqual(entries["map:1/display_name"]["kind"],
                         "map_display_name")
        self.assertFalse([k for k in entries if k.startswith("map_infos")])
        # "Riverbend Cave" is only the tree's name for map 2.
        self.assertNotIn("Riverbend Cave", [e["text"] for e in entries.values()])

    def test_names_only_the_editor_shows_are_not_text(self):
        shown = [e["text"] for e in self.entries().values()]
        for developer_only in ("Elder", "Cave Entrance", "Chest",
                               "Door Opened", "Gold Bonus", "Heal Party",
                               "Hit Physical"):
            self.assertNotIn(developer_only, shown)

    def test_an_empty_string_is_not_an_entry(self):
        entries = self.entries()
        self.assertEqual([k for k, e in entries.items() if e["text"] == ""],
                         [])
        self.assertNotIn("actors:1/nickname", entries)

    def test_comments_only_when_asked(self):
        self.set_list(1, 2, 0, [
            self.command(108, 0, self.S("TODO fix")),
            self.command(408, 0, self.S("second line")),
            self.command(0, 0)])
        self.assertNotIn("map:1/event:2/page:0/cmd:0", self.entries())
        asked = self.entries(include_comments=True)
        self.assertEqual(asked["map:1/event:2/page:0/cmd:0"]["text"],
                         "TODO fix\nsecond line")
        self.assertEqual(asked["map:1/event:2/page:0/cmd:0"]["kind"],
                         "comment")

    def test_each_choice_is_its_own_entry(self):
        self.dialogue_with(
            self.command(102, 0, [self.S("Yes"), self.S("No")], 2),
            self.command(402, 0, 0, self.S("Yes")),
            self.command(402, 0, 1, self.S("No")),
            self.command(404, 0))
        entries = self.entries()
        self.assertEqual(entries["map:1/event:1/page:0/cmd:0/choice:0"]["text"],
                         "Yes")
        self.assertEqual(entries["map:1/event:1/page:0/cmd:0/choice:1"]["text"],
                         "No")
        self.assertEqual(
            entries["map:1/event:1/page:0/cmd:0/choice:1"]["kind"], "choice")
        # The editor's copies in the When branches are not separate entries.
        self.assertNotIn("map:1/event:1/page:0/cmd:1", entries)

    def test_a_choice_list_longer_than_the_editor_allows_is_cut(self):
        self.dialogue_with(self.command(
            102, 0, [self.S("c%d" % n) for n in range(10)], 0))
        choices = sorted(k for k in self.entries() if "/choice:" in k)
        self.assertEqual(choices, ["map:1/event:1/page:0/cmd:0/choice:%d" % n
                                   for n in range(4)])

    def test_scrolling_text_is_one_entry(self):
        self.dialogue_with(
            self.command(105, 0, 2, False),
            self.command(405, 0, self.S("Long ago")),
            self.command(405, 0, self.S("there was a river.")))
        entry = self.entries()[ELDER_KEY]
        self.assertEqual(entry["text"], "Long ago\nthere was a river.")
        self.assertEqual(entry["kind"], "scroll_text")
        self.assertEqual(entry["context"], {"speed": 2, "no_fast": False})

    def test_common_event_and_troop_battle_page_text(self):
        common = self.project.get_entry("common_events", 1)
        common.set("list", [
            self.command(101, 0, self.S(""), 0, 0, 2),
            self.command(401, 0, self.S("The party is healed.")),
            self.command(0, 0)])
        self.project.save_file(self.project.filename_for("common_events"),
                               tool="test", summary="common")
        troop = self.project.get_entry("troops", 1)
        page = RubyObject("RPG::Troop::Page", {
            "@list": [self.command(101, 0, self.S(""), 0, 0, 2),
                      self.command(401, 0, self.S("The slime wobbles.")),
                      self.command(0, 0)]})
        troop.set("pages", [page])
        self.project.save_file(self.project.filename_for("troops"),
                               tool="test", summary="troop")
        self.ctx = self.reopen()
        self.project = self.ctx.project
        texts = self.texts()
        self.assertEqual(texts["common_events:1/cmd:0"], "The party is healed.")
        self.assertEqual(texts["troops:1/page:0/cmd:0"], "The slime wobbles.")

    def test_a_line_that_is_not_text_makes_a_read_only_entry(self):
        self.dialogue_with(self.command(101, 0, self.S(""), 0, 0, 2),
                           self.command(401, 0, self.S("fine")),
                           self.command(401, 0, 7))
        entry = self.entries()[ELDER_KEY]
        self.assertTrue(entry["read_only"])
        self.assertIn("not a string", entry["reason"])

    def test_a_line_with_a_line_break_in_it_is_read_only(self):
        self.dialogue_with(self.command(101, 0, self.S(""), 0, 0, 2),
                           self.command(401, 0, self.S("one\ntwo")))
        entry = self.entries()[ELDER_KEY]
        self.assertTrue(entry["read_only"])
        self.assertIn("line break", entry["reason"])


class TestAnExportIsASummaryAndAFile(TextTest):

    def test_counts_add_up_to_the_entries_and_name_each_kind(self):
        reply = self.export()
        self.assertEqual(sum(reply["counts"].values()), reply["entries"])
        self.assertEqual(reply["counts"]["show_text"], 4)
        self.assertEqual(reply["counts"]["map_display_name"], 1)
        self.assertEqual(reply["counts"]["name"], 16)
        self.assertEqual(reply["read_only_entries"], 0)

    def test_the_preview_is_the_first_entries(self):
        reply = self.export(preview=3)
        self.assertEqual(len(reply["preview"]), 3)
        document = self.document(reply)
        self.assertEqual([r["key"] for r in reply["preview"]],
                         [e["key"] for e in document["entries"][:3]])

    def test_the_default_preview_is_ten_and_zero_is_none(self):
        self.assertEqual(len(self.export()["preview"]), 10)
        self.assertEqual(self.export(preview=0)["preview"], [])

    def test_a_preview_past_its_ceiling_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.export(preview=text_tools.MAX_PREVIEW_ENTRIES + 1)
        self.assertIn("preview must be at most", str(caught.exception))

    def test_scopes_narrow_the_export(self):
        keys = self.entries(scopes=["system"])
        self.assertTrue(keys)
        self.assertEqual([k for k in keys if not k.startswith("system/")], [])
        keys = self.entries(scopes=["database"])
        self.assertEqual(
            [k for k in keys if k.startswith(("map:", "system/"))], [])

    def test_map_ids_narrow_the_maps(self):
        keys = self.entries(scopes=["map_events", "map_names"], map_ids=[2])
        self.assertEqual(sorted(keys), ["map:2/event:1/page:0/cmd:1"])

    def test_a_map_that_is_not_there_is_named(self):
        reply = self.export(scopes=["map_events"], map_ids=[2, 77])
        self.assertEqual(reply["files_not_read"], {"map 77": "no such map file"})

    def test_an_unknown_scope_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.export(scopes=["everything"])
        self.assertIn("unknown scopes", str(caught.exception))

    def test_the_export_changes_nothing_in_the_project(self):
        before = {str(p.relative_to(self.project.root)): p.read_bytes()
                  for p in self.project.root.rglob("*") if p.is_file()}
        reply = self.export()
        after = {str(p.relative_to(self.project.root)): p.read_bytes()
                 for p in self.project.root.rglob("*") if p.is_file()}
        new = sorted(set(after) - set(before))
        self.assertEqual(len(new), 1, new)
        self.assertTrue(new[0].startswith(".rpgvxace-mcp\\exports\\text-")
                        or new[0].startswith(".rpgvxace-mcp/exports/text-"))
        self.assertEqual(Path(reply["path"]).read_bytes(), after[new[0]])
        for name, data in before.items():
            self.assertEqual(after[name], data, name)

    def test_the_file_is_named_by_its_content_and_stable(self):
        first = self.export()["path"]
        second = self.export()["path"]
        self.assertEqual(first, second)
        self.assertRegex(Path(first).name, r"^text-[0-9a-f]{10}\.json$")

    def test_the_reply_says_it_is_a_cache_and_not_an_edit(self):
        self.assertIn("not a project edit", self.export()["export_note"])


class TestAnExportFolderKeepsItsNewestAndOnlyItsOwn(TextTest):

    def test_the_oldest_of_its_own_files_go_and_a_lookalike_stays(self):
        self.exports.mkdir(parents=True, exist_ok=True)
        base = time.time() - 10_000
        for number in range(text_tools.MAX_KEPT_EXPORTS + 10):
            path = self.exports / ("text-%010x.json" % number)
            path.write_text("{}")
            os.utime(path, (base + number, base + number))
        lookalike = self.exports / "text-notes-1.json"
        lookalike.write_text("mine")
        not_hex = self.exports / "text-00000000zz.json"
        not_hex.write_text("also mine")
        os.utime(lookalike, (base - 1000, base - 1000))
        os.utime(not_hex, (base - 2000, base - 2000))
        self.export()
        own = [p for p in self.exports.glob("text-*.json")
               if text_tools._OWN_EXPORT.fullmatch(p.name)]
        self.assertEqual(len(own), text_tools.MAX_KEPT_EXPORTS)
        # The newest survive; the oldest do not.
        self.assertFalse((self.exports / "text-0000000000.json").exists())
        self.assertTrue((self.exports / ("text-%010x.json" % (
            text_tools.MAX_KEPT_EXPORTS + 9))).exists())
        self.assertTrue(lookalike.exists())
        self.assertTrue(not_hex.exists())
        self.assertEqual(list(self.exports.glob("*.tmp")), [])


class TestAnExportIsBounded(TextTest):

    def patched(self, name, value):
        original = getattr(text_tools, name)
        self.addCleanup(setattr, text_tools, name, original)
        setattr(text_tools, name, value)

    def test_the_entry_ceiling_cuts_and_says_so(self):
        self.patched("MAX_EXPORT_ENTRIES", 5)
        reply = self.export()
        self.assertEqual(reply["entries"], 5)
        self.assertTrue(reply["entries_truncated"])
        self.assertIn("scopes or map_ids", reply["entries_note"])
        self.assertEqual(len(self.document(reply)["entries"]), 5)

    def test_the_file_ceiling_cuts_and_says_so_and_the_file_fits(self):
        self.patched("MAX_EXPORT_FILE_BYTES", 2500)
        reply = self.export()
        self.assertTrue(reply["entries_truncated"])
        self.assertLessEqual(reply["bytes"], 2500)
        self.assertGreater(reply["entries"], 0)
        self.assertLessEqual(Path(reply["path"]).stat().st_size, 2500)

    def test_an_entry_over_its_byte_ceiling_is_listed_without_its_text(self):
        self.patched("MAX_TEXT_ENTRY_BYTES", 20)
        entry = self.entries()[ELDER_KEY]
        self.assertEqual(entry["text"], "")
        self.assertTrue(entry["read_only"])
        self.assertIn("above the 20", entry["reason"])
        # And a short one next to it is untouched.
        self.assertEqual(self.entries()["items:1/name"]["text"], "Potion")

    def test_a_database_field_over_its_byte_ceiling_is_read_only_too(self):
        self.patched("MAX_TEXT_ENTRY_BYTES", 5)
        entry = self.entries()["items:1/name"]
        self.assertEqual(entry["text"], "")
        self.assertTrue(entry["read_only"])
        self.assertIn("above the 5", entry["reason"])
        self.assertEqual(self.entries()["system/currency_unit"]["text"], "G")

    def test_a_string_of_a_few_megabytes_is_not_decoded_into_the_file(self):
        self.dialogue_with(self.command(101, 0, self.S(""), 0, 0, 2),
                           self.command(401, 0, RubyString(
                               b"x" * 3_000_000, {"E": True})))
        reply = self.export()
        entry = self.entries()[ELDER_KEY]
        self.assertEqual(entry["text"], "")
        self.assertTrue(entry["read_only"])
        self.assertLess(reply["bytes"], 50_000)

    def test_the_byte_budget_stops_the_walk_and_says_so(self):
        self.patched("_TEXT_BYTE_BUDGET", 100)
        reply = self.export()
        self.assertIn("100 bytes", reply["walk_stopped"])
        self.assertLess(reply["entries"], 80)

    def test_one_enormous_command_list_is_charged_and_cut(self):
        filler = self.command(111, 0, 0, 1, 0)
        self.set_list(1, 1, 0, [filler] * 250_000 + [self.command(0, 0)])
        started = time.perf_counter()
        reply = self.export(scopes=["map_events"])
        self.assertLess(time.perf_counter() - started, 10)
        self.assertIn("incomplete", reply["walk_stopped"])

    def test_a_walk_that_cannot_finish_says_so(self):
        """Four million (event, page) pairs from a few kilobytes."""

        class Crafted(EmptyPageMap, TextTest):
            def runTest(self):
                pass
        crafted = Crafted()
        crafted.engine = self.engine
        crafted.tmp, crafted.project, crafted.ctx = (
            self.tmp, self.project, self.ctx)
        crafted.assertLess = self.assertLess
        crafted.reopen = self.reopen
        crafted.call = self.call
        crafted.craft_empty_page_map()
        self.ctx = crafted.ctx
        self.project = self.ctx.project
        started = time.perf_counter()
        reply = self.export(scopes=["map_events"])
        self.assertLess(time.perf_counter() - started, 10)
        self.assertIn("walk_stopped", reply)
        self.assertIn("incomplete", reply["walk_stopped"])


class TestAnImportThatChangesNothingTouchesNothing(TextTest):

    def check_untouched(self, reply):
        before = self.snapshot
        self.assertEqual(self.data_files(), before)
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual(reply["applied"], [])
        self.assertEqual(reply["skipped"], [])
        self.assertEqual(reply["writes"], [])
        self.assertEqual(self.work_listing(), self.listing_before)
        self.assertEqual(self.project.recent_changes(), self.changes_before)

    def work_listing(self):
        work = self.project.work_dir()
        return sorted(str(p) for p in work.rglob("*")
                      if "exports" not in p.parts and p.name != "changes.jsonl")

    def take_snapshot(self):
        self.snapshot = self.data_files()
        self.listing_before = self.work_listing()
        self.changes_before = self.project.recent_changes()

    def setUp(self):
        super().setUp()
        self.take_snapshot()

    def test_an_unedited_export_round_trips_byte_for_byte(self):
        exported = self.export(preview=0)
        reply = self.call("text", op="import", path=exported["path"])
        self.check_untouched(reply)
        self.assertEqual(reply["unchanged"], exported["entries"])

    def test_it_holds_with_comments_choices_and_scrolling_text_present(self):
        self.set_list(1, 1, 0, [
            self.command(108, 0, self.S("note")),
            self.command(408, 0, self.S("more")),
            self.command(102, 0, [self.S("A"), self.S("B")], 2),
            self.command(402, 0, 0, self.S("A")),
            self.command(402, 0, 1, self.S("B")),
            self.command(404, 0),
            self.command(105, 0, 2, False),
            self.command(405, 0, self.S("scroll")),
            self.command(0, 0)])
        self.take_snapshot()
        exported = self.export(include_comments=True, preview=0)
        reply = self.call("text", op="import", path=exported["path"])
        self.check_untouched(reply)
        self.assertEqual(reply["unchanged"], exported["entries"])

    def test_read_only_entries_do_not_break_the_round_trip(self):
        self.set_field("weapons", 1, "name", RubyString(
            b"bad \xff bytes", {"E": True}))
        self.take_snapshot()
        exported = self.export(preview=0)
        self.assertEqual(exported["read_only_entries"], 1)
        reply = self.call("text", op="import", path=exported["path"])
        self.check_untouched(reply)

    def test_a_byte_order_mark_on_the_file_is_accepted(self):
        exported = self.export(preview=0)
        raw = Path(exported["path"]).read_bytes()
        path = self.write_file(b"\xef\xbb\xbf" + raw, "bom.json")
        reply = self.call("text", op="import", path=path)
        self.check_untouched(reply)


class TestAnEditLandsWhereItWasAimed(TextTest):

    def test_a_name_changes_and_only_its_file_does(self):
        before = self.data_files()
        reply = self.edit_and_import({"items:1/name": "Elixir"})
        self.assertEqual(reply["edit_count"], 1)
        self.assertEqual(reply["applied"], [{
            "key": "items:1/name", "kind": "name", "before": "Potion",
            "after": "Elixir"}])
        after = self.data_files()
        changed = sorted(n for n in after if after[n] != before.get(n))
        self.assertEqual(changed, ["Items" + self.extension])
        fresh = self.reopen().project
        self.assertEqual(fresh.codec.decode(
            fresh.get_entry("items", 1).get("name").data), "Elixir")
        self.assertEqual(fresh.codec.decode(
            fresh.get_entry("items", 2).get("name").data), "Hi-Potion")

    def test_the_new_string_has_the_projects_string_form(self):
        self.edit_and_import({"items:1/name": "Elixir"})
        name = self.reopen().project.get_entry("items", 1).get("name")
        self.assertEqual(name.ivars, self.project.codec.string_ivars)

    def test_a_system_term_and_a_list_element_change(self):
        self.edit_and_import({"system/terms/basic/0": "Niveau",
                              "system/skill_types/2": "Sorcery",
                              "system/game_title": "Demo Saga"})
        entries = self.entries()
        self.assertEqual(entries["system/terms/basic/0"]["text"], "Niveau")
        self.assertEqual(entries["system/skill_types/2"]["text"], "Sorcery")
        self.assertEqual(entries["system/game_title"]["text"], "Demo Saga")
        self.assertEqual(entries["system/terms/basic/1"]["text"], "Lv")

    def test_a_map_display_name_changes(self):
        self.edit_and_import({"map:1/display_name": "Rivière"})
        self.assertEqual(self.entries()["map:1/display_name"]["text"],
                         "Rivière")

    def test_a_multiline_description_keeps_its_line_break(self):
        self.edit_and_import({"items:1/description": "Line one.\nLine two."})
        item = self.reopen().project.get_entry("items", 1)
        self.assertEqual(item.get("description").data, b"Line one.\nLine two.")

    def test_a_write_is_backed_up_and_logged_under_this_tool(self):
        self.edit_and_import({"items:1/name": "Elixir"})
        log = self.project.recent_changes()
        self.assertEqual([(r["tool"], r["file"]) for r in log],
                         [("text", "Items" + self.extension)])
        self.assertIn("1 entries", log[0]["summary"])
        self.assertTrue(list((self.project.work_dir() / "backups").rglob(
            "Items" + self.extension)))


class TestShowTextIsRewrappedAsTheEditorWritesIt(TextTest):

    def test_a_longer_message_gains_continuation_lines_in_place(self):
        self.edit_and_import({ELDER_KEY: "One\nTwo\nThree"})
        self.assertEqual(self.codes(), [101, 401, 401, 401, 111, 101, 401,
                                        121, 412, 0])
        lines = [c.get("parameters")[0].data
                 for c in self.page_commands() if c.get("code") == 401]
        self.assertEqual(lines[:3], [b"One", b"Two", b"Three"])
        self.assertEqual(lines[3], GATE_MESSAGE.encode())

    def test_a_shorter_message_drops_the_extra_lines(self):
        self.edit_and_import({ELDER_KEY: "Hello."})
        self.assertEqual(self.codes(), [101, 401, 111, 101, 401, 121, 412, 0])

    def test_the_face_and_window_settings_are_left_alone(self):
        before = self.page_commands()[0]
        self.edit_and_import({ELDER_KEY: "One\nTwo\nThree"})
        head = self.page_commands()[0]
        self.assertEqual(marshal_rb.dump(head), marshal_rb.dump(before))

    def test_new_lines_take_the_indent_of_their_message(self):
        self.edit_and_import({GATE_KEY: "Thanks!\nReally."})
        commands = self.page_commands()
        new = [c for c in commands if c.get("code") == 401][-2:]
        self.assertEqual([c.get("indent") for c in new], [1, 1])
        self.assertEqual([c.get("parameters")[0].data for c in new],
                         [b"Thanks!", b"Really."])

    def test_two_messages_in_one_list_both_land_when_the_first_grows(self):
        """Keys name positions as the export saw them: the later one must be
        written before the earlier one moves it."""
        self.edit_and_import({ELDER_KEY: "A\nB\nC", GATE_KEY: "Done\nAgain"})
        self.assertEqual(self.codes(), [101, 401, 401, 401, 111, 101, 401,
                                        401, 121, 412, 0])
        lines = [c.get("parameters")[0].data
                 for c in self.page_commands() if c.get("code") == 401]
        self.assertEqual(lines, [b"A", b"B", b"C", b"Done", b"Again"])

    def test_two_messages_in_one_list_both_land_when_the_first_shrinks(self):
        self.edit_and_import({ELDER_KEY: "Short", GATE_KEY: "Longer\nNow"})
        self.assertEqual(self.codes(), [101, 401, 111, 101, 401, 401, 121,
                                        412, 0])
        lines = [c.get("parameters")[0].data
                 for c in self.page_commands() if c.get("code") == 401]
        self.assertEqual(lines, [b"Short", b"Longer", b"Now"])

    def test_the_command_after_the_message_is_still_the_branch(self):
        self.edit_and_import({ELDER_KEY: "One\nTwo\nThree\nFour"})
        commands = self.page_commands()
        self.assertEqual(commands[5].get("code"), 111)
        self.assertEqual(list(commands[5].get("parameters")), [0, 1, 0])

    def test_more_lines_than_the_message_window_holds_are_refused(self):
        reply = self.edit_and_import({ELDER_KEY: "1\n2\n3\n4\n5"})
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual(len(reply["skipped"]), 1)
        self.assertEqual(reply["skipped"][0]["key"], ELDER_KEY)
        self.assertIn("is 5 lines, above the 4", reply["skipped"][0]["reason"])
        self.assertEqual(self.codes(), [101, 401, 401, 111, 101, 401, 121,
                                        412, 0])

    def test_four_lines_are_fine(self):
        self.assertEqual(self.edit_and_import(
            {ELDER_KEY: "1\n2\n3\n4"})["edit_count"], 1)

    def test_a_message_that_already_had_more_may_keep_them_but_not_gain(self):
        self.dialogue_with(
            self.command(101, 0, self.S(""), 0, 0, 2),
            *[self.command(401, 0, self.S("line %d" % n)) for n in range(6)])
        document = self.document(self.export(preview=0))
        entry = next(e for e in document["entries"] if e["key"] == ELDER_KEY)
        entry["text"] = "\n".join("edited %d" % n for n in range(6))
        reply = self.call("text", op="import", path=self.write_file(document))
        self.assertEqual(reply["edit_count"], 1)
        entry["text"] = "\n".join("again %d" % n for n in range(7))
        reply = self.call("text", op="import", path=self.write_file(document),
                          force=True)
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("above the 6", reply["skipped"][0]["reason"])

    def test_one_empty_line_is_a_message_with_a_blank_line(self):
        self.edit_and_import({ELDER_KEY: "Hello.\n"})
        lines = [c.get("parameters")[0].data
                 for c in self.page_commands() if c.get("code") == 401]
        self.assertEqual(lines[:2], [b"Hello.", b""])


class TestChoicesKeepBothCopiesOfTheirText(TextTest):

    def choice_page(self):
        self.dialogue_with(
            self.command(102, 0, [self.S("Yes"), self.S("No")], 2),
            self.command(402, 0, 0, self.S("Yes")),
            self.command(402, 0, 1, self.S("No")),
            self.command(404, 0))

    def test_the_list_and_the_when_branch_are_both_updated(self):
        self.choice_page()
        self.edit_and_import({"map:1/event:1/page:0/cmd:0/choice:1": "Nope"})
        commands = self.page_commands()
        self.assertEqual([c.get("code") for c in commands],
                         [102, 402, 402, 404, 0])
        self.assertEqual(commands[0].get("parameters")[0][0].data, b"Yes")
        self.assertEqual(commands[0].get("parameters")[0][1].data, b"Nope")
        self.assertEqual(commands[1].get("parameters")[1].data, b"Yes")
        self.assertEqual(commands[2].get("parameters")[1].data, b"Nope")
        self.assertEqual(commands[2].get("parameters")[0], 1)
        self.assertEqual(commands[0].get("parameters")[1], 2)

    def test_both_choices_can_change_in_one_import(self):
        self.choice_page()
        self.edit_and_import({"map:1/event:1/page:0/cmd:0/choice:0": "Oui",
                              "map:1/event:1/page:0/cmd:0/choice:1": "Non"})
        commands = self.page_commands()
        self.assertEqual(commands[0].get("parameters")[0][0].data, b"Oui")
        self.assertEqual(commands[0].get("parameters")[0][1].data, b"Non")
        self.assertEqual(commands[1].get("parameters")[1].data, b"Oui")
        self.assertEqual(commands[2].get("parameters")[1].data, b"Non")

    def test_a_when_branch_the_caller_never_saw_is_left_alone(self):
        """Only the copy that matches the old text is the editor's copy."""
        self.choice_page()
        commands = self.project.load_map(1).get("events").get(1).get(
            "pages")[0].get("list")
        commands[2].get("parameters")[1] = self.S("Something else")
        self.write_map(1, self.project.load_map(1))
        self.edit_and_import({"map:1/event:1/page:0/cmd:0/choice:1": "Nope"})
        self.assertEqual(self.page_commands()[2].get("parameters")[1].data,
                         b"Something else")

    def test_a_choice_is_one_line(self):
        self.choice_page()
        reply = self.edit_and_import(
            {"map:1/event:1/page:0/cmd:0/choice:0": "Yes\nplease"})
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("one line", reply["skipped"][0]["reason"])


class TestScrollingTextAndCommentsRewrapToo(TextTest):

    def test_scrolling_text_grows_past_a_message_window(self):
        self.dialogue_with(self.command(105, 0, 2, False),
                           self.command(405, 0, self.S("one")))
        lines = "\n".join("line %d" % n for n in range(12))
        self.edit_and_import({ELDER_KEY: lines})
        commands = self.page_commands()
        self.assertEqual([c.get("code") for c in commands], [105] + [405] * 12
                         + [0])
        self.assertEqual(list(commands[0].get("parameters")), [2, False])

    def test_a_comment_grows_and_shrinks_with_its_head_line(self):
        self.dialogue_with(self.command(108, 0, self.S("a")),
                           self.command(408, 0, self.S("b")))
        self.edit_and_import({ELDER_KEY: "x\ny\nz"},
                             export_args={"include_comments": True})
        commands = self.page_commands()
        self.assertEqual([c.get("code") for c in commands],
                         [108, 408, 408, 0])
        self.assertEqual([c.get("parameters")[0].data for c in commands[:3]],
                         [b"x", b"y", b"z"])
        self.edit_and_import({ELDER_KEY: "only"},
                             export_args={"include_comments": True})
        commands = self.page_commands()
        self.assertEqual([c.get("code") for c in commands], [108, 0])
        self.assertEqual(commands[0].get("parameters")[0].data, b"only")

    def test_more_scrolling_lines_than_the_ceiling_are_refused(self):
        self.dialogue_with(self.command(105, 0, 2, False),
                           self.command(405, 0, self.S("one")))
        reply = self.edit_and_import({ELDER_KEY: "\n".join(
            ["x"] * (text_tools.MAX_LONG_TEXT_LINES + 1))})
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("above the %d" % text_tools.MAX_LONG_TEXT_LINES,
                      reply["skipped"][0]["reason"])


class TestAStaleEntryIsReportedAndSkipped(TextTest):

    def export_then_change_the_project(self):
        document = self.document(self.export(preview=0))
        for entry in document["entries"]:
            if entry["key"] == "items:1/name":
                entry["text"] = "From the file"
        path = self.write_file(document)
        # The project moves on: someone else renames the item.
        self.set_field("items", 1, "name", "Changed meanwhile")
        return path

    def test_it_is_not_written(self):
        path = self.export_then_change_the_project()
        before = self.data_files()
        reply = self.call("text", op="import", path=path)
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual(reply["skipped_count"], 1)
        self.assertEqual(reply["skipped"][0]["key"], "items:1/name")
        self.assertIn("stale", reply["skipped"][0]["reason"])
        self.assertEqual(self.data_files(), before)

    def test_force_writes_it_and_says_it_did(self):
        path = self.export_then_change_the_project()
        reply = self.call("text", op="import", path=path, force=True)
        self.assertEqual(reply["edit_count"], 1)
        self.assertEqual(reply["applied"], [{
            "key": "items:1/name", "kind": "name",
            "before": "Changed meanwhile", "after": "From the file",
            "forced": True}])
        self.assertEqual(self.entries()["items:1/name"]["text"],
                         "From the file")

    def test_a_stale_entry_does_not_stop_the_fresh_ones(self):
        document = self.document(self.export(preview=0))
        for entry in document["entries"]:
            if entry["key"] in ("items:1/name", "items:2/name"):
                entry["text"] = entry["text"] + "!"
        path = self.write_file(document)
        self.set_field("items", 1, "name", "Changed meanwhile")
        reply = self.call("text", op="import", path=path)
        self.assertEqual([r["key"] for r in reply["applied"]], ["items:2/name"])
        self.assertEqual([r["key"] for r in reply["skipped"]], ["items:1/name"])
        self.assertEqual(self.entries()["items:2/name"]["text"], "Hi-Potion!")

    def test_an_unedited_entry_the_project_has_changed_is_reported_not_written(self):
        """The file keeps only the hash of the old text, so an entry the
        project has since changed cannot be told from one that was edited."""
        document = self.document(self.export(preview=0))
        path = self.write_file(document)
        self.set_field("items", 1, "name", "Changed meanwhile")
        before = self.data_files()
        reply = self.call("text", op="import", path=path)
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual([r["key"] for r in reply["skipped"]], ["items:1/name"])
        self.assertEqual(self.data_files(), before)

    def test_an_entry_whose_text_equals_the_projects_is_simply_unchanged(self):
        document = self.document(self.export(preview=0))
        for entry in document["entries"]:
            if entry["key"] == "items:1/name":
                entry["text"] = "Same now"
                entry["original_hash"] = "0" * 24
        path = self.write_file(document)
        self.set_field("items", 1, "name", "Same now")
        reply = self.call("text", op="import", path=path)
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual(reply["skipped"], [])

    def test_a_group_changed_underneath_is_stale_too(self):
        document = self.document(self.export(preview=0))
        for entry in document["entries"]:
            if entry["key"] == ELDER_KEY:
                entry["text"] = "Edited"
        path = self.write_file(document)
        self.dialogue_with(self.command(101, 0, self.S(""), 0, 0, 2),
                           self.command(401, 0, self.S("Someone else's.")))
        reply = self.call("text", op="import", path=path)
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("stale", reply["skipped"][0]["reason"])


class TestAKeyThatIsNotTextWritesNothing(TextTest):

    def import_entries(self, entries, **import_args):
        document = {"format": "rpgvxace-mcp-text", "version": 1,
                    "engine": self.project.engine,
                    "encoding": self.project.codec.name, "entries": entries}
        return self.call("text", op="import", path=self.write_file(document),
                         **import_args)

    def entry(self, key, text="hacked", kind="name", digest=None):
        return {"key": key, "kind": kind, "text": text,
                "original_hash": digest or "0" * 24}

    def test_keys_that_address_things_that_are_not_text(self):
        before = self.data_files()
        keys = [
            "items:1/price", "items:1/note", "items:1/icon_index",
            "items:1/features", "actors:1/character_name",
            "system/version_id", "system/switches/1", "system/terms",
            "system/terms/basic", "system/bgm", "map_infos:1/name",
            "troops:1/name", "common_events:1/name", "map:1/note",
            "map:1/event:1/name", "map:1/event:1/page:0",
            "map:1/event:1/page:0/cmd:3", "map:1/event:1/page:0/cmd:99999",
            "map:1/event:99/page:0/cmd:0",
            "map:99999/display_name", "map:0/display_name",
            "scripts:1/name", "tilesets:1/name", "items:0/name",
            "items:999999/name", "items:1/name/extra",
        ]
        for key in keys:
            with self.subTest(key=key):
                reply = self.import_entries([self.entry(key)], force=True)
                self.assertEqual(reply["edit_count"], 0, key)
                self.assertEqual(len(reply["skipped"]), 1, key)
                self.assertIn("no text is stored at this key",
                              reply["skipped"][0]["reason"])
        self.assertEqual(self.data_files(), before)

    def test_a_continuation_line_inside_a_message_is_not_a_key(self):
        reply = self.import_entries([self.entry(
            "map:1/event:1/page:0/cmd:1", kind="show_text")], force=True)
        self.assertEqual(reply["edit_count"], 0)

    def test_a_command_that_has_since_changed_code_is_not_written(self):
        document = self.document(self.export(preview=0))
        for entry in document["entries"]:
            if entry["key"] == GATE_KEY:
                entry["text"] = "Edited"
        path = self.write_file(document)
        # The message at cmd:4 becomes a Control Switches command.
        commands = self.project.load_map(1).get("events").get(1).get(
            "pages")[0].get("list")
        commands[4] = self.command(121, 1, 2, 2, 0)
        self.write_map(1, self.project.load_map(1))
        before = self.data_files()
        for force in (False, True):
            with self.subTest(force=force):
                reply = self.call("text", op="import", path=path, force=force)
                self.assertEqual(reply["edit_count"], 0)
                self.assertEqual(reply["skipped"][0]["key"], GATE_KEY)
                self.assertIn("no text is stored", reply["skipped"][0]["reason"])
        self.assertEqual(self.data_files(), before)

    def test_a_key_now_holding_a_different_kind_of_text_is_not_written(self):
        document = self.document(self.export(preview=0))
        for entry in document["entries"]:
            if entry["key"] == ELDER_KEY:
                entry["text"] = "Edited"
        path = self.write_file(document)
        self.dialogue_with(self.command(105, 0, 2, False),
                           self.command(405, 0, self.S("Scrolling now")))
        reply = self.call("text", op="import", path=path, force=True)
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("holds a scroll_text", reply["skipped"][0]["reason"])
        self.assertEqual(self.entries()[ELDER_KEY]["text"], "Scrolling now")

    def test_an_entry_with_no_kind_is_a_malformed_file(self):
        with self.assertRaises(ToolError) as caught:
            self.import_entries([{"key": "items:1/name", "text": "x",
                                  "original_hash": "0" * 24}])
        self.assertIn("kind must be a string", str(caught.exception))


class TestAnEditThatBreaksARuleIsSkippedWithItsReason(TextTest):

    def one(self, key, text):
        reply = self.edit_and_import({key: text})
        return reply

    def test_control_characters_are_refused(self):
        for label, char in (("NUL", "\x00"), ("ESC", "\x1b"),
                            ("CR", "\r"), ("tab", "\t"), ("DEL", "\x7f"),
                            ("C1", "\x85"), ("line separator", "\u2028"),
                            ("paragraph separator", "\u2029")):
            with self.subTest(label):
                before = self.data_files()
                reply = self.one(ELDER_KEY, "bad %s here" % char)
                self.assertEqual(reply["edit_count"], 0)
                self.assertIn("control character",
                              reply["skipped"][0]["reason"])
                self.assertEqual(self.data_files(), before)

    def test_a_lone_surrogate_is_refused(self):
        document = self.document(self.export(preview=0))
        raw = json.dumps(document)
        raw = raw.replace('"text": "Potion"', '"text": "Po\\ud800tion"')
        self.assertIn("\\ud800", raw)
        path = self.write_file(raw.encode("ascii"), "surrogate.json")
        before = self.data_files()
        reply = self.call("text", op="import", path=path)
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("lone surrogate", reply["skipped"][0]["reason"])
        self.assertEqual(self.data_files(), before)

    def test_a_name_is_one_line(self):
        reply = self.one("items:1/name", "Two\nlines")
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("one line", reply["skipped"][0]["reason"])

    def test_a_name_cannot_be_blanked(self):
        reply = self.one("items:1/name", "")
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("cannot be empty", reply["skipped"][0]["reason"])

    def test_text_past_its_byte_ceiling_is_refused(self):
        reply = self.one("items:1/description",
                         "x" * (text_tools.MAX_TEXT_ENTRY_BYTES + 1))
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("above the %d" % text_tools.MAX_TEXT_ENTRY_BYTES,
                      reply["skipped"][0]["reason"])

    def test_a_multibyte_text_is_judged_by_its_bytes(self):
        reply = self.one("items:1/description",
                         "\u00e9" * (text_tools.MAX_TEXT_ENTRY_BYTES // 2 + 1))
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("encoded", reply["skipped"][0]["reason"])

    def test_one_bad_entry_does_not_cost_the_good_ones(self):
        reply = self.edit_and_import({"items:1/name": "Good",
                                      "items:2/name": "Bad\x00",
                                      "actors:1/name": "Also good"})
        self.assertEqual(sorted(r["key"] for r in reply["applied"]),
                         ["actors:1/name", "items:1/name"])
        self.assertEqual([r["key"] for r in reply["skipped"]],
                         ["items:2/name"])
        self.assertEqual(self.entries()["items:2/name"]["text"], "Hi-Potion")


class TestAnImportFileThatLies(TextTest):

    def good(self):
        return self.document(self.export(preview=0))

    def refuses(self, document, fragment):
        path = self.write_file(document)
        before = self.data_files()
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="import", path=path)
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(self.data_files(), before)

    def test_a_repeated_key_is_a_refusal_not_a_choice(self):
        document = self.good()
        document["entries"].append(dict(document["entries"][0]))
        self.refuses(document, "repeats the key")

    def test_a_repeated_json_member_is_a_refusal(self):
        raw = Path(self.export(preview=0)["path"]).read_text("utf-8")
        raw = raw.replace('"text": "Potion"',
                          '"text": "Potion", "text": "Elixir"')
        self.assertIn('"Elixir"', raw)
        self.refuses(raw.encode("utf-8"), "repeats the key")

    def test_entries_that_is_not_a_list(self):
        document = self.good()
        document["entries"] = {"items:1/name": "x"}
        self.refuses(document, "entries must be an array")

    def test_an_entry_that_is_not_an_object(self):
        document = self.good()
        document["entries"][3] = "items:1/name"
        self.refuses(document, "entries[3] must be an object")

    def test_wrong_types_in_an_entry(self):
        for field, value in (("key", 7), ("key", None), ("key", ["a"]),
                             ("text", 7), ("text", None), ("text", ["a"]),
                             ("kind", 1), ("original_hash", 1)):
            with self.subTest(field=field, value=value):
                document = self.good()
                document["entries"][0][field] = value
                path = self.write_file(document)
                with self.assertRaises(ToolError) as caught:
                    self.call("text", op="import", path=path)
                self.assertIn("entries[0]", str(caught.exception))

    def test_a_key_that_is_not_shaped_like_ours(self):
        for key in ("", "items:1/name\n", "../../Data/Items", "items 1/name",
                    "items:1/na\u00efme", "a" * 201, "items:1/name\x00",
                    "map:1/event:1/page:0/cmd:-1"):
            with self.subTest(key=key[:30]):
                document = self.good()
                document["entries"][0]["key"] = key
                self.refuses(document, "entries[0].key is not a key")

    def test_a_hash_that_is_not_one_of_ours(self):
        for value in ("", "zz" * 12, "A" * 24, "0" * 23, "0" * 25):
            with self.subTest(value=value):
                document = self.good()
                document["entries"][0]["original_hash"] = value
                self.refuses(document, "original_hash")

    def test_a_document_that_is_not_ours(self):
        for mutate, fragment in (
                (lambda d: d.update(format="something-else"), "not a file"),
                (lambda d: d.pop("format"), "not a file"),
                (lambda d: d.update(version=2), "version"),
                (lambda d: d.update(version=True), "version"),
                (lambda d: d.update(version="1"), "version"),
                (lambda d: d.update(engine="vx"), "engine"),
                (lambda d: d.update(encoding="cp932"), "encoding"),
                (lambda d: d.pop("entries"), "entries must be an array")):
            with self.subTest(fragment=fragment):
                document = self.good()
                mutate(document)
                self.refuses(document, fragment)

    def test_json_that_is_not_an_object(self):
        for raw in (b"[]", b"null", b"7", b'"text"', b"true"):
            with self.subTest(raw=raw):
                self.refuses(raw, "not a file")

    def test_bytes_that_are_not_json_or_not_utf8(self):
        self.refuses(b"{not json", "not usable JSON")
        self.refuses(b"", "not usable JSON")
        self.refuses(b"\xff\xfe\x00{}", "not UTF-8")
        self.refuses(b'{"format": NaN}', "NaN is not valid JSON")
        self.refuses(b'{"format": Infinity}', "Infinity is not valid JSON")

    def test_a_document_nested_far_past_the_parsers_stack(self):
        self.refuses(b"[" * 200_000 + b"]" * 200_000, "not usable JSON")
        self.refuses(b'{"a":' * 100_000 + b"1" + b"}" * 100_000,
                     "not usable JSON")

    def test_more_entries_than_an_export_carries(self):
        original = text_tools.MAX_EXPORT_ENTRIES
        self.addCleanup(setattr, text_tools, "MAX_EXPORT_ENTRIES", original)
        document = self.good()
        text_tools.MAX_EXPORT_ENTRIES = len(document["entries"]) - 1
        self.refuses(document, "entries; an export carries at most")

    def test_a_file_past_the_size_ceiling_is_not_read(self):
        original = text_tools.MAX_EXPORT_FILE_BYTES
        self.addCleanup(setattr, text_tools, "MAX_EXPORT_FILE_BYTES", original)
        path = self.write_file(self.good())
        text_tools.MAX_EXPORT_FILE_BYTES = Path(path).stat().st_size - 1
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="import", path=path)
        self.assertIn("above the", str(caught.exception))

    def test_an_enormous_text_is_one_skipped_entry_not_a_crash(self):
        document = self.good()
        document["entries"][0]["text"] = "x" * 5_000_000
        key = document["entries"][0]["key"]
        reply = self.call("text", op="import", path=self.write_file(document))
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual(reply["skipped"][0]["key"], key)
        self.assertLess(len(json.dumps(reply)), 20_000)

    def test_a_hostile_key_is_not_echoed_whole(self):
        document = self.good()
        document["entries"][0]["key"] = "a" * 500
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="import", path=self.write_file(document))
        self.assertLess(len(str(caught.exception)), 300)

    def test_a_file_from_another_encoding_is_not_written_into_this_one(self):
        document = self.good()
        document["encoding"] = "cp932"
        self.refuses(document, "encoding")


class TestAnImportOnlyReadsInsideTheProject(TextTest):

    def refused(self, path, fragment="outside the project"):
        before = self.data_files()
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="import", path=path)
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(self.data_files(), before)

    def outside_file(self, name="outside.json"):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        exported = self.document(self.export(preview=0))
        path = folder / name
        path.write_text(json.dumps(exported), "utf-8")
        return path

    def test_a_file_outside_the_project_is_refused(self):
        self.refused(str(self.outside_file()))

    def test_a_dotdot_that_leaves_the_project_is_refused(self):
        outside = self.outside_file()
        relative = os.path.relpath(outside, self.exports) \
            if self.exports.exists() else None
        self.exports.mkdir(parents=True, exist_ok=True)
        relative = os.path.relpath(outside, self.exports)
        self.assertIn("..", relative)
        self.refused(relative)

    def test_a_dotdot_that_stays_inside_the_project_works(self):
        exported = self.export(preview=0)
        name = Path(exported["path"]).name
        reply = self.call("text", op="import",
                          path="../exports/../exports/" + name)
        self.assertEqual(reply["edit_count"], 0)

    def test_a_relative_name_is_looked_up_in_the_exports_folder(self):
        exported = self.export(preview=0)
        reply = self.call("text", op="import",
                          path=Path(exported["path"]).name)
        self.assertEqual(reply["entries_in_file"], exported["entries"])

    def test_a_file_elsewhere_in_the_project_is_allowed(self):
        exported = self.export(preview=0)
        elsewhere = self.project.root / "my-translation.json"
        shutil.copy(exported["path"], elsewhere)
        reply = self.call("text", op="import", path=str(elsewhere))
        self.assertEqual(reply["edit_count"], 0)

    def test_a_link_inside_the_exports_folder_to_a_file_outside(self):
        outside = self.outside_file()
        self.exports.mkdir(parents=True, exist_ok=True)
        link = self.exports / "linked.json"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("cannot make a symlink here")
        self.refused(str(link))

    def test_a_junction_inside_the_project_to_a_folder_outside_it(self):
        outside = self.outside_file()
        link = self.project.root / "linked"
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link),
                               str(outside.parent)], capture_output=True)
        if done.returncode != 0:
            self.skipTest("cannot make a junction here")
        self.addCleanup(subprocess.run, ["cmd", "/c", "rmdir", str(link)])
        self.assertTrue((link / outside.name).is_file())
        self.refused(str(link / outside.name))

    def test_a_junction_as_the_exports_folder_refuses_every_file_in_it(self):
        outside = self.outside_file()
        work = self.project.work_dir()
        shutil.rmtree(self.exports, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(["cmd", "/c", "mklink", "/J",
                               str(work / "exports"), str(outside.parent)],
                              capture_output=True)
        if done.returncode != 0:
            self.skipTest("cannot make a junction here")
        self.addCleanup(subprocess.run,
                        ["cmd", "/c", "rmdir", str(work / "exports")])
        self.refused(outside.name)
        self.refused(str(work / "exports" / outside.name))

    def test_other_things_that_are_not_an_import_file(self):
        exported = self.export(preview=0)
        self.refused(str(Path(exported["path"]).parent), "not a .json file")
        txt = Path(exported["path"]).with_suffix(".txt")
        shutil.copy(exported["path"], txt)
        self.refused(str(txt), "not a .json file")
        self.refused(str(self.exports / "missing.json"), "no such file")
        self.refused(str(self.exports / "folder.json"), "no such file")
        (self.exports / "folder.json").mkdir()
        self.refused(str(self.exports / "folder.json"), "no such file")
        self.refused("bad\x00name.json", "control character")
        self.refused("bad\nname.json", "control character")
        self.refused("C:\\Windows\\win.ini", "outside the project")
        self.refused("\\\\?\\C:\\Windows\\win.ini", "outside the project")

    def test_a_path_longer_than_the_schema_allows(self):
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="import", path="x" * 1025)
        self.assertIn("at most 1024", str(caught.exception))

    def test_a_device_name_is_not_opened(self):
        for name in ("CON", "NUL", "con.json", "nul.json", "COM1.json",
                     "AUX"):
            with self.subTest(name=name):
                with self.assertRaises(ToolError):
                    self.call("text", op="import", path=name)


class TestTheExportFolderIsCheckedBeforeItIsMade(TextTest):

    def plant(self, link, target):
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link),
                               str(target)], capture_output=True)
        if done.returncode != 0:
            self.skipTest("cannot make a junction here")
        self.addCleanup(subprocess.run, ["cmd", "/c", "rmdir", str(link)])

    def test_a_work_folder_that_is_a_link_out_of_the_project(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        work = self.project.work_dir()
        if work.exists():
            shutil.rmtree(work)
        self.plant(work, outside)
        with self.assertRaises(ProjectError) as caught:
            self.export()
        self.assertIn("outside", str(caught.exception))
        self.assertEqual(list(outside.iterdir()), [],
                         "the exports folder was made outside the project")

    def test_an_exports_folder_that_is_a_link_out_of_the_project(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        work = self.project.work_dir()
        work.mkdir(parents=True, exist_ok=True)
        self.plant(work / "exports", outside)
        with self.assertRaises(ProjectError) as caught:
            self.export()
        self.assertIn("outside", str(caught.exception))
        self.assertEqual(list(outside.iterdir()), [])


class TestADryRunWritesNothingAndShowsBeforeAndAfter(TextTest):

    def test_nothing_reaches_the_disk(self):
        before = self.data_files()
        reply = self.edit_and_import({"items:1/name": "Elixir",
                                      ELDER_KEY: "One\nTwo\nThree"},
                                     dry_run=True)
        self.assertEqual(self.data_files(), before)
        self.assertFalse((self.project.work_dir() / "backups").exists())
        self.assertEqual(self.project.recent_changes(), [])
        self.assertTrue(reply["dry_run"])
        self.assertEqual(next(iter(reply)), "dry_run")

    def test_each_change_is_before_and_would_become(self):
        reply = self.edit_and_import({"items:1/name": "Elixir"}, dry_run=True)
        self.assertEqual(reply["would_apply"], [{
            "key": "items:1/name", "kind": "name", "before": "Potion",
            "would_become": "Elixir"}])
        self.assertNotIn("applied", reply)
        self.assertEqual(reply["edit_count"], 1)
        self.assertEqual([w["file"] for w in reply["would_write"]],
                         ["Items" + self.extension])

    def test_the_preview_does_not_leak_into_the_next_real_write(self):
        self.edit_and_import({"items:1/name": "Elixir"}, dry_run=True)
        # A later, unrelated write of the same file must not carry it.
        self.set_field("items", 2, "name", "Renamed")
        self.assertEqual(self.entries()["items:1/name"]["text"], "Potion")

    def test_a_dry_run_applies_the_same_rules(self):
        reply = self.edit_and_import({"items:1/name": "Bad\x00"}, dry_run=True)
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual(len(reply["skipped"]), 1)


class TestWritesAreStagedAcrossFiles(TextTest):

    def two_file_edit(self):
        return {"items:1/name": "Elixir", ELDER_KEY: "Changed"}

    def test_a_failure_on_the_only_file_leaves_nothing(self):
        before = self.data_files()
        with mock.patch.object(
                Project, "_assert_not_stale",
                side_effect=StaleFileError("Items changed on disk")):
            with self.assertRaises(StaleFileError):
                self.edit_and_import({"items:1/name": "Elixir"})
        self.assertEqual(self.data_files(), before)
        # And the cache holds no leftover edit for a later write to persist.
        self.assertEqual(self.entries()["items:1/name"]["text"], "Potion")

    def test_a_failure_on_the_first_of_two_files_leaves_both(self):
        before = self.data_files()
        with mock.patch.object(
                Project, "_assert_not_stale",
                side_effect=StaleFileError("changed on disk")):
            with self.assertRaises(StaleFileError):
                self.edit_and_import(self.two_file_edit())
        self.assertEqual(self.data_files(), before)

    def test_a_failure_on_the_second_says_which_file_already_landed(self):
        real = Project._assert_not_stale

        def only_the_map(project, filename, path, cached):
            if filename.startswith("Map"):
                raise StaleFileError("%s changed on disk" % filename)
            return real(project, filename, path, cached)

        before = self.data_files()
        with mock.patch.object(Project, "_assert_not_stale", only_the_map):
            with self.assertRaises(ToolError) as caught:
                self.edit_and_import(self.two_file_edit())
        message = str(caught.exception)
        self.assertIn("Items" + self.extension + " were written", message)
        self.assertIn("Map001" + self.extension + " could not be", message)
        after = self.data_files()
        self.assertNotEqual(after["Items" + self.extension],
                            before["Items" + self.extension])
        self.assertEqual(after["Map001" + self.extension],
                         before["Map001" + self.extension])

    def test_a_value_that_cannot_be_marshalled_is_found_before_any_write(self):
        before = self.data_files()
        real = Project.save_file

        def failing(project, filename, dry_run=False, **kwargs):
            if dry_run and filename.startswith("Map"):
                raise ProjectError("cannot marshal %s" % filename)
            return real(project, filename, dry_run=dry_run, **kwargs)

        with mock.patch.object(Project, "save_file", failing):
            with self.assertRaises(ProjectError):
                self.edit_and_import(self.two_file_edit())
        self.assertEqual(self.data_files(), before)

    def test_a_read_only_server_writes_nothing_but_previews(self):
        class Locked(TextTest):
            read_only = True

            def runTest(self):
                pass
        locked = Locked()
        locked.setUp()
        self.addCleanup(locked.doCleanups)
        exported = locked.export(preview=0)
        document = locked.document(exported)
        document["entries"][0]["text"] = "Changed"
        path = locked.write_file(document)
        before = locked.data_files()
        preview = locked.call("text", op="import", path=path, dry_run=True)
        self.assertEqual(preview["edit_count"], 1)
        with self.assertRaises(ReadOnlyError):
            locked.call("text", op="import", path=path)
        self.assertEqual(locked.data_files(), before)

    def test_a_read_only_server_can_still_export(self):
        class Locked(TextTest):
            read_only = True

            def runTest(self):
                pass
        locked = Locked()
        locked.setUp()
        self.addCleanup(locked.doCleanups)
        self.assertGreater(locked.export()["entries"], 50)


class TestAnImportReplyIsBounded(TextTest):

    def patched(self, name, value):
        original = getattr(text_tools, name)
        self.addCleanup(setattr, text_tools, name, original)
        setattr(text_tools, name, value)

    def test_applied_rows_are_capped_and_counted(self):
        self.patched("MAX_REPORTED_EDITS", 3)
        reply = self.edit_and_import({"items:1/name": "A", "items:2/name": "B",
                                      "actors:1/name": "C",
                                      "actors:2/name": "D",
                                      "states:1/name": "E"})
        self.assertEqual(reply["edit_count"], 5)
        self.assertEqual(len(reply["applied"]), 3)
        self.assertTrue(reply["edits_truncated"])
        self.assertIn("edit_count is the total", reply["edits_note"])

    def test_skipped_rows_are_capped_and_counted(self):
        self.patched("MAX_REPORTED_SKIPS", 2)
        reply = self.edit_and_import({"items:1/name": "A\x00",
                                      "items:2/name": "B\x00",
                                      "actors:1/name": "C\x00",
                                      "actors:2/name": "D\x00"})
        self.assertEqual(reply["skipped_count"], 4)
        self.assertEqual(len(reply["skipped"]), 2)
        self.assertTrue(reply["skipped_truncated"])

    def test_long_texts_are_clipped_in_the_reply_not_in_the_write(self):
        long_text = "word " * 10_000
        reply = self.edit_and_import({"items:1/description": long_text})
        row = reply["applied"][0]
        self.assertLessEqual(len(row["after"]), text_tools.MAX_SHOWN_CHARS + 40)
        self.assertIn("(50000 characters)", row["after"])
        item = self.reopen().project.get_entry("items", 1)
        self.assertEqual(item.get("description").data, long_text.encode())

    def test_a_control_character_in_a_stored_text_is_not_echoed_raw(self):
        self.set_field("items", 1, "description", RubyString(
            b"hello \x1b[31mred", {"E": True}))
        reply = self.edit_and_import({"items:1/description": "plain"})
        self.assertNotIn("\x1b", json.dumps(reply, ensure_ascii=False))
        self.assertIn("hello ?[31mred", reply["applied"][0]["before"])


class TestAProjectThatSharesObjects(TextTest):

    def test_a_list_shared_by_two_pages_is_read_only_at_its_one_key(self):
        game_map = self.project.load_map(1)
        event = game_map.get("events").get(1)
        pages = event.get("pages")
        pages[1].set("list", pages[0].get("list"))
        self.write_map(1, game_map)
        entries = self.entries()
        shared = [e for k, e in entries.items()
                  if k.startswith("map:1/event:1/") and e["kind"] == "show_text"]
        self.assertEqual(len(shared), 2)
        for entry in shared:
            self.assertTrue(entry["read_only"], entry)
            self.assertIn("more than one place", entry["reason"])
        self.assertIn("shared_note", self.export())

    def test_editing_through_a_shared_list_is_refused_and_writes_nothing(self):
        game_map = self.project.load_map(1)
        pages = game_map.get("events").get(1).get("pages")
        pages[1].set("list", pages[0].get("list"))
        self.write_map(1, game_map)
        before = self.data_files()
        reply = self.edit_and_import({ELDER_KEY: "Edited"})
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("read-only", reply["skipped"][0]["reason"])
        self.assertEqual(self.data_files(), before)

    def test_an_unedited_round_trip_of_shared_structure_is_byte_identical(self):
        game_map = self.project.load_map(1)
        pages = game_map.get("events").get(1).get("pages")
        pages[1].set("list", pages[0].get("list"))
        self.write_map(1, game_map)
        before = self.data_files()
        exported = self.export(preview=0)
        self.call("text", op="import", path=exported["path"])
        self.assertEqual(self.data_files(), before)

    def test_a_database_entry_in_two_slots_is_read_only(self):
        items = self.project.load_data("items")
        items[2] = items[1]
        self.project.save_file(self.project.filename_for("items"),
                               tool="test", summary="share")
        self.ctx = self.reopen()
        self.project = self.ctx.project
        entries = self.entries()
        self.assertTrue(entries["items:1/name"]["read_only"])
        self.assertNotIn("items:2/name", entries)

    def test_a_command_object_in_many_slots_is_edited_in_one_slot_only(self):
        """One comment command, fifty slots of one list: Marshal links."""
        shared = self.command(108, 0, self.S("x"))
        self.set_list(1, 1, 0, [shared] * 50 + [self.command(0, 0)])
        scope = {"include_comments": True, "scopes": ["map_events"],
                 "map_ids": [1]}
        entries = self.entries(**scope)
        self.assertEqual(sorted(k for k in entries if "event:1/page:0" in k),
                         sorted("map:1/event:1/page:0/cmd:%d" % n
                                for n in range(50)))
        key = "map:1/event:1/page:0/cmd:5"
        reply = self.edit_and_import({key: "changed"}, export_args=scope)
        self.assertEqual(reply["edit_count"], 1)
        fresh = self.texts(**scope)
        self.assertEqual(fresh[key], "changed")
        self.assertEqual({t for k, t in fresh.items()
                          if "event:1/page:0" in k and k != key}, {"x"})

    def test_a_map_of_thousands_of_shared_pages_is_bounded_and_read_only(self):
        class Shared(SharedObjectMap, TextTest):
            def runTest(self):
                pass
        shared = Shared()
        shared.engine = self.engine
        shared.tmp, shared.project, shared.ctx = (
            self.tmp, self.project, self.ctx)
        shared.assertLess = self.assertLess
        shared.reopen = self.reopen
        shared.call = self.call
        shared.craft_shared_map()
        self.ctx = shared.ctx
        self.project = self.ctx.project
        started = time.perf_counter()
        entries = self.entries(include_comments=True, scopes=["map_events"],
                               map_ids=[1])
        self.assertLess(time.perf_counter() - started, 10)
        self.assertEqual(len(entries), 2000)
        self.assertTrue(all(e.get("read_only") for e in entries.values()))

    def test_a_cyclic_parameter_list_is_not_text_and_not_a_crash(self):
        loop = []
        loop.append(loop)
        self.dialogue_with(self.command(102, 0, loop, 0),
                           self.command(101, 0, loop, 0, 0, 2),
                           self.command(401, 0, loop))
        entries = self.entries()
        self.assertFalse([k for k in entries if k.startswith("map:1/event:1/page:0")])
        self.assertEqual(self.entries(include_comments=True)
                         .get("map:1/event:1/page:0/cmd:1"), None)

    def test_odd_command_fields_are_skipped_not_fatal(self):
        weird = [RubyObject("RPG::EventCommand", {"@code": 101.0}),
                 RubyObject("RPG::EventCommand", {"@code": True}),
                 RubyObject("RPG::EventCommand", {"@code": RubyString(b"101")}),
                 RubyObject("RPG::EventCommand", {"@code": 10 ** 5000}),
                 RubyObject("RPG::EventCommand", {"@code": 101,
                                                  "@parameters": 5}),
                 RubyObject("RPG::EventCommand", {"@code": 401,
                                                  "@parameters": RubyHash()}),
                 5, None, [], RubyString(b"text"),
                 self.command(101, 0, self.S(""), 0, 0, 2),
                 self.command(401, 0, self.S("the only real one")),
                 self.command(0, 0)]
        self.set_list(1, 1, 0, weird)
        texts = {k: t for k, t in self.texts().items()
                 if k.startswith("map:1/event:1/page:0")}
        self.assertEqual(texts, {"map:1/event:1/page:0/cmd:10":
                                 "the only real one"})

    def test_ids_that_are_not_ids_are_skipped(self):
        game_map = self.project.load_map(1)
        event = game_map.get("events").get(1)
        events = RubyHash([(1, event), (10 ** 5000, event),
                           (RubyString(b"two"), event), (-3, event),
                           (10 ** 9, event), (True, event)])
        game_map.set("events", events)
        self.write_map(1, game_map)
        keys = [k for k in self.entries() if k.startswith("map:1/event:")]
        self.assertTrue(keys)
        self.assertEqual({k.split("/")[1] for k in keys}, {"event:1"})

    def test_two_events_with_one_id_cannot_both_be_addressed(self):
        game_map = self.project.load_map(1)
        first = game_map.get("events").get(1)
        second = game_map.get("events").get(2)
        second.set("pages", marshal_rb.load(marshal_rb.dump(
            first.get("pages"))))
        game_map.set("events", RubyHash([(1, first), (1, second)]))
        self.write_map(1, game_map)
        listed = self.document(self.export())["entries"]
        mine = [e for e in listed if e["key"].startswith("map:1/event:")]
        keys = [e["key"] for e in mine]
        self.assertEqual(len(keys), len(set(keys)), keys)
        self.assertTrue(keys)
        for entry in mine:
            self.assertTrue(entry.get("read_only"), entry)
        self.assertIn("share a key", self.export()["shared_note"])

    def test_an_enormous_string_linked_from_many_places_is_charged_each_time(self):
        big = RubyString(b"y" * 60_000, {"E": True})
        lines = [self.command(401, 0, big) for _ in range(200)]
        self.dialogue_with(self.command(101, 0, self.S(""), 0, 0, 2), *lines)
        original = text_tools._TEXT_BYTE_BUDGET
        self.addCleanup(setattr, text_tools, "_TEXT_BYTE_BUDGET", original)
        text_tools._TEXT_BYTE_BUDGET = 1_000_000
        reply = self.export(scopes=["map_events"])
        self.assertIn("passed 1000000 bytes", reply["walk_stopped"])


class TestAnEncodingThatIsNotUtf8(TextTest):
    """A Japanese install stores Shift_JIS; text must survive both ways."""

    def reopen(self):
        return Context(Project(self.tmp / "project", encoding="cp932"))

    def setUp(self):
        super().setUp()
        self.ctx = self.reopen()
        self.project = self.ctx.project

    def test_text_is_read_and_written_in_the_projects_codec(self):
        self.set_field("items", 1, "name", "ポーション")
        self.assertEqual(self.entries()["items:1/name"]["text"], "ポーション")
        self.edit_and_import({"items:1/name": "ハイポーション"})
        stored = self.reopen().project.get_entry("items", 1).get("name").data
        self.assertEqual(stored, "ハイポーション".encode("cp932"))

    def test_the_file_says_which_encoding_it_came_from(self):
        document = self.document(self.export())
        self.assertEqual(document["encoding"], "cp932")

    def test_a_character_the_codec_lacks_is_refused_not_replaced(self):
        before = self.data_files()
        reply = self.edit_and_import({"items:1/name": "Potion \U0001F9EA"})
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("cannot be written", reply["skipped"][0]["reason"])
        self.assertEqual(self.data_files(), before)

    def test_bytes_that_decode_but_do_not_encode_back_are_read_only(self):
        # 0x879B and 0x81BF are two spellings of one character in cp932.
        self.set_field("weapons", 1, "name", RubyString(b"\x87\x9b"))
        entry = self.entries()["weapons:1/name"]
        self.assertTrue(entry["read_only"])
        self.assertIn("does not encode back", entry["reason"])
        before = self.data_files()
        reply = self.edit_and_import({"weapons:1/name": "Sword"})
        self.assertEqual(reply["edit_count"], 0)
        self.assertIn("read-only", reply["skipped"][0]["reason"])
        self.assertEqual(self.data_files(), before)

    def test_bytes_the_codec_cannot_decode_are_read_only(self):
        self.set_field("weapons", 1, "name", RubyString(b"ab\x81"))
        entry = self.entries()["weapons:1/name"]
        self.assertTrue(entry["read_only"])
        self.assertIn("not valid cp932", entry["reason"])
        self.assertEqual(entry["text"], "ab\ufffd")
        self.assertEqual(entry["original_hash"],
                         stored_hash("name", b"ab\x81"))

    def test_a_utf8_projects_invalid_bytes_are_read_only_too(self):
        self.reopen = lambda: Context(Project(self.tmp / "project",
                                              encoding="utf-8"))
        self.ctx = self.reopen()
        self.project = self.ctx.project
        self.set_field("weapons", 1, "name", RubyString(
            b"caf\xe9", {"E": True}))
        entry = self.entries()["weapons:1/name"]
        self.assertTrue(entry["read_only"])
        self.assertIn("not valid utf-8", entry["reason"])


class TestVXAndXP(unittest.TestCase):

    def test_vx_exports_and_round_trips(self):
        class VX(TextTest):
            engine = "vx"

            def runTest(self):
                pass
        case = VX()
        case.setUp()
        self.addCleanup(case.doCleanups)
        entries = case.entries()
        self.assertEqual(entries[ELDER_KEY]["text"], FIRST_MESSAGE)
        self.assertEqual(entries["system/terms/level"]["text"], "Level")
        self.assertEqual(entries["system/terms/game_end"]["text"], "End Game")
        before = case.data_files()
        exported = case.export(preview=0)
        reply = case.call("text", op="import", path=exported["path"])
        self.assertEqual(reply["edit_count"], 0)
        self.assertEqual(case.data_files(), before)
        reply = case.edit_and_import({ELDER_KEY: "Hi\nthere\nfriend"})
        self.assertEqual(reply["edit_count"], 1)
        self.assertEqual(case.codes(), [101, 401, 401, 401, 111, 101, 401,
                                        121, 412, 0])

    def test_xp_is_refused_with_the_reason(self):
        class XP(TextTest):
            engine = "xp"

            def runTest(self):
                pass
        case = XP()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for op_args in ({"op": "export"}, {"op": "import", "path": "x.json"}):
            with self.assertRaises(ToolError) as caught:
                case.call("text", **op_args)
            self.assertIn("VX Ace and VX", str(caught.exception))
            self.assertIn("first line in the 101", str(caught.exception))
        self.assertFalse(case.exports.exists())


class TestTheToolsArguments(TextTest):

    def test_an_op_is_required_and_known(self):
        with self.assertRaises(ToolError) as caught:
            self.call("text")
        self.assertIn("op is required", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="delete")
        self.assertIn("must be one of export, import", str(caught.exception))

    def test_an_argument_the_op_does_not_read_is_refused(self):
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="export", path="x.json")
        self.assertIn("does not read", str(caught.exception))
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="import", path="x.json", scopes=["system"])
        self.assertIn("does not read", str(caught.exception))

    def test_an_import_needs_a_path(self):
        with self.assertRaises(ToolError) as caught:
            self.call("text", op="import")
        self.assertIn("path is required", str(caught.exception))

    def test_wrong_typed_arguments_are_refusals_not_crashes(self):
        bad = [
            ("export", dict(map_ids=[True])), ("export", dict(map_ids=[1.5])),
            ("export", dict(map_ids=["1"])), ("export", dict(map_ids=[0])),
            ("export", dict(map_ids=[10 ** 5000])),
            ("export", dict(map_ids=[[1]])),
            ("export", dict(map_ids=list(range(1, 4098)))),
            ("export", dict(scopes="system")), ("export", dict(scopes=[1])),
            ("export", dict(scopes=[["system"]])),
            ("export", dict(include_comments="yes")),
            ("export", dict(preview=True)), ("export", dict(preview=-1)),
            ("export", dict(preview="3")), ("export", dict(preview=2.5)),
            ("export", dict(preview=10 ** 5000)),
            ("import", dict(path=5)), ("import", dict(path=["a"])),
            ("import", dict(path="a.json", force="yes")),
            ("import", dict(path="a.json", dry_run=1)),
        ]
        for op, arguments in bad:
            with self.subTest(op=op, arguments=sorted(arguments)):
                with self.assertRaises(ToolError):
                    self.call("text", op=op, **arguments)

    def test_every_ceiling_is_advertised(self):
        description = REGISTRY["text"].description
        for value in (text_tools.MAX_EXPORT_ENTRIES, text_tools.MAX_TEXT_ENTRY_BYTES,
                      text_tools.MAX_EXPORT_FILE_BYTES,
                      text_tools.MAX_KEPT_EXPORTS,
                      text_tools.MAX_PREVIEW_ENTRIES,
                      text_tools.MAX_KEY_CHARS,
                      text_tools.MAX_LONG_TEXT_LINES,
                      text_tools.MAX_EXPORT_MAPS,
                      text_tools.MAX_REPORTED_EDITS,
                      text_tools.MAX_REPORTED_SKIPS,
                      text_tools.MAX_SHOWN_CHARS):
            self.assertTrue(
                "{:,}".format(value) in description
                or str(value) in description, value)

    def test_the_tool_is_marked_as_writing(self):
        annotations = REGISTRY["text"].definition()["annotations"]
        self.assertFalse(annotations["readOnlyHint"])


if __name__ == "__main__":
    unittest.main()
