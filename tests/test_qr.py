"""QR code encoder (sc_qr): RS, format bits, SVG output, symbol structure, round trip."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sc_qr  # noqa: E402


# --- a small independent reader, used to check the encoder's output -----------------

def _gf_tables():
    exp, log = [0] * 512, [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


EXP, LOG = _gf_tables()


def syndromes(block, n_ecc):
    """Evaluate the codeword polynomial at alpha^0..alpha^(n-1). All zero for a valid block."""
    out = []
    for i in range(n_ecc):
        s = 0
        for c in block:
            s = (EXP[LOG[s] + i] if s else 0) ^ c
        out.append(s)
    return out


ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
         7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
# version: (ecc per block, [data codewords per block]), level M
BLOCKS = {1: (10, [16]), 2: (16, [28]), 3: (26, [44]), 4: (18, [32, 32]),
          5: (24, [43, 43]), 6: (16, [27] * 4), 7: (18, [31] * 4),
          8: (22, [38, 38, 39, 39]), 9: (22, [36, 36, 36, 37, 37]),
          10: (26, [43, 43, 43, 43, 44])}
MASKS = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0,
         lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
         lambda r, c: (r // 2 + c // 3) % 2 == 0,
         lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
         lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
         lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]


def function_mask(v):
    size = 4 * v + 17
    f = [[False] * size for _ in range(size)]

    def box(r0, c0, h, w):
        for r in range(max(r0, 0), min(r0 + h, size)):
            for c in range(max(c0, 0), min(c0 + w, size)):
                f[r][c] = True

    box(0, 0, 9, 9)
    box(0, size - 8, 9, 8)
    box(size - 8, 0, 8, 9)
    box(6, 0, 1, size)
    box(0, 6, size, 1)
    a = ALIGN[v]
    for r in a:
        for c in a:
            if (r, c) in ((6, 6), (6, a[-1]), (a[-1], 6)):
                continue
            box(r - 2, c - 2, 5, 5)
    if v >= 7:
        box(0, size - 11, 6, 3)
        box(size - 11, 0, 3, 6)
    return f


def read_symbol(m):
    """Decode a byte-mode level-M symbol. Returns (text, version, mask)."""
    size = len(m)
    v = (size - 17) // 4
    # format info, first copy: bits 14..9 along row 8, 8..0 up column 8
    seq = [m[8][c] for c in (0, 1, 2, 3, 4, 5, 7)] + [m[8][8], m[7][8]] + \
          [m[r][8] for r in (5, 4, 3, 2, 1, 0)]
    fmt1 = "".join("1" if b else "0" for b in seq)
    seq2 = [m[r][8] for r in range(size - 1, size - 8, -1)] + \
           [m[8][c] for c in range(size - 8, size)]
    fmt2 = "".join("1" if b else "0" for b in seq2)
    assert fmt1 == fmt2, (fmt1, fmt2)
    mask = next(k for k in range(8) if sc_qr.format_bits("M", k) == fmt1)
    func = function_mask(v)
    bits = []
    col, upward = size - 1, True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for c in (col, col - 1):
                if not func[r][c]:
                    bits.append(m[r][c] ^ MASKS[mask](r, c))
        col -= 2
        upward = not upward
    cw = [int("".join("1" if b else "0" for b in bits[i:i + 8]), 2)
          for i in range(0, len(bits) // 8 * 8, 8)]
    n_ecc, sizes = BLOCKS[v]
    blocks = [[] for _ in sizes]
    k = 0
    for i in range(max(sizes)):
        for b, n in enumerate(sizes):
            if i < n:
                blocks[b].append(cw[k])
                k += 1
    for i in range(n_ecc):
        for b in range(len(sizes)):
            blocks[b].append(cw[k])
            k += 1
    for blk in blocks:
        assert syndromes(blk, n_ecc) == [0] * n_ecc, "RS check failed"
    data = [c for blk, n in zip(blocks, sizes) for c in blk[:n]]
    bitstr = "".join(f"{c:08b}" for c in data)
    assert bitstr[:4] == "0100", bitstr[:4]
    nlen = 8 if v < 10 else 16
    count = int(bitstr[4:4 + nlen], 2)
    p = 4 + nlen
    raw = bytes(int(bitstr[p + 8 * i:p + 8 * i + 8], 2) for i in range(count))
    return raw.decode("utf-8"), v, mask


def svg_matrix(svg, scale):
    """Rebuild the module grid (quiet zone included) from the SVG's path."""
    w = int(re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).group(1))
    n = w // scale
    grid = [[False] * n for _ in range(n)]
    for x, y in re.findall(r"M(\d+),(\d+)h", svg):
        grid[int(y) // scale][int(x) // scale] = True
    return grid


class QRTests(unittest.TestCase):
    def test_rs_hello_world(self):
        self.assertEqual(
            sc_qr.rs_ecc([32, 91, 11, 120, 209, 114, 220, 77, 67, 64, 236, 17, 236, 17, 236, 17], 10),
            [196, 35, 39, 119, 235, 215, 231, 226, 93, 23])

    def test_format_bits(self):
        self.assertEqual(sc_qr.format_bits("M", 0), "101010000010010")
        self.assertEqual(sc_qr.format_bits("L", 4), "110011000101111")

    def test_svg_size(self):
        svg = sc_qr.qr_svg("http://192.168.100.100:7879/live?k=" + "x" * 22)
        self.assertTrue(svg.startswith("<svg"))
        w, h = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg).groups()
        self.assertEqual(w, h)
        self.assertIn(int(w), [(4 * v + 17 + 8) * 4 for v in range(1, 11)])

    def test_structure(self):
        m = sc_qr.qr_matrix("hi")
        size = len(m)
        v = (size - 17) // 4
        self.assertEqual(v, 1)
        finder = [[r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)
                   for c in range(7)] for r in range(7)]
        for r0, c0 in ((0, 0), (0, size - 7), (size - 7, 0)):
            for r in range(7):
                for c in range(7):
                    self.assertEqual(m[r0 + r][c0 + c], finder[r][c], (r0, c0, r, c))
        # separators are light
        for i in range(8):
            self.assertFalse(m[7][i])
            self.assertFalse(m[i][7])
            self.assertFalse(m[7][size - 1 - i])
            self.assertFalse(m[i][size - 8])
            self.assertFalse(m[size - 8][i])
            self.assertFalse(m[size - 1 - i][7])
        for i in range(8, size - 8):
            self.assertEqual(m[6][i], i % 2 == 0, ("row timing", i))
            self.assertEqual(m[i][6], i % 2 == 0, ("col timing", i))
        self.assertTrue(m[4 * v + 9][8])          # dark module

    def test_alignment_and_version_info(self):
        text = "x" * 150                           # needs version 7 or more
        m = sc_qr.qr_matrix(text)
        size = len(m)
        v = (size - 17) // 4
        self.assertGreaterEqual(v, 7)
        a = ALIGN[v]
        for r in a:
            for c in a:
                if (r, c) in ((6, 6), (6, a[-1]), (a[-1], 6)):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        want = max(abs(dr), abs(dc)) != 1
                        self.assertEqual(m[r + dr][c + dc], want, (r, c, dr, dc))
        # version info: 18 bits, bit i at (i // 3, size - 11 + i % 3) and transposed
        rem = v
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = v << 12 | rem
        for i in range(18):
            want = bool(bits >> i & 1)
            self.assertEqual(m[i // 3][size - 11 + i % 3], want, i)
            self.assertEqual(m[size - 11 + i % 3][i // 3], want, i)

    def test_round_trip_all_versions(self):
        seen = set()
        for n in (1, 10, 20, 30, 50, 70, 90, 110, 140, 170, 200, 213):
            text = "".join(chr(ord("a") + (i * 7) % 26) for i in range(n))
            m = sc_qr.qr_matrix(text)
            got, v, _mask = read_symbol(m)
            self.assertEqual(got, text)
            seen.add(v)
        self.assertEqual(seen, set(range(1, 11)))

    def test_round_trip_url(self):
        url = "http://192.168.1.23:7890/live?k=AbCdEfGhIjKlMnOpQrStUv"
        got, _v, _m = read_symbol(sc_qr.qr_matrix(url))
        self.assertEqual(got, url)

    def test_svg_matches_matrix(self):
        text = "http://10.0.0.5:7890/live?k=abc"
        m = sc_qr.qr_matrix(text)
        for scale in (1, 3):
            g = svg_matrix(sc_qr.qr_svg(text, scale=scale), scale)
            self.assertEqual(len(g), len(m) + 8)
            for r in range(len(g)):
                for c in range(len(g)):
                    inner = 4 <= r < len(m) + 4 and 4 <= c < len(m) + 4
                    want = m[r - 4][c - 4] if inner else False
                    self.assertEqual(g[r][c], want, (scale, r, c))

    def test_masks_vary(self):
        masks = {read_symbol(sc_qr.qr_matrix("t" * n + str(n)))[2] for n in range(1, 40)}
        self.assertGreater(len(masks), 1)

    def test_capacity_matches_standard(self):
        total = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134, 6: 172, 7: 196, 8: 242, 9: 292, 10: 346}
        remainder = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}
        for v in range(1, 11):
            free = sum(row.count(False) for row in function_mask(v))
            self.assertEqual(free, total[v] * 8 + remainder[v], v)
            n_ecc, sizes = BLOCKS[v]
            self.assertEqual(sum(sizes) + n_ecc * len(sizes), total[v], v)

    def test_too_long(self):
        with self.assertRaises(ValueError):
            sc_qr.qr_svg("x" * 214)


if __name__ == "__main__":
    unittest.main()
