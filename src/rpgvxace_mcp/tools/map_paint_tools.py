"""Painting a map by autotile kind, and the generators built on that.

`map_tiles` writes exactly the values it is given, which is the right
contract for a raw tool and the wrong one for building a map: an autotile's
id carries a shape that depends on its neighbours, so painting a patch of
grass means computing forty-odd shape numbers by hand -- and re-computing
the ones *around* the patch, which `map_tiles` must not touch because it was
not asked to. This tool is the other contract: it is given a *kind*, writes
the shape each cell's neighbours call for, and re-shapes the cells around
the edit. So it is a tool of its own rather than more ops on `map_tiles`,
whose ops all promise to write what they were sent and nothing else.

The generators -- `room`, `corridor`, `dungeon` -- lay out floor cells and
then write through the same path, in the style of a VX Ace dungeon: the
floor, the wall face above every floor cell that has no floor above it, and
the wall top (the "ceiling") around both. The editor's automatic shadow is
added to the right of every wall, as the editor would.

The shape rules themselves are in `..autotile`, which `render` reads too.
"""

from __future__ import annotations

import random
from typing import Any

from ..autotile import (
    KIND_RANGES,
    Layer,
    TILE_ID_A5,
    auto_shadow,
    first_id,
    kind_of,
    neighbourhood,
    reshape,
    sheet_of,
    table_of,
)
from ..project import MAX_MAP_ID, _engine_label
from ..rubytypes import RubyObject, Table, as_list
from ..schema import (
    MAX_REGION_ID,
    META_LAYER,
    TILESET_SHEET_NAMES,
    with_region,
)
from .map_common import MAX_CELLS_PER_READ, _check_budget
from .registry import (
    ARR,
    BOOL,
    Context,
    DRY_RUN,
    INT,
    STR,
    ToolError,
    as_int,
    check_op_args,
    decoded,
    need_int,
    need_str,
    one_of,
    opt_bool,
    opt_int,
    opt_int_default,
    opt_list,
    prop,
    schema,
    tool,
    write_reply,
)


# How many cells one `cells` list may name. A list is for scattered cells;
# anything larger is a rectangle, and `fill` takes those.
MAX_PAINT_CELLS = 2000

# How many rooms one dungeon may hold, and how long a room's side may be.
# Placement tries thirty spots per room, so the room count bounds the work.
MAX_ROOMS = 30
MAX_ROOM_SIDE = 30

# How tall a wall face is drawn, and how wide a corridor. The editor's own
# dungeon walls are two tiles tall; one and three are the useful variants.
MAX_WALL_HEIGHT = 3
MAX_CORRIDOR_WIDTH = 3

# The largest seed, so a seed round-trips through any JSON client exactly.
MAX_SEED = 2_147_483_647

# What a dungeon is built with when the caller does not say.
DEFAULT_ROOMS = 8
DEFAULT_ROOM_MIN, DEFAULT_ROOM_MAX = 4, 9
DEFAULT_WALL_HEIGHT = 2

# Where a room's door may go.
DOOR_SIDES = ("none", "top", "bottom", "left", "right")

# Placement attempts per wanted room before a dungeon settles for fewer.
_TRIES_PER_ROOM = 30

# How many cells one call may re-shape: the cells it painted, their
# neighbours, and every cell of a wall run beside them (a wall's left and
# right join depends on its whole run). The painted cells are bounded by the
# windowed ceiling; this bounds what a map of very tall walls adds to them.
MAX_RESHAPE_CELLS = 100_000


# The arguments every op reads, and every generator.
_COMMON = {"op", "map_id", "dry_run"}
_GENERATED = _COMMON | {"floor", "wall", "wall_height", "shadows"}

# What each op reads.
PAINT_OP_ARGS = {
    "fill": _COMMON | {"kind", "x", "y", "width", "height", "z", "shadows"},
    "paint": _COMMON | {"kind", "cells", "z", "shadows"},
    "border": _COMMON | {"kind", "x", "y", "width", "height", "z",
                         "shadows"},
    "region": _COMMON | {"region", "x", "y", "width", "height", "cells"},
    "room": _GENERATED | {"x", "y", "width", "height", "door", "door_at"},
    "corridor": _GENERATED | {"x", "y", "to_x", "to_y", "corridor_width"},
    "dungeon": _GENERATED | {"x", "y", "width", "height", "seed", "rooms",
                             "room_min", "room_max", "corridor_width"},
}


class _Kind:
    """One thing a caller asked to paint: an autotile kind, or a plain tile.

    ``spec`` is how the caller wrote it, kept for the reply; ``kind`` is the
    autotile kind number (None for a plain A5 tile) and ``tile_id`` the id
    written before shaping -- a kind's shape 0, or the plain tile itself.
    """

    __slots__ = ("spec", "kind", "tile_id", "sheet")

    def __init__(self, spec: str, kind: "int | None", tile_id: int,
                 sheet: str):
        self.spec = spec
        self.kind = kind
        self.tile_id = tile_id
        self.sheet = sheet

    @property
    def table(self) -> str:
        return "plain" if self.kind is None else table_of(self.kind)

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"spec": self.spec, "sheet": self.sheet,
                               "table": self.table}
        if self.kind is not None:
            out["kind"] = self.kind
        out["tile_id"] = self.tile_id
        return out

    def holds(self, tile_id: int) -> bool:
        """Whether a stored id is this kind, in any shape."""
        if self.kind is None:
            return tile_id == self.tile_id
        return kind_of(tile_id) == self.kind


_SPEC_HELP = (
    "Write a kind as SHEET:INDEX -- A1:0 to A1:15, A2:0 to A2:31, A3:0 to "
    "A3:31, A4:0 to A4:47, counting blocks left to right and top to bottom "
    "on that sheet (A4 alternates a row of wall tops and a row of wall "
    "faces); A5:0 to A5:127 is a plain floor tile. A tile id read with "
    "map_tiles, written as a number in a string, names the same kind.")


def _parse_kind(spec: str, what: str) -> _Kind:
    """A kind spelled by the caller, or a refusal that says how to spell it."""
    text = spec.strip()
    if text.isdigit():
        tile_id = int(text)
        kind = kind_of(tile_id)
        if kind is not None:
            return _Kind(spec, kind, first_id(kind), sheet_of(kind))
        if TILE_ID_A5 <= tile_id < TILE_ID_A5 + 128:
            return _Kind(spec, None, tile_id, "A5")
        raise ToolError(
            "%s %s is not an A1-A5 tile id. B-E tiles belong on layer 2 and "
            "are written with map_tiles. %s" % (what, spec, _SPEC_HELP))
    sheet, _, index = text.partition(":")
    sheet = sheet.upper()
    if sheet == "A5":
        count = 128
    elif sheet in KIND_RANGES:
        first, last = KIND_RANGES[sheet]
        count = last - first
    else:
        raise ToolError("%s %r names no A1-A5 sheet. %s"
                        % (what, spec, _SPEC_HELP))
    if not index.isdigit() or int(index) >= count:
        raise ToolError("%s %r: %s has blocks 0 to %d. %s"
                        % (what, spec, sheet, count - 1, _SPEC_HELP))
    if sheet == "A5":
        return _Kind(spec, None, TILE_ID_A5 + int(index), "A5")
    kind = KIND_RANGES[sheet][0] + int(index)
    return _Kind(spec, kind, first_id(kind), sheet)


def _wall_pair(wall: _Kind) -> tuple[_Kind, _Kind]:
    """(top, face) for an A4 wall or (roof, wall) for an A3 one.

    Either half names the pair: on both sheets the row under a top or roof
    holds the face that goes with it, eight kinds on.
    """
    if wall.kind is None or wall.sheet not in ("A3", "A4"):
        raise ToolError(
            "wall %s is on %s; a wall comes from A4 (dungeon and interior "
            "walls: a top above a face) or A3 (buildings: a roof above a "
            "wall). %s" % (wall.spec, wall.sheet, _SPEC_HELP))
    row = (wall.kind - KIND_RANGES[wall.sheet][0]) // 8
    top = wall.kind if row % 2 == 0 else wall.kind - 8
    return (_Kind("%s:%d" % (wall.sheet, top - KIND_RANGES[wall.sheet][0]),
                  top, first_id(top), wall.sheet),
            _Kind("%s:%d" % (wall.sheet,
                             top + 8 - KIND_RANGES[wall.sheet][0]),
                  top + 8, first_id(top + 8), wall.sheet))


def _floor_kind(floor: _Kind) -> _Kind:
    if floor.table not in ("floor", "plain"):
        raise ToolError(
            "floor %s is a %s kind; a floor is an A1 or A2 kind, an A4 wall "
            "top, or a plain A5 tile." % (floor.spec, floor.table))
    return floor


class _Canvas:
    """The map being painted: its ground and meta layers, and what changed.

    Every write goes through `put`, which remembers the first value each cell
    had, so the reply can say how many cells the op wrote and how many
    around them were re-shaped -- and the two are told apart by whether the
    op asked for the cell, not by whether it changed.
    """

    __slots__ = ("layer", "meta", "painted", "before")

    def __init__(self, data: Table, z: int):
        self.layer = Layer(data.data, data.xsize, data.ysize, z)
        self.meta = (Layer(data.data, data.xsize, data.ysize, META_LAYER)
                     if data.zsize > META_LAYER else None)
        self.painted: set[tuple[int, int]] = set()
        self.before: dict[tuple[int, int], int] = {}

    @property
    def width(self) -> int:
        return self.layer.width

    @property
    def height(self) -> int:
        return self.layer.height

    def put(self, x: int, y: int, tile_id: int) -> None:
        self.before.setdefault((x, y), self.layer.get(x, y))
        self.layer.set(x, y, tile_id)
        self.painted.add((x, y))

    def finish(self, shadows: bool) -> dict[str, int]:
        """Re-shape around everything painted, shade it, and count."""
        around = neighbourhood(self.layer, self.painted)
        if len(around) > MAX_RESHAPE_CELLS:
            raise ToolError(
                "that edit would re-shape %d cells -- what it painted, the "
                "cells around it and the wall runs beside them -- above the "
                "%d one call re-shapes. Paint a smaller area."
                % (len(around), MAX_RESHAPE_CELLS))
        for cell in around:
            self.before.setdefault(cell, self.layer.get(*cell))
        reshape(self.layer, around)
        counts = {
            "painted": len(self.painted),
            "reshaped": sum(1 for cell in around if cell not in self.painted
                            and self.layer.get(*cell) != self.before[cell]),
        }
        if shadows and self.meta is not None:
            counts["shaded"] = auto_shadow(self.layer, self.meta, around)
        return counts


def _rect(args: dict[str, Any], width: int, height: int, what: str,
          verb: str = "paint") -> tuple[int, int, int, int]:
    """``(x, y, w, h)`` from the arguments, inside the map or refused.

    Bounded by the windowed cell ceiling `map_tiles` uses: every cell of the
    rectangle is a Python-level write and a re-shape.
    """
    x = opt_int_default(args, "x", 0, minimum=0, maximum=width - 1)
    y = opt_int_default(args, "y", 0, minimum=0, maximum=height - 1)
    w = opt_int_default(args, "width", width - x, minimum=1)
    h = opt_int_default(args, "height", height - y, minimum=1)
    if x + w > width or y + h > height:
        raise ToolError("the %s (%d, %d) %dx%d does not fit inside this %dx%d "
                        "map" % (what, x, y, w, h, width, height))
    _check_budget(w * h, verb)
    return x, y, w, h


def _cells(args: dict[str, Any], width: int,
           height: int) -> list[tuple[int, int]]:
    """The `cells` list: [x, y] pairs, each on the map.

    Its length is held to MAX_PAINT_CELLS by the schema's `maxItems`, which
    `check_args` enforces before the handler runs.
    """
    raw = opt_list(args, "cells")
    if not raw:
        raise ToolError("cells needs at least one [x, y] pair")
    out = []
    for index, pair in enumerate(raw):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ToolError("cells[%d] must be an [x, y] pair" % index)
        x = as_int(pair[0], "cells[%d][0]" % index)
        y = as_int(pair[1], "cells[%d][1]" % index)
        if not (0 <= x < width and 0 <= y < height):
            raise ToolError("cells[%d] is (%d, %d), outside this %dx%d map"
                            % (index, x, y, width, height))
        out.append((x, y))
    return out


def _require_sheets(ctx: Context, game_map: RubyObject,
                    kinds: "list[_Kind]") -> None:
    """Refuse a kind on a sheet this map's tileset names no file for.

    The game draws such a cell as nothing at all, and nothing about the
    write would say so.
    """
    tileset_id = game_map.get("tileset_id")
    entry = (ctx.project.get_entry("tilesets", tileset_id)
             if isinstance(tileset_id, int) and not isinstance(tileset_id, bool)
             else None)
    if not isinstance(entry, RubyObject):
        raise ToolError("this map uses tileset %r, which Tilesets does not "
                        "have, so no kind can be checked against its sheets"
                        % (tileset_id,))
    names = as_list(entry.get("tileset_names"))
    named = [sheet for index, sheet in enumerate(TILESET_SHEET_NAMES)
             if index < len(names) and decoded(ctx, names[index])]
    for kind in kinds:
        if kind.sheet not in named:
            raise ToolError(
                "%s is on the %s sheet, and tileset %d (%s) names no %s file, "
                "so the game would draw nothing there. This tileset has %s; "
                "pick a kind from those, or give the map another tileset."
                % (kind.spec, kind.sheet, tileset_id,
                   decoded(ctx, entry.get("name")), kind.sheet,
                   ", ".join(named) or "no sheets at all"))


def _refuse_engine(ctx: Context) -> None:
    """Only VX Ace's tileset model is known here."""
    engine = ctx.project.engine
    if engine == "vx_ace":
        return
    if engine == "vx":
        raise ToolError(
            "map_paint paints VX Ace maps. A VX (RGSS2) project has one "
            "global tileset in Graphics/System and a different autotile "
            "layout, which this tool does not model, so the shapes it wrote "
            "would be a guess. Use map_tiles to write tile ids directly.")
    raise ToolError(
        "map_paint paints VX Ace maps, and this is a %s project. RPG Maker XP "
        "keeps seven autotiles per tileset with their own 48-id layout, which "
        "this tool does not model, so the shapes it wrote would be a guess. "
        "Use map_tiles to write tile ids directly." % _engine_label(engine))


@tool(
    "map_paint",
    "Paint autotiles by kind, or generate rooms and dungeons",
    "Build a VX Ace map without computing tile ids: name an autotile KIND "
    "and each cell gets the shape (edge, corner, inner corner) its "
    "neighbours call for, and the cells around the edit are re-shaped to "
    "join it, as the editor does. %s "
    "fill paints a rectangle, paint a list of [x, y] cells (at most %d), "
    "border a rectangle's outline, region a region id (0-%d) over a "
    "rectangle or cells, keeping the shadow under it. Generators, all on "
    "layer 0 in the style of a VX Ace dungeon (floor, a wall face above "
    "every floor cell with no floor above it, the wall top around both): "
    "room stamps one room with an optional door; corridor joins two points "
    "with an L-shaped passage; dungeon lays out up to %d rooms of side up "
    "to %d joined by corridors, the same every time for one seed (0-%d). "
    "The wall is an A4 top and face pair or an A3 roof and wall pair, named "
    "by either half; walls are 1 to %d tall, corridors 1 to %d wide. The "
    "editor's shadow is set to the right of every wall (shadows=false "
    "leaves layer 3 alone). Rectangles and dungeon areas cover at most %d "
    "cells, and one call re-shapes at most %d (what it painted, the cells "
    "around it, and the wall runs beside those). Every kind must be on a "
    "sheet the map's tileset names. VX and XP projects are refused: their "
    "tilesets are laid out differently."
    % (_SPEC_HELP, MAX_PAINT_CELLS, MAX_REGION_ID, MAX_ROOMS, MAX_ROOM_SIDE,
       MAX_SEED, MAX_WALL_HEIGHT, MAX_CORRIDOR_WIDTH, MAX_CELLS_PER_READ,
       MAX_RESHAPE_CELLS),
    schema({
        "op": prop(STR, "What to do.", enum=list(PAINT_OP_ARGS)),
        "map_id": prop(INT, "Which map.", minimum=1, maximum=MAX_MAP_ID),
        "kind": prop(STR, "For fill, paint and border: what to paint, as "
                          "SHEET:INDEX (e.g. A2:0, A4:8, A5:3) or a tile id "
                          "in a string."),
        "x": prop(INT, "Left edge of the rectangle, room floor, dungeon "
                       "area, or a corridor's start (default 0).", minimum=0),
        "y": prop(INT, "Top edge, likewise (default 0).", minimum=0),
        "width": prop(INT, "Rectangle width (default: to the map's right "
                           "edge; a room needs one).", minimum=1),
        "height": prop(INT, "Rectangle height (default: to the map's bottom "
                            "edge; a room needs one).", minimum=1),
        "cells": prop(ARR, "For paint and region: [x, y] pairs, at most %d."
                      % MAX_PAINT_CELLS,
                      items={"type": ARR, "items": {"type": INT}},
                      maxItems=MAX_PAINT_CELLS),
        "z": prop(INT, "For fill, paint and border: layer 0 (the ground, "
                       "default) or 1 (A tiles drawn over it).",
                  minimum=0, maximum=1),
        "region": prop(INT, "For region: the region id to paint, 0-%d."
                       % MAX_REGION_ID, minimum=0, maximum=MAX_REGION_ID),
        "floor": prop(STR, "For room, corridor and dungeon: the floor, an A1 "
                           "or A2 kind, an A4 top, or an A5 tile."),
        "wall": prop(STR, "For room, corridor and dungeon: either half of an "
                          "A4 top/face pair or an A3 roof/wall pair."),
        "wall_height": prop(INT, "How many tiles tall a wall face is "
                                 "(default %d)." % DEFAULT_WALL_HEIGHT,
                            minimum=1, maximum=MAX_WALL_HEIGHT),
        "door": prop(STR, "For room: which side has a door (default none).",
                     enum=list(DOOR_SIDES)),
        "door_at": prop(INT, "For room: how far along that side the door is, "
                             "from its left or top end (default the middle).",
                        minimum=0),
        "to_x": prop(INT, "For corridor: where it ends.", minimum=0),
        "to_y": prop(INT, "For corridor: where it ends.", minimum=0),
        "corridor_width": prop(INT, "For corridor and dungeon: how wide a "
                                    "corridor is (default 1).",
                               minimum=1, maximum=MAX_CORRIDOR_WIDTH),
        "seed": prop(INT, "For dungeon: the seed. One seed always builds the "
                          "same dungeon (default 0).",
                     minimum=0, maximum=MAX_SEED),
        "rooms": prop(INT, "For dungeon: how many rooms to try for (default "
                           "%d, at most %d)." % (DEFAULT_ROOMS, MAX_ROOMS),
                      minimum=1, maximum=MAX_ROOMS),
        "room_min": prop(INT, "For dungeon: the smallest room side (default "
                              "%d)." % DEFAULT_ROOM_MIN,
                         minimum=1, maximum=MAX_ROOM_SIDE),
        "room_max": prop(INT, "For dungeon: the largest room side (default "
                              "%d, at most %d)."
                         % (DEFAULT_ROOM_MAX, MAX_ROOM_SIDE),
                         minimum=1, maximum=MAX_ROOM_SIDE),
        "shadows": prop(BOOL, "Set the editor's wall shadow on layer 3 "
                              "around a ground-layer edit (default true)."),
        "dry_run": DRY_RUN,
    }, ["op", "map_id"]),
    writes=True,
)
def map_paint(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Paint by kind or generate, then re-shape and shade around the edit.

    Every argument is resolved and checked before the first cell is written,
    and the writing is staged, so a refusal or a dry run leaves the cached
    map as it was.
    """
    op = one_of(args, "op", list(PAINT_OP_ARGS))
    check_op_args(op, args, PAINT_OP_ARGS, "map_paint")
    _refuse_engine(ctx)
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    game_map = ctx.project.load_map(map_id)
    data = game_map.get("data")
    if not isinstance(data, Table) or data.zsize <= META_LAYER:
        raise ToolError("map %d has no four-layer tile table to paint" % map_id)
    if data.xsize < 1 or data.ysize < 1:
        raise ToolError("map %d is %dx%d, so it has no cells to paint. Give "
                        "it a size with map_edit op=\"resize\" first."
                        % (map_id, data.xsize, data.ysize))

    plan = _PLANNERS[op](ctx, args, game_map, data)
    dry_run = opt_bool(args, "dry_run")
    filename = ctx.project.map_file(map_id)
    with ctx.staged(filename) as write_landed:
        reply, summary = plan()
        write = write_landed.save(filename, dry_run=dry_run, tool="map_paint",
                                  summary=summary)
    reply = {"map_id": map_id, **reply, "changed": summary, "write": write}
    return write_reply(reply, dry_run)


def _shadows(args: dict[str, Any], z: int) -> bool:
    if z != 0 and args.get("shadows") is not None:
        raise ToolError("shadows follow the ground layer, and this paints "
                        "layer %d; leave shadows out." % z)
    return z == 0 and opt_bool(args, "shadows", True)


def _plan_paint(ctx: Context, args: dict[str, Any], game_map: RubyObject,
                data: Table):
    """fill, paint and border: one kind over a set of cells."""
    op = args["op"]
    kind = _parse_kind(need_str(args, "kind"), "kind")
    z = opt_int_default(args, "z", 0, minimum=0, maximum=1)
    shadows = _shadows(args, z)
    if op == "paint":
        cells = _cells(args, data.xsize, data.ysize)
        where = "%d listed cell(s)" % len(set(cells))
    else:
        x, y, w, h = _rect(args, data.xsize, data.ysize, "rectangle")
        cells = [(cx, cy) for cy in range(y, y + h) for cx in range(x, x + w)
                 if op == "fill" or cx in (x, x + w - 1)
                 or cy in (y, y + h - 1)]
        where = "%s%dx%d at (%d, %d)" % (
            "the outline of " if op == "border" else "", w, h, x, y)
    _require_sheets(ctx, game_map, [kind])

    def run() -> tuple[dict[str, Any], str]:
        canvas = _Canvas(data, z)
        for cx, cy in cells:
            canvas.put(cx, cy, kind.tile_id)
        counts = canvas.finish(shadows)
        summary = ("painted %s on layer %d with %s; re-shaped %d around it"
                   % (where, z, kind.spec, counts["reshaped"]))
        return {"kind": kind.describe(), "z": z, **counts}, summary
    return run


def _plan_region(ctx: Context, args: dict[str, Any], game_map: RubyObject,
                 data: Table):
    """A region id over a rectangle or a list, keeping each cell's shadow."""
    region = need_int(args, "region", minimum=0, maximum=MAX_REGION_ID)
    rect_given = any(args.get(k) is not None
                     for k in ("x", "y", "width", "height"))
    if args.get("cells") is not None:
        if rect_given:
            raise ToolError("region takes cells or a rectangle, not both")
        cells = _cells(args, data.xsize, data.ysize)
    else:
        x, y, w, h = _rect(args, data.xsize, data.ysize, "rectangle")
        cells = [(cx, cy) for cy in range(y, y + h) for cx in range(x, x + w)]

    def run() -> tuple[dict[str, Any], str]:
        meta = Layer(data.data, data.xsize, data.ysize, META_LAYER)
        for cx, cy in cells:
            meta.set(cx, cy, with_region(meta.get(cx, cy), region))
        count = len(set(cells))
        return ({"region": region, "painted": count},
                "painted region %d over %d cell(s)" % (region, count))
    return run


def _generator_kinds(ctx: Context, args: dict[str, Any],
                     game_map: RubyObject) -> tuple[_Kind, _Kind, _Kind]:
    """(floor, top, face) for a generator, checked against the tileset."""
    floor = _floor_kind(_parse_kind(need_str(args, "floor"), "floor"))
    top, face = _wall_pair(_parse_kind(need_str(args, "wall"), "wall"))
    _require_sheets(ctx, game_map, [floor, top, face])
    return floor, top, face


def _kinds_reply(floor: _Kind, top: _Kind, face: _Kind) -> dict[str, Any]:
    return {"floor": floor.describe(), "wall_top": top.describe(),
            "wall_face": face.describe()}


class _Builder:
    """Lays out a dungeon-style area on a canvas: floor, faces, ceiling.

    Floor cells are collected first and the walls derived from all of them
    at once, so a corridor that runs into a room opens its wall rather than
    ending at it.
    """

    __slots__ = ("canvas", "floor", "top", "face", "wall_height", "floors")

    def __init__(self, canvas: _Canvas, floor: _Kind, top: _Kind,
                 face: _Kind, wall_height: int):
        self.canvas = canvas
        self.floor = floor
        self.top = top
        self.face = face
        self.wall_height = wall_height
        self.floors: set[tuple[int, int]] = set()

    def is_floor(self, x: int, y: int) -> bool:
        return ((x, y) in self.floors
                or self.floor.holds(self.canvas.layer.get(x, y)))

    def is_wall(self, x: int, y: int) -> bool:
        tile_id = self.canvas.layer.get(x, y)
        return self.top.holds(tile_id) or self.face.holds(tile_id)

    def add_floor(self, x: int, y: int, w: int, h: int) -> None:
        canvas = self.canvas
        for cy in range(max(y, 0), min(y + h, canvas.height)):
            for cx in range(max(x, 0), min(x + w, canvas.width)):
                self.floors.add((cx, cy))

    def build(self, ring: bool) -> None:
        """Write the floor, then the faces above it, then the ceiling."""
        canvas = self.canvas
        for cx, cy in sorted(self.floors, key=lambda c: (c[1], c[0])):
            canvas.put(cx, cy, self.floor.tile_id)
        faces = set()
        for cx, cy in sorted(self.floors, key=lambda c: (c[1], c[0])):
            for step in range(1, self.wall_height + 1):
                above = cy - step
                if above < 0 or self.is_floor(cx, above):
                    break
                faces.add((cx, above))
        for cx, cy in sorted(faces, key=lambda c: (c[1], c[0])):
            canvas.put(cx, cy, self.face.tile_id)
        if not ring:
            return
        solid = self.floors | faces
        ceiling = set()
        for cx, cy in solid:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = cx + dx, cy + dy
                    if (0 <= nx < canvas.width and 0 <= ny < canvas.height
                            and (nx, ny) not in solid
                            and not self.is_floor(nx, ny)
                            and not self.is_wall(nx, ny)):
                        ceiling.add((nx, ny))
        for cx, cy in sorted(ceiling, key=lambda c: (c[1], c[0])):
            canvas.put(cx, cy, self.top.tile_id)


def _corridor_cells(builder: _Builder, start: tuple[int, int],
                    end: tuple[int, int], width: int,
                    vertical_first: bool = False) -> None:
    """An L-shaped passage ``width`` wide from ``start`` to ``end``."""
    (x0, y0), (x1, y1) = start, end
    corner = (x0, y1) if vertical_first else (x1, y0)
    for (ax, ay), (bx, by) in ((start, corner), (corner, end)):
        left, top = min(ax, bx), min(ay, by)
        builder.add_floor(left, top, abs(bx - ax) + width, abs(by - ay) + width)


def _plan_room(ctx: Context, args: dict[str, Any], game_map: RubyObject,
               data: Table):
    floor, top, face = _generator_kinds(ctx, args, game_map)
    wall_height = opt_int_default(args, "wall_height", DEFAULT_WALL_HEIGHT,
                                  minimum=1, maximum=MAX_WALL_HEIGHT)
    for name in ("width", "height"):
        if args.get(name) is None:
            raise ToolError("room needs a %s: the size of its floor" % name)
    x, y, w, h = _rect(args, data.xsize, data.ysize, "room")
    if y < wall_height:
        raise ToolError(
            "a room whose floor starts at row %d has %d row(s) above it, and "
            "its wall is %d tall. Move it down, or lower wall_height."
            % (y, y, wall_height))
    door = one_of(args, "door", list(DOOR_SIDES), "none")
    side = w if door in ("top", "bottom") else h
    door_at = opt_int_default(args, "door_at", side // 2, minimum=0)
    if door == "none" and args.get("door_at") is not None:
        raise ToolError("door_at places a door; name its side with door")
    if door != "none" and door_at >= side:
        raise ToolError("door_at is %d, past the end of that %d-tile side"
                        % (door_at, side))
    shadows = opt_bool(args, "shadows", True)

    def run() -> tuple[dict[str, Any], str]:
        canvas = _Canvas(data, 0)
        builder = _Builder(canvas, floor, top, face, wall_height)
        builder.add_floor(x, y, w, h)
        if door == "top":
            builder.add_floor(x + door_at, y - wall_height - 1, 1,
                              wall_height + 1)
        elif door == "bottom":
            builder.add_floor(x + door_at, y + h, 1, 1)
        elif door == "left":
            builder.add_floor(x - 1, y + door_at, 1, 1)
        elif door == "right":
            builder.add_floor(x + w, y + door_at, 1, 1)
        builder.build(ring=True)
        counts = canvas.finish(shadows)
        reply = {"room": {"x": x, "y": y, "width": w, "height": h},
                 "door": door, **_kinds_reply(floor, top, face), **counts}
        return reply, ("built a %dx%d room at (%d, %d)%s; re-shaped %d around "
                       "it" % (w, h, x, y, "" if door == "none"
                               else " with a door on the " + door,
                               counts["reshaped"]))
    return run


def _plan_corridor(ctx: Context, args: dict[str, Any], game_map: RubyObject,
                   data: Table):
    floor, top, face = _generator_kinds(ctx, args, game_map)
    wall_height = opt_int_default(args, "wall_height", DEFAULT_WALL_HEIGHT,
                                  minimum=1, maximum=MAX_WALL_HEIGHT)
    width = opt_int_default(args, "corridor_width", 1, minimum=1,
                            maximum=MAX_CORRIDOR_WIDTH)
    points = []
    for xs, ys in (("x", "y"), ("to_x", "to_y")):
        px = need_int(args, xs, minimum=0)
        py = need_int(args, ys, minimum=0)
        if px + width > data.xsize or py + width > data.ysize:
            raise ToolError(
                "a corridor %d wide at (%d, %d) runs off this %dx%d map"
                % (width, px, py, data.xsize, data.ysize))
        points.append((px, py))
    shadows = opt_bool(args, "shadows", True)

    def run() -> tuple[dict[str, Any], str]:
        canvas = _Canvas(data, 0)
        builder = _Builder(canvas, floor, top, face, wall_height)
        _corridor_cells(builder, points[0], points[1], width)
        builder.build(ring=True)
        counts = canvas.finish(shadows)
        reply = {"corridor": {"from": list(points[0]), "to": list(points[1]),
                              "width": width},
                 **_kinds_reply(floor, top, face), **counts}
        return reply, ("dug a corridor from (%d, %d) to (%d, %d); re-shaped "
                       "%d around it" % (points[0] + points[1]
                                         + (counts["reshaped"],)))
    return run


def _place_rooms(rng: random.Random, area: tuple[int, int, int, int],
                 wanted: int, smallest: int, largest: int,
                 wall_height: int) -> list[tuple[int, int, int, int]]:
    """Up to ``wanted`` rooms inside ``area``, apart from one another.

    Each room's floor leaves room above it for its wall and a row of ceiling,
    and a column of ceiling either side, so two rooms never share a wall.
    """
    ax, ay, aw, ah = area
    rooms: list[tuple[int, int, int, int]] = []
    for _ in range(wanted * _TRIES_PER_ROOM):
        if len(rooms) == wanted:
            break
        w = rng.randint(smallest, largest)
        h = rng.randint(smallest, largest)
        low_x, high_x = ax + 1, ax + aw - 1 - w
        low_y, high_y = ay + wall_height + 1, ay + ah - 1 - h
        if high_x < low_x or high_y < low_y:
            continue
        x, y = rng.randint(low_x, high_x), rng.randint(low_y, high_y)
        if any(x - 2 < ox + ow and ox - 2 < x + w
               and y - wall_height - 2 < oy + oh
               and oy - wall_height - 2 < y + h
               for ox, oy, ow, oh in rooms):
            continue
        rooms.append((x, y, w, h))
    return rooms


def _plan_dungeon(ctx: Context, args: dict[str, Any], game_map: RubyObject,
                  data: Table):
    floor, top, face = _generator_kinds(ctx, args, game_map)
    wall_height = opt_int_default(args, "wall_height", DEFAULT_WALL_HEIGHT,
                                  minimum=1, maximum=MAX_WALL_HEIGHT)
    width = opt_int_default(args, "corridor_width", 1, minimum=1,
                            maximum=MAX_CORRIDOR_WIDTH)
    seed = opt_int_default(args, "seed", 0, minimum=0, maximum=MAX_SEED)
    wanted = opt_int_default(args, "rooms", DEFAULT_ROOMS, minimum=1,
                             maximum=MAX_ROOMS)
    smallest = opt_int_default(args, "room_min", DEFAULT_ROOM_MIN, minimum=1,
                               maximum=MAX_ROOM_SIDE)
    largest = opt_int_default(args, "room_max", max(DEFAULT_ROOM_MAX, smallest),
                              minimum=1, maximum=MAX_ROOM_SIDE)
    if largest < smallest:
        raise ToolError("room_max is %d, below room_min %d"
                        % (largest, smallest))
    if smallest < width:
        raise ToolError("room_min is %d, narrower than the %d-wide corridors "
                        "that have to fit inside a room" % (smallest, width))
    area = _rect(args, data.xsize, data.ysize, "dungeon area", "generate")
    shadows = opt_bool(args, "shadows", True)

    def run() -> tuple[dict[str, Any], str]:
        rng = random.Random(seed)
        rooms = _place_rooms(rng, area, wanted, smallest, largest,
                             wall_height)
        if not rooms:
            raise ToolError(
                "no room of side %d to %d fits in the %dx%d area with its "
                "wall; make the area larger or the rooms smaller"
                % (smallest, largest, area[2], area[3]))
        canvas = _Canvas(data, 0)
        ax, ay, aw, ah = area
        for cy in range(ay, ay + ah):
            for cx in range(ax, ax + aw):
                canvas.put(cx, cy, top.tile_id)
        builder = _Builder(canvas, floor, top, face, wall_height)
        for x, y, w, h in rooms:
            builder.add_floor(x, y, w, h)
        for a, b in _joins(rooms, width):
            _corridor_cells(builder, a, b, width,
                            vertical_first=rng.random() < 0.5)
        builder.build(ring=False)
        counts = canvas.finish(shadows)
        reply = {"seed": seed,
                 "rooms": [{"x": x, "y": y, "width": w, "height": h}
                           for x, y, w, h in rooms],
                 **_kinds_reply(floor, top, face), **counts}
        return reply, ("generated a dungeon of %d rooms over %dx%d at "
                       "(%d, %d) from seed %d"
                       % (len(rooms), aw, ah, ax, ay, seed))
    return run


def _joins(rooms: "list[tuple[int, int, int, int]]", width: int
           ) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Which rooms a corridor joins: a shortest tree over their middles.

    Each room not yet reached is joined to the nearest one that is (by
    Manhattan distance, ties to the earlier room), so every room is reached
    and no corridor crosses the map to a room that has a nearer neighbour --
    which joining them in list order did, leaving parallel corridors.
    """
    anchors = [_anchor(room, width) for room in rooms]
    reached, pending = [0], list(range(1, len(rooms)))
    out = []
    while pending:
        _, near, far = min(
            (abs(anchors[a][0] - anchors[b][0])
             + abs(anchors[a][1] - anchors[b][1]), a, b)
            for a in reached for b in pending)
        out.append((anchors[near], anchors[far]))
        reached.append(far)
        pending.remove(far)
    return out


def _anchor(room: tuple[int, int, int, int], width: int) -> tuple[int, int]:
    """Where a corridor leaves a room: its middle, kept inside the floor."""
    x, y, w, h = room
    return (min(x + w // 2, x + w - width), min(y + h // 2, y + h - width))


_PLANNERS = {
    "fill": _plan_paint,
    "paint": _plan_paint,
    "border": _plan_paint,
    "region": _plan_region,
    "room": _plan_room,
    "corridor": _plan_corridor,
    "dungeon": _plan_dungeon,
}

