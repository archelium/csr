#!/usr/bin/env python3
"""Draw the GitHub social-preview card and save it as docs/img/social-preview.png.

DEVELOPER TOOL — not part of the app. Like tools/make_icon.py it needs Pillow, which
CSR itself never does (the app is standard-library only). The generated PNG is
committed, so nobody has to run this:

    pip install pillow
    python tools/make_social.py

GitHub shows this image on the repo card and on EVERY release link, which is what
Discord, Slack and X unfurl. Without one, GitHub auto-generates a card built around
the account avatar. Upload it at Settings -> General -> Social preview.

1280x640 is GitHub's recommended size (2:1; 640x320 minimum). Unfurls get cropped and
scaled hard, so everything important stays inside a ~90 px margin and nothing relies
on small text. Mark geometry and colours mirror the in-app SVG (viewBox 0 0 90 110)
so the card, the favicon and csr.ico stay the same drawing.
"""
import os

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640
BG = (7, 11, 17)                # --bg    #070b11
CYAN = (91, 209, 230)           # --cyan  #5bd1e6
TXT = (219, 231, 244)           # --txt   #dbe7f4
MUTED = (143, 167, 189)         # --muted #8fa7bd
DIM = (100, 122, 144)           # --dim   #647a90
GLOW = (20, 40, 58)             # --glow1 #14283a

# the mark, in its 90x110 viewBox — identical to the favicon and csr.ico
TOP = [(45, 4), (86, 58), (68, 58), (45, 30), (22, 58), (4, 58)]
LOW = [(45, 44), (70, 78), (52, 78), (45, 68), (38, 78), (20, 78)]
DROP = ((45, 90), (45, 104))
VB_W, VB_H = 90, 110

FONTS = r"C:\Windows\Fonts"


def _font(name, size):
    """Bahnschrift stands in for Chakra Petch and Consolas for Share Tech Mono: the
    real faces ship as woff2 inside sc_fonts_embed.css, which Pillow cannot read."""
    try:
        return ImageFont.truetype(os.path.join(FONTS, name), size)
    except OSError:
        return ImageFont.load_default()


def tracked(d, xy, text, font, fill, track=0):
    """Draw text with letter-spacing (Pillow has no tracking) and return its width."""
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font) + track
    return x - xy[0] - track


def measure(d, text, font, track=0):
    return sum(d.textlength(c, font=font) for c in text) + track * (len(text) - 1)


def main():
    img = Image.new("RGB", (W, H), BG)

    # soft radial glow behind the mark — drawn as concentric ellipses on a scratch
    # layer, cheaper than a real gradient and invisible at card scale either way
    glow = Image.new("RGB", (W, H), BG)
    gd = ImageDraw.Draw(glow)
    cx, cy = 330, H // 2
    for i in range(60, 0, -1):
        r = i * 9
        t = i / 60.0
        gd.ellipse([cx - r, cy - r, cx + r, cy + r],
                   fill=tuple(int(BG[c] + (GLOW[c] - BG[c]) * (1 - t) * 0.5) for c in range(3)))
    img = Image.blend(img, glow, 0.85)

    d = ImageDraw.Draw(img)

    # faint HUD grid, matching --hud-grid in the dashboard
    grid = tuple(int(BG[c] + (CYAN[c] - BG[c]) * 0.05) for c in range(3))
    for x in range(0, W, 48):
        d.line([(x, 0), (x, H)], fill=grid)
    for y in range(0, H, 48):
        d.line([(0, y), (W, y)], fill=grid)

    # --- the mark -----------------------------------------------------------
    s = 3.1                                    # 90x110 -> ~279x341
    ox, oy = cx - VB_W * s / 2, cy - VB_H * s / 2
    pt = lambda p: (ox + p[0] * s, oy + p[1] * s)
    d.polygon([pt(p) for p in TOP], fill=CYAN)
    d.polygon([pt(p) for p in LOW], fill=(198, 210, 222))
    d.line([pt(DROP[0]), pt(DROP[1])], fill=CYAN, width=int(3 * s))

    # --- wordmark -----------------------------------------------------------
    x = 610
    f_csr = _font("bahnschrift.ttf", 168)
    f_sub = _font("consola.ttf", 31)
    f_tag = _font("segoeui.ttf", 34)
    f_foot = _font("consola.ttf", 23)

    d.text((x, 150), "CSR", font=f_csr, fill=TXT)
    tracked(d, (x + 6, 330), "CITIZEN SERVICE RECORD", f_sub, CYAN, track=2.4)

    d.line([(x + 6, 386), (x + 6 + 470, 386)], fill=(29, 44, 61), width=2)

    d.text((x + 6, 410), "Your Star Citizen career,", font=f_tag, fill=MUTED)
    d.text((x + 6, 452), "read from the game's own logs.", font=f_tag, fill=MUTED)

    tracked(d, (x + 6, 520), "WINDOWS  ·  NO INSTALL  ·  FREE", f_foot, DIM, track=1.6)

    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "img", "social-preview.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    img.save(out, "PNG", optimize=True)
    print("wrote %s  (%dx%d, %.0f KB)"
          % (out, W, H, os.path.getsize(out) / 1024))


if __name__ == "__main__":
    main()
