"""Python stand-ins for the Ruby objects found inside RPG Maker data files.

Every value Marshal can carry has a representation here, chosen so that a
load/dump round trip is *byte identical* even for constructs this server does
not understand. That matters more than convenience: a data file is a user's
game, and a lossy rewrite is indistinguishable from data loss.

Strings are the subtle case. RGSS3 (VX Ace) runs Ruby 1.9, which tags every
string with its encoding by wrapping it in an instance-variable record; RGSS2
(VX) runs Ruby 1.8, whose strings are bare bytes in whatever code page the
editor ran under. Neither is decoded here: bytes are kept verbatim, any wrapper
is preserved as-is, and decoding is deferred to the presentation layer.
"""

from __future__ import annotations

import struct
from array import array
from typing import Any, Iterable, Literal

# Guard rails for `Table`. RGSS itself imposes no limit, but a map axis above a
# few thousand tiles is not a real map, and a corrupt or hostile header must not
# be able to make us allocate for one. 16 million cells is ~32 MB of payload,
# far beyond any real project, and is the point where refusing beats trying.
MAX_TABLE_AXIS = 65535
MAX_TABLE_CELLS = 16 * 1024 * 1024

# Cells are unsigned 16-bit on disk; some tables are semantically signed.
CELL_SIGN_BIT = 0x8000
CELL_MODULUS = 0x10000
CELL_MASK = 0xFFFF


# How many field names a refusal lists before it says how many more there
# are. The names come out of the project file, so their number is not this
# server's to choose: an entry carrying five thousand instance variables
# produced a fifty-nine-kilobyte error message, and `_tool_error` does not
# truncate.
MAX_NAMES_LISTED = 40


def named(names: "Any", limit: int = MAX_NAMES_LISTED,
          separator: str = ", ") -> str:
    """A list of field names for a message, capped and counted.

    Sorted by the caller where order matters; this only bounds it. The count
    is kept because "it carries 40 fields" and "it carries 5000" are different
    facts, and the one a caller needs is usually the second.

    Here rather than in the tools layer because `convert` needs it too and
    sits below that layer -- which is exactly the objection its own copy of
    this function raised, and the reason for a shared home rather than a
    second copy. The two had already drifted: one grew a `limit` parameter and
    the other hard-coded the constant.
    """
    names = list(names)
    return _capped_join(names, len(names), limit, separator)


def rendered(values: "Any", render: "Any", limit: int = MAX_NAMES_LISTED,
             separator: str = ", ") -> str:
    """:func:`named`, for entries that have to be built before they can be said.

    A separate function rather than a `render` parameter on ``named``, because
    the point is *where* the work happens: ``named(render(v) for v in values)``
    has rendered every entry by the time ``named`` sees the first one, and for
    values read out of a project file that rendering is the cost being
    bounded, not the length of the result. Nothing in this project counted
    bytes -- only nodes -- so an Array of a million links to one megabyte
    string was a single step of a walk budget and a terabyte of decoding.
    """
    items = values if isinstance(values, (list, tuple)) else list(values)
    shown = [render(value) for value in items[:limit]]
    return _capped_join(shown, len(items), limit, separator)


def _capped_join(shown: list[str], total: int, limit: int,
                 separator: str) -> str:
    if total <= limit:
        return separator.join(shown)
    return "%s and %d more" % (separator.join(shown[:limit]), total - limit)


# Kept, because they are text and not commands: a note field really does
# carry newlines and tabs, and mangling those would be a worse answer than
# the one this function exists to avoid.
KEPT_CONTROLS = "\t\n\r"


def printable_text(text: str) -> str:
    """Text that is safe to put in a JSON response, and in a terminal.

    Two problems, one answer. A name read out of a file is decoded with
    ``surrogateescape``, so it can hold lone surrogates; ``json.dumps`` emits
    those happily and the UTF-8 transport then cannot encode them, which
    would end the session over one corrupt byte in one class name. And a
    reply is read by a terminal, where an ESC in a name out of a file this
    server did not write is a sequence that terminal acts on.

    One filter covers both, because no character that fails UTF-8 encoding is
    printable -- checked over the whole code-point range, not assumed: every
    unencodable character is a lone surrogate, and those are category Cs.
    This used to be a separate `encode("utf-8", "replace")` and nothing at
    all for the control characters, while `decoded_text` claimed to sanitise;
    the test that was supposed to hold that claim asserted about the
    *longest* name in the reply rather than the crafted one, so it passed
    while an escape went into `map_list` whole.
    """
    # Every real name takes this branch, and both are C-level checks: this
    # runs once per class name and once per field name across a project.
    if text.isprintable():
        return text
    # A question mark rather than a deletion, so the length still says
    # something about what was there.
    return "".join(char if char.isprintable() or char in KEPT_CONTROLS
                   else "?" for char in text)


def _ivar_key(name: str) -> str:
    """``"name"`` and ``"@name"`` both address the ivar ``@name``."""
    return name if name.startswith("@") else "@" + name


def _bare_name(key: str) -> str:
    """``"@name"`` -> ``"name"``, stripping exactly one ``@``.

    Exactly one: a class variable ``@@x`` must not collapse onto ``@x``.
    """
    return key[1:] if key.startswith("@") else key


class RubySymbol(str):
    """A Ruby ``Symbol``. Subclasses ``str`` so it can be used as a dict key.

    ``ivars`` carries the encoding record Ruby >= 1.9 wraps around a symbol
    whose name is not ASCII, so such a symbol can be written back as it came.
    """

    __slots__ = ("ivars",)

    def __new__(cls, value: str, ivars: dict[str, Any] | None = None):
        self = super().__new__(cls, value)
        self.ivars = dict(ivars or {})
        return self

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ":" + str(self)


class RubyString:
    """A Ruby ``String``: raw bytes, plus any instance-variable record.

    ``ivars`` holds what an ``I`` wrapper carried -- ``{"E": True}`` for a
    UTF-8 string written by Ruby >= 1.9, empty for a bare Ruby 1.8 string.
    """

    __slots__ = ("data", "ivars")

    def __init__(self, data: bytes | bytearray, ivars: dict[str, Any] | None = None):
        self.data = bytes(data)
        self.ivars = dict(ivars or {})

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RubyString):
            return self.data == other.data and self.ivars == other.ivars
        if isinstance(other, (bytes, bytearray)):
            return self.data == bytes(other)
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.data)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyString(%r)" % (self.data,)


class RubyObject:
    """A plain Ruby object: a class name plus its instance variables.

    Keys in ``ivars`` keep their leading ``@`` exactly as Marshal stores them.
    """

    __slots__ = ("cls", "ivars")

    def __init__(self, cls: str, ivars: dict[str, Any] | None = None):
        self.cls = cls
        self.ivars: dict[str, Any] = dict(ivars or {})

    def get(self, name: str, default: Any = None) -> Any:
        """Read an ivar by bare name (``"name"`` finds ``@name``)."""
        return self.ivars.get(_ivar_key(name), default)

    def set(self, name: str, value: Any) -> None:
        self.ivars[_ivar_key(name)] = value

    def has(self, name: str) -> bool:
        return _ivar_key(name) in self.ivars

    def fields(self) -> list[str]:
        """Bare ivar names, in file order."""
        return [_bare_name(key) for key in self.ivars]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RubyObject):
            return self.cls == other.cls and self.ivars == other.ivars
        return NotImplemented

    # Equality is by value, but hashing stays by identity: these are mutable,
    # and the Marshal link tables key on id() rather than on hash.
    __hash__ = object.__hash__

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyObject(%s, %s)" % (self.cls, list(self.ivars))


class RubyUserDef:
    """An object serialised through ``_dump``/``_load`` (Marshal type ``u``).

    The payload is opaque bytes. ``Table``, ``Color`` and ``Tone`` are decoded
    into richer types below; anything else stays here untouched.
    """

    __slots__ = ("cls", "payload", "ivars")

    def __init__(self, cls: str, payload: bytes, ivars: dict[str, Any] | None = None):
        self.cls = cls
        self.payload = bytes(payload)
        self.ivars = dict(ivars or {})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyUserDef(%s, %d bytes)" % (self.cls, len(self.payload))


class RubyUserMarshal:
    """An object serialised through ``marshal_dump``/``marshal_load`` (type ``U``)."""

    __slots__ = ("cls", "value")

    def __init__(self, cls: str, value: Any):
        self.cls = cls
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyUserMarshal(%s)" % self.cls


class RubyData:
    """An object serialised through Marshal's C-level ``_dump_data`` (type ``d``).

    Nothing here interprets the payload; it is carried so that a file using one
    can still be read and written back unchanged.
    """

    __slots__ = ("cls", "value")

    def __init__(self, cls: str, value: Any):
        self.cls = cls
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyData(%s)" % self.cls


class RubyIvars:
    """Instance variables attached to a value that cannot hold them itself.

    Ruby can wrap *any* value in an ``I`` record. Most targets here have an
    ``ivars`` slot of their own; an Array, Hash or Float does not, so rather
    than dropping the record -- which would break the byte-identical round trip
    this codec promises -- the value is wrapped in one of these and the writer
    re-emits the ``I`` record around it.
    """

    __slots__ = ("inner", "ivars")

    def __init__(self, inner: Any, ivars: dict[str, Any] | None = None):
        self.inner = inner
        self.ivars = dict(ivars or {})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyIvars(%r, %s)" % (self.inner, list(self.ivars))


class RubyStruct:
    """A Ruby ``Struct`` (type ``S``)."""

    __slots__ = ("cls", "members")

    def __init__(self, cls: str, members: dict[str, Any] | None = None):
        self.cls = cls
        self.members: dict[str, Any] = dict(members or {})

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyStruct(%s, %s)" % (self.cls, list(self.members))


ClassRefKind = Literal["class", "module", "old_module"]


class RubyClassRef:
    """A reference to a Class (type ``c``) or Module (type ``m`` / ``M``)."""

    __slots__ = ("name", "kind")

    def __init__(self, name: str, kind: ClassRefKind = "class"):
        self.name = name
        self.kind: ClassRefKind = kind

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyClassRef(%s, %s)" % (self.name, self.kind)


class RubyExtended:
    """An object with singleton modules mixed in (type ``e``)."""

    __slots__ = ("modules", "inner")

    def __init__(self, modules: list[str], inner: Any):
        self.modules = modules
        self.inner = inner

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyExtended(%s)" % self.modules


class RubyUClass:
    """An instance of a subclass of String/Array/Hash (type ``C``)."""

    __slots__ = ("cls", "inner")

    def __init__(self, cls: str, inner: Any):
        self.cls = cls
        self.inner = inner

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyUClass(%s)" % self.cls


class RubyRegexp:
    """A Ruby ``Regexp`` (type ``/``), kept as source plus option bits.

    ``options`` is Ruby's flag bitmask -- 1 ``i`` (ignore case), 2 ``x``
    (extended), 4 ``m`` (multiline). The source is never compiled here; it is
    carried verbatim so it can be written back.
    """

    __slots__ = ("source", "options")

    def __init__(self, source: bytes, options: int):
        self.source = bytes(source)
        self.options = options

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyRegexp(%r, %d)" % (self.source, self.options)


class RubyFloat(float):
    """A Ruby ``Float`` that remembers its original textual form.

    Ruby writes floats as a decimal string, and Ruby 1.8 sometimes followed it
    with a NUL and extra mantissa bits. Re-emitting the bytes we read keeps
    round trips exact for values nobody edited.
    """

    __slots__ = ("raw",)

    raw: bytes | None

    def __new__(cls, value: float, raw: bytes | None = None):
        self = super().__new__(cls, value)
        self.raw = raw
        return self


class RubyHash:
    """A Ruby ``Hash``, stored as ordered pairs so non-hashable keys survive.

    Lookups are a linear scan, which is the right trade at the sizes RPG Maker
    reaches (a map's events, a project's MapInfos -- hundreds of entries, not
    millions) and is what lets duplicate or unhashable keys round trip. A caller
    doing repeated lookups in a loop should hoist ``dict(hash.items())`` out of
    it rather than paying the scan each time.
    """

    __slots__ = ("pairs", "default")

    def __init__(self, pairs: Iterable[tuple[Any, Any]] | None = None, default: Any = None):
        self.pairs: list[tuple[Any, Any]] = list(pairs or [])
        self.default = default

    # -- mapping-ish helpers, keyed by value equality -------------------------
    def _find(self, key: Any) -> int | None:
        for index, (candidate, _) in enumerate(self.pairs):
            if candidate == key:
                return index
        return None

    def get(self, key: Any, default: Any = None) -> Any:
        index = self._find(key)
        return self.pairs[index][1] if index is not None else default

    def set(self, key: Any, value: Any) -> None:
        index = self._find(key)
        if index is None:
            self.pairs.append((key, value))
        else:
            self.pairs[index] = (key, value)

    def delete(self, key: Any) -> bool:
        index = self._find(key)
        if index is None:
            return False
        del self.pairs[index]
        return True

    def keys(self) -> list[Any]:
        return [k for k, _ in self.pairs]

    def values(self) -> list[Any]:
        return [v for _, v in self.pairs]

    def items(self) -> list[tuple[Any, Any]]:
        return list(self.pairs)

    def sorted_items(self) -> list[tuple[Any, Any]]:
        """Items ordered by key when keys are comparable (e.g. map event ids)."""
        try:
            return sorted(self.pairs, key=lambda kv: kv[0])
        except TypeError:
            return list(self.pairs)

    def __len__(self) -> int:
        return len(self.pairs)

    def __contains__(self, key: Any) -> bool:
        return self._find(key) is not None

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RubyHash):
            return self.pairs == other.pairs and self.default == other.default
        return NotImplemented

    __hash__ = object.__hash__

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyHash(%d pairs)" % len(self.pairs)


def _infer_dim(xsize: int, ysize: int, zsize: int) -> int:
    """How many dimensions a Table with these extents declares.

    RGSS stores the declared dimension separately from the extents, and
    ``Table#[]`` takes that many subscripts, so it has to match.
    """
    if zsize > 1:
        return 3
    return 2 if ysize > 1 else 1


class Table:
    """The RGSS ``Table`` class: a 1-, 2- or 3-dimensional grid of 16-bit cells.

    Marshalled through ``_dump`` as ``[dim, xsize, ysize, zsize, size].pack('L5')``
    followed by ``size`` unsigned 16-bit little-endian values. Map tile data is
    the big one: ``xsize * ysize * zsize`` cells addressed ``[x, y, z]``.

    Cells live in an ``array("H")`` rather than a list: a 100k-cell map costs
    ~200 KB this way against ~3.6 MB of boxed Python ints, and loaded files are
    held in the project cache for the life of the server. Indexing, slicing,
    ``len`` and iteration behave as they would for a list.

    Values are the unsigned ones found on disk (0..65535). Some tables are
    semantically signed (animation cell data uses -1 for "no cell"), so
    :meth:`get` takes ``signed=True`` rather than guessing per table.
    """

    __slots__ = ("dim", "xsize", "ysize", "zsize", "data")

    CLASS_NAME = "Table"
    HEADER = struct.Struct("<5I")
    CELL_BYTES = 2

    def __init__(self, xsize: int = 0, ysize: int = 1, zsize: int = 1,
                 dim: int | None = None, data: Iterable[int] | None = None):
        _check_extents(xsize, ysize, zsize)
        self.xsize = xsize
        self.ysize = ysize
        self.zsize = zsize
        self.dim = dim if dim is not None else _infer_dim(xsize, ysize, zsize)
        size = xsize * ysize * zsize
        if data is None:
            self.data = array("H", bytes(size * self.CELL_BYTES))
        else:
            self.data = array("H", data)
            if len(self.data) != size:
                raise ValueError(
                    "Table data length %d != %d*%d*%d = %d"
                    % (len(self.data), xsize, ysize, zsize, size)
                )

    # -- serialisation --------------------------------------------------------
    @classmethod
    def from_payload(cls, payload: bytes) -> "Table":
        header = cls.HEADER.size
        if len(payload) < header:
            raise ValueError("Table payload too short: %d bytes" % len(payload))
        dim, xsize, ysize, zsize, size = cls.HEADER.unpack_from(payload, 0)
        if not 1 <= dim <= 3:
            raise ValueError("Table declares %d dimensions, expected 1, 2 or 3" % dim)
        # Validate the extents individually, not just their product: a header
        # of xsize=0xFFFFFFFF, ysize=0 has a product of 0 and would otherwise be
        # accepted, leaving an absurd xsize for every later consumer to iterate.
        _check_extents(xsize, ysize, zsize)
        if xsize * ysize * zsize != size:
            raise ValueError(
                "Table header inconsistent: %d*%d*%d != declared size %d"
                % (xsize, ysize, zsize, size)
            )
        expected = header + size * cls.CELL_BYTES
        if len(payload) != expected:
            # Not just truncation: trailing bytes would be silently dropped by
            # to_payload, so hand the payload back to the caller untouched.
            raise ValueError(
                "Table payload is %d bytes, expected exactly %d"
                % (len(payload), expected)
            )
        cells = array("H")
        cells.frombytes(payload[header:expected])
        if _BIG_ENDIAN:
            cells.byteswap()
        return cls(xsize, ysize, zsize, dim=dim, data=cells)

    def to_payload(self) -> bytes:
        size = len(self.data)
        if self.xsize * self.ysize * self.zsize != size:
            raise ValueError(
                "Table extents %dx%dx%d disagree with its %d cells"
                % (self.xsize, self.ysize, self.zsize, size)
            )
        header = self.HEADER.pack(self.dim, self.xsize, self.ysize, self.zsize, size)
        if _BIG_ENDIAN:
            swapped = array("H", self.data)
            swapped.byteswap()
            return header + swapped.tobytes()
        return header + self.data.tobytes()

    # -- cell access ----------------------------------------------------------
    def _index(self, x: int, y: int = 0, z: int = 0) -> int:
        if not (0 <= x < self.xsize and 0 <= y < self.ysize and 0 <= z < self.zsize):
            raise IndexError(
                "(%d, %d, %d) outside table %dx%dx%d"
                % (x, y, z, self.xsize, self.ysize, self.zsize)
            )
        return x + y * self.xsize + z * self.xsize * self.ysize

    def get(self, x: int, y: int = 0, z: int = 0, signed: bool = False) -> int:
        value = self.data[self._index(x, y, z)]
        if signed and value >= CELL_SIGN_BIT:
            value -= CELL_MODULUS
        return value

    def set(self, x: int, y: int = 0, z: int = 0, value: int = 0) -> None:
        self.data[self._index(x, y, z)] = value & CELL_MASK

    def resize(self, xsize: int, ysize: int, zsize: int) -> "Table":
        """Return a resized copy, preserving cells that still fit.

        The declared dimension is recomputed from the new extents: RGSS
        subscripts a Table by ``dim``, so carrying a 3-D ``dim`` onto a
        single-layer table would leave its cells unreachable in the engine.
        """
        out = Table(xsize, ysize, zsize)
        width = min(xsize, self.xsize)
        if width:
            # Row at a time: cells are contiguous along x, and a per-cell copy
            # through get/set costs two Python calls per cell (seconds, on a
            # large map resize).
            for z in range(min(zsize, self.zsize)):
                for y in range(min(ysize, self.ysize)):
                    source = y * self.xsize + z * self.xsize * self.ysize
                    target = y * xsize + z * xsize * ysize
                    out.data[target:target + width] = self.data[source:source + width]
        return out

    @property
    def size(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Table(%dx%dx%d, dim=%d)" % (self.xsize, self.ysize, self.zsize, self.dim)


_BIG_ENDIAN = struct.pack("=H", 1) != struct.pack("<H", 1)


def _check_extents(xsize: int, ysize: int, zsize: int) -> None:
    """Refuse extents no real table has, before allocating for them."""
    for name, value in (("xsize", xsize), ("ysize", ysize), ("zsize", zsize)):
        if not isinstance(value, int) or value < 0:
            raise ValueError("Table %s must be a non-negative whole number, got %r"
                             % (name, value))
        if value > MAX_TABLE_AXIS:
            raise ValueError("Table %s of %d exceeds the %d cell limit per axis"
                             % (name, value, MAX_TABLE_AXIS))
    if xsize * ysize * zsize > MAX_TABLE_CELLS:
        raise ValueError(
            "a %dx%dx%d table is %d cells, above the %d this server will allocate"
            % (xsize, ysize, zsize, xsize * ysize * zsize, MAX_TABLE_CELLS)
        )


class RGSSFloats:
    """Shared behaviour for ``Color`` and ``Tone``: four packed doubles."""

    __slots__ = ("values",)
    CLASS_NAME = ""
    FIELDS: tuple[str, ...] = ()
    PACKER = struct.Struct("<4d")

    def __init__(self, values: Iterable[float]):
        if not self.CLASS_NAME:
            raise TypeError("RGSSFloats is abstract; use Color or Tone")
        vals = [float(v) for v in values]
        if len(vals) != len(self.FIELDS):
            raise ValueError("%s needs %d values, got %d"
                             % (self.CLASS_NAME, len(self.FIELDS), len(vals)))
        self.values = vals

    @classmethod
    def from_payload(cls, payload: bytes):
        if len(payload) != cls.PACKER.size:
            raise ValueError(
                "%s payload must be %d bytes, got %d"
                % (cls.CLASS_NAME, cls.PACKER.size, len(payload))
            )
        return cls(cls.PACKER.unpack(payload))

    def to_payload(self) -> bytes:
        return self.PACKER.pack(*self.values)

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.FIELDS, self.values))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and self.values == other.values

    # Value equality, identity hashing: mutable, and the link tables key on id().
    __hash__ = object.__hash__

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "%s%s" % (self.CLASS_NAME, tuple(self.values))


class Color(RGSSFloats):
    __slots__ = ()
    CLASS_NAME = "Color"
    FIELDS = ("red", "green", "blue", "alpha")


class Tone(RGSSFloats):
    __slots__ = ()
    CLASS_NAME = "Tone"
    FIELDS = ("red", "green", "blue", "gray")


def ruby_children(value: "Any") -> "list[Any]":
    """Every value nested one level inside a Ruby wrapper.

    "Which of these wrappers contains other values" was encoded in five
    separate walks and they disagreed: `project.collect_strings` descended
    into three kinds where `schema._observe` walks eight, so encoding
    detection never saw a string inside a `RubyIvars` -- and `analysis`' own
    comment records that this codec really does wrap values in `RubyIvars`
    in files the editor writes. One function, so the set is stated once and a
    wrapper added later is a container everywhere or nowhere.

    Hash keys as well as values, because a Ruby Hash may be keyed by a
    String and a walk that skipped keys would not find it. Ivar records are
    included wherever a wrapper carries them, which is the half three of the
    five copies missed.

    Callers keep their own memo and their own budget: this answers what is
    inside one value, and says nothing about how far to go.
    """
    if isinstance(value, RubyObject):
        return list(value.ivars.values())
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, RubyHash):
        out: list[Any] = []
        for key, item in value.pairs:
            out.append(key)
            out.append(item)
        return out
    if isinstance(value, RubyStruct):
        return list(value.members.values())
    if isinstance(value, (RubyUserMarshal, RubyData)):
        # These name a class and keep their whole state in one nested value.
        return [value.value]
    if isinstance(value, (RubyIvars, RubyUClass, RubyExtended)):
        return [value.inner] + list(getattr(value, "ivars", {}).values())
    if isinstance(value, (RubyUserDef, RubyString, RubySymbol)):
        # A payload is bytes, not a value tree; the ivar record is a tree.
        return list(value.ivars.values())
    return []


def as_list(value: "Any") -> "list[Any]":
    """A value out of a project file, as something safe to iterate.

    An ivar the class model calls an Array can hold anything at all in a
    file this server did not write, and ``for x in 7`` is a TypeError that
    escapes the whole call rather than becoming one finding.

    `or []` is not this, and the difference is the defect: `RubyHash`
    defines `__len__` and neither `__iter__` nor `__getitem__`, so an empty
    hash is falsy and becomes `[]` while a *non-empty* one is truthy and
    reaches the loop. Measured -- a Hash of one pair where a command's
    @parameters belongs made `event_get` answer "'RubyHash' object is not
    subscriptable" and `search` "not iterable".

    Here rather than in `analysis`, which is where it was: `commands` needs
    it too and sits below that, so the copy in `commands` was nine `or []`
    spellings instead.
    """
    return value if isinstance(value, list) else []


def as_int(value: "Any") -> "int | None":
    """A value out of a project file as an int, or None if it is not one.

    Beside `as_list` and for the same reason: `bool` is a subclass of `int`
    in Python and is not an id in any RGSS file, so `isinstance(x, int)`
    alone answers yes for `True`.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
