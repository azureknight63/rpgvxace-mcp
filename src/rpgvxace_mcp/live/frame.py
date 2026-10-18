"""A captured game frame made ready for the PNG encoder.

GDI hands a window back as 32-bit BGRA; `render.png` encodes RGB or RGBA.
This is the step between, and the halving a frame takes when its PNG is
over the screenshot budget. The encoding itself is `render.png`'s, the one
encoder in the package.
"""

from __future__ import annotations


def bgra_to_rgb(pixels: "bytes | bytearray") -> bytes:
    """32-bit BGRA, as GDI hands a DIB section back, as packed RGB.

    Slices rather than a loop: a 640x480 frame is 300,000 pixels, and this
    runs on the server's only thread.
    """
    count = len(pixels) // 4
    out = bytearray(count * 3)
    out[0::3] = pixels[2::4]
    out[1::3] = pixels[1::4]
    out[2::3] = pixels[0::4]
    return bytes(out)


def halve(width: int, height: int, rgb: bytes) -> "tuple[int, int, bytes]":
    """Every other pixel of every other row: half the size, nearest-neighbour."""
    stride, half_width, half_height = width * 3, width // 2, height // 2
    span = half_width * 6
    rows = []
    for y in range(half_height):
        row = rgb[2 * y * stride:2 * y * stride + span]
        kept = bytearray(half_width * 3)
        for channel in range(3):
            kept[channel::3] = row[channel::6]
        rows.append(bytes(kept))
    return half_width, half_height, b"".join(rows)
