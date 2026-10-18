"""Encrypted RGSS archives: ``Game.rgss3a`` (VX Ace) and ``Game.rgss2a`` (VX).

An archive is one file holding the project's ``Data``, ``Graphics`` and
``Audio`` folders under a light XOR scrambling. This module lists, reads and
extracts one; it never writes into an archive and never executes anything.

The format, as this reader understands it::

    header   "RGSSAD" 0x00, then a version byte: 1 (rgssad, rgss2a) or 3 (rgss3a)

    version 1   a 32-bit key starts at 0xDEADCAFE and every use advances it,
                key = key * 7 + 3 (mod 2**32). Entries run to the end of file:
                  name length   u32 ^ key        (key advances)
                  name          each byte ^ (key & 0xFF), key advancing per byte
                  data length   u32 ^ key        (key advances)
                  data          scrambled with a copy of the key as it now
                                stands; that copy steps once per 4 bytes. The
                                stream key itself is not advanced by the data.

    version 3   a u32 base value follows the header; the key is base * 9 + 3
                and never changes. The table is entries of
                  offset, size, file key, name length   (each u32 ^ key)
                  name          byte i ^ byte (i mod 4) of the key
                ended by an offset of 0. A body sits at its offset and is
                scrambled with its own file key, stepped x7+3 per 4 bytes.

What has been checked against a real archive: nothing. No archive was available
when this was written and none was downloaded, so every detail above comes from
the format's published description, and the tests round-trip against an encoder
written separately from it. The details most worth a second look on a real file
are the version 1 data key (taken from the stream key after the length field,
and left unadvanced) and the version 3 name bytes (cycling through the base
key's four bytes).

Everything read is hostile until checked: counts, name lengths and sizes are
bounded before anything is allocated, names are refused unless they stay under
the directory they are extracted to, and bodies are streamed in fixed chunks.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import Iterator

from .paths import ProjectError, _RESERVED_NAMES, _contained


# Archive file names, in the order they are looked for.
ARCHIVE_NAMES = ("Game.rgss3a", "Game.rgss2a")


# More than any shipped game has; a table claiming more is refused.
MAX_ENTRIES = 100_000


# Windows' MAX_PATH. Longer names are refused, not truncated.
MAX_NAME_BYTES = 260


# One entry's body. Bodies are streamed, so this bounds time and disk, not
# memory; `read` holds a whole body and is the reason it is capped.
MAX_ENTRY_BYTES = 512 * 1024 * 1024


# What one extraction may write in total.
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


# Streaming chunk, a multiple of 4 so the key steps stay aligned.
CHUNK_BYTES = 1024 * 1024


_MAGIC = b"RGSSAD\0"


_V1_KEY = 0xDEADCAFE


_MASK = 0xFFFFFFFF


# Characters Windows will not take in a file name, plus the stream separator.
_BAD_CHARS = frozenset('<>:"|?*')


class ArchiveError(ProjectError):
    """The archive is damaged, hostile, or over a limit."""


class Entry:
    """One archived file: where its body is, and the key that opens it."""

    __slots__ = ("name", "offset", "size", "key")

    def __init__(self, name: "str | bytes", offset: int, size: int, key: int):
        self.name = name
        self.offset = offset
        self.size = size
        self.key = key


def find_archive(root: Path) -> Path | None:
    """The archive in a project folder, if it ships one."""
    for name in ARCHIVE_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


class RgssArchive:
    """A parsed archive: its entries, and the means to read or extract them."""

    def __init__(self, path: Path, version: int, entries: list[Entry]):
        self.path = path
        self.version = version
        self.entries = entries
        self._by_name = {_fold(entry.name): entry for entry in entries}

    @classmethod
    def open(cls, path: str | os.PathLike[str]) -> "RgssArchive":
        path = Path(path)
        try:
            with open(path, "rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                head = handle.read(8)
                if len(head) < 8 or head[:7] != _MAGIC:
                    raise ArchiveError(
                        "%s is not an RGSS archive: it does not start with "
                        "the RGSSAD header." % path.name)
                version = head[7]
                if version == 1:
                    entries = _read_v1(handle, size)
                elif version == 3:
                    entries = _read_v3(handle, size)
                else:
                    raise ArchiveError(
                        "%s is RGSS archive version %d; only versions 1 "
                        "(rgssad, rgss2a) and 3 (rgss3a) are known."
                        % (path.name, version))
        except OSError as exc:
            raise ArchiveError("could not read %s: %s" % (path, exc)) from exc
        _check_names(entries, path.name)
        return cls(path, version, entries)

    def names(self) -> list[str]:
        return [entry.name for entry in self.entries]

    def stems_in(self, folder: str) -> set[str]:
        """File names without extension directly inside one archived folder."""
        want = _fold(folder).strip("/")
        stems = set()
        for entry in self.entries:
            parent, _, leaf = entry.name.rpartition("/")
            if _fold(parent) == want:
                stems.add(os.path.splitext(leaf)[0])
        return stems

    def _entry(self, which: "Entry | str") -> Entry:
        if isinstance(which, Entry):
            return which
        entry = self._by_name.get(_fold(which))
        if entry is None:
            raise ArchiveError("%s holds no file named %r" % (self.path.name, which))
        return entry

    def read(self, which: "Entry | str") -> bytes:
        """One file's decrypted bytes, held in memory (so size-capped)."""
        entry = self._entry(which)
        if entry.size > MAX_ENTRY_BYTES:
            raise ArchiveError(
                "%s is %d bytes, over the %d-byte limit for reading one file "
                "into memory." % (entry.name, entry.size, MAX_ENTRY_BYTES))
        return b"".join(self._chunks(entry))

    def _chunks(self, entry: Entry) -> Iterator[bytes]:
        key, left = entry.key, entry.size
        try:
            with open(self.path, "rb") as handle:
                handle.seek(entry.offset)
                while left:
                    raw = handle.read(min(left, CHUNK_BYTES))
                    if not raw:
                        raise ArchiveError(
                            "%s is truncated inside %s" % (self.path.name, entry.name))
                    left -= len(raw)
                    yield _unscramble(raw, key)
                    key = _advance(key, (len(raw) + 3) // 4)
        except OSError as exc:
            raise ArchiveError("could not read %s: %s" % (self.path, exc)) from exc

    def extract(self, destination: str | os.PathLike[str],
                only: str | None = None) -> int:
        """Write the files (or one top-level folder's) under `destination`.

        Refuses before writing anything if the total is over the limit, never
        overwrites, and checks each target resolves inside `destination`.
        Returns the bytes written.
        """
        chosen = [e for e in self.entries
                  if only is None or _fold(e.name).split("/")[0] == _fold(only)]
        total = sum(e.size for e in chosen)
        if total > MAX_TOTAL_BYTES:
            raise ArchiveError(
                "extracting would write %d bytes in total, over the %d-byte "
                "limit." % (total, MAX_TOTAL_BYTES))
        root = Path(destination)
        try:
            root.mkdir(parents=True, exist_ok=True)
            for entry in chosen:
                self._write(entry, root)
        except OSError as exc:
            raise ArchiveError(
                "could not extract %s into %s: %s" % (self.path.name, root, exc)
            ) from exc
        return total

    def _write(self, entry: Entry, root: Path) -> None:
        target = root.joinpath(*entry.name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        _contained(target.parent, root, "archive entry %r" % entry.name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(target, flags, 0o644)
        with os.fdopen(descriptor, "wb") as out:
            for chunk in self._chunks(entry):
                out.write(chunk)


def _fold(name: str) -> str:
    return name.replace("\\", "/").casefold()


def _step(key: int) -> int:
    return (key * 7 + 3) & _MASK


def _advance(key: int, steps: int) -> int:
    for _ in range(steps):
        key = _step(key)
    return key


def _unscramble(raw: bytes, key: int) -> bytes:
    """XOR with a key stepping every 4 bytes; a partial tail uses the low bytes."""
    words = (len(raw) + 3) // 4
    stream = bytearray()
    for _ in range(words):
        stream += struct.pack("<I", key)
        key = _step(key)
    mask = int.from_bytes(stream[:len(raw)], "little")
    return (int.from_bytes(raw, "little") ^ mask).to_bytes(len(raw), "little")


def _need(handle, count: int, what: str) -> bytes:
    raw = handle.read(count)
    if len(raw) != count:
        raise ArchiveError("the archive is truncated in %s" % what)
    return raw


def _read_v1(handle, file_size: int) -> list[Entry]:
    entries: list[Entry] = []
    key = _V1_KEY
    position = 8
    while position < file_size:
        if len(entries) >= MAX_ENTRIES:
            raise ArchiveError(
                "the archive has more than %d entries, which is not plausible"
                % MAX_ENTRIES)
        length = struct.unpack("<I", _need(handle, 4, "an entry's name length"))[0] ^ key
        key = _step(key)
        if not 0 < length <= MAX_NAME_BYTES:
            raise ArchiveError(
                "an entry has a name length of %d; names run 1 to %d bytes"
                % (length, MAX_NAME_BYTES))
        raw = _need(handle, length, "an entry's name")
        name = bytearray()
        for byte in raw:
            name.append(byte ^ (key & 0xFF))
            key = _step(key)
        size = struct.unpack("<I", _need(handle, 4, "an entry's data length"))[0] ^ key
        key = _step(key)
        position += 8 + length
        if position + size > file_size:
            raise ArchiveError(
                "an entry's data runs %d bytes past the end of the archive"
                % (position + size - file_size))
        entries.append(Entry(bytes(name), position, size, key))
        position += size
        handle.seek(position)
    return entries


def _read_v3(handle, file_size: int) -> list[Entry]:
    base = struct.unpack("<I", _need(handle, 4, "the base key"))[0]
    key = (base * 9 + 3) & _MASK
    entries: list[Entry] = []
    while True:
        offset = struct.unpack("<I", _need(handle, 4, "the entry table"))[0] ^ key
        if offset == 0:
            break
        if len(entries) >= MAX_ENTRIES:
            raise ArchiveError(
                "the archive has more than %d entries, which is not plausible"
                % MAX_ENTRIES)
        size, file_key, length = (
            value ^ key for value in struct.unpack(
                "<III", _need(handle, 12, "the entry table")))
        if not 0 < length <= MAX_NAME_BYTES:
            raise ArchiveError(
                "an entry has a name length of %d; names run 1 to %d bytes"
                % (length, MAX_NAME_BYTES))
        raw = _need(handle, length, "an entry's name")
        name = bytes(b ^ ((key >> (8 * (i % 4))) & 0xFF) for i, b in enumerate(raw))
        if offset + size > file_size:
            raise ArchiveError(
                "an entry's data (offset %d, %d bytes) lies outside the %d-byte "
                "archive" % (offset, size, file_size))
        entries.append(Entry(name, offset, size, file_key))
    table_end = handle.tell()
    previous_end = table_end
    for entry in sorted((e for e in entries if e.size), key=lambda e: e.offset):
        if entry.offset < previous_end:
            raise ArchiveError(
                "entry data overlaps the table or another entry (offset %d)"
                % entry.offset)
        previous_end = entry.offset + entry.size
    return entries


def _check_names(entries: list[Entry], archive: str) -> None:
    """Decode and vet every name, in place; refuse the archive on the first bad one."""
    seen: set[str] = set()
    for entry in entries:
        raw = entry.name
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = raw.decode("cp932")
            except UnicodeDecodeError as exc:
                raise ArchiveError(
                    "%s has an entry name that is neither UTF-8 nor Shift-JIS"
                    % archive) from exc
        entry.name = _safe_name(text.replace("\\", "/"), archive)
        folded = entry.name.casefold()
        if folded in seen:
            raise ArchiveError("%s lists %r twice" % (archive, entry.name))
        seen.add(folded)


def _safe_name(name: str, archive: str) -> str:
    def refuse(why: str):
        return ArchiveError(
            "%s has an entry named %s, which is refused: %s" % (archive, ascii(name), why))

    if name.startswith("/"):
        raise refuse("it is an absolute or network path")
    for part in name.split("/"):
        if not part:
            raise refuse("it has an empty path component")
        if part in (".", ".."):
            raise refuse("it climbs or repeats a directory")
        if any(ord(c) < 32 or ord(c) == 127 or c in _BAD_CHARS for c in part):
            raise refuse("it has a drive letter, stream marker or character "
                         "Windows does not allow in names")
        if part.endswith((".", " ")):
            raise refuse("a component ends in a dot or a space")
        if part.split(".")[0].casefold() in _RESERVED_NAMES:
            raise refuse("it names a Windows device")
    return name
