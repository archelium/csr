"""QR code encoder (ISO/IEC 18004): byte mode, error correction level M, versions 1-10.

Stdlib only. Used for the phone link on the settings page. qr_svg() returns a plain
SVG with a 4-module quiet zone.
"""

# --- Reed-Solomon over GF(256), polynomial x^8 + x^4 + x^3 + x^2 + 1 ----------------

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(n):
    """Coefficients of (x - a^0)(x - a^1)...(x - a^(n-1)), highest power first."""
    g = [1]
    for i in range(n):
        nxt = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            nxt[j] ^= c
            nxt[j + 1] ^= _gf_mul(c, _EXP[i])
        g = nxt
    return g


def rs_ecc(data, n):
    """The n error correction codewords for one block of data codewords."""
    g = _generator(n)
    rem = [0] * n
    for d in data:
        factor = d ^ rem[0]
        rem = rem[1:] + [0]
        if factor:
            for j in range(n):
                rem[j] ^= _gf_mul(g[j + 1], factor)
    return rem


# --- tables, level M only ------------------------------------------------------------

# version: (ecc codewords per block, data codewords per block, short blocks first)
_BLOCKS = {
    1: (10, [16]),
    2: (16, [28]),
    3: (26, [44]),
    4: (18, [32, 32]),
    5: (24, [43, 43]),
    6: (16, [27] * 4),
    7: (18, [31] * 4),
    8: (22, [38, 38, 39, 39]),
    9: (22, [36, 36, 36, 37, 37]),
    10: (26, [43, 43, 43, 43, 44]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_ECC_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}

_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def format_bits(ecc, mask):
    """The 15 format bits for an error correction level and mask, MSB first."""
    data = _ECC_BITS[ecc] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return f"{(data << 10 | rem) ^ 0x5412:015b}"


def _version_bits(v):
    rem = v
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return v << 12 | rem


# --- encoding --------------------------------------------------------------------------

def _pick_version(n_bytes):
    for v in range(1, 11):
        cap = sum(_BLOCKS[v][1]) * 8
        if 4 + (8 if v < 10 else 16) + 8 * n_bytes <= cap:
            return v
    raise ValueError("Text too long for a version 10 QR code.")


def _codewords(raw, v):
    n_ecc, sizes = _BLOCKS[v]
    cap = sum(sizes) * 8
    bits = "0100" + format(len(raw), "08b" if v < 10 else "016b")
    bits += "".join(f"{b:08b}" for b in raw)
    bits += "0" * min(4, cap - len(bits))
    bits += "0" * (-len(bits) % 8)
    data = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(data) < sum(sizes):
        data.append(pad)
        pad ^= 0xEC ^ 0x11
    blocks, k = [], 0
    for n in sizes:
        blocks.append(data[k:k + n])
        k += n
    eccs = [rs_ecc(b, n_ecc) for b in blocks]
    out = []
    for i in range(max(sizes)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(n_ecc):
        out += [e[i] for e in eccs]
    return out


class _Symbol:
    def __init__(self, v):
        self.v = v
        self.size = n = 4 * v + 17
        self.m = [[False] * n for _ in range(n)]
        self.func = [[False] * n for _ in range(n)]

    def set(self, r, c, dark):
        self.m[r][c] = dark
        self.func[r][c] = True

    def draw_function_patterns(self):
        n = self.size
        for i in range(n):                      # timing patterns
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for r0, c0 in ((3, 3), (3, n - 4), (n - 4, 3)):     # finders + separators
            for dr in range(-4, 5):
                for dc in range(-4, 5):
                    r, c = r0 + dr, c0 + dc
                    if 0 <= r < n and 0 <= c < n:
                        d = max(abs(dr), abs(dc))
                        self.set(r, c, d not in (2, 4))
        a = _ALIGN[self.v]
        for r0 in a:
            for c0 in a:
                if (r0, c0) in ((6, 6), (6, a[-1]), (a[-1], 6)):
                    continue
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        self.set(r0 + dr, c0 + dc, max(abs(dr), abs(dc)) != 1)
        self.draw_format(0)                     # reserve the format areas
        if self.v >= 7:
            bits = _version_bits(self.v)
            for i in range(18):
                dark = bool(bits >> i & 1)
                self.set(i // 3, n - 11 + i % 3, dark)
                self.set(n - 11 + i % 3, i // 3, dark)

    def draw_format(self, mask):
        n = self.size
        bits = int(format_bits("M", mask), 2)

        def bit(i):
            return bool(bits >> i & 1)

        for i in range(6):
            self.set(i, 8, bit(i))
        self.set(7, 8, bit(6))
        self.set(8, 8, bit(7))
        self.set(8, 7, bit(8))
        for i in range(9, 15):
            self.set(8, 14 - i, bit(i))
        for i in range(8):
            self.set(8, n - 1 - i, bit(i))
        for i in range(8, 15):
            self.set(n - 15 + i, 8, bit(i))
        self.set(n - 8, 8, True)                # dark module

    def place_data(self, codewords):
        n = self.size
        total = len(codewords) * 8
        i = 0
        col, upward = n - 1, True
        while col > 0:
            if col == 6:                        # skip the vertical timing column
                col -= 1
            rows = range(n - 1, -1, -1) if upward else range(n)
            for r in rows:
                for c in (col, col - 1):
                    if self.func[r][c]:
                        continue
                    if i < total:
                        self.m[r][c] = bool(codewords[i >> 3] >> (7 - (i & 7)) & 1)
                    i += 1                      # remainder bits stay light
            col -= 2
            upward = not upward

    def apply_mask(self, mask):
        f = _MASKS[mask]
        for r in range(self.size):
            for c in range(self.size):
                if not self.func[r][c] and f(r, c):
                    self.m[r][c] = not self.m[r][c]


_FINDER_LIKE = ([True, False, True, True, True, False, True, False, False, False, False],
                [False, False, False, False, True, False, True, True, True, False, True])


def _penalty(m):
    n = len(m)
    score = 0
    lines = [row for row in m] + [[m[r][c] for r in range(n)] for c in range(n)]
    for line in lines:
        run, prev = 0, None                     # rule 1: runs of 5 or more
        for x in line:
            if x == prev:
                run += 1
            else:
                if run >= 5:
                    score += 3 + run - 5
                run, prev = 1, x
        if run >= 5:
            score += 3 + run - 5
        for i in range(n - 10):                 # rule 3: finder-like patterns
            seg = line[i:i + 11]
            if seg == _FINDER_LIKE[0] or seg == _FINDER_LIKE[1]:
                score += 40
    for r in range(n - 1):                      # rule 2: 2x2 blocks
        for c in range(n - 1):
            x = m[r][c]
            if x == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    dark = sum(sum(row) for row in m)           # rule 4: dark/light balance
    k = (abs(dark * 20 - n * n * 10) + n * n - 1) // (n * n) - 1
    score += max(k, 0) * 10
    return score


def qr_matrix(text):
    """The symbol as rows of booleans (True = dark), quiet zone not included."""
    raw = text.encode("utf-8")
    v = _pick_version(len(raw))
    cw = _codewords(raw, v)
    best, best_score = None, None
    for mask in range(8):
        s = _Symbol(v)
        s.draw_function_patterns()
        s.place_data(cw)
        s.apply_mask(mask)
        s.draw_format(mask)
        score = _penalty(s.m)
        if best_score is None or score < best_score:
            best, best_score = s, score
    return best.m


def qr_svg(text, scale=4):
    """An SVG of the QR code for `text`, with a 4-module quiet zone. Raises ValueError
    when the text needs more than version 10."""
    m = qr_matrix(text)
    w = (len(m) + 8) * scale
    parts = []
    for r, row in enumerate(m):
        for c, dark in enumerate(row):
            if dark:
                parts.append(f"M{(c + 4) * scale},{(r + 4) * scale}h{scale}v{scale}h-{scale}z")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {w}" '
            f'width="{w}" height="{w}" shape-rendering="crispEdges">'
            f'<rect width="{w}" height="{w}" fill="#fff"/>'
            f'<path fill="#000" d="{"".join(parts)}"/></svg>')
