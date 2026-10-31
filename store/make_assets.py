"""Generate the Microsoft Store icon assets and the app's .ico.

Run from the repo root:
    python store/make_assets.py

Writes every PNG the AppxManifest.xml references into store/Assets/, and
assets/reader.ico (the exe and window icon). The artwork is reader/brand.py's
icon_image(), so the Store tiles and the running app match.

Required sizes (all from the MSIX packaging requirements):
  StoreLogo              50 × 50    (and ×2, ×4 scale variants)
  Square44x44Logo        44 × 44    (and ×1.25, ×1.5, ×2, ×4 scale variants)
  Square150x150Logo     150 × 150   (and ×1.25, ×1.5, ×2, ×4 scale variants)
  Wide310x150Logo       310 × 150   (icon + name)
  Square310x310Logo     310 × 310   (and ×1.25, ×1.5, ×2 scale variants)
  SplashScreen          620 × 300   (standard splash)
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ASSETS_DIR = os.path.join(HERE, "Assets")
ICO_PATH = os.path.join(REPO, "assets", "reader.ico")
os.makedirs(ASSETS_DIR, exist_ok=True)

sys.path.insert(0, os.path.join(REPO, "reader"))
from PIL import Image, ImageDraw  # noqa: E402

from brand import CYAN, NAVY, icon_image  # noqa: E402

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
        path = os.path.join(ASSETS_DIR, f"{base}.{label}.png")
        icon_image(px).save(path)
        print(f"  {os.path.basename(path)}  ({px}×{px})")

    # Also a bare (no scale suffix) copy at the smallest size for the manifest
    # reference — makeappx resolves scaled variants automatically.
    smallest_px = min(px for _, px in scales)
    icon_image(smallest_px).save(os.path.join(ASSETS_DIR, f"{base}.png"))
    print(f"  {base}.png  ({smallest_px}×{smallest_px})  [manifest reference]")

# ---------------------------------------------------------------------------
# Wide310x150Logo  (navy bar, icon right, "Read Selected" text left)
# ---------------------------------------------------------------------------


def wide_tile(w=310, h=150):
    wide = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(wide)
    d.rectangle([0, 0, w - 1, h - 1], fill=NAVY)

    icon = icon_image(h - 20)
    wide.paste(icon, (w - icon.width - 10, (h - icon.height) // 2), icon)

    try:
        from PIL import ImageFont
        fnt_b = ImageFont.truetype("segoeuib.ttf", h // 6)
        fnt_r = ImageFont.truetype("segoeui.ttf", h // 9)
        d.text((14, h // 4), "Read", font=fnt_b, fill=CYAN)
        d.text((14, h // 4 + h // 5 + 4), "Selected", font=fnt_r,
               fill=(255, 255, 255, 180))
    except (OSError, ImportError):
        pass
    return wide


for label, (w, h) in [("scale-100", (310, 150)), ("scale-200", (620, 300))]:
    path = os.path.join(ASSETS_DIR, f"Wide310x150Logo.{label}.png")
    wide_tile(w, h).save(path)
    print(f"  {os.path.basename(path)}  ({w}×{h})")
wide_tile(310, 150).save(os.path.join(ASSETS_DIR, "Wide310x150Logo.png"))
print("  Wide310x150Logo.png  (310×150)  [manifest reference]")

# ---------------------------------------------------------------------------
# SplashScreen  (620×300, navy background, centred icon)
# ---------------------------------------------------------------------------


def splash(w=620, h=300):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle([0, 0, w - 1, h - 1], fill=NAVY)
    icon = icon_image(min(h - 40, 220))
    img.paste(icon, ((w - icon.width) // 2, (h - icon.height) // 2), icon)
    return img


splash().save(os.path.join(ASSETS_DIR, "SplashScreen.png"))
print("  SplashScreen.png  (620×300)")

# ---------------------------------------------------------------------------
# assets/reader.ico  (exe, window title bar and taskbar in dev runs)
# ---------------------------------------------------------------------------

icon_image(256).save(ICO_PATH, sizes=[(n, n) for n in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
print(f"  {ICO_PATH}  (16-256 px)")

print(f"\nAll assets written to {ASSETS_DIR}")
