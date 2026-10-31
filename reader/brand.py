"""The Read Selected icon: a text cursor beside a speaker with sound waves.

One drawing for every place the icon appears: the Store tiles
(store/make_assets.py), assets/reader.ico (exe and window icon) and the tray.
"""

from PIL import Image, ImageDraw

NAVY = (15, 23, 42, 255)       # #0f172a
CYAN = (56, 189, 248, 255)     # #38bdf8
INDIGO = (129, 140, 248, 255)  # #818cf8
DEEP = (79, 70, 229, 255)      # #4f46e5
DOT = (255, 255, 255, 180)


def _rr(d, x0, y0, x1, y1, r, fill):
    d.rounded_rectangle([x0, y0, x1, y1], radius=max(1, r), fill=fill)


def icon_image(size):
    """The icon as a square RGBA image; Windows rounds the corners itself."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 160.0
    d.rectangle([0, 0, size - 1, size - 1], fill=NAVY)

    # I-beam text cursor
    bx, by = round(34 * s), round(34 * s)
    bw, bh = max(2, round(8 * s)), round(92 * s)
    sh, sw = max(2, round(8 * s)), round(36 * s)
    _rr(d, bx, by, bx + bw, by + bh, bw // 2, CYAN)
    _rr(d, bx - sw // 2, by, bx + bw + sw // 2, by + sh, sh // 2, CYAN)
    _rr(d, bx - sw // 2, by + bh - sh, bx + bw + sw // 2, by + bh, sh // 2, CYAN)

    # Speaker cone and body
    d.polygon([(round(72 * s), round(54 * s)), (round(72 * s), round(106 * s)),
               (round(96 * s), round(118 * s)), (round(96 * s), round(42 * s))],
              fill=CYAN)
    _rr(d, round(60 * s), round(62 * s), round(74 * s), round(98 * s),
        round(3 * s), CYAN)
    dr = max(2, round(5 * s))
    mx, my = round(67 * s), round(80 * s)
    d.ellipse([mx - dr, my - dr, mx + dr, my + dr], fill=DOT)

    # Sound waves from the speaker mouth (PIL arcs run clockwise from 3 o'clock)
    wx, wy = round(96 * s), round(80 * s)
    for radius, color, width in ((14, CYAN, max(2, round(4 * s))),
                                 (24, INDIGO, max(2, round(3 * s))),
                                 (36, DEEP, max(1, round(3 * s)))):
        r = round(radius * s)
        d.arc([wx - r, wy - r, wx + r, wy + r], start=300, end=60,
              fill=color, width=width)
    return img
