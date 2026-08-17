"""Recover non-ANSI keyboard input that Tk turns into '?' on Windows.

When the system ANSI codepage can't represent a typed character — Georgian on a
codepage-1252 machine, say — Tk's Entry delivers '?' and the real character is
lost, so display and any search built on the text break. Reconstructing it from
the virtual key fails too: some layouts (Georgian QWERTY) don't answer
ToUnicodeEx/MapVirtualKey at all.

But the truth is in the WM_CHAR message before Tk touches it: the message queue
is Unicode, so its wParam is the real UTF-16 character. We install a thread-local
WH_GETMESSAGE hook that stashes each non-ASCII WM_CHAR, and each fixed Entry, on
the KeyPress where Tk hands it a '?', inserts the stashed character instead.
No-op off Windows.
"""

import collections
import ctypes
import sys
from ctypes import wintypes

_WIN = sys.platform == "win32"
_u32 = ctypes.WinDLL("user32", use_last_error=True) if _WIN else None
_k32 = ctypes.WinDLL("kernel32", use_last_error=True) if _WIN else None

_WH_GETMESSAGE = 3
_WM_CHAR = 0x0102
_PM_REMOVE = 0x0001

# Real characters seen on WM_CHAR, waiting for their (mangled) KeyPress. Small
# and FIFO: typing is one char at a time, so it drains in lock-step.
_pending = collections.deque(maxlen=16)
_hook = None
_hook_proc = None  # kept alive; a GC'd callback crashes the message loop


if _WIN:
    _LRESULT = ctypes.c_ssize_t
    _HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int,
                                   wintypes.WPARAM, _LRESULT)

    class _MSG(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                    ("wParam", wintypes.WPARAM), ("lParam", _LRESULT),
                    ("time", wintypes.DWORD), ("pt_x", wintypes.LONG),
                    ("pt_y", wintypes.LONG)]

    _u32.CallNextHookEx.restype = _LRESULT
    _u32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int,
                                    wintypes.WPARAM, _LRESULT]
    _u32.SetWindowsHookExW.restype = wintypes.HHOOK
    _u32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC,
                                       wintypes.HINSTANCE, wintypes.DWORD]


def _install_hook():
    """Install the WM_CHAR hook on the calling (Tk) thread, once."""
    global _hook, _hook_proc
    if _hook or not _WIN:
        return

    def proc(code, wparam, lparam):
        # Only the actual dequeue (PM_REMOVE); WH_GETMESSAGE also fires on peek.
        if code >= 0 and wparam == _PM_REMOVE:
            msg = ctypes.cast(lparam, ctypes.POINTER(_MSG)).contents
            if msg.message == _WM_CHAR and msg.wParam >= 0x80:
                _pending.append(chr(msg.wParam & 0xFFFF))
        return _u32.CallNextHookEx(None, code, wparam, lparam)

    _hook_proc = _HOOKPROC(proc)
    _hook = _u32.SetWindowsHookExW(_WH_GETMESSAGE, _hook_proc, None,
                                   _k32.GetCurrentThreadId())


def fix_unicode_input(entry):
    """Make `entry` recover non-ANSI typing lost to '?'. Returns it."""
    if not _WIN:
        return entry
    _install_hook()
    target = getattr(entry, "_entry", None) or entry  # CTkEntry -> inner tk.Entry

    def on_key(event):
        # Tk mangles the unrepresentable char to '?' (or drops it); when the hook
        # has the real one, use it. Anything Tk got right (ASCII) is left alone.
        if event.char in ("", "?") and _pending:
            real = _pending.popleft()
            if real.isprintable():
                try:
                    event.widget.delete("sel.first", "sel.last")
                except Exception:
                    pass
                event.widget.insert("insert", real)
                return "break"
        elif event.char and event.char not in ("", "?"):
            _pending.clear()  # Tk handled this key; don't let a stale char linger
        return None

    # Instance binding runs before the Entry class binding, so "break" suppresses
    # the default '?' insertion.
    target.bind("<KeyPress>", on_key, add="+")
    return entry
