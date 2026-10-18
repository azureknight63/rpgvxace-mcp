"""Encrypted RGSS archives: the reader, and a project that ships only one.

There is no real archive on the machine that wrote this, and none is
downloaded, so the encoder below is written here from the format description
and not by calling the reader's own key arithmetic. A reader checked only
against its own mirror image would pass with the format wrong, so the one
thing that stands in for a real file is that the two sides share no code.
What that cannot catch is a mistake in the description itself; the module's
docstring says which details rest on that.
"""

import os
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))

from build_project import build  # noqa: E402

from rpgvxace_mcp import rgss_archive  # noqa: E402
from rpgvxace_mcp.paths import ProjectError, ReadOnlyError  # noqa: E402
from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.rgss_archive import ArchiveError, RgssArchive  # noqa: E402


M32 = 0xFFFFFFFF


def keystream(seed, count):
    """`count` successive keys: the seed, then each stepped (x7+3) from the last."""
    out, key = [], seed
    for _ in range(count):
        out.append(key)
        key = (key * 7 + 3) & M32
    return out


def scramble_words(data, key):
    """XOR `data` with a key that steps after every 4 bytes."""
    out = bytearray(data)
    words = keystream(key, len(out) // 4 + 1)
    for position in range(len(out)):
        out[position] ^= (words[position // 4] >> (8 * (position % 4))) & 0xFF
    return bytes(out)


def encode_v1(files, magic=0xDEADCAFE):
    """[name length][name][data length][data], one rolling key, stepped x7+3."""
    blob = bytearray(b"RGSSAD\0\x01")
    key = magic

    def step():
        nonlocal key
        key = (key * 7 + 3) & M32

    for name, data in files:
        raw = name.encode("utf-8")
        blob += struct.pack("<I", len(raw) ^ key)
        step()
        for byte in raw:
            blob.append(byte ^ (key & 0xFF))
            step()
        blob += struct.pack("<I", len(data) ^ key)
        step()
        # The data is keyed from the stream's current value, and does not
        # advance the stream: the next entry's name length uses `key` as is.
        blob += scramble_words(data, key)
    return bytes(blob)


def encode_v3(files, base=0x12345678, file_keys=None, order=None, pad=0):
    """Header, base key, then [offset][size][key][name length][name] each
    XORed with base*9+3, a zero offset to end, and the file bodies."""
    key = (base * 9 + 3) & M32
    file_keys = file_keys or [(0x1000193 * (i + 1)) & M32 for i in range(len(files))]
    table_size = sum(16 + len(name.encode("utf-8")) for name, _ in files) + 4
    cursor = 8 + 4 + table_size + pad
    entries, bodies = [], bytearray()
    for (name, data), fkey in zip(files, file_keys):
        entries.append((cursor + len(bodies), len(data), fkey, name))
        bodies += scramble_words(data, fkey)
    blob = bytearray(b"RGSSAD\0\x03") + struct.pack("<I", base)
    for offset, size, fkey, name in (order(entries) if order else entries):
        raw = name.encode("utf-8")
        for value in (offset, size, fkey, len(raw)):
            blob += struct.pack("<I", value ^ key)
        # Name bytes use the base key's four bytes in rotation.
        blob += bytes(b ^ ((key >> (8 * (i % 4))) & 0xFF)
                      for i, b in enumerate(raw))
    blob += struct.pack("<I", 0 ^ key)
    blob += bytes(pad) + bodies
    return bytes(blob)


SAMPLE = [
    ("Data/Actors.rvdata2", b"actor bytes \x00\x01\x02\xff"),
    ("Data\\Map001.rvdata2", b"m" * 11),          # backslash, as a Windows tool writes
    ("Graphics/Characters/Hero.png", bytes(range(256)) * 3),
    ("Audio/BGM/Theme.ogg", b""),                 # empty body
    ("Graphics/Pictures/\u30c6\u30b9\u30c8.png", b"x"),  # non-ASCII name
]


class Scratch(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="rgss-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, blob, name="Game.rgss3a"):
        path = self.dir / name
        path.write_bytes(blob)
        return path


class TestRoundTrip(Scratch):
    def check(self, blob, name):
        archive = RgssArchive.open(self.write(blob, name))
        self.assertEqual(
            [e.name for e in archive.entries],
            [n.replace("\\", "/") for n, _ in SAMPLE])
        for (n, data), entry in zip(SAMPLE, archive.entries):
            self.assertEqual(archive.read(entry), data, n)
        return archive

    def test_version_1_round_trips(self):
        self.assertEqual(self.check(encode_v1(SAMPLE), "Game.rgss2a").version, 1)

    def test_version_3_round_trips(self):
        self.assertEqual(self.check(encode_v3(SAMPLE), "Game.rgss3a").version, 3)

    def test_read_by_name_ignores_case_and_separator(self):
        archive = RgssArchive.open(self.write(encode_v3(SAMPLE)))
        self.assertEqual(archive.read("data\\actors.RVDATA2"), SAMPLE[0][1])
        with self.assertRaises(ArchiveError):
            archive.read("Data/Nope.rvdata2")

    def test_version_3_entries_may_be_stored_out_of_order(self):
        blob = encode_v3(SAMPLE, order=lambda e: list(reversed(e)))
        archive = RgssArchive.open(self.write(blob))
        self.assertEqual(archive.read("Data/Actors.rvdata2"), SAMPLE[0][1])

    def test_keystream_is_the_documented_one(self):
        # Pins the arithmetic itself, not just self-consistency: the first
        # name-length word of a version 1 file is the length XOR 0xDEADCAFE.
        blob = encode_v1([("a", b"")])
        self.assertEqual(struct.unpack_from("<I", blob, 8)[0], 1 ^ 0xDEADCAFE)
        self.assertEqual(struct.unpack_from("<I", encode_v3([("a", b"")]), 8)[0],
                         0x12345678)

    def test_extract_writes_files_and_counts_bytes(self):
        archive = RgssArchive.open(self.write(encode_v3(SAMPLE)))
        out = self.dir / "out"
        total = archive.extract(out)
        self.assertEqual(total, sum(len(d) for _, d in SAMPLE))
        self.assertEqual((out / "Data" / "Map001.rvdata2").read_bytes(), b"m" * 11)
        self.assertEqual((out / "Audio" / "BGM" / "Theme.ogg").read_bytes(), b"")

    def test_extract_can_be_limited_to_a_folder(self):
        archive = RgssArchive.open(self.write(encode_v1(SAMPLE)))
        out = self.dir / "out"
        archive.extract(out, only="Data")
        self.assertEqual(sorted(p.name for p in out.iterdir()), ["Data"])


class TestHostile(Scratch):
    def refuse(self, blob, fragment=""):
        with self.assertRaises(ArchiveError) as caught:
            RgssArchive.open(self.write(blob))
        self.assertIn(fragment, str(caught.exception))
        self.assertIsInstance(caught.exception, ProjectError)

    def test_not_an_archive(self):
        self.refuse(b"", "RGSSAD")
        self.refuse(b"PK\x03\x04" + bytes(20), "RGSSAD")
        self.refuse(b"RGSSAD\0\x02" + bytes(20), "version")
        self.refuse(b"RGSSAD\0\x03", "truncated")

    def test_v1_truncations_at_every_length(self):
        blob = encode_v1(SAMPLE)
        # A cut exactly between two entries is a shorter, valid archive.
        clean = {len(encode_v1(SAMPLE[:n])) for n in range(len(SAMPLE) + 1)}
        for cut in range(9, len(blob)):
            if cut in clean:
                continue
            with self.subTest(cut=cut):
                with self.assertRaises(ArchiveError):
                    RgssArchive.open(self.write(blob[:cut]))

    def test_v3_truncations_at_every_length(self):
        blob = encode_v3(SAMPLE)
        for cut in range(8, len(blob)):
            with self.subTest(cut=cut):
                with self.assertRaises(ArchiveError):
                    archive = RgssArchive.open(self.write(blob[:cut]))
                    for entry in archive.entries:
                        archive.read(entry)

    def test_oversized_name_length(self):
        blob = b"RGSSAD\0\x01" + struct.pack("<I", 0x7FFFFFFF ^ 0xDEADCAFE)
        self.refuse(blob, "name")

    def test_oversized_data_length_v1(self):
        key = 0xDEADCAFE
        blob = bytearray(encode_v1([("a", b"")]))
        k1 = (key * 7 + 3) & M32
        k2 = (k1 * 7 + 3) & M32
        struct.pack_into("<I", blob, 8 + 4 + 1, 0x7FFFFFF0 ^ k2)
        self.refuse(bytes(blob), "past the end")

    def test_oversized_offset_and_size_v3(self):
        key = (0x12345678 * 9 + 3) & M32
        for field in (0, 1):
            blob = bytearray(encode_v3([("a", b"abc")]))
            struct.pack_into("<I", blob, 12 + 4 * field, 0xFFFFFFF0 ^ key)
            with self.subTest(field=field):
                self.refuse(bytes(blob), "outside")

    def test_oversized_name_length_v3(self):
        key = (0x12345678 * 9 + 3) & M32
        blob = bytearray(encode_v3([("a", b"abc")]))
        struct.pack_into("<I", blob, 12 + 12, 0xFFFFFF ^ key)
        self.refuse(bytes(blob), "name")

    def test_hostile_names(self):
        bad = ["..\\..\\x", "../x", "Data/../../x", "/etc/passwd", "\\x",
               "C:\\x", "C:x", "Data/C:evil", "", "Data//x", "Data/./x",
               "a/", "NUL", "Data/con.txt", "Data/x\x01", "Data/a:b",
               "Data/trail.", "Data/sp ", "Data/a*b", "\\\\server\\share\\x",
               "x" * 300]
        for name in bad:
            for label, encode in (("v1", encode_v1), ("v3", encode_v3)):
                with self.subTest(name=name, version=label):
                    self.refuse(encode([(name, b"z")]))

    def test_nothing_is_written_when_a_name_is_hostile(self):
        out = self.dir / "out"
        with self.assertRaises(ArchiveError):
            RgssArchive.open(self.write(
                encode_v3([("Data/ok", b"1"), ("..\\..\\escape", b"2")]))).extract(out)
        self.assertFalse(out.exists() and any(out.rglob("*")))
        self.assertFalse((self.dir.parent / "escape").exists())

    def test_duplicate_names(self):
        self.refuse(encode_v3([("Data/a", b"1"), ("data\\A", b"2")]), "twice")
        self.refuse(encode_v1([("Data/a", b"1"), ("data\\A", b"2")]), "twice")

    def test_overlapping_bodies_v3(self):
        files = [("Data/a", b"x" * 40), ("Data/b", b"y" * 40)]
        blob = bytearray(encode_v3(files))
        key = (0x12345678 * 9 + 3) & M32
        # Point the second entry's offset into the first body.
        first = struct.unpack_from("<I", blob, 12)[0] ^ key
        second_at = 12 + 16 + len(b"Data/a")
        struct.pack_into("<I", blob, second_at, (first + 4) ^ key)
        self.refuse(bytes(blob), "overlap")

    def test_body_inside_the_table_v3(self):
        blob = bytearray(encode_v3([("Data/a", b"x" * 8)]))
        key = (0x12345678 * 9 + 3) & M32
        struct.pack_into("<I", blob, 12, 12 ^ key)
        self.refuse(bytes(blob), "overlap")

    def test_entry_count_limit(self):
        files = [("Data/f%d" % i, b"") for i in range(6)]
        for encode in (encode_v1, encode_v3):
            with mock.patch.object(rgss_archive, "MAX_ENTRIES", 5):
                self.refuse(encode(files), "entries")

    def test_huge_entry_count_stops_at_the_limit(self):
        # A v3 table with no terminator and far more rows than the limit
        # must stop at the limit rather than parse until the file ends.
        key = (0x12345678 * 9 + 3) & M32
        row = b"".join(struct.pack("<I", v ^ key) for v in (999, 1, 0, 1))
        row += bytes([0x41 ^ (key & 0xFF)])
        blob = b"RGSSAD\0\x03" + struct.pack("<I", 0x12345678) + row * 200
        with mock.patch.object(rgss_archive, "MAX_ENTRIES", 50):
            self.refuse(blob, "entries")

    def test_name_length_limit(self):
        with mock.patch.object(rgss_archive, "MAX_NAME_BYTES", 8):
            self.refuse(encode_v3([("Data/toolong.rvdata2", b"")]), "name")
            self.refuse(encode_v1([("Data/toolong.rvdata2", b"")]), "name")

    def test_total_size_limit_is_checked_before_anything_is_written(self):
        files = [("Data/a", b"x" * 600), ("Data/b", b"y" * 600)]
        out = self.dir / "out"
        for encode in (encode_v1, encode_v3):
            archive = RgssArchive.open(self.write(encode(files)))
            with mock.patch.object(rgss_archive, "MAX_TOTAL_BYTES", 1000):
                with self.assertRaises(ArchiveError) as caught:
                    archive.extract(out)
            self.assertIn("total", str(caught.exception))
            self.assertFalse(out.exists() and any(out.rglob("*")))

    def test_single_entry_larger_than_the_cap_is_refused_unread(self):
        archive = RgssArchive.open(self.write(encode_v3([("Data/a", b"x" * 100)])))
        with mock.patch.object(rgss_archive, "MAX_ENTRY_BYTES", 50):
            with self.assertRaises(ArchiveError):
                archive.read("Data/a")

    def test_extract_refuses_to_follow_a_planted_link(self):
        out = self.dir / "out"
        outside = self.dir / "outside"
        outside.mkdir()
        out.mkdir()
        try:
            os.symlink(outside, out / "Data", target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available here")
        archive = RgssArchive.open(self.write(encode_v3([("Data/a", b"1")])))
        with self.assertRaises(ProjectError):
            archive.extract(out)
        self.assertEqual(list(outside.iterdir()), [])

    def test_extract_refuses_to_overwrite(self):
        out = self.dir / "out"
        (out / "Data").mkdir(parents=True)
        (out / "Data" / "a").write_bytes(b"mine")
        archive = RgssArchive.open(self.write(encode_v3([("Data/a", b"1")])))
        with self.assertRaises(ProjectError):
            archive.extract(out)
        self.assertEqual((out / "Data" / "a").read_bytes(), b"mine")

    def test_a_name_that_is_both_file_and_folder(self):
        out = self.dir / "out"
        archive = RgssArchive.open(self.write(
            encode_v3([("Data/a", b"1"), ("Data/a/b", b"2")])))
        with self.assertRaises(ProjectError):
            archive.extract(out)

    def test_a_folder_named_like_an_archive_is_not_one(self):
        (self.dir / "Game.rgss3a").mkdir()
        self.assertIsNone(rgss_archive.find_archive(self.dir))


class TestProject(Scratch):
    def make(self, engine="vx_ace", encode=encode_v3, name="Game.rgss3a",
             keep_data=False):
        game = self.dir / "game"
        build(game, engine)
        files = []
        for path in sorted(game.rglob("*")):
            if path.is_file() and path.relative_to(game).parts[0] == "Data":
                files.append((path.relative_to(game).as_posix(), path.read_bytes()))
        files.append(("Graphics/Characters/Hero.png", b"png"))
        (game / name).write_bytes(encode(files))
        if not keep_data:
            shutil.rmtree(game / "Data")
        return game

    def test_an_ace_archive_opens_read_only(self):
        game = self.make()
        project = Project(game)
        self.addCleanup(project.close)
        self.assertTrue(project.read_only)
        info = project.describe()
        self.assertTrue(info["read_only"])
        self.assertEqual(info["archive"]["file"], "Game.rgss3a")
        self.assertIn("read-only", info["archive"]["note"])
        self.assertGreater(info["map_files"], 0)
        self.assertFalse((game / "Data").exists(),
                         "extraction must not touch the project")
        self.assertFalse((game / ".rpgvxace-mcp").exists())

    def test_a_vx_archive_opens(self):
        game = self.make("vx", encode_v1, "Game.rgss2a")
        project = Project(game)
        self.addCleanup(project.close)
        self.assertEqual(project.engine, "vx")
        self.assertTrue(project.read_only)

    def test_archive_graphics_count_as_project_assets(self):
        project = Project(self.make())
        self.addCleanup(project.close)
        self.assertIn("Hero", project.asset_names("characters"))

    def test_writes_are_refused_and_say_why(self):
        project = Project(self.make())
        self.addCleanup(project.close)
        project.load_data("actors")
        with self.assertRaises(ReadOnlyError) as caught:
            project.save_file(project.filename_for("actors"))
        message = str(caught.exception)
        self.assertIn("archive", message)
        self.assertIn("would load the archive", message)
        self.assertNotIn("Restart without --read-only", message)

    def test_read_only_is_forced_on(self):
        project = Project(self.make(), read_only=False)
        self.addCleanup(project.close)
        self.assertTrue(project.read_only)

    def test_a_real_data_folder_wins_over_the_archive(self):
        game = self.make(keep_data=True)
        project = Project(game)
        self.assertFalse(project.read_only)
        self.assertIsNone(project.describe()["archive"])
        self.assertEqual(project.data_dir, (game / "Data").resolve())

    def test_a_corrupt_archive_is_a_project_error(self):
        game = self.dir / "g"
        game.mkdir()
        (game / "Game.rgss3a").write_bytes(b"junk")
        with self.assertRaises(ProjectError):
            Project(game)

    def test_an_archive_with_no_data_says_so(self):
        game = self.dir / "g"
        game.mkdir()
        (game / "Game.rgss3a").write_bytes(encode_v3([("Graphics/x.png", b"1")]))
        with self.assertRaises(ProjectError) as caught:
            Project(game)
        self.assertIn("Data", str(caught.exception))

    def test_no_archive_and_no_data_keeps_the_old_message(self):
        game = self.dir / "g"
        game.mkdir()
        with self.assertRaises(ProjectError) as caught:
            Project(game)
        self.assertIn("no Data directory", str(caught.exception))

    def test_close_removes_the_cache(self):
        project = Project(self.make())
        cache = project.data_dir
        self.assertTrue(cache.exists())
        project.close()
        self.assertFalse(cache.exists())
        project.close()  # and twice is fine

    def test_a_failed_extraction_leaves_no_cache_behind(self):
        game = self.dir / "g"
        game.mkdir()
        (game / "Game.rgss3a").write_bytes(encode_v3([("Data/a", b"1" * 100)]))
        before = set(Path(tempfile.gettempdir()).glob("rpgvxace-rgss-*"))
        with mock.patch.object(rgss_archive, "MAX_TOTAL_BYTES", 10):
            with self.assertRaises(ProjectError):
                Project(game)
        after = set(Path(tempfile.gettempdir()).glob("rpgvxace-rgss-*"))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
