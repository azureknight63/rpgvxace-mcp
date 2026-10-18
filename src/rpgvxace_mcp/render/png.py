"""A PNG decoder and encoder in the standard library alone.

The decoder reads what RPG Maker tilesets and character sheets are: 8-bit
greyscale, RGB, palette, greyscale-with-alpha and RGBA, with all five scanline
filters. It refuses everything else by name rather than guessing, and it
treats the file as hostile, because a project can carry any PNG at all and the
map renderer opens every sheet a tileset names:

* the declared size is bounded before anything is allocated,
* the compressed stream is inflated with a ceiling of exactly the size the
  header implies, so a decompression bomb costs one extra byte of work,
* every chunk's CRC is checked, and a file with no IEND is truncated,
* interlaced images are refused (no tileset is Adam7 and the pass logic is a
  second decoder to get wrong).

The result is always 8-bit RGBA, whatever the file held.
"""

from __future__ import annotations

import struct
import zlib
from itertools import accumulate


SIGNATURE = b"\x89PNG\r\n\x1a\n"


# The largest file this module will open. A real RTP sheet is under 200 KB.
MAX_PNG_BYTES = 16 * 1024 * 1024


# The largest side, and the most pixels, a decoded image may have. The pixel
# ceiling is what bounds memory (four bytes each) and the side ceiling stops a
# 1 x 100,000,000 strip that passes the first; a tileset sheet is 1536 x 1152
# at the very most.
MAX_PNG_SIDE = 8192
MAX_PNG_PIXELS = 8 * 1024 * 1024


# How many chunks one file may hold. A real one has a handful of IDAT chunks;
# a file of a million empty chunks is a file that wants the loop to spin.
MAX_PNG_CHUNKS = 4096


# Samples per pixel for each colour type, and how a refusal names it.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_COLOUR_TYPE_NAMES = {0: "greyscale", 2: "RGB", 3: "palette",
                      4: "greyscale with alpha", 6: "RGBA"}


class PngError(ValueError):
    """The bytes are not a PNG this module will read, and why."""


class Image:
    """A decoded picture: ``data`` is ``width * height`` RGBA pixels, row by row."""

    __slots__ = ("width", "height", "data")

    def __init__(self, width: int, height: int, data: bytearray):
        self.width = width
        self.height = height
        self.data = data


def _name(kind: bytes) -> str:
    return kind.decode("latin-1", "replace")


def _chunks(data: bytes):
    """Each ``(type, payload)`` in order, with its CRC checked."""
    if data[:8] != SIGNATURE:
        raise PngError("not a PNG file (the 8-byte signature is wrong)")
    position = 8
    count = 0
    while True:
        if position + 8 > len(data):
            raise PngError("the PNG is truncated: it ends before its IEND "
                           "chunk")
        length, kind = struct.unpack_from(">I4s", data, position)
        count += 1
        if count > MAX_PNG_CHUNKS:
            raise PngError("the PNG holds more than %d chunks" % MAX_PNG_CHUNKS)
        start = position + 8
        end = start + length
        if end + 4 > len(data):
            raise PngError("the PNG is truncated inside a %s chunk"
                           % _name(kind))
        payload = data[start:end]
        (crc,) = struct.unpack_from(">I", data, end)
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise PngError("a %s chunk fails its CRC check, so the file is "
                           "corrupt" % _name(kind))
        yield kind, payload
        if kind == b"IEND":
            return
        position = end + 4


def _header(payload: bytes) -> tuple[int, int, int, int]:
    if len(payload) != 13:
        raise PngError("the IHDR chunk is %d bytes, not 13" % len(payload))
    width, height, depth, colour, compression, filtering, interlace = \
        struct.unpack(">IIBBBBB", payload)
    if not (1 <= width <= MAX_PNG_SIDE and 1 <= height <= MAX_PNG_SIDE):
        raise PngError("the image declares %d x %d pixels; this reader takes "
                       "1 to %d on a side" % (width, height, MAX_PNG_SIDE))
    if width * height > MAX_PNG_PIXELS:
        raise PngError("the image declares %d pixels, above the %d this "
                       "reader will hold" % (width * height, MAX_PNG_PIXELS))
    if colour not in _CHANNELS:
        raise PngError("colour type %d is not a PNG colour type" % colour)
    if depth != 8:
        raise PngError("bit depth %d (%s) is not supported; save the image "
                       "as 8 bits per channel"
                       % (depth, _COLOUR_TYPE_NAMES[colour]))
    if compression != 0 or filtering != 0:
        raise PngError("the IHDR names a compression or filter method PNG "
                       "does not define")
    if interlace != 0:
        raise PngError("interlaced (Adam7) PNGs are not supported; save the "
                       "image without interlacing")
    return width, height, colour, _CHANNELS[colour]


def _inflate(compressed: bytes, expected: int) -> bytes:
    """The whole stream, which must come to exactly ``expected`` bytes."""
    decoder = zlib.decompressobj()
    try:
        # One byte past the size the header implies: enough to notice a stream
        # that wants to be larger, without ever producing the bomb.
        raw = decoder.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise PngError("the image data does not decompress (%s)" % exc) from exc
    if len(raw) > expected or decoder.unconsumed_tail:
        raise PngError("the image data inflates to more than the %d bytes its "
                       "header allows -- refused as a decompression bomb"
                       % expected)
    if len(raw) < expected or not decoder.eof:
        raise PngError("the image data is truncated: %d bytes where the "
                       "header implies %d" % (len(raw), expected))
    return raw


def _add_bytes(a: bytes, b: bytes, low: int, high: int) -> bytes:
    """Byte-wise ``(a + b) mod 256`` of two equal-length strings.

    Done on whole integers: the seven low bits of every byte are added, which
    cannot carry into the next byte, and the top bit is repaired with an xor.
    One C-level pass per row instead of one Python step per byte, which is
    what the Up filter comes to.
    """
    x = int.from_bytes(a, "little")
    y = int.from_bytes(b, "little")
    summed = ((x & low) + (y & low)) ^ ((x ^ y) & high)
    return summed.to_bytes(len(a), "little")


def _unfilter(raw: bytes, height: int, stride: int, bpp: int) -> bytearray:
    """Undo the five scanline filters, row by row."""
    out = bytearray(height * stride)
    low = int.from_bytes(b"\x7f" * stride, "little")
    high = int.from_bytes(b"\x80" * stride, "little")
    previous = bytes(stride)
    position = 0
    for y in range(height):
        kind = raw[position]
        row = bytearray(raw[position + 1:position + 1 + stride])
        position += 1 + stride
        if kind == 0:
            pass
        elif kind == 1:
            for channel in range(bpp):
                row[channel::bpp] = bytes(
                    [v & 255 for v in accumulate(row[channel::bpp])])
        elif kind == 2:
            row = bytearray(_add_bytes(row, previous, low, high))
        elif kind == 3:
            for i in range(bpp):
                row[i] = (row[i] + (previous[i] >> 1)) & 255
            for i in range(bpp, stride):
                row[i] = (row[i] + ((row[i - bpp] + previous[i]) >> 1)) & 255
        elif kind == 4:
            for i in range(bpp):
                row[i] = (row[i] + previous[i]) & 255
            for i in range(bpp, stride):
                a = row[i - bpp]
                b = previous[i]
                c = previous[i - bpp]
                p = a + b - c
                pa = p - a if p > a else a - p
                pb = p - b if p > b else b - p
                pc = p - c if p > c else c - p
                if pa <= pb and pa <= pc:
                    predictor = a
                elif pb <= pc:
                    predictor = b
                else:
                    predictor = c
                row[i] = (row[i] + predictor) & 255
        else:
            raise PngError("row %d uses filter type %d; PNG defines 0 to 4"
                           % (y, kind))
        out[y * stride:(y + 1) * stride] = row
        previous = row
    return out


def _palette_tables(palette: bytes | None, transparency: bytes | None
                    ) -> tuple[int, bytes, bytes, bytes, bytes]:
    """The palette as four 256-entry lookup tables, and how many are real."""
    if not palette:
        raise PngError("a palette image has no PLTE chunk")
    if len(palette) % 3 or len(palette) > 768:
        raise PngError("the PLTE chunk is %d bytes, which is not 1 to 256 "
                       "colours" % len(palette))
    count = len(palette) // 3
    alphas = (bytes(transparency or b"")[:count]).ljust(count, b"\xff")
    pad = bytes(256 - count)
    return (count, palette[0::3] + pad, palette[1::3] + pad,
            palette[2::3] + pad, alphas + pad)


def _to_rgba(samples: bytes, width: int, height: int, colour: int,
             palette: bytes | None, transparency: bytes | None) -> bytearray:
    pixels = width * height
    out = bytearray(pixels * 4)
    if colour == 6:
        out[:] = samples
        return out
    out[3::4] = b"\xff" * pixels
    if colour == 2:
        out[0::4], out[1::4], out[2::4] = (
            samples[0::3], samples[1::3], samples[2::3])
        if transparency is not None:
            if len(transparency) != 6:
                raise PngError("the tRNS chunk of an RGB image is %d bytes, "
                               "not 6" % len(transparency))
            key = bytes([transparency[1], transparency[3], transparency[5]])
            for i in range(pixels):
                if samples[i * 3:i * 3 + 3] == key:
                    out[i * 4 + 3] = 0
    elif colour == 0:
        out[0::4] = out[1::4] = out[2::4] = samples
        if transparency is not None:
            if len(transparency) != 2:
                raise PngError("the tRNS chunk of a greyscale image is %d "
                               "bytes, not 2" % len(transparency))
            key = transparency[1]
            out[3::4] = samples.translate(
                bytes(0 if v == key else 255 for v in range(256)))
    elif colour == 4:
        out[0::4] = out[1::4] = out[2::4] = samples[0::2]
        out[3::4] = samples[1::2]
    else:
        count, reds, greens, blues, alphas = _palette_tables(palette,
                                                             transparency)
        if samples and max(samples) >= count:
            raise PngError("a pixel names palette entry %d but the palette "
                           "holds %d" % (max(samples), count))
        out[0::4] = samples.translate(reds)
        out[1::4] = samples.translate(greens)
        out[2::4] = samples.translate(blues)
        out[3::4] = samples.translate(alphas)
    return out


def decode(data: bytes) -> Image:
    """Read PNG bytes into an RGBA :class:`Image`, or raise :class:`PngError`."""
    if len(data) > MAX_PNG_BYTES:
        raise PngError("the file is %d bytes, above the %d this reader opens"
                       % (len(data), MAX_PNG_BYTES))
    header = None
    palette = transparency = None
    compressed = []
    seen_data = False
    for kind, payload in _chunks(data):
        if header is None:
            if kind != b"IHDR":
                raise PngError("the first chunk is %s, not IHDR" % _name(kind))
            header = _header(payload)
        elif kind == b"IHDR":
            raise PngError("the PNG has two IHDR chunks")
        elif kind == b"PLTE":
            if seen_data:
                raise PngError("the PLTE chunk comes after the image data")
            palette = payload
        elif kind == b"tRNS":
            transparency = payload
        elif kind == b"IDAT":
            seen_data = True
            compressed.append(payload)
        elif kind != b"IEND" and kind[0:1].isupper():
            # A critical chunk this reader does not know cannot be skipped
            # without drawing the wrong picture; an ancillary one can.
            raise PngError("the PNG has a critical %s chunk this reader does "
                           "not understand" % _name(kind))
    if header is None:
        raise PngError("the PNG has no IHDR chunk")
    width, height, colour, bpp = header
    if not compressed:
        raise PngError("the PNG has no image data")
    stride = width * bpp
    raw = _inflate(b"".join(compressed), height * (stride + 1))
    samples = bytes(_unfilter(raw, height, stride, bpp))
    return Image(width, height,
                 _to_rgba(samples, width, height, colour, palette,
                          transparency))


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def encode(width: int, height: int, pixels: bytes | bytearray,
           channels: int = 4) -> bytes:
    """PNG bytes for 8-bit RGB (``channels=3``) or RGBA (``channels=4``) pixels."""
    if channels not in (3, 4):
        raise PngError("the encoder writes RGB or RGBA, not %d channels"
                       % channels)
    if width < 1 or height < 1 or len(pixels) != width * height * channels:
        raise PngError("%d bytes is not a %d x %d image of %d channels"
                       % (len(pixels), width, height, channels))
    stride = width * channels
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows += pixels[y * stride:(y + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, 8,
                         6 if channels == 4 else 2, 0, 0, 0)
    return (SIGNATURE + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(rows), 6))
            + _chunk(b"IEND", b""))
