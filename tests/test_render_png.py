"""The PNG decoder and encoder, against PNGs built by hand.

Nothing here opens a file the repository did not write: each case builds its
bytes from the specification (a forward filter written independently of the
decoder's inverse), so a decoder that is wrong the same way as the encoder
cannot pass by agreeing with itself.
"""

import random
import struct
import sys
import unittest
import zlib
from pathlib import Path


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from rpgvxace_mcp.render import png  # noqa: E402


def chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def filtered(rows, bpp, kinds):
    """Scanlines with the forward filter of each row's kind applied."""
    out = bytearray()
    previous = bytes(len(rows[0]))
    for index, row in enumerate(rows):
        kind = kinds[index % len(kinds)]
        out.append(kind)
        for i, value in enumerate(row):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            upleft = previous[i - bpp] if i >= bpp else 0
            predictor = (0, left, up, (left + up) // 2,
                         paeth(left, up, upleft))[kind]
            out.append((value - predictor) & 255)
        previous = row
    return bytes(out)


def build(width, height, colour, rows, kinds=(0,), extra=(), depth=8,
          interlace=0, idat_split=1):
    """A PNG holding `rows` (a list of raw sample rows)."""
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    body = zlib.compress(filtered(rows, bpp, kinds))
    header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0,
                         interlace)
    size = max(1, len(body) // idat_split)
    pieces = [body[i:i + size] for i in range(0, len(body), size)]
    return (png.SIGNATURE + chunk(b"IHDR", header)
            + b"".join(chunk(kind, payload) for kind, payload in extra)
            + b"".join(chunk(b"IDAT", p) for p in pieces)
            + chunk(b"IEND", b""))


def pattern_rows(width, height, bpp):
    """Rows that make every filter's predictor non-trivial.

    Noise, not a gradient: on a smooth image Paeth picks the left neighbour
    nearly every time, and a decoder that always did would pass.
    """
    rng = random.Random(width * 131 + height * 7 + bpp)
    return [rng.randbytes(width * bpp) for _ in range(height)]


class TestDecodingEveryFilter(unittest.TestCase):
    def test_each_filter_type_alone_restores_the_rows(self):
        for kind in range(5):
            for colour, bpp in ((6, 4), (2, 3)):
                with self.subTest(filter=kind, colour=colour):
                    rows = pattern_rows(9, 6, bpp)
                    image = png.decode(build(9, 6, colour, rows, (kind,)))
                    self.assertEqual((image.width, image.height), (9, 6))
                    expected = bytearray()
                    for row in rows:
                        for x in range(9):
                            pixel = row[x * bpp:(x + 1) * bpp]
                            expected += pixel + (b"" if bpp == 4 else b"\xff")
                    self.assertEqual(bytes(image.data), bytes(expected))

    def test_every_filter_in_one_image(self):
        rows = pattern_rows(11, 10, 4)
        image = png.decode(build(11, 10, 6, rows, (0, 1, 2, 3, 4, 4, 3)))
        self.assertEqual(bytes(image.data), b"".join(rows))

    def test_the_data_may_be_split_across_idat_chunks(self):
        rows = pattern_rows(16, 16, 4)
        image = png.decode(build(16, 16, 6, rows, (4,), idat_split=7))
        self.assertEqual(bytes(image.data), b"".join(rows))


class TestEveryColourType(unittest.TestCase):
    def test_greyscale(self):
        rows = [bytes([0, 128, 255]), bytes([10, 20, 30])]
        image = png.decode(build(3, 2, 0, rows))
        self.assertEqual(bytes(image.data[:8]), bytes([0, 0, 0, 255,
                                                       128, 128, 128, 255]))

    def test_greyscale_with_alpha(self):
        rows = [bytes([200, 0, 100, 255])]
        image = png.decode(build(2, 1, 4, rows))
        self.assertEqual(bytes(image.data), bytes([200, 200, 200, 0,
                                                   100, 100, 100, 255]))

    def test_palette_with_transparency(self):
        palette = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])
        image = png.decode(build(3, 1, 3, [bytes([2, 0, 1])],
                                 extra=[(b"PLTE", palette),
                                        (b"tRNS", bytes([255, 0]))]))
        # tRNS lists alphas for entries 0 and 1 only; entry 2 defaults opaque.
        self.assertEqual(bytes(image.data), bytes([0, 0, 255, 255,
                                                   255, 0, 0, 255,
                                                   0, 255, 0, 0]))

    def test_rgb_colour_key_transparency(self):
        rows = [bytes([1, 2, 3, 9, 9, 9])]
        key = struct.pack(">HHH", 1, 2, 3)
        image = png.decode(build(2, 1, 2, rows, extra=[(b"tRNS", key)]))
        self.assertEqual(bytes(image.data), bytes([1, 2, 3, 0, 9, 9, 9, 255]))

    def test_an_unknown_ancillary_chunk_is_skipped(self):
        rows = pattern_rows(2, 2, 4)
        image = png.decode(build(2, 2, 6, rows, extra=[(b"tEXt", b"k\0v")]))
        self.assertEqual(bytes(image.data), b"".join(rows))


class TestHostileInput(unittest.TestCase):
    def refused(self, data, fragment):
        with self.assertRaises(png.PngError) as caught:
            png.decode(data)
        self.assertIn(fragment, str(caught.exception))

    def good(self):
        return build(4, 4, 6, pattern_rows(4, 4, 4))

    def test_not_a_png(self):
        self.refused(b"GIF89a" + b"\0" * 40, "signature")
        self.refused(b"", "signature")

    def test_a_bad_crc(self):
        data = bytearray(self.good())
        data[20] ^= 0xFF           # inside the IHDR payload
        self.refused(bytes(data), "CRC")

    def test_truncated_anywhere(self):
        data = self.good()
        for cut in (9, 20, 33, len(data) - 13, len(data) - 1):
            with self.subTest(cut=cut):
                with self.assertRaises(png.PngError):
                    png.decode(data[:cut])

    def test_no_iend(self):
        data = self.good()[:-12]
        self.refused(data, "truncated")

    def test_huge_dimensions_are_refused_before_allocating(self):
        header = struct.pack(">IIBBBBB", 100000, 100000, 8, 6, 0, 0, 0)
        data = png.SIGNATURE + chunk(b"IHDR", header) + chunk(b"IEND", b"")
        self.refused(data, "pixels")
        header = struct.pack(">IIBBBBB", 8000, 8000, 8, 6, 0, 0, 0)
        data = png.SIGNATURE + chunk(b"IHDR", header) + chunk(b"IEND", b"")
        self.refused(data, "pixels")

    def test_zero_dimensions(self):
        header = struct.pack(">IIBBBBB", 0, 5, 8, 6, 0, 0, 0)
        self.refused(png.SIGNATURE + chunk(b"IHDR", header)
                     + chunk(b"IEND", b""), "pixels")

    def test_interlaced_is_refused(self):
        self.refused(build(4, 4, 6, pattern_rows(4, 4, 4), interlace=1),
                     "interlac")

    def test_other_bit_depths_are_refused_by_name(self):
        self.refused(build(4, 4, 6, pattern_rows(4, 4, 4), depth=16), "depth 16")
        self.refused(build(4, 4, 3, pattern_rows(4, 4, 1), depth=4,
                           extra=[(b"PLTE", bytes(48))]), "depth 4")

    def test_a_decompression_bomb_is_stopped_at_the_declared_size(self):
        # 2 x 2 declared, 50 MB of zeros inside the deflate stream.
        header = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
        bomb = zlib.compress(bytes(50 * 1024 * 1024), 9)
        data = (png.SIGNATURE + chunk(b"IHDR", header)
                + chunk(b"IDAT", bomb) + chunk(b"IEND", b""))
        self.refused(data, "bomb")

    def test_a_stream_shorter_than_the_header_implies(self):
        header = struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0)
        data = (png.SIGNATURE + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(bytes(10)))
                + chunk(b"IEND", b""))
        self.refused(data, "truncated")

    def test_garbage_in_the_deflate_stream(self):
        header = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
        data = (png.SIGNATURE + chunk(b"IHDR", header)
                + chunk(b"IDAT", b"\x00\x01\x02not deflate")
                + chunk(b"IEND", b""))
        self.refused(data, "decompress")

    def test_a_file_above_the_byte_ceiling(self):
        self.refused(png.SIGNATURE + bytes(png.MAX_PNG_BYTES), "bytes")

    def test_a_palette_index_beyond_the_palette(self):
        self.refused(build(2, 1, 3, [bytes([0, 5])],
                           extra=[(b"PLTE", bytes([1, 2, 3]))]), "palette")

    def test_a_palette_image_with_no_palette(self):
        self.refused(build(2, 1, 3, [bytes([0, 0])]), "PLTE")

    def test_a_bad_filter_byte(self):
        header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        raw = bytes([9, 1, 2, 3, 4])
        data = (png.SIGNATURE + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
        self.refused(data, "filter type 9")

    def test_an_unknown_critical_chunk(self):
        rows = pattern_rows(2, 2, 4)
        self.refused(build(2, 2, 6, rows, extra=[(b"ZZZZ", b"")]), "critical")

    def test_more_chunks_than_any_real_file(self):
        rows = pattern_rows(2, 2, 4)
        filler = [(b"tEXt", b"")] * (png.MAX_PNG_CHUNKS + 1)
        self.refused(build(2, 2, 6, rows, extra=filler), "chunks")

    def test_the_first_chunk_must_be_ihdr(self):
        data = png.SIGNATURE + chunk(b"IEND", b"")
        self.refused(data, "IHDR")


class TestEncoding(unittest.TestCase):
    def test_rgba_round_trips(self):
        pixels = b"".join(pattern_rows(13, 7, 4))
        image = png.decode(png.encode(13, 7, pixels, 4))
        self.assertEqual((image.width, image.height), (13, 7))
        self.assertEqual(bytes(image.data), pixels)

    def test_rgb_round_trips_to_opaque_rgba(self):
        pixels = b"".join(pattern_rows(5, 3, 3))
        image = png.decode(png.encode(5, 3, pixels, 3))
        expected = b"".join(pixels[i:i + 3] + b"\xff"
                            for i in range(0, len(pixels), 3))
        self.assertEqual(bytes(image.data), expected)

    def test_output_has_a_valid_structure(self):
        data = png.encode(2, 2, bytes(16), 4)
        self.assertEqual(data[:8], png.SIGNATURE)
        kinds = [kind for kind, _ in png._chunks(data)]
        self.assertEqual(kinds, [b"IHDR", b"IDAT", b"IEND"])

    def test_the_wrong_buffer_size_is_refused(self):
        with self.assertRaises(png.PngError):
            png.encode(2, 2, bytes(15), 4)
        with self.assertRaises(png.PngError):
            png.encode(2, 2, bytes(16), 2)
        with self.assertRaises(png.PngError):
            png.encode(0, 2, b"", 4)


if __name__ == "__main__":
    unittest.main()
