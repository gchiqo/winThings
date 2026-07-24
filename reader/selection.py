"""Get "whatever text is selected right now", the Windows way.

X11 has a PRIMARY selection that always holds the current highlight, so the
Linux version just read it. Windows has no such thing: the only mechanism that
works across arbitrary apps is to synthesize Ctrl+C and look at the clipboard.
That is destructive, so we snapshot the clipboard first and put it back.

Two Windows specifics this has to work around:

  * The hotkey fires on key-down, so the user is still holding Shift (and maybe
    Alt/Win) when we send Ctrl+C — the focused app would see Ctrl+Shift+C, not
    Ctrl+C. So we wait for the modifiers to come up, and force them up if the
    user is leaning on the keys.
  * The clipboard is a lock other processes hold for short bursts, so every open
    is retried. And GetClipboardSequenceNumber is the only reliable "did
    anything actually get copied" signal: comparing the text before and after
    can't tell "nothing was selected" from "the selection equals what was
    already on the clipboard".

Known limitation: only CF_UNICODETEXT is restored. If the clipboard held an
image or files, Ctrl+C in the target app has already replaced it and we cannot
put it back — the captured text is left there instead of an empty clipboard.
"""

import time

import win32api
import win32clipboard
import win32con

import bootstrap  # noqa: F401  (repo root on sys.path, for common.hotkey)

# Ctrl is deliberately absent: we are about to send Ctrl+C, so the user still
# holding it does no harm. Every one of these, though, turns our Ctrl+C into
# Ctrl+<something>+C in the target app.
MODIFIER_KEYS = (win32con.VK_LSHIFT, win32con.VK_RSHIFT,
                 win32con.VK_LMENU, win32con.VK_RMENU,
                 win32con.VK_LWIN, win32con.VK_RWIN)


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

    GetAsyncKeyState rather than keyboard.is_pressed: the latter answers from
    the `keyboard` package's own hook state, which is only populated once a hook
    is installed and misses keys pressed before that.
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


def capture(timeout=0.6, settle=0.05, wait_for_release=True):
    """Return the selected text, or "" if nothing was selected.

    Leaves the clipboard as it found it (text formats only, see module docstring).
    """
    from common import hotkey

    saved = get_text()
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
            changed = bool(fresh) and fresh != saved
        else:
            changed = now != before
        if changed:
            break
    if not changed:
        return ""
    time.sleep(settle)

    text = get_text() or ""
    if saved is not None and saved != text:
        set_text(saved)
    return text
