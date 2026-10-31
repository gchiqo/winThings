"""pystray wrapper: one tray icon per app, drawn at runtime.

Icons are drawn with Pillow rather than shipped as files so there's no asset to
lose. Each app names an icon "kind" ("clipboard", "transcriber") and gets art
that says what it does, or passes its own image; an unknown kind falls back to
a lettered badge.

set_app_id() gives the process a real Windows application identity, so the tray
and its settings page label the icon by the app instead of "Python" (the
description of the pythonw.exe the tray actually runs under).
"""

import threading

from PIL import Image, ImageDraw

from . import theme

WHITE = (255, 255, 255, 255)
_HI_RES = 256  # draw art large, then downsample so it stays crisp at 16-32 px


def _hex(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def _badge(color):
    """Rounded, softly-shaded square in the app colour, at _HI_RES."""
    s = _HI_RES
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    top = _hex(color)
    bot = tuple(max(0, int(c * 0.78)) for c in top)
    grad = Image.new("RGBA", (1, s))
    for y in range(s):
        t = y / (s - 1)
        grad.putpixel((0, y), tuple(int(top[i] * (1 - t) + bot[i] * t)
                                    for i in range(3)) + (255,))
    grad = grad.resize((s, s))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle([6, 6, s - 7, s - 7],
                                           radius=s // 5, fill=255)
    img.paste(grad, (0, 0), mask)
    return img


def _draw_clipboard(img, color):
    """A clipboard with a search magnifier — 'searchable clipboard'."""
    d = ImageDraw.Draw(img)
    acc = _hex(color) + (255,)
    d.rounded_rectangle([70, 58, 186, 210], radius=16, fill=WHITE)
    d.rounded_rectangle([104, 40, 152, 74], radius=10, fill=WHITE)
    d.rounded_rectangle([112, 48, 144, 66], radius=6, fill=acc)
    for y in (100, 126):
        d.rounded_rectangle([88, y, 168, y + 10], radius=5, fill=acc)
    d.ellipse([116, 132, 168, 184], outline=acc, width=12)
    d.line([166, 182, 196, 212], fill=acc, width=16)
    d.ellipse([116, 132, 168, 184], outline=WHITE, width=4)


def _draw_transcriber(img, color):
    """A sound waveform turning into text lines — 'audio to text'."""
    d = ImageDraw.Draw(img)
    acc = _hex(color) + (255,)
    cy = _HI_RES // 2
    # waveform bars on the left
    heights = (28, 54, 78, 44, 62, 30)
    x = 48
    for h in heights:
        d.rounded_rectangle([x, cy - h, x + 10, cy + h], radius=5, fill=WHITE)
        x += 18
    # text lines on the right
    for i, w in enumerate((70, 54, 62)):
        y = cy - 30 + i * 30
        d.rounded_rectangle([166, y, 166 + w, y + 12], radius=6, fill=WHITE)


_ART = {"clipboard": _draw_clipboard, "transcriber": _draw_transcriber}


def _draw_letter(img, glyph):
    """Fallback: a single letter, for kinds without dedicated art."""
    d = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont

        fnt = ImageFont.truetype("segoeuib.ttf", int(_HI_RES * 0.6))
    except (OSError, ImportError):
        return
    box = d.textbbox((0, 0), glyph, font=fnt)
    d.text(((_HI_RES - box[2] + box[0]) / 2 - box[0],
            (_HI_RES - box[3] + box[1]) / 2 - box[1]),
           glyph, font=fnt, fill=WHITE)


def make_icon(kind, color=theme.ACCENT, size=64):
    """Badge art for `kind` ("reader"/"clipboard"), or a lettered fallback."""
    img = _badge(color)
    drawer = _ART.get(kind)
    if drawer is not None:
        drawer(img, color)
    else:
        _draw_letter(img, kind)
    return img.resize((size, size), Image.LANCZOS)


def set_app_id(app_id):
    """Give this process an explicit AppUserModelID so Windows labels the tray
    icon by the app, not by the pythonw.exe it runs under. Best-effort.

    Skipped inside an MSIX package: the package already has an identity, and
    overriding it makes the taskbar show the window icon instead of the
    package logo and breaks pinning."""
    from .autostart import in_msix_package

    if in_msix_package():
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(str(app_id))
    except Exception:
        pass


class Tray:
    """Tray icon running on its own thread.

    The pystray backend on Windows needs its own message loop, so run() blocks;
    call start() to get it on a daemon thread instead and keep the main thread
    free for Tk (which insists on being the main thread).
    """

    def __init__(self, app_name, title, kind, color=theme.ACCENT, image=None):
        self.app_name = app_name
        self.title = title
        self._kind = kind
        self._color = color
        self._image = image
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
            icon=(self._image.resize((64, 64), Image.LANCZOS) if self._image
                  else make_icon(self._kind, self._color)),
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
