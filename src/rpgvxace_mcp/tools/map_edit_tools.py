"""Creating, renaming, resizing and deleting maps.

Split from map_tools. Everything here writes, and every path writes both the
map file and the MapInfos tree entry when a rename is involved -- which is
why they cannot be separated further: the two files have to move together.
"""

from __future__ import annotations

from typing import Any
from ..analysis import reference_preview
from ..convert import apply_patch, normalize_strings
from ..project import MAX_MAP_ID, ProjectError
from ..rubytypes import RubyHash, RubyObject, Table, named
from ..analysis import _int
from ..schema import STRUCTURAL_MAP_FIELDS, new_map_info, on_map
from .map_common import _RESIZE_REMEDY, _check_budget
from .registry import (
    Context,
    DRY_RUN,
    INT,
    OBJ,
    STR,
    StagedWrite,
    ToolError,
    check_op_args,
    known_fields,
    need_int,
    one_of,
    opt_bool,
    opt_dict,
    opt_int,
    opt_str,
    prop,
    refuse_fields,
    refuse_unknown_fields,
    schema,
    tool,
    write_reply,
)

# The largest map side this server will create or resize to. The editor's own
# ceiling is 500; this leaves room above it while refusing the extents that
# would build a multi-megabyte tile table out of one argument.
MAX_MAP_SIDE = 1000

# What a new map is sized as when the caller does not say. The editor's own
# default for a new map, and small enough to be cheap to create.
DEFAULT_MAP_WIDTH, DEFAULT_MAP_HEIGHT = 20, 15

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

    # One object, resolved once. Both halves of the request are validated
    # before either is written, and the value each check ran against is the
    # value the write uses -- which is what the two-copies version could only
    # promise on the readers' behalf.
    request = _MapEdit(ctx, op, args, map_id)
    _refuse_structural(request.patch)
    request.require_tree_entry()

    with ctx.staged(*request.files()) as write_landed:
        return _apply_map_edit(request, write_landed)


class _MapEdit:
    """One map_edit request: resolved once, checked before anything is written.

    `_DbRequest`'s argument, on the tool that needed it more. `name` and
    `patch` used to be read in `map_edit` for the checks and read again in
    `_apply_map_edit` for the write, so the object `_refuse_structural`
    refused and the object `apply_patch` wrote were two objects that happened
    to be equal -- because the readers are pure, which is a fact about them
    rather than about this code. Measured at two reads each per request.

    ``args`` is kept for one caller only: `_apply_resize` reads `width` and
    `height` from it, and resolving those here would run a resize's bounds
    checks on an `update`.
    """

    __slots__ = ("ctx", "op", "args", "map_id", "info", "dry_run", "name",
                 "patch")

    def __init__(self, ctx: Context, op: str, args: dict[str, Any],
                 map_id: int):
        self.ctx = ctx
        self.op = op
        self.args = args
        self.map_id = map_id
        self.dry_run = opt_bool(args, "dry_run")
        self.name = opt_str(args, "name")
        self.patch = opt_dict(args, "patch")
        # Set by `require_tree_entry`, which is also what proves it exists.
        self.info: Any = None

    def require_tree_entry(self) -> None:
        """The MapInfos entry a rename needs, proved to exist before any write.

        The one value that cannot simply be re-derived: looking it up is the
        check. It runs outside the staged block because the map file used to
        be saved first, so a request that also renamed a map with no tree
        entry committed the resize and then told the caller it had failed.
        """
        if self.name is None:
            return
        self.info = self.ctx.project.get_entry("map_infos", self.map_id)
        if self.info is None:
            raise ToolError(
                "map %d has no MapInfos entry, so it cannot be renamed. Use "
                "map_edit op=\"create\" to add the tree entry." % self.map_id)
        # Encoded now so a name this project's code page cannot hold is
        # refused before anything is written.
        self.ctx.codec.encode_strict(self.name)

    def files(self) -> list[str]:
        """Every file this request may write, for the staged block to hold."""
        files = [self.ctx.project.map_file(self.map_id)]
        if self.info is not None:
            files.append(self.ctx.project.filename_for("map_infos"))
        return files


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
        # Capped: this list is one entry per event, from a count that comes
        # out of the file, and the joined `changes` becomes the `summary`
        # that `Project.save_file` appends to changes.jsonl -- written before
        # any reply is measured, so MAX_TOOL_RESULT_CHARS rejecting the reply
        # afterwards leaves a seven-megabyte line in the work directory.
        changes.append("events now outside the map: %s"
                       % named(str(entry) for entry in out_of_bounds))
    return changes


def _apply_map_edit(request: "_MapEdit",
                    write_landed: StagedWrite) -> dict[str, Any]:
    """Run an update or a resize against the cached map. Saves at the end.

    Two parameters where there were seven. The five that went were the ones
    already resolved by the caller, and two of them -- `name` and `patch` --
    were re-read here rather than passed, so the value checked and the value
    written were separate objects. See `_MapEdit`.
    """
    ctx, op, map_id = request.ctx, request.op, request.map_id
    args, info, dry_run = request.args, request.info, request.dry_run
    name, patch = request.name, request.patch
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
    # Through `write_reply`, so these two ops say what create and delete
    # already said. This is the tool that disagreed with itself: create and
    # delete answered `dry_run` plus `would_write`/`would_delete`, and update
    # and resize answered `changes: ["resized from 20x15 to 30x20"]`.
    return write_reply({"map_id": map_id, "op": op, "changes": changes,
                        "writes": writes}, dry_run)


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
        # `row_index`, not `y`: this walks rows of a flat buffer, and `y >=
        # height` reads exactly like the on-the-map rule `on_map` now owns
        # while being a different question. The name is the only thing that
        # distinguished them, so it says which one this is.
        for row_index in range(data.ysize):
            row = buffer[base + row_index * stride:
                         base + (row_index + 1) * stride]
            if row_index >= height:
                # Every painted cell in a dropped row goes.
                dropped += stride - row.count(0)
            else:
                # Only the part of the row past the new width.
                tail = row[width:]
                dropped += len(tail) - tail.count(0)
    return dropped


def _events_out_of_bounds(game_map: RubyObject, width: int,
                          height: int) -> list[int]:
    """Which events a resize would leave off the map.

    Through `on_map`, which is the copy of this rule that had drifted: this
    tested only the upper bound, so an event at x = -1 survived a resize
    unreported; and `or 0` turned a missing coordinate into 0 while a
    coordinate that is a String reached `>= width` as a TypeError.
    `_int` answers None for both, and a position that cannot be read is
    reported rather than assumed to be fine -- a resize is the one moment
    this list is a caller's only warning.
    """
    events = game_map.get("events")
    if not isinstance(events, RubyHash):
        return []
    out = []
    for event_id, event in events.sorted_items():
        if not isinstance(event, RubyObject):
            continue
        x, y = _int(event.get("x")), _int(event.get("y"))
        if x is None or y is None or not on_map(x, y, width, height):
            out.append(event_id)
    return out


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
            #
            # Through `write_reply`, like every other preview. This branch
            # hand-rolled both halves and got both wrong: `changes` is on the
            # list of words that read as a completed write, and `would_write`
            # held names where the act's `writes` holds dicts -- in the very
            # tool `PREVIEW_NAMES`' comment names as its reason for existing.
            return write_reply({
                "op": "create", "map_id": map_id, "name": name,
                "writes": [filename, infos_file],
                "size": [width, height],
                # The patch was applied to the in-memory map above, and a
                # preview that omits what it understood cannot be checked
                # against what the real call will do.
                "changes": changes,
            }, True)
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
    # Just the locations: `as_reply` says how many there were and
    # whether the scan finished.
    incoming = [hit["where"] for hit in preview.rows]

    filename = ctx.project.map_file(map_id)
    # Asked here, so the preview and the act agree: the file can legitimately
    # be absent, and reporting a deletion the real call will not perform is
    # the divergence _create_map's comment and Project.snapshot's docstring
    # both call worse than no preview.
    has_file = ctx.project.exists(filename)
    if dry_run:
        # `reply`, not `preview`: `preview` above is the ReferencePreview, and
        # rebinding the name here left one local holding two unrelated types
        # on two paths of one function -- with `preview.counted` read further
        # down, correct only because this branch returns. `_op_delete` in
        # db_tools names its reply `reply` for the same reason.
        #
        # Through `write_reply`, and through the one helper that decides what
        # the three reference facts are called: this branch spelled them by
        # hand and emitted two of them unconditionally while the act below
        # emitted them only `if incoming` -- so deleting an unreferenced map
        # previewed two keys the act omits, in the tool whose own comments
        # say a preview that does not match the act is worse than none.
        reply: dict[str, Any] = {
            "op": "delete", "map_id": map_id,
            "deleted": filename if has_file else None}
        reply.update(preview.as_reply("incoming_transfers", incoming))
        if not has_file:
            reply["note"] = ("there is no %s to delete, so only the tree "
                             "entry would be removed" % filename)
        return write_reply(reply, True)
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
    # The same keys as the preview, on every path rather than only when
    # there is something to report: a caller comparing the two got a
    # different key set depending on whether the map happened to be
    # referenced. `warnings`, plural and a list, because db_edit's reply
    # spells it that way and a caller reading one tool's replies should not
    # have to learn two words for one thing.
    result.update(preview.as_reply("incoming_transfers", incoming))
    if incoming:
        result["warnings"] = [
            "%s Transfer Player command(s) still point at map %d and will now "
            "send the player nowhere" % (preview.counted, map_id)]
    return result
