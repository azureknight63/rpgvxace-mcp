"""Bridging between Ruby values and JSON.

Ruby's object model carries things JSON has no notion of -- symbols, byte
strings, hashes with non-string keys, classes serialised through ``_dump``. Each
gets an explicit ``__``-prefixed marker so the mapping is reversible: whatever
:func:`to_json` produces, :func:`from_json` turns back into the same Ruby value.

Text is the awkward part. RGSS strings are bytes: RGSS3 tags them with an
encoding, RGSS2 does not, and either way the code page is the editor's. They are
decoded for display and re-encoded on write with the same codec, in the same
style the engine uses. Bytes that do not survive that trip are handed over as
base64 rather than silently mangled.
"""

from __future__ import annotations

import base64
import codecs
import re
import reprlib
from typing import Any

from .rubytypes import (
    CELL_MASK,
    Color,
    RGSSFloats,
    RubyClassRef,
    RubyData,
    RubyExtended,
    RubyFloat,
    RubyHash,
    RubyIvars,
    RubyObject,
    RubyRegexp,
    RubyString,
    RubyStruct,
    RubySymbol,
    RubyUClass,
    RubyUserDef,
    RubyUserMarshal,
    Table,
    Tone,
    _bare_name,
    named,
    printable_text,
)

# How a Table's cells are rendered.
TABLE_MODES = ("summary", "full")

# Cells are omitted from generic views above this size; map data is far bigger
# than anything worth pasting into a tool result.
TABLE_INLINE_LIMIT = 256

# Even when a caller asks for every cell, there is a size past which the answer
# is not usable as a JSON response. A 500x500x4 map is a million cells; nobody
# can read that, and map_tiles reads a region far more cheaply.
TABLE_FULL_LIMIT = 20_000

# A loaded value is a graph, not a tree: Marshal links resolve to the same
# object, and a file may legally contain a cycle. Both caps bound the walk.
MAX_JSON_DEPTH = 64
MAX_NORMALIZE_DEPTH = 24

# Path-based cycle detection has to repeat a shared value at every reference,
# because JSON has no links. This bounds what that can add up to.
#
# Sized from the response ceiling instead of chosen beside it. `_dump_json`
# indents, so every node costs at least four characters of output -- a payload
# that actually reached the old two million serialised to eight megabytes and
# was then *certainly* rejected at the transport, which bounds the response
# and not the work or the peak memory. So the largest conversion this server
# permitted was always thrown away, after several seconds and a few hundred
# megabytes. This is `server.MAX_TOOL_RESULT_CHARS // 8`, eight characters a
# node being the conservative divisor; written as a number because `server`
# imports this module and not the other way round, and held to that
# relationship by `TestTheNodeBudgetFitsTheResponse`.
#
# The cost, said out loud: on a crafted file this now refuses where it used
# to return a truncated reply with a note, because the truncated reply was
# itself past what the transport would carry. The refusal names the remedy --
# read one entry, one event, or a tile region -- so it is actionable, which
# is what makes it a better answer than eight megabytes of work discarded at
# the transport.
MAX_JSON_NODES = 250_000

# The most a single opaque _dump payload may carry, so a base64 blob cannot be
# used to inflate a data file without bound.
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

# Ruby's own rules for these names. Writing one Ruby rejects produces a file
# the editor can no longer open, which is a corruption we can refuse up front.
_IVAR_NAME = re.compile(r"^@[A-Za-z_][A-Za-z0-9_]*$")
_CLASS_PATH = re.compile(r"^[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*$")


def check_ivar_name(name: str) -> str:
    """Refuse a field name Ruby would not accept as an instance variable."""
    if not _IVAR_NAME.match(name if name.startswith("@") else "@" + name):
        raise ConversionError(
            "%r is not a usable field name. Ruby instance variables are a "
            "letter or underscore followed by letters, digits or underscores; "
            "a name outside that produces a file the editor cannot open."
            % name
        )
    return name


def check_class_name(name: str) -> str:
    """Refuse a class name Ruby would not resolve."""
    if not isinstance(name, str) or not _CLASS_PATH.match(name):
        raise ConversionError(
            "%r is not a usable Ruby class name. Expected something like "
            "RPG::Item or Table." % (name,)
        )
    return name


def _payload_from_base64(value: Any, what: str) -> bytes:
    """Decode a base64 payload strictly, so what we write is what was sent."""
    if not isinstance(value, str):
        raise ConversionError("%s must be a base64 string, got %r" % (what, value))
    try:
        # validate=True: without it, characters outside the alphabet are
        # silently dropped and the bytes written differ from the bytes sent.
        payload = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ConversionError("%s is not valid base64: %s" % (what, exc)) from None
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ConversionError(
            "%s decodes to %d bytes, above the %d byte limit"
            % (what, len(payload), MAX_PAYLOAD_BYTES))
    return payload


class ConversionError(ValueError):
    """Raised when a value cannot cross the Ruby/JSON boundary.

    Reported to the caller as a tool error, so the message has to say what to
    do instead -- these are usually reachable by a model sending a plausible
    but wrong argument.
    """


class TextCodec:
    """The code page a project's strings are stored in, and how it wraps them.

    ``string_ivars`` is the ivar record this engine's Ruby puts on every string:
    ``{"E": True}`` for RGSS3, which runs Ruby 1.9 and tags strings as UTF-8,
    and nothing at all for RGSS2, whose Ruby 1.8 strings are bare bytes.
    """

    def __init__(self, name: str = "utf-8", string_ivars: dict[str, Any] | None = None):
        try:
            codecs.lookup(name)
        except LookupError:
            raise ConversionError(
                "%r is not a known text encoding; pass --encoding with one Python "
                "recognises, such as utf-8, cp932 or cp1252." % name
            ) from None
        self.name = name
        self.string_ivars = dict(string_ivars or {})

    def decode(self, data: bytes) -> str:
        return data.decode(self.name, "surrogateescape")

    def make_string(self, text: str) -> RubyString:
        """A RubyString in this project's style, ready to write back.

        Encoded strictly: this is the path caller-supplied text takes, and a
        lone surrogate (which JSON permits) would otherwise become arbitrary
        raw bytes inside a string the engine has tagged as UTF-8.
        """
        return RubyString(self.encode_strict(text), dict(self.string_ivars) or None)

    def _encode(self, text: str, errors: "str | None",
                chain: bool) -> bytes:
        """One body for both encoders, so one sentence covers both.

        `encode` and `encode_strict` were the same six lines with the same
        word-for-word message, differing only in the error handler and in
        `from None` versus `from exc` -- two places to reword one refusal,
        and the refusal is the part a caller acts on.
        """
        try:
            return (text.encode(self.name) if errors is None
                    else text.encode(self.name, errors))
        except UnicodeEncodeError as exc:
            bad = text[exc.start:exc.end]
            error = ConversionError(
                "%r cannot be written in this project's %s encoding. Either use "
                "characters that fit that code page, or restart the server with "
                "--encoding set to a codec that covers them." % (bad, self.name))
            # `from exc` where the failure came out of a file, `from None`
            # where it came out of the caller's own request: the traceback is
            # noise in the second case and evidence in the first.
            raise error from (exc if chain else None)

    def encode_strict(self, text: str) -> bytes:
        """Encode text that came from a caller rather than from a file."""
        return self._encode(text, None, chain=False)

    def is_default_style(self, value: RubyString) -> bool:
        return value.ivars == self.string_ivars

    def encode(self, text: str) -> bytes:
        """Encode text that came back out of a file this server read."""
        return self._encode(text, "surrogateescape", chain=True)

    def is_clean(self, data: bytes) -> bool:
        """Whether ``data`` decodes to text with no undecodable bytes left over."""
        try:
            data.decode(self.name)
        except UnicodeDecodeError:
            return False
        return True


# Codecs tried in order when detecting a project's encoding. UTF-8 first
# because it is the only one that can be recognised with confidence; cp932 for
# Japanese installs; cp1252 last because it accepts almost any byte and so
# would otherwise mask the others.
CANDIDATE_ENCODINGS = ("utf-8", "cp932", "cp1252")


def detect_encoding(samples: list[bytes]) -> tuple[str, str]:
    """Guess a project's code page from string samples.

    Returns ``(encoding, reason)``.
    """
    non_ascii = [s for s in samples if not s.isascii()]
    if not non_ascii:
        return "utf-8", "all sampled text is ASCII, so any code page reads the same"
    for name in CANDIDATE_ENCODINGS:
        codec = TextCodec(name)
        if all(codec.is_clean(s) for s in non_ascii):
            return name, "%d of %d sampled strings contain non-ASCII bytes and all " \
                         "decode cleanly as %s" % (len(non_ascii), len(samples), name)
    return "cp1252", "no candidate code page decoded every string; falling back to " \
                     "cp1252 and preserving the original bytes"


# ---------------------------------------------------------------------------
# Ruby -> JSON
# ---------------------------------------------------------------------------


def to_json(value: Any, codec: TextCodec, tables: str = "summary",
            walk: "_Walk | None" = None) -> Any:
    """Convert a loaded Ruby value into JSON-safe data.

    ``tables`` selects how :class:`Table` cells are rendered: ``"summary"``
    reports the shape only, ``"full"`` includes every cell up to
    :data:`TABLE_FULL_LIMIT`.

    ``walk`` lets a caller converting several values in one response share one
    work budget across all of them. Without it, each call got its own, so a
    tool reading two hundred entries had two hundred times the allowance the
    budget was chosen to give -- see `new_walk`.
    """
    return _to_json(value, codec, tables, walk or _Walk())


def new_walk() -> "_Walk":
    """One shared conversion budget, for a caller rendering many values.

    Named rather than exposing `_Walk`, so the reason it exists stays with it:
    a per-value budget bounds one value and says nothing about a response
    built from hundreds of them.
    """
    return _Walk()


class _Walk:
    """Bookkeeping for one conversion: the path taken, and the work left.

    ``path`` holds the id of every container between the root and here, so a
    value that contains itself is reported instead of recursed into. It is one
    mutable set with entries added and removed around each recursion rather
    than a fresh copy per node -- copying made the walk quadratic in the size of
    a map.

    ``budget`` bounds the total nodes visited. Path-based cycle detection is the
    right answer for JSON output, which has no links and so must repeat a shared
    value at each reference, but that means a crafted graph sharing a child at
    every level can still expand exponentially. The budget is what stops it.
    """

    __slots__ = ("path", "depth", "budget")

    def spend(self, nodes: int = 1) -> None:
        """Charge for rendered nodes, refusing once there is nothing left.

        A method rather than the inline `budget -= 1` this replaces,
        because there are two chargers now: one per value converted, and
        one per cell of a Table. A Table used to cost a single node however
        many cells it inlined, which made it the one payload shape this
        budget could not see -- and the budget was sized from the response
        ceiling precisely so a conversion cannot build more than the
        transport will carry.
        """
        self.budget -= nodes
        if self.budget < 0:
            raise ConversionError(
                "this value expands to more than %d pieces, which is too "
                "large for one response. Read a smaller part of it -- a "
                "single entry, one event, or a tile region with map_tiles."
                % MAX_JSON_NODES)

    def __init__(self, budget: int = MAX_JSON_NODES):
        self.path: set[int] = set()
        self.depth = 0
        self.budget = budget


def _to_json(value: Any, codec: TextCodec, tables: str, walk: _Walk) -> Any:
    """One step of the conversion."""
    if walk.depth > MAX_JSON_DEPTH:
        raise ConversionError(
            "value nests deeper than %d levels; this file is not shaped like "
            "project data" % MAX_JSON_DEPTH
        )
    walk.spend()

    container = isinstance(value, (list, RubyHash, RubyObject, RubyIvars))
    if container:
        if id(value) in walk.path:
            # Report the cycle rather than recurse into it forever.
            return {"__cycle__": type(value).__name__,
                    "__note__": "this value contains itself; expanding it would "
                                "not terminate"}
        walk.path.add(id(value))
    walk.depth += 1
    try:
        return _to_json_value(value, codec, tables, walk)
    finally:
        walk.depth -= 1
        if container:
            walk.path.discard(id(value))


def _to_json_value(value: Any, codec: TextCodec, tables: str, walk: _Walk) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, RubyFloat):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, RubySymbol):  # before RubyString/str checks
        return {"__symbol__": printable_text(str(value))}
    if isinstance(value, RubyString):
        return _string_to_json(value, codec, tables, walk)
    if isinstance(value, (bytes, bytearray)):
        # All four arguments. This read as a handled case and passed two of
        # them, so any bare `bytes` reaching here raised TypeError -- which
        # server.py reports to the model as "an argument was not the shape the
        # tool expected", a diagnosis about the caller for a defect in here.
        # Nothing in the reader produces a bare `bytes` today, so the line had
        # never run; a branch that crashes when reached is not a handled case.
        return _string_to_json(RubyString(bytes(value)), codec, tables, walk)
    if isinstance(value, list):
        return [_to_json(v, codec, tables, walk) for v in value]
    if isinstance(value, RubyHash):
        out: dict[str, Any] = {
            "__hash__": [[_to_json(k, codec, tables, walk),
                          _to_json(v, codec, tables, walk)]
                         for k, v in value.pairs]
        }
        if value.default is not None:
            out["__default__"] = _to_json(value.default, codec, tables, walk)
        return out
    if isinstance(value, Table):
        return table_to_json(value, tables, walk)
    if isinstance(value, RGSSFloats):  # Color and Tone
        return {"__class__": type(value).CLASS_NAME, **value.as_dict()}
    if isinstance(value, RubyObject):
        result = {"__class__": printable_text(value.cls)}
        for name, item in value.ivars.items():
            result[printable_text(_bare_name(name))] = _to_json(
                item, codec, tables, walk)
        return result
    if isinstance(value, RubyIvars):
        return {"__ivar_wrapper__": _to_json(value.inner, codec, tables, walk),
                "__ivars__": {printable_text(k): _to_json(v, codec, tables, walk)
                              for k, v in value.ivars.items()}}
    if isinstance(value, RubyData):
        return {"__data__": printable_text(value.cls),
                "value": _to_json(value.value, codec, tables, walk)}
    if isinstance(value, RubyUserDef):
        return {
            "__class__": printable_text(value.cls),
            "__payload_base64__": base64.b64encode(value.payload).decode("ascii"),
            "__note__": "opaque _dump payload, preserved verbatim",
        }
    if isinstance(value, RubyUserMarshal):
        return {"__class__": printable_text(value.cls),
                "__marshal_value__": _to_json(value.value, codec, tables, walk)}
    if isinstance(value, RubyStruct):
        return {"__struct__": printable_text(value.cls),
                "members": {printable_text(k): _to_json(v, codec, tables, walk)
                            for k, v in value.members.items()}}
    if isinstance(value, RubyClassRef):
        return {"__class_ref__": value.name, "kind": value.kind}
    if isinstance(value, RubyRegexp):
        return {"__regexp__": codec.decode(value.source), "options": value.options}
    if isinstance(value, RubyUClass):
        return {"__uclass__": printable_text(value.cls),
                "inner": _to_json(value.inner, codec, tables, walk)}
    if isinstance(value, RubyExtended):
        return {"__extended__": value.modules,
                "inner": _to_json(value.inner, codec, tables, walk)}
    raise ConversionError("cannot represent a %s as JSON" % type(value).__name__)


def _string_to_json(value: RubyString, codec: TextCodec, tables: str,
                    walk: "_Walk") -> Any:
    try:
        # One strict decode, rather than is_clean() decoding and throwing the
        # result away before decoding again. Strings are the commonest value in
        # a project, so this runs a great many times.
        text = value.data.decode(codec.name)
    except UnicodeDecodeError:
        text = None
    if text is not None:
        if codec.is_default_style(value):
            # The common case: nothing to say beyond the text itself.
            return text
        return {"__string__": text,
                # The caller's walk, not a fresh one. `to_json` here started a
                # new budget and a new depth counter for every string that
                # carried an ivar record, so MAX_JSON_NODES and MAX_JSON_DEPTH
                # were both bypassable from a small file -- and the shared
                # budget threaded through for whole responses was bypassable
                # with them.
                "__ivars__": {k: _to_json(v, codec, tables, walk)
                              for k, v in value.ivars.items()}}
    # Undecodable bytes: hand them over as base64 so nothing is lost and the
    # caller can see that this is not ordinary text. Any string form the value
    # carried goes with it, so the round trip stays exact.
    out: dict[str, Any] = {
        "__bytes_base64__": base64.b64encode(value.data).decode("ascii"),
        "__note__": "bytes that are not valid %s" % codec.name,
    }
    if not codec.is_default_style(value):
        out["__ivars__"] = {k: _to_json(v, codec, tables, walk)
                            for k, v in value.ivars.items()}
    return out


def table_to_json(table: Table, mode: str = "summary",
                  walk: "_Walk | None" = None) -> dict[str, Any]:
    """One RGSS Table as JSON, charging the budget for the cells it inlines.

    Measured before the charge: a map whose @encounter_list is 250,000
    links to one 256-cell Table is 1.3 MB on disk, and `map_get map_id=1`
    with no arguments at all converted 64,000,000 cells into about a
    gigabyte of lists -- 250,000 nodes of a 250,000-node budget, and every
    one of those cells free.

    The omission is reported in the same vocabulary as every other partial
    answer, `cells_truncated` plus `cells_note`, rather than in a `hint` of
    its own. `cells_omitted` stays: the count is a fact a caller reads, and
    neither suffix carries a number.
    """
    out: dict[str, Any] = {
        "__class__": "Table",
        "dim": table.dim,
        "xsize": table.xsize,
        "ysize": table.ysize,
        "zsize": table.zsize,
        "size": table.size,
    }
    inline = table.size <= TABLE_INLINE_LIMIT or (
        mode == "full" and table.size <= TABLE_FULL_LIMIT)
    if inline:
        if walk is not None:
            # Before the cells are built, and one per cell: each is a
            # rendered node of the reply, which is what this budget counts.
            walk.spend(table.size)
        out["cells"] = list(table.data)
    else:
        out["cells_omitted"] = table.size
        # Built by hand, and one of the two places in the project that is:
        # `registry.partial` owns these suffixes and this module sits below
        # `tools`, so reaching it would invert the layering.
        # `TestOneVocabularyForAPartialAnswer` names both exceptions.
        out["cells_truncated"] = True
        out["cells_note"] = (
            "use map_tiles to read or write regions of this table"
            if table.size > TABLE_FULL_LIMIT and mode == "full"
            else "pass tables=\"full\" for the cells, or use map_tiles to "
                 "read a region")
    return out


# ---------------------------------------------------------------------------
# JSON -> Ruby
# ---------------------------------------------------------------------------


def from_json(value: Any, codec: TextCodec) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return codec.make_string(value)
    if isinstance(value, list):
        return [from_json(v, codec) for v in value]
    if isinstance(value, dict):
        return _dict_from_json(value, codec)
    raise ConversionError("cannot convert a %s into a Ruby value" % type(value).__name__)


def _ivars_from_json(value: dict[str, Any], codec: TextCodec) -> dict[str, Any]:
    """The ``__ivars__`` a wrapper carries, with every name checked.

    Three forms carry these -- the two string forms and the ivar wrapper -- and
    each had its own copy of this comprehension without the name check, which
    is how one fix would have missed the other two. `check_ivar_name`'s
    docstring says an unchecked name "produces a file the editor cannot open",
    and these names are written into the Marshal stream exactly as given.
    """
    return {check_ivar_name(name): from_json(item, codec)
            for name, item in (value.get("__ivars__") or {}).items()}


def _dict_from_json(value: dict[str, Any], codec: TextCodec) -> Any:
    if "__symbol__" in value:
        return RubySymbol(value["__symbol__"])
    if "__bytes_base64__" in value:
        # The string form travels with the bytes, so an undecodable string with
        # a non-default encoding record still round trips.
        ivars = _ivars_from_json(value, codec)
        return RubyString(
            _payload_from_base64(value["__bytes_base64__"], "__bytes_base64__"),
            ivars or None)
    if "__string__" in value:
        ivars = _ivars_from_json(value, codec)
        return RubyString(codec.encode(value["__string__"]), ivars or None)
    if "__hash__" in value:
        pairs = [(from_json(k, codec), from_json(v, codec)) for k, v in value["__hash__"]]
        default = from_json(value["__default__"], codec) if "__default__" in value else None
        return RubyHash(pairs, default)
    if "__ivar_wrapper__" in value:
        return RubyIvars(from_json(value["__ivar_wrapper__"], codec),
                         _ivars_from_json(value, codec))
    if "__data__" in value:
        return RubyData(check_class_name(value["__data__"]),
                        from_json(value.get("value"), codec))
    if "__cycle__" in value:
        raise ConversionError(
            "this value was reported as a cycle when it was read, so it cannot "
            "be written back. Edit the field that contains it instead."
        )
    if "__struct__" in value:
        # A struct's members are named like ivars and are written as symbols,
        # so they go through the same check.
        return RubyStruct(check_class_name(value["__struct__"]),
                          {check_ivar_name(name): from_json(item, codec)
                           for name, item in (value.get("members") or {}).items()})
    if "__class_ref__" in value:
        return RubyClassRef(check_class_name(value["__class_ref__"]),
                            value.get("kind", "class"))
    if "__regexp__" in value:
        return RubyRegexp(codec.encode(value["__regexp__"]), int(value.get("options", 0)))
    if "__uclass__" in value:
        return RubyUClass(check_class_name(value["__uclass__"]),
                          from_json(value["inner"], codec))
    if "__extended__" in value:
        # Module names, written as symbols in the stream, so the same shape
        # applies: `Comparable`, `RPG::Whatever`.
        return RubyExtended(
            [check_class_name(name) for name in value["__extended__"]],
            from_json(value["inner"], codec))

    cls = value.get("__class__")
    if cls is None:
        raise ConversionError(
            'an object needs a "__class__" key naming its Ruby class (got keys %s); '
            "JSON objects without one cannot be turned into Ruby values"
            % sorted(value)
        )
    if "__payload_base64__" in value:
        return RubyUserDef(
            check_class_name(cls),
            _payload_from_base64(value["__payload_base64__"], "__payload_base64__"))
    if "__marshal_value__" in value:
        return RubyUserMarshal(check_class_name(cls),
                               from_json(value["__marshal_value__"], codec))
    if cls == "Table":
        return table_from_json(value)
    if cls == "Color":
        return Color([float(value.get(f, 0.0)) for f in Color.FIELDS])
    if cls == "Tone":
        return Tone([float(value.get(f, 0.0)) for f in Tone.FIELDS])

    obj = RubyObject(check_class_name(cls))
    for key, item in value.items():
        if key.startswith("__"):
            continue
        obj.set(check_ivar_name(key), from_json(item, codec))
    return obj


def table_from_json(value: dict[str, Any]) -> Table:
    xsize = int(value.get("xsize", 0))
    ysize = int(value.get("ysize", 1))
    zsize = int(value.get("zsize", 1))
    dim = value.get("dim")
    if dim is not None and (not isinstance(dim, int) or not 1 <= dim <= 3):
        raise ConversionError(
            "a Table has 1, 2 or 3 dimensions, not %r" % (dim,))
    cells = value.get("cells")
    if cells is None:
        # A summary view omits the cells, so accepting one here would write a
        # table of zeros over whatever was there -- silently wiping a map's
        # tiles on a read-then-write round trip.
        if xsize * ysize * zsize:
            raise ConversionError(
                "this Table has %d cells but no \"cells\" to write. It was read "
                "as a summary, which does not carry them. Use map_tiles to "
                "change tile data, or read it again with tables=\"full\"."
                % (xsize * ysize * zsize)
            )
        return Table(xsize, ysize, zsize, dim=dim)
    # Range-checked rather than left to `array("H", ...)`, which raises
    # OverflowError -- an ArithmeticError, so not in server.py's caught list,
    # so a wrong argument was reported as an internal error. `Table.set` masks
    # to 16 bits for the same reason.
    checked = []
    for index, cell in enumerate(cells):
        number = int(cell)
        if not 0 <= number <= CELL_MASK:
            raise ConversionError(
                "cell %d is %d, outside the 0 to %d a Table holds"
                % (index, number, CELL_MASK))
        checked.append(number)
    return Table(xsize, ysize, zsize, dim=dim, data=checked)


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------


def _hash_key(target: RubyHash, key: str, codec: TextCodec) -> Any:
    """The key to use when patching into a Ruby Hash.

    JSON object keys are always strings, but a project's hashes are keyed by
    integers (a map's events, the map tree) or by Ruby strings. Looking up the
    raw JSON string would miss every one of them and append a new entry the
    engine cannot use.
    """
    existing = target.keys()
    if key.lstrip("-").isdigit() and any(
            isinstance(k, int) and not isinstance(k, bool) for k in existing):
        return int(key)
    if any(isinstance(k, RubyString) for k in existing):
        return codec.make_string(key)
    return key


def apply_patch(target: Any, patch: dict[str, Any], codec: TextCodec,
                path: str = "") -> list[str]:
    """Merge a JSON patch into a loaded Ruby object, in place.

    Nested plain objects merge field by field, so ``{"bgm": {"name": "Town"}}``
    changes only the BGM's name. A value carrying ``__class__`` replaces the
    whole field, which is how you swap an object out wholesale. Returns a list
    of human-readable descriptions of what changed.
    """
    changes: list[str] = []
    # Two passes over the *scalar* values: each is converted before any is
    # assigned, so a value this project's code page cannot hold is refused
    # before anything is written. A nested merge is not pre-converted here --
    # it is validated when the recursion reaches it, by which point this
    # object's own scalars have landed in the cached copy.
    #
    # So this pass is not what keeps a half-applied patch off disk. What does
    # is `Context.staged`, which forgets every file whose save did not happen;
    # an earlier version of this comment claimed the guarantee for itself.
    for key, new_value in patch.items():
        if key.startswith("__"):
            continue
        if isinstance(target, RubyObject):
            check_ivar_name(key)
        merge_only = (isinstance(new_value, dict)
                      and not any(k.startswith("__") for k in new_value))
        if not merge_only:
            from_json(new_value, codec)

    for key, new_value in patch.items():
        if key.startswith("__"):
            continue
        field_path = "%s.%s" % (path, key) if path else key

        if isinstance(target, RubyObject):
            lookup: Any = key
            existing = target.get(key)
            has_field = target.has(key)
        elif isinstance(target, RubyHash):
            lookup = _hash_key(target, key, codec)
            existing = target.get(lookup)
            has_field = lookup in target
        else:
            raise ConversionError("cannot patch a %s at %s"
                                  % (type(target).__name__, field_path or "the root"))

        merge = (isinstance(new_value, dict)
                 and not any(k.startswith("__") for k in new_value)
                 and isinstance(existing, (RubyObject, RubyHash)))
        if merge:
            if isinstance(existing, RubyObject):
                _refuse_invented_fields(existing, new_value, field_path)
            changes.extend(apply_patch(existing, new_value, codec, field_path))
            continue

        converted = from_json(new_value, codec)
        if has_field and isinstance(existing, (RubyObject, RubyHash, Table)) \
                and not isinstance(converted, type(existing)):
            raise ConversionError(
                "%s currently holds %s; replacing it with %s would produce a "
                "file the editor rejects. Patch its fields instead, or pass an "
                "object with a matching \"__class__\"."
                % (field_path, _change_line(existing, codec),
           _change_line(converted, codec))
            )
        # The replacement's own field names, which nothing checked: the patch's
        # keys are checked by `refuse_unknown_fields` and a nested merge by
        # `_refuse_invented_fields`, and a value carrying `__class__` goes
        # through neither.
        refuse_invented_in_replacement(existing, converted, field_path)
        before = _change_line(existing, codec) if has_field else "<unset>"
        target.set(lookup, converted)
        changes.append("%s: %s -> %s" % (field_path, before,
                          _change_line(converted, codec)))
    return changes


def _refuse_invented_fields(target: RubyObject, patch: dict[str, Any],
                            path: str) -> None:
    """Refuse a nested field the object being merged into does not carry.

    The tools guard the top level of a patch with ``refuse_unknown_fields``,
    which compares against the fields the entry has. Nothing looked one level
    down, so ``{"bgm": {"nmae": "Town"}}`` merged into the RPG::BGM, added
    ``@nmae`` and came back as ``bgm.nmae: <unset> -> "Town"`` -- a typo
    reported as the change the caller asked for, in a file the editor then
    refuses to open.

    Only for a nested ``RubyObject``, and only on the merge path. A Hash
    legitimately gains keys, and a value carrying ``__class__`` is replacing
    the object rather than merging into it, so neither comes through here.

    An object the *editor* wrote carries its complete field set, because
    Marshal writes every instance variable an object has -- which is what makes
    this safe to refuse on rather than warn about. A file the editor did not
    write is untrusted like any other, so the refusal names the fields the
    object does carry and an object carrying none is left alone.

    Not closed by this: an object inside an *array*, given wholesale with a
    ``__class__``. That is not a merge, so it never reaches here, and
    ``from_json`` builds it with only `check_ivar_name` -- a typo in
    ``{"learnings": [{"__class__": "RPG::Class::Learning", "levl": 5}]}``
    still invents a field.
    """
    if not target.ivars:
        # Nothing to compare against, so nothing is refused -- the same escape
        # `refuse_unknown_fields` makes one level up. Without it a nested
        # object carrying no ivars refused every field with "it carries no
        # fields at all", while the identical patch at the top level was
        # allowed.
        return
    unknown = sorted(name for name in patch
                     if not name.startswith("__") and not target.has(name))
    if not unknown:
        return
    raise ConversionError(
        "%s is %s and has no field%s %s. It carries %s. Nothing here invents "
        "a field, because the editor rejects an object with instance "
        "variables it does not expect -- check the spelling, or pass a "
        "\"__class__\" to replace the whole object."
        % (path, target.cls, "s" if len(unknown) > 1 else "",
           ", ".join(repr(name) for name in unknown),
           named(target.fields()) or "no fields at all"))


def refuse_invented_in_replacement(existing: Any, replacement: Any,
                                   path: str, depth: int = 0) -> None:
    """Refuse a field name in a replacement value that the thing it replaces lacks.

    A value carrying ``__class__`` replaces an object outright rather than
    merging into it, so it reaches the file through `from_json`, which checks
    only that each name is a syntactically valid Ruby ivar. A typo inside one
    -- ``{"bgm": {"__class__": "RPG::BGM", "naem": "Town1", ...}}`` -- was
    therefore written, and reported as the change the caller asked for, by
    every tool that takes a patch and by raw_data.

    Compared against the value being replaced rather than a documented field
    table, because this module sits below :mod:`schema` and because the object
    already there is the better authority anyway: it is what the editor wrote.
    A list is compared element-wise against any existing element of the same
    class, which is what catches a typo in one entry of an @learnings array.

    Walks objects, lists and hashes, because those are the three containers
    the format nests objects inside. Hashes were missed at first, and the gap
    was closed by coincidence rather than by design: the format has two hash
    locations -- a map's `events` and the MapInfos file -- and raw_data
    refuses replacing `events` for an unrelated reason while no patch path
    reaches MapInfos whole. Neither of those is this rule, and a caller
    reading the docstring would have believed this rule applied.

    Where there is nothing to compare against -- an empty array, a class not
    already present, a hash key the existing hash does not have -- nothing is
    refused. That is the same escape `refuse_unknown_fields` makes, and for
    the same reason: this narrows what a write may say, and never blocks one
    it cannot judge.
    """
    if depth > MAX_JSON_DEPTH:
        return
    if isinstance(replacement, RubyObject):
        if not isinstance(existing, RubyObject) or existing.cls != replacement.cls:
            return
        if not existing.ivars:
            return
        unknown = sorted(name for name in replacement.fields()
                         if not existing.has(name))
        if unknown:
            raise ConversionError(
                "%s would be replaced by %s carrying field%s %s, which the %s "
                "already there does not have. It carries %s. Nothing here "
                "invents a field, because the editor rejects an object with "
                "instance variables it does not expect -- check the spelling."
                % (path or "the value", replacement.cls,
                   "s" if len(unknown) > 1 else "",
                   ", ".join(repr(name) for name in unknown),
                   existing.cls, named(existing.fields())))
        for name in replacement.fields():
            refuse_invented_in_replacement(
                existing.get(name), replacement.get(name),
                "%s.%s" % (path, name) if path else name, depth + 1)
        return
    if isinstance(replacement, RubyHash) and isinstance(existing, RubyHash):
        # By key, because a hash in this format is an index -- event id to
        # event -- so the value under a key is the thing the value under that
        # same key replaces. A key the existing hash lacks has no authority
        # behind it and is left alone, the same escape as everywhere else here.
        old_values = dict(existing.items())
        for key, item in replacement.items():
            if key in old_values:
                refuse_invented_in_replacement(
                    old_values[key], item, "%s[%r]" % (path, key), depth + 1)
        return
    if isinstance(replacement, list) and isinstance(existing, list):
        # One pass over `existing` to find the first object of each class,
        # rather than a fresh scan per replacement element. The scan
        # short-circuited only on a match, so its worst case was an existing
        # list with no objects in it at all: a field holding a million
        # integers patched with ten thousand objects is 10^10 isinstance calls
        # on the write path, before anything is written.
        first_of_class: dict[str, RubyObject] = {}
        for old in existing:
            if isinstance(old, RubyObject) and old.cls not in first_of_class:
                first_of_class[old.cls] = old
        for index, item in enumerate(replacement):
            here = "%s[%d]" % (path, index)
            if isinstance(item, (list, RubyHash)):
                # A container inside a list: compared against the element at
                # the same position, since there is no class to match on. This
                # branch used to `continue`, so a list of lists -- and a list
                # of hashes -- went through unexamined.
                if index < len(existing):
                    refuse_invented_in_replacement(
                        existing[index], item, here, depth + 1)
                continue
            if not isinstance(item, RubyObject):
                continue
            twin = first_of_class.get(item.cls)
            if twin is not None:
                refuse_invented_in_replacement(twin, item, here, depth + 1)


# How much of one string from a project file is put into a message or a
# reply. The value comes out of a file this server did not write, and a
# crafted name would otherwise be repeated into every finding about it.
#
# Moved here from `analysis`, where it applied to findings only while four
# copies of the same decode in the tools layer applied it to nothing: a
# 1,000,006-character map name went into map_list's reply whole, carrying an
# ANSI escape, while the same value in a finding came back capped.
MAX_TEXT_CHARS = 200


def decoded_text(codec: TextCodec, value: Any) -> str:
    """Text from a project file, ready to put in a message or a reply.

    Sanitised and length-capped. Sliced *before* decoding, because the cap is
    meant to bound the work as well as the result and a crafted name can be
    megabytes; and it says how many bytes or characters there really were,
    because a name silently cut is how a caller ends up looking for the wrong
    map.

    Moved here verbatim from `analysis._text` rather than written fresh: the
    four copies in the tools layer answered "" for everything that was not a
    RubyString, and this distinguishes a name that is absent from a name that
    is an Array -- which is the more useful answer in a reply as well as in a
    finding.
    """
    if isinstance(value, RubyString):
        raw = value.data
        text = codec.decode(raw[:MAX_TEXT_CHARS * 4])
        if len(raw) > MAX_TEXT_CHARS * 4:
            return (printable_text(text[:MAX_TEXT_CHARS])
                    + "... (%d bytes)" % len(raw))
    elif value is None:
        return ""
    elif isinstance(value, (str, int, float, bool)):
        text = str(value)
    else:
        # A name that is an Array or a Hash is not a name. Say what it was
        # rather than stringify the whole structure.
        return "<%s>" % type(value).__name__
    text = printable_text(text)
    if len(text) > MAX_TEXT_CHARS:
        return text[:MAX_TEXT_CHARS] + "... (%d characters)" % len(text)
    return text


# `repr` with every container bounded, so a crafted value cannot make a
# message out of its own length. The per-container counts are generous
# relative to MAX_TEXT_CHARS -- the slice below still decides the final
# length -- and the point of each is only that the *building* stops.
_BOUNDED_REPR = reprlib.Repr()
_BOUNDED_REPR.maxlevel = 4
_BOUNDED_REPR.maxstring = MAX_TEXT_CHARS
_BOUNDED_REPR.maxother = MAX_TEXT_CHARS
for _container in ("list", "tuple", "dict", "set", "frozenset", "array",
                   "deque"):
    setattr(_BOUNDED_REPR, "max" + _container, 20)


def brief(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """A value quoted into a message, bounded before it is built.

    The bound comes first, which is the point and the half one of the two
    copies of this helper missed: `analysis._brief`'s only caller passes a
    MapInfos hash key straight out of an untrusted file, so a crafted key
    was repr'd whole and cut afterwards -- a cap that bounds the result and
    not the work, which is the opposite of the rule `decoded_text` states
    for itself just above.

    A string is sliced. Everything else goes through `reprlib`, because
    slicing a `repr` is not bounding it: a 200,000-entry Array is a legal
    MapInfos key, and `repr` of one built 1.3 MB to return 224 characters --
    measured. `reprlib` stops at the limits below instead of building the
    whole thing first. Stdlib, so it costs no dependency.

    Here rather than in either caller: `registry.echo` had the same job and
    the pre-slice for strings, `analysis._brief` had the job and neither,
    and the two used different constants.
    """
    if isinstance(value, RubyString):
        return brief(value.data, limit)
    if isinstance(value, (str, bytes, bytearray)) and len(value) > limit:
        return "%r... (%d %s)" % (
            value[:limit], len(value),
            "characters" if isinstance(value, str) else "bytes")
    if isinstance(value, int) and not isinstance(value, bool):
        # Before `repr`, not through it. CPython 3.11 refuses to render an
        # integer above 4300 digits at all -- `ValueError: Exceeds the
        # limit for integer string conversion` -- and a MapInfos key is
        # whatever the file put there, so a Bignum key raised that out of
        # `check_map_tree` and ended the whole validate run. Reported by
        # size, which is the only fact about it worth a message.
        if value.bit_length() > limit * 3:
            return "<Integer, %d bits>" % value.bit_length()
    shown = _BOUNDED_REPR.repr(value)
    if len(shown) > limit:
        return shown[:limit] + "... (%d characters)" % len(shown)
    return shown


def normalize_strings(value: Any, codec: TextCodec, depth: int = 0) -> Any:
    """Restyle every string in a freshly built object tree for this project.

    Templates are written once, in UTF-8 and with no string form; this re-encodes
    them into the project's code page and puts the engine's own string form on
    them, so a new event or map is shaped like the editor's own output.

    Only ever applied to objects this server just created: it assumes the bytes
    it finds are the UTF-8 the templates in :mod:`schema` produce.
    """
    if depth > MAX_NORMALIZE_DEPTH:
        # Templates are a handful of levels deep, so this is unreachable for
        # them; it exists so the function cannot recurse without bound.
        return value
    if isinstance(value, RubyString):
        if codec.name != "utf-8":
            value.data = codec.encode(value.data.decode("utf-8", "surrogateescape"))
        value.ivars = dict(codec.string_ivars)
        return value
    if isinstance(value, RubyObject):
        for item in value.ivars.values():
            normalize_strings(item, codec, depth + 1)
    elif isinstance(value, list):
        for item in value:
            normalize_strings(item, codec, depth + 1)
    elif isinstance(value, RubyHash):
        for key, item in value.pairs:
            normalize_strings(key, codec, depth + 1)
            normalize_strings(item, codec, depth + 1)
    return value


# How much of a value one change line shows. Shorter than MAX_TEXT_CHARS
# because a change line is a phrase inside a sentence, and `apply_patch`
# builds one per changed field.
MAX_CHANGE_LINE_CHARS = 40


def _change_line(value: Any, codec: TextCodec) -> str:
    """One value as it appears in a change line: "before -> after".

    Named for where it goes, because it used to be a second `_brief` in a
    file that has one -- same private name, different arity. And it had the
    defect `brief`'s docstring says the consolidation fixed: it decoded the
    *whole* string before cutting it to forty characters, and its `repr`
    fallback had no cap at all. Its callers are `apply_patch`'s change
    lines, where the value comes straight out of an untrusted file and the
    joined result becomes the summary written to changes.jsonl.

    The container renderings stay here rather than moving into `brief`:
    "<Table 20x15x4>" is a fact about a change, and `brief` answers what a
    value *is*.
    """
    if isinstance(value, RubyString):
        # Through `decoded_text`, which slices the raw bytes before it
        # decodes them, and then trimmed to a change line's length. The old
        # code decoded the *whole* string and cut afterwards -- a cap on the
        # result and not the work, on a value straight out of a file -- and
        # `brief` alone would repr the bytes rather than read them.
        text = decoded_text(codec, value)
        if len(text) > MAX_CHANGE_LINE_CHARS:
            text = text[:MAX_CHANGE_LINE_CHARS - 3] + "..."
        return '"%s"' % text
    if isinstance(value, RubyObject):
        return "<%s>" % printable_text(value.cls)
    if isinstance(value, Table):
        return "<Table %dx%dx%d>" % (value.xsize, value.ysize, value.zsize)
    if isinstance(value, RubyHash):
        return "<Hash %d entries>" % len(value)
    if isinstance(value, list):
        return "<Array %d items>" % len(value)
    return brief(value, MAX_CHANGE_LINE_CHARS)
