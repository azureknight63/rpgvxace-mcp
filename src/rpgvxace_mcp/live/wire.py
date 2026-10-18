"""The bridge's file protocol: where each message lives and what it holds.

Both halves speak Ruby Marshal, because RGSS3 has it built in and this
server already has a codec for it. RGSS3 is Ruby 1.9.2 with no standard
library to speak of -- `require "socket"`, `"json"` and `"yaml"` all raise
LoadError inside Game.exe -- so a socket or a JSON payload would mean
shipping a parser in Ruby, and files plus Marshal mean shipping nothing.

One message per file, written to a `.tmp` name and renamed into place, so
a reader sees a whole message or none. The game polls once a frame.

    config.bin    Python -> Ruby, before launch: session token and switches
    ready.bin     Ruby -> Python, once: the bridge is up and polling
    request.bin   Python -> Ruby: {"seq", "op", "args"}
    response.bin  Ruby -> Python: {"seq", "ok", "result" | "error", ...}
    crash.bin     Ruby -> Python: a Ruby exception the bridge caught

The names live here and are substituted into the Ruby source, so the two
halves cannot spell one file two ways.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .. import marshal_rb
from ..rubytypes import RubyFloat, RubyHash, RubyString, RubySymbol, printable_text


BRIDGE_DIR = ".rpgvxace-mcp/bridge"
CONFIG_FILE = "config.bin"
READY_FILE = "ready.bin"
REQUEST_FILE = "request.bin"
RESPONSE_FILE = "response.bin"
CRASH_FILE = "crash.bin"


# The largest message either side reads. A reply this server asked for is a
# few kilobytes; one larger than this is not a reply.
MAX_WIRE_BYTES = 8 * 1024 * 1024


# How deep a reply may nest before the rest is replaced by a note. The
# bridge's replies are three or four levels deep; this bounds the recursion
# for one that is not.
MAX_WIRE_DEPTH = 32


# Longest single string kept from a reply.
MAX_WIRE_TEXT = 20_000


def to_ruby(value: Any) -> Any:
    """A JSON-shaped Python value as the Ruby value Marshal should carry.

    Strings are tagged UTF-8 (``E: true``): Ruby 1.9 reads an untagged
    String as ASCII-8BIT, and comparing that against a literal in the bridge
    fails for anything outside ASCII.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return RubyString(value.encode("utf-8", "replace"), {"E": True})
    if isinstance(value, (list, tuple)):
        return [to_ruby(item) for item in value]
    if isinstance(value, dict):
        return RubyHash([(to_ruby(key), to_ruby(item))
                         for key, item in value.items()])
    raise TypeError("cannot send a %s to the game" % type(value).__name__)


def from_ruby(value: Any, depth: int = 0) -> Any:
    """A reply from the game as JSON-ready Python, sanitised and bounded.

    Hash keys come back as strings, because the next stop is a JSON reply
    and JSON has no other kind. Text is passed through `printable_text`:
    a switch name or a message line is text this server did not write.
    """
    if depth > MAX_WIRE_DEPTH:
        return "(nested too deep to show; ask for less)"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, RubyFloat):
        return float(value)
    if isinstance(value, float):
        return value
    if isinstance(value, RubySymbol):
        return printable_text(str(value))
    if isinstance(value, RubyString):
        text = value.data.decode("utf-8", "replace")
        return printable_text(text[:MAX_WIRE_TEXT])
    if isinstance(value, (list, tuple)):
        return [from_ruby(item, depth + 1) for item in value]
    if isinstance(value, RubyHash):
        out = {}
        for key, item in value.pairs:
            name = from_ruby(key, depth + 1)
            out[name if isinstance(name, str) else str(name)] = from_ruby(
                item, depth + 1)
        return out
    return printable_text(repr(value)[:200])


def write_atomic(path: "str | os.PathLike[str]", value: Any) -> None:
    """Marshal ``value`` (already Ruby-shaped) to ``path`` in one rename."""
    target = Path(path)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_bytes(marshal_rb.dump(value))
    os.replace(temporary, target)


def read_message(path: "str | os.PathLike[str]") -> Any:
    """One message file, decoded to JSON-ready Python.

    Bounded before it is read: the file is in a folder a running game
    writes to, and the game runs whatever scripts the project carries.
    """
    target = Path(path)
    size = target.stat().st_size
    if size > MAX_WIRE_BYTES:
        raise ValueError("%s is %d bytes, above the %d this server reads"
                         % (target.name, size, MAX_WIRE_BYTES))
    return from_ruby(marshal_rb.load(target.read_bytes()))
