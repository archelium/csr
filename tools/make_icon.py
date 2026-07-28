#!/usr/bin/env python3
"""Draw the CSR ship-chevron mark and save it as csr.ico (multi-size).

DEVELOPER TOOL — not part of the app, and not needed to build or run it. CSR itself
is standard-library only; this script is the one thing in the repo that needs a
package (Pillow), which is why it lives in tools/ rather than beside the source.
The generated csr.ico is committed, so a fresh clone builds an exe without ever
running this. Re-run it only to change the mark:

    pip install pillow
    python tools/make_icon.py           # writes csr.ico + csr_icon.png at the repo root

Mirrors the SVG mark (viewBox 0 0 90 110): a cyan upper chevron, a light lower
chevron, and a cyan drop stroke. At 16 px the lower chevron + stroke are dropped
for legibility.
"""
import os

from PIL import Image, ImageDraw

CYAN = (91, 209, 230, 255)      # #5bd1e6
LIGHT = (231, 237, 242, 217)    # #e7edf2 @ ~0.85

TOP = [(45, 4), (86, 58), (68, 58), (45, 30), (22, 58), (4, 58)]
LOW = [(45, 44), (70, 78), (52, 78), (45, 68), (38, 78), (20, 78)]
DROP = ((45, 90), (45, 104))

VB_W, VB_H = 90, 110


def render(size, supersample=4):
    """Render the mark centred on a transparent square canvas of `size`px."""
    S = size * supersample
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = 0.10                                  # 10% margin
    scale = (S * (1 - 2 * pad)) / VB_H
    off_x = (S - VB_W * scale) / 2
    off_y = (S - VB_H * scale) / 2

    def T(p):
        return (off_x + p[0] * scale, off_y + p[1] * scale)

    tiny = size <= 20
    d.polygon([T(p) for p in TOP], fill=CYAN)
    if not tiny:
        d.polygon([T(p) for p in LOW], fill=LIGHT)
        (x1, y1), (x2, y2) = T(DROP[0]), T(DROP[1])
        w = max(1, int(3 * scale))
        d.line([(x1, y1), (x2, y2)], fill=CYAN, width=w)
        rr = w / 2                              # round the stroke ends
        for (cx, cy) in ((x1, y1), (x2, y2)):
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=CYAN)

    return img.resize((size, size), Image.LANCZOS)


def main():
    # Always write to the repo root, not the working directory — this script lives in
    # tools/ but csr.spec expects csr.ico beside the source.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sizes = [256, 128, 64, 48, 32, 24, 16]     # largest first = base
    imgs = [render(s) for s in sizes]
    ico = os.path.join(root, "csr.ico")
    png = os.path.join(root, "csr_icon.png")
    # write every distinct-size render into the ICO (base + appended)
    imgs[0].save(ico, format="ICO", append_images=imgs[1:],
                 sizes=[(s, s) for s in sizes])
    render(256).save(png, format="PNG")
    print("wrote %s + %s (sizes: %s)" % (ico, png, sizes))


if __name__ == "__main__":
    main()
