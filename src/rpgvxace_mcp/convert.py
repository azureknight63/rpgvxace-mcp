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
)

# Cells are omitted from generic views above this size; map data is far bigger
# than anything worth pasting into a tool result.
TABLE_INLINE_LIMIT = 256


class ConversionError(ValueError):
    pass


# Ivars Ruby uses to record a string's own encoding. They are bookkeeping, not
# content, so they do not belong in a tool result.
ENCODING_IVARS = ("E", "encoding")


class TextCodec:
    """The code page a project's strings are stored in, and how it wraps them.

    ``string_ivars`` is the ivar record this engine's Ruby puts on every string:
    ``{"E": True}`` for RGSS3, which runs Ruby 1.9 and tags strings as UTF-8,
    and nothing at all for RGSS2, whose Ruby 1.8 strings are bare bytes.
    """

    def __init__(self, name: str = "utf-8", string_ivars: dict[str, Any] | None = None):
        self.name = name
        self.string_ivars = dict(string_ivars or {})

    def decode(self, data: bytes) -> str:
        return data.decode(self.name, "surrogateescape")

    def make_string(self, text: str) -> RubyString:
        """A RubyString in this project's style, ready to write back."""
        return RubyString(self.encode(text), dict(self.string_ivars) or None)

    def is_default_style(self, value: RubyString) -> bool:
        return value.ivars == self.string_ivars

    def encode(self, text: str) -> bytes:
        try:
            return text.encode(self.name, "surrogateescape")
        except UnicodeEncodeError as exc:
            bad = text[exc.start:exc.end]
            raise ConversionError(
                "%r cannot be written in this project's %s encoding. Either use "
                "characters that fit that code page, or restart the server with "
                "--encoding set to a codec that covers them." % (bad, self.name)
            ) from exc

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
    non_ascii = [s for s in samples if any(b > 0x7F for b in s)]
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


def to_json(value: Any, codec: TextCodec, tables: str = "summary") -> Any:
    """Convert a loaded Ruby value into JSON-safe data.

    ``tables`` selects how :class:`Table` cells are rendered: ``"summary"``
    reports the shape only, ``"full"`` includes every cell.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, RubyFloat):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, RubySymbol):  # before RubyString/str checks
        return {"__symbol__": str(value)}
    if isinstance(value, RubyString):
        return _string_to_json(value, codec)
    if isinstance(value, (bytes, bytearray)):
        return _string_to_json(RubyString(bytes(value)), codec)
    if isinstance(value, list):
        return [to_json(v, codec, tables) for v in value]
    if isinstance(value, RubyHash):
        out: dict[str, Any] = {
            "__hash__": [[to_json(k, codec, tables), to_json(v, codec, tables)]
                         for k, v in value.pairs]
        }
        if value.default is not None:
            out["__default__"] = to_json(value.default, codec, tables)
        return out
    if isinstance(value, Table):
        return table_to_json(value, tables)
    if isinstance(value, Color):
        return dict({"__class__": "Color"}, **value.as_dict())
    if isinstance(value, RubyObject):
        result = {"__class__": value.cls}
        for name, item in value.ivars.items():
            result[name.lstrip("@")] = to_json(item, codec, tables)
        return result
    if isinstance(value, RubyUserDef):
        return {
            "__class__": value.cls,
            "__payload_base64__": base64.b64encode(value.payload).decode("ascii"),
            "__note__": "opaque _dump payload, preserved verbatim",
        }
    if isinstance(value, RubyUserMarshal):
        return {"__class__": value.cls,
                "__marshal_value__": to_json(value.value, codec, tables)}
    if isinstance(value, RubyStruct):
        return {"__struct__": value.cls,
                "members": {k: to_json(v, codec, tables) for k, v in value.members.items()}}
    if isinstance(value, RubyClassRef):
        return {"__class_ref__": value.name, "kind": value.kind}
    if isinstance(value, RubyRegexp):
        return {"__regexp__": codec.decode(value.source), "options": value.options}
    if isinstance(value, RubyUClass):
        return {"__uclass__": value.cls, "inner": to_json(value.inner, codec, tables)}
    if isinstance(value, RubyExtended):
        return {"__extended__": value.modules, "inner": to_json(value.inner, codec, tables)}
    raise ConversionError("cannot represent a %s as JSON" % type(value).__name__)


def _string_to_json(value: RubyString, codec: TextCodec) -> Any:
    if codec.is_clean(value.data):
        text = codec.decode(value.data)
        if codec.is_default_style(value):
            # The common case: nothing to say beyond the text itself.
            return text
        return {"__string__": text,
                "__ivars__": {k: to_json(v, codec) for k, v in value.ivars.items()}}
    # Undecodable bytes: hand them over as base64 so nothing is lost and the
    # caller can see that this is not ordinary text.
    return {"__bytes_base64__": base64.b64encode(value.data).decode("ascii"),
            "__note__": "bytes that are not valid %s" % codec.name}


def table_to_json(table: Table, mode: str = "summary") -> dict[str, Any]:
    out: dict[str, Any] = {
        "__class__": "Table",
        "dim": table.dim,
        "xsize": table.xsize,
        "ysize": table.ysize,
        "zsize": table.zsize,
        "size": table.size,
    }
    if mode == "full" or table.size <= TABLE_INLINE_LIMIT:
        out["cells"] = list(table.data)
    else:
        out["cells_omitted"] = table.size
        out["hint"] = "use map_tiles to read or write regions of this table"
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


def _dict_from_json(value: dict[str, Any], codec: TextCodec) -> Any:
    if "__symbol__" in value:
        return RubySymbol(value["__symbol__"])
    if "__bytes_base64__" in value:
        return RubyString(base64.b64decode(value["__bytes_base64__"]))
    if "__string__" in value:
        ivars = {k: from_json(v, codec)
                 for k, v in (value.get("__ivars__") or {}).items()}
        return RubyString(codec.encode(value["__string__"]), ivars or None)
    if "__hash__" in value:
        pairs = [(from_json(k, codec), from_json(v, codec)) for k, v in value["__hash__"]]
        default = from_json(value["__default__"], codec) if "__default__" in value else None
        return RubyHash(pairs, default)
    if "__struct__" in value:
        return RubyStruct(value["__struct__"],
                          {k: from_json(v, codec)
                           for k, v in (value.get("members") or {}).items()})
    if "__class_ref__" in value:
        return RubyClassRef(value["__class_ref__"], value.get("kind", "class"))
    if "__regexp__" in value:
        return RubyRegexp(codec.encode(value["__regexp__"]), int(value.get("options", 0)))
    if "__uclass__" in value:
        return RubyUClass(value["__uclass__"], from_json(value["inner"], codec))
    if "__extended__" in value:
        return RubyExtended(list(value["__extended__"]), from_json(value["inner"], codec))

    cls = value.get("__class__")
    if cls is None:
        raise ConversionError(
            'an object needs a "__class__" key naming its Ruby class (got keys %s); '
            "JSON objects without one cannot be turned into Ruby values"
            % sorted(value)
        )
    if "__payload_base64__" in value:
        return RubyUserDef(cls, base64.b64decode(value["__payload_base64__"]))
    if "__marshal_value__" in value:
        return RubyUserMarshal(cls, from_json(value["__marshal_value__"], codec))
    if cls == "Table":
        return table_from_json(value)
    if cls == "Color":
        return Color([float(value.get(f, 0.0)) for f in Color.FIELDS])
    if cls == "Tone":
        from .rubytypes import Tone
        return Tone([float(value.get(f, 0.0)) for f in Tone.FIELDS])

    obj = RubyObject(cls)
    for key, item in value.items():
        if key.startswith("__"):
            continue
        obj.set(key, from_json(item, codec))
    return obj


def table_from_json(value: dict[str, Any]) -> Table:
    xsize = int(value.get("xsize", 0))
    ysize = int(value.get("ysize", 1))
    zsize = int(value.get("zsize", 1))
    cells = value.get("cells")
    if cells is None:
        return Table(xsize, ysize, zsize, dim=value.get("dim"))
    return Table(xsize, ysize, zsize, dim=value.get("dim"), data=[int(c) for c in cells])


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------


def apply_patch(target: Any, patch: dict[str, Any], codec: TextCodec,
                path: str = "") -> list[str]:
    """Merge a JSON patch into a loaded Ruby object, in place.

    Nested plain objects merge field by field, so ``{"bgm": {"name": "Town"}}``
    changes only the BGM's name. A value carrying ``__class__`` replaces the
    whole field, which is how you swap an object out wholesale. Returns a list
    of human-readable descriptions of what changed.
    """
    changes: list[str] = []
    for key, new_value in patch.items():
        if key.startswith("__"):
            continue
        field_path = "%s.%s" % (path, key) if path else key

        if isinstance(target, RubyObject):
            existing = target.get(key)
            has_field = target.has(key)
        elif isinstance(target, RubyHash):
            existing = target.get(key)
            has_field = key in target
        else:
            raise ConversionError("cannot patch a %s at %s"
                                  % (type(target).__name__, field_path or "the root"))

        merge = (isinstance(new_value, dict)
                 and not any(k.startswith("__") for k in new_value)
                 and isinstance(existing, (RubyObject, RubyHash)))
        if merge:
            changes.extend(apply_patch(existing, new_value, codec, field_path))
            continue

        converted = from_json(new_value, codec)
        before = _brief(existing, codec) if has_field else "<unset>"
        if isinstance(target, RubyObject):
            target.set(key, converted)
        else:
            target.set(key, converted)
        changes.append("%s: %s -> %s" % (field_path, before, _brief(converted, codec)))
    return changes


def normalize_strings(value: Any, codec: TextCodec, depth: int = 0) -> Any:
    """Restyle every string in a freshly built object tree for this project.

    Templates are written once, in one style; this puts the engine's own string
    form on them so a new event or map is byte-shaped like the editor's output.
    Only ever applied to objects this server just created, never to loaded data.
    """
    if depth > 24:
        return value
    if isinstance(value, RubyString):
        value.ivars = dict(codec.string_ivars)
        return value
    if isinstance(value, RubyObject):
        for item in value.ivars.values():
            normalize_strings(item, codec, depth + 1)
    elif isinstance(value, list):
        for item in value:
            normalize_strings(item, codec, depth + 1)
    elif isinstance(value, RubyHash):
        for _, item in value.pairs:
            normalize_strings(item, codec, depth + 1)
    return value


def _brief(value: Any, codec: TextCodec) -> str:
    """A short, log-friendly rendering of a value."""
    if isinstance(value, RubyString):
        text = codec.decode(value.data) if codec.is_clean(value.data) else "<binary>"
        return '"%s"' % (text if len(text) <= 40 else text[:37] + "...")
    if isinstance(value, RubyObject):
        return "<%s>" % value.cls
    if isinstance(value, Table):
        return "<Table %dx%dx%d>" % (value.xsize, value.ysize, value.zsize)
    if isinstance(value, RubyHash):
        return "<Hash %d entries>" % len(value)
    if isinstance(value, list):
        return "<Array %d items>" % len(value)
    return repr(value)
