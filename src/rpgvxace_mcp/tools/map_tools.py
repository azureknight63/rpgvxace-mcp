"""Map tools: the map tree, map properties and tile data."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..convert import apply_patch, normalize_strings, to_json
from ..analysis import find_references
from ..project import MAX_MAP_ID, ProjectError
from ..rubytypes import CELL_MASK, RubyHash, RubyObject, RubyString, Table
from ..schema import TILE_ID_RANGES, new_map_info
from .registry import (
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
                bgm = game_map.get("bgm")
                if isinstance(bgm, RubyObject) and game_map.get("autoplay_bgm"):
                    row["bgm"] = _name(ctx, bgm.get("name"))
            except (ProjectError, ValueError) as exc:
                row["error"] = str(exc)
        rows.append(row)

    orphans = sorted(on_disk - {r["id"] for r in rows}) if parent_filter is None else []
    return {
        "maps": rows,
        "total": len(rows),
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
        "tiles": prop(STR, "Tile reporting: none (default), counts (which tile ids "
                           "appear on each layer, and how often).",
                      enum=["none", "counts"]),
        "refresh": REFRESH,
    }, ["map_id"]),
)
def map_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """One map: properties, events in summary, optional tile tally."""
    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    refresh = opt_bool(args, "refresh")
    game_map = ctx.project.load_map(map_id, refresh)
    names = _map_names(ctx)

    properties = {}
    for field in MAP_PROPERTY_FIELDS:
        if game_map.has(field):
            properties[field] = to_json(game_map.get(field), ctx.codec)
    for field in ("bgm", "bgs"):
        track = game_map.get(field)
        if isinstance(track, RubyObject):
            properties[field] = to_json(track, ctx.codec)

    result: dict[str, Any] = {
        "map_id": map_id,
        "name": names.get(map_id, ""),
        "file": ctx.project.map_file(map_id),
        "properties": properties,
        "unknown_fields": [f for f in game_map.fields()
                           if f not in MAP_PROPERTY_FIELDS
                           and f not in ("bgm", "bgs", "data", "events")],
    }

    data = game_map.get("data")
    if isinstance(data, Table):
        result["tile_table"] = {
            "xsize": data.xsize, "ysize": data.ysize, "zsize": data.zsize,
            "layers": {str(z): ctx.project.profile.map_layers.get(
                           z, "layer %d (not used by this engine)" % z)
                       for z in range(data.zsize)},
        }
        if one_of(args, "tiles", ["none", "counts"], "none") == "counts":
            result["tile_counts"] = _tile_counts(data)

    if opt_bool(args, "include_events", True):
        result["events"] = _event_summaries(ctx, game_map)
    warnings = ctx.project.file_warnings(ctx.project.map_file(map_id))
    if warnings:
        result["file_warnings"] = warnings
    return result


def _tile_counts(data: Table) -> dict[str, Any]:
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
                            for tile, count in counter.most_common(12)],
        }
    return out


def _tile_sheet(tile_id: int) -> str:
    if tile_id == 0:
        return "empty"
    for sheet, (low, high) in TILE_ID_RANGES.items():
        if low <= tile_id <= high:
            return sheet
    return "unknown"


def _event_summaries(ctx: Context, game_map: RubyObject) -> list[dict[str, Any]]:
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        return []
    triggers = {0: "action button", 1: "player touch", 2: "event touch",
                3: "autorun", 4: "parallel process"}
    out = []
    for event_id, event in events.sorted_items():
        if not isinstance(event, RubyObject):
            continue
        pages = event.get("pages") or []
        page_rows = []
        for index, page in enumerate(pages):
            if not isinstance(page, RubyObject):
                continue
            commands = page.get("list") or []
            graphic = page.get("graphic")
            page_rows.append({
                "page": index,
                "trigger": triggers.get(page.get("trigger"), page.get("trigger")),
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
    return out


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
    "resize it. Creating and deleting keep Map###.rvdata and MapInfos in step, "
    "which is what the editor needs to show the map at all. Resizing preserves "
    "the tiles that still fit.",
    schema({
        "op": prop(STR, "What to do.", enum=["update", "create", "delete", "resize"]),
        "map_id": prop(INT, "Which map. Omit for create to take the next free id."),
        "name": prop(STR, "Map name in the tree. Used by create, or to rename."),
        "parent_id": prop(INT, "Parent map id for create (0 for the tree root)."),
        "width": prop(INT, "Width in tiles, for create and resize.", minimum=1),
        "height": prop(INT, "Height in tiles, for create and resize.", minimum=1),
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
    op = one_of(args, "op", ["update", "create", "delete", "resize"])
    dry_run = opt_bool(args, "dry_run")

    if op == "create":
        return _create_map(ctx, args, dry_run)

    map_id = need_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    if op == "delete":
        return _delete_map(ctx, map_id, dry_run)

    name = opt_str(args, "name")
    patch = opt_dict(args, "patch")
    _check_map_patch(op, args, patch)

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
        return _apply_map_edit(ctx, op, args, map_id, name, patch, info,
                               dry_run, write_landed)


def _apply_map_edit(ctx: Context, op: str, args: dict[str, Any], map_id: int,
                    name: str | None, patch: dict[str, Any], info: Any,
                    dry_run: bool, write_landed: StagedWrite) -> dict[str, Any]:
    game_map = ctx.project.load_map(map_id)
    changes: list[str] = []
    writes = []

    if op == "resize":
        width = opt_int(args, "width", game_map.get("width"),
                        minimum=1, maximum=MAX_MAP_SIDE)
        height = opt_int(args, "height", game_map.get("height"),
                         minimum=1, maximum=MAX_MAP_SIDE)
        if width is None or height is None:
            raise ToolError("resize needs a width and a height")
        data = game_map.get("data")
        if not isinstance(data, Table):
            raise ToolError("map %d has no tile table to resize" % map_id)
        old_width, old_height = data.xsize, data.ysize
        dropped = _cells_outside(data, width, height)
        game_map.set("data", data.resize(width, height, data.zsize))
        game_map.set("width", width)
        game_map.set("height", height)
        changes.append("resized from %dx%d to %dx%d"
                       % (old_width, old_height, width, height))
        if dropped:
            # Shrinking discards tiles, and silently discarding someone's work
            # is not something a tool should do without saying so.
            changes.append("%d painted tile(s) outside the new size were "
                           "discarded" % dropped)
        out_of_bounds = _events_out_of_bounds(game_map, width, height)
        if out_of_bounds:
            changes.append("events now outside the map: %s" % out_of_bounds)

    map_changed = bool(patch) or op == "resize"
    if patch:
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
        writes.append(ctx.project.save_file(ctx.project.map_file(map_id),
                                            dry_run=dry_run, tool="map_edit",
                                            summary="; ".join(changes)))
    if info is not None:
        writes.append(ctx.project.save_file(ctx.project.filename_for("map_infos"),
                                            dry_run=dry_run, tool="map_edit",
                                            summary="renamed map %d" % map_id))
    if not dry_run:
        write_landed.done()
    return {"map_id": map_id, "op": op, "changes": changes, "writes": writes}


# The largest map side this server will create or resize to. The editor's own
# ceiling is 500; this leaves room above it while refusing the extents that
# would build a multi-megabyte tile table out of one argument.
MAX_MAP_SIDE = 1000

# Fields whose value has to agree with the tile table, so they cannot be
# patched on their own: setting @width alone leaves a map claiming a size its
# @data does not have, which is the shape the editor refuses to open.
STRUCTURAL_MAP_FIELDS = ("width", "height", "data")


# How many cells one read returns. The whole of a large map is more than a
# caller can use and more than the response can carry.
MAX_CELLS_PER_READ = 20000

# How many of the references to a deleted map are listed back. Enough to
# see the shape of the problem without turning a delete into a report.
REFERENCE_PREVIEW = 25

# The highest value each layer can hold. Layers 0 and 1 carry tile ids; layer 2
# is a four-bit shadow mask; layer 3 on VX Ace holds region ids, which the
# editor numbers 0 to 63.
LAYER_CEILINGS = {2: 15, 3: 63}


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


def _check_map_patch(op: str, args: dict[str, Any], patch: dict[str, Any]) -> None:
    """Refuse a patch that would leave the map disagreeing with itself."""
    for field in STRUCTURAL_MAP_FIELDS:
        if field in patch:
            raise ToolError(
                "%s cannot be set with a patch, because it has to stay in step "
                "with the tile table. Use op \"resize\" to change the size of "
                "a map, or map_tiles to change its tiles." % field)
    if op != "resize" and (args.get("width") is not None
                           or args.get("height") is not None):
        raise ToolError(
            "width and height only apply to op \"resize\"; this call used op "
            "\"%s\"." % op)


def _cells_outside(data: Table, width: int, height: int) -> int:
    """How many painted cells a resize to this size would discard."""
    if width >= data.xsize and height >= data.ysize:
        return 0
    dropped = 0
    for z in range(data.zsize):
        for y in range(data.ysize):
            for x in range(data.xsize):
                if (x >= width or y >= height) and data.get(x, y, z):
                    dropped += 1
    return dropped


def _events_out_of_bounds(game_map: RubyObject, width: int, height: int) -> list[int]:
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        return []
    return [event_id for event_id, event in events.sorted_items()
            if isinstance(event, RubyObject)
            and ((event.get("x") or 0) >= width or (event.get("y") or 0) >= height)]


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
    used = set(ctx.project.map_ids_on_disk()) | {
        k for k in infos.keys() if isinstance(k, int)
    }
    # Range-checked here rather than by map_file() further down: that check
    # used to run after the tree entry had already been added to the cache,
    # leaving a phantom entry under an id no map file can have.
    map_id = opt_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    if map_id is None:
        map_id = next((i for i in range(1, MAX_MAP_ID + 1) if i not in used), None)
        if map_id is None:
            raise ToolError(
                "every map id from 1 to %d is taken, so there is no room for "
                "another map" % MAX_MAP_ID)
    elif map_id in used:
        raise ToolError("map %d already exists" % map_id)

    width = opt_int(args, "width", 20, minimum=1, maximum=MAX_MAP_SIDE) or 20
    height = opt_int(args, "height", 15, minimum=1, maximum=MAX_MAP_SIDE) or 15
    tileset_id = opt_int(args, "tileset_id", 1, minimum=1) or 1
    if ctx.project.profile.has_tileset_database:
        known = {i for i, _ in ctx.project.entries("tilesets")}
        if tileset_id not in known:
            raise ToolError(
                "tileset %d does not exist; this project has %s"
                % (tileset_id, ", ".join(str(i) for i in sorted(known))))
    game_map = normalize_strings(ctx.project.profile.new_map(
        width, height, tileset_id), ctx.codec)
    patch = opt_dict(args, "patch")
    changes = apply_patch(game_map, patch, ctx.codec) if patch else []

    order = max([info.get("order") or 0 for _, info in ctx.project.entries("map_infos")]
                or [0]) + 1
    info = normalize_strings(
        new_map_info(name, opt_int(args, "parent_id", 0) or 0, order), ctx.codec)
    # Rewrite the name through the project codec so a Japanese install gets
    # Shift_JIS rather than the template's UTF-8.
    apply_patch(info, {"name": name}, ctx.codec)
    filename = ctx.project.map_file(map_id)
    infos_file = ctx.project.filename_for("map_infos")

    # Staged like every other write, rather than undone by hand: the old
    # `infos.delete(map_id)` covered the dry run only, so a refused save left
    # a tree entry pointing at a map file that was never created.
    with ctx.staged(filename, infos_file) as write_landed:
        infos.set(map_id, info)
        if dry_run:
            # Returning without done() is what discards the tree entry above;
            # the map file itself was never created on this path.
            return {
                "op": "create", "map_id": map_id, "name": name, "dry_run": True,
                "would_write": [filename, infos_file],
                "size": [width, height],
            }
        ctx.project.create_file(filename, game_map)
        writes = [
            ctx.project.save_file(filename, tool="map_edit",
                                  summary="created map %d (%s)" % (map_id, name)),
            ctx.project.save_file(infos_file, tool="map_edit",
                                  summary="added map %d to the tree" % map_id),
        ]
        write_landed.done()
    return {"op": "create", "map_id": map_id, "name": name,
            "size": [width, height], "changes": changes, "writes": writes}


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
    incoming = []
    for hit in find_references(ctx.project, "maps", map_id,
                               limit=REFERENCE_PREVIEW):
        incoming.append(hit["where"])

    filename = ctx.project.map_file(map_id)
    if dry_run:
        return {"op": "delete", "map_id": map_id, "dry_run": True,
                "would_delete": filename, "incoming_transfers": incoming}
    # The tree entry goes first. Removing the file first meant that a refused
    # MapInfos save -- the editor holding it open, which is exactly what the
    # staleness check exists for -- left the map deleted and the tree still
    # pointing at it, while the caller was told nothing had happened.
    infos_file = ctx.project.filename_for("map_infos")
    with ctx.staged(infos_file) as tree_landed:
        infos.delete(map_id)
        write = ctx.project.save_file(infos_file, tool="map_edit",
                                      summary="removed map %d from the tree"
                                              % map_id)
        tree_landed.done()
    deleted = ctx.project.delete_file(filename, tool="map_edit")
    result = {"op": "delete", "map_id": map_id, "deleted": deleted, "write": write}
    if incoming:
        result["warning"] = (
            "%d Transfer Player command(s) still point at map %d and will now send "
            "the player nowhere" % (len(incoming), map_id))
        result["incoming_transfers"] = incoming
    return result


@tool(
    "map_tiles",
    "Read or write map tile data",
    "Read a rectangle of tile ids, write one back, or flood a rectangle with a "
    "single tile. Tiles are addressed x, y (from the top-left) on layer z: "
    "0 is the ground, 1 sits above it, 2 is the shadow layer, and on VX Ace 3 "
    "holds region ids. map_get reports the layers a given map actually has. "
    "Rows come back as arrays of tile ids, top row first.",
    schema({
        "map_id": prop(INT, "Which map."),
        "op": prop(STR, "read (default), write, or fill.", enum=["read", "write", "fill"]),
        "x": prop(INT, "Left edge of the rectangle (default 0).", minimum=0),
        "y": prop(INT, "Top edge of the rectangle (default 0).", minimum=0),
        "width": prop(INT, "Rectangle width (default: to the map's right edge).",
                      minimum=1),
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
    op = one_of(args, "op", ["read", "write", "fill"], "read")
    game_map = ctx.project.load_map(map_id)
    data = game_map.get("data")
    if not isinstance(data, Table):
        raise ToolError("map %d has no tile table" % map_id)

    # Each axis is bounded before it is used. A negative x used to slip past
    # the fit check below, because the default width was computed from it, and
    # then failed deep in the Table with an error the model could not act on.
    x = opt_int(args, "x", 0, minimum=0, maximum=data.xsize - 1) or 0
    y = opt_int(args, "y", 0, minimum=0, maximum=data.ysize - 1) or 0
    z = opt_int(args, "z", 0, minimum=0) or 0
    if z >= data.zsize:
        # Named rather than bounded generically, so the message says which
        # layers this map actually has.
        raise ToolError("layer %d does not exist; this map has layers 0..%d"
                        % (z, data.zsize - 1))
    width = opt_int(args, "width", data.xsize - x, minimum=1)
    height = opt_int(args, "height", data.ysize - y, minimum=1)
    if width is None or height is None:
        raise ToolError("width and height must be at least 1")
    if x + width > data.xsize or y + height > data.ysize:
        raise ToolError(
            "the rectangle (%d, %d) %dx%d does not fit inside this %dx%d map"
            % (x, y, width, height, data.xsize, data.ysize)
        )
    if op == "read" and width * height > MAX_CELLS_PER_READ:
        raise ToolError(
            "that rectangle is %d cells, above the %d this tool returns at "
            "once. Ask for a smaller x/y/width/height window."
            % (width * height, MAX_CELLS_PER_READ))

    region = {"map_id": map_id, "x": x, "y": y, "width": width, "height": height,
              "z": z, "layer": ctx.project.profile.map_layers.get(
                  z, "layer %d (not used by this engine)" % z)}

    if op == "read":
        region["rows"] = [
            [data.get(x + dx, y + dy, z) for dx in range(width)]
            for dy in range(height)
        ]
        return region

    # Every value is checked before the first cell is written. Table.set
    # masks what it stores, so an out-of-range id used to be written as some
    # other tile entirely -- and validating inside the write loop left the
    # rows before the bad one in the cached map.
    if op == "fill":
        tile_id = opt_int(args, "tile_id")
        if tile_id is None:
            raise ToolError("fill needs a tile_id")
        _check_tile(ctx, tile_id, z, "tile_id")
        cells = [tile_id] * (width * height)
        summary = "filled %dx%d at (%d, %d) layer %d with tile %d" % (
            width, height, x, y, z, tile_id)
    else:
        rows = opt_list(args, "rows")
        if len(rows) != height:
            raise ToolError("rows has %d rows but the rectangle is %d tall"
                            % (len(rows), height))
        cells = []
        for dy, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != width:
                raise ToolError("row %d has %s cells but the rectangle is %d wide"
                                % (dy, len(row) if isinstance(row, list) else "?", width))
            for dx, value in enumerate(row):
                tile_id = as_int(value, "rows[%d][%d]" % (dy, dx))
                _check_tile(ctx, tile_id, z, "rows[%d][%d]" % (dy, dx))
                cells.append(tile_id)
        summary = "wrote %dx%d tiles at (%d, %d) layer %d" % (width, height, x, y, z)

    dry_run = opt_bool(args, "dry_run")
    filename = ctx.project.map_file(map_id)
    with ctx.staged(filename) as write_landed:
        for index, tile_id in enumerate(cells):
            data.set(x + index % width, y + index // width, z, tile_id)
        write = ctx.project.save_file(filename, dry_run=dry_run,
                                      tool="map_tiles", summary=summary)
        if not dry_run:
            write_landed.done()
    region["changed"] = summary
    region["write"] = write
    return region
