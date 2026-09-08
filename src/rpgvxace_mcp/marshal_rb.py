"""A pure-Python reader and writer for Ruby's Marshal format, version 4.8.

RPG Maker stores a whole project as ``Marshal.dump`` output: ``Data/*.rvdata2``
for VX Ace, ``Data/*.rvdata`` for VX. Existing MCP servers that reach these
engines shell out to a Ruby interpreter to convert those files; implementing the
format directly removes that dependency, which is the difference between
"works" and "needs a Ruby install" on a typical Windows machine.

Two properties are treated as non-negotiable:

* **Lossless.** Anything not understood (unknown classes, user-defined dumps,
  structs, extended objects, C-level data) is carried through verbatim, so
  re-dumping a file we merely read produces identical bytes. Instance-variable
  records are preserved even on values that cannot hold them, via
  :class:`~rpgvxace_mcp.rubytypes.RubyIvars`.
* **Faithful string form.** An ``I`` (instance-variable) record around a string
  is what Ruby >= 1.9 uses to record its encoding. RGSS3 (VX Ace) writes one on
  every string; RGSS2 (VX) runs Ruby 1.8 and writes strings bare, and would fail
  to load an ``I`` record. This codec therefore never invents or removes one: a
  string is written back exactly as wrapped (or unwrapped) as it arrived, and
  the layer above chooses the right form for newly created strings.

Reference: ``marshal.c`` from the Ruby source, whose ``w_long``/``r_long``
integer packing and object/symbol link tables this mirrors.
"""

from __future__ import annotations

import math
import struct
from typing import Any, Callable

from .rubytypes import (
    Color,
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
)

MAJOR_VERSION = 4
MINOR_VERSION = 8

# Marshal type tags. The hex value is the ASCII byte Ruby writes; the comment
# gives that character and what the record holds.
T_NIL = 0x30          # 0  nil
T_TRUE = 0x54         # T  true
T_FALSE = 0x46        # F  false
T_FIXNUM = 0x69       # i  small integer, packed
T_BIGNUM = 0x6C       # l  arbitrary-precision integer
T_FLOAT = 0x66        # f  float, as a decimal string
T_SYMBOL = 0x3A       # :  symbol definition
T_SYMLINK = 0x3B      # ;  back-reference into the symbol table
T_LINK = 0x40         # @  back-reference into the object table
T_STRING = 0x22       # "  byte string
T_ARRAY = 0x5B        # [  array
T_HASH = 0x7B         # {  hash
T_HASH_DEF = 0x7D     # }  hash carrying a default value
T_OBJECT = 0x6F       # o  plain object: class name plus instance variables
T_USERDEF = 0x75      # u  object serialised by its own _dump
T_USRMARSHAL = 0x55   # U  object serialised by its own marshal_dump
T_STRUCT = 0x53       # S  Struct
T_CLASS = 0x63        # c  reference to a Class
T_MODULE = 0x6D       # m  reference to a Module
T_MODULE_OLD = 0x4D   # M  reference to a Module, pre-1.8 form
T_EXTENDED = 0x65     # e  value with singleton modules mixed in
T_UCLASS = 0x43       # C  instance of a String/Array/Hash subclass
T_REGEXP = 0x2F       # /  Regexp
T_DATA = 0x64         # d  object serialised by a C extension's _dump_data
T_IVAR = 0x49         # I  wraps a value that carries instance variables

# Which tag each kind of RubyClassRef is written as, and read back from.
CLASS_REF_TAGS: dict[str, int] = {
    "class": T_CLASS, "module": T_MODULE, "old_module": T_MODULE_OLD,
}
CLASS_REF_KINDS = {tag: kind for kind, tag in CLASS_REF_TAGS.items()}

# The sign byte of a Bignum record.
BIGNUM_POSITIVE = 0x2B  # +
BIGNUM_NEGATIVE = 0x2D  # -

# Ruby's packed-integer encoding (``w_long``/``r_long``). A single header byte
# either *is* the value, biased by LONG_BIAS to leave room for the length
# markers, or gives the number of little-endian magnitude bytes that follow:
#
#   header 0             -> the value 0
#   header 6..127        -> the value header - 5, i.e. 1..122
#   header 128..250      -> the value header - 256 + 5, i.e. -123..-1
#   header 1..4          -> that many bytes follow, positive
#   header 252..255      -> (256 - header) bytes follow, negative
#
# so 123 and -124 are the first values that need a length-prefixed body.
LONG_BIAS = 5
LONG_INLINE_MAX = 122          # largest value the header byte can carry alone
LONG_INLINE_MIN = -123         # most negative such value
LONG_MAX_BODY_BYTES = 4        # Ruby never writes more than four
LONG_LIMIT = 2 ** 31           # ... so this is the encodable range

# Ruby writes a Fixnum only when the value fits in 31 signed bits (the tag bit
# costs one); anything wider becomes a Bignum.
FIXNUM_MIN = -(2 ** 30)
FIXNUM_MAX = 2 ** 30 - 1

# Real project data nests around fifteen deep. A cap well above that turns a
# hostile or corrupt file into a clean error instead of a RecursionError raised
# from an arbitrary point in the parse.
MAX_DEPTH = 192

# ``_dump``-serialised classes we understand well enough to decode. The writer
# reads this same mapping, so a class added here cannot decode on read and then
# fail on dump.
USERDEF_CLASSES: dict[str, Any] = {
    cls.CLASS_NAME: cls for cls in (Table, Color, Tone)
}
USERDEF_TYPES = tuple(USERDEF_CLASSES.values())


class MarshalError(ValueError):
    """Raised when a byte stream is not valid Marshal 4.8 data."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


class MarshalReader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.end = len(data)
        self.pos = 0
        self.minor_version = MINOR_VERSION
        self.symbols: list[RubySymbol] = []
        self.objects: list[Any] = []
        self.warnings: list[str] = []

    # -- primitives -----------------------------------------------------------
    def _byte(self) -> int:
        if self.pos >= self.end:
            raise MarshalError("unexpected end of data at byte %d" % self.pos)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def _bytes(self, count: int) -> bytes:
        if count < 0:
            raise MarshalError("negative length %d at byte %d" % (count, self.pos))
        end = self.pos + count
        if end > self.end:
            raise MarshalError(
                "wanted %d bytes at %d but only %d remain"
                % (count, self.pos, self.end - self.pos)
            )
        chunk = self.data[self.pos:end]
        self.pos = end
        return chunk

    def _long(self) -> int:
        """Decode Ruby's packed integer (``r_long``). See LONG_BIAS above."""
        c = self._byte()
        if c == 0:
            return 0
        if c > 127:  # the header byte is signed
            c -= 256
        if c > 0:
            if c > LONG_MAX_BODY_BYTES:
                return c - LONG_BIAS
            value = 0
            for i in range(c):
                value |= self._byte() << (8 * i)
            return value
        if c < -LONG_MAX_BODY_BYTES:
            return c + LONG_BIAS
        # Negative multi-byte: Ruby stops emitting bytes once the remaining
        # high bits are all ones, so start from -1 and overwrite the bytes that
        # were written. That sign-extends the ones Ruby left out.
        count = -c
        value = -1
        for i in range(count):
            value &= ~(0xFF << (8 * i))
            value |= self._byte() << (8 * i)
        return value

    def _count(self, what: str) -> int:
        """A length or element count, which must not be negative.

        Without this, a corrupt negative count makes ``range()`` empty and the
        container decodes as silently empty rather than being rejected.
        """
        value = self._long()
        if value < 0:
            raise MarshalError(
                "negative %s count %d at byte %d" % (what, value, self.pos)
            )
        return value

    def _string_bytes(self) -> bytes:
        return self._bytes(self._count("string length"))

    def _text(self) -> str:
        """A name stored as bytes (a class or module path).

        Decoded with ``surrogateescape`` so a non-UTF-8 name survives the round
        trip instead of raising out of the parse.
        """
        return self._string_bytes().decode("utf-8", "surrogateescape")

    # -- entry tables ---------------------------------------------------------
    def _register(self, obj: Any) -> Any:
        """Claim the next object-link slot.

        Ruby assigns link indices to every non-immediate value in the order they
        appear, floats and bignums included. Diverging here would misresolve
        every ``@`` link later in the file.
        """
        self.objects.append(obj)
        return obj

    def _entry(self, table: list[Any], index: int, kind: str) -> Any:
        """Resolve a back-reference, refusing an index outside the table.

        The lower bound matters as much as the upper one: Python would happily
        treat a negative index as counting from the end and hand back a
        plausible but wrong object, which we would then write back over the
        user's file.
        """
        if not 0 <= index < len(table):
            raise MarshalError(
                "%s %d out of range (%d known) at byte %d"
                % (kind, index, len(table), self.pos)
            )
        return table[index]

    def _read_symbol_ref(self, depth: int = 0) -> RubySymbol:
        """Read a value that must be a symbol (class names, ivar names).

        Depth-checked like `_read_value`, and for the same reason: the T_IVAR
        branch below recurses, so a name position filled with encoding records
        -- 1.2KB of them is enough -- raised RecursionError. That is a
        RuntimeError, so it escapes every `except MarshalError` on the way out
        and `Project.__init__` reported a traceback rather than a bad file.
        """
        if depth > MAX_DEPTH:
            raise MarshalError(
                "a symbol is nested more than %d records deep at byte %d; "
                "this file is not one the editor wrote"
                % (MAX_DEPTH, self.pos))
        tag = self._byte()
        if tag == T_SYMBOL:
            return self._read_symbol_def(depth)
        if tag == T_SYMLINK:
            return self._entry(self.symbols, self._long(), "symbol link")
        if tag == T_IVAR:
            # Ruby >= 1.9 wraps a symbol whose name is not ASCII in an encoding
            # record. Loading it beats refusing the file, but a symbol in a name
            # position (a class name, an ivar name) becomes a plain string key
            # here, so the record cannot be written back -- say so rather than
            # let the file change shape in silence. No RGSS class or instance
            # variable is named in anything but ASCII, so this stays theoretical.
            symbol = self._read_symbol_ref(depth + 1)
            ivars = self._read_ivars(depth + 1)
            symbol.ivars.update(ivars)
            if ivars:
                self.warnings.append(
                    "symbol :%s carries an encoding record in a name position; "
                    "that record cannot be reproduced when writing this file "
                    "back" % symbol
                )
            return symbol
        raise MarshalError(
            "expected a symbol at byte %d but found tag %r" % (self.pos - 1, chr(tag))
        )

    # -- values ---------------------------------------------------------------
    def read(self) -> Any:
        if self.end < 2:
            raise MarshalError("data is too short to be Marshal output")
        major, minor = self.data[0], self.data[1]
        if major != MAJOR_VERSION or minor > MINOR_VERSION:
            raise MarshalError(
                "unsupported Marshal version %d.%d (expected major %d, minor %d "
                "or lower)" % (major, minor, MAJOR_VERSION, MINOR_VERSION)
            )
        self.minor_version = minor
        self.pos = 2
        return self._read_value()

    def _read_value(self, depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            raise MarshalError(
                "value nested more than %d deep at byte %d; refusing to recurse "
                "further" % (MAX_DEPTH, self.pos)
            )
        tag = self._byte()
        handler = _READ_HANDLERS.get(tag)
        if handler is None:
            raise MarshalError(
                "unknown Marshal tag %r at byte %d" % (chr(tag), self.pos - 1)
            )
        return handler(self, depth)

    # -- one method per tag ---------------------------------------------------
    def _read_nil(self, depth: int) -> None:
        return None

    def _read_true(self, depth: int) -> bool:
        return True

    def _read_false(self, depth: int) -> bool:
        return False

    def _read_fixnum(self, depth: int) -> int:
        value = self._long()
        if not FIXNUM_MIN <= value <= FIXNUM_MAX:
            # Ruby only writes a Fixnum inside that range, so this came from
            # somewhere else and we would re-dump it as a Bignum. Say so rather
            # than quietly changing the file's shape.
            self.warnings.append(
                "Fixnum %d at byte %d is outside the range Ruby writes as a "
                "Fixnum; it will be written back as a Bignum" % (value, self.pos)
            )
        return value

    def _read_symbol_def(self, depth: int) -> RubySymbol:
        symbol = RubySymbol(self._text())
        self.symbols.append(symbol)
        return symbol

    def _read_symlink(self, depth: int) -> RubySymbol:
        return self._entry(self.symbols, self._long(), "symbol link")

    def _read_link(self, depth: int) -> Any:
        return self._entry(self.objects, self._long(), "object link")

    def _read_ivar(self, depth: int) -> Any:
        inner = self._read_value(depth + 1)
        ivars = self._read_ivars(depth + 1)
        if not ivars:
            # An `I` record carrying no instance variables. Ruby never writes
            # one, so this is a crafted or damaged file; dropping it is the
            # right read, but it means the bytes will not come back identical,
            # and every other unreproducible construct here says so.
            self.warnings.append(
                "an instance-variable record with no variables was dropped at "
                "byte %d; this file will not write back byte-identically"
                % self.pos)
            return inner
        if isinstance(inner, (RubyString, RubyUserDef, RubyObject, RubySymbol)):
            inner.ivars.update(ivars)
            return inner
        # An Array, Hash, Float or decoded Table has nowhere to keep them, so
        # wrap rather than drop: the writer re-emits the record around the
        # value, and the round trip stays byte-exact.
        return RubyIvars(inner, ivars)

    def _read_float(self, depth: int) -> RubyFloat:
        raw = self._string_bytes()
        return self._register(RubyFloat(_parse_float(raw), raw))

    def _read_bignum(self, depth: int) -> int:
        sign = self._byte()
        if sign not in (BIGNUM_POSITIVE, BIGNUM_NEGATIVE):
            raise MarshalError(
                "bignum sign byte %#04x at byte %d is neither '+' nor '-'"
                % (sign, self.pos - 1)
            )
        words = self._count("bignum word")
        value = int.from_bytes(self._bytes(words * 2), "little")
        return self._register(-value if sign == BIGNUM_NEGATIVE else value)

    def _read_string(self, depth: int) -> RubyString:
        return self._register(RubyString(self._string_bytes()))

    def _read_array(self, depth: int) -> list[Any]:
        count = self._count("array element")
        array: list[Any] = []
        self._register(array)
        for _ in range(count):
            array.append(self._read_value(depth + 1))
        return array

    def _read_hash(self, depth: int) -> RubyHash:
        return self._read_hash_pairs(depth, with_default=False)

    def _read_hash_def(self, depth: int) -> RubyHash:
        return self._read_hash_pairs(depth, with_default=True)

    def _read_hash_pairs(self, depth: int, with_default: bool) -> RubyHash:
        count = self._count("hash pair")
        hash_obj = RubyHash()
        self._register(hash_obj)
        for _ in range(count):
            key = self._read_value(depth + 1)
            hash_obj.pairs.append((key, self._read_value(depth + 1)))
        if with_default:
            hash_obj.default = self._read_value(depth + 1)
        return hash_obj

    def _read_object(self, depth: int) -> RubyObject:
        obj = RubyObject(str(self._read_symbol_ref(depth)))
        self._register(obj)
        obj.ivars.update(self._read_ivars(depth + 1))
        return obj

    def _read_userdef(self, depth: int) -> Any:
        cls = str(self._read_symbol_ref(depth))
        payload = self._string_bytes()
        return self._register(self._decode_userdef(cls, payload))

    def _read_usrmarshal(self, depth: int) -> RubyUserMarshal:
        wrapper = RubyUserMarshal(str(self._read_symbol_ref(depth)), None)
        self._register(wrapper)
        wrapper.value = self._read_value(depth + 1)
        return wrapper

    def _read_struct(self, depth: int) -> RubyStruct:
        struct_obj = RubyStruct(str(self._read_symbol_ref(depth)))
        self._register(struct_obj)
        for _ in range(self._count("struct member")):
            name = str(self._read_symbol_ref(depth + 1))
            struct_obj.members[name] = self._read_value(depth + 1)
        return struct_obj

    def _read_class_ref(self, depth: int, kind: str) -> RubyClassRef:
        return self._register(RubyClassRef(self._text(), kind))

    def _read_class(self, depth: int) -> RubyClassRef:
        return self._read_class_ref(depth, "class")

    def _read_module(self, depth: int) -> RubyClassRef:
        return self._read_class_ref(depth, "module")

    def _read_old_module(self, depth: int) -> RubyClassRef:
        return self._read_class_ref(depth, "old_module")

    def _read_extended(self, depth: int) -> RubyExtended:
        modules = [str(self._read_symbol_ref(depth))]
        inner = self._read_value(depth + 1)
        while isinstance(inner, RubyExtended):  # nested e records
            modules.extend(inner.modules)
            inner = inner.inner
        return RubyExtended(modules, inner)

    def _read_uclass(self, depth: int) -> RubyUClass:
        cls = str(self._read_symbol_ref(depth))
        return RubyUClass(cls, self._read_value(depth + 1))

    def _read_regexp(self, depth: int) -> RubyRegexp:
        source = self._string_bytes()
        return self._register(RubyRegexp(source, self._byte()))

    def _read_data(self, depth: int) -> RubyData:
        # A C extension's own dump. The payload is one nested value, so it can
        # be carried through verbatim even though nothing here interprets it.
        wrapper = RubyData(str(self._read_symbol_ref(depth)), None)
        self._register(wrapper)
        wrapper.value = self._read_value(depth + 1)
        return wrapper

    # -- shared helpers -------------------------------------------------------
    def _read_ivars(self, depth: int = 0) -> dict[str, Any]:
        ivars: dict[str, Any] = {}
        for _ in range(self._count("instance variable")):
            name = str(self._read_symbol_ref(depth))
            ivars[name] = self._read_value(depth + 1)
        return ivars

    def _decode_userdef(self, cls: str, payload: bytes) -> Any:
        decoder = USERDEF_CLASSES.get(cls)
        if decoder is not None:
            try:
                return decoder.from_payload(payload)
            except (ValueError, struct.error) as exc:
                # Better to hand back opaque bytes than to refuse the file: the
                # payload still round trips, and callers get a warning.
                self.warnings.append("could not decode %s payload (%s)" % (cls, exc))
        return RubyUserDef(cls, payload)


_READ_HANDLERS: dict[int, Callable[[MarshalReader, int], Any]] = {
    T_NIL: MarshalReader._read_nil,
    T_TRUE: MarshalReader._read_true,
    T_FALSE: MarshalReader._read_false,
    T_FIXNUM: MarshalReader._read_fixnum,
    T_SYMBOL: MarshalReader._read_symbol_def,
    T_SYMLINK: MarshalReader._read_symlink,
    T_LINK: MarshalReader._read_link,
    T_IVAR: MarshalReader._read_ivar,
    T_FLOAT: MarshalReader._read_float,
    T_BIGNUM: MarshalReader._read_bignum,
    T_STRING: MarshalReader._read_string,
    T_ARRAY: MarshalReader._read_array,
    T_HASH: MarshalReader._read_hash,
    T_HASH_DEF: MarshalReader._read_hash_def,
    T_OBJECT: MarshalReader._read_object,
    T_USERDEF: MarshalReader._read_userdef,
    T_USRMARSHAL: MarshalReader._read_usrmarshal,
    T_STRUCT: MarshalReader._read_struct,
    T_CLASS: MarshalReader._read_class,
    T_MODULE: MarshalReader._read_module,
    T_MODULE_OLD: MarshalReader._read_old_module,
    T_EXTENDED: MarshalReader._read_extended,
    T_UCLASS: MarshalReader._read_uclass,
    T_REGEXP: MarshalReader._read_regexp,
    T_DATA: MarshalReader._read_data,
}


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
        raise MarshalError("unparseable float literal %r" % raw[:32]) from None


def load(data: bytes) -> Any:
    """Decode Marshal 4.8 ``data``, discarding any warnings."""
    return load_with_warnings(data)[0]


def load_with_warnings(data: bytes) -> tuple[Any, list[str]]:
    """Decode Marshal 4.8 ``data``, returning ``(value, warnings)``.

    A warning means the file held something this codec could read but cannot
    reproduce exactly, so a caller about to write the value back has a reason
    to stop and look.
    """
    reader = MarshalReader(data)
    value = reader.read()
    if reader.pos != len(data):
        reader.warnings.append(
            "%d trailing bytes after the top-level object" % (len(data) - reader.pos)
        )
    return value, reader.warnings


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

# The two wrapper records that do not claim a link slot of their own: the value
# they wrap takes it, exactly as in Ruby. Adding _register for these would shift
# every later link index and misresolve the file.
NON_LINKED_TYPES = (RubyExtended, RubyUClass, RubyIvars)


class MarshalWriter:
    def __init__(self, minor: int = MINOR_VERSION) -> None:
        self.out = bytearray()
        self.minor = minor
        self.symbols: dict[str, int] = {}
        self.objects: dict[int, int] = {}
        # id() is only unique among live objects, so hold references to
        # everything registered to stop the interpreter reusing an address.
        self._alive: list[Any] = []

    # -- primitives -----------------------------------------------------------
    def _byte(self, value: int) -> None:
        self.out.append(value & 0xFF)

    def _long(self, value: int) -> None:
        """Encode Ruby's packed integer (``w_long``). See LONG_BIAS above."""
        if value == 0:
            self.out.append(0)
            return
        if 0 < value <= LONG_INLINE_MAX:
            self.out.append(value + LONG_BIAS)
            return
        if LONG_INLINE_MIN <= value < 0:
            self.out.append((value - LONG_BIAS) & 0xFF)
            return
        if not -LONG_LIMIT <= value < LONG_LIMIT:
            raise MarshalError("integer %d does not fit Marshal's long encoding" % value)
        body = bytearray()
        remaining = value
        for length in range(1, LONG_MAX_BODY_BYTES + 1):
            body.append(remaining & 0xFF)
            remaining >>= 8
            if remaining == 0:
                self.out.append(length)
                break
            if remaining == -1:
                self.out.append((-length) & 0xFF)
                break
        else:  # pragma: no cover - unreachable while LONG_LIMIT holds
            # Without this the body would be written with no length prefix,
            # silently corrupting the stream.
            raise MarshalError(
                "integer %d did not terminate within %d bytes"
                % (value, LONG_MAX_BODY_BYTES)
            )
        self.out.extend(body)

    def _string_bytes(self, data: bytes) -> None:
        self._long(len(data))
        self.out.extend(data)

    def _symbol(self, name: str, ivars: dict[str, Any] | None = None) -> None:
        index = self.symbols.get(name)
        if index is not None:
            self._byte(T_SYMLINK)
            self._long(index)
            return
        self.symbols[name] = len(self.symbols)
        if ivars:
            self._byte(T_IVAR)
        self._byte(T_SYMBOL)
        self._string_bytes(name.encode("utf-8", "surrogateescape"))
        if ivars:
            self._ivars(ivars)

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
        self.out.extend(bytes([MAJOR_VERSION, self.minor]))
        self._write_value(obj)
        return bytes(self.out)

    def _write_value(self, obj: Any) -> None:
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
            self._symbol(str(obj), obj.ivars)
            return
        if isinstance(obj, int) and not isinstance(obj, bool) \
                and FIXNUM_MIN <= obj <= FIXNUM_MAX:
            self._byte(T_FIXNUM)
            self._long(obj)
            return

        if self._emit_link(obj):
            return
        if not isinstance(obj, NON_LINKED_TYPES) and not _is_interned(obj):
            # Registered before the contents are written, so a value that
            # contains itself links back rather than recursing forever.
            self._register(obj)

        # Ordered by how often each type appears in real project data, then by
        # subclass relationships: RubyFloat is a float, RubySymbol is a str, so
        # the order of these checks is load-bearing and a type() lookup table
        # would not preserve it.
        if isinstance(obj, RubyString):
            self._write_string(obj)
        elif isinstance(obj, list):
            self._write_array(obj)
        elif isinstance(obj, RubyHash):
            self._write_hash(obj)
        elif isinstance(obj, RubyObject):
            self._write_object(obj)
        elif isinstance(obj, USERDEF_TYPES):
            self._userdef(type(obj).CLASS_NAME, obj.to_payload(), {})
        elif isinstance(obj, RubyUserDef):
            self._userdef(obj.cls, obj.payload, obj.ivars)
        elif isinstance(obj, int):
            self._write_bignum(obj)
        elif isinstance(obj, float):
            self._write_float(obj)
        elif isinstance(obj, (bytes, bytearray)):
            self._write_raw_bytes(obj)
        elif isinstance(obj, str):
            raise MarshalError(
                "refusing to guess an encoding for the Python string %r; convert it "
                "to RubyString with the project's code page first" % obj[:40]
            )
        elif isinstance(obj, dict):
            self._write_dict(obj)
        elif isinstance(obj, RubyIvars):
            self._write_ivar_wrapper(obj)
        elif isinstance(obj, RubyUserMarshal):
            self._write_usrmarshal(obj)
        elif isinstance(obj, RubyData):
            self._write_data(obj)
        elif isinstance(obj, RubyStruct):
            self._write_struct(obj)
        elif isinstance(obj, RubyClassRef):
            self._write_class_ref(obj)
        elif isinstance(obj, RubyExtended):
            self._write_extended(obj)
        elif isinstance(obj, RubyUClass):
            self._write_uclass(obj)
        elif isinstance(obj, RubyRegexp):
            self._write_regexp(obj)
        else:
            raise MarshalError("cannot marshal a %s" % type(obj).__name__)

    # -- one method per type --------------------------------------------------
    def _write_string(self, obj: RubyString) -> None:
        if obj.ivars:
            self._byte(T_IVAR)
        self._byte(T_STRING)
        self._string_bytes(obj.data)
        if obj.ivars:
            self._ivars(obj.ivars)

    def _write_raw_bytes(self, obj: bytes | bytearray) -> None:
        self._byte(T_STRING)
        self._string_bytes(bytes(obj))

    def _write_array(self, obj: list[Any]) -> None:
        self._byte(T_ARRAY)
        self._long(len(obj))
        for item in obj:
            self._write_value(item)

    def _write_hash(self, obj: RubyHash) -> None:
        self._byte(T_HASH if obj.default is None else T_HASH_DEF)
        self._long(len(obj.pairs))
        for key, value in obj.pairs:
            self._write_value(key)
            self._write_value(value)
        if obj.default is not None:
            self._write_value(obj.default)

    def _write_dict(self, obj: dict[Any, Any]) -> None:
        self._byte(T_HASH)
        self._long(len(obj))
        for key, value in obj.items():
            self._write_value(key)
            self._write_value(value)

    def _write_object(self, obj: RubyObject) -> None:
        self._byte(T_OBJECT)
        self._symbol(obj.cls)
        self._ivars(obj.ivars)

    def _write_bignum(self, obj: int) -> None:
        self._byte(T_BIGNUM)
        self._byte(BIGNUM_NEGATIVE if obj < 0 else BIGNUM_POSITIVE)
        magnitude = abs(obj)
        length = (magnitude.bit_length() + 15) // 16
        self._long(length)
        self.out.extend(magnitude.to_bytes(length * 2, "little"))

    def _write_float(self, obj: float) -> None:
        self._byte(T_FLOAT)
        raw = obj.raw if isinstance(obj, RubyFloat) else None
        # The stored bytes are reused only while they still describe this value,
        # so an edited float is reformatted rather than written back stale.
        if raw is None or _parse_float(raw) != obj:
            raw = _format_float(float(obj))
        self._string_bytes(raw)

    def _write_ivar_wrapper(self, obj: RubyIvars) -> None:
        self._byte(T_IVAR)
        self._write_value(obj.inner)
        self._ivars(obj.ivars)

    def _write_usrmarshal(self, obj: RubyUserMarshal) -> None:
        self._byte(T_USRMARSHAL)
        self._symbol(obj.cls)
        self._write_value(obj.value)

    def _write_data(self, obj: RubyData) -> None:
        self._byte(T_DATA)
        self._symbol(obj.cls)
        self._write_value(obj.value)

    def _write_struct(self, obj: RubyStruct) -> None:
        self._byte(T_STRUCT)
        self._symbol(obj.cls)
        self._long(len(obj.members))
        for name, value in obj.members.items():
            self._symbol(name)
            self._write_value(value)

    def _write_class_ref(self, obj: RubyClassRef) -> None:
        tag = CLASS_REF_TAGS.get(obj.kind)
        if tag is None:
            raise MarshalError(
                "unknown class reference kind %r; expected one of %s"
                % (obj.kind, ", ".join(sorted(CLASS_REF_TAGS)))
            )
        self._byte(tag)
        self._string_bytes(obj.name.encode("utf-8", "surrogateescape"))

    def _write_extended(self, obj: RubyExtended) -> None:
        for module in obj.modules:
            self._byte(T_EXTENDED)
            self._symbol(module)
        self._write_value(obj.inner)

    def _write_uclass(self, obj: RubyUClass) -> None:
        self._byte(T_UCLASS)
        self._symbol(obj.cls)
        self._write_value(obj.inner)

    def _write_regexp(self, obj: RubyRegexp) -> None:
        self._byte(T_REGEXP)
        self._string_bytes(obj.source)
        self._byte(obj.options)

    # -- shared helpers -------------------------------------------------------
    def _userdef(self, cls: str, payload: bytes, ivars: dict[str, Any]) -> None:
        if ivars:
            self._byte(T_IVAR)
        self._byte(T_USERDEF)
        self._symbol(cls)
        self._string_bytes(payload)
        if ivars:
            self._ivars(ivars)

    def _ivars(self, ivars: dict[str, Any]) -> None:
        self._long(len(ivars))
        for name, value in ivars.items():
            self._symbol(name)
            self._write_value(value)


def _is_interned(obj: Any) -> bool:
    """Whether CPython may hand out one object for two equal literals.

    ``b""`` and every single-byte ``bytes`` are singletons, so registering them
    in the id()-keyed link table would emit a link for what the caller meant as
    two separate strings -- and Ruby would load them as one shared, mutable
    String. Writing the bytes twice is the harmless failure.
    """
    return isinstance(obj, bytes) and len(obj) <= 1


def _format_float(value: float) -> bytes:
    """Format a float the way Ruby's ``w_float`` does.

    Ruby writes the shortest representation that round trips, with no trailing
    ``.0`` on an integral value.
    """
    if math.isnan(value):
        return b"nan"
    if math.isinf(value):
        return b"inf" if value > 0 else b"-inf"
    if value == 0.0:
        return b"-0" if math.copysign(1.0, value) < 0 else b"0"
    text = repr(value)
    if text.endswith(".0"):
        return text[:-2].encode("ascii")
    if "e" in text and "." not in text:
        # Ruby writes 1.0e+20 where Python's repr gives 1e+20.
        mantissa, exponent = text.split("e", 1)
        return ("%s.0e%s" % (mantissa, exponent)).encode("ascii")
    return text.encode("ascii")


def dump(obj: Any, minor: int = MINOR_VERSION) -> bytes:
    """Encode ``obj`` as Marshal bytes the engine's Ruby can load.

    ``minor`` is the format's minor version, which every real RPG Maker file
    sets to 8; pass the value :class:`MarshalReader` reported to reproduce a
    file that used an older one.
    """
    return MarshalWriter(minor).write(obj)
