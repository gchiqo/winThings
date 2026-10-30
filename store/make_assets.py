"""Generate all Microsoft Store icon assets — "Cursor + Voice" design.

Run from the repo root:
    python store/make_assets.py

Outputs every PNG the AppxManifest.xml references into store/Assets/.

Icon design: I-beam cursor (left) + speaker cone + three wave arcs (right),
cyan/indigo on deep navy. Clean, instantly readable at 44 px.

Required sizes (all from the MSIX packaging requirements):
  StoreLogo              50 × 50    (and ×2, ×4 scale variants)
  Square44x44Logo        44 × 44    (and ×1.25, ×1.5, ×2, ×4 scale variants)
  Square150x150Logo     150 × 150   (and ×1.25, ×1.5, ×2, ×4 scale variants)
  Wide310x150Logo       310 × 150   (badge + icon, both scaled)
  Square310x310Logo     310 × 310   (and ×1.25, ×1.5, ×2 scale variants)
  SplashScreen          620 × 300   (standard splash)
"""

import os
import sys
import math

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ASSETS_DIR = os.path.join(HERE, "Assets")
os.makedirs(ASSETS_DIR, exist_ok=True)

sys.path.insert(0, REPO)
from PIL import Image, ImageDraw

# Palette (matches the SVG design)
_NAVY   = (15,  23,  42, 255)   # #0f172a
_CYAN   = (56,  189, 248, 255)  # #38bdf8
_BLUE   = (96,  165, 250, 255)  # #60a5fa
_INDIGO = (129, 140, 248, 255)  # #818cf8
_DEEP   = (79,  70,  229, 255)  # #4f46e5
_WHITE  = (255, 255, 255, 180)


def _rr(d, x0, y0, x1, y1, r, fill):
    d.rounded_rectangle([x0, y0, x1, y1], radius=max(1, r), fill=fill)


def square(size: int) -> Image.Image:
    """Cursor + Voice icon at the requested pixel size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    s   = size / 160.0

    # Background — no pre-rounded corners; the OS/Store clips the shape.
    d.rectangle([0, 0, size - 1, size - 1], fill=_NAVY)

    # ── I-beam cursor (left half) ─────────────────────────────────────────
    bx = round(34 * s); by = round(34 * s)
    bw = max(2, round(8 * s)); bh = round(92 * s)
    sh = max(2, round(8 * s)); sw = round(36 * s)
    _rr(d, bx, by, bx + bw, by + bh, bw // 2, _CYAN)              # vertical
    _rr(d, bx - sw // 2, by, bx + bw + sw // 2, by + sh, sh // 2, _CYAN)  # top serif
    _rr(d, bx - sw // 2, by + bh - sh, bx + bw + sw // 2, by + bh, sh // 2, _CYAN)  # btm

    # ── Speaker (right half) ─────────────────────────────────────────────
    # Cone: a trapezoid opening right
    cx0, cy0 = round(72 * s), round(42 * s)
    cx1, cy1 = round(96 * s), round(118 * s)
    cone = [
        (round(72 * s), round(54 * s)),
        (round(72 * s), round(106 * s)),
        (round(96 * s), round(118 * s)),
        (round(96 * s), round(42 * s)),
    ]
    d.polygon(cone, fill=_CYAN)
    # Body rect
    _rr(d, round(60 * s), round(62 * s), round(74 * s), round(98 * s),
        round(3 * s), _CYAN)
    # Centre dot
    dr = max(2, round(5 * s))
    mx, my = round(67 * s), round(80 * s)
    d.ellipse([mx - dr, my - dr, mx + dr, my + dr], fill=_WHITE)

    # ── Sound waves ──────────────────────────────────────────────────────
    # Arcs centred at the speaker mouth (x=96, y=80), opening right.
    # PIL arc angles: 0=right, clockwise. 300→60 gives a 120° right-facing arc.
    wx, wy = round(96 * s), round(80 * s)
    for rad, color, lw in [
        (14, _CYAN,   max(2, round(4 * s))),
        (24, _INDIGO, max(2, round(3 * s))),
        (36, _DEEP,   max(1, round(3 * s))),
    ]:
        r_px = round(rad * s)
        bbox = [wx - r_px, wy - r_px, wx + r_px, wy + r_px]
        d.arc(bbox, start=300, end=60, fill=color, width=lw)

    return img



# ---------------------------------------------------------------------------
# Square icon variants
# ---------------------------------------------------------------------------

SQUARE_SPECS = {
    # (base_name, [(scale_label, pixel_size), ...])
    "StoreLogo": [
        ("scale-100", 50), ("scale-200", 100), ("scale-400", 200),
    ],
    "Square44x44Logo": [
        ("scale-100", 44), ("scale-125", 55), ("scale-150", 66),
        ("scale-200", 88), ("scale-400", 176),
    ],
    "Square150x150Logo": [
        ("scale-100", 150), ("scale-125", 188), ("scale-150", 225),
        ("scale-200", 300), ("scale-400", 600),
    ],
    "Square310x310Logo": [
        ("scale-100", 310), ("scale-125", 388), ("scale-150", 465),
        ("scale-200", 620),
    ],
}

for base, scales in SQUARE_SPECS.items():
    for label, px in scales:
        img = square(px)
        path = os.path.join(ASSETS_DIR, f"{base}.{label}.png")
        img.save(path)
        print(f"  {os.path.basename(path)}  ({px}×{px})")

    # Also save a bare (no scale suffix) copy at the smallest size for the
    # manifest reference — makeappx resolves scaled variants automatically.
    smallest_px = min(px for _, px in scales)
    square(smallest_px).save(os.path.join(ASSETS_DIR, f"{base}.png"))
    print(f"  {base}.png  ({smallest_px}×{smallest_px})  [manifest reference]")

# ---------------------------------------------------------------------------
# Wide310x150Logo  (navy bar, icon right, "Piper Read Aloud" text left)
# ---------------------------------------------------------------------------

def wide_tile(w=310, h=150):
    wide = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(wide)
    d.rectangle([0, 0, w - 1, h - 1], fill=_NAVY)

    icon = square(h - 20)
    ix = w - icon.width - 10
    iy = (h - icon.height) // 2
    wide.paste(icon, (ix, iy), icon)

    try:
        from PIL import ImageFont
        fnt_b = ImageFont.truetype("segoeuib.ttf", h // 6)
        fnt_r = ImageFont.truetype("segoeui.ttf",  h // 9)
        d.text((14, h // 4),       "Piper",       font=fnt_b, fill=_CYAN[:3] + (255,))
        d.text((14, h // 4 + h // 5 + 4), "Read Aloud", font=fnt_r, fill=(255, 255, 255, 180))
    except (OSError, ImportError):
        pass
    return wide

for label, (w, h) in [("scale-100", (310, 150)), ("scale-200", (620, 300))]:
    img = wide_tile(w, h)
    path = os.path.join(ASSETS_DIR, f"Wide310x150Logo.{label}.png")
    img.save(path)
    print(f"  {os.path.basename(path)}  ({w}×{h})")
wide_tile(310, 150).save(os.path.join(ASSETS_DIR, "Wide310x150Logo.png"))
print("  Wide310x150Logo.png  (310×150)  [manifest reference]")

# ---------------------------------------------------------------------------
# SplashScreen  (620×300, accent background, centred icon)
# ---------------------------------------------------------------------------

def splash(w=620, h=300):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], fill=_NAVY)
    icon = square(min(h - 40, 220))
    ix = (w - icon.width) // 2
    iy = (h - icon.height) // 2
    img.paste(icon, (ix, iy), icon)
    return img

splash().save(os.path.join(ASSETS_DIR, "SplashScreen.png"))
print("  SplashScreen.png  (620×300)")

print(f"\nAll assets written to {ASSETS_DIR}")
print("Now run  store\\build_msix.ps1  to create the .msix package.")
