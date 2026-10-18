"""Reading maps: the tree, one map's properties, and its tile tally.

Split from map_tools, which had grown to 1196 lines over three subjects with
three op tables and two write surfaces. This half writes nothing.
"""

from __future__ import annotations

from collections import Counter
from typing import Any
from ..convert import TABLE_MODES, new_walk, to_json
from ..project import MAX_MAP_ID, ProjectError
from ..rubytypes import RubyHash, RubyObject, RubyString, Table, as_list
from ..schema import (
    META_LAYER,
    PAGE_CONDITION_FLAGS,
    TILE_ID_RANGES,
    TRIGGER_NAMES,
    region_of,
    shadow_of,
    unreadable_events_note,
)
from .map_common import MAX_CELLS_PER_WALK, _check_budget
from .registry import (
    BOOL,
    Context,
    INT,
    REFRESH,
    STR,
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


MAP_PROPERTY_FIELDS = (
    "width", "height", "scroll_type", "autoplay_bgm", "autoplay_bgs",
    "disable_dashing", "encounter_step", "encounter_list", "parallax_name",
    "parallax_loop_x", "parallax_loop_y", "parallax_sx", "parallax_sy",
    "parallax_show",
)

# How many tile ids one layer's tally lists. A layer can hold hundreds of
# distinct ids and a caller cannot use them all; what matters is that the reply
# says how many it did not show, because the tool used to promise "which tile
# ids appear on each layer" and return only these.
TOP_TILE_IDS = 12

# How many tree rows one `map_list` returns. A project has a few dozen maps;
# the number of MapInfos entries comes out of the file, and each row is a
# dict plus a `map_file()` call. Paged with `offset`, like `db_list`.
MAX_MAP_ROWS = 500

# How much tile data map_get includes. Named once, because the schema that
# advertises it and the reader that enforces it are 40 lines apart.
TILE_DETAIL = ("none", "counts")

# How many rows one map_get summary may build -- event rows and page rows
# together. Events times pages, and both numbers come out of the file: the
# shared-object map is fifty events of fifty pages, all one object, so 7 KB
# produced 2500 rows, and nothing in the reply said the list was long because
# the file was crafted rather than because the map is big.
#
# "Together" is the correction. The first version of this counted only page
# rows, because the counter sat inside the pages loop while the event row was
# appended outside it -- so an event with an empty @pages cost nothing and
# still produced a row, and a hash of a million links to one such event (four
# bytes a pair on disk) built a million dicts. `MAX_TOOL_RESULT_CHARS` does
# not save that: it is measured after the reply has been serialised, so it
# bounds the response and not the work or the memory.
MAX_EVENT_SUMMARY_ROWS = 2000


def _map_names(ctx: Context) -> dict[int, str]:
    names = {}
    try:
        for map_id, info in ctx.project.entries("map_infos"):
            if isinstance(info, RubyObject):
                names[map_id] = decoded(ctx, info.get("name"))
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
    "event count. Returns at most %d rows -- \"matching_maps\" is how many "
    "matched the filter, so page with offset when it is larger than "
    "\"returned\"."
    % MAX_MAP_ROWS,
    schema({
        "include_details": prop(BOOL, "Load every map file to report size, event "
                                      "count and BGM. Slower on large projects."),
        "parent_id": prop(INT, "Only list children of this map id (0 for the root).",
                          minimum=0, maximum=MAX_MAP_ID),
        "limit": prop(INT, "Maximum rows to return (default %d, at most %d)."
                           % (MAX_MAP_ROWS, MAX_MAP_ROWS),
                      minimum=1, maximum=MAX_MAP_ROWS),
        "offset": prop(INT, "Skip this many matching rows first (at most %d)."
                            % MAX_MAP_ID, minimum=0, maximum=MAX_MAP_ID),
        "refresh": REFRESH,
    }),
)
def map_list(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """The map tree, and which of its entries have a file. Reads only."""
    refresh = opt_bool(args, "refresh")
    details = opt_bool(args, "include_details")
    parent_filter = opt_int(args, "parent_id")

    # Read before the loop, because they bound the *building* and not just
    # the reply: this had no window at all, so a MapInfos hash of a million
    # links built a million reply dicts -- and one `map_file()` call each --
    # while `MAX_TOOL_RESULT_CHARS` rejected the answer afterwards. That
    # constant "bounds the response and not the work", as this module's own
    # comment on MAX_EVENT_SUMMARY_ROWS says.
    offset = opt_int_default(args, "offset", 0, minimum=0, maximum=MAX_MAP_ID)
    limit = opt_int_default(args, "limit", MAX_MAP_ROWS, minimum=1,
                            maximum=MAX_MAP_ROWS)

    infos = ctx.project.entries("map_infos", refresh)
    # With `refresh`, like the two reads around it. Without it this asked a
    # cached directory listing, so `map_list refresh=true` after the editor
    # created a map showed the new tree row with `file_present: false` and
    # no details -- and "use after the RPG Maker editor has saved" is the
    # whole of what `REFRESH` is for.
    on_disk = ctx.project.map_ids_present(refresh)
    rows = []
    matching = 0
    for map_id, info in infos:
        # A key that is not a map id (0, negative, past the ceiling, text)
        # names no file: `map_file` raised for it and failed the whole
        # listing on one hostile entry.
        if not isinstance(info, RubyObject) or not _is_map_id(map_id):
            continue
        parent = info.get("parent_id") or 0
        if parent_filter is not None and parent != parent_filter:
            continue
        # Counted whatever the window is, so `matching_maps` still answers
        # "how many match" rather than "how many came back".
        matching += 1
        # Only the window is built: the rows before `offset` each cost a
        # `map_file` call and, with details, a map load, to be thrown away.
        if matching <= offset or matching > offset + limit:
            continue
        row: dict[str, Any] = {
            "id": map_id,
            "name": decoded(ctx, info.get("name")),
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
                    row.update(partial("events", notes=(unreadable,)))
                bgm = game_map.get("bgm")
                if isinstance(bgm, RubyObject) and game_map.get("autoplay_bgm"):
                    row["bgm"] = decoded(ctx, bgm.get("name"))
            except (ProjectError, ValueError) as exc:
                row["error"] = str(exc)
        rows.append(row)

    # Over the whole tree, whatever the filter. This used to be an empty list
    # whenever a filter was given, without looking, so a filtered call reported
    # "no orphan map files" from a check that never ran -- and it is one set
    # difference, so there was nothing to save.
    # From the tree already read above, rather than a second `entries`
    # call: that re-runs `container_kind` and a `sorted_items()` sort to
    # rebuild exactly what `infos` is holding.
    orphans = sorted(on_disk - {i for i, _ in infos})
    return {
        "maps": rows,
        # Named for what it counted. "total" read as the size of the tree, so
        # a filtered listing looked like the whole of a much smaller project --
        # the same rename db_list already carries.
        "matching_maps": matching,
        "returned": len(rows),
        "offset": offset,
        "map_files_without_tree_entry": orphans,
        "start_position": _start_position(ctx),
    }


def _is_map_id(key: Any) -> bool:
    """A MapInfos key that can name a map file."""
    return type(key) is int and 1 <= key <= MAX_MAP_ID


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
                           "Each entry names its tileset sheet, except on VX "
                           "Ace's layer 3, which holds no tiles: there each "
                           "value is given as its region and shadow. "
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
            result.update(partial("tile_counts", notes=(
                "this map holds its tile data as %s rather than a Table, so "
                "there was nothing to count. Run validate to see what is "
                "wrong with the file." % type(data).__name__,)))
    else:
        result["tile_table"] = {
            "xsize": data.xsize, "ysize": data.ysize, "zsize": data.zsize,
            "layers": {str(z): ctx.project.profile.map_layers.get(
                           z, "layer %d (not used by this engine)" % z)
                       for z in range(min(data.zsize, _LAYERS_SHOWN))},
        }
        if data.zsize > _LAYERS_SHOWN:
            # A real map has four. The extent is the file's, up to 65,535,
            # and the table walks below take no cells from the budget when
            # the other two extents are small.
            result.update(partial("tile_layers", truncated=True, notes=(
                "this table declares %d layers; only the first %d are "
                "described. Run validate to see what is wrong with the file."
                % (data.zsize, _LAYERS_SHOWN),)))
        if tiles == "counts":
            result["tile_counts"] = _tile_counts(
                data, META_LAYER if ctx.project.profile.has_region_layer
                else None, ctx.project.profile.tile_id_ranges)
            # Each layer lists its most common ids and counts the rest. The
            # count alone was the only sign: a caller reading the list, not
            # the count, saw a complete answer -- the thing the partial
            # vocabulary exists to prevent.
            unlisted = sum(layer["tile_ids_not_shown"]
                           for layer in result["tile_counts"].values())
            if unlisted:
                result.update(partial("tile_counts", truncated=True, notes=(
                    "%d tile ids are not listed: each layer names its %d most "
                    "common, and tile_ids_not_shown says how many it left out"
                    % (unlisted, TOP_TILE_IDS),)))

    if opt_bool(args, "include_events", True):
        result["events"], cut_short = _event_summaries(ctx, game_map)
        # A boolean *and* a sentence, from `partial`. The sentence used to
        # be under the boolean's name, while `references_truncated` two
        # modules over was a boolean -- so one suffix meant two types and a
        # caller reading either had to know which.
        #
        # One call for both facts, because they are two sentences about the
        # same subject: `result["events_note"] = unreadable` was assigned
        # afterwards and dropped the truncation sentence whenever a map was
        # both truncated and unreadable. `partial` joins what it is given.
        result.update(partial(
            "events", truncated=cut_short, notes=(
                ("this map describes more than %d event and page rows, so "
                 "the summary stops there. A map that large is usually a "
                 "file that shares one page object across many slots "
                 "rather than a big map -- run validate. Read particular "
                 "events with event_get." % MAX_EVENT_SUMMARY_ROWS)
                if cut_short else "",
                unreadable_events_note(game_map.get("events")))))
    warnings = ctx.project.file_warnings(ctx.project.map_file(map_id))
    if warnings:
        result["file_warnings"] = warnings
    return result


# More layers than any engine has (VX Ace has four) are not listed.
_LAYERS_SHOWN = 16


def _tile_counts(data: Table, packed_layer: "int | None" = None,
                 ranges: "dict[str, tuple[int, int]] | None" = None
                 ) -> dict[str, Any]:
    """How often each tile id appears on each layer.

    Bounded like every other whole-table walk: this is a per-cell Python loop,
    and a crafted map can declare millions of cells.

    ``packed_layer`` is VX Ace's shadow and region layer, whose values are
    not tile ids: they are reported as the region and shadow they hold
    rather than named after the sheet whose id range they happen to fall in
    (256, region 1, used to read as a tile from sheet C).

    ``ranges`` names the sheets: the VX family's A1-E by default, XP's
    autotiles and tileset for an XP map.
    """
    _check_budget(
        data.xsize * data.ysize * data.zsize, "count the tiles of",
        windowed=False,
        remedy="A tally covers the whole table by definition, so there is no "
               "smaller window to ask for and map_tiles cannot count either. "
               "Read the layers you care about in pieces with map_tiles and "
               "count those.")
    # One Counter per layer over a slice of the flat buffer, rather than a
    # `data.get(x, y, z)` per cell -- two Python calls plus a bounds check and
    # an index multiply, for up to the four million cells `_check_budget`
    # allows. `_cells_outside` in map_edit_tools already counts this way and its
    # docstring gives this same reason; this walk simply had not been changed
    # with it.
    #
    # Byte-identical results, so it carries no guard: reverting it changes
    # nothing a correctness test can see and a timing test would be a race.
    # `TestATileTallyUsesTheBuffer.test_the_tally_matches_a_cell_by_cell_count`
    # pins this form against the slow one, layer by layer.
    out = {}
    plane = data.xsize * data.ysize
    buffer = data.data
    for z in range(min(data.zsize, _LAYERS_SHOWN)):
        counter: Counter[int] = Counter(buffer[z * plane:(z + 1) * plane])
        out[str(z)] = {
            "distinct_tile_ids": len(counter),
            "most_common": [{"tile_id": tile, "count": count,
                             **_what_a_value_is(tile, z == packed_layer,
                                                ranges or TILE_ID_RANGES)}
                            for tile, count in counter.most_common(TOP_TILE_IDS)],
            # Said in the reply, not only in the description: a caller asking
            # whether one tile is used anywhere read a short list, did not find
            # it, and had nothing to tell it the list was not the whole answer.
            "tile_ids_not_shown": max(0, len(counter) - TOP_TILE_IDS),
        }
    return out


def _what_a_value_is(value: int, packed: bool,
                     ranges: dict[str, tuple[int, int]]) -> dict[str, Any]:
    """The sheet a tile id is cut from, or the parts of a layer-3 word."""
    if packed:
        return {"region": region_of(value), "shadow": shadow_of(value)}
    return {"sheet": _tile_sheet(value, ranges)}


def _tile_sheet(tile_id: int, ranges: dict[str, tuple[int, int]]) -> str:
    if tile_id == 0:
        return "empty"
    for sheet, (low, high) in ranges.items():
        if low <= tile_id <= high:
            return sheet
    return "unknown"


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
    cut_short = False
    for event_id, event in events.sorted_items():
        if rows >= MAX_EVENT_SUMMARY_ROWS:
            return out, True
        # The event's own row, before the isinstance test rather than after
        # it. Charging only for a slot that reads as an RPG::Event meant a
        # hash of a million integers was walked free, which is the same
        # defect one level up as the pages loop below had.
        rows += 1
        if not isinstance(event, RubyObject):
            continue
        pages = as_list(event.get("pages"))
        page_rows = []
        for index, page in enumerate(pages):
            if rows >= MAX_EVENT_SUMMARY_ROWS:
                # Recorded, not just broken out of: this loop is inside the
                # outer one, so a ceiling reached on the *last* event fell
                # through to `return out, False` and answered a short page
                # list as though it were the whole event.
                cut_short = True
                break
            # Every slot examined, not every row appended. A slot that is not
            # a page `continue`d for free, so the ceiling was reached only
            # through the outer charge and the inner loop ran to completion
            # once per event -- a product, out of a file that is small.
            rows += 1
            if not isinstance(page, RubyObject):
                continue
            commands = as_list(page.get("list"))
            graphic = page.get("graphic")
            page_rows.append({
                "page": index,
                "trigger": _trigger_name(page.get("trigger")),
                "commands": len(commands),
                "graphic": decoded(ctx, graphic.get("character_name"))
                if isinstance(graphic, RubyObject) else "",
                "conditions": _conditions(page.get("condition")),
            })
        out.append({
            "id": event_id,
            "name": decoded(ctx, event.get("name")),
            "x": event.get("x"),
            "y": event.get("y"),
            # How many pages the event really has, beside however many of
            # them fitted. `event_get` carries this for the same reason: a
            # short list that does not say it is short is read as complete.
            "page_count": len(pages),
            "pages": page_rows,
        })
    # Both halves, on every path. Returning a bare list here while the two
    # early returns above returned pairs made `events, cut_short = ...` unpack
    # a two-event map into its own events -- so an ordinary map reported the
    # first event as the whole summary and the second as "truncated".
    return out, cut_short


_SHOWN_BOUND = 2 ** 62


def _shown(value: Any) -> str:
    """A file's number as text. Anything that is not a sane whole number is
    "(unreadable)": an integer of over 4,300 digits cannot be formatted at
    all, and an object would print every field it holds."""
    if (isinstance(value, int) and not isinstance(value, bool)
            and -_SHOWN_BOUND <= value <= _SHOWN_BOUND):
        return str(value)
    return "(unreadable)"


def _trigger_name(trigger: Any) -> Any:
    """A page's trigger as its name. Looked up only for a whole number: a
    Ruby Array in the file is a Python list, which cannot be a dict key."""
    if trigger is None:
        return None
    if isinstance(trigger, int) and not isinstance(trigger, bool):
        name = TRIGGER_NAMES.get(trigger)
        if name is not None:
            return name
        # An unknown number is reported as the number it is, as it always
        # was -- unless it is too large to put in a reply.
        return trigger if -_SHOWN_BOUND <= trigger <= _SHOWN_BOUND             else _shown(trigger)
    return "(unreadable)"


def _self_switch_letter(letter: Any) -> str:
    """The self-switch channel as text. A, B, C or D in every real file."""
    if isinstance(letter, RubyString):
        return letter.data.decode("ascii", "replace")
    return str(letter)


# One sentence per page-condition flag, keyed by the flag. Each condition
# names a different companion field -- `switch1_id`, `variable_id` with
# `variable_value`, `self_switch_ch` -- so the sentences cannot be derived;
# the six names and the six-ness can be, and were not.
#
# `PAGE_CONDITION_FLAGS` says in its own comment that it exists so the field
# docs, the constructor and the validate rule agree. This was the fourth
# reader and the one that agreed by coincidence: a seventh flag would have
# been documented, constructed and validated, and not described.
_CONDITION_SENTENCES = {
    "switch1_valid": lambda c: "switch %s is ON" % _shown(c.get("switch1_id")),
    "switch2_valid": lambda c: "switch %s is ON" % _shown(c.get("switch2_id")),
    "variable_valid": lambda c: "variable %s >= %s" % (
        _shown(c.get("variable_id")), _shown(c.get("variable_value"))),
    "self_switch_valid": lambda c: "self switch %s is ON" % (
        _self_switch_letter(c.get("self_switch_ch"))),
    "item_valid": lambda c: "party has item %s" % _shown(c.get("item_id")),
    "actor_valid": lambda c: "actor %s is in the party" % _shown(c.get("actor_id")),
}


def _conditions(condition: Any) -> list[str]:
    """What a page's conditions say, in the order the flags are declared."""
    if not isinstance(condition, RubyObject):
        return []
    return [_CONDITION_SENTENCES[flag](condition)
            for flag in PAGE_CONDITION_FLAGS if condition.get(flag)]
