"""Seeing a map: `map_render` draws one as a PNG for a caller that can look.

The drawing itself is in `rpgvxace_mcp.render`, which knows nothing about
projects. This half reads the project -- the map, its tileset, the graphics
those name -- bounds everything a crafted file could inflate, and writes the
picture.

The picture comes back as an MCP image block after the JSON when it is small
enough to ride in a reply, and always goes to a file whose path the reply
carries, for a client that shows no images. That file is a render cache, not a project edit: it lands in
`.rpgvxace-mcp/renders/` (or `--render-dir`), it is written with `--read-only`
in force, and `map_render` says so rather than leaving a caller to find out.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from ..project import MAX_MAP_ID, _contained
from ..render import png
from ..render.canvas import resize_nearest, to_rgb
from ..render.scene import Sprite, TileGrid, render_map
from ..render.tiles import SHEET_NAMES, TILE, sheet_needed
from ..rubytypes import RubyHash, RubyObject, Table, as_list
from ..schema import TILE_LAYER_COUNT, on_map
from .map_common import _check_budget
from .registry import (
    BOOL,
    Context,
    INT,
    ImageReply,
    REFRESH,
    STR,
    ToolError,
    decoded,
    need_int,
    one_of,
    opt_bool,
    opt_int,
    opt_int_default,
    partial,
    prop,
    schema,
    tool,
)


# Pixels the picture may have before it is scaled to `tile_size`: 32 a tile,
# so about 11,700 tiles -- a 108 x 108 map. The canvas is a four-byte-a-pixel
# bytearray, and the scaled copy is a second one.
MAX_NATIVE_PIXELS = 12_000_000


# Pixels in the PNG that is written. A viewer that can look at a picture can
# rarely use one much larger than this, and the encoder is a Python loop.
MAX_OUTPUT_PIXELS = 4_194_304


# The largest PNG a reply also carries inline, as an MCP image block. The
# server refuses a reply whose text and base64 image pass its own budget, so
# this sits well under it (base64 is four characters for three bytes) and a
# render that worked is never turned into an error by its size; a larger
# picture is still written and its path returned. The same as the live
# screenshot's ceiling, so one size of picture comes back the same way.
MAX_INLINE_IMAGE_BYTES = 1_000_000


TILE_SIZES = (4, 64)
RENDER_EVENTS = ("graphics", "markers", "none")


# How many events one render looks at. Events times nothing here, but the count
# comes out of the file and each one is converted before it is drawn.
MAX_RENDER_EVENTS = 2000


# How many different character sheets one render opens. Each is a PNG decode
# in Python of up to `png.MAX_PNG_PIXELS`; a map has a handful.
_MAX_CHARACTER_SHEETS = 32


# How many pictures `.rpgvxace-mcp/renders/` keeps; the oldest go first. A
# render's name is a hash of its bytes, so an unchanged map re-renders to the
# file it already has and only a changed one adds to the folder.
MAX_KEPT_RENDERS = 40


# Decoded sheets, shared by every render this process makes. Keyed by file,
# modification time and size so an edited tileset is not served stale. A
# tileset is nine sheets of at most a few megabytes decoded.
MAX_CACHED_SHEETS = 24
_SHEETS: "OrderedDict[tuple[str, int, int], png.Image]" = OrderedDict()


def _read_sheet(path: Path) -> png.Image:
    """One decoded PNG, from the cache when the file has not changed."""
    info = path.stat()
    if info.st_size > png.MAX_PNG_BYTES:
        raise png.PngError("the file is %d bytes, above the %d this reader "
                           "opens" % (info.st_size, png.MAX_PNG_BYTES))
    key = (str(path), info.st_mtime_ns, info.st_size)
    cached = _SHEETS.get(key)
    if cached is not None:
        _SHEETS.move_to_end(key)
        return cached
    image = png.decode(path.read_bytes())
    _SHEETS[key] = image
    while len(_SHEETS) > MAX_CACHED_SHEETS:
        _SHEETS.popitem(last=False)
    return image


def _load_graphic(ctx: Context, kind: str, name: str) -> png.Image:
    """A PNG from the project or its RTP, or an error saying why not."""
    path = ctx.project.asset_file(kind, name)
    if path is None:
        raise ToolError("no file named \"%s\" in Graphics/%s or any RTP"
                        % (name, kind.capitalize()))
    if path.suffix.lower() != ".png":
        raise ToolError("%s is not a PNG, which is the only format this "
                        "renderer reads" % path.name)
    try:
        return _read_sheet(path)
    except (png.PngError, OSError) as exc:
        raise ToolError("%s: %s" % (path.name, exc)) from exc


def _tileset_of(ctx: Context, game_map: RubyObject, refresh: bool
                ) -> tuple[int, RubyObject]:
    tileset_id = game_map.get("tileset_id")
    if isinstance(tileset_id, bool) or not isinstance(tileset_id, int):
        raise ToolError("this map's tileset_id is not a number, so there is "
                        "no tileset to draw it with")
    entry = ctx.project.get_entry("tilesets", tileset_id, refresh)
    if not isinstance(entry, RubyObject):
        raise ToolError("this map uses tileset %d, which Tilesets does not "
                        "have, so its tiles have no sheets to be cut from"
                        % tileset_id)
    return tileset_id, entry


def _window(args: dict[str, Any], width: int, height: int
            ) -> tuple[int, int, int, int]:
    """The ``(x, y, w, h)`` to draw, inside the map or refused."""
    x = opt_int_default(args, "x", 0, minimum=0)
    y = opt_int_default(args, "y", 0, minimum=0)
    if not on_map(x, y, width, height):
        raise ToolError("x=%d y=%d is outside this %d x %d map"
                        % (x, y, width, height))
    w = opt_int(args, "width", width - x, minimum=1) or width - x
    h = opt_int(args, "height", height - y, minimum=1) or height - y
    if x + w > width or y + h > height:
        raise ToolError(
            "the window x=%d y=%d width=%d height=%d runs past the edge of "
            "this %d x %d map" % (x, y, w, h, width, height))
    return x, y, w, h


def _sprites(ctx: Context, game_map: RubyObject, window: tuple[int, int, int, int]
             ) -> tuple[list[Sprite], bool]:
    """Page 1 of each event as a sprite, and whether the walk was cut short."""
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        return [], False
    wx, wy, ww, wh = window
    out: list[Sprite] = []
    for examined, (event_id, event) in enumerate(events.sorted_items()):
        if examined >= MAX_RENDER_EVENTS:
            return out, True
        x, y = (event.get("x"), event.get("y")) if isinstance(event, RubyObject) \
            else (None, None)
        if (not isinstance(event_id, int) or isinstance(event_id, bool)
                or not isinstance(x, int) or not isinstance(y, int)
                or not (wx - 3 <= x < wx + ww + 3 and wy - 1 <= y < wy + wh + 4)):
            continue
        pages = as_list(event.get("pages"))
        page = pages[0] if pages else None
        graphic = page.get("graphic") if isinstance(page, RubyObject) else None
        sprite = Sprite(event_id, x, y)
        if isinstance(graphic, RubyObject):
            numbers = {}
            for field in ("character_index", "direction", "pattern", "tile_id"):
                value = graphic.get(field)
                numbers[field] = value if isinstance(value, int) \
                    and not isinstance(value, bool) else 0
            sprite.name = decoded(ctx, graphic.get("character_name"))
            sprite.index = min(max(numbers["character_index"], 0), 7)
            sprite.direction = numbers["direction"]
            sprite.pattern = numbers["pattern"]
            sprite.tile_id = max(numbers["tile_id"], 0)
        out.append(sprite)
    return out, False


def _ids_in(grid: TileGrid, window: tuple[int, int, int, int]) -> set[int]:
    """Every tile id the three tile layers hold inside the window."""
    wx, wy, ww, wh = window
    found: set[int] = set()
    plane = grid.width * grid.height
    for z in range(min(TILE_LAYER_COUNT, grid.depth)):
        for y in range(wy, wy + wh):
            start = z * plane + y * grid.width + wx
            found.update(grid.cells[start:start + ww])
    found.discard(0)
    return found


# The names `_write_picture` makes: `?` in the glob matches any character, so
# a person's `map-0001-holidaypic.png` in a shared folder looked like ours.
_OWN_PICTURE = re.compile(r"map-[0-9]{4}-[0-9a-f]{10}\.png")


def _write_picture(ctx: Context, data: bytes, map_id: int) -> Path:
    """Put the PNG in the render folder, and trim the folder to its ceiling."""
    folder = ctx.project.render_dir()
    # Checked before it is made: a link planted as the work folder carried
    # the `mkdir` outside the project before the check below refused.
    if ctx.project.render_dir_override is None:
        _contained(folder, ctx.project.root, "the render folder")
    folder.mkdir(parents=True, exist_ok=True)
    name = "map-%04d-%s.png" % (map_id, hashlib.sha1(data).hexdigest()[:10])
    target = folder / name
    # A render folder inside the project is held to the project, so a link
    # planted there cannot carry the write somewhere else.
    if ctx.project.render_dir_override is None:
        _contained(target, ctx.project.root, "the render file")
    # A temp file made new, under a name of its own. `<picture>.tmp` was a
    # name an attacker could predict (the picture is named by a hash of its
    # bytes), and `write_bytes` follows a link planted there.
    descriptor, temporary_name = tempfile.mkstemp(
        dir=folder, prefix=name + ".", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, target)
    except BaseException:
        # The trim below never matches a `.tmp`, so one left here stays.
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    # Only names this function writes: `map-*.png` also took the pictures a
    # person kept in a shared `--render-dir`.
    kept = sorted((p for p in folder.glob("map-????-??????????.png")
                   if p.is_file() and _OWN_PICTURE.fullmatch(p.name)),
                  key=lambda p: p.stat().st_mtime_ns, reverse=True)
    for stale in kept[MAX_KEPT_RENDERS:]:
        try:
            stale.unlink()
        except OSError:
            pass
    return target


@tool(
    "map_render",
    "Render a map to a PNG",
    "Draw a VX Ace map as a picture you can look at: the tile layers with "
    "autotiles joined as the editor stored them, the shadows (the low bits of "
    "layer 3), and every "
    "event as its first page's graphic (a coloured box with the event id "
    "where the graphic is missing). Tilesets and characters come from the "
    "project and its RTP. The PNG comes back as an image after the JSON "
    "reply when it is at most %s bytes, and is always written to a file "
    "whose path the reply gives (image.inline says which) -- read that file "
    "if your client shows no image, or if the picture was larger. The file "
    "goes in .rpgvxace-mcp/renders/ inside the project (or "
    "--render-dir), which keeps the %d newest; it is a render cache, not a "
    "project edit, so it is written even under --read-only, and nothing in "
    "the map or its data changes. Crop with x/y/width/height (tiles) and "
    "scale with tile_size (pixels per tile, default %d). A window may cover "
    "at most %s pixels at the game's own size and the picture at most %s "
    "pixels. "
    "Graphics must be 8-bit PNGs without interlacing, of at most %s bytes, "
    "%d pixels on a side and %s pixels; any other is left out and named in "
    "sheets_not_read. regions overlays the region ids from layer 3, grid draws "
    "cell lines with coordinates every fifth cell. Not drawn: animation, "
    "parallax, weather, tint, counter-table fronts, later event pages. VX "
    "maps are refused."
    % ("{:,}".format(MAX_INLINE_IMAGE_BYTES), MAX_KEPT_RENDERS, TILE, "{:,}".format(MAX_NATIVE_PIXELS),
       "{:,}".format(MAX_OUTPUT_PIXELS), "{:,}".format(png.MAX_PNG_BYTES),
       png.MAX_PNG_SIDE, "{:,}".format(png.MAX_PNG_PIXELS)),
    schema({
        "map_id": prop(INT, "Which map to draw.", minimum=1, maximum=MAX_MAP_ID),
        "x": prop(INT, "Left edge of the window, in tiles (default 0).",
                  minimum=0, maximum=MAX_MAP_ID),
        "y": prop(INT, "Top edge of the window, in tiles (default 0).",
                  minimum=0, maximum=MAX_MAP_ID),
        "width": prop(INT, "Window width in tiles (default: to the map's right "
                           "edge).", minimum=1, maximum=MAX_MAP_ID),
        "height": prop(INT, "Window height in tiles (default: to the map's "
                            "bottom edge).", minimum=1, maximum=MAX_MAP_ID),
        "tile_size": prop(INT, "Pixels per tile in the picture, %d to %d "
                               "(default %d, the game's own size). Smaller "
                               "shows more map; the event ids and grid "
                               "numbers shrink with it."
                          % (TILE_SIZES[0], TILE_SIZES[1], TILE),
                          minimum=TILE_SIZES[0], maximum=TILE_SIZES[1]),
        "events": prop(STR, "graphics (default): each event as its page-1 "
                            "picture; markers: every event as a numbered "
                            "box, useful to find invisible ones; none.",
                       enum=list(RENDER_EVENTS)),
        "shadows": prop(BOOL, "Draw the shadows layer 3 holds (default true)."),
        "regions": prop(BOOL, "Overlay the region ids layer 3 holds (default "
                                "false)."),
        "grid": prop(BOOL, "Draw cell lines with coordinates (default false)."),
        "refresh": REFRESH,
    }, ["map_id"]),
)
def map_render(ctx: Context, args: dict[str, Any]) -> "dict[str, Any] | ImageReply":
    """One map as a PNG file, and inline when it fits; writes only the render."""
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    refresh = opt_bool(args, "refresh")
    tile_size = opt_int_default(args, "tile_size", TILE, TILE_SIZES[0],
                                TILE_SIZES[1])
    events = one_of(args, "events", list(RENDER_EVENTS), RENDER_EVENTS[0])
    project = ctx.project
    if project.profile.tileset_layout != "ace":
        raise ToolError(
            "map_render draws VX Ace maps, and this is an %s project. VX "
            "(RGSS2) has one global tileset in Graphics/System with a "
            "different autotile layout; XP (RGSS1) has one tileset graphic and "
            "seven autotiles whose 48 patterns its built-in Tilemap chooses, "
            "and draws by a per-tile priority. This renderer models neither, "
            "so drawing one would be a guess. Use map_tiles to read the tile "
            "ids instead." % project.profile.label)

    game_map = project.load_map(map_id, refresh)
    data = game_map.get("data")
    if not isinstance(data, Table):
        raise ToolError(
            "this map's tile data is a %s rather than a Table, so there is "
            "nothing to draw. Run validate to see what is wrong with the "
            "file." % type(data).__name__)
    _check_budget(data.xsize * data.ysize * data.zsize, "draw", windowed=False,
                  remedy="A map this large has to be rendered in the editor.")
    window = _window(args, data.xsize, data.ysize)
    wx, wy, ww, wh = window
    if ww * wh * TILE * TILE > MAX_NATIVE_PIXELS:
        raise ToolError(
            "a %d x %d tile window is %d pixels at the game's own size, above "
            "the %d this renderer works at. Ask for a smaller width/height "
            "and render the map in pieces." % (ww, wh, ww * wh * TILE * TILE,
                                               MAX_NATIVE_PIXELS))
    out_w, out_h = ww * tile_size, wh * tile_size
    if out_w * out_h > MAX_OUTPUT_PIXELS:
        raise ToolError(
            "that picture would be %d x %d = %d pixels, above the %d this "
            "tool writes. Lower tile_size or narrow the window."
            % (out_w, out_h, out_w * out_h, MAX_OUTPUT_PIXELS))

    tileset_id, tileset = _tileset_of(ctx, game_map, refresh)
    flags_table = tileset.get("flags")
    flags = flags_table.data if isinstance(flags_table, Table) else ()
    grid = TileGrid(data.xsize, data.ysize, data.zsize, data.data)
    sprites, cut_short = ([], False) if events == "none" else \
        _sprites(ctx, game_map, window)

    # Only the sheets the window actually uses are opened: a decode costs
    # more than the drawing, and most maps use five of the nine.
    wanted = {sheet_needed(t) for t in _ids_in(grid, window)}
    if events == "graphics":
        # A marker draws no graphic, so it needs no sheet; decoding one here
        # also reported sheets "not read" that nothing drew.
        wanted.update(sheet_needed(s.tile_id) for s in sprites if s.tile_id)
    wanted.discard(None)
    names = as_list(tileset.get("tileset_names"))
    sheets: dict[str, png.Image | None] = {}
    unread: dict[str, str] = {}
    for index, sheet_name in enumerate(SHEET_NAMES):
        if sheet_name not in wanted:
            continue
        file_name = decoded(ctx, names[index]) if index < len(names) else ""
        sheets[sheet_name] = None
        if not file_name:
            unread[sheet_name] = "the tileset names no file for this sheet"
            continue
        try:
            sheets[sheet_name] = _load_graphic(ctx, "tilesets", file_name)
        except ToolError as exc:
            unread[sheet_name] = "%s: %s" % (file_name, exc)

    loaded: dict[str, png.Image | None] = {}
    left_out: set[str] = set()

    def character(name: str) -> png.Image | None:
        if name not in loaded:
            if len(loaded) >= _MAX_CHARACTER_SHEETS:
                # 2,000 events can each name a different sheet, and each is a
                # decode of up to MAX_PNG_PIXELS; the rest are markers.
                left_out.add(name)
                return None
            try:
                loaded[name] = _load_graphic(ctx, "characters", name)
            except ToolError:
                loaded[name] = None
        return loaded[name]

    result = render_map(
        grid, flags, sheets, window, sprites=sprites, sprite_mode=events,
        load_character=character, shadows=opt_bool(args, "shadows", True),
        regions=opt_bool(args, "regions"), show_grid=opt_bool(args, "grid"))
    picture = resize_nearest(result.image, out_w, out_h)
    encoded = png.encode(out_w, out_h, to_rgb(picture), 3)
    path = _write_picture(ctx, encoded, map_id)
    inline = len(encoded) <= MAX_INLINE_IMAGE_BYTES

    reply: dict[str, Any] = {
        "map_id": map_id,
        "image": {"path": str(path), "width": out_w, "height": out_h,
                  "bytes": path.stat().st_size, "inline": inline},
        "window": {"x": wx, "y": wy, "width": ww, "height": wh},
        "tile_size": tile_size,
        "tileset": {"id": tileset_id,
                    "name": decoded(ctx, tileset.get("name")),
                    "mode": tileset.get("mode")},
        "drawn": {"tiles": result.tiles_drawn,
                  "event_graphics": result.sprites_drawn,
                  "event_markers": len(result.sprites_marked)
                  if events == "graphics" else 0},
    }
    reply.update(partial("render", notes=(
        "Wrote a render cache file; this is not a project edit, so it is "
        "done under --read-only too and no project data changed.",)))
    reply.update(partial("sheets", not_read=unread))
    reply.update(partial("tiles", notes=(
        ("not drawn, by sheet: %s. Their cells show the background."
         % ", ".join("%s %d" % item
                     for item in sorted(result.tiles_not_drawn.items())))
        if result.tiles_not_drawn else "",)))
    reply.update(partial("events", truncated=cut_short, notes=(
        ("only the first %d events were considered" % MAX_RENDER_EVENTS)
        if cut_short else "",
        ("only the first %d different character sheets were opened; %d "
         "more were drawn as markers" % (_MAX_CHARACTER_SHEETS, len(left_out)))
        if left_out else "",
        ("event graphics replaced by a marker: %s" % "; ".join(
            "%d (%s)" % item for item in result.sprites_marked[:20])
         + (" ..." if len(result.sprites_marked) > 20 else ""))
        if result.sprites_marked and events == "graphics" else "")))
    if not inline:
        reply.update(partial("image", notes=(
            "the PNG is %d bytes, over the %d a reply carries inline, so it "
            "is only in %s. Lower tile_size or narrow the window to get it "
            "back as an image." % (len(encoded), MAX_INLINE_IMAGE_BYTES,
                                   path),)))
        return reply
    return ImageReply(reply, encoded)
