"""Map stamps: a piece of a map, kept by name, pasted back anywhere.

`map_tiles` reads and writes a rectangle one layer at a time and `map_paint`
builds a map from autotile kinds. Neither can *reuse* what is already built:
a house, a lamp post with its shadow, a room with its chest and the event that
opens it. A stamp is that: the tile ids of the layers asked for, the events
inside the rectangle, and the engine they came from, stored as one JSON file
under the project's work folder (`.rpgvxace-mcp/stamps/NAME.json`) and written
back by `paste`, possibly turned, possibly onto another map.

Three things make a paste more than a copy, and each is a place a plain
"write these ids" would be silently wrong:

* An autotile id carries a shape that depends on its neighbours, and turning
  a stamp changes the neighbours. So a turned stamp's autotile cells are
  re-shaped by the rules `map_paint` uses (`autotile.reshape`), and so is the
  one-cell ring around the paste (`reshape_edges`), so it joins what it lands
  beside. Layer 3's four shadow quarters move with the turn; its region ids do
  not.
* An event is not a tile. It gets a new id, a moved position and its own name;
  it is never laid over another event; and the commands that cannot survive
  the move (a Set Event Location to absolute coordinates, a Transfer Player to
  the map it now stands on, a reference to another event by number) are
  *reported*, not rewritten and not left to be found in play.
* A stamp file is input from outside the server: it may be hand-edited, or a
  copy of someone else's. Every field is checked before the map is touched.

Event commands are stored as `raw` entries and rebuilt through
`commands.compile_commands`, the compiler `event_edit` uses, so a stamp's
events are held to exactly the rules an authored event is -- and a hand-written
stamp may use the DSL forms too.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from array import array
from pathlib import Path
from typing import Any

from ..analysis import WalkBudget
from ..autotile import Layer, neighbourhood, reshape
from ..commands import (
    CODE_BLANK,
    CODE_CONDITIONAL_BRANCH,
    CODE_SET_EVENT_LOCATION,
    CODE_SHOW_ANIMATION,
    CODE_SHOW_BALLOON,
    CODE_TRANSFER_PLAYER,
    CommandError,
    MAX_AUTHORED_INDENT,
    MAX_COMMAND_CODE,
    command_name,
    compile_commands,
    move_route_steps,
    route_codes,
)
from ..convert import (
    ConversionError,
    apply_patch,
    new_walk,
    normalize_strings,
    to_json,
)
from ..paths import _contained, _is_reparse_point
from ..project import MAX_MAP_ID
from ..rubytypes import CELL_MASK, RubyHash, RubyObject, Table, as_list
from ..schema import (
    MAX_REGION_ID,
    MAX_SHADOW,
    META_LAYER,
    META_LAYER_BITS,
    TILE_LAYER_COUNT,
    new_event_command,
    on_map,
)
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
    echo,
    known_fields,
    need_int,
    need_str,
    one_of,
    opt_bool,
    opt_int_default,
    opt_list,
    partial,
    prop,
    refuse_fields,
    refuse_unknown_fields,
    schema,
    tool,
    write_reply,
)

# How many cells one stamp holds: its width times its height. Each layer kept
# is that many numbers in the file, and every one is written, turned and
# checked in Python.
MAX_STAMP_CELLS = 5000

# How many events one stamp carries. A rectangle holding more is refused rather
# than copied with some of them missing.
MAX_STAMP_EVENTS = 50

# How many pages one stamped event carries, and how many commands one page.
# Both bound what a stamp file can ask the compiler to build.
MAX_STAMP_PAGES = 20
MAX_STAMP_PAGE_COMMANDS = 2000

# How many stamps the work folder keeps, and how long a stamp's name is.
MAX_STAMPS = 100
MAX_STAMP_NAME = 48

# The most bytes one stamp file may hold, checked before it is parsed: it is
# the one input here whose size nothing else bounds.
MAX_STAMP_FILE_BYTES = 1_000_000

# How many bytes `list` opens in all. Past it, the stamps are named without
# being read, and `stamps_not_read` says so.
MAX_STAMP_LIST_BYTES = 8_000_000

# How many cells one paste may re-shape: the cells it painted, the ring around
# them, and every cell of a wall run beside those.
MAX_STAMP_RESHAPE_CELLS = 100_000

# How many position-dependent commands one reply lists.
MAX_STAMP_FLAGS = 100

# Not caller-facing. The highest event id a map holds (`event_edit`'s own
# ceiling), the longest event name a stamp carries, and how many directory
# entries `list` looks at before giving up on a folder somebody filled.
_EVENT_ID_UPPER = 9999
_EVENT_NAME_UPPER = 200
_SCAN_UPPER = 2000


STAMP_FORMAT = "rpgvxace-mcp-stamp"
STAMP_VERSION = 1
STAMP_DIR = "stamps"


MIRRORS = ("none", "horizontal", "vertical", "both")
ROTATIONS = (0, 90, 180, 270)


STAMP_OP_ARGS = {
    "copy": {"op", "name", "map_id", "x", "y", "width", "height", "layers",
             "include_events", "dry_run"},
    "paste": {"op", "name", "map_id", "x", "y", "mirror", "rotate",
              "include_events", "reshape_edges", "dry_run"},
    "list": {"op"},
    "delete": {"op", "name", "dry_run"},
}


_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_RESERVED = frozenset(["con", "prn", "aux", "nul"]
                      + ["com%d" % n for n in range(1, 10)]
                      + ["lpt%d" % n for n in range(1, 10)])


# ---------------------------------------------------------------------------
# Turning
# ---------------------------------------------------------------------------


class Orientation:
    """A mirror and then a clockwise rotation, as a map from cell to cell.

    Mirror first, then rotate, so "horizontal, 90" is one definite thing. The
    same `place` moves a tile, an event, and -- on a 2 x 2 grid -- a shadow
    quarter, which is why a cell and the shadow drawn in it cannot disagree.
    """

    __slots__ = ("mirror", "rotate", "_shadow")

    def __init__(self, mirror: str = "none", rotate: int = 0):
        self.mirror = mirror
        self.rotate = rotate
        # Layer 3's low nibble: bit 1 top-left, 2 top-right, 4 bottom-left,
        # 8 bottom-right, i.e. bit = row * 2 + column of the quarter.
        table = []
        for bits in range(MAX_SHADOW + 1):
            turned = 0
            for bit in range(4):
                if bits >> bit & 1:
                    column, row = self.place(bit & 1, bit >> 1, 2, 2)
                    turned |= 1 << (row * 2 + column)
            table.append(turned)
        self._shadow = tuple(table)

    @property
    def identity(self) -> bool:
        return self.mirror == "none" and self.rotate == 0

    def size(self, width: int, height: int) -> tuple[int, int]:
        """The stamp's width and height after the turn."""
        return (height, width) if self.rotate in (90, 270) else (width, height)

    def place(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        """Where the cell (x, y) of a width x height block lands."""
        if self.mirror in ("horizontal", "both"):
            x = width - 1 - x
        if self.mirror in ("vertical", "both"):
            y = height - 1 - y
        for _ in range(self.rotate // 90):
            x, y = height - 1 - y, x
            width, height = height, width
        return x, y

    def shadow(self, bits: int) -> int:
        """A shadow nibble with its quarters moved to where the turn puts them."""
        return self._shadow[bits & MAX_SHADOW]

    def facing(self, direction: Any) -> Any:
        """An event's direction (2 down, 4 left, 6 right, 8 up) after the turn."""
        vectors = {2: (0, 1), 4: (-1, 0), 6: (1, 0), 8: (0, -1)}
        if type(direction) is not int or direction not in vectors:
            return direction
        dx, dy = vectors[direction]
        if self.mirror in ("horizontal", "both"):
            dx = -dx
        if self.mirror in ("vertical", "both"):
            dy = -dy
        for _ in range(self.rotate // 90):
            dx, dy = -dy, dx
        return {(0, 1): 2, (-1, 0): 4, (1, 0): 6, (0, -1): 8}[(dx, dy)]


def turn_cells(cells: "list[int]", width: int, height: int,
               turn: Orientation, meta: bool) -> "list[int]":
    """One layer of a stamp, row by row, after the turn.

    ``meta`` marks layer 3, whose low nibble is four shadow quarters that move
    with the cells; its region byte, and the bits above the nibble that
    nothing reads, stay where the cell goes.
    """
    if turn.identity:
        return list(cells)
    new_width, _ = turn.size(width, height)
    out = [0] * (width * height)
    for sy in range(height):
        base = sy * width
        for sx in range(width):
            value = cells[base + sx]
            if meta:
                value = (value & ~MAX_SHADOW) | turn.shadow(value)
            tx, ty = turn.place(sx, sy, width, height)
            out[ty * new_width + tx] = value
    return out


# ---------------------------------------------------------------------------
# The stamp file
# ---------------------------------------------------------------------------


def _stamp_name(name: str) -> str:
    """A stamp's name, or a refusal. Judged before anything touches the disk."""
    if len(name) > MAX_STAMP_NAME or not _NAME.match(name):
        raise ToolError(
            "%s is not a usable stamp name. It has to be 1 to %d letters, "
            "digits, dashes or underscores, starting with a letter or digit "
            "-- no dots, spaces, separators or drive letters."
            % (echo(name), MAX_STAMP_NAME))
    if name.lower() in _RESERVED:
        raise ToolError("%s is not a usable stamp name: Windows resolves it to "
                        "a device rather than a file." % echo(name))
    return name


def _stamp_folder(ctx: Context) -> Path:
    """The stamp folder, judged to lie inside the project before it is made.

    A link planted as the work folder would otherwise carry the `mkdir`, and
    then the write, somewhere else.
    """
    folder = ctx.project.work_dir() / STAMP_DIR
    _contained(folder, ctx.project.root, "the stamp folder")
    return folder


def _stamp_path(ctx: Context, name: str) -> "tuple[Path, Path]":
    """``(folder, file)`` for a stamp name that has been checked."""
    folder = _stamp_folder(ctx)
    path = folder / (name + ".json")
    if _is_reparse_point(path):
        raise ToolError(
            "stamp '%s' is a link, not a file this server wrote, so it is not "
            "followed. Delete the link, or copy the area again." % name)
    _contained(path, folder, "stamp '%s'" % name)
    return folder, path


def _stamp_files(folder: Path) -> "tuple[list[str], bool]":
    """The names of the stamps in a folder, sorted, and whether the scan gave up.

    Only ``NAME.json`` regular files whose NAME this tool would accept;
    anything else in the folder is not a stamp and is not listed. The scan
    stops after `_SCAN_UPPER` entries so a folder somebody filled cannot make
    a listing walk forever.
    """
    names: list[str] = []
    gave_up = False
    try:
        with os.scandir(folder) as entries:
            for seen, entry in enumerate(entries):
                if seen >= _SCAN_UPPER:
                    gave_up = True
                    break
                stem, dot, extension = entry.name.rpartition(".")
                if (dot and extension == "json" and len(stem) <= MAX_STAMP_NAME
                        and _NAME.match(stem) and stem.lower() not in _RESERVED
                        and not entry.is_dir(follow_symlinks=False)):
                    names.append(stem)
    except FileNotFoundError:
        pass
    return sorted(names), gave_up


def _relative(ctx: Context, path: Path) -> str:
    try:
        return path.relative_to(ctx.project.root).as_posix()
    except ValueError:
        return path.as_posix()


def _read_stamp_bytes(path: Path, name: str) -> bytes:
    """The file's bytes, refusing a file over the ceiling before reading it."""
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ToolError("stamp '%s' could not be read: %s" % (name, exc)) from exc
    if size > MAX_STAMP_FILE_BYTES:
        raise ToolError(
            "stamp '%s' is %d bytes, above the %d a stamp file may hold, so it "
            "was not opened. This server wrote nothing that size; the file "
            "was made some other way." % (name, size, MAX_STAMP_FILE_BYTES))
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_STAMP_FILE_BYTES + 1)
    except OSError as exc:
        raise ToolError("stamp '%s' could not be read: %s" % (name, exc)) from exc
    if len(raw) > MAX_STAMP_FILE_BYTES:
        raise ToolError("stamp '%s' grew past the %d bytes a stamp file may "
                        "hold while it was being read" % (name,
                                                          MAX_STAMP_FILE_BYTES))
    return raw


def _refuse_constant(token: str) -> Any:
    raise ValueError("%s is not a number JSON allows" % token)


def _parse_json(raw: bytes, name: str) -> Any:
    try:
        return json.loads(raw.decode("utf-8"), parse_constant=_refuse_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        # RecursionError: a few thousand open brackets, which fit in a file
        # far under the byte ceiling.
        raise ToolError(
            "stamp '%s' is not JSON this server can read (%s). It was not "
            "written by map_stamp; copy the area again."
            % (name, echo(str(exc)))) from exc


class _Stamp:
    """A stamp file that has passed every check, with its events built."""

    __slots__ = ("name", "width", "height", "layers", "cells", "events",
                 "source_map", "protos")

    def __init__(self, name: str):
        self.name = name
        self.width = 0
        self.height = 0
        self.layers: list[int] = []
        self.cells: dict[int, list[int]] = {}
        self.events: list[dict[str, Any]] = []
        self.source_map = 0
        # One built event per entry of `events`, with a provisional id and
        # position. Built once, when the file is checked, because building is
        # the check: it is what runs the payload through the compiler.
        self.protos: list[RubyObject] = []


def _bad(name: str, why: str) -> ToolError:
    return ToolError(
        "stamp '%s' is not usable: %s. A stamp file that was edited by hand, "
        "or made by something other than map_stamp, is checked before any "
        "map is touched; copy the area again to make a fresh one."
        % (name, why))


def _whole(name: str, value: Any, what: str, low: int, high: int) -> int:
    if type(value) is not int:
        raise _bad(name, "%s must be a whole number from %d to %d, got %s"
                   % (what, low, high, echo(value)))
    if not low <= value <= high:
        raise _bad(name, "%s must be from %d to %d, got %s"
                   % (what, low, high, echo(value)))
    return value


def _exact_keys(name: str, value: Any, keys: "set[str]", what: str) -> dict:
    if not isinstance(value, dict):
        raise _bad(name, "%s must be an object, got %s" % (what, echo(value)))
    missing = sorted(keys - set(value))
    unknown = sorted(set(value) - keys, key=str)
    if missing:
        raise _bad(name, "%s lacks %s" % (what, ", ".join(missing)))
    if unknown:
        raise _bad(name, "%s has %s, which a stamp does not carry"
                   % (what, ", ".join(echo(key) for key in unknown[:5])))
    return value


def _check_cells(ctx: Context, name: str, z: int, cells: Any,
                 expected: int, width: int) -> "list[int]":
    """One layer's numbers: the right count, all whole, all in range."""
    if not isinstance(cells, list) or len(cells) != expected:
        raise _bad(name, "layer %d holds %s cells but %d x %d needs %d"
                   % (z, len(cells) if isinstance(cells, list) else "no list of",
                      width, expected // width, expected))
    ceiling = CELL_MASK
    if all(type(value) is int for value in cells):
        lowest, highest = (min(cells), max(cells)) if cells else (0, 0)
        if lowest >= 0 and highest <= ceiling and (
                z != META_LAYER or not any(v & ~META_LAYER_BITS for v in cells)):
            return cells
    for index, value in enumerate(cells):
        where = "layer %d cell (%d, %d)" % (z, index % width, index // width)
        if type(value) is not int:
            raise _bad(name, "%s must be a whole number, got %s"
                       % (where, echo(value)))
        if not 0 <= value <= ceiling:
            raise _bad(name, "%s is %s, which no tile id can be: 0 to %d"
                       % (where, echo(value), ceiling))
        if z == META_LAYER and value & ~META_LAYER_BITS:
            raise _bad(name, "%s is %d, which sets bits outside the region "
                       "and shadow fields (region * 256 + shadow, a region 0 "
                       "to %d above shadow bits 0 to %d)"
                       % (where, value, MAX_REGION_ID, MAX_SHADOW))
    raise _bad(name, "layer %d holds values this server cannot place" % z)


def _load_stamp(ctx: Context, name: str) -> _Stamp:
    """A stamp by name, read, parsed and checked field by field.

    Everything is refused before the map is loaded. The events are built here
    as well, through the same compiler `event_edit` uses, so a payload the
    editor would not take is found now rather than half way through a paste.
    """
    _folder, path = _stamp_path(ctx, name)
    if not path.is_file():
        raise ToolError("there is no stamp named '%s'. map_stamp op=list shows "
                        "the stamps there are." % name)
    data = _parse_json(_read_stamp_bytes(path, name), name)
    data = _exact_keys(name, data, {"format", "version", "engine", "source",
                                    "width", "height", "layers", "cells",
                                    "events"}, "the file")
    if data["format"] != STAMP_FORMAT:
        raise _bad(name, "format is %s, not %r" % (echo(data["format"]),
                                                   STAMP_FORMAT))
    if data["version"] != STAMP_VERSION or type(data["version"]) is not int:
        raise _bad(name, "version is %s; this server reads version %d"
                   % (echo(data["version"]), STAMP_VERSION))
    engine = ctx.project.engine
    if data["engine"] != engine:
        raise ToolError(
            "stamp '%s' was copied from a %s project, and this is a %s "
            "project. Tile ids, autotile layouts and event commands differ "
            "between the engines, so it is not pasted here."
            % (name, echo(data["engine"]), echo(engine)))
    stamp = _Stamp(name)
    source = _exact_keys(name, data["source"], {"map_id", "x", "y"}, "source")
    stamp.source_map = _whole(name, source["map_id"], "source.map_id", 1,
                              MAX_MAP_ID)
    _whole(name, source["x"], "source.x", 0, MAX_MAP_ID)
    _whole(name, source["y"], "source.y", 0, MAX_MAP_ID)
    stamp.width = _whole(name, data["width"], "width", 1, MAX_STAMP_CELLS)
    stamp.height = _whole(name, data["height"], "height", 1, MAX_STAMP_CELLS)
    count = stamp.width * stamp.height
    if count > MAX_STAMP_CELLS:
        raise _bad(name, "%d x %d is %d cells, above the %d a stamp holds"
                   % (stamp.width, stamp.height, count, MAX_STAMP_CELLS))

    layers = data["layers"]
    deepest = META_LAYER if ctx.project.profile.has_region_layer \
        else TILE_LAYER_COUNT - 1
    if (not isinstance(layers, list) or not layers
            or any(type(z) is not int or not 0 <= z <= deepest for z in layers)
            or layers != sorted(set(layers))):
        raise _bad(name, "layers must be a list of distinct layer numbers, "
                   "smallest first, from 0 to %d for this engine, got %s"
                   % (deepest, echo(layers)))
    stamp.layers = list(layers)
    cells = data["cells"]
    if not isinstance(cells, dict):
        raise _bad(name, "cells must be an object, got %s" % echo(cells))
    for key in cells:
        if key not in {str(z) for z in layers}:
            raise _bad(name, "cells holds layer %s, which layers does not "
                       "list" % echo(key))
    for z in layers:
        if str(z) not in cells:
            raise _bad(name, "layers lists layer %d but cells has none for it"
                       % z)
        stamp.cells[z] = _check_cells(ctx, name, z, cells[str(z)], count,
                                      stamp.width)

    events = data["events"]
    if not isinstance(events, list):
        raise _bad(name, "events must be a list, got %s" % echo(events))
    if len(events) > MAX_STAMP_EVENTS:
        raise _bad(name, "it holds %d events, above the %d a stamp keeps"
                   % (len(events), MAX_STAMP_EVENTS))
    for index, event in enumerate(events):
        where = "event %d" % index
        spec = _exact_keys(name, event, {"id", "name", "x", "y", "pages"},
                           where)
        _whole(name, spec["id"], where + " id", 1, _EVENT_ID_UPPER)
        if not isinstance(spec["name"], str) \
                or len(spec["name"]) > _EVENT_NAME_UPPER:
            raise _bad(name, "%s name must be text of at most %d characters"
                       % (where, _EVENT_NAME_UPPER))
        if (type(spec["x"]) is not int or type(spec["y"]) is not int
                or not on_map(spec["x"], spec["y"], stamp.width, stamp.height)):
            raise _bad(name, "%s must stand inside the %d x %d stamp, got "
                       "(%s, %s)" % (where, stamp.width, stamp.height,
                                     echo(spec["x"]), echo(spec["y"])))
        pages = spec["pages"]
        if (not isinstance(pages, list)
                or not 1 <= len(pages) <= MAX_STAMP_PAGES):
            raise _bad(name, "%s needs 1 to %d pages" % (where,
                                                         MAX_STAMP_PAGES))
        for page_index, page in enumerate(pages):
            held = _exact_keys(name, page, {"patch", "commands"},
                               "%s page %d" % (where, page_index))
            if not isinstance(held["patch"], dict):
                raise _bad(name, "%s page %d patch must be an object"
                           % (where, page_index))
            if (not isinstance(held["commands"], list)
                    or len(held["commands"]) > MAX_STAMP_PAGE_COMMANDS):
                raise _bad(name, "%s page %d commands must be a list of at "
                           "most %d entries" % (where, page_index,
                                                MAX_STAMP_PAGE_COMMANDS))
        try:
            proto = _build_event(ctx, spec, spec["id"], spec["x"], spec["y"])
        except ToolError as exc:
            raise _bad(name, "%s (%s): %s" % (where, echo(spec["name"]), exc))
        stamp.events.append(spec)
        stamp.protos.append(proto)
    return stamp


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _with_route_steps(commands: "list[RubyObject]",
                      engine: str) -> "list[RubyObject]":
    """The 505 (XP 509) lines beside a Set Move Route that lacks them.

    The same job `event_tools._insert_move_route_steps` does for an authored
    list; a tool module may not import a sibling, and a stamp copied from the
    editor already carries the lines, so this fires only for a hand-written
    stamp.
    """
    route_code, step_code = route_codes(engine)
    out: list[RubyObject] = []
    for index, command in enumerate(commands):
        out.append(command)
        if command.get("code") != route_code:
            continue
        following = commands[index + 1] if index + 1 < len(commands) else None
        if (isinstance(following, RubyObject)
                and following.get("code") == step_code):
            continue
        params = as_list(command.get("parameters"))
        route = params[1] if len(params) > 1 else None
        if isinstance(route, RubyObject) and route.cls == "RPG::MoveRoute":
            out.extend(move_route_steps(route, command.get("indent", 0) or 0,
                                        step_code))
    return out


def _build_event(ctx: Context, spec: dict[str, Any], event_id: int,
                 x: int, y: int) -> RubyObject:
    """A stamp event as an `RPG::Event`, through the paths `event_edit` uses.

    The page patch goes through the checks a page patch gets there (the
    command list is not patchable, a field the page lacks is refused) and
    `apply_patch`; the commands go through `compile_commands`, the compiler
    behind `event_edit`'s `commands`, and get the terminating blank command.
    """
    codec = ctx.codec
    profile = ctx.project.profile
    try:
        event = normalize_strings(profile.new_event(event_id, "", x, y), codec)
        # Always, empty included: the template names an event after the id it
        # was built with, and a stamp keeps the name its event had.
        apply_patch(event, {"name": spec["name"]}, codec)
        pages = []
        for index, held in enumerate(spec["pages"]):
            where = "page %d" % index
            page = normalize_strings(profile.new_page(), codec)
            patch = held["patch"]
            refuse_fields(patch, ("list",),
                          "A page's commands are stored apart from its fields.")
            refuse_fields(patch, ("id",), "An entry's id is its own position.")
            refuse_unknown_fields(patch, known_fields(ctx, page,
                                                      "RPG::Event::Page"),
                                  where)
            apply_patch(page, patch, codec)
            try:
                commands = compile_commands(held["commands"], codec,
                                            engine=ctx.project.engine)
            except CommandError as exc:
                raise ToolError("%s commands: %s" % (where, exc)) from exc
            commands = _with_route_steps(commands, ctx.project.engine)
            commands.append(new_event_command(CODE_BLANK, 0, []))
            if len(commands) > MAX_STAMP_PAGE_COMMANDS + 1:
                raise ToolError(
                    "%s compiles to %d commands, above the %d a stamp page "
                    "keeps" % (where, len(commands), MAX_STAMP_PAGE_COMMANDS))
            page.set("list", commands)
            pages.append(page)
        event.set("pages", pages)
    except ConversionError as exc:
        raise ToolError(str(exc)) from exc
    return event


def _event_spec(ctx: Context, source_id: int, event: RubyObject,
                x0: int, y0: int) -> "tuple[dict[str, Any] | None, str]":
    """An event as a stamp entry, or None and the reason it cannot be one.

    Checked by building it back: an entry that would not paste is not kept.
    """
    codec = ctx.codec
    ex, ey = event.get("x"), event.get("y")
    pages = event.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_STAMP_PAGES:
        return None, "it has %s pages; a stamp keeps 1 to %d" % (
            len(pages) if isinstance(pages, list) else "no list of",
            MAX_STAMP_PAGES)
    walk = new_walk()
    out_pages = []
    try:
        for index, page in enumerate(pages):
            if not isinstance(page, RubyObject):
                return None, "page %d is not an event page" % index
            commands = page.get("list")
            if not isinstance(commands, list):
                return None, "page %d has no command list" % index
            if len(commands) > MAX_STAMP_PAGE_COMMANDS + 1:
                return None, ("page %d has %d commands; a stamp page keeps "
                              "at most %d" % (index, len(commands),
                                              MAX_STAMP_PAGE_COMMANDS))
            kept = list(commands)
            last = kept[-1] if kept else None
            if (isinstance(last, RubyObject) and last.get("code") == CODE_BLANK
                    and not as_list(last.get("parameters"))):
                kept.pop()
            entries = []
            for command in kept:
                if not isinstance(command, RubyObject):
                    return None, "page %d holds a command that is not one" % index
                code, indent = command.get("code"), command.get("indent", 0)
                if type(code) is not int or type(indent) is not int:
                    return None, "page %d holds a command with a odd code" % index
                if not (0 <= code <= MAX_COMMAND_CODE
                        and 0 <= indent <= MAX_AUTHORED_INDENT):
                    return None, ("page %d holds command %d at indent %d, "
                                  "outside what can be stored"
                                  % (index, code, indent))
                entries.append({"raw": {
                    "code": code, "indent": indent,
                    "parameters": to_json(as_list(command.get("parameters")),
                                          codec, walk=walk)}})
            patch = {}
            for key, value in page.ivars.items():
                bare = key[1:] if key.startswith("@") else key
                if bare != "list":
                    patch[bare] = to_json(value, codec, walk=walk)
            out_pages.append({"patch": patch, "commands": entries})
    except ConversionError as exc:
        return None, str(exc)
    spec = {"id": source_id, "name": decoded(ctx, event.get("name")),
            "x": ex - x0, "y": ey - y0, "pages": out_pages}
    try:
        _build_event(ctx, spec, source_id, ex - x0, ey - y0)
    except ToolError as exc:
        return None, "it would not paste back: %s" % exc
    return spec, ""


# What the position-dependent commands are called, per engine. VX and VX Ace
# share the numbering (and the parameter layouts, which is what the VX family's
# authoring forms rely on); XP numbers three of these differently.
_VX_CODES = {"transfer": CODE_TRANSFER_PLAYER,
             "locate": CODE_SET_EVENT_LOCATION,
             "animation": CODE_SHOW_ANIMATION, "balloon": CODE_SHOW_BALLOON}
_COMMAND_CODES = {
    "vx_ace": _VX_CODES,
    "vx": _VX_CODES,
    # XP: Transfer Player is 201 as well; Set Event Location is 202 and Show
    # Animation 207, and it has no balloon command.
    "xp": {"transfer": CODE_TRANSFER_PLAYER, "locate": 202, "animation": 207,
           "balloon": None},
}

# Move-route steps that name a direction: the moves (1-8), jump (14) and the
# turns (16-21). A mirrored or rotated stamp leaves them pointing the old way.
_DIRECTIONAL_STEPS = frozenset(list(range(1, 9)) + [14] + list(range(16, 22)))

# A move-route step that runs a Ruby line.
_ROUTE_SCRIPT = 45

# Page.move_type for a custom route.
_CUSTOM_ROUTE = 3


class _Flagger:
    """The commands a paste cannot make right, found and described.

    Reported rather than rewritten: whether a Set Event Location is meant to
    move with the stamp is not something this tool can know, and a silently
    wrong coordinate is found in play.
    """

    def __init__(self, ctx: Context, source_map: int, dest_map: "int | None",
                 turned: bool, renumbered: "dict[int, int] | None"):
        self.ctx = ctx
        self.codes = _COMMAND_CODES.get(ctx.project.engine,
                                        _COMMAND_CODES["vx_ace"])
        self.route_code = route_codes(ctx.project.engine)[0]
        self.source_map = source_map
        self.dest_map = dest_map
        self.turned = turned
        self.renumbered = renumbered
        self.rows: list[dict[str, Any]] = []
        self.truncated = False

    def scan(self, label: int, name: str, event: RubyObject) -> None:
        for page_index, page in enumerate(as_list(event.get("pages"))):
            if not isinstance(page, RubyObject):
                continue
            if page.get("move_type") == _CUSTOM_ROUTE:
                self._route(label, name, page_index, None, None,
                            page.get("move_route"), "its move route")
            for index, command in enumerate(as_list(page.get("list"))):
                if isinstance(command, RubyObject):
                    self._command(label, name, page_index, index, command)

    def _add(self, label: int, name: str, page: int, index: "int | None",
             code: "int | None", why: str) -> None:
        if len(self.rows) >= MAX_STAMP_FLAGS:
            self.truncated = True
            return
        self.rows.append({"event": label, "name": name, "page": page,
                          "command": index, "code": code,
                          "why": "%s: %s" % (
                              command_name(code, self.ctx.project.engine)
                              if code is not None else "page", why)})

    @staticmethod
    def _int(params: list, index: int) -> "int | None":
        value = params[index] if index < len(params) else None
        return value if type(value) is int else None

    def _reference(self, number: int) -> str:
        if self.renumbered is None:
            tail = "stamp events are renumbered when they are pasted"
        elif number in self.renumbered:
            tail = ("stamp event %d is now event %d"
                    % (number, self.renumbered[number]))
        else:
            tail = "event %d is not part of this stamp" % number
        return ("refers to event %d by number, and pasted events get new "
                "numbers (%s)" % (number, tail))

    def _command(self, label: int, name: str, page: int, index: int,
                 command: RubyObject) -> None:
        code = command.get("code")
        params = as_list(command.get("parameters"))
        codes = self.codes
        if code == codes["transfer"] and self._int(params, 0) == 0:
            target = self._int(params, 1)
            where = [text for text, wanted in (
                ("the map the stamp was copied from", self.source_map),
                ("the map it is pasted into", self.dest_map))
                if wanted is not None and target == wanted]
            if where:
                self._add(label, name, page, index, code,
                          "sends the player to (%s, %s) on map %d, %s, by "
                          "coordinates that do not move with the stamp"
                          % (self._int(params, 2), self._int(params, 3),
                             target, " and ".join(where)))
        elif code == codes["locate"]:
            who = self._int(params, 0)
            mode = self._int(params, 1)
            subject = ("this event" if who == 0 else "the player"
                       if who == -1 else "event %s" % who)
            what = {0: "moves %s to the absolute tile (%s, %s)" % (
                        subject, self._int(params, 2), self._int(params, 3)),
                    1: "moves %s to coordinates held in variables" % subject,
                    2: "swaps %s with another event" % subject}.get(
                mode, "moves %s" % subject)
            why = what + ", which no longer means the same place"
            if who is not None and who > 0:
                why += "; it " + self._reference(who)
            self._add(label, name, page, index, code, why)
        elif code == self.route_code:
            who = self._int(params, 0)
            if who is not None and who > 0:
                self._add(label, name, page, index, code,
                          "it " + self._reference(who))
            self._route(label, name, page, index, code,
                        params[1] if len(params) > 1 else None, "its route")
        elif code in (codes["animation"], codes["balloon"]) and code is not None:
            who = self._int(params, 0)
            if who is not None and who > 0:
                self._add(label, name, page, index, code,
                          "it " + self._reference(who))
        elif code == CODE_CONDITIONAL_BRANCH and self._int(params, 0) == 6 \
                and _vx_family(self.ctx):
            who = self._int(params, 1)
            if who is not None and who > 0:
                self._add(label, name, page, index, code,
                          "the condition " + self._reference(who))

    def _route(self, label: int, name: str, page: int, index: "int | None",
               code: "int | None", route: Any, what: str) -> None:
        if not isinstance(route, RubyObject):
            return
        steps = [step for step in as_list(route.get("list"))
                 if isinstance(step, RubyObject)]
        directional = any(step.get("code") in _DIRECTIONAL_STEPS
                          for step in steps)
        if self.turned and directional:
            self._add(label, name, page, index, code,
                      "%s has directional steps (move, jump, turn) that are "
                      "not mirrored or rotated with the stamp" % what)
        for step in steps:
            if step.get("code") != _ROUTE_SCRIPT:
                continue
            text = as_list(step.get("parameters"))
            line = decoded(self.ctx, text[0]) if text else ""
            if any(character.isdigit() for character in line):
                self._add(label, name, page, index, code,
                          "%s runs a script line that may name coordinates"
                          % what)
                break


def _vx_family(ctx: Context) -> bool:
    """Whether the open project's conditional-branch layout is the VX family's."""
    return ctx.project.engine in ("vx_ace", "vx")


def _flag_reply(flagger: _Flagger) -> dict[str, Any]:
    out: dict[str, Any] = {"position_dependent": flagger.rows}
    out.update(partial("position_dependent", truncated=flagger.truncated,
                       notes=("only the first %d are listed" % MAX_STAMP_FLAGS,)
                       if flagger.truncated else ()))
    return out


# ---------------------------------------------------------------------------
# The ops
# ---------------------------------------------------------------------------


def _table_of(game_map: RubyObject, map_id: int) -> Table:
    data = game_map.get("data")
    if not isinstance(data, Table):
        raise ToolError("map %d has no tile table" % map_id)
    if data.xsize < 1 or data.ysize < 1:
        raise ToolError(
            "map %d has a tile table of %dx%d, so it has no cells to copy or "
            "paste into. Give it a size with map_edit op=\"resize\" first."
            % (map_id, data.xsize, data.ysize))
    return data


def _layers_arg(ctx: Context, args: dict[str, Any], data: Table) -> list[int]:
    deepest = META_LAYER if ctx.project.profile.has_region_layer \
        else TILE_LAYER_COUNT - 1
    available = [z for z in range(min(data.zsize, deepest + 1))]
    if args.get("layers") is None:
        return available
    wanted = []
    for index, value in enumerate(opt_list(args, "layers")):
        z = as_int(value, "layers[%d]" % index, 0, META_LAYER)
        if z not in available:
            raise ToolError(
                "layer %d does not exist here; this map has layers %s"
                % (z, ", ".join(str(a) for a in available)))
        if z in wanted:
            raise ToolError("layers names layer %d twice" % z)
        wanted.append(z)
    if not wanted:
        raise ToolError("layers needs at least one layer; leave it out for all")
    return sorted(wanted)


def _events_in(game_map: RubyObject, x: int, y: int, width: int,
               height: int) -> "list[tuple[int, RubyObject]]":
    """The map's events standing inside a rectangle, by id."""
    events = game_map.get("events")
    if events is None:
        return []
    if not isinstance(events, RubyHash):
        raise ToolError(
            "this map holds its events as %s rather than a Hash, so they "
            "cannot be copied. Pass include_events=false to copy the tiles."
            % type(events).__name__)
    budget = WalkBudget()
    found = []
    for key, event in events.pairs:
        if not budget.spend(1):
            raise ToolError(budget.note("a stamp") or "the walk stopped")
        if (type(key) is int and isinstance(event, RubyObject)
                and type(event.get("x")) is int and type(event.get("y")) is int
                and x <= event.get("x") < x + width
                and y <= event.get("y") < y + height):
            found.append((key, event))
    return sorted(found, key=lambda pair: pair[0])


def _op_copy(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    name = _stamp_name(need_str(args, "name"))
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    dry_run = opt_bool(args, "dry_run")
    include_events = opt_bool(args, "include_events", True)
    if not dry_run:
        ctx.project._assert_writable("saving stamp %s" % name)
    game_map = ctx.project.load_map(map_id)
    data = _table_of(game_map, map_id)
    x = opt_int_default(args, "x", 0, minimum=0, maximum=data.xsize - 1)
    y = opt_int_default(args, "y", 0, minimum=0, maximum=data.ysize - 1)
    width = opt_int_default(args, "width", data.xsize - x, minimum=1)
    height = opt_int_default(args, "height", data.ysize - y, minimum=1)
    if x + width > data.xsize or y + height > data.ysize:
        raise ToolError("the rectangle (%d, %d) %dx%d does not fit inside "
                        "this %dx%d map" % (x, y, width, height,
                                            data.xsize, data.ysize))
    if width * height > MAX_STAMP_CELLS:
        raise ToolError(
            "that rectangle is %d cells, above the %d one stamp holds (width "
            "times height). Copy a smaller area." % (width * height,
                                                     MAX_STAMP_CELLS))
    layers = _layers_arg(ctx, args, data)

    folder, path = _stamp_path(ctx, name)
    if path.exists():
        raise ToolError("a stamp named '%s' already exists. Delete it first, "
                        "or choose another name." % name)
    kept, _gave_up = _stamp_files(folder)
    if len(kept) >= MAX_STAMPS:
        raise ToolError("the stamp folder already holds %d stamps, the most "
                        "this server keeps. Delete one first." % len(kept))

    plane = data.xsize * data.ysize
    cells: dict[str, list[int]] = {}
    for z in layers:
        flat: list[int] = []
        for dy in range(height):
            start = z * plane + (y + dy) * data.xsize + x
            flat.extend(data.data[start:start + width])
        cells[str(z)] = flat

    found = _events_in(game_map, x, y, width, height) if include_events else []
    if len(found) > MAX_STAMP_EVENTS:
        raise ToolError(
            "that rectangle holds %d events, above the %d one stamp keeps. "
            "Copy a smaller area, or pass include_events=false for the tiles "
            "alone." % (len(found), MAX_STAMP_EVENTS))
    specs, not_read, protos = [], {}, []
    for source_id, event in found:
        spec, reason = _event_spec(ctx, source_id, event, x, y)
        label = "event %d (%s)" % (source_id, decoded(ctx, event.get("name")))
        if spec is None:
            not_read[label] = reason
        else:
            specs.append(spec)
            protos.append((spec, event))

    stamp = {"format": STAMP_FORMAT, "version": STAMP_VERSION,
             "engine": ctx.project.engine,
             "source": {"map_id": map_id, "x": x, "y": y},
             "width": width, "height": height, "layers": layers,
             "cells": cells, "events": specs}
    body = json.dumps(stamp, separators=(",", ":")).encode("ascii")
    if len(body) > MAX_STAMP_FILE_BYTES:
        raise ToolError(
            "that stamp would be %d bytes, above the %d a stamp file may "
            "hold. Copy a smaller area, or pass include_events=false."
            % (len(body), MAX_STAMP_FILE_BYTES))

    flagger = _Flagger(ctx, map_id, None, False, None)
    for spec, event in protos:
        flagger.scan(spec["id"], spec["name"], event)
    if not dry_run:
        _write_stamp(folder, path, name, body)
    reply: dict[str, Any] = {
        "name": name,
        "stamp": {"map_id": map_id, "x": x, "y": y, "width": width,
                  "height": height, "layers": layers, "cells": width * height,
                  "events": len(specs)},
        "created": _relative(ctx, path),
        "bytes": len(body),
        "events_copied": [{"id": s["id"], "name": s["name"]} for s in specs],
    }
    reply.update(partial("events", not_read=not_read))
    reply.update(_flag_reply(flagger))
    return write_reply(reply, dry_run)


def _write_stamp(folder: Path, path: Path, name: str, body: bytes) -> None:
    """Put a stamp on disk: a new temp file of our own naming, then a rename."""
    folder.mkdir(parents=True, exist_ok=True)
    _contained(folder, folder.parent.parent, "the stamp folder")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=folder, prefix=name + ".", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _orientation(args: dict[str, Any]) -> Orientation:
    mirror = one_of(args, "mirror", list(MIRRORS), "none")
    rotate = as_int(args["rotate"], "rotate") if args.get("rotate") is not None \
        else 0
    if rotate not in ROTATIONS:
        raise ToolError("rotate must be one of %s (degrees clockwise), got %s"
                        % (", ".join(str(r) for r in ROTATIONS), echo(rotate)))
    return Orientation(mirror, rotate)


def _op_paste(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    name = _stamp_name(need_str(args, "name"))
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    x = need_int(args, "x", minimum=0, maximum=MAX_MAP_ID)
    y = need_int(args, "y", minimum=0, maximum=MAX_MAP_ID)
    turn = _orientation(args)
    include_events = opt_bool(args, "include_events", True)
    reshape_edges = opt_bool(args, "reshape_edges", True)
    dry_run = opt_bool(args, "dry_run")

    stamp = _load_stamp(ctx, name)
    engine = ctx.project.engine
    if not turn.identity and engine != "vx_ace":
        raise ToolError(
            "mirror and rotate need the autotile shapes re-derived for the "
            "new orientation, and that model exists for VX Ace only. This is "
            "a %s project; paste the stamp as it is." % engine)
    game_map = ctx.project.load_map(map_id)
    data = _table_of(game_map, map_id)
    if x > data.xsize - 1 or y > data.ysize - 1:
        raise ToolError("(%d, %d) is outside this %dx%d map: x must be at "
                        "most %d and y at most %d"
                        % (x, y, data.xsize, data.ysize, data.xsize - 1,
                           data.ysize - 1))
    for z in stamp.layers:
        if z >= data.zsize:
            raise ToolError("the stamp carries layer %d and map %d has layers "
                            "0..%d" % (z, map_id, data.zsize - 1))

    new_width, new_height = turn.size(stamp.width, stamp.height)
    inside_width = min(new_width, data.xsize - x)
    inside_height = min(new_height, data.ysize - y)
    clipped = (new_width * new_height - inside_width * inside_height) \
        * len(stamp.layers)

    filename = ctx.project.map_file(map_id)
    with ctx.staged(filename) as landed:
        painted = _paint(data, stamp, turn, x, y, inside_width, inside_height,
                         new_width)
        created, skipped, flagger = _place_events(
            ctx, game_map, stamp, turn, x, y, map_id,
            include_events and bool(stamp.events))
        reshaped = (_reshape(data, stamp, turn, x, y, inside_width,
                             inside_height, reshape_edges)
                    if engine == "vx_ace" else 0)
        summary = ("pasted stamp %s (%dx%d%s) at (%d, %d)"
                   % (name, new_width, new_height,
                      "" if turn.identity else ", mirror %s rotate %d"
                      % (turn.mirror, turn.rotate), x, y))
        write = landed.save(filename, dry_run=dry_run, tool="map_stamp",
                            summary=summary)

    reply: dict[str, Any] = {
        "map_id": map_id, "stamp": name,
        "placed": {"x": x, "y": y, "width": new_width, "height": new_height,
                   "mirror": turn.mirror, "rotate": turn.rotate},
        "painted": painted, "reshaped": reshaped, "clipped_cells": clipped,
        "created": created, "changed": summary, "write": write,
    }
    notes = []
    if clipped:
        notes.append("%d cells fell outside the map and were not written"
                     % clipped)
    reply.update(partial("cells", truncated=bool(clipped), notes=notes))
    if skipped:
        reply["events_skipped"] = skipped
    event_notes = []
    if skipped:
        event_notes.append("%d events were not pasted; events_skipped says "
                           "why" % len(skipped))
    if stamp.events and not include_events:
        event_notes.append("include_events=false: the stamp's %d events were "
                           "not pasted" % len(stamp.events))
    reply.update(partial("events", truncated=bool(skipped),
                         notes=event_notes))
    reply.update(_flag_reply(flagger))
    return write_reply(reply, dry_run)


def _paint(data: Table, stamp: _Stamp, turn: Orientation, x: int, y: int,
           inside_width: int, inside_height: int, new_width: int) -> int:
    """Write the stamp's layers onto the table, as much as lies on the map."""
    buffer = data.data
    plane = data.xsize * data.ysize
    painted = 0
    for z in stamp.layers:
        grid = turn_cells(stamp.cells[z], stamp.width, stamp.height, turn,
                          z == META_LAYER)
        for dy in range(inside_height):
            source = dy * new_width
            start = z * plane + (y + dy) * data.xsize + x
            buffer[start:start + inside_width] = array(
                buffer.typecode, grid[source:source + inside_width])
        painted += inside_width * inside_height
    return painted


def _reshape(data: Table, stamp: _Stamp, turn: Orientation, x: int, y: int,
             inside_width: int, inside_height: int, edges: bool) -> int:
    """Re-shape the autotiles a paste touched, by the rules `map_paint` uses.

    With ``edges`` the pasted rectangle and the ring around it (and the wall
    runs beside those) are re-shaped, so the paste joins what it landed
    beside. Without, the neighbours are left alone -- but a turned stamp's own
    cells still are, because the shape they carry was chosen for another
    orientation and is simply wrong in this one.
    """
    changed = 0
    rectangle = [(x + dx, y + dy) for dy in range(inside_height)
                 for dx in range(inside_width)]
    for z in stamp.layers:
        if z >= TILE_LAYER_COUNT:
            continue
        layer = Layer(data.data, data.xsize, data.ysize, z)
        if edges:
            around = neighbourhood(layer, rectangle)
            if len(around) > MAX_STAMP_RESHAPE_CELLS:
                raise ToolError(
                    "that paste would re-shape %d cells -- what it painted, "
                    "the cells around it and the wall runs beside them -- "
                    "above the %d one call re-shapes. Paste a smaller stamp."
                    % (len(around), MAX_STAMP_RESHAPE_CELLS))
            changed += reshape(layer, around)
        elif not turn.identity:
            changed += reshape(layer, rectangle)
    return changed


def _place_events(ctx: Context, game_map: RubyObject, stamp: _Stamp,
                  turn: Orientation, x: int, y: int, map_id: int,
                  wanted: bool) -> "tuple[list, list, _Flagger]":
    """Add the stamp's events to the map; skip, with a reason, those that fit no cell.

    Never overwrites: an event whose cell holds another is skipped and says
    which one. Ids are the lowest free ones, in the stamp's order.
    """
    flagger = _Flagger(ctx, stamp.source_map, map_id, not turn.identity, {})
    if not wanted:
        return [], [], flagger
    events = game_map.get("events")
    if events is None:
        events = RubyHash()
        game_map.set("events", events)
    elif not isinstance(events, RubyHash):
        raise ToolError(
            "map %d holds its events as %s rather than a Hash, so events "
            "cannot be added without discarding what is there. Pass "
            "include_events=false, or run validate." % (
                map_id, type(events).__name__))
    budget = WalkBudget()
    used: set[int] = set()
    occupied: dict[tuple[int, int], int] = {}
    for key, event in events.pairs:
        if not budget.spend(1):
            raise ToolError(budget.note("a stamp") or "the walk stopped")
        if type(key) is int:
            used.add(key)
            if isinstance(event, RubyObject) and type(event.get("x")) is int \
                    and type(event.get("y")) is int:
                occupied.setdefault((event.get("x"), event.get("y")), key)
    width, height = game_map.get("width"), game_map.get("height")
    created, skipped = [], []
    next_id = 1
    for spec, proto in zip(stamp.events, stamp.protos):
        tx, ty = turn.place(spec["x"], spec["y"], stamp.width, stamp.height)
        at = (x + tx, y + ty)
        row = {"name": spec["name"], "x": at[0], "y": at[1]}
        if not on_map(at[0], at[1], width, height):
            skipped.append({**row, "reason": "it would stand at (%d, %d), "
                            "outside the map" % at})
            continue
        if at in occupied:
            skipped.append({**row, "reason": "the cell (%d, %d) already holds "
                            "event %d, and a paste never overwrites an event"
                            % (at[0], at[1], occupied[at])})
            continue
        while next_id in used:
            next_id += 1
        if next_id > _EVENT_ID_UPPER:
            skipped.append({**row, "reason": "this map has no free event id "
                            "left (the most is %d)" % _EVENT_ID_UPPER})
            continue
        new_id = next_id
        used.add(new_id)
        proto.set("id", new_id)
        proto.set("x", at[0])
        proto.set("y", at[1])
        if not turn.identity:
            for page in as_list(proto.get("pages")):
                graphic = page.get("graphic")
                if isinstance(graphic, RubyObject) and graphic.has("direction"):
                    graphic.set("direction",
                                turn.facing(graphic.get("direction")))
        events.set(new_id, proto)
        flagger.renumbered[spec["id"]] = new_id
        flagger.scan(new_id, spec["name"], proto)
        created.append({"id": new_id, "name": spec["name"], "x": at[0],
                        "y": at[1]})
    return created, skipped, flagger


def _op_list(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    folder = _stamp_folder(ctx)
    names, gave_up = _stamp_files(folder)
    shown = names[:MAX_STAMPS]
    left = MAX_STAMP_LIST_BYTES
    rows: list[dict[str, Any]] = []
    not_read: dict[str, str] = {}
    for name in shown:
        path = folder / (name + ".json")
        row: dict[str, Any] = {"name": name}
        rows.append(row)
        try:
            if _is_reparse_point(path):
                not_read[name] = "it is a link, which is not followed"
                continue
            size = path.stat().st_size
            row["bytes"] = size
            if size > MAX_STAMP_FILE_BYTES:
                not_read[name] = ("%d bytes, above the %d a stamp file may "
                                  "hold" % (size, MAX_STAMP_FILE_BYTES))
                continue
            if size > left:
                not_read[name] = ("not opened: a listing reads at most %d "
                                  "bytes in all" % MAX_STAMP_LIST_BYTES)
                continue
            left -= size
            data = _parse_json(_read_stamp_bytes(path, name), name)
            row.update(_summary(data))
        except (OSError, ToolError, KeyError, TypeError, ValueError) as exc:
            not_read[name] = echo(str(exc))
    reply: dict[str, Any] = {"stamps": rows}
    truncated = len(names) > MAX_STAMPS or gave_up
    reply.update(partial(
        "stamps", truncated=truncated, not_read=not_read,
        notes=("only the first %d of the stamp files are listed"
               % MAX_STAMPS,) if truncated else ()))
    return reply


def _summary(data: Any) -> dict[str, Any]:
    """What a listing says about a stamp, read without trusting it."""
    if not isinstance(data, dict):
        raise ValueError("the file is not an object")
    source = data["source"]
    out = {"engine": data["engine"], "width": data["width"],
           "height": data["height"], "layers": data["layers"],
           "events": len(data["events"]), "source_map_id": source["map_id"]}
    if (not isinstance(out["engine"], str) or not isinstance(out["layers"], list)
            or any(type(out[key]) is not int
                   for key in ("width", "height", "source_map_id"))):
        raise ValueError("the file's header is not shaped like a stamp")
    out["engine"] = out["engine"][:20]
    out["layers"] = [z for z in out["layers"] if type(z) is int][:4]
    return out


def _op_delete(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    name = _stamp_name(need_str(args, "name"))
    dry_run = opt_bool(args, "dry_run")
    if not dry_run:
        ctx.project._assert_writable("deleting stamp %s" % name)
    # Not `_stamp_path`: that refuses a link, and a link planted in the folder
    # is something to be able to delete. `unlink` removes the link itself.
    path = _stamp_folder(ctx) / (name + ".json")
    if not (path.is_file() or path.is_symlink()):
        raise ToolError("there is no stamp named '%s'. map_stamp op=list shows "
                        "the stamps there are." % name)
    if not dry_run:
        path.unlink()
    return write_reply({"name": name, "deleted": _relative(ctx, path)},
                       dry_run)


_OPS = {"copy": _op_copy, "paste": _op_paste, "list": _op_list,
        "delete": _op_delete}


@tool(
    "map_stamp",
    "Copy a piece of a map under a name and paste it anywhere",
    "Keep a rectangle of a map as a named stamp and paste it back later, onto "
    "the same map or another, optionally mirrored or rotated. copy stores the "
    "tile ids of the layers asked for (default every layer the map has, "
    "including VX Ace's shadow and region layer 3) and the events standing in "
    "the rectangle; paste writes it with its top-left at x, y; list shows the "
    "stamps; delete removes one. Stamps are JSON files in "
    ".rpgvxace-mcp/stamps/ inside the project, named with 1 to %d letters, "
    "digits, dashes or underscores; they are not backed up, and copy never "
    "overwrites a name. Ceilings: a stamp holds at most %d cells (width times "
    "height), %d events of at most %d pages and %d commands a page, a file at "
    "most %s bytes, and the folder at most %d stamps; list opens at most %s "
    "bytes in all, and a reply lists at most %d position-dependent commands. "
    "mirror (none, horizontal, vertical, both) is applied before rotate (0, "
    "90, 180, 270 degrees clockwise). A turned stamp's autotile cells are "
    "re-shaped for the new orientation by the rules map_paint uses, layer 3's "
    "shadow quarters move with the turn and its region ids do not, and "
    "reshape_edges (default true) also re-shapes the ring of cells around the "
    "paste so it joins its neighbours; with it off, only a turned stamp's own "
    "cells are re-shaped. A paste that runs past the map edge is clipped, not "
    "refused: the cells that fit are written and cells_truncated, "
    "clipped_cells and cells_note say what was left out. Events are given the "
    "lowest free ids, positions moved (and mirrored or rotated) with the "
    "stamp, their names, pages and commands as they were, and the way they "
    "face turned with it. An event that would land on another event or off the "
    "map is skipped and listed in events_skipped with the reason; a paste "
    "never overwrites or deletes an event. Commands that cannot move with a "
    "stamp are not rewritten but reported in position_dependent: Set Event "
    "Location, a Transfer Player to the map the stamp came from or is pasted "
    "into, a reference to another event by number, and (when turned) "
    "directional move routes. Not copied or changed: self-switch state, the "
    "map's tileset, music or other settings, the cells around a paste beyond "
    "re-shaping, automatic wall shadows, script commands' contents. A stamp "
    "remembers its engine and is refused on another; mirror and rotate are "
    "for VX Ace maps only. A stamp file is checked field by field before the "
    "map is touched, and a paste is one staged change that a failure leaves "
    "undone; dry_run previews. Cell counts are layer-cells: a 3x2 stamp of "
    "four layers is 24." % (
        MAX_STAMP_NAME, MAX_STAMP_CELLS, MAX_STAMP_EVENTS, MAX_STAMP_PAGES,
        MAX_STAMP_PAGE_COMMANDS, "{:,}".format(MAX_STAMP_FILE_BYTES),
        MAX_STAMPS, "{:,}".format(MAX_STAMP_LIST_BYTES), MAX_STAMP_FLAGS)
    + " A paste re-shapes at most %s cells."
    % "{:,}".format(MAX_STAMP_RESHAPE_CELLS),
    schema({
        "op": prop(STR, "copy, paste, list or delete.",
                   enum=list(STAMP_OP_ARGS)),
        "name": prop(STR, "The stamp's name: 1 to %d letters, digits, dashes "
                          "or underscores, starting with a letter or digit."
                     % MAX_STAMP_NAME, maxLength=MAX_STAMP_NAME),
        "map_id": prop(INT, "copy: the map to copy from. paste: the map to "
                            "paste onto.", minimum=1, maximum=MAX_MAP_ID),
        "x": prop(INT, "copy: left edge of the rectangle (default 0). paste: "
                       "the column the stamp's top-left lands on (required).",
                  minimum=0, maximum=MAX_MAP_ID),
        "y": prop(INT, "copy: top edge of the rectangle (default 0). paste: "
                       "the row the stamp's top-left lands on (required).",
                  minimum=0, maximum=MAX_MAP_ID),
        "width": prop(INT, "copy: rectangle width (default: to the map's "
                           "right edge). Width times height is at most %d."
                      % MAX_STAMP_CELLS, minimum=1, maximum=MAX_MAP_ID),
        "height": prop(INT, "copy: rectangle height (default: to the map's "
                            "bottom edge).", minimum=1, maximum=MAX_MAP_ID),
        "layers": prop(ARR, "copy: which layers to keep, from 0 (ground), 1, "
                            "2 and, on VX Ace, 3 (shadow and region). "
                            "Default: all the map has.",
                       maxItems=4, items={"type": INT, "minimum": 0,
                                          "maximum": META_LAYER}),
        "include_events": prop(BOOL, "copy: keep the events in the rectangle "
                                     "(default true). paste: add the "
                                     "stamp's events (default true)."),
        "mirror": prop(STR, "paste: flip the stamp before placing it (default "
                            "none).", enum=list(MIRRORS)),
        "rotate": prop(INT, "paste: degrees clockwise after the mirror: 0 "
                            "(default), 90, 180 or 270.",
                       enum=list(ROTATIONS)),
        "reshape_edges": prop(BOOL, "paste: also re-shape the ring of "
                                    "autotile cells around the paste so it "
                                    "joins them (default true)."),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def map_stamp(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Copy, paste, list or delete stamps. See the tool description."""
    op = one_of(args, "op", list(STAMP_OP_ARGS))
    check_op_args(op, args, STAMP_OP_ARGS, "map_stamp")
    return _OPS[op](ctx, args)
