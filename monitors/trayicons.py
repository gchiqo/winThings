"""Render a metric value into a notification-area icon.

Windows asks for a 16x16 icon and scales whatever it is given, so these are
drawn at 64x64 and downsampled — small text stays legible that way, where
drawing directly at 16x16 turns to mush.

The number is the reading; the bar behind it is the same value as a level, so
the icon is still readable at a glance when the digits are too small to parse.
"""

from PIL import Image, ImageDraw, ImageFont

SIZE = 64
_font_cache = {}


def _font(px):
    if px not in _font_cache:
        for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf"):
            try:
                _font_cache[px] = ImageFont.truetype(name, px)
                break
            except OSError:
                continue
        else:
            _font_cache[px] = ImageFont.load_default()
    return _font_cache[px]


def _hex_to_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def _fit(draw, text, limit):
    """Largest font size whose rendering of `text` fits inside `limit` px wide."""
    size = limit
    while size > 8:
        font = _font(size)
        box = draw.textbbox((0, 0), text, font=font)
        if box[2] - box[0] <= limit and box[3] - box[1] <= limit:
            return font
        size -= 2
    return _font(8)


def metric_icon(text, color, level=None):
    """Icon showing `text` in `color`, over a bar filled to `level` (0..1)."""
    rgb = _hex_to_rgb(color)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if level is not None:
        filled = max(0.0, min(1.0, level))
        top = int(SIZE * (1.0 - filled))
        draw.rounded_rectangle([0, 0, SIZE - 1, SIZE - 1], radius=12,
                               fill=rgb + (48,))
        if filled > 0:
            draw.rounded_rectangle([0, top, SIZE - 1, SIZE - 1], radius=12,
                                   fill=rgb + (110,))

    font = _fit(draw, text, SIZE - 6)
    box = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((SIZE - (box[2] - box[0])) / 2 - box[0],
         (SIZE - (box[3] - box[1])) / 2 - box[1]),
        text, font=font, fill=rgb + (255,),
    )
    return img


def percent_icon(value, color):
    """CPU/RAM/GPU: the integer percent, with a matching level bar."""
    if value is None:
        return metric_icon("--", color)
    return metric_icon(f"{value:.0f}", color, level=value / 100.0)


def rate_icon(bytes_per_sec, color, arrow):
    """Network throughput as a short magnitude, e.g. 12K.

    Direction is carried by colour and by a thin edge marker rather than a
    stacked arrow glyph: at the 16x16 Windows actually displays, an arrow costs
    a third of the height and leaves the digits unreadable.
    """
    value = max(0.0, bytes_per_sec)
    if value < 1024:
        text = f"{value:.0f}"
    elif value < 1024 ** 2:
        text = f"{value / 1024:.0f}K"
    elif value < 1024 ** 3:
        text = f"{value / 1024 ** 2:.1f}M"
    else:
        text = f"{value / 1024 ** 3:.1f}G"

    rgb = _hex_to_rgb(color)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bar = 6
    if arrow == "up":
        draw.rectangle([0, 0, SIZE - 1, bar], fill=rgb + (255,))
        text_top = bar + 2
    else:
        draw.rectangle([0, SIZE - 1 - bar, SIZE - 1, SIZE - 1], fill=rgb + (255,))
        text_top = 0

    font = _fit(draw, text, SIZE - 4)
    box = draw.textbbox((0, 0), text, font=font)
    height = SIZE - bar - 2
    draw.text(
        ((SIZE - (box[2] - box[0])) / 2 - box[0],
         text_top + (height - (box[3] - box[1])) / 2 - box[1]),
        text, font=font, fill=rgb + (255,),
    )
    return img
