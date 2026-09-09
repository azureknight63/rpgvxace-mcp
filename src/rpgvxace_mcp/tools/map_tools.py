"""Map tools: the map tree, map properties and tile data."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..convert import (
    TABLE_MODES,
    apply_patch,
    new_walk,
    normalize_strings,
    to_json,
)
from ..analysis import reference_preview
from ..project import MAX_MAP_ID, ProjectError
from ..rubytypes import CELL_MASK, RubyHash, RubyObject, RubyString, Table
from ..schema import TILE_ID_RANGES, TRIGGER_NAMES, new_map_info
from .registry import (
    refuse_unknown_fields,
    check_op_args,
    known_fields,
    refuse_fields,
    StagedWrite,
    as_int,
    ARR,
    BOOL,
    Context,
    DRY_RUN,
    INT,
    OBJ,
    REFRESH,
    STR,
    ToolError,
    need_int,
    one_of,
    opt_bool,
    opt_dict,
    opt_int,
    opt_list,
    opt_str,
    prop,
    schema,
    tool,
)

# Map properties that are simple values worth showing without asking.
MAP_PROPERTY_FIELDS = (
    "width", "height", "scroll_type", "autoplay_bgm", "autoplay_bgs",
    "disable_dashing", "encounter_step", "encounter_list", "parallax_name",
    "parallax_loop_x", "parallax_loop_y", "parallax_sx", "parallax_sy",
    "parallax_show",
)


# The largest map side this server will create or resize to. The editor's own
# ceiling is 500; this leaves room above it while refusing the extents that
# would build a multi-megabyte tile table out of one argument.
MAX_MAP_SIDE = 1000

# What a new map is sized as when the caller does not say. The editor's own
# default for a new map, and small enough to be cheap to create.
DEFAULT_MAP_WIDTH, DEFAULT_MAP_HEIGHT = 20, 15

# Map fields a patch must not touch, because something else owns the
# invariants they are part of: setting @width alone leaves a map claiming a
# size its @data does not have, which is a file the editor refuses to open.
#
# "events" is here because a patch merges field by field, so
# `patch={"events": {"1": {"x": 99999}}}` reached straight into the event
# object and moved it off the map, past event_edit's bounds check -- while the
# refusal message printed right here already told the caller to use event_edit
# for events. raw_data's list had "events" and this one did not, which is the
# drift that made the two disagree; there is now one list, and raw_data imports
# it.
STRUCTURAL_MAP_FIELDS = ("width", "height", "data", "events")


# How many cells one windowed read returns or one write covers. The whole of a
# large map is more than a caller can use and more than the response can carry.
MAX_CELLS_PER_READ = 20000

# What a whole-table walk may cover. Larger, because these are not windowed:
# a caller cannot ask for less of a tile tally or of a resize, so refusing one
# at the windowed ceiling would refuse a legitimate map -- MAX_MAP_SIDE allows
# 1000x1000, and a 100x100x4 map is already 40000 cells. This still refuses the
# extents only a hand-crafted file declares.
MAX_CELLS_PER_WALK = 4_000_000


# How many tile ids one layer's tally lists. A layer can hold hundreds of
# distinct ids and a caller cannot use them all; what matters is that the reply
# says how many it did not show, because the tool used to promise "which tile
# ids appear on each layer" and return only these.
TOP_TILE_IDS = 12

# The highest value each layer can hold. Layers 0 and 1 carry tile ids; layer 2
# is a four-bit shadow mask; layer 3 on VX Ace holds region ids, which the
# editor numbers 0 to 63.
LAYER_CEILINGS = {2: 15, 3: 63}


# What each op reads. This replaces a hand-written check that named only
# width and height and ran only after the create and delete paths had already
# returned -- so op "delete" accepted a width, a name and a patch, ignored all
# three, and reported the delete as a plain success.
#
# "resize" reads name and patch because renaming or patching alongside a
# resize is one file write either way; "delete" reads nothing but the id.
MAP_OP_ARGS = {
    "create": {"op", "dry_run", "map_id", "name", "parent_id", "width",
               "height", "tileset_id", "patch"},
    "update": {"op", "dry_run", "map_id", "name", "patch"},
    "delete": {"op", "dry_run", "map_id"},
    "resize": {"op", "dry_run", "map_id", "name", "width", "height", "patch"},
}


# How much tile data map_get includes. Named once, because the schema that
# advertises it and the reader that enforces it are 40 lines apart.
TILE_DETAIL = ("none", "counts")

# What each map_tiles op reads. "read" writes nothing, so it takes no
# dry_run; "fill" paints one id everywhere and "write" takes a grid, and each
# used to accept the other's argument and ignore it -- a fill carrying rows
# reported "filled ..." and never looked at them.
TILE_OP_ARGS = {
    "read": {"op", "map_id", "x", "y", "width", "height", "z"},
    "fill": {"op", "map_id", "x", "y", "width", "height", "z", "tile_id",
             "dry_run"},
    "write": {"op", "map_id", "x", "y", "width", "height", "z", "rows",
              "dry_run"},
}


def _name(ctx: Context, value: Any) -> str:
    return ctx.codec.decode(value.data) if isinstance(value, RubyString) else ""


def _map_names(ctx: Context) -> dict[int, str]:
    names = {}
    try:
        for map_id, info in ctx.project.entries("map_infos"):
            if isinstance(info, RubyObject):
                names[map_id] = _name(ctx, info.get("name"))
    except (ProjectError, ValueError):
        # map_list reports the tree itself and validate reports the file, so an
        # empty mapping here means "no names to show" rather than hiding a bug.
        pass
    return names


@tool(
    "map_list",
    "List maps",
    "List the map tree from MapInfos: id, name, parent and whether the map file "
    "is present. Pass include_details to also load each map for its size and "
    "event count.",
    schema({
        "include_details": prop(BOOL, "Load every map file to report size, event "
                                      "count and BGM. Slower on large projects."),
        "parent_id": prop(INT, "Only list children of this map id (0 for the root)."),
        "refresh": REFRESH,
    }),
)
def map_list(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """The map tree, and which of its entries have a file. Reads only."""
    refresh = opt_bool(args, "refresh")
    details = opt_bool(args, "include_details")
    parent_filter = opt_int(args, "parent_id")

    infos = ctx.project.entries("map_infos", refresh)
    on_disk = set(ctx.project.map_ids_on_disk())
    rows = []
    for map_id, info in infos:
        if not isinstance(info, RubyObject):
            continue
        parent = info.get("parent_id") or 0
        if parent_filter is not None and parent != parent_filter:
            continue
        row: dict[str, Any] = {
            "id": map_id,
            "name": _name(ctx, info.get("name")),
            "parent_id": parent,
            "order": info.get("order"),
            "file": ctx.project.map_file(map_id),
            "file_present": map_id in on_disk,
        }
        if details and map_id in on_disk:
            try:
                game_map = ctx.project.load_map(map_id, refresh)
                events = game_map.get("events")
                row["width"] = game_map.get("width")
                row["height"] = game_map.get("height")
                row["events"] = len(events) if isinstance(events, RubyHash) else 0
                unreadable = unreadable_events_note(events)
                if unreadable:
                    # Rather than the 0 above standing unexplained: a count of
                    # zero from a field that could not be read is the one
                    # reading a caller cannot check.
                    row["events_note"] = unreadable
                bgm = game_map.get("bgm")
                if isinstance(bgm, RubyObject) and game_map.get("autoplay_bgm"):
                    row["bgm"] = _name(ctx, bgm.get("name"))
            except (ProjectError, ValueError) as exc:
                row["error"] = str(exc)
        rows.append(row)

    # Over the whole tree, whatever the filter. This used to be an empty list
    # whenever a filter was given, without looking, so a filtered call reported
    # "no orphan map files" from a check that never ran -- and it is one set
    # difference, so there was nothing to save.
    orphans = sorted(on_disk - {i for i, _ in ctx.project.entries("map_infos")})
    return {
        "maps": rows,
        # Named for what it counted. "total" read as the size of the tree, so
        # a filtered listing looked like the whole of a much smaller project --
        # the same rename db_list already carries.
        "matching_maps": len(rows),
        "map_files_without_tree_entry": orphans,
        "start_position": _start_position(ctx),
    }


def _start_position(ctx: Context) -> dict[str, Any] | None:
    try:
        system = ctx.project.load_data("system")
    except (ProjectError, ValueError):
        # validate reports an unreadable System; map_list just has no start
        # position to show.
        return None
    if not isinstance(system, RubyObject):
        return None
    return {
        "map_id": system.get("start_map_id"),
        "x": system.get("start_x"),
        "y": system.get("start_y"),
    }


@tool(
    "map_get",
    "Read a map's properties and events",
    "Read one map: its properties, its events in summary, and optionally a "
    "breakdown of which tile ids it uses. Use event_get for the commands inside "
    "an event, and map_tiles to read the tile grid itself.",
    schema({
        "map_id": prop(INT, "Which map to read.", minimum=1,
                       maximum=MAX_MAP_ID),
        "include_events": prop(BOOL, "Include a summary of each event (default true)."),
        "tiles": prop(STR, "Tile reporting: none (default), or counts -- the "
                           "%d most common tile ids on each layer with how "
                           "often each appears, how many distinct ids the "
                           "layer holds, and how many of them are not listed. "
                           "Not every id: a layer can hold thousands, and "
                           "\"tile_ids_not_shown\" says how many were left "
                           "out. A tally covers the whole tile table by "
                           "definition, so it is refused above %s cells -- "
                           "there is no smaller window to ask for, and "
                           "map_tiles cannot count either."
                           % (TOP_TILE_IDS,
                              "{:,}".format(MAX_CELLS_PER_WALK)),
                      enum=list(TILE_DETAIL)),
        "refresh": REFRESH,
    }, ["map_id"]),
)
def map_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """One map: properties, events in summary, optional tile tally."""
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    refresh = opt_bool(args, "refresh")
    game_map = ctx.project.load_map(map_id, refresh)
    names = _map_names(ctx)

    # One conversion budget for the whole reply, not one per field.
    walk = new_walk()
    properties = {}
    for field in MAP_PROPERTY_FIELDS:
        if game_map.has(field):
            properties[field] = to_json(game_map.get(field), ctx.codec,
                                        TABLE_MODES[0], walk)
    for field in ("bgm", "bgs"):
        track = game_map.get(field)
        if isinstance(track, RubyObject):
            properties[field] = to_json(track, ctx.codec,
                                        TABLE_MODES[0], walk)

    result: dict[str, Any] = {
        "map_id": map_id,
        "name": names.get(map_id, ""),
        "file": ctx.project.map_file(map_id),
        "properties": properties,
        # Not "unknown": a stock VX Ace map lists tileset_id, display_name,
        # note and the battleback fields here, all of them documented
        # RPG::Map fields. They are simply the ones this tool does not
        # summarise, which is what the name now says -- read them with
        # raw_data or project_info include_schema.
        "other_fields": [f for f in game_map.fields()
                         if f not in MAP_PROPERTY_FIELDS
                         and f not in ("bgm", "bgs", "data", "events")],
    }

    data = game_map.get("data")
    # Resolved before the branch, so an invalid value is refused even on a
    # map with no tile table -- and so asking for counts that cannot be
    # produced is answered rather than dropped.
    tiles = one_of(args, "tiles", list(TILE_DETAIL), TILE_DETAIL[0])
    if not isinstance(data, Table):
        if tiles == "counts":
            result["tile_counts_note"] = (
                "this map holds its tile data as %s rather than a Table, so "
                "there was nothing to count. Run validate to see what is "
                "wrong with the file." % type(data).__name__)
    if isinstance(data, Table):
        result["tile_table"] = {
            "xsize": data.xsize, "ysize": data.ysize, "zsize": data.zsize,
            "layers": {str(z): ctx.project.profile.map_layers.get(
                           z, "layer %d (not used by this engine)" % z)
                       for z in range(data.zsize)},
        }
        if tiles == "counts":
            result["tile_counts"] = _tile_counts(data)

    if opt_bool(args, "include_events", True):
        result["events"], cut_short = _event_summaries(ctx, game_map)
        if cut_short:
            result["events_truncated"] = (
                "this map describes more than %d event pages, so the summary "
                "stops there. A map that large is usually a file that shares "
                "one page object across many slots rather than a big map -- "
                "run validate. Read particular events with event_get."
                % MAX_EVENT_SUMMARY_ROWS)
        unreadable = unreadable_events_note(game_map.get("events"))
        if unreadable:
            result["events_note"] = unreadable
    warnings = ctx.project.file_warnings(ctx.project.map_file(map_id))
    if warnings:
        result["file_warnings"] = warnings
    return result


def _tile_counts(data: Table) -> dict[str, Any]:
    """How often each tile id appears on each layer.

    Bounded like every other whole-table walk: this is a per-cell Python loop,
    and a crafted map can declare millions of cells.
    """
    _check_budget(
        data.xsize * data.ysize * data.zsize, "count the tiles of",
        windowed=False,
        remedy="A tally covers the whole table by definition, so there is no "
               "smaller window to ask for and map_tiles cannot count either. "
               "Read the layers you care about in pieces with map_tiles and "
               "count those.")
    out = {}
    for z in range(data.zsize):
        counter: Counter[int] = Counter()
        for y in range(data.ysize):
            for x in range(data.xsize):
                counter[data.get(x, y, z)] += 1
        out[str(z)] = {
            "distinct_tile_ids": len(counter),
            "most_common": [{"tile_id": tile, "count": count,
                             "sheet": _tile_sheet(tile)}
                            for tile, count in counter.most_common(TOP_TILE_IDS)],
            # Said in the reply, not only in the description: a caller asking
            # whether one tile is used anywhere read a short list, did not find
            # it, and had nothing to tell it the list was not the whole answer.
            "tile_ids_not_shown": max(0, len(counter) - TOP_TILE_IDS),
        }
    return out


def _tile_sheet(tile_id: int) -> str:
    if tile_id == 0:
        return "empty"
    for sheet, (low, high) in TILE_ID_RANGES.items():
        if low <= tile_id <= high:
            return sheet
    return "unknown"


def unreadable_events_note(events: Any) -> "str | None":
    """What to say about an @events that is not a Hash, or None if it is fine.

    `event_edit` refuses such a map by name; the three read tools returned an
    empty list, so "we could not read this" and "there are none" looked
    identical and a model concluded the map has no events. One sentence, in
    one place, because three tools were reporting the same fact three ways --
    which is to say, not reporting it at all.
    """
    if events is None or isinstance(events, RubyHash):
        return None
    return ("this map holds its events as %s rather than a Hash, so none "
            "could be read. Run validate to see what is wrong with the file."
            % type(events).__name__)


# How many event page rows one map_get summary may build. Events times pages,
# and both numbers come out of the file: the shared-object map is fifty events
# of fifty pages, all one object, so 7 KB produced 2500 rows -- and nothing in
# the reply said the list was long because the file was crafted rather than
# because the map is big. Every other whole-file walk in this module goes
# through `_check_budget`; this one went through nothing.
MAX_EVENT_SUMMARY_ROWS = 2000


def _event_summaries(ctx: Context, game_map: RubyObject
                     ) -> tuple[list[dict[str, Any]], bool]:
    """Each event on a map in summary, and whether the list was cut short.

    Returns the flag rather than noting it here, because the caller is the one
    that owns the reply -- and a summary that stopped and does not say so is
    the shape this module spends most of its comments avoiding.
    """
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        return [], False
    out = []
    rows = 0
    for event_id, event in events.sorted_items():
        if rows >= MAX_EVENT_SUMMARY_ROWS:
            return out, True
        if not isinstance(event, RubyObject):
            continue
        pages = event.get("pages") or []
        page_rows = []
        for index, page in enumerate(pages):
            if rows >= MAX_EVENT_SUMMARY_ROWS:
                break
            if not isinstance(page, RubyObject):
                continue
            rows += 1
            commands = page.get("list") or []
            graphic = page.get("graphic")
            page_rows.append({
                "page": index,
                "trigger": TRIGGER_NAMES.get(page.get("trigger"), page.get("trigger")),
                "commands": len(commands),
                "graphic": _name(ctx, graphic.get("character_name"))
                if isinstance(graphic, RubyObject) else "",
                "conditions": _conditions(page.get("condition")),
            })
        out.append({
            "id": event_id,
            "name": _name(ctx, event.get("name")),
            "x": event.get("x"),
            "y": event.get("y"),
            "pages": page_rows,
        })
    # Both halves, on every path. Returning a bare list here while the two
    # early returns above returned pairs made `events, cut_short = ...` unpack
    # a two-event map into its own events -- so an ordinary map reported the
    # first event as the whole summary and the second as "truncated".
    return out, False


def _conditions(condition: Any) -> list[str]:
    if not isinstance(condition, RubyObject):
        return []
    out = []
    if condition.get("switch1_valid"):
        out.append("switch %s is ON" % condition.get("switch1_id"))
    if condition.get("switch2_valid"):
        out.append("switch %s is ON" % condition.get("switch2_id"))
    if condition.get("variable_valid"):
        out.append("variable %s >= %s" % (condition.get("variable_id"),
                                          condition.get("variable_value")))
    if condition.get("self_switch_valid"):
        letter = condition.get("self_switch_ch")
        text = letter.data.decode("ascii", "replace") if isinstance(letter, RubyString) \
            else str(letter)
        out.append("self switch %s is ON" % text)
    if condition.get("item_valid"):
        out.append("party has item %s" % condition.get("item_id"))
    if condition.get("actor_valid"):
        out.append("actor %s is in the party" % condition.get("actor_id"))
    return out


@tool(
    "map_edit",
    "Create, update, delete or resize a map",
    "Change a map's properties, add a new map to the tree, remove one, or "
    "resize it. Creating and deleting keep the map file and MapInfos in step, "
    "which is what the editor needs to show the map at all. Resizing preserves "
    "the tiles that still fit.",
    schema({
        "op": prop(STR, "What to do.", enum=list(MAP_OP_ARGS)),
        "map_id": prop(INT, "Which map. Omit for create to take the next free id."),
        "name": prop(STR, "Map name in the tree. Used by create, or to rename."),
        "parent_id": prop(INT, "Parent map id for create (0 for the tree root)."),
        "width": prop(INT, "Width in tiles, for create and resize. Default "
                           "%d for create." % DEFAULT_MAP_WIDTH,
                      minimum=1, maximum=MAX_MAP_SIDE),
        "height": prop(INT, "Height in tiles, for create and resize. Default "
                            "%d for create." % DEFAULT_MAP_HEIGHT,
                       minimum=1, maximum=MAX_MAP_SIDE),
        "tileset_id": prop(INT, "For create on VX Ace: which tileset the new "
                                "map uses (default 1). VX has no tilesets."),
        "patch": prop(OBJ, "Map fields to set, e.g. "
                           '{"autoplay_bgm": true, "bgm": {"name": "Town1"}, '
                           '"encounter_step": 25, "display_name": "Port"}'),
        "dry_run": DRY_RUN,
    }, ["op"]),
    writes=True,
)
def map_edit(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Create, resize, rename or patch a map, keeping the tree in step.

    Both halves of the request are resolved and validated before either
    is written, and the mutation is staged, so a refusal leaves nothing
    behind. Refuses a patch touching width, height or data -- those have
    to agree with the tile table, so they go through op "resize".
    """
    op = one_of(args, "op", list(MAP_OP_ARGS))
    check_op_args(op, args, MAP_OP_ARGS, "map_edit")
    dry_run = opt_bool(args, "dry_run")

    if op == "create":
        return _create_map(ctx, args, dry_run)

    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    if op == "delete":
        return _delete_map(ctx, map_id, dry_run)

    name = opt_str(args, "name")
    patch = opt_dict(args, "patch")
    _refuse_structural(patch)

    # Both halves of the request are resolved and validated here, before
    # either is written: the map file used to be saved first, so a request
    # that also renamed a map with no tree entry committed the resize and then
    # told the caller it had failed.
    info = None
    if name is not None:
        info = ctx.project.get_entry("map_infos", map_id)
        if info is None:
            raise ToolError(
                "map %d has no MapInfos entry, so it cannot be renamed. Use "
                "map_edit op=\"create\" to add the tree entry." % map_id)
        # Encoded now so a name this project's code page cannot hold is
        # refused before anything is written.
        ctx.codec.encode_strict(name)

    files = [ctx.project.map_file(map_id)]
    if info is not None:
        files.append(ctx.project.filename_for("map_infos"))
    with ctx.staged(*files) as write_landed:
        return _apply_map_edit(ctx, op, args, map_id, info, dry_run,
                               write_landed)


def _apply_resize(ctx: Context, args: dict[str, Any], game_map: RubyObject,
                  map_id: int) -> list[str]:
    """Resize one map's tile table, saying what that cost.

    Its own function because it is the only op with a body of any size, and
    because everything it needs comes out of `args` and the map -- so
    `_apply_map_edit` no longer threads the resize's arguments through a
    signature shared with three ops that do not use them.
    """
    width = opt_int(args, "width", game_map.get("width"),
                    minimum=1, maximum=MAX_MAP_SIDE)
    height = opt_int(args, "height", game_map.get("height"),
                     minimum=1, maximum=MAX_MAP_SIDE)
    if width is None or height is None:
        raise ToolError("resize needs a width and a height")
    if args.get("width") is None and args.get("height") is None:
        raise ToolError(
            "resize needs a width, a height, or both. Passing neither "
            "would rewrite the map to the size it already has.")
    data = game_map.get("data")
    if not isinstance(data, Table):
        raise ToolError("map %d has no tile table to resize" % map_id)

    # Both sizes, unconditionally. This used to be checked only inside
    # `_cells_outside`, which returns before reaching it when both axes grow
    # -- so the whole-table ceiling applied to shrinking a map and not to
    # growing one, and a grow was bounded only by the Table's own
    # 16-million-cell allocation cap.
    _check_budget(data.xsize * data.ysize * data.zsize, "walk",
                  windowed=False, remedy=_RESIZE_REMEDY)
    _check_budget(width * height * data.zsize, "write",
                  windowed=False, remedy=_RESIZE_REMEDY)

    old_width, old_height = data.xsize, data.ysize
    dropped = _cells_outside(data, width, height)
    game_map.set("data", data.resize(width, height, data.zsize))
    game_map.set("width", width)
    game_map.set("height", height)
    changes = ["resized from %dx%d to %dx%d"
               % (old_width, old_height, width, height)]
    if dropped:
        # Shrinking discards tiles, and silently discarding someone's work is
        # not something a tool should do without saying so.
        changes.append("%d painted tile(s) outside the new size were "
                       "discarded" % dropped)
    out_of_bounds = _events_out_of_bounds(game_map, width, height)
    if out_of_bounds:
        changes.append("events now outside the map: %s" % out_of_bounds)
    return changes


def _apply_map_edit(ctx: Context, op: str, args: dict[str, Any], map_id: int,
                    info: Any, dry_run: bool,
                    write_landed: StagedWrite) -> dict[str, Any]:
    """Run an update or a resize against the cached map. Saves at the end.

    `name` and `patch` are re-read from `args` rather than passed in: both
    readers are idempotent, and the six-value signature this replaced meant
    that adding one more resolved value touched the signature, the call site
    and the docstring together. The one thing that cannot be re-derived is
    `info`, because looking it up is what proves the map has a tree entry to
    rename -- and that check has to happen before anything is written.
    """
    name = opt_str(args, "name")
    patch = opt_dict(args, "patch")
    game_map = ctx.project.load_map(map_id)
    changes: list[str] = []
    writes = []

    if op == "resize":
        changes += _apply_resize(ctx, args, game_map, map_id)

    map_changed = bool(patch) or op == "resize"
    if patch:
        refuse_unknown_fields(patch, _map_fields(ctx, game_map),
                              "map %d" % map_id)
        changes += apply_patch(game_map, patch, ctx.codec)
    if info is not None:
        changes += apply_patch(info, {"name": name}, ctx.codec)

    if not changes:
        raise ToolError(
            "nothing to change. op \"update\" takes patch and name; op "
            "\"resize\" takes width and height.")

    # Only the file that actually changed: a rename touches the tree, not the
    # map, and writing both made a rename look like a map edit in the log.
    if map_changed:
        writes.append(write_landed.save(ctx.project.map_file(map_id),
                                        dry_run=dry_run, tool="map_edit",
                                        summary="; ".join(changes)))
    if info is not None:
        infos_file = ctx.project.filename_for("map_infos")
        if dry_run:
            writes.append(write_landed.save(infos_file, dry_run=True,
                                            tool="map_edit",
                                            summary="renamed map %d" % map_id))
        else:
            writes.append(_save_tree_after(
                write_landed, infos_file, "renamed map %d" % map_id,
                ("the map itself was saved (%s)" % "; ".join(changes)
                 if map_changed else "nothing was written to the map file"),
                "the rename did not land, so the map keeps its old name in "
                "the editor's tree. Close the editor and run the rename "
                "again; the map edit itself does not need repeating."))
    return {"map_id": map_id, "op": op, "changes": changes, "writes": writes}




def _check_budget(cells: int, what: str, windowed: bool = True,
                  remedy: str | None = None) -> None:
    """Refuse a cell walk larger than this server will spend time on.

    Every one of these is a per-cell loop on a single-threaded server, and a
    map's declared extents come out of a file that need not be one the editor
    wrote.

    ``windowed`` says whether the caller can ask for less. A read or a write
    takes an x/y/width/height, so the ceiling is small and the remedy is to
    narrow it; a tile tally or a resize covers the whole table by definition,
    so it gets the larger ceiling.

    ``remedy`` is passed in by callers whose way out is not the default one.
    The default told every non-windowed caller to "read it in pieces with
    map_tiles", which is no help to a resize -- map_tiles cannot resize -- and
    only half an answer for a tile tally, which it cannot compute either.
    """
    ceiling = MAX_CELLS_PER_READ if windowed else MAX_CELLS_PER_WALK
    if cells <= ceiling:
        return
    if remedy is None:
        remedy = ("Ask for a smaller x/y/width/height window."
                  if windowed else
                  "This map is larger than this server will walk in one call; "
                  "read it in pieces with map_tiles instead.")
    raise ToolError("that would %s %d cells, above the %d this tool handles at "
                    "once. %s" % (what, cells, ceiling, remedy))


# What a caller can do about a map too large to resize. Not "use map_tiles":
# map_tiles reads and paints, and cannot change a map's size at all.
_RESIZE_REMEDY = (
    "Both the current and the requested size are walked, and this map is "
    "larger than this server will walk in one call. A map this size has to be "
    "resized in the RPG Maker editor.")


def _check_tile(ctx: Context, tile_id: int, z: int, what: str) -> None:
    """Refuse a value the layer cannot hold.

    ``Table.set`` masks to 16 bits, so an out-of-range id was silently stored
    as a different tile and then reported back as the value the caller asked
    for.
    """
    ceiling = LAYER_CEILINGS.get(z, CELL_MASK)
    if not 0 <= tile_id <= ceiling:
        layer = ctx.project.profile.map_layers.get(z, "layer %d" % z)
        raise ToolError(
            "%s is %d, which %s cannot hold: its values run 0 to %d."
            % (what, tile_id, layer, ceiling))


def _map_fields(ctx: Context, game_map: RubyObject) -> set[str]:
    """What a patch to a map may name: what it has, and what RPG::Map documents."""
    return known_fields(ctx, game_map, "RPG::Map")


def _refuse_structural(patch: dict[str, Any]) -> None:
    """Refuse a patch naming a field the rest of the map has to agree with."""
    refuse_fields(
        patch, STRUCTURAL_MAP_FIELDS,
        "It has to stay in step with the rest of the map: use op \"resize\" "
        "for the size, map_tiles for tiles, and event_edit for events.")




def _cells_outside(data: Table, width: int, height: int) -> int:
    """How many painted cells a resize to this size would discard.

    Counted over slices of the flat buffer rather than cell by cell: the
    per-cell version made two Python calls for each of up to four million
    cells on a single-threaded server, to answer a question about rows.
    The caller owns the budget check, so that a grow is bounded too.
    """
    if width >= data.xsize and height >= data.ysize:
        return 0
    dropped = 0
    stride = data.xsize
    plane = data.xsize * data.ysize
    buffer = data.data
    for z in range(data.zsize):
        base = z * plane
        for y in range(data.ysize):
            row = buffer[base + y * stride:base + (y + 1) * stride]
            if y >= height:
                # Every painted cell in a dropped row goes.
                dropped += stride - row.count(0)
            else:
                # Only the part of the row past the new width.
                tail = row[width:]
                dropped += len(tail) - tail.count(0)
    return dropped


def _events_out_of_bounds(game_map: RubyObject, width: int, height: int) -> list[int]:
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        return []
    return [event_id for event_id, event in events.sorted_items()
            if isinstance(event, RubyObject)
            and ((event.get("x") or 0) >= width or (event.get("y") or 0) >= height)]


def _new_map_id(ctx: Context, args: dict[str, Any], infos: Any) -> int:
    """The id a new map will take, checked against every id already in use.

    Range-checked here rather than by `map_file` further down: that check used
    to run after the tree entry had been added to the cache, leaving a phantom
    entry under an id no map file can have.
    """
    used = set(ctx.project.map_ids_on_disk()) | {
        k for k in infos.keys() if isinstance(k, int)
    }
    map_id = opt_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    if map_id is None:
        map_id = next((i for i in range(1, MAX_MAP_ID + 1) if i not in used), None)
        if map_id is None:
            raise ToolError(
                "every map id from 1 to %d is taken, so there is no room for "
                "another map" % MAX_MAP_ID)
    elif map_id in used:
        raise ToolError("map %d already exists" % map_id)
    return map_id


def _new_tileset_id(ctx: Context, args: dict[str, Any]) -> int:
    """Which tileset a new map uses, on an engine that has a Tilesets table."""
    tileset_id = opt_int(args, "tileset_id", 1, minimum=1) or 1
    if not ctx.project.profile.has_tileset_database:
        if args.get("tileset_id") is not None:
            raise ToolError(
                "%s has no Tilesets database -- a map carries its own tileset "
                "-- so tileset_id does not apply here."
                % ctx.project.profile.label)
        return tileset_id
    known = {i for i, _ in ctx.project.entries("tilesets")}
    if tileset_id not in known:
        raise ToolError(
            "tileset %d does not exist; this project has %s"
            % (tileset_id, ", ".join(str(i) for i in sorted(known))))
    return tileset_id


def _new_tree_info(ctx: Context, args: dict[str, Any], name: str) -> Any:
    """The MapInfos entry for a new map: its name, parent and tree position."""
    order = max([info.get("order") or 0
                 for _, info in ctx.project.entries("map_infos")] or [0]) + 1
    parent_id = opt_int(args, "parent_id", 0, minimum=0) or 0
    if parent_id and parent_id not in {i for i, _ in
                                       ctx.project.entries("map_infos")}:
        raise ToolError(
            "parent_id %d is not a map in the tree, so the editor would have "
            "nowhere to show the new map. Pass 0 for the top level, or an "
            "existing map id." % parent_id)
    info = normalize_strings(new_map_info(name, parent_id, order), ctx.codec)
    # The name again, through the project codec, so a Japanese install gets
    # Shift_JIS rather than the template's UTF-8 -- and so a name this code
    # page cannot hold is refused rather than mangled.
    apply_patch(info, {"name": name}, ctx.codec)
    return info


def _create_map(ctx: Context, args: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    name = opt_str(args, "name")
    if not name:
        raise ToolError("create needs a name for the new map")
    if ctx.project.engine != ctx.project.profile.key:
        # An engine opened with --allow-other-engines is described with the
        # closest model available, which is fine for reading and refuses to
        # guess here: a map built from the wrong engine's field set is a file
        # its editor would reject.
        raise ToolError(
            "this is a %s project being described with the %s model, so the "
            "field set for a new map is not known. Editing existing maps is "
            "still fine; creating one is not."
            % (ctx.project.engine, ctx.project.profile.label)
        )
    infos = ctx.project.load_data("map_infos")
    map_id = _new_map_id(ctx, args, infos)
    width = opt_int(args, "width", DEFAULT_MAP_WIDTH,
                    minimum=1, maximum=MAX_MAP_SIDE) or DEFAULT_MAP_WIDTH
    height = opt_int(args, "height", DEFAULT_MAP_HEIGHT,
                     minimum=1, maximum=MAX_MAP_SIDE) or DEFAULT_MAP_HEIGHT
    game_map = normalize_strings(ctx.project.profile.new_map(
        width, height, _new_tileset_id(ctx, args)), ctx.codec)
    patch = opt_dict(args, "patch")
    # The same refusal the update path makes. Creating used to return before
    # that check ran, so a create could write a map whose @width disagreed
    # with its tile table -- the shape the editor will not open.
    _refuse_structural(patch)
    if patch:
        refuse_unknown_fields(patch, _map_fields(ctx, game_map), "a new map")
    changes = apply_patch(game_map, patch, ctx.codec) if patch else []

    info = _new_tree_info(ctx, args, name)
    filename = ctx.project.map_file(map_id)
    infos_file = ctx.project.filename_for("map_infos")

    # Staged like every other write, rather than undone by hand: the old
    # `infos.delete(map_id)` covered the dry run only, so a refused save left
    # a tree entry pointing at a map file that was never created.
    with ctx.staged(filename, infos_file) as write_landed:
        infos.set(map_id, info)
        if dry_run:
            # Returning without saving is what discards the tree entry
            # above; the map file itself was never created on this path.
            return {
                "op": "create", "map_id": map_id, "name": name, "dry_run": True,
                "would_write": [filename, infos_file],
                "size": [width, height],
                # The patch was applied to the in-memory map above, and a
                # preview that omits what it understood cannot be checked
                # against what the real call will do.
                "changes": changes,
            }
        ctx.project.create_file(filename, game_map)
        writes = [
            write_landed.save(filename, tool="map_edit",
                              summary="created map %d (%s)" % (map_id, name)),
            _save_tree_after(
                write_landed, infos_file,
                "added map %d to the tree" % map_id,
                "map %d was created as %s" % (map_id, filename),
                "the new map file has no tree entry, so the editor will not "
                "show it -- it appears under map_list's "
                "map_files_without_tree_entry. Close the editor and run this "
                "same create again to add the entry, or delete the file."),
        ]
    return {"op": "create", "map_id": map_id, "name": name,
            "size": [width, height], "changes": changes, "writes": writes}


def _save_tree_after(write_landed: StagedWrite, infos_file: str,
                     summary: str, landed: str, remedy: str) -> dict[str, Any]:
    """Save the map tree, reporting what already landed if this one does not.

    Two files, saved one after the other, cannot both land or neither: the
    editor can be holding MapInfos open, which is the very case the staleness
    check exists to catch. When that happens on the second save, "your edit was
    not applied" is false -- the first file is on disk -- and a caller told
    that will do the whole thing again.

    `_delete_map` was given this in round three and the create and rename paths
    were not, so both reported a half-landed pair as a clean failure.
    """
    try:
        return write_landed.save(infos_file, tool="map_edit", summary=summary)
    except (ProjectError, OSError) as exc:
        raise ToolError(
            "%s, but the map tree in %s could not be saved (%s). The two do "
            "not agree now, so this is not \"nothing happened\": %s"
            % (landed, infos_file, exc, remedy)) from exc


def _delete_map(ctx: Context, map_id: int, dry_run: bool) -> dict[str, Any]:
    infos = ctx.project.load_data("map_infos")
    children = [i for i, info in ctx.project.entries("map_infos")
                if isinstance(info, RubyObject) and info.get("parent_id") == map_id]
    if children:
        raise ToolError(
            "map %d is the parent of map(s) %s; move or delete those first so the "
            "editor's map tree stays valid"
            % (map_id, ", ".join(str(c) for c in children))
        )
    # One past the preview, so a full page can be told from one that happens
    # to be exactly full. `len(incoming)` is reported as the number of commands
    # pointing at this map, so without the extra a map referenced three hundred
    # times was said to be referenced twenty-five -- and a caller that fixed
    # those twenty-five would believe the map was free. The constant's own
    # comment claimed this fix before this line had it.
    # The same three facts db_edit's delete needs, from the same place: the
    # walk returns early only when it finds hits, so "no map transfers here"
    # is the answer that costs a full traversal of every command in the
    # project. This used to be five lines here and five more there, and the
    # "one past the limit" half was in only one of them.
    preview = reference_preview(ctx.project, "maps", map_id,
                                "a map delete's transfer scan")
    incoming = [hit["where"] for hit in preview.rows]
    more_than_shown, incomplete = preview.truncated, preview.incomplete

    filename = ctx.project.map_file(map_id)
    # Asked here, so the preview and the act agree: the file can legitimately
    # be absent, and reporting a deletion the real call will not perform is
    # the divergence _create_map's comment and Project.snapshot's docstring
    # both call worse than no preview.
    has_file = ctx.project.exists(filename)
    if dry_run:
        preview: dict[str, Any] = {
            "op": "delete", "map_id": map_id, "dry_run": True,
            "would_delete": filename if has_file else None,
            "incoming_transfers": incoming,
            "incoming_transfers_truncated": more_than_shown}
        if incomplete:
            # An empty `incoming_transfers` from a scan that stopped reads as
            # "no map sends the player here", which is the one thing a caller
            # about to delete a map acts on.
            preview["incoming_transfers_scan_stopped"] = incomplete
        if not has_file:
            preview["note"] = ("there is no %s to delete, so only the tree "
                               "entry would be removed" % filename)
        return preview
    # The tree entry goes first. Removing the file first meant that a refused
    # MapInfos save -- the editor holding it open, which is exactly what the
    # staleness check exists for -- left the map deleted and the tree still
    # pointing at it, while the caller was told nothing had happened.
    infos_file = ctx.project.filename_for("map_infos")
    # Checked before the tree is touched: the file can legitimately be absent
    # (map_list reports that as file_present: false), and removing the tree
    # entry first meant the caller was told the call failed after it had in
    # fact landed.
    with ctx.staged(infos_file) as tree_landed:
        infos.delete(map_id)
        write = tree_landed.save(infos_file, tool="map_edit",
                                 summary="removed map %d from the tree"
                                         % map_id)
    try:
        deleted = (ctx.project.delete_file(filename, tool="map_edit")
                   if has_file else None)
    except ProjectError as exc:
        # The tree write has already landed, so this is not "nothing
        # happened": say what did happen and where the leftover shows up.
        raise ToolError(
            "map %d was removed from the tree, but %s could not be deleted "
            "(%s). It now shows under map_list's map_files_without_tree_entry; "
            "delete it by hand, or run map_edit op=\"create\" with that id to "
            "put the tree entry back." % (map_id, filename, exc)) from exc
    result: dict[str, Any] = {"op": "delete", "map_id": map_id,
                              "deleted": deleted, "write": write}
    if not has_file:
        result["note"] = ("there is no %s to delete, so only the tree entry "
                          "was removed" % filename)
    if incomplete:
        result["incoming_transfers_scan_stopped"] = incomplete
    if incoming:
        result["warning"] = (
            "%s Transfer Player command(s) still point at map %d and will now "
            "send the player nowhere" % (preview.counted, map_id))
        result["incoming_transfers"] = incoming
        result["incoming_transfers_truncated"] = more_than_shown
    return result


class _Rect:
    """The rectangle of one map_tiles call, and the table it sits in.

    Every op resolves the same five numbers from the same arguments, and each
    of those has a check that must not be skipped, so they are resolved once
    here rather than in a body that then branches three ways.
    """

    __slots__ = ("data", "map_id", "x", "y", "width", "height", "z")

    def __init__(self, ctx: Context, args: dict[str, Any], map_id: int,
                 data: Table):
        self.data = data
        self.map_id = map_id
        if data.xsize < 1 or data.ysize < 1:
            # Named here, because every bound below is derived from these two:
            # an empty table used to be reported as "the default for x must be
            # at most -1, got 0", which is about an argument never sent.
            raise ToolError(
                "map %d has a tile table of %dx%d, so it has no cells to read "
                "or write. Give it a size with map_edit op=\"resize\" first."
                % (map_id, data.xsize, data.ysize))

        # Each axis is bounded before it is used. A negative x used to slip
        # past the fit check below, because the default width was computed
        # from it, and then failed deep in the Table with an error the model
        # could not act on.
        self.x = opt_int(args, "x", 0, minimum=0, maximum=data.xsize - 1) or 0
        self.y = opt_int(args, "y", 0, minimum=0, maximum=data.ysize - 1) or 0
        self.z = opt_int(args, "z", 0, minimum=0) or 0
        if self.z >= data.zsize:
            # Named rather than bounded generically, so the message says which
            # layers this map actually has.
            raise ToolError("layer %d does not exist; this map has layers 0..%d"
                            % (self.z, data.zsize - 1))

        # Both defaults are at least 1, because x and y are capped one inside
        # the table's extents and an empty table is refused above; an explicit
        # value below 1 is refused by the bound. So there is no zero-size
        # rectangle left to test for.
        self.width = opt_int(args, "width", data.xsize - self.x, minimum=1)
        self.height = opt_int(args, "height", data.ysize - self.y, minimum=1)
        if self.x + self.width > data.xsize or self.y + self.height > data.ysize:
            raise ToolError(
                "the rectangle (%d, %d) %dx%d does not fit inside this %dx%d map"
                % (self.x, self.y, self.width, self.height,
                   data.xsize, data.ysize))

    @property
    def cells(self) -> int:
        return self.width * self.height

    def describe(self, ctx: Context) -> dict[str, Any]:
        return {"map_id": self.map_id, "x": self.x, "y": self.y,
                "width": self.width, "height": self.height, "z": self.z,
                "layer": ctx.project.profile.map_layers.get(
                    self.z, "layer %d (not used by this engine)" % self.z)}

    def read(self) -> list[list[int]]:
        return [[self.data.get(self.x + dx, self.y + dy, self.z)
                 for dx in range(self.width)]
                for dy in range(self.height)]

    def paint(self, cells: "list[int]") -> None:
        """Write one value per cell, left to right and top to bottom."""
        for index, value in enumerate(cells):
            self.data.set(self.x + index % self.width,
                          self.y + index // self.width, self.z, value)


def _cells_to_fill(ctx: Context, args: dict[str, Any],
                   rect: _Rect) -> "tuple[list[int], str]":
    """One tile id for every cell of the rectangle."""
    tile_id = opt_int(args, "tile_id")
    if tile_id is None:
        raise ToolError("fill needs a tile_id")
    _check_tile(ctx, tile_id, rect.z, "tile_id")
    _check_budget(rect.cells, "fill")
    return ([tile_id] * rect.cells,
            "filled %dx%d at (%d, %d) layer %d with tile %d"
            % (rect.width, rect.height, rect.x, rect.y, rect.z, tile_id))


def _cells_from_rows(ctx: Context, args: dict[str, Any],
                     rect: _Rect) -> "tuple[list[int], str]":
    """The caller's grid, flattened, with every value checked first.

    Every value before the first cell is written: `Table.set` masks what it
    stores, so an out-of-range id used to be written as some other tile
    entirely -- and validating inside the write loop left the rows before the
    bad one in the cached map.
    """
    rows = opt_list(args, "rows")
    if len(rows) != rect.height:
        raise ToolError("rows has %d rows but the rectangle is %d tall"
                        % (len(rows), rect.height))
    _check_budget(rect.cells, "write")
    cells: list[int] = []
    for dy, row in enumerate(rows):
        if not isinstance(row, list) or len(row) != rect.width:
            raise ToolError(
                "row %d has %s cells but the rectangle is %d wide"
                % (dy, len(row) if isinstance(row, list) else "?", rect.width))
        for dx, value in enumerate(row):
            where = "rows[%d][%d]" % (dy, dx)
            tile_id = as_int(value, where)
            _check_tile(ctx, tile_id, rect.z, where)
            cells.append(tile_id)
    return (cells, "wrote %dx%d tiles at (%d, %d) layer %d"
            % (rect.width, rect.height, rect.x, rect.y, rect.z))


@tool(
    "map_tiles",
    "Read or write map tile data",
    "Read a rectangle of tile ids, write one back, or flood a rectangle with a "
    "single tile. Tiles are addressed x, y (from the top-left) on layer z: "
    "0 is the ground, 1 sits above it, 2 is the shadow layer, and on VX Ace 3 "
    "holds region ids. map_get reports the layers a given map actually has. "
    "Rows come back as arrays of tile ids, top row first.",
    schema({
        "map_id": prop(INT, "Which map.", minimum=1, maximum=MAX_MAP_ID),
        "op": prop(STR, "read (default), write, or fill.",
                   enum=list(TILE_OP_ARGS)),
        "x": prop(INT, "Left edge of the rectangle (default 0).", minimum=0),
        "y": prop(INT, "Top edge of the rectangle (default 0).", minimum=0),
        "width": prop(INT, "Rectangle width (default: to the map's right "
                           "edge). Width times height is at most %d cells per "
                           "call." % MAX_CELLS_PER_READ, minimum=1),
        "height": prop(INT, "Rectangle height (default: to the map's bottom edge).",
                       minimum=1),
        "z": prop(INT, "Layer: 0 ground, 1 above ground, 2 shadow mask (values "
                       "0-15), and on VX Ace 3 region ids (0-63). Default 0. "
                       "map_get reports the layers a given map has.",
                  minimum=0),
        "rows": prop(ARR, "For write: rows of tile ids, top row first. Must match "
                          "the rectangle.", items={"type": ARR, "items": {"type": INT}}),
        "tile_id": prop(INT, "For fill: the tile id to write into every cell. "
                             "Layers 0 and 1 take 0-65535, layer 2 takes 0-15, "
                             "and layer 3 takes 0-63.", minimum=0),
        "dry_run": DRY_RUN,
    }, ["map_id"]),
    writes=True,
)
def map_tiles(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Read, write or flood a rectangle of tile ids on one layer.

    Every coordinate and every value is checked before the first cell is
    written, because Table.set masks what it stores: an out-of-range id
    would otherwise be saved as a different tile.
    """
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    op = one_of(args, "op", list(TILE_OP_ARGS), "read")
    check_op_args(op, args, TILE_OP_ARGS, "map_tiles")
    game_map = ctx.project.load_map(map_id)
    data = game_map.get("data")
    if not isinstance(data, Table):
        raise ToolError("map %d has no tile table" % map_id)

    rect = _Rect(ctx, args, map_id, data)
    region = rect.describe(ctx)
    if op == "read":
        _check_budget(rect.cells, "read")
        region["rows"] = rect.read()
        return region

    cells, summary = (_cells_to_fill(ctx, args, rect) if op == "fill"
                      else _cells_from_rows(ctx, args, rect))
    dry_run = opt_bool(args, "dry_run")
    filename = ctx.project.map_file(map_id)
    with ctx.staged(filename) as write_landed:
        rect.paint(cells)
        write = write_landed.save(filename, dry_run=dry_run,
                                  tool="map_tiles", summary=summary)
    region["changed"] = summary
    region["write"] = write
    return region
