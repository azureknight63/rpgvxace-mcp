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
from typing import Any, NamedTuple

from ..project import MAX_MAP_ID, _contained
from ..render import overlays as marks, png
from ..render.canvas import resize_nearest, to_rgb
from ..render.layouts import ACE_LAYOUT, VX_LAYOUT, Layout, xp_layout
from ..render.scene import Sprite, TileGrid, render_map
from ..render.tiles import SHEET_NAMES, TILE
from ..rubytypes import RubyHash, RubyObject, Table, as_list
from ..schema import TILE_LAYER_COUNT, XP_AUTOTILES, on_map
from .map_common import _check_budget
from .registry import (
    ARR,
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
    scopes_arg,
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


# How many overlays one call may name: each of the kinds, once.
MAX_OVERLAYS = len(marks.OVERLAY_NAMES)


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


# Why VX is refused unless the call asks for the remembered numbering, and what
# is and is not established. The facts are in docs/engine_parity.md.
_VX_REFUSAL = (
    "map_render does not draw RPG Maker VX maps unless you pass "
    "unverified_formats=true. What is established: VX keeps its tile graphics "
    "in Graphics/System as TileA1 to TileA5 and TileB to TileE, in the same "
    "sizes as VX Ace's. What is not: how VX numbers its tile ids and "
    "autotile shapes in map data (no VX project exists to check this "
    "server's table against) and what its flag table holds, so a picture "
    "could be wrong in ways nobody could tell from a right one. With "
    "unverified_formats=true it is drawn with VX Ace's numbering and the "
    "reply carries an engine_warning; map_tiles reads the tile ids without "
    "guessing. See docs/engine_parity.md.")


class _Plan(NamedTuple):
    """How one call draws, decided before the map is read."""

    layout: Layout
    engine: str
    unverified: bool


def _plan(ctx: Context, args: dict[str, Any], overlays: list[str]) -> _Plan:
    """The layout for this project, or the refusal that says what is missing."""
    project = ctx.project
    engine = project.engine
    unverified = opt_bool(args, "unverified_formats")
    if engine == "vx_ace":
        return _Plan(ACE_LAYOUT, engine, unverified)
    if engine not in ("vx", "xp"):
        raise ToolError(
            "map_render draws VX Ace maps (and, with its caveats, VX and "
            "XP), and this is an %s project. Use map_tiles to read the "
            "tile ids instead." % project.profile.label)
    short = "XP" if engine == "xp" else "VX"
    if engine == "vx" and not unverified:
        raise ToolError(_VX_REFUSAL)
    layout = VX_LAYOUT if engine == "vx" \
        else xp_layout(autotiles=unverified, characters=unverified)
    if opt_bool(args, "regions"):
        raise ToolError("%s has no region layer, so regions has nothing to "
                        "draw." % short)
    missing = [name for name in overlays if name not in layout.overlays]
    if missing:
        if engine == "xp":
            why = ("XP keeps three tables (passages, priorities and "
                   "terrain_tags) where VX Ace has one flag word, and how "
                   "its Game_Map combines them is not established here; "
                   "unverified_formats does not change that")
        else:
            why = ("VX's flag table (the System file's passages) is not "
                   "modelled here")
        raise ToolError(
            "the %s overlay is not drawn for %s: %s. event_ids can be "
            "drawn; map_tiles and db_get read the tables themselves. See "
            "docs/engine_parity.md." % (", ".join(missing), short, why))
    return _Plan(layout, engine, unverified)


def _engine_warning(plan: _Plan, flag_entries: int) -> str:
    """What a reader of an XP or VX picture must not take for granted."""
    if plan.engine == "xp" and not plan.unverified:
        return (
            "RPG Maker XP map, drawn from the facts this server has on "
            "record (docs/engine_parity.md): tileset tiles, above or below "
            "characters by whether their priority is 0. Not drawn: autotile "
            "cells (their 48-pattern table is the engine's and is not "
            "verified; the ground is often autotiles), character graphics "
            "(numbered markers instead), passability and terrain tags, "
            "animation, panorama and fog. Pass unverified_formats=true to "
            "draw autotiles and characters from remembered formats; the "
            "picture may then be wrong.")
    if plan.engine == "xp":
        return (
            "RPG Maker XP map drawn with unverified_formats: autotiles use a "
            "48-pattern table and 96 x 128 graphic layout that were "
            "remembered, not read from XP (frame 0 of an animated one), and "
            "characters a remembered 4 x 4 sheet layout. Tiles go above "
            "characters when their priority is not 0, whatever the number. "
            "Not drawn: passability, terrain tags, animation, panorama, fog. "
            "The picture may be wrong; docs/engine_parity.md says where.")
    return (
        "RPG Maker VX map drawn with unverified_formats: VX Ace's tile id "
        "bands, autotile shape numbering and block layout applied to "
        "Graphics/System/TileA1..TileE. The sheet names and sizes were "
        "measured on VX tile packs; the numbering was not checked against "
        "any VX map. VX's flag table is read from the System file's "
        "passages, %s. The picture may be wrong; "
        "docs/engine_parity.md says where."
        % ("and tiles with its star bit are drawn above characters "
           "(%d entries)" % flag_entries
           if flag_entries else "which this project does not have, so no "
                                "tile is drawn above characters"))


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


def _sheet_files(ctx: Context, plan: _Plan, tileset: RubyObject
                 ) -> "list[tuple[str, str, str]]":
    """``(sheet key, file name, asset kind)`` for every sheet the layout cuts.

    Ace: the nine names a tileset lists, in Graphics/Tilesets. VX: the same
    nine sheets as ``Tile`` plus their letter, in Graphics/System. XP: the one
    tileset graphic and seven autotile graphics (Graphics/Autotiles).
    """
    if plan.engine == "vx_ace":
        names = as_list(tileset.get("tileset_names"))
        return [(sheet, decoded(ctx, names[index]) if index < len(names)
                 else "", "tilesets")
                for index, sheet in enumerate(SHEET_NAMES)]
    if plan.engine == "vx":
        return [(sheet, "Tile" + sheet, "system") for sheet in SHEET_NAMES]
    autotiles = as_list(tileset.get("autotile_names"))
    out = [("tileset", decoded(ctx, tileset.get("tileset_name")), "tilesets")]
    for number in range(1, XP_AUTOTILES + 1):
        out.append(("autotile %d" % number,
                    decoded(ctx, autotiles[number - 1])
                    if number <= len(autotiles) else "", "autotiles"))
    return out


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
        if isinstance(page, RubyObject):
            sprite.through = page.get("through")
            sprite.priority_type = page.get("priority_type")
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


def _overlay_notes(overlays: list[str], result, flag_entries: int,
                   tile_size: int) -> list[str]:
    """What the overlays could not say, or say only approximately."""
    notes = []
    if result.overlay_labels_skipped:
        skipped = "; ".join("%s %d" % item for item in
                            sorted(result.overlay_labels_skipped.items()))
        if marks.digit_scale(tile_size) is None:
            why = ("tile_size %d is below %d, where a digit cannot be read"
                   % (tile_size, marks.MIN_DIGIT_TILE_SIZE))
        else:
            why = "the number is too long to fit a cell"
        notes.append("labels counted but not drawn (%s): %s." % (skipped, why))
    if result.overlay_unknown_cells:
        notes.append(
            "cells holding a tile id the tileset's flags table (%d entries) "
            "has no entry for, by overlay: %s; they are treated as passable "
            "and untagged."
            % (flag_entries, ", ".join(
                "%s %d" % item
                for item in sorted(result.overlay_unknown_cells.items()))))
    if marks.PASSABILITY in overlays:
        notes.append(
            "passability follows Game_Map#check_passage for the player; solid "
            "events and tile-graphic events are judged by each event's first "
            "page, with its conditions not evaluated, and vehicles, a looping "
            "map and cells outside the window are not shown.")
    return notes


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
    "Draw a map as a picture you can look at -- VX Ace fully, XP and VX "
    "only as far as they are established (see engine_warning): the tile layers with "
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
    "cell lines with coordinates every fifth cell. overlays (at most %d, each "
    "of passability, terrain_tags, event_ids) mark the picture for reading "
    "rather than looking: passability tints cells the player cannot enter "
    "and bars each blocked edge, from the tileset's passage flags as the "
    "engine reads them, and frames cells a solid event stands on; terrain_tags "
    "writes the tileset's tag (1-7) on tagged cells; event_ids writes each "
    "event's id on its cell. The reply's legend says what each mark means "
    "and overlay_cells counts them. Digits are skipped below tile_size %d "
    "(counted, with overlays_note saying so). Not drawn: animation, "
    "parallax, weather, tint, counter-table fronts, later event pages (the "
    "overlays also judge events by page 1). XP: tileset tiles are drawn, "
    "autotiles and characters (markers instead) only with "
    "unverified_formats; passability and terrain_tags are refused. VX: "
    "refused unless unverified_formats, which draws with VX Ace's "
    "numbering. Every XP or VX reply carries engine_warning. "
    "docs/engine_parity.md has what is and is not known."
    % ("{:,}".format(MAX_INLINE_IMAGE_BYTES), MAX_KEPT_RENDERS, TILE, "{:,}".format(MAX_NATIVE_PIXELS),
       "{:,}".format(MAX_OUTPUT_PIXELS), "{:,}".format(png.MAX_PNG_BYTES),
       png.MAX_PNG_SIDE, "{:,}".format(png.MAX_PNG_PIXELS), MAX_OVERLAYS,
       marks.MIN_DIGIT_TILE_SIZE),
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
        "overlays": prop(ARR, "Marks to draw over the map, any of %s (at most "
                              "%d, each once; default none). passability: "
                              "red tint on cells the player cannot enter, a "
                              "yellow bar on each blocked edge, a cyan frame "
                              "where a solid event stands. terrain_tags: the "
                              "tag digit on tagged cells. event_ids: the "
                              "event id on its cell. They read events even "
                              "when events is none."
                         % (", ".join(marks.OVERLAY_NAMES), MAX_OVERLAYS),
                         items={"type": STR, "enum": list(marks.OVERLAY_NAMES)},
                         maxItems=MAX_OVERLAYS),
        "unverified_formats": prop(
            BOOL, "XP and VX only, default false, no effect on VX Ace. true "
                  "draws what was remembered rather than verified: for XP "
                  "its autotile pieces and character sheets, for VX the "
                  "whole map with VX Ace's tile numbering. The picture may "
                  "be wrong, and the reply says so in engine_warning."),
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
    overlays = scopes_arg(args, "overlays", marks.OVERLAY_NAMES, default=())
    project = ctx.project
    plan = _plan(ctx, args, overlays)
    layout = plan.layout

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

    if plan.engine == "vx":
        # VX has one global tileset: no row to read, and its flag table is
        # (as far as is remembered) the System file's `passages`.
        tileset_id, tileset = None, RubyObject("RPG::Tileset")
        system = project.load_data("system", refresh)
        flags_table = system.get("passages") if isinstance(
            system, RubyObject) else None
    else:
        tileset_id, tileset = _tileset_of(ctx, game_map, refresh)
        flags_table = tileset.get("priorities" if plan.engine == "xp"
                                  else "flags")
    flags = flags_table.data if isinstance(flags_table, Table) else ()
    grid = TileGrid(data.xsize, data.ysize, data.zsize, data.data)
    # The overlays that mark events read them even when none is drawn.
    wants_events = bool({marks.PASSABILITY, marks.EVENT_IDS} & set(overlays))
    sprites, cut_short = ([], False) if events == "none" and not wants_events \
        else _sprites(ctx, game_map, window)

    # Only the sheets the window actually uses are opened: a decode costs
    # more than the drawing, and most maps use five of the nine.
    wanted = {layout.sheet_needed(t) for t in _ids_in(grid, window)}
    if events == "graphics":
        # A marker draws no graphic, so it needs no sheet; decoding one here
        # also reported sheets "not read" that nothing drew.
        wanted.update(layout.sheet_needed(s.tile_id) for s in sprites
                      if s.tile_id)
    wanted.discard(None)
    sheets: dict[str, png.Image | None] = {}
    unread: dict[str, str] = {}
    for sheet_name, file_name, kind in _sheet_files(ctx, plan, tileset):
        if sheet_name not in wanted:
            continue
        sheets[sheet_name] = None
        if not file_name:
            unread[sheet_name] = "the tileset names no file for this sheet"
            continue
        try:
            sheets[sheet_name] = _load_graphic(ctx, kind, file_name)
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
        regions=opt_bool(args, "regions"), show_grid=opt_bool(args, "grid"),
        overlays=overlays, tile_size=tile_size, layout=layout)
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
        "tileset": ({"id": tileset_id,
                     "name": decoded(ctx, tileset.get("name")),
                     "mode": tileset.get("mode")} if plan.engine == "vx_ace"
                    else {"id": tileset_id,
                          "name": decoded(ctx, tileset.get("name"))}),
        "drawn": {"tiles": result.tiles_drawn,
                  "event_graphics": result.sprites_drawn,
                  "event_markers": len(result.sprites_marked)
                  if events == "graphics" else 0},
    }
    if plan.engine != "vx_ace":
        reply["engine"] = project.profile.label
        reply["unverified_formats"] = plan.unverified
        reply["engine_warning"] = _engine_warning(plan, len(flags))
    if overlays:
        reply["overlays"] = overlays
        reply["legend"] = marks.legend(overlays)
        reply["overlay_cells"] = {
            key: count for counts in result.overlay_cells.values()
            for key, count in counts.items()}
        reply.update(partial("overlays", notes=_overlay_notes(
            overlays, result, len(flags), tile_size)))
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
