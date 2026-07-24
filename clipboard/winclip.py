"""Reading, writing and watching the Windows clipboard.

Three things here are worth explaining.

Change notification uses AddClipboardFormatListener + WM_CLIPBOARDUPDATE on a
message-only window instead of a polling loop. pywin32 does not wrap that call
so it comes from ctypes; the window class and message pump are pywin32. The
alternative APIs are worse: SetClipboardViewer builds a linked chain that any
badly-behaved app in it can break, and polling burns a wakeup several times a
second forever to notice nothing changed.

Opening the clipboard can simply fail. It is one global resource with a single
owner, and the app that just wrote to it often still holds it open for a few
milliseconds, so every access retries briefly rather than dropping the entry.

Applications that handle secrets mark their content with the
ExcludeClipboardContentFromMonitorProcessing / CanIncludeInClipboardHistory
formats. That is the only mechanism Windows offers for a source app to opt out
of clipboard managers, so it is checked before anything is read.
"""

import ctypes
import io
import struct
import threading
import time

import pywintypes
import win32api
import win32clipboard as wc
import win32con
import win32gui
import win32process
from PIL import Image

WM_CLIPBOARDUPDATE = 0x031D
HWND_MESSAGE = -3
_user32 = ctypes.windll.user32

# Registered clipboard formats are stable for the lifetime of the session and
# RegisterClipboardFormat is idempotent, so resolving them once at import is fine.
FMT_EXCLUDE = wc.RegisterClipboardFormat("ExcludeClipboardContentFromMonitorProcessing")
FMT_HISTORY = wc.RegisterClipboardFormat("CanIncludeInClipboardHistory")

THUMB_SIZE = (96, 96)
# Full text is stored, but only this much of it is kept as the searchable
# preview: search is a LIKE scan, and letting a multi-megabyte paste into that
# column makes every keystroke in the popup crawl.
PREVIEW_CHARS = 4096


class ClipboardBusy(Exception):
    pass


class _Clipboard:
    """Context manager that retries OpenClipboard for a short while."""

    def __init__(self, tries=8, delay=0.02):
        self.tries = tries
        self.delay = delay

    def __enter__(self):
        for attempt in range(self.tries):
            try:
                wc.OpenClipboard()
                return self
            except pywintypes.error:
                time.sleep(self.delay * (attempt + 1))
        raise ClipboardBusy("another app is holding the clipboard open")

    def __exit__(self, *exc):
        try:
            wc.CloseClipboard()
        except pywintypes.error:
            pass
        return False


def sequence():
    """Bumps on every clipboard write, by anyone. Used to spot our own writes."""
    return _user32.GetClipboardSequenceNumber()


def _is_excluded():
    """True if the source app asked clipboard managers to skip this content.

    ExcludeClipboardContentFromMonitorProcessing means "do not process at all".
    CanIncludeInClipboardHistory is a DWORD; zero means "keep out of history".
    Assumes the clipboard is already open.
    """
    if wc.IsClipboardFormatAvailable(FMT_EXCLUDE):
        return True
    if wc.IsClipboardFormatAvailable(FMT_HISTORY):
        try:
            raw = wc.GetClipboardData(FMT_HISTORY)
        except pywintypes.error:
            return True  # marked but unreadable: assume the app meant to opt out
        if isinstance(raw, bytes) and len(raw) >= 4:
            return struct.unpack("<I", raw[:4])[0] == 0
    return False


def _dib_to_image(dib):
    """CF_DIB is a BMP without its 14-byte file header; PIL reads that directly."""
    from PIL import BmpImagePlugin

    return BmpImagePlugin.DibImageFile(io.BytesIO(dib))


def _image_to_dib(img):
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 255, 255))
        flat.paste(img, mask=img.split()[-1])  # CF_DIB has no usable alpha
        img = flat
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    return buf.getvalue()[14:]


def read():
    """Current clipboard contents, or None if there's nothing we store.

    Returns {"kind", "content", "preview", "thumb"} where content is utf-8 bytes
    for text and PNG bytes for images.
    """
    with _Clipboard():
        if _is_excluded():
            return None
        if wc.IsClipboardFormatAvailable(wc.CF_UNICODETEXT):
            try:
                text = wc.GetClipboardData(wc.CF_UNICODETEXT)
            except pywintypes.error:
                return None
            if not text or not text.strip():
                return None
            return {
                "kind": "text",
                "content": text.encode("utf-8"),
                "preview": text[:PREVIEW_CHARS],
                "thumb": None,
            }
        if wc.IsClipboardFormatAvailable(wc.CF_DIB):
            try:
                dib = wc.GetClipboardData(wc.CF_DIB)
            except pywintypes.error:
                return None
            img = _dib_to_image(dib)
            full = io.BytesIO()
            img.convert("RGB").save(full, "PNG")
            thumb_img = img.convert("RGB").copy()
            thumb_img.thumbnail(THUMB_SIZE, Image.LANCZOS)
            thumb = io.BytesIO()
            thumb_img.save(thumb, "PNG")
            return {
                "kind": "image",
                "content": full.getvalue(),
                "preview": f"Image {img.width}x{img.height}",
                "thumb": thumb.getvalue(),
            }
    return None


def write_text(text):
    with _Clipboard():
        wc.EmptyClipboard()
        wc.SetClipboardText(text, wc.CF_UNICODETEXT)
    return sequence()


def write_image(png_bytes):
    dib = _image_to_dib(Image.open(io.BytesIO(png_bytes)))
    with _Clipboard():
        wc.EmptyClipboard()
        wc.SetClipboardData(wc.CF_DIB, dib)
    return sequence()


# -- foreground window ----------------------------------------------------
def foreground_window():
    return win32gui.GetForegroundWindow()


def foreground_process():
    """Lowercased exe name of whatever has focus, or '' if we can't tell.

    Elevated processes are unreadable from a normal-integrity process, which is
    the main hole in the exclude list.
    """
    try:
        import psutil

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return ""
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def focus_window(hwnd):
    """Give hwnd the foreground back before we synthesize a paste."""
    if not hwnd or not win32gui.IsWindow(hwnd):
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except pywintypes.error:
        pass
    # Windows only lets the process owning the foreground window hand focus
    # over. Once we've lost that right, briefly share the target thread's input
    # queue so SetForegroundWindow is allowed.
    ours = win32api.GetCurrentThreadId()
    theirs, _ = win32process.GetWindowThreadProcessId(hwnd)
    if theirs == ours:
        return False
    attached = _user32.AttachThreadInput(ours, theirs, True)
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except pywintypes.error:
        return False
    finally:
        if attached:
            _user32.AttachThreadInput(ours, theirs, False)


# -- change notification --------------------------------------------------
class Listener:
    """Message-only window that calls on_change() on every clipboard write.

    on_change runs on this object's own thread, so it must not touch Tk.
    """

    _class_atom = None

    def __init__(self, on_change, class_name="WinThingsClipListener"):
        self.on_change = on_change
        self.class_name = class_name
        self.hwnd = None
        self._thread = None
        self._ready = None

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_CLIPBOARDUPDATE:
            try:
                self.on_change()
            except Exception:
                pass  # a bad entry must never kill the listener thread
            return 0
        if msg == win32con.WM_CLOSE:
            win32gui.DestroyWindow(hwnd)
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    def _run(self):
        wndclass = win32gui.WNDCLASS()
        wndclass.lpszClassName = self.class_name
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = win32api.GetModuleHandle(None)
        try:
            atom = win32gui.RegisterClass(wndclass)
        except pywintypes.error:
            atom = self.class_name  # already registered in this process
        self._wndclass = wndclass  # the WNDPROC must outlive the window
        self.hwnd = win32gui.CreateWindowEx(
            0, atom, "clipboard-listener", 0, 0, 0, 0, 0,
            HWND_MESSAGE, 0, wndclass.hInstance, None,
        )
        _user32.AddClipboardFormatListener(self.hwnd)
        self._ready.set()
        win32gui.PumpMessages()

    def start(self):
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self

    def stop(self):
        if not self.hwnd:
            return
        _user32.RemoveClipboardFormatListener(self.hwnd)
        win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(2)
        self.hwnd = None
