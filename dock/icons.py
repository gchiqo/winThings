"""Getting a Pillow image out of whatever a window is willing to hand over.

There is no single API that returns an icon for an arbitrary window, so this
tries the cheap per-window sources first (WM_GETICON, then the window class's
icon) and falls back to pulling one out of the executable on disk. Anything
that still fails gets a generated letter tile rather than an exception —
elevated processes and some packaged apps simply will not answer.

Extraction is expensive enough (a DC, a bitmap and a blit each time) that
results are cached per executable and reused for every window of that app,
which is also what a taskbar wants visually.
"""

import ctypes
import os
from ctypes import wintypes

import win32con
import win32gui
import win32ui
from PIL import Image, ImageDraw, ImageFont

from common import theme

WM_GETICON = 0x007F
ICON_SMALL, ICON_BIG, ICON_SMALL2 = 0, 1, 2
GCL_HICON, GCL_HICONSM = -14, -34
SMTO_ABORTIFHUNG = 0x0002

# Icons are cached at this size and downscaled for display, so the slider can
# go up to 64px without re-extracting or looking soft.
NATIVE = 64

_user32 = ctypes.WinDLL("user32")
# Documented only as "not for general use" but exported since NT4; unlike
# ExtractIconEx it picks the best embedded image for an arbitrary size, which
# is what keeps a 64px dock from looking like an upscaled 32px icon.
_private_extract = getattr(_user32, "PrivateExtractIconsW", None)
if _private_extract is not None:
    _private_extract.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.UINT),
        wintypes.UINT, wintypes.UINT,
    ]
    _private_extract.restype = wintypes.UINT

_get_class_long = getattr(_user32, "GetClassLongPtrW", None) or _user32.GetClassLongW
_get_class_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_class_long.restype = ctypes.c_void_p

_cache = {}       # cache key -> PIL image at NATIVE size
_scaled = {}      # (cache key, size) -> PIL image
_font = None


def _letter_font(size):
    global _font
    if _font is None or _font.size != size:
        try:
            _font = ImageFont.truetype("segoeuib.ttf", size)
        except OSError:
            _font = ImageFont.load_default()
    return _font


def placeholder(label, size=NATIVE):
    """Generated tile for apps that won't give up an icon."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [1, 1, size - 2, size - 2], radius=size // 5,
        fill=theme.BG_HOVER, outline=theme.BORDER, width=max(1, size // 32),
    )
    ch = (label or "?").strip()[:1].upper() or "?"
    font = _letter_font(int(size * 0.5))
    box = draw.textbbox((0, 0), ch, font=font)
    draw.text(
        ((size - box[2] + box[0]) / 2 - box[0], (size - box[3] + box[1]) / 2 - box[1]),
        ch, font=font, fill=theme.FG_DIM,
    )
    return img


def hicon_to_image(hicon, size=NATIVE):
    """Blit an HICON into a 32-bit DIB and read it back as RGBA."""
    screen = win32gui.GetDC(0)
    dc = win32ui.CreateDCFromHandle(screen)
    mem = dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(dc, size, size)
    old = mem.SelectObject(bmp)
    try:
        mem.FillSolidRect((0, 0, size, size), 0)
        win32gui.DrawIconEx(
            mem.GetHandleOutput(), 0, 0, hicon, size, size, 0, None,
            win32con.DI_NORMAL,
        )
        img = Image.frombuffer(
            "RGBA", (size, size), bmp.GetBitmapBits(True), "raw", "BGRA", 0, 1
        )
        if img.getextrema()[3][1] == 0:
            # Pre-XP style icon with no alpha channel: rebuild transparency
            # from the AND mask, which DI_MASK paints white where see-through.
            mem.FillSolidRect((0, 0, size, size), 0)
            win32gui.DrawIconEx(
                mem.GetHandleOutput(), 0, 0, hicon, size, size, 0, None,
                win32con.DI_MASK,
            )
            mask = Image.frombuffer(
                "RGBA", (size, size), bmp.GetBitmapBits(True), "raw", "BGRA", 0, 1
            ).convert("L").point(lambda v: 0 if v > 127 else 255)
            img.putalpha(mask)
        return img
    finally:
        mem.SelectObject(old)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        dc.DeleteDC()
        win32gui.ReleaseDC(0, screen)


def _from_window(hwnd):
    """WM_GETICON, then the window class icon."""
    for which in (ICON_BIG, ICON_SMALL2, ICON_SMALL):
        try:
            handle = win32gui.SendMessageTimeout(
                hwnd, WM_GETICON, which, 0, SMTO_ABORTIFHUNG, 120
            )
        except win32gui.error:
            continue
        if isinstance(handle, tuple):
            handle = handle[-1]
        if handle:
            return int(handle)
    for index in (GCL_HICON, GCL_HICONSM):
        handle = _get_class_long(wintypes.HWND(hwnd), index)
        if handle:
            return int(handle)
    return 0


def _from_executable(path):
    """Best-resolution icon PrivateExtractIcons/ExtractIconEx will give us."""
    if _private_extract is not None:
        out = (wintypes.HICON * 1)()
        ids = (wintypes.UINT * 1)()
        if _private_extract(path, 0, NATIVE, NATIVE, out, ids, 1, 0) and out[0]:
            return int(out[0]), True
    try:
        large, small = win32gui.ExtractIconEx(path, 0, 1)
    except win32gui.error:
        return 0, False
    keep = large or small
    for unused in (small if large else []) + list(keep[1:]):
        win32gui.DestroyIcon(unused)
    return (int(keep[0]), True) if keep else (0, False)


def _extract(exe, hwnd, label):
    handle = _from_window(hwnd) if hwnd else 0
    owned = False
    if not handle and exe and os.path.exists(exe):
        handle, owned = _from_executable(exe)
    if not handle:
        return placeholder(label), False
    try:
        return hicon_to_image(handle), True
    except Exception:
        return placeholder(label), False
    finally:
        if owned:
            try:
                win32gui.DestroyIcon(handle)
            except win32gui.error:
                pass


def cache_key(exe, hwnd, name):
    """Per-executable, so every window of an app shares one extraction.

    Elevated processes hide their path; those fall back to the process name,
    which is still stable enough to cache against.
    """
    return (exe or "").lower() or f"proc:{name}"


def get(exe, hwnd, name, size, dim=False):
    """RGBA image of `size` px for an app. Never raises.

    dim fades the icon for pinned apps that aren't running, so the dot under
    the button isn't the only cue.
    """
    key = cache_key(exe, hwnd, name)
    scaled_key = (key, size, dim)
    if scaled_key in _scaled:
        return _scaled[scaled_key]
    if key not in _cache:
        try:
            image, _ = _extract(exe, hwnd, name)
        except Exception:
            image = placeholder(name)
        _cache[key] = image
    image = _cache[key]
    if image.size != (size, size):
        image = image.resize((size, size), Image.LANCZOS)
    if dim:
        image = image.copy()
        image.putalpha(image.getchannel("A").point(lambda v: v * 45 // 100))
    _scaled[scaled_key] = image
    return image


def resolved(exe, hwnd, name):
    """True if a real icon was found (used by the self-test, not the UI)."""
    key = cache_key(exe, hwnd, name)
    if key not in _cache:
        image, ok = _extract(exe, hwnd, name)
        _cache[key] = image
        return ok
    return True


def forget_scaled():
    """Drop resized copies after an icon-size change; keep the expensive originals."""
    _scaled.clear()
