"""`map_render` against the demo project, with tileset sheets painted for it.

The demo project's graphics are empty files, which is right for the checks
that only name them and wrong here, so each test writes the PNGs it needs into
the project's own Graphics folders (the workspace is restored between tests).
"""

import base64
import hashlib
import json
import os
import struct
import sys
import unittest
import zlib
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE / "fixtures"))
sys.path.insert(0, str(HERE))  # tool_case: bare-name import must work from the repo root

from rpgvxace_mcp.project import Project  # noqa: E402
from rpgvxace_mcp.render import png  # noqa: E402
from rpgvxace_mcp.tools import map_render_tools  # noqa: E402
from rpgvxace_mcp.server import MAX_TOOL_RESULT_CHARS, Server  # noqa: E402
from rpgvxace_mcp.tools.registry import (  # noqa: E402
    Context,
    ImageReply,
    ToolError,
)

from tool_case import ToolTest  # noqa: E402


def paint(path, width, height, colour_at):
    """Write a PNG whose pixel (x, y) is `colour_at(x, y)`."""
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels += bytes(colour_at(x, y))
    Path(path).write_bytes(png.encode(width, height, bytes(pixels), 3))


def quarter(x, y):
    """A sheet colouring every 16-pixel square by where it is."""
    return ((x // 16) * 8 % 256, (y // 16) * 6 % 256, 99)


_PAINTED = {}


def painted(path, width, height, colour_at):
    """`paint`, encoding each distinct sheet once for the whole run."""
    key = (path.name, width, height)
    if key not in _PAINTED:
        paint(path, width, height, colour_at)
        _PAINTED[key] = path.read_bytes()
    else:
        path.write_bytes(_PAINTED[key])


def unwrap(case, reply):
    """The JSON half of a render; an inline picture must be the file's bytes."""
    if isinstance(reply, ImageReply):
        case.assertEqual(reply.png,
                         Path(reply.body["image"]["path"]).read_bytes())
        return reply.body
    return reply


class MapRenderTest(ToolTest):
    def setUp(self):
        super().setUp()
        graphics = self.tmp / "project" / "Graphics"
        painted(graphics / "Tilesets" / "World_A2.png", 512, 576, quarter)
        painted(graphics / "Tilesets" / "World_B.png", 512, 512, quarter)
        painted(graphics / "Characters" / "People1.png", 384, 256,
                lambda x, y: (40, 200, 40))
        map_render_tools._SHEETS.clear()

    def render(self, **arguments):
        """The reply's JSON, after checking any image it carried is the file."""
        arguments.setdefault("map_id", 1)
        return unwrap(self, self.call("map_render", **arguments))

    def picture(self, reply):
        return png.decode(Path(reply["image"]["path"]).read_bytes())

    def at(self, image, x, y):
        i = (y * image.width + x) * 4
        return tuple(image.data[i:i + 3])


class TestARender(MapRenderTest):
    def test_the_picture_is_written_and_described(self):
        reply = self.render()
        path = Path(reply["image"]["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent,
                         self.tmp / "project" / ".rpgvxace-mcp" / "renders")
        self.assertEqual(reply["image"]["bytes"], path.stat().st_size)
        self.assertEqual((reply["image"]["width"], reply["image"]["height"]),
                         (640, 480))
        self.assertEqual(reply["window"],
                         {"x": 0, "y": 0, "width": 20, "height": 15})
        self.assertEqual(reply["tileset"]["id"], 1)
        self.assertEqual(reply["tileset"]["name"], "Field")
        image = self.picture(reply)
        self.assertEqual((image.width, image.height), (640, 480))

    def test_tiles_come_from_the_tilesets_own_sheets(self):
        image = self.picture(self.render())
        # Layer 0 is A2 kind 16, shape 0: every quarter from block (0, 0).
        self.assertEqual(self.at(image, 8 * 32 + 8, 8 * 32 + 8),
                         quarter(2 * 16, 4 * 16))
        self.assertEqual(self.at(image, 8 * 32 + 24, 8 * 32 + 24),
                         quarter(1 * 16, 3 * 16))
        # Layer 1 puts B tile 20 along the top row: column 4, row 2 of B.
        self.assertEqual(self.at(image, 3 * 32 + 4, 4), quarter(4 * 32, 2 * 32))

    def test_the_counts_say_what_was_drawn(self):
        reply = self.render()
        self.assertEqual(reply["drawn"]["tiles"], 20 * 15 + 20)
        self.assertEqual(reply["drawn"]["event_graphics"], 1)
        self.assertEqual(reply["drawn"]["event_markers"], 0)
        self.assertNotIn("sheets_not_read", reply)
        self.assertNotIn("tiles_note", reply)

    def test_an_event_is_drawn_as_its_first_page_graphic(self):
        image = self.picture(self.render())
        # The Elder stands at (5, 4): inside his tile, above the bottom edge.
        self.assertEqual(self.at(image, 5 * 32 + 16, 4 * 32 + 20), (40, 200, 40))
        # The cave door has no graphic and is not drawn.
        self.assertNotEqual(self.at(image, 8 * 32 + 16, 2 * 32 + 16),
                            (40, 200, 40))

    def test_it_says_it_wrote_a_cache_and_not_the_project(self):
        before = self.data_bytes("Map001")
        reply = self.render()
        self.assertIn("not a project edit", reply["render_note"])
        self.assertEqual(self.data_bytes("Map001"), before)

    def test_the_same_render_is_the_same_file(self):
        first = self.render()["image"]["path"]
        second = self.render()["image"]["path"]
        self.assertEqual(first, second)
        digest = hashlib.sha1(Path(first).read_bytes()).hexdigest()[:10]
        self.assertTrue(first.endswith("%s.png" % digest))

    def test_a_changed_map_is_another_file(self):
        first = self.render()["image"]["path"]
        self.project.load_map(1).get("data").set(0, 14, 0, 0)
        self.assertNotEqual(self.render()["image"]["path"], first)

    def test_an_edited_sheet_is_read_again(self):
        first = self.picture(self.render())
        paint(self.tmp / "project" / "Graphics" / "Tilesets" / "World_A2.png",
              512, 576, lambda x, y: (250, 1, 2))
        os.utime(self.tmp / "project" / "Graphics" / "Tilesets"
                 / "World_A2.png", ns=(1, 2))
        second = self.picture(self.render())
        self.assertNotEqual(self.at(first, 300, 300), (250, 1, 2))
        self.assertEqual(self.at(second, 300, 300), (250, 1, 2))


class TestThePictureComesBackAsAnImage(MapRenderTest):
    """A client that can show an image gets one; the path stays for the rest."""

    def call_over_the_server(self, **arguments):
        server = Server(self.project)
        server.initialized = True
        return server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "map_render",
                       "arguments": dict({"map_id": 1}, **arguments)}})["result"]

    def test_the_tool_returns_the_png_beside_its_path(self):
        reply = self.call("map_render", map_id=1)
        self.assertIsInstance(reply, ImageReply)
        path = Path(reply.body["image"]["path"])
        self.assertEqual(reply.png, path.read_bytes())
        self.assertIs(reply.body["image"]["inline"], True)
        image = png.decode(reply.png)
        self.assertEqual((image.width, image.height),
                         (reply.body["image"]["width"],
                          reply.body["image"]["height"]))

    def test_the_server_sends_an_image_block_after_the_text(self):
        result = self.call_over_the_server()
        self.assertFalse(result["isError"])
        text, image = result["content"]
        body = json.loads(text["text"])
        self.assertEqual(image["type"], "image")
        self.assertEqual(image["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(image["data"]),
                         Path(body["image"]["path"]).read_bytes())

    def test_a_png_over_the_inline_cap_comes_back_as_a_path_only(self):
        with mock.patch.object(map_render_tools, "MAX_INLINE_IMAGE_BYTES", 64):
            reply = self.call("map_render", map_id=1)
            result = self.call_over_the_server()
        self.assertIsInstance(reply, dict)
        self.assertIs(reply["image"]["inline"], False)
        self.assertTrue(Path(reply["image"]["path"]).is_file())
        self.assertRegex(reply["image_note"], r"(?<![0-9,])64(?![0-9,])")
        self.assertIn(reply["image"]["path"], reply["image_note"])
        self.assertFalse(result["isError"])
        self.assertEqual([block["type"] for block in result["content"]],
                         ["text"])

    def test_the_largest_inline_picture_fits_a_reply(self):
        """The server refuses a reply whose text and image pass its budget,
        so an inline cap above that budget would turn a render that worked
        into an error. Base64 is four characters for three bytes, and the
        JSON beside it is given a generous allowance."""
        inline = -(-map_render_tools.MAX_INLINE_IMAGE_BYTES // 3) * 4
        self.assertLessEqual(inline + 256 * 1024, MAX_TOOL_RESULT_CHARS)

    def test_the_cap_is_advertised(self):
        from rpgvxace_mcp.tools.registry import REGISTRY
        self.assertIn("{:,}".format(map_render_tools.MAX_INLINE_IMAGE_BYTES),
                      REGISTRY["map_render"].description)
        self.assertNotIn("cannot return an image",
                         REGISTRY["map_render"].description)


class TestWindowAndScale(MapRenderTest):
    def test_a_window_is_cropped_and_scaled(self):
        reply = self.render(x=2, y=1, width=3, height=2, tile_size=16)
        self.assertEqual((reply["image"]["width"], reply["image"]["height"]),
                         (48, 32))
        self.assertEqual(reply["tile_size"], 16)
        self.assertEqual(reply["window"],
                         {"x": 2, "y": 1, "width": 3, "height": 2})
        self.assertEqual(reply["drawn"]["tiles"], 6)

    def test_a_larger_tile_size_scales_up(self):
        reply = self.render(width=2, height=2, tile_size=64)
        image = self.picture(reply)
        self.assertEqual((image.width, image.height), (128, 128))
        self.assertEqual(self.at(image, 0, 0), self.at(image, 1, 1))

    def test_the_window_must_be_on_the_map(self):
        for arguments in ({"x": 20}, {"y": 15}, {"x": 19, "width": 2},
                          {"y": 14, "height": 2}, {"width": 21}):
            with self.subTest(arguments), self.assertRaises(ToolError):
                self.render(**arguments)

    def test_arguments_are_held_to_their_declared_bounds(self):
        for arguments in ({"tile_size": 3}, {"tile_size": 65}, {"x": -1},
                          {"width": 0}, {"map_id": 0}, {"events": "all"},
                          {"grid": "yes"}, {"colour": True}):
            with self.subTest(arguments), self.assertRaises(ToolError):
                self.render(**arguments)

    def test_a_picture_over_the_output_ceiling_is_refused(self):
        with mock.patch.object(map_render_tools, "MAX_OUTPUT_PIXELS", 1000):
            with self.assertRaises(ToolError) as caught:
                self.render()
        self.assertIn("1000", str(caught.exception))
        self.assertIn("tile_size", str(caught.exception))

    def test_a_window_over_the_native_ceiling_is_refused_before_drawing(self):
        with mock.patch.object(map_render_tools, "MAX_NATIVE_PIXELS", 1000):
            with mock.patch.object(map_render_tools, "render_map") as drawn:
                with self.assertRaises(ToolError):
                    self.render(tile_size=4)
        drawn.assert_not_called()

    def test_a_window_inside_the_ceilings_is_not_refused(self):
        self.render(width=3, height=3)


class TestWhatCannotBeDrawn(MapRenderTest):
    def test_an_unreadable_sheet_is_named_and_its_tiles_skipped(self):
        (self.tmp / "project" / "Graphics" / "Tilesets"
         / "World_B.png").write_bytes(b"")
        reply = self.render()
        self.assertIn("B", reply["sheets_not_read"])
        self.assertIn("World_B", reply["sheets_not_read"]["B"])
        self.assertIn("B 20", reply["tiles_note"])
        self.assertEqual(reply["drawn"]["tiles"], 20 * 15)

    def test_a_sheet_the_tileset_does_not_name_is_reported(self):
        tileset = self.project.get_entry("tilesets", 1)
        tileset.get("tileset_names")[1] = self.project.codec.make_string("")
        reply = self.render()
        self.assertIn("A2", reply["sheets_not_read"])
        self.assertEqual(reply["drawn"]["tiles"], 20)

    def test_a_sheet_that_is_a_png_bomb_is_refused_by_name(self):
        header = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)

        def chunk(kind, payload):
            return (struct.pack(">I", len(payload)) + kind + payload
                    + struct.pack(">I", zlib.crc32(kind + payload)))
        bomb = (png.SIGNATURE + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(bytes(40 << 20), 9))
                + chunk(b"IEND", b""))
        (self.tmp / "project" / "Graphics" / "Tilesets"
         / "World_B.png").write_bytes(bomb)
        reply = self.render()
        self.assertIn("bomb", reply["sheets_not_read"]["B"])

    def test_a_sheet_that_is_not_a_png_says_so(self):
        folder = self.tmp / "project" / "Graphics" / "Tilesets"
        (folder / "World_B.png").unlink()
        (folder / "World_B.jpg").write_bytes(b"\xff\xd8\xff")
        reply = self.render()
        self.assertIn("not a PNG", reply["sheets_not_read"]["B"])

    def test_a_missing_character_graphic_becomes_a_marker(self):
        (self.tmp / "project" / "Graphics" / "Characters"
         / "People1.png").unlink()
        reply = self.render()
        self.assertEqual(reply["drawn"]["event_graphics"], 0)
        self.assertEqual(reply["drawn"]["event_markers"], 1)
        self.assertIn("1 (graphic \"People1\" could not be loaded)",
                      reply["events_note"])
        image = self.picture(reply)
        self.assertNotEqual(self.at(image, 5 * 32 + 16, 4 * 32 + 16),
                            (40, 200, 40))

    def test_a_tileset_that_does_not_exist_is_refused(self):
        self.project.load_map(1).set("tileset_id", 77)
        with self.assertRaises(ToolError) as caught:
            self.render()
        self.assertIn("77", str(caught.exception))

    def test_a_non_table_map_is_refused(self):
        self.project.load_map(1).set("data", 5)
        with self.assertRaises(ToolError):
            self.render()

    def test_a_hostile_sheet_name_is_just_missing(self):
        tileset = self.project.get_entry("tilesets", 1)
        tileset.get("tileset_names")[5] = self.project.codec.make_string(
            "..\\..\\Data\\Map001")
        reply = self.render()
        self.assertIn("no file named", reply["sheets_not_read"]["B"])

    def test_events_beyond_the_ceiling_are_reported(self):
        with mock.patch.object(map_render_tools, "MAX_RENDER_EVENTS", 1):
            reply = self.render()
        self.assertTrue(reply["events_truncated"])
        self.assertIn("first 1 events", reply["events_note"])


class TestOptions(MapRenderTest):
    def test_events_can_be_boxed_or_left_out(self):
        marked = self.picture(self.render(events="markers"))
        none = self.picture(self.render(events="none"))
        self.assertNotEqual(self.at(marked, 5 * 32 + 16, 4 * 32 + 20),
                            (40, 200, 40))
        self.assertNotEqual(self.at(marked, 8 * 32 + 16, 2 * 32 + 16),
                            self.at(none, 8 * 32 + 16, 2 * 32 + 16))
        self.assertEqual(self.render(events="none")["drawn"]["event_graphics"], 0)

    def test_regions_shadows_and_the_grid_change_the_picture(self):
        data = self.project.load_map(1).get("data")
        data.set(3, 3, 3, (5 << 8) | 15)
        base = self.picture(self.render(shadows=False))
        shadowed = self.picture(self.render())
        regions = self.picture(self.render(shadows=False, regions=True))
        grid = self.picture(self.render(shadows=False, grid=True))
        self.assertNotEqual(base.data, shadowed.data)
        self.assertNotEqual(base.data, regions.data)
        self.assertNotEqual(base.data, grid.data)

    def test_the_render_folder_keeps_only_the_newest(self):
        with mock.patch.object(map_render_tools, "MAX_KEPT_RENDERS", 2):
            for column in range(4):
                self.project.load_map(1).get("data").set(column, 14, 0, 0)
                self.render()
        folder = self.tmp / "project" / ".rpgvxace-mcp" / "renders"
        self.assertEqual(len(list(folder.glob("map-*.png"))), 2)
        self.assertEqual(list(folder.glob("*.tmp")), [])

    def test_the_render_folder_can_be_somewhere_else(self):
        elsewhere = self.tmp / "elsewhere"
        project = Project(self.tmp / "project", render_dir=elsewhere)
        reply = self.call_with(project)
        self.assertEqual(Path(reply["image"]["path"]).parent, elsewhere)
        self.assertFalse((self.tmp / "project" / ".rpgvxace-mcp"
                          / "renders").exists())

    def call_with(self, project):
        from rpgvxace_mcp.tools.registry import REGISTRY
        return unwrap(self, REGISTRY["map_render"].run(Context(project),
                                                       {"map_id": 1}))


class TestReadOnly(MapRenderTest):
    read_only = True

    def test_a_render_is_still_written_and_the_project_is_untouched(self):
        data = {p: p.read_bytes() for p in
                (self.tmp / "project" / "Data").iterdir()}
        reply = self.render()
        self.assertTrue(Path(reply["image"]["path"]).is_file())
        self.assertEqual({p: p.read_bytes() for p in
                          (self.tmp / "project" / "Data").iterdir()}, data)


class TestVX(MapRenderTest):
    engine = "vx"

    def test_a_vx_project_is_refused_with_the_reason(self):
        with self.assertRaises(ToolError) as caught:
            self.render()
        self.assertIn("VX", str(caught.exception))
        self.assertIn("map_tiles", str(caught.exception))

    def setUp(self):
        # The sheets this suite paints are Ace's; a VX project has none.
        ToolTest.setUp(self)


class TestTheCommandLine(unittest.TestCase):
    def test_render_dir_is_an_option_and_defaults_to_none(self):
        from rpgvxace_mcp.__main__ import build_parser
        parser = build_parser()
        self.assertIsNone(parser.parse_args(["proj"]).render_dir)
        self.assertEqual(
            parser.parse_args(["proj", "--render-dir", "out"]).render_dir,
            "out")


@unittest.skipUnless(os.environ.get("RPGVXACE_TEST_PROJECT"),
                     "set RPGVXACE_TEST_PROJECT to a VX Ace project")
class TestRealProject(unittest.TestCase):
    """Every map of a real game renders, with every sheet found and read."""

    def test_every_map_renders_and_nothing_is_missing(self):
        import tempfile
        from rpgvxace_mcp.tools.registry import REGISTRY
        with tempfile.TemporaryDirectory() as out:
            project = Project(os.environ["RPGVXACE_TEST_PROJECT"],
                              read_only=True, render_dir=out)
            ctx = Context(project)
            before = {p: p.stat().st_mtime_ns
                      for p in project.data_dir.iterdir()}
            for map_id in project.map_ids_on_disk():
                with self.subTest(map_id=map_id):
                    reply = unwrap(self, REGISTRY["map_render"].run(
                        ctx, {"map_id": map_id}))
                    self.assertNotIn("sheets_not_read", reply)
                    image = png.decode(Path(reply["image"]["path"]).read_bytes())
                    self.assertEqual(image.width, reply["image"]["width"])
            self.assertEqual({p: p.stat().st_mtime_ns
                              for p in project.data_dir.iterdir()}, before)
            self.assertFalse((project.root / ".rpgvxace-mcp" / "renders")
                             .exists() and any(
                                 (project.root / ".rpgvxace-mcp" / "renders")
                                 .glob("map-*.png")))


class TestFindingAnAsset(ToolTest):
    def test_a_name_is_matched_without_regard_to_case(self):
        folder = self.tmp / "project" / "Graphics" / "Characters"
        (folder / "hero.PNG").write_bytes(b"")
        found = self.project.asset_file("characters", "Hero")
        self.assertEqual(found.name, "hero.PNG")

    def test_the_project_beats_the_rtp_and_png_beats_the_rest(self):
        rtp = self.tmp / "rtp"
        (rtp / "Graphics" / "Characters").mkdir(parents=True)
        (rtp / "Graphics" / "Characters" / "Actor1.png").write_bytes(b"rtp")
        (rtp / "Graphics" / "Characters" / "Only.png").write_bytes(b"rtp")
        project = Project(self.tmp / "project", rtp_paths=[str(rtp)])
        self.assertEqual(project.asset_file("characters", "Actor1").parent,
                         self.tmp / "project" / "Graphics" / "Characters")
        self.assertEqual(project.asset_file("characters", "Only").parent,
                         rtp / "Graphics" / "Characters")
        folder = self.tmp / "project" / "Graphics" / "Characters"
        (folder / "Both.jpg").write_bytes(b"")
        (folder / "Both.png").write_bytes(b"")
        fresh = Project(self.tmp / "project")
        self.assertEqual(fresh.asset_file("characters", "Both").suffix, ".png")

    def test_a_name_that_is_not_a_file_name_is_not_found(self):
        for name in ("", "..", "../Actor1", "..\\Actor1", "a/b", "C:Actor1",
                     "Actor1\x00", ".hidden", "Actor1 ", "x" * 300, "a*b",
                     None, 5):
            with self.subTest(name=name):
                self.assertIsNone(self.project.asset_file("characters", name))

    def test_special_prefixes_are_ordinary_characters(self):
        found = self.project.asset_file("characters", "!Chest")
        self.assertEqual(found.name, "!Chest.png")

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(Exception):
            self.project.asset_file("nonsense", "x")


if __name__ == "__main__":
    unittest.main()
