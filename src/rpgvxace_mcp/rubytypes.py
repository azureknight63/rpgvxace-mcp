"""Python stand-ins for the Ruby objects found inside RPG Maker VX ``.rvdata`` files.

Every value Marshal can carry has a representation here, chosen so that a
load/dump round trip is *byte identical* even for constructs this server does
not understand. That matters more than convenience: an ``.rvdata`` file is a
user's game, and a lossy rewrite is indistinguishable from data loss.
"""

from __future__ import annotations

import struct
from typing import Any, Iterable


class RubySymbol(str):
    """A Ruby ``Symbol``. Subclasses ``str`` so it can be used as a dict key."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return ":" + str(self)


class RubyString:
    """A Ruby 1.8 ``String``: raw bytes, with no encoding attached.

    RGSS2 runs Ruby 1.8, whose strings are byte strings. Text is stored in
    whatever code page the editor ran under (Windows-1252 for Western installs,
    Shift_JIS for Japanese ones), so bytes are kept verbatim and decoding is
    deferred to the presentation layer.
    """

    __slots__ = ("data", "ivars")

    def __init__(self, data: bytes | bytearray, ivars: dict[str, Any] | None = None):
        self.data = bytes(data)
        # Ruby >= 1.9 wraps strings in an ivar record to carry :E / :encoding.
        # VX files never contain one; preserving any we find keeps foreign files
        # (e.g. VX Ace data dropped into a VX project) round-tripping.
        self.ivars = ivars or {}

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
        return self.ivars.get(name if name.startswith("@") else "@" + name, default)

    def set(self, name: str, value: Any) -> None:
        self.ivars[name if name.startswith("@") else "@" + name] = value

    def has(self, name: str) -> bool:
        return (name if name.startswith("@") else "@" + name) in self.ivars

    def fields(self) -> list[str]:
        """Bare ivar names, in file order."""
        return [k.lstrip("@") for k in self.ivars]

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
        self.ivars = ivars or {}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyUserDef(%s, %d bytes)" % (self.cls, len(self.payload))


class RubyUserMarshal:
    """An object serialised through ``marshal_dump``/``marshal_load`` (type ``U``)."""

    __slots__ = ("cls", "value")

    def __init__(self, cls: str, value: Any):
        self.cls = cls
        self.value = value


class RubyStruct:
    """A Ruby ``Struct`` (type ``S``)."""

    __slots__ = ("cls", "members")

    def __init__(self, cls: str, members: dict[str, Any] | None = None):
        self.cls = cls
        self.members: dict[str, Any] = dict(members or {})


class RubyClassRef:
    """A reference to a Class (type ``c``) or Module (type ``m`` / ``M``)."""

    __slots__ = ("name", "kind")

    def __init__(self, name: str, kind: str = "class"):
        self.name = name
        self.kind = kind  # "class" | "module" | "old_module"


class RubyExtended:
    """An object with singleton modules mixed in (type ``e``)."""

    __slots__ = ("modules", "inner")

    def __init__(self, modules: list[str], inner: Any):
        self.modules = modules
        self.inner = inner


class RubyUClass:
    """An instance of a subclass of String/Array/Hash (type ``C``)."""

    __slots__ = ("cls", "inner")

    def __init__(self, cls: str, inner: Any):
        self.cls = cls
        self.inner = inner


class RubyRegexp:
    __slots__ = ("source", "options")

    def __init__(self, source: bytes, options: int):
        self.source = bytes(source)
        self.options = options


class RubyFloat(float):
    """A Ruby ``Float`` that remembers its original textual form.

    Ruby 1.8 wrote floats as a decimal string optionally followed by a NUL and
    extra mantissa bits. Re-emitting the bytes we read keeps round trips exact
    for values nobody edited.
    """

    __slots__ = ("raw",)

    def __new__(cls, value: float, raw: bytes | None = None):
        self = super().__new__(cls, value)
        self.raw = raw
        return self


class RubyHash:
    """A Ruby ``Hash``, stored as ordered pairs so non-hashable keys survive."""

    __slots__ = ("pairs", "default")

    def __init__(self, pairs: Iterable[tuple[Any, Any]] | None = None, default: Any = None):
        self.pairs: list[tuple[Any, Any]] = list(pairs or [])
        self.default = default

    # -- mapping-ish helpers, keyed by value equality -------------------------
    def get(self, key: Any, default: Any = None) -> Any:
        for k, v in self.pairs:
            if k == key:
                return v
        return default

    def set(self, key: Any, value: Any) -> None:
        for i, (k, _) in enumerate(self.pairs):
            if k == key:
                self.pairs[i] = (key, value)
                return
        self.pairs.append((key, value))

    def delete(self, key: Any) -> bool:
        for i, (k, _) in enumerate(self.pairs):
            if k == key:
                del self.pairs[i]
                return True
        return False

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
        return any(k == key for k, _ in self.pairs)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RubyHash):
            return self.pairs == other.pairs and self.default == other.default
        return NotImplemented

    __hash__ = object.__hash__

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "RubyHash(%d pairs)" % len(self.pairs)


class Table:
    """The RGSS ``Table`` class: a 1-, 2- or 3-dimensional grid of 16-bit cells.

    Marshalled through ``_dump`` as ``[dim, xsize, ysize, zsize, size].pack('L5')``
    followed by ``size`` unsigned 16-bit little-endian values. Map tile data is
    the big one: ``xsize * ysize * zsize`` cells addressed ``[x, y, z]``.

    Cells are held as the unsigned values found on disk (0..65535). Some tables
    are semantically signed (animation frame data uses -1 for "no cell"), so
    :meth:`get` takes ``signed=True`` rather than guessing per table.
    """

    __slots__ = ("dim", "xsize", "ysize", "zsize", "data")

    HEADER = struct.Struct("<5I")

    def __init__(self, xsize: int = 0, ysize: int = 1, zsize: int = 1,
                 dim: int | None = None, data: list[int] | None = None):
        self.xsize = xsize
        self.ysize = ysize
        self.zsize = zsize
        self.dim = dim if dim is not None else (3 if zsize > 1 else (2 if ysize > 1 else 1))
        size = xsize * ysize * zsize
        if data is None:
            self.data = [0] * size
        else:
            if len(data) != size:
                raise ValueError(
                    "Table data length %d != %d*%d*%d = %d"
                    % (len(data), xsize, ysize, zsize, size)
                )
            self.data = list(data)

    # -- serialisation --------------------------------------------------------
    @classmethod
    def from_payload(cls, payload: bytes) -> "Table":
        if len(payload) < 20:
            raise ValueError("Table payload too short: %d bytes" % len(payload))
        dim, xsize, ysize, zsize, size = cls.HEADER.unpack_from(payload, 0)
        # A declared size that disagrees with the dimensions means a corrupt
        # file; surface it now rather than at write time.
        if xsize * ysize * zsize != size:
            raise ValueError(
                "Table header inconsistent: %d*%d*%d != declared size %d"
                % (xsize, ysize, zsize, size)
            )
        expected = 20 + size * 2
        if len(payload) < expected:
            raise ValueError(
                "Table payload truncated: need %d bytes, have %d" % (expected, len(payload))
            )
        data = list(struct.unpack_from("<%dH" % size, payload, 20))
        table = cls.__new__(cls)
        table.dim, table.xsize, table.ysize, table.zsize = dim, xsize, ysize, zsize
        table.data = data
        return table

    def to_payload(self) -> bytes:
        size = len(self.data)
        header = self.HEADER.pack(self.dim, self.xsize, self.ysize, self.zsize, size)
        return header + struct.pack("<%dH" % size, *(v & 0xFFFF for v in self.data))

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
        if signed and value > 0x7FFF:
            value -= 0x10000
        return value

    def set(self, x: int, y: int, z: int, value: int) -> None:
        self.data[self._index(x, y, z)] = value & 0xFFFF

    def resize(self, xsize: int, ysize: int, zsize: int) -> "Table":
        """Return a resized copy, preserving cells that still fit."""
        out = Table(xsize, ysize, zsize, dim=self.dim)
        for z in range(min(zsize, self.zsize)):
            for y in range(min(ysize, self.ysize)):
                for x in range(min(xsize, self.xsize)):
                    out.set(x, y, z, self.get(x, y, z))
        return out

    @property
    def size(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Table(%dx%dx%d, dim=%d)" % (self.xsize, self.ysize, self.zsize, self.dim)


class RGSSFloats:
    """Shared behaviour for ``Color`` and ``Tone``: four packed doubles."""

    __slots__ = ("values",)
    CLASS_NAME = ""
    FIELDS: tuple[str, ...] = ()

    def __init__(self, values: Iterable[float]):
        vals = [float(v) for v in values]
        if len(vals) != 4:
            raise ValueError("%s needs 4 values, got %d" % (self.CLASS_NAME, len(vals)))
        self.values = vals

    @classmethod
    def from_payload(cls, payload: bytes):
        if len(payload) != 32:
            raise ValueError(
                "%s payload must be 32 bytes, got %d" % (cls.CLASS_NAME, len(payload))
            )
        return cls(struct.unpack("<4d", payload))

    def to_payload(self) -> bytes:
        return struct.pack("<4d", *self.values)

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.FIELDS, self.values))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, type(self)) and self.values == other.values

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
