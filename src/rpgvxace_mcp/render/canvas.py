"""Pixel operations on an RGBA :class:`~.png.Image`: copy, blend, scale, draw.

Everything here works on whole rows with slice assignment and ``bytes.translate``
rather than pixel by pixel, because a map render is a few million pixels of
pure Python: a 40 x 30 map is 1.2 million, and one Python-level step each is
seconds. The only per-pixel loop is for the edge pixels of a sprite whose
alpha is neither 0 nor 255, and a row is classified with two ``count`` calls
before it comes to that.
"""

from __future__ import annotations

from array import array
from functools import lru_cache
from operator import itemgetter

from .png import Image


def new_surface(width: int, height: int,
                fill: tuple[int, int, int, int] = (0, 0, 0, 0)) -> Image:
    """A ``width`` x ``height`` image filled with one colour."""
    return Image(width, height, bytearray(bytes(fill) * (width * height)))


def _clip(dst: Image, src: Image, sx: int, sy: int, w: int, h: int,
          dx: int, dy: int) -> tuple[int, int, int, int, int, int] | None:
    """The part of a source rectangle that lands inside both images."""
    if dx < 0:
        sx, w, dx = sx - dx, w + dx, 0
    if dy < 0:
        sy, h, dy = sy - dy, h + dy, 0
    if sx < 0:
        dx, w, sx = dx - sx, w + sx, 0
    if sy < 0:
        dy, h, sy = dy - sy, h + sy, 0
    w = min(w, dst.width - dx, src.width - sx)
    h = min(h, dst.height - dy, src.height - sy)
    if w <= 0 or h <= 0:
        return None
    return sx, sy, w, h, dx, dy


def copy_rect(dst: Image, src: Image, sx: int, sy: int, w: int, h: int,
              dx: int, dy: int) -> None:
    """Replace a rectangle of ``dst`` with one of ``src``, alpha included."""
    clipped = _clip(dst, src, sx, sy, w, h, dx, dy)
    if clipped is None:
        return
    sx, sy, w, h, dx, dy = clipped
    for row in range(h):
        s = ((sy + row) * src.width + sx) * 4
        d = ((dy + row) * dst.width + dx) * 4
        dst.data[d:d + w * 4] = src.data[s:s + w * 4]


def blit(dst: Image, src: Image, sx: int, sy: int, w: int, h: int,
         dx: int, dy: int) -> None:
    """Draw a rectangle of ``src`` over ``dst`` with source-over blending.

    ``dst`` is treated as opaque, which every target here is: the canvas
    starts as a filled background. A row that is entirely opaque is one slice
    copy, one that is entirely clear is skipped, and only a row with partial
    alpha is walked pixel by pixel.
    """
    clipped = _clip(dst, src, sx, sy, w, h, dx, dy)
    if clipped is None:
        return
    sx, sy, w, h, dx, dy = clipped
    sdata, ddata = src.data, dst.data
    for row in range(h):
        s = ((sy + row) * src.width + sx) * 4
        d = ((dy + row) * dst.width + dx) * 4
        pixels = sdata[s:s + w * 4]
        alpha = pixels[3::4]
        opaque = alpha.count(255)
        if opaque == w:
            ddata[d:d + w * 4] = pixels
            continue
        if alpha.count(0) == w:
            continue
        for i in range(w):
            a = alpha[i]
            if a == 255:
                ddata[d + i * 4:d + i * 4 + 4] = pixels[i * 4:i * 4 + 4]
            elif a:
                for c in range(3):
                    at = d + i * 4 + c
                    ddata[at] = (pixels[i * 4 + c] * a
                                 + ddata[at] * (255 - a) + 127) // 255


@lru_cache(maxsize=256)
def _mix_table(value: int, alpha: int) -> bytes:
    """What a channel byte becomes under ``value`` at ``alpha``/255 coverage."""
    return bytes((v * (255 - alpha) + value * alpha + 127) // 255
                 for v in range(256))


def blend_rect(dst: Image, x: int, y: int, w: int, h: int,
               rgb: tuple[int, int, int], alpha: int) -> None:
    """Tint a rectangle of ``dst`` with a colour at ``alpha``/255 strength."""
    left, top = max(x, 0), max(y, 0)
    right, bottom = min(x + w, dst.width), min(y + h, dst.height)
    if right <= left or bottom <= top or alpha <= 0:
        return
    tables = [_mix_table(rgb[c], min(alpha, 255)) for c in range(3)]
    span = right - left
    for row in range(top, bottom):
        d = (row * dst.width + left) * 4
        segment = dst.data[d:d + span * 4]
        for c in range(3):
            segment[c::4] = segment[c::4].translate(tables[c])
        dst.data[d:d + span * 4] = segment


# A 3 x 5 pixel digit font, one string of rows per glyph. Region and event
# numbers are the only text this module draws, and a font of ten glyphs is
# smaller than the code that would load a real one.
_GLYPHS = {
    "0": "111101101101111", "1": "010110010010111", "2": "111001111100111",
    "3": "111001111001111", "4": "101101111001001", "5": "111100111001111",
    "6": "111100111101111", "7": "111001010010010", "8": "111101111101111",
    "9": "111101111001111",
}


def draw_number(dst: Image, x: int, y: int, number: int, scale: int = 2,
                rgb: tuple[int, int, int] = (255, 255, 255)) -> tuple[int, int]:
    """Write a non-negative integer on a dark plate, top-left at ``(x, y)``.

    Returns the plate's width and height so a caller can lay out a second line.
    """
    digits = str(max(number, 0))
    width = (len(digits) * 4 + 1) * scale
    height = 7 * scale
    blend_rect(dst, x, y, width, height, (0, 0, 0), 170)
    for index, digit in enumerate(digits):
        glyph = _GLYPHS[digit]
        for row in range(5):
            for col in range(3):
                if glyph[row * 3 + col] == "1":
                    px = x + (index * 4 + 1 + col) * scale
                    py = y + (1 + row) * scale
                    blend_rect(dst, px, py, scale, scale, rgb, 255)
    return width, height


def resize_nearest(src: Image, width: int, height: int) -> Image:
    """``src`` resampled to a new size by taking the nearest source pixel."""
    if (width, height) == (src.width, src.height):
        return src
    pixels = array("I")
    pixels.frombytes(bytes(src.data))
    columns = [min(src.width - 1, x * src.width // width) for x in range(width)]
    pick = itemgetter(*columns) if width > 1 else (lambda row: (row[columns[0]],))
    out = array("I")
    last_row = -1
    last = None
    for y in range(height):
        source_row = min(src.height - 1, y * src.height // height)
        if source_row != last_row:
            last = array("I", pick(pixels[source_row * src.width:
                                          (source_row + 1) * src.width]))
            last_row = source_row
        out.extend(last)
    return Image(width, height, bytearray(out.tobytes()))


def to_rgb(src: Image) -> bytes:
    """The pixels without their alpha channel, for an opaque PNG."""
    out = bytearray(src.width * src.height * 3)
    out[0::3] = src.data[0::4]
    out[1::3] = src.data[1::4]
    out[2::3] = src.data[2::4]
    return bytes(out)
