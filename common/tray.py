"""pystray wrapper: one tray icon per app, built from a generated PNG.

Icons are drawn with Pillow rather than shipped as files so there's no asset to
lose, and so each app can tint its glyph from the shared palette.
"""

import threading

from PIL import Image, ImageDraw

from . import theme


def make_icon(glyph, color=theme.ACCENT, size=64):
    """Rounded square with a letter/symbol punched into it."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, size - 3, size - 3], radius=size // 5, fill=color)

    # Pillow's default bitmap font is tiny; scale a text layer up so the glyph
    # fills the badge without needing a TTF on disk.
    scale = 4
    layer = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    try:
        from PIL import ImageFont

        fnt = ImageFont.truetype("segoeuib.ttf", int(size * scale * 0.62))
    except (OSError, ImportError):
        fnt = None
    if fnt is not None:
        box = ld.textbbox((0, 0), glyph, font=fnt)
        ld.text(
            ((size * scale - box[2] + box[0]) / 2 - box[0],
             (size * scale - box[3] + box[1]) / 2 - box[1]),
            glyph, font=fnt, fill=(255, 255, 255, 255),
        )
        layer = layer.resize((size, size), Image.LANCZOS)
        img.alpha_composite(layer)
    return img


class Tray:
    """Tray icon running on its own thread.

    The pystray backend on Windows needs its own message loop, so run() blocks;
    call start() to get it on a daemon thread instead and keep the main thread
    free for Tk (which insists on being the main thread).
    """

    def __init__(self, app_name, title, glyph, color=theme.ACCENT):
        self.app_name = app_name
        self.title = title
        self._glyph = glyph
        self._color = color
        self._items = []
        self._icon = None
        self._thread = None

    def add(self, label, action, default=False, checked=None, enabled=True):
        """Queue a menu entry. Call before start()."""
        self._items.append(
            {"label": label, "action": action, "default": default,
             "checked": checked, "enabled": enabled}
        )
        return self

    def separator(self):
        self._items.append(None)
        return self

    def _build_menu(self):
        import pystray

        entries = []
        for it in self._items:
            if it is None:
                entries.append(pystray.Menu.SEPARATOR)
                continue
            entries.append(
                pystray.MenuItem(
                    it["label"],
                    (lambda fn: (lambda icon, item: fn()))(it["action"]),
                    default=it["default"],
                    checked=it["checked"],
                    enabled=it["enabled"],
                )
            )
        return pystray.Menu(*entries)

    def start(self):
        import pystray

        self._icon = pystray.Icon(
            self.app_name,
            icon=make_icon(self._glyph, self._color),
            title=self.title,
            menu=self._build_menu(),
        )
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        return self

    def notify(self, message, title=None):
        if self._icon is not None:
            try:
                self._icon.notify(message, title or self.title)
            except Exception:
                pass  # notifications are best-effort; never fatal

    def set_tooltip(self, text):
        if self._icon is not None:
            self._icon.title = text

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
