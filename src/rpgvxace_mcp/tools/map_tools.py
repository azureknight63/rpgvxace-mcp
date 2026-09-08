"""Map tools: the map tree, map properties and tile data."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ..convert import apply_patch, normalize_strings, to_json
from ..rubytypes import RubyHash, RubyObject, RubyString, Table
from ..schema import TILE_ID_RANGES, new_map_info
from .registry import (
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
    except Exception:
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
            except Exception as exc:
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
    except Exception:
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
        "map_id": prop(INT, "Which map to read."),
        "include_events": prop(BOOL, "Include a summary of each event (default true)."),
        "tiles": prop(STR, "Tile reporting: none (default), counts (which tile ids "
                           "appear on each layer, and how often).",
                      enum=["none", "counts"]),
        "refresh": REFRESH,
    }, ["map_id"]),
)
def map_get(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    map_id = need_int(args, "map_id")
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
    op = one_of(args, "op", ["update", "create", "delete", "resize"])
    dry_run = opt_bool(args, "dry_run")

    if op == "create":
        return _create_map(ctx, args, dry_run)

    map_id = need_int(args, "map_id")
    if op == "delete":
        return _delete_map(ctx, map_id, dry_run)

    game_map = ctx.project.load_map(map_id)
    changes: list[str] = []
    writes = []

    if op == "resize":
        width = opt_int(args, "width", game_map.get("width"))
        height = opt_int(args, "height", game_map.get("height"))
        if width is None or height is None or width < 1 or height < 1:
            raise ToolError("resize needs a positive width and height")
        data = game_map.get("data")
        if not isinstance(data, Table):
            raise ToolError("map %d has no tile table to resize" % map_id)
        old = (data.xsize, data.ysize)
        game_map.set("data", data.resize(width, height, data.zsize))
        game_map.set("width", width)
        game_map.set("height", height)
        changes.append("resized from %dx%d to %dx%d" % (old[0], old[1], width, height))
        out_of_bounds = _events_out_of_bounds(game_map, width, height)
        if out_of_bounds:
            changes.append("events now outside the map: %s" % out_of_bounds)

    name = opt_str(args, "name")
    patch = opt_dict(args, "patch")
    if patch:
        changes += apply_patch(game_map, patch, ctx.codec)

    if changes or patch:
        writes.append(ctx.project.save_file(ctx.project.map_file(map_id),
                                            dry_run=dry_run, tool="map_edit",
                                            summary="; ".join(changes)))
    if name is not None:
        info = ctx.project.get_entry("map_infos", map_id)
        if info is None:
            raise ToolError("map %d has no MapInfos entry to rename" % map_id)
        changes += apply_patch(info, {"name": name}, ctx.codec)
        writes.append(ctx.project.save_file(ctx.project.filename_for("map_infos"),
                                            dry_run=dry_run, tool="map_edit",
                                            summary="renamed map %d" % map_id))
    if not changes:
        raise ToolError("nothing to change: pass patch, name, or width/height")
    return {"map_id": map_id, "op": op, "changes": changes, "writes": writes}


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
    infos = ctx.project.load_data("map_infos")
    used = set(ctx.project.map_ids_on_disk()) | {
        k for k in infos.keys() if isinstance(k, int)
    }
    map_id = opt_int(args, "map_id")
    if map_id is None:
        map_id = next(i for i in range(1, 1000) if i not in used)
    elif map_id in used:
        raise ToolError("map %d already exists" % map_id)

    width = opt_int(args, "width", 20) or 20
    height = opt_int(args, "height", 15) or 15
    game_map = normalize_strings(ctx.project.profile.new_map(
        width, height, opt_int(args, "tileset_id", 1) or 1), ctx.codec)
    patch = opt_dict(args, "patch")
    changes = apply_patch(game_map, patch, ctx.codec) if patch else []

    order = max([info.get("order") or 0 for _, info in ctx.project.entries("map_infos")]
                or [0]) + 1
    info = normalize_strings(
        new_map_info(name, opt_int(args, "parent_id", 0) or 0, order), ctx.codec)
    # Rewrite the name through the project codec so a Japanese install gets
    # Shift_JIS rather than the template's UTF-8.
    apply_patch(info, {"name": name}, ctx.codec)
    infos.set(map_id, info)

    filename = ctx.project.map_file(map_id)
    if dry_run:
        infos.delete(map_id)
        return {
            "op": "create", "map_id": map_id, "name": name, "dry_run": True,
            "would_write": [filename, ctx.project.filename_for("map_infos")],
            "size": [width, height],
        }
    ctx.project.create_file(filename, game_map)
    writes = [
        ctx.project.save_file(filename, tool="map_edit",
                              summary="created map %d (%s)" % (map_id, name)),
        ctx.project.save_file(ctx.project.filename_for("map_infos"), tool="map_edit",
                              summary="added map %d to the tree" % map_id),
    ]
    return {"op": "create", "map_id": map_id, "name": name, "size": [width, height],
            "changes": changes, "writes": writes}


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
    from ..analysis import find_references
    for hit in find_references(ctx.project, "maps", map_id, limit=25):
        incoming.append(hit["where"])

    filename = ctx.project.map_file(map_id)
    if dry_run:
        return {"op": "delete", "map_id": map_id, "dry_run": True,
                "would_delete": filename, "incoming_transfers": incoming}
    deleted = ctx.project.delete_file(filename)
    infos.delete(map_id)
    write = ctx.project.save_file(ctx.project.filename_for("map_infos"),
                                  tool="map_edit",
                                  summary="removed map %d from the tree" % map_id)
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
        "z": prop(INT, "Layer: 0 ground, 1 above ground, 2 shadow (default 0).",
                  minimum=0),
        "rows": prop(ARR, "For write: rows of tile ids, top row first. Must match "
                          "the rectangle.", items={"type": ARR, "items": {"type": INT}}),
        "tile_id": prop(INT, "For fill: the tile id to write into every cell."),
        "dry_run": DRY_RUN,
    }, ["map_id"]),
    writes=True,
)
def map_tiles(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    map_id = need_int(args, "map_id")
    op = one_of(args, "op", ["read", "write", "fill"], "read")
    game_map = ctx.project.load_map(map_id)
    data = game_map.get("data")
    if not isinstance(data, Table):
        raise ToolError("map %d has no tile table" % map_id)

    x = opt_int(args, "x", 0) or 0
    y = opt_int(args, "y", 0) or 0
    z = opt_int(args, "z", 0) or 0
    width = opt_int(args, "width", data.xsize - x)
    height = opt_int(args, "height", data.ysize - y)
    if z >= data.zsize:
        raise ToolError("layer %d does not exist; this map has layers 0..%d"
                        % (z, data.zsize - 1))
    if width is None or height is None or width < 1 or height < 1:
        raise ToolError("width and height must be at least 1")
    if x + width > data.xsize or y + height > data.ysize:
        raise ToolError(
            "the rectangle (%d, %d) %dx%d does not fit inside this %dx%d map"
            % (x, y, width, height, data.xsize, data.ysize)
        )

    region = {"map_id": map_id, "x": x, "y": y, "width": width, "height": height,
              "z": z, "layer": ctx.project.profile.map_layers.get(
                  z, "layer %d (not used by this engine)" % z)}

    if op == "read":
        region["rows"] = [
            [data.get(x + dx, y + dy, z) for dx in range(width)]
            for dy in range(height)
        ]
        return region

    if op == "fill":
        tile_id = opt_int(args, "tile_id")
        if tile_id is None:
            raise ToolError("fill needs a tile_id")
        for dy in range(height):
            for dx in range(width):
                data.set(x + dx, y + dy, z, tile_id)
        summary = "filled %dx%d at (%d, %d) layer %d with tile %d" % (
            width, height, x, y, z, tile_id)
    else:
        rows = opt_list(args, "rows")
        if len(rows) != height:
            raise ToolError("rows has %d rows but the rectangle is %d tall"
                            % (len(rows), height))
        for dy, row in enumerate(rows):
            if not isinstance(row, list) or len(row) != width:
                raise ToolError("row %d has %s cells but the rectangle is %d wide"
                                % (dy, len(row) if isinstance(row, list) else "?", width))
            for dx, tile_id in enumerate(row):
                data.set(x + dx, y + dy, z, int(tile_id))
        summary = "wrote %dx%d tiles at (%d, %d) layer %d" % (width, height, x, y, z)

    write = ctx.project.save_file(ctx.project.map_file(map_id),
                                  dry_run=opt_bool(args, "dry_run"),
                                  tool="map_tiles", summary=summary)
    region["changed"] = summary
    region["write"] = write
    return region
