"""raw_data: the unvalidated escape hatch, and the path syntax it addresses by.

Split out of misc_tools. This is the one tool that deliberately does not
validate what it writes, so it is worth reading on its own -- what it *does*
refuse is here in one place, and each refusal is a shape that produces a file
the editor cannot open rather than merely an unusual value.
"""

from __future__ import annotations

import re
from typing import Any

from ..convert import (
    MAX_JSON_DEPTH,
    MAX_JSON_NODES,
    TABLE_MODES,
    TextCodec,
    _hash_key,
    from_json,
    refuse_invented_in_replacement,
    to_json,
)
from ..project import MAX_MAP_ID
from ..rubytypes import (
    RubyHash,
    RubyIvars,
    RubyObject,
    Table,
    _bare_name,
)
from ..schema import ALL_TABLE_KEYS, STRUCTURAL_MAP_FIELDS
from .registry import (
    write_reply,
    Context,
    named,
    DRY_RUN,
    INT,
    OBJ,
    REFRESH,
    STR,
    ToolError,
    check_op_args,
    one_of,
    opt_bool,
    opt_int,
    opt_str,
    prop,
    schema,
    tool,
)


# What each raw_data op reads. `op` defaults to "get", which is the dangerous
# half: a caller that meant to write and forgot the op got back the value
# already there, under a "value" key that reads like confirmation of the write
# it thought it had made. So "get" refuses `value` outright.
#
# This paragraph was left behind in misc_tools when raw_data moved here, where
# it read as a description of the scripts op table.
RAW_OP_ARGS = {
    "get": {"op", "table", "map_id", "path", "tables", "refresh"},
    "set": {"op", "table", "map_id", "path", "value", "dry_run", "refresh"},
}

# Map fields whose value has to agree with the tile table or with the map's own
# structure. raw_data is the unvalidated escape hatch, but writing one of these
# on its own produces a file the editor refuses to open, which is not reach --
# it is just breakage.
# The same list map_edit refuses in a patch, imported rather than repeated:
# the two were written separately and had already drifted apart -- this one had
# "events" and map_edit's did not, so a patch could move an event off the map
# through the tool that validates while the escape hatch refused it.
STRUCTURAL_MAP_PATHS = STRUCTURAL_MAP_FIELDS

# The path is the one argument whose cost is a *product* of its own length and
# the file's. `_read_hash_pairs` registers a hash in the link table before it
# reads its pairs, so a file may legally hold a hash that contains itself --
# and each hash segment costs three linear scans of that hash. Nothing capped
# the segment count, so `"events/" + "1/" * 4_000_000` fits inside
# MAX_MESSAGE_CHARS and asks for 10^11 comparisons this server cannot be
# interrupted out of. Bounding the path rather than teaching `_resolve` about
# cycles: `to_json` already refuses a cycle, and no real field is this deep.
# Matched to MAX_JSON_DEPTH, since a path deeper than a renderable value has
# nothing at the end of it to render.
MAX_PATH_SEGMENTS = MAX_JSON_DEPTH


@tool(
    "raw_data",
    "Read or write any field by path",
    "An escape hatch for anything the other tools do not cover. Addresses a "
    "value by path, e.g. table \"system\" path \"party_members\", or -- to read "
    'one command parameter -- map 1 path "events/1/pages/0/list/3/parameters/0". '
    "Values are the same JSON shapes db_get returns, so objects need a "
    "__class__ key. Use the purpose-built tools where they exist: they "
    "validate, and this does not. It still refuses four things, because each "
    "produces a file the editor cannot open rather than an unusual value: the "
    "scripts table, for reading as well as writing (a slot body is compressed "
    "Ruby -- use the scripts tool); a map's data, width, height and events, "
    "which op \"get\" reads for you but op \"set\" refuses at any depth (use "
    "map_edit resize, map_tiles and event_edit to change those); swapping a "
    "structure for a scalar or the reverse; and a field the object does not "
    "already have.",
    schema({
        "op": prop(STR, "get (default) or set. A get renders at most %s "
                        "values and nests %d deep, and says so when it "
                        "stopped; a value larger than that has to be read "
                        "field by field along a deeper path."
                        % ("{:,}".format(MAX_JSON_NODES), MAX_JSON_DEPTH),
                   enum=list(RAW_OP_ARGS)),
        "table": prop(STR, "A data table name, e.g. system, actors, map_infos.",
                      enum=ALL_TABLE_KEYS),
        "map_id": prop(INT, "A map to address instead of a table."),
        "path": prop(STR, "Slash-separated path from the top of the file, "
                          "e.g. \"events/1/pages/0/list/3/parameters/0\". "
                          "Empty for the whole file. An array segment is an "
                          "index, and a negative one counts from the end, so "
                          "\"pages/-1\" is the last page. At most %d segments."
                          % MAX_PATH_SEGMENTS),
        "value": prop(OBJ, "For set: the new value, wrapped as {\"value\": ...} so "
                           "any JSON type can be passed."),
        "tables": prop(STR, "How to render RGSS Table fields.",
                       enum=list(TABLE_MODES)),
        "dry_run": DRY_RUN,
        "refresh": REFRESH,
    }),
    writes=True,
)
def raw_data(ctx: Context, args: dict[str, Any]) -> dict[str, Any]:
    """Read or write any field by path: the unvalidated escape hatch.

    Deliberately permissive about values, and deliberately not about
    shapes: it refuses replacing an object, hash, array or Table with a
    value of another kind, and refuses a map's data, width, height or
    events wholesale, because those produce a file the editor cannot
    open. Staged like every other write.
    """
    op = one_of(args, "op", list(RAW_OP_ARGS), "get")
    check_op_args(op, args, RAW_OP_ARGS, "raw_data")
    table = opt_str(args, "table")
    map_id = opt_int(args, "map_id", minimum=1, maximum=MAX_MAP_ID)
    if (table is None) == (map_id is None):
        raise ToolError("pass exactly one of table or map_id")
    refresh = opt_bool(args, "refresh")

    if table == "scripts":
        # Before the load, not after it. This refusal is unconditional for
        # this table, so unmarshalling the file first was work whose result
        # could never be used -- and it left a cache entry for a table nothing
        # here can act on. `map_edit_tools` fixes the same ordering, with a
        # comment saying the check "used to run after the tree entry had been
        # added to the cache".
        #
        # Both directions, which is what the description says and what db_get
        # already does. Reading was allowed, and handed back every slot body
        # as base64 of compressed Ruby -- bytes no caller can use, in a
        # response large enough to crowd out everything else, from a table
        # whose whole point is that the scripts tool decompresses it.
        raise ToolError(
            "the scripts table cannot be read or written through raw_data: a "
            "slot body is zlib-compressed Ruby, so reading it here gives you "
            "base64 of compressed bytes, and writing it as anything else "
            "stops the game booting. Use the scripts tool, which decompresses "
            "and compresses it for you.")

    if map_id is not None:
        filename = ctx.project.map_file(map_id)
        root = ctx.project.load_map(map_id, refresh)
    else:
        filename = ctx.project.filename_for(table)
        root = ctx.project.load_data(table, refresh)

    path = [p for p in (opt_str(args, "path") or "").split("/") if p]
    if len(path) > MAX_PATH_SEGMENTS:
        raise ToolError(
            "path has %d segments and at most %d are read. A path this deep "
            "does not address a field in an RGSS file: the deepest real one "
            "is about eight segments. Resolving each segment costs a scan of "
            "the container it names, so the work is the path's length times "
            "the file's."
            % (len(path), MAX_PATH_SEGMENTS))
    mode = one_of(args, "tables", list(TABLE_MODES), TABLE_MODES[0])

    if op == "get":
        value = _resolve(root, path, ctx.codec)
        return {"file": filename, "path": "/".join(path),
                "type": type(value).__name__,
                "value": to_json(value, ctx.codec, mode)}

    if not path:
        raise ToolError("set needs a path; replacing a whole file is not supported")
    if "value" not in args or not isinstance(args["value"], dict) \
            or "value" not in args["value"]:
        raise ToolError('set needs value as {"value": <the new value>}')

    if map_id is not None:
        _check_structural(path)
    parent = _resolve(root, path[:-1], ctx.codec)
    key = path[-1]
    new_value = from_json(args["value"]["value"], ctx.codec)
    existing = _resolve(root, path, ctx.codec)
    _check_same_kind(existing, new_value, "/".join(path))
    # The same check the validating tools make on a replacement. raw_data is
    # deliberately permissive about *values* and has never been permissive
    # about shapes: an invented field name is a shape the editor rejects, and
    # this tool's own description says it refuses one.
    refuse_invented_in_replacement(existing, new_value, "/".join(path))
    before = to_json(existing, ctx.codec, "summary")

    dry_run = opt_bool(args, "dry_run")
    with ctx.staged(filename) as write_landed:
        _assign(parent, key, new_value, ctx.codec, "/".join(path))
        write = write_landed.save(filename, dry_run=dry_run,
                                  tool="raw_data",
                                  summary="set %s" % "/".join(path))
    # `after` becomes `would_become` on a preview: it read as the value now
    # on disk, which is the one thing a dry run guarantees it is not.
    return write_reply(
        {"file": filename, "path": "/".join(path), "before": before,
         "after": to_json(new_value, ctx.codec, "summary"), "write": write},
        dry_run)

def _check_structural(path: list[str]) -> None:
    """Refuse any path into a map field the rest of the map has to agree with.

    Only the first segment is inspected, and that is sufficient rather than a
    shortcut: every name in the list is a top-level field of RPG::Map, so a
    deeper path like "events/1/pages" reaches one of them only by passing
    through segment 0. Replacing that page list produces the same map the
    interpreter cannot run as replacing "events" wholesale, so both are
    refused by the same test.

    Bare names, because `_resolve` accepts either spelling of an ivar.
    """
    if path and _bare_name(path[0]) in STRUCTURAL_MAP_PATHS:
        raise ToolError(
            "%s cannot be replaced through raw_data, because the rest of the "
            "map has to agree with it. Use map_edit op=\"resize\" for the "
            "size, map_tiles for tiles, and event_edit for events."
            % _bare_name(path[0]))

def _check_same_kind(existing: Any, replacement: Any, path: str) -> None:
    """Refuse swapping a structure for something that is not one.

    ``apply_patch`` refuses this and raw_data did not, so an Integer could take
    the place of an event Hash or a tile Table -- a file neither the editor nor
    the game can open. Anything else raw_data still allows: this is the one
    guard it keeps, because being able to write an odd *value* is the point,
    and being able to write an odd *shape* is not.
    """
    if existing is None:
        # A field or slot holding nil has no fields to set inside it, so the
        # remedy below cannot be followed -- and filling one in is exactly
        # what this tool is for. The reverse (an object back to nil) still
        # goes through the checks: that is a removal, not a fill.
        return
    kinds = ((RubyObject, "an object"), (RubyHash, "a hash"),
             (Table, "an RGSS Table"), (list, "an array"))
    for kind, described in kinds:
        # Both directions: putting a structure where a scalar was produces the
        # same file the editor will not open as the reverse.
        if isinstance(existing, kind) != isinstance(replacement, kind):
            held, given = ((described, type(replacement).__name__)
                           if isinstance(existing, kind)
                           else (type(existing).__name__, described))
            raise ToolError(
                "%s currently holds %s, and replacing it with %s would leave a "
                "file the editor cannot open. Set a field inside it instead, or "
                "use the tool that owns this structure." % (path, held, given))

def _resolve(value: Any, path: list[str], codec: TextCodec) -> Any:
    """Walk a path into a loaded file.

    The codec is needed because a Ruby Hash can be keyed by RubyStrings as well
    as by integers, and looking such a key up as a bare Python string finds
    nothing while reporting that the key does not exist.
    """
    current = value
    for depth, key in enumerate(path):
        trail = "/".join(path[:depth]) or "the top of the file"
        if isinstance(current, RubyObject):
            if not current.has(key):
                raise ToolError("%s has no field %r (it has: %s)"
                                % (trail, key, named(current.fields())))
            current = current.get(key)
        elif isinstance(current, list):
            current = _index_list(current, key, trail)
        elif isinstance(current, RubyHash):
            lookup = _hash_key(current, key, codec)
            if lookup not in current:
                raise ToolError("%s has no key %r (keys: %s)"
                                % (trail, key, current.keys()[:20]))
            current = current.get(lookup)
        elif isinstance(current, RubyIvars):
            # A value the codec wrapped to keep an instance-variable record it
            # could not store inline. Unwrap and retry the SAME key: the
            # wrapper is not a level of the path.
            current = current.inner
            return _resolve(current, path[depth:], codec)
        elif isinstance(current, Table):
            raise ToolError("%s is an RGSS Table; use map_tiles to read or write "
                            "its cells" % trail)
        else:
            raise ToolError("cannot descend into %s at %s"
                            % (type(current).__name__, trail))
    return current

def _list_index(items: list[Any], key: str, trail: str) -> int:
    """One path segment as an index into a list, or a refusal naming where.

    Both directions ask here. The write side had its own copy that did not
    carry the path, so a refused `raw_data` set reported "index 3 is out of
    range for 2 items" without saying which segment of the path it meant.

    A negative index counts from the end, as it does in Ruby and in Python:
    "events/1/pages/-1" is the last page. Said in the `path` description,
    because an index that silently means a different slot than the caller
    intended is the shape of mistake this module's refusals exist to stop.
    """
    if not _is_int(key):
        raise ToolError("%s is an array, so %r must be an index" % (trail, key))
    index = int(key)
    if not -len(items) <= index < len(items):
        raise ToolError("%s has %d items, so index %d is out of range"
                        % (trail, len(items), index))
    return index

def _index_list(items: list[Any], key: str, trail: str) -> Any:
    return items[_list_index(items, key, trail)]

def _assign(parent: Any, key: str, value: Any, codec: TextCodec,
            trail: str) -> None:
    if isinstance(parent, RubyObject):
        if not parent.has(key):
            raise ToolError(
                "%s has no field %r. raw_data will not invent fields, because the "
                "editor rejects objects with unexpected instance variables."
                % (parent.cls, key)
            )
        parent.set(key, value)
    elif isinstance(parent, list):
        parent[_list_index(parent, key, trail)] = value
    elif isinstance(parent, RubyHash):
        # The same resolution the read side uses. Keying differently here meant
        # a set on a RubyString-keyed hash appended a new bare-str key beside
        # the pair that had just been read, so the reported before/after was a
        # lie and the write then failed in the codec.
        parent.set(_hash_key(parent, key, codec), value)
    else:
        raise ToolError("cannot assign into a %s" % type(parent).__name__)

def _is_int(text: str) -> bool:
    return bool(re.fullmatch(r"-?\d+", text))
