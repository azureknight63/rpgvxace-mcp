"""A pure-Python reader and writer for Ruby's Marshal format, version 4.8.

RPG Maker VX stores its whole database as ``Marshal.dump`` output in
``Data/*.rvdata``. Existing MCP servers that reach VX-era projects shell out to
a Ruby interpreter to convert those files; implementing the format directly
removes that dependency, which is the difference between "works" and "needs a
Ruby install" on a typical Windows machine.

Two properties are treated as non-negotiable:

* **Lossless.** Anything not understood (unknown classes, user-defined dumps,
  structs, extended objects) is carried through verbatim, so re-dumping a file
  we merely read produces identical bytes.
* **Ruby 1.8 compatible output.** RGSS2 embeds Ruby 1.8, which fails to load
  the ``I`` (instance-variable) wrapper Ruby >= 1.9 puts around every string.
  Strings are therefore written bare unless the file we read had a wrapper.

Reference: ``marshal.c`` from the Ruby source, whose ``w_long``/``r_long``
integer packing and object/symbol link tables this mirrors.
"""

from __future__ import annotations

from typing import Any

from .rubytypes import (
    Color,
    RubyClassRef,
    RubyExtended,
    RubyFloat,
    RubyHash,
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
)

MAJOR_VERSION = 4
MINOR_VERSION = 8

# Marshal type tags.
T_NIL = 0x30          # 0
T_TRUE = 0x54         # T
T_FALSE = 0x46        # F
T_FIXNUM = 0x69       # i
T_BIGNUM = 0x6C       # l
T_FLOAT = 0x66        # f
T_SYMBOL = 0x3A       # :
T_SYMLINK = 0x3B      # ;
T_LINK = 0x40         # @
T_STRING = 0x22       # "
T_ARRAY = 0x5B        # [
T_HASH = 0x7B         # {
T_HASH_DEF = 0x7D     # }
T_OBJECT = 0x6F       # o
T_USERDEF = 0x75      # u
T_USRMARSHAL = 0x55   # U
T_STRUCT = 0x53       # S
T_CLASS = 0x63        # c
T_MODULE = 0x6D       # m
T_MODULE_OLD = 0x4D   # M
T_EXTENDED = 0x65     # e
T_UCLASS = 0x43       # C
T_REGEXP = 0x2F       # /
T_DATA = 0x64         # d
T_IVAR = 0x49         # I -- wraps a value that carries instance variables

# Ruby writes a Fixnum only when the value fits in 31 signed bits (the tag bit
# costs one); anything wider becomes a Bignum.
FIXNUM_MIN = -(2 ** 30)
FIXNUM_MAX = 2 ** 30 - 1

# ``_dump``-serialised classes we understand well enough to decode.
USERDEF_CLASSES = {"Table": Table, "Color": Color, "Tone": Tone}


class MarshalError(ValueError):
    """Raised when a byte stream is not valid Marshal 4.8 data."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class MarshalReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.symbols: list[RubySymbol] = []
        self.objects: list[Any] = []
        self.warnings: list[str] = []

    # -- primitives -----------------------------------------------------------
    def _byte(self) -> int:
        if self.pos >= len(self.data):
            raise MarshalError("unexpected end of data at byte %d" % self.pos)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def _bytes(self, count: int) -> bytes:
        if count < 0:
            raise MarshalError("negative length %d at byte %d" % (count, self.pos))
        end = self.pos + count
        if end > len(self.data):
            raise MarshalError(
                "wanted %d bytes at %d but only %d remain"
                % (count, self.pos, len(self.data) - self.pos)
            )
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def _long(self) -> int:
        """Ruby's variable-width integer packing (``r_long`` in marshal.c)."""
        c = self._byte()
        if c == 0:
            return 0
        if c > 127:  # negative when read as a signed byte
            c -= 256
        if c > 0:
            if 4 < c < 128:
                return c - 5
            value = 0
            for i in range(c):
                value |= self._byte() << (8 * i)
            return value
        if -129 < c < -4:
            return c + 5
        count = -c
        value = -1
        for i in range(count):
            value &= ~(0xFF << (8 * i))
            value |= self._byte() << (8 * i)
        return value

    def _string_bytes(self) -> bytes:
        return self._bytes(self._long())

    # -- entry tables ---------------------------------------------------------
    def _register(self, obj: Any) -> Any:
        """Claim the next object-link slot.

        Ruby assigns link indices to every non-immediate value in the order they
        appear, floats and bignums included. Diverging here would misresolve
        every ``@`` link later in the file.
        """
        self.objects.append(obj)
        return obj

    def _symbol(self) -> RubySymbol:
        sym = RubySymbol(self._string_bytes().decode("utf-8", "surrogateescape"))
        self.symbols.append(sym)
        return sym

    def _read_symbol_ref(self) -> RubySymbol:
        """Read a value that must be a symbol (class names, ivar names)."""
        tag = self._byte()
        if tag == T_SYMBOL:
            return self._symbol()
        if tag == T_SYMLINK:
            index = self._long()
            try:
                return self.symbols[index]
            except IndexError:
                raise MarshalError("symlink %d out of range at byte %d" % (index, self.pos))
        raise MarshalError(
            "expected a symbol at byte %d but found tag %r" % (self.pos - 1, chr(tag))
        )

    # -- values ---------------------------------------------------------------
    def read(self) -> Any:
        if len(self.data) < 2:
            raise MarshalError("data is too short to be Marshal output")
        major, minor = self.data[0], self.data[1]
        if major != MAJOR_VERSION or minor > MINOR_VERSION:
            raise MarshalError(
                "unsupported Marshal version %d.%d (expected %d.%d)"
                % (major, minor, MAJOR_VERSION, MINOR_VERSION)
            )
        self.pos = 2
        return self._value()

    def _value(self) -> Any:
        tag = self._byte()

        if tag == T_NIL:
            return None
        if tag == T_TRUE:
            return True
        if tag == T_FALSE:
            return False
        if tag == T_FIXNUM:
            return self._long()
        if tag == T_SYMBOL:
            return self._symbol()
        if tag == T_SYMLINK:
            index = self._long()
            try:
                return self.symbols[index]
            except IndexError:
                raise MarshalError("symlink %d out of range" % index)
        if tag == T_LINK:
            index = self._long()
            try:
                return self.objects[index]
            except IndexError:
                raise MarshalError("object link %d out of range" % index)

        if tag == T_IVAR:
            inner = self._value()
            ivars = self._read_ivars()
            self._attach_ivars(inner, ivars)
            return inner

        if tag == T_FLOAT:
            raw = self._string_bytes()
            return self._register(RubyFloat(_parse_float(raw), raw))
        if tag == T_BIGNUM:
            sign = self._byte()
            words = self._long()
            chunk = self._bytes(words * 2)
            value = int.from_bytes(chunk, "little")
            if sign == 0x2D:  # '-'
                value = -value
            return self._register(value)
        if tag == T_STRING:
            return self._register(RubyString(self._string_bytes()))
        if tag == T_ARRAY:
            count = self._long()
            array: list[Any] = []
            self._register(array)
            for _ in range(count):
                array.append(self._value())
            return array
        if tag in (T_HASH, T_HASH_DEF):
            count = self._long()
            hash_obj = RubyHash()
            self._register(hash_obj)
            for _ in range(count):
                key = self._value()
                hash_obj.pairs.append((key, self._value()))
            if tag == T_HASH_DEF:
                hash_obj.default = self._value()
            return hash_obj
        if tag == T_OBJECT:
            cls = str(self._read_symbol_ref())
            obj = RubyObject(cls)
            self._register(obj)
            obj.ivars.update(self._read_ivars())
            return obj
        if tag == T_USERDEF:
            cls = str(self._read_symbol_ref())
            payload = self._string_bytes()
            return self._register(self._decode_userdef(cls, payload))
        if tag == T_USRMARSHAL:
            cls = str(self._read_symbol_ref())
            wrapper = RubyUserMarshal(cls, None)
            self._register(wrapper)
            wrapper.value = self._value()
            return wrapper
        if tag == T_STRUCT:
            cls = str(self._read_symbol_ref())
            struct_obj = RubyStruct(cls)
            self._register(struct_obj)
            for _ in range(self._long()):
                name = str(self._read_symbol_ref())
                struct_obj.members[name] = self._value()
            return struct_obj
        if tag == T_CLASS:
            return self._register(RubyClassRef(self._string_bytes().decode("utf-8"), "class"))
        if tag == T_MODULE:
            return self._register(RubyClassRef(self._string_bytes().decode("utf-8"), "module"))
        if tag == T_MODULE_OLD:
            return self._register(
                RubyClassRef(self._string_bytes().decode("utf-8"), "old_module")
            )
        if tag == T_EXTENDED:
            modules = [str(self._read_symbol_ref())]
            inner = self._value()
            while isinstance(inner, RubyExtended):  # nested e records
                modules.extend(inner.modules)
                inner = inner.inner
            return RubyExtended(modules, inner)
        if tag == T_UCLASS:
            cls = str(self._read_symbol_ref())
            return RubyUClass(cls, self._value())
        if tag == T_REGEXP:
            source = self._string_bytes()
            return self._register(RubyRegexp(source, self._byte()))
        if tag == T_DATA:
            cls = str(self._read_symbol_ref())
            raise MarshalError(
                "class %s uses Marshal's C-level 'data' format, which cannot be "
                "read or rewritten safely" % cls
            )

        raise MarshalError("unknown Marshal tag %r at byte %d" % (chr(tag), self.pos - 1))

    def _read_ivars(self) -> dict[str, Any]:
        ivars: dict[str, Any] = {}
        for _ in range(self._long()):
            name = str(self._read_symbol_ref())
            ivars[name] = self._value()
        return ivars

    def _attach_ivars(self, target: Any, ivars: dict[str, Any]) -> None:
        if isinstance(target, (RubyString, RubyUserDef, RubyObject)):
            target.ivars.update(ivars)
        elif ivars:
            self.warnings.append(
                "dropped instance variables %s from a %s"
                % (sorted(ivars), type(target).__name__)
            )

    def _decode_userdef(self, cls: str, payload: bytes) -> Any:
        decoder = USERDEF_CLASSES.get(cls)
        if decoder is not None:
            try:
                return decoder.from_payload(payload)
            except ValueError as exc:
                # Better to hand back opaque bytes than to refuse the file: the
                # payload still round trips, and callers get a warning.
                self.warnings.append("could not decode %s payload (%s)" % (cls, exc))
        return RubyUserDef(cls, payload)


def _parse_float(raw: bytes) -> float:
    text = raw.split(b"\x00", 1)[0].decode("ascii", "replace").strip()
    if text in ("inf", "Inf"):
        return float("inf")
    if text in ("-inf", "-Inf"):
        return float("-inf")
    if text in ("nan", "NaN"):
        return float("nan")
    try:
        return float(text)
    except ValueError:
        raise MarshalError("unparseable float literal %r" % raw)


def load(data: bytes, with_warnings: bool = False):
    """Decode Marshal 4.8 ``data``.

    With ``with_warnings`` the return value is ``(value, warnings)``.
    """
    reader = MarshalReader(data)
    value = reader.read()
    if reader.pos != len(data):
        reader.warnings.append(
            "%d trailing bytes after the top-level object" % (len(data) - reader.pos)
        )
    return (value, reader.warnings) if with_warnings else value


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class MarshalWriter:
    def __init__(self):
        self.out = bytearray()
        self.symbols: dict[str, int] = {}
        self.objects: dict[int, int] = {}
        # id() is only unique among live objects, so hold references to
        # everything registered to stop the interpreter reusing an address.
        self._alive: list[Any] = []

    # -- primitives -----------------------------------------------------------
    def _byte(self, value: int) -> None:
        self.out.append(value & 0xFF)

    def _long(self, value: int) -> None:
        """Ruby's variable-width integer packing (``w_long`` in marshal.c)."""
        if value == 0:
            self.out.append(0)
            return
        if 0 < value < 123:
            self.out.append(value + 5)
            return
        if -124 < value < 0:
            self.out.append((value - 5) & 0xFF)
            return
        if not (-(2 ** 31) <= value < 2 ** 31):
            raise MarshalError("integer %d does not fit Marshal's long encoding" % value)
        body = bytearray()
        remaining = value
        for length in range(1, 5):
            body.append(remaining & 0xFF)
            remaining >>= 8
            if remaining == 0:
                self.out.append(length)
                break
            if remaining == -1:
                self.out.append((-length) & 0xFF)
                break
        self.out.extend(body)

    def _string_bytes(self, data: bytes) -> None:
        self._long(len(data))
        self.out.extend(data)

    def _symbol(self, name: str) -> None:
        index = self.symbols.get(name)
        if index is not None:
            self._byte(T_SYMLINK)
            self._long(index)
            return
        self.symbols[name] = len(self.symbols)
        self._byte(T_SYMBOL)
        self._string_bytes(name.encode("utf-8", "surrogateescape"))

    def _register(self, obj: Any) -> None:
        self.objects[id(obj)] = len(self.objects)
        self._alive.append(obj)

    def _emit_link(self, obj: Any) -> bool:
        index = self.objects.get(id(obj))
        if index is None:
            return False
        self._byte(T_LINK)
        self._long(index)
        return True

    # -- values ---------------------------------------------------------------
    def write(self, obj: Any) -> bytes:
        self.out.extend(bytes([MAJOR_VERSION, MINOR_VERSION]))
        self._value(obj)
        return bytes(self.out)

    def _value(self, obj: Any) -> None:
        # Immediates first: they never take a link slot.
        if obj is None:
            self._byte(T_NIL)
            return
        if obj is True:
            self._byte(T_TRUE)
            return
        if obj is False:
            self._byte(T_FALSE)
            return
        if isinstance(obj, RubySymbol):  # before str, which it subclasses
            self._symbol(str(obj))
            return
        if isinstance(obj, bool):
            self._byte(T_TRUE if obj else T_FALSE)
            return
        if isinstance(obj, int) and FIXNUM_MIN <= obj <= FIXNUM_MAX:
            self._byte(T_FIXNUM)
            self._long(obj)
            return

        if self._emit_link(obj):
            return

        if isinstance(obj, int):  # Bignum
            self._register(obj)
            self._byte(T_BIGNUM)
            self._byte(0x2D if obj < 0 else 0x2B)
            magnitude = abs(obj)
            length = (magnitude.bit_length() + 15) // 16
            self._long(length)
            self.out.extend(magnitude.to_bytes(length * 2, "little"))
            return
        if isinstance(obj, float):
            self._register(obj)
            self._byte(T_FLOAT)
            raw = getattr(obj, "raw", None)
            if raw is None or _parse_float(raw) != obj:
                raw = _format_float(float(obj))
            self._string_bytes(raw)
            return
        if isinstance(obj, RubyString):
            self._register(obj)
            self._with_ivars(obj.ivars, lambda: (self._byte(T_STRING),
                                                 self._string_bytes(obj.data)))
            return
        if isinstance(obj, (bytes, bytearray)):
            self._register(obj)
            self._byte(T_STRING)
            self._string_bytes(bytes(obj))
            return
        if isinstance(obj, str):
            raise MarshalError(
                "refusing to guess an encoding for the Python string %r; convert it "
                "to RubyString with the project's code page first" % obj[:40]
            )
        if isinstance(obj, list):
            self._register(obj)
            self._byte(T_ARRAY)
            self._long(len(obj))
            for item in obj:
                self._value(item)
            return
        if isinstance(obj, RubyHash):
            self._register(obj)
            self._byte(T_HASH if obj.default is None else T_HASH_DEF)
            self._long(len(obj.pairs))
            for key, value in obj.pairs:
                self._value(key)
                self._value(value)
            if obj.default is not None:
                self._value(obj.default)
            return
        if isinstance(obj, dict):
            self._register(obj)
            self._byte(T_HASH)
            self._long(len(obj))
            for key, value in obj.items():
                self._value(key)
                self._value(value)
            return
        if isinstance(obj, Table):
            self._register(obj)
            self._userdef("Table", obj.to_payload(), {})
            return
        if isinstance(obj, Color):
            self._register(obj)
            self._userdef("Color", obj.to_payload(), {})
            return
        if isinstance(obj, Tone):
            self._register(obj)
            self._userdef("Tone", obj.to_payload(), {})
            return
        if isinstance(obj, RubyUserDef):
            self._register(obj)
            self._userdef(obj.cls, obj.payload, obj.ivars)
            return
        if isinstance(obj, RubyObject):
            self._register(obj)
            self._byte(T_OBJECT)
            self._symbol(obj.cls)
            self._ivars(obj.ivars)
            return
        if isinstance(obj, RubyUserMarshal):
            self._register(obj)
            self._byte(T_USRMARSHAL)
            self._symbol(obj.cls)
            self._value(obj.value)
            return
        if isinstance(obj, RubyStruct):
            self._register(obj)
            self._byte(T_STRUCT)
            self._symbol(obj.cls)
            self._long(len(obj.members))
            for name, value in obj.members.items():
                self._symbol(name)
                self._value(value)
            return
        if isinstance(obj, RubyClassRef):
            self._register(obj)
            self._byte({"class": T_CLASS, "module": T_MODULE}.get(obj.kind, T_MODULE_OLD))
            self._string_bytes(obj.name.encode("utf-8"))
            return
        if isinstance(obj, RubyExtended):
            for module in obj.modules:
                self._byte(T_EXTENDED)
                self._symbol(module)
            self._value(obj.inner)
            return
        if isinstance(obj, RubyUClass):
            self._byte(T_UCLASS)
            self._symbol(obj.cls)
            self._value(obj.inner)
            return
        if isinstance(obj, RubyRegexp):
            self._register(obj)
            self._byte(T_REGEXP)
            self._string_bytes(obj.source)
            self._byte(obj.options)
            return

        raise MarshalError("cannot marshal a %s" % type(obj).__name__)

    def _userdef(self, cls: str, payload: bytes, ivars: dict[str, Any]) -> None:
        self._with_ivars(ivars, lambda: (self._byte(T_USERDEF),
                                         self._symbol(cls),
                                         self._string_bytes(payload)))

    def _with_ivars(self, ivars: dict[str, Any], emit) -> None:
        """Wrap ``emit`` in an ``I`` record when there are ivars to carry.

        Only files that arrived with a wrapper get one back, keeping output
        loadable by the Ruby 1.8 interpreter inside RGSS2.
        """
        if ivars:
            self._byte(T_IVAR)
        emit()
        if ivars:
            self._ivars(ivars)

    def _ivars(self, ivars: dict[str, Any]) -> None:
        self._long(len(ivars))
        for name, value in ivars.items():
            self._symbol(name)
            self._value(value)


def _format_float(value: float) -> bytes:
    if value != value:
        return b"nan"
    if value == float("inf"):
        return b"inf"
    if value == float("-inf"):
        return b"-inf"
    if value == 0.0:
        return b"-0" if str(value)[0] == "-" else b"0"
    return repr(value).encode("ascii")


def dump(obj: Any) -> bytes:
    """Encode ``obj`` as Marshal 4.8 bytes loadable by Ruby 1.8 (RGSS2)."""
    return MarshalWriter().write(obj)
