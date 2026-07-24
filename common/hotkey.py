"""Global hotkeys and key synthesis, with no keyboard hook anywhere.

This module keeps the interface the apps were written against, but the
implementation underneath changed for a reason worth recording.

It used to wrap the `keyboard` package's add_hotkey(..., suppress=True), which
installs a WH_KEYBOARD_LL hook. To suppress a combination that package must
decide, on the Ctrl key-down, whether a hotkey is coming — so it blocks the
keystroke and re-injects it later if the combination turns out not to match.
Press Tab or Caps Lock after a modifier and that replay is exactly what runs.
With the clipboard and reader each running their own block-and-replay state
machine over one physical keyboard, a lost key-up leaves Ctrl or Shift stuck
down, which is what happened in practice.

Now:
  * hotkeys go through RegisterHotKey (see winhotkey.py) — the OS matches and
    consumes the combination, so there is no hook and nothing to replay
  * synthetic keystrokes go through SendInput (see keysend.py), which never
    passes through a hook, always releases what it pressed, and force-releases
    any modifier left down
  * recording a hotkey in the settings window polls GetAsyncKeyState instead of
    hooking the keyboard

The `keyboard` package is no longer used by any of this.
"""

from . import keysend
from .winhotkey import HotkeyManager, available, parse  # noqa: F401

__all__ = ["HotkeyManager", "normalize", "pretty", "is_valid", "available",
           "send", "release_stuck", "modifiers_down", "capture"]

ALIASES = {
    "control": "ctrl", "win": "windows", "super": "windows", "meta": "windows",
    "return": "enter", "escape": "esc",
}
DISPLAY = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "windows": "Win",
           "esc": "Esc", "enter": "Enter", "space": "Space", "tab": "Tab",
           "capslock": "Caps Lock"}
# Win first, matching how Windows itself writes its shortcuts (Win+Shift+S,
# Win+Ctrl+D). Only affects display and the stored string; parse() does not care
# about modifier order.
MODIFIER_ORDER = {"windows": 0, "ctrl": 1, "alt": 2, "shift": 3}


def normalize(spec):
    """'Ctrl + Shift + V' -> 'ctrl+shift+v'."""
    if not spec:
        return ""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    return "+".join(ALIASES.get(p, p) for p in parts)


def pretty(spec):
    """'ctrl+shift+v' -> 'Ctrl + Shift + V'."""
    if not spec:
        return "(none)"
    return " + ".join(
        DISPLAY.get(p, p.upper() if len(p) == 1 else p.capitalize())
        for p in normalize(spec).split("+")
    )


def is_valid(spec):
    """Parseable as a modifier+key combination? Registers nothing."""
    try:
        parse(normalize(spec))
        return True
    except ValueError:
        return False


def send(combo):
    """Synthesise a keystroke, e.g. 'ctrl+v'. Releases everything it presses."""
    return keysend.send(normalize(combo))


def release_stuck(skip_win=True):
    """Force any held modifier up. Returns the VKs it had to release."""
    return keysend.release_stuck(skip_win=skip_win)


def modifiers_down():
    return keysend.modifiers_down()


# -- recording a hotkey ------------------------------------------------------
# Polled rather than hooked: this runs while the user is pressing modifiers, so
# it is the last place that should install a hook that might swallow a key-up.

import threading  # noqa: E402
import time  # noqa: E402

_VK_NAMES = {
    0x1B: "esc", 0x09: "tab", 0x20: "space", 0x0D: "enter", 0x08: "backspace",
    0x2E: "delete", 0x2D: "insert", 0x24: "home", 0x23: "end",
    0x21: "pageup", 0x22: "pagedown", 0x26: "up", 0x28: "down",
    0x25: "left", 0x27: "right", 0x14: "capslock", 0x2C: "printscreen",
    0x13: "pause",
    **{0x6F + n: f"f{n}" for n in range(1, 25)},
    **{0x30 + n: str(n) for n in range(10)},
    **{0x41 + i: chr(0x61 + i) for i in range(26)},
}

_MOD_VKS = [("ctrl", (0xA2, 0xA3)), ("alt", (0xA4, 0xA5)),
            ("shift", (0xA0, 0xA1)), ("windows", (0x5B, 0x5C))]


def capture(on_done, cancel_key="esc", timeout=15.0, poll=0.03):
    """Record the next combination the user presses.

    Calls on_done(spec) once a non-modifier key goes down while at least one
    modifier is held, on_done(None) if Esc is pressed alone or nothing arrives
    before the timeout. Returns a function that aborts the capture.
    """
    state = {"stopped": False}

    def finish(spec):
        if state["stopped"]:
            return
        state["stopped"] = True
        on_done(spec)

    def held_modifiers():
        return [name for name, vks in _MOD_VKS
                if any(keysend.user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)]

    def _any_key_down():
        return any(keysend.user32.GetAsyncKeyState(vk) & 0x8000
                   for vk in _VK_NAMES)

    def _pressed(vk):
        """Down now, or tapped since the last check.

        GetAsyncKeyState's low bit reports a press that happened between calls,
        which is what catches a key tapped faster than the poll interval. Reading
        it also clears it, so the drain loop below leaves a clean slate.
        """
        return keysend.user32.GetAsyncKeyState(vk) & 0x8001

    def watch():
        deadline = time.monotonic() + timeout
        # Drain first: the keystroke that opened the recorder must not be the one
        # it records, and stale tapped-since bits have to be cleared.
        while time.monotonic() < deadline and not state["stopped"]:
            for vk in _VK_NAMES:
                _pressed(vk)
            if not held_modifiers() and not _any_key_down():
                break
            time.sleep(poll)

        while time.monotonic() < deadline and not state["stopped"]:
            mods = held_modifiers()
            for vk, name in _VK_NAMES.items():
                if not _pressed(vk):
                    continue
                if name == cancel_key and not mods:
                    finish(None)
                    return
                if mods:
                    ordered = sorted(mods, key=lambda m: MODIFIER_ORDER[m])
                    finish(normalize("+".join(ordered + [name])))
                    return
            time.sleep(poll)
        finish(None)

    threading.Thread(target=watch, daemon=True, name="hotkey-capture").start()
    return lambda: finish(None)
