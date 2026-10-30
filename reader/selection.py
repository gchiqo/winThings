"""Get "whatever text is selected right now", the Windows way.

X11 has a PRIMARY selection that always holds the current highlight, so the
Linux version just read it. Windows has no such thing. Two routes, in order:

  1. UI Automation. Ask the focused element for its TextPattern and read the
     selection ranges straight out of the app. No clipboard, no keystrokes, no
     modifier juggling, nothing for a clipboard-history tool to record. Works
     wherever the app implements TextPattern (see reader/README.md for which
     do). The calls are cross-process COM, so they run on a dedicated worker
     thread with UIA's own timeouts set low and a hard deadline on top: a hung
     app costs ~250 ms and a fallback, never a frozen hotkey.
  2. Synthesize Ctrl+C and look at the clipboard. Works in nearly everything,
     but is destructive, so we snapshot the clipboard first and put it back.

capture() tries 1, then 2, and logs which one answered for which app.

Two Windows specifics the Ctrl+C route has to work around:

  * The hotkey fires on key-down, so the user is still holding Shift (and maybe
    Alt/Win) when we send Ctrl+C — the focused app would see Ctrl+Shift+C, not
    Ctrl+C. So we wait for the modifiers to come up, and force them up if the
    user is leaning on the keys.
  * The clipboard is a lock other processes hold for short bursts, so every open
    is retried. And GetClipboardSequenceNumber is the only reliable "did
    anything actually get copied" signal: comparing the text before and after
    can't tell "nothing was selected" from "the selection equals what was
    already on the clipboard".

The restore puts back every format that is plain data (text, HTML, RTF, images,
file lists, and most app-registered formats), marked so that clipboard-history
tools don't record it. What can't come back, and why, is noted at
HGLOBAL_FORMATS and SKIP_FORMAT_NAMES.
"""

import collections
import ctypes
import logging
import os
import queue
import sys
import threading
import time

import win32api
import win32clipboard
import win32con
import win32gui
import win32process

import bootstrap  # noqa: F401  (repo root on sys.path, for common.hotkey)

log = logging.getLogger("reader.selection")

# Ctrl is deliberately absent: we are about to send Ctrl+C, so the user still
# holding it does no harm. Every one of these, though, turns our Ctrl+C into
# Ctrl+<something>+C in the target app.
MODIFIER_KEYS = (win32con.VK_LSHIFT, win32con.VK_RSHIFT,
                 win32con.VK_LMENU, win32con.VK_RMENU,
                 win32con.VK_LWIN, win32con.VK_RWIN)

# In a terminal with nothing selected, Ctrl+C interrupts the running program, so
# the Ctrl+C fallback is never sent to one. UI Automation still reads a selection
# in conhost and Windows Terminal.
TERMINAL_WINDOW_CLASSES = {
    "ConsoleWindowClass",             # conhost: cmd, PowerShell
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
    "mintty",                         # Git Bash, MSYS2, Cygwin
    "VirtualConsoleClass",            # ConEmu, Cmder
    "PuTTY", "KiTTY",
}
XTERM_JS_CLASS = "xterm-helper-textarea"  # terminals in VS Code, Hyper, Tabby


def _open(retries=12, delay=0.02):
    """Open the clipboard, retrying while another process owns it."""
    for _ in range(retries):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(delay)
    return False


def get_text():
    """Clipboard text, or None if the clipboard holds no text at all."""
    if not _open():
        return None
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return None
        return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except Exception:
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def set_text(text):
    if not _open():
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        return True
    except Exception:
        return False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def sequence():
    """Bumped by Windows on every clipboard change, by anyone."""
    try:
        return win32clipboard.GetClipboardSequenceNumber()
    except Exception:
        return None


def modifiers_down():
    """Which of Shift/Alt/Win are physically down right now.

    GetAsyncKeyState asks the OS directly, which is the only trustworthy source:
    no keyboard hook is installed anywhere in this suite any more.
    """
    return [vk for vk in MODIFIER_KEYS
            if win32api.GetAsyncKeyState(vk) & 0x8000]


def release_modifiers(timeout=0.5, poll=0.02):
    """Wait for Shift/Alt/Win to come up so our Ctrl+C isn't Ctrl+Shift+C.

    The hotkey fires on key-down, so this is the normal case, not an edge case.
    Returns True if the user let go on their own.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not modifiers_down():
            return True
        time.sleep(poll)

    for vk in modifiers_down():  # they're leaning on it; let go on their behalf
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    # The synthetic key-ups have to land in the target's input queue before our
    # Ctrl+C does, or it still sees Ctrl+Shift+C.
    time.sleep(0.06)
    return False


def capture(timeout=0.6, settle=0.05, wait_for_release=True, use_uia=True):
    """Return the selected text, or "" if nothing was selected.

    UI Automation first; the Ctrl+C route only if that came back empty for any
    reason (unsupported element, empty selection, timeout, error).
    """
    t0 = time.perf_counter()
    app = _foreground_exe()
    if use_uia:
        text, reason = uia_selection()
        if text:
            log.info("selection via uia (%s) app=%s chars=%d %.0fms",
                     reason, app, len(text), (time.perf_counter() - t0) * 1000)
            return text
    else:
        reason = "disabled"
    if reason.startswith("terminal") or _terminal_window():
        log.info("selection: no Ctrl+C in a terminal (uia: %s) app=%s", reason, app)
        return ""
    text = _capture_clipboard(timeout, settle, wait_for_release)
    log.info("selection via clipboard (uia: %s) app=%s chars=%d %.0fms",
             reason, app, len(text), (time.perf_counter() - t0) * 1000)
    return text


def _terminal_window():
    try:
        return win32gui.GetClassName(win32gui.GetForegroundWindow()) in TERMINAL_WINDOW_CLASSES
    except Exception:
        return False


def _foreground_exe():
    """Process name of the foreground window, for the log line."""
    try:
        import psutil

        pid = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())[1]
        return psutil.Process(pid).name()
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# UI Automation
# ---------------------------------------------------------------------------
# comtypes against UIAutomationCore.dll directly rather than the `uiautomation`
# package: we need five calls, and that package wraps all of UIA, initialises
# COM on whatever thread imports it, and offers no per-call timeout. Going
# direct keeps the COM apartment and the timeouts under our control.

UIA_DEADLINE = 0.25       # seconds the caller waits for an answer
UIA_COM_TIMEOUT_MS = 150  # IUIAutomation2 connection/transaction timeouts
UIA_INIT_WAIT = 2.0       # first call only: comtypes import + CUIAutomation8
UIA_MAX_ANCESTORS = 5
# Chromium (Chrome, Edge, Electron apps like VS Code) and Gecko build their
# accessibility tree lazily, on the first UIA request to that page or process:
# that first answer is "no TextPattern" or an empty selection, and the real one
# turns up later — ~10-30 ms for a fresh Chrome page, ~130 ms for Edge's PDF
# viewer (measured). So for those frameworks only, and only on the first read
# from a given window, keep retrying for a short budget before giving up on
# UIA. Warm reads never pay for this.
UIA_LAZY_FRAMEWORKS = {"Chrome", "Gecko"}
UIA_RETRY_BUDGET = 0.15
UIA_RETRY_GAP = 0.03
UIA_E_TIMEOUT = -2146233083            # 0x80131505
UIA_E_ELEMENTNOTAVAILABLE = -2147220991  # 0x80040201


class _UiaWorker:
    """One long-lived MTA thread that owns the IUIAutomation object.

    UIA calls into the target process and blocks until it answers, so they are
    never made on the caller's thread. If a call outlives its deadline the
    caller falls back; the worker stays marked busy until the stuck call
    returns (bounded by the COM timeouts), and calls in the meantime skip UIA
    rather than queue up behind it.
    """

    def __init__(self):
        self._jobs = queue.Queue()
        self._ready = threading.Event()
        self._busy = threading.Event()
        self._lock = threading.Lock()
        self._thread = None
        self._error = None  # set if comtypes/UIA could not be loaded at all

    def start(self):
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="uia",
                                                daemon=True)
                self._thread.start()

    def query(self, deadline=UIA_DEADLINE):
        self.start()
        if not self._ready.wait(UIA_INIT_WAIT):
            return "", "init"
        if self._error:
            return "", self._error
        reply = queue.Queue(maxsize=1)
        with self._lock:  # hotkey and tray can both ask at once
            if self._busy.is_set():
                return "", "busy"
            self._busy.set()
            self._jobs.put(reply)
        try:
            return reply.get(timeout=deadline)
        except queue.Empty:
            return "", "timeout"

    def _run(self):
        try:
            auto, uia = self._load()
        except Exception as exc:
            log.warning("UI Automation unavailable, Ctrl+C only: %r", exc)
            self._error = f"unavailable:{type(exc).__name__}"
            self._ready.set()
            return
        self._ready.set()
        while True:
            reply = self._jobs.get()
            try:
                result = _read_with_retry(auto, uia)
            finally:
                self._busy.clear()
            try:
                reply.put_nowait(result)
            except queue.Full:
                pass

    @staticmethod
    def _load():
        # comtypes initialises COM for the importing thread at import time, as
        # STA unless sys.coinit_flags says otherwise. UIA clients belong in the
        # MTA, and nothing else in the reader uses comtypes.
        logging.getLogger("comtypes").setLevel(logging.WARNING)  # cache chatter
        had_flags = hasattr(sys, "coinit_flags")
        if not had_flags:
            sys.coinit_flags = 0  # COINIT_MULTITHREADED
        try:
            import comtypes
            import comtypes.client
        finally:
            if not had_flags:
                del sys.coinit_flags
        if getattr(sys, "frozen", False):
            # Frozen, comtypes adds %TEMP%\comtypes_cache\... to comtypes.gen's
            # search path, so a .py planted there would be imported. The UIA
            # wrappers are bundled (reader.spec); anything else stays in memory.
            import comtypes.gen
            comtypes.gen.__path__ = [p for p in comtypes.gen.__path__
                                     if "comtypes_cache" not in p]
            comtypes.client.gen_dir = None
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except OSError:
            pass  # already initialised on this thread by the import
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia

        auto = comtypes.client.CreateObject(uia.CUIAutomation8,
                                            interface=uia.IUIAutomation2)
        auto.ConnectionTimeout = UIA_COM_TIMEOUT_MS
        auto.TransactionTimeout = UIA_COM_TIMEOUT_MS
        return auto, uia


def _com_reason(exc):
    hresult = getattr(exc, "hresult", None)
    if hresult is None and getattr(exc, "args", None):
        hresult = exc.args[0] if isinstance(exc.args[0], int) else None
    if hresult == UIA_E_TIMEOUT:
        return "timeout"
    if hresult == UIA_E_ELEMENTNOTAVAILABLE:
        return "no-element"
    return f"error:{type(exc).__name__}"


def _text_pattern(element, uia):
    """The element's TextPattern (or TextPattern2), or None."""
    for pattern_id, iface in ((uia.UIA_TextPatternId, uia.IUIAutomationTextPattern),
                              (uia.UIA_TextPattern2Id, uia.IUIAutomationTextPattern2)):
        unknown = element.GetCurrentPattern(pattern_id)
        if unknown:
            return unknown.QueryInterface(iface)
    return None


_seen_windows = collections.OrderedDict()  # top-level hwnds already queried


def _first_visit():
    """True the first time we query this foreground window.

    Only a window's first query can hit an unbuilt accessibility tree; after
    that an empty answer means an empty selection, and retrying would just add
    the whole budget to every "nothing selected" read.
    """
    hwnd = win32gui.GetForegroundWindow()
    if hwnd in _seen_windows:
        _seen_windows.move_to_end(hwnd)
        return False
    _seen_windows[hwnd] = True
    if len(_seen_windows) > 64:
        _seen_windows.popitem(last=False)
    return True


def _read_with_retry(auto, uia):
    """Runs on the worker thread. Returns (text, reason)."""
    budget = UIA_RETRY_BUDGET if _first_visit() else 0
    give_up = time.monotonic() + budget
    attempt = 0
    while True:
        framework = None
        try:
            text, reason, framework = _read_selection(auto, uia)
        except Exception as exc:
            text, reason = "", _com_reason(exc)
        if (text or framework not in UIA_LAZY_FRAMEWORKS
                or time.monotonic() + UIA_RETRY_GAP > give_up):
            break
        time.sleep(UIA_RETRY_GAP)
        attempt += 1
    if attempt:
        reason = f"{reason} after {attempt} retr{'y' if attempt == 1 else 'ies'}"
    return text, reason


def _read_selection(auto, uia):
    """One attempt. Returns (text, reason, framework id of the focused element)."""
    element = auto.GetFocusedElement()
    if not element:
        return "", "no-element", None
    framework = element.CurrentFrameworkId
    terminal = element.CurrentClassName == XTERM_JS_CLASS
    # Browsers focus a link or a child node inside the document; the document
    # is what implements TextPattern, so look a few levels up.
    pattern = _text_pattern(element, uia)
    walker = auto.ControlViewWalker
    hops = 0
    while pattern is None and hops < UIA_MAX_ANCESTORS:
        element = walker.GetParentElement(element)
        if not element:
            break
        pattern = _text_pattern(element, uia)
        hops += 1
    if pattern is None:
        return "", "terminal" if terminal else "no-textpattern", framework

    ranges = pattern.GetSelection()
    parts = []
    for i in range(ranges.Length if ranges else 0):
        text = ranges.GetElement(i).GetText(-1)
        if text:
            parts.append(text)
    text = "\n".join(parts)
    if not text.strip():
        return "", "terminal" if terminal else "empty", framework
    return text, ("ok" if hops == 0 else f"ok+{hops}up"), framework


_uia = _UiaWorker()


def warm_up():
    """Load comtypes and create the automation object ahead of the first read.

    The very first import also generates comtypes' wrapper module for
    UIAutomationCore (seconds, once per venv); afterwards it is ~70 ms.
    """
    _uia.start()


def uia_selection(deadline=UIA_DEADLINE):
    """(text, reason) from UI Automation. Never raises, never blocks past the
    deadline once warmed up."""
    if os.environ.get("WINTHINGS_NO_UIA"):
        return "", "disabled"
    return _uia.query(deadline)


# ---------------------------------------------------------------------------
# Ctrl+C fallback
# ---------------------------------------------------------------------------

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32.GlobalSize.argtypes = [ctypes.c_void_p]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
GMEM_MOVEABLE = 0x0002
gdi32.GetEnhMetaFileBits.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_char_p]
gdi32.GetEnhMetaFileBits.restype = ctypes.c_uint
gdi32.SetEnhMetaFileBits.argtypes = [ctypes.c_uint, ctypes.c_char_p]
gdi32.SetEnhMetaFileBits.restype = ctypes.c_void_p
gdi32.DeleteEnhMetaFile.argtypes = [ctypes.c_void_p]

# Standard formats whose data is a plain HGLOBAL, so their bytes are the whole
# story: text and its locale, DIBs, file drops, and a few legacy blobs.
# Everything else in the standard range is a GDI or owner handle (CF_BITMAP,
# CF_PALETTE, CF_METAFILEPICT, CF_DSP*, CF_OWNERDISPLAY, CF_PRIVATE*, CF_GDIOBJ*)
# and is skipped.
HGLOBAL_FORMATS = {
    win32con.CF_TEXT, win32con.CF_SYLK, win32con.CF_DIF, win32con.CF_TIFF,
    win32con.CF_OEMTEXT, win32con.CF_DIB, win32con.CF_RIFF, win32con.CF_WAVE,
    win32con.CF_UNICODETEXT, win32con.CF_HDROP, win32con.CF_LOCALE,
    win32con.CF_DIBV5,
}
# Windows synthesises these from CF_DIB / CF_ENHMETAFILE (which are kept, and
# which it in turn synthesises from these), so dropping them loses nothing and
# isn't worth a log line.
SYNTHESISED = {win32con.CF_BITMAP, win32con.CF_PALETTE, win32con.CF_METAFILEPICT}
FIRST_REGISTERED_FORMAT = 0xC000

# Registered formats that are not self-contained data. The OLE ones identify
# or point into the original owner's live COM object, so their bytes mean
# nothing (or the wrong thing) once that owner no longer holds the clipboard.
# FileContents is IStream-backed and indexed per file, so GetClipboardData can't
# fetch it; restoring its descriptor without it would offer files that don't
# paste.
SKIP_FORMAT_NAMES = {
    "Ole Private Data", "DataObject", "Object Descriptor", "Link Source",
    "Link Source Descriptor", "Embed Source", "Embedded Object", "OwnerLink",
    "ObjectLink", "Native", "FileContents", "FileGroupDescriptor",
    "FileGroupDescriptorW",
}
MAX_FORMAT_BYTES = 64 * 1024 * 1024

# Set on everything we put back, so clipboard-history tools (CopyQ honours
# these, measured on 17.0.0) and Windows' own Win+V history don't record the
# restore as a new copy, and it is not synced to other devices.
DONT_RECORD = ("ExcludeClipboardContentFromMonitorProcessing",
               "CanIncludeInClipboardHistory", "CanUploadToCloudClipboard")


def _format_name(fmt):
    if fmt < FIRST_REGISTERED_FORMAT:
        return None
    try:
        return win32clipboard.GetClipboardFormatName(fmt)
    except Exception:
        return None


def _global_bytes(handle):
    size = kernel32.GlobalSize(handle)
    if not size or size > MAX_FORMAT_BYTES:
        return None, size
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return None, size
    try:
        return ctypes.string_at(ptr, size), size
    finally:
        kernel32.GlobalUnlock(handle)


def _set_global(fmt, data):
    """SetClipboardData with exactly these bytes. (pywin32's own version pads
    the text formats with an extra NUL.) The clipboard owns the memory once
    this succeeds."""
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return False
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def _emf_bytes(handle):
    size = gdi32.GetEnhMetaFileBits(handle, 0, None)
    if not size or size > MAX_FORMAT_BYTES:
        return None
    buf = ctypes.create_string_buffer(size)
    if gdi32.GetEnhMetaFileBits(handle, size, buf) != size:
        return None
    return buf.raw


def snapshot():
    """Every clipboard format that can be put back faithfully, as
    [(format, bytes)], or None if the clipboard could not be opened.

    Asking for a delay-rendered format makes its owner render it now, which is
    the price of keeping it (Excel and other big producers notice).
    """
    if not _open():
        return None
    saved, skipped = [], []
    try:
        fmt = win32clipboard.EnumClipboardFormats(0)
        while fmt:
            name = _format_name(fmt)
            label = name or fmt
            try:
                if fmt == win32con.CF_ENHMETAFILE:
                    data = _emf_bytes(win32clipboard.GetClipboardDataHandle(fmt))
                    if data:
                        saved.append((fmt, data))
                    else:
                        skipped.append(label)
                elif fmt in HGLOBAL_FORMATS or (name and name not in SKIP_FORMAT_NAMES):
                    data, size = _global_bytes(win32clipboard.GetClipboardDataHandle(fmt))
                    if data:
                        saved.append((fmt, data))
                    else:
                        skipped.append(f"{label} ({size} bytes)")
                elif fmt not in SYNTHESISED:
                    skipped.append(label)
            except Exception:
                skipped.append(label)
            fmt = win32clipboard.EnumClipboardFormats(fmt)
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass
    if skipped:
        log.info("clipboard snapshot: kept %d formats, cannot restore %s",
                 len(saved), skipped)
    return saved


def _snapshot_text(saved):
    """CF_UNICODETEXT out of a snapshot, as str, or None."""
    for fmt, data in saved or ():
        if fmt == win32con.CF_UNICODETEXT:
            return data.decode("utf-16-le", "replace").split("\0", 1)[0]
    return None


def restore(saved):
    """Put a snapshot back, marked so clipboard history doesn't record it."""
    if not _open():
        return False
    try:
        win32clipboard.EmptyClipboard()
        failed = []
        for fmt, data in saved:
            if fmt == win32con.CF_ENHMETAFILE:
                handle = gdi32.SetEnhMetaFileBits(len(data), data)
                ok = bool(handle) and bool(user32.SetClipboardData(fmt, handle))
                if handle and not ok:
                    gdi32.DeleteEnhMetaFile(handle)
            else:
                ok = _set_global(fmt, data)
            if not ok:
                failed.append(_format_name(fmt) or fmt)
        zero = (0).to_bytes(4, "little")
        for name in DONT_RECORD:
            _set_global(win32clipboard.RegisterClipboardFormat(name), zero)
        if failed:
            log.warning("clipboard restore: could not set %s", failed)
        return True
    except Exception:
        log.warning("clipboard restore failed", exc_info=True)
        return False
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:
            pass


def _capture_clipboard(timeout=0.6, settle=0.05, wait_for_release=True):
    """Ctrl+C the selection, read it, put the old clipboard back.

    Every format snapshot() can keep is restored, not just text, and the
    restore is marked so clipboard-history tools skip it. The Ctrl+C itself
    is the target app's write, so it cannot be marked; see reader/README.md.
    """
    from common import hotkey

    saved = snapshot()
    saved_text = _snapshot_text(saved)
    before = sequence()
    if wait_for_release:
        release_modifiers()

    hotkey.send("ctrl+c")

    # Give the target app time to answer the keystroke. Some apps (Office,
    # Electron) set the clipboard in two steps, hence the extra settle.
    changed = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.02)
        now = sequence()
        if now is None:  # sequence numbers unavailable; fall back to the text
            fresh = get_text()
            changed = bool(fresh) and fresh != saved_text
        else:
            changed = now != before
        if changed:
            break
    if not changed:
        return ""
    time.sleep(settle)

    text = get_text() or ""
    if saved is None:
        log.warning("clipboard could not be snapshotted; captured text left on it")
    else:
        restore(saved)
    return text
