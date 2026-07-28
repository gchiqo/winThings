"""Synthesise keystrokes with SendInput, and clean up modifiers afterwards.

The `keyboard` package's send() routes through the same low-level hook it uses
for hotkeys, so a synthetic Ctrl+V could race that hook's suppression state and
leave Ctrl down. SendInput talks to the input queue directly: no hook, no
replay, and every key this module presses is released in the same call.

release_stuck() is the safety net. Any modifier the OS still believes is held —
whether this module pressed it, or a physical key-up went missing — is forced
up. Cheap enough to call after anything that touches input.
"""

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C

# Left/right variants, because releasing VK_CONTROL does not clear VK_RCONTROL.
ALL_MODIFIERS = (VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL,
                 VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN)

EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
            VK_RCONTROL, VK_RMENU, VK_LWIN, VK_RWIN}


# dwExtraInfo is ULONG_PTR, so it is pointer-sized. Getting this wrong makes
# every struct the wrong length and SendInput rejects the lot with
# ERROR_INVALID_PARAMETER, silently sending nothing.
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    """Never used, but it is the union's largest member and therefore sets
    sizeof(INPUT) — 40 bytes on x64, 28 on x86."""
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTunion)]


def _event(vk, up):
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.union.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0,
                               dwExtraInfo=0)
    return item


def _send(events):
    if not events:
        return True
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), ctypes.byref(array),
                            ctypes.sizeof(INPUT))
    if sent != len(events):
        # Worth surfacing: a silent no-op here is how a Ctrl+V that never
        # happens looks from the outside.
        import logging

        logging.getLogger(__name__).warning(
            "SendInput sent %s of %s events (error %s)",
            sent, len(events), ctypes.get_last_error())
    return sent == len(events)


def modifiers_down():
    """Modifier VKs the OS currently believes are physically held."""
    return [vk for vk in ALL_MODIFIERS
            if user32.GetAsyncKeyState(vk) & 0x8000]


def release_stuck(skip_win=True):
    """Force every held modifier up. Returns the VKs it had to release.

    Win is skipped by default: a lone Win key-up opens the Start menu, so
    releasing it as a precaution is worse than leaving it.
    """
    released = []
    for vk in modifiers_down():
        if skip_win and vk in (VK_LWIN, VK_RWIN):
            continue
        _send([_event(vk, up=True)])
        released.append(vk)
    return released


def send(spec, settle=0.02):
    """Press a combination like 'ctrl+v' and release everything it pressed.

    Any modifier already held is released first, so a user still leaning on the
    hotkey's Shift cannot turn this into Ctrl+Shift+V in the target app.
    """
    from .winhotkey import NAMED_KEYS

    # Left-hand VKs specifically: SendInput with the neutral VK_CONTROL can
    # leave the OS's left/right shadow state inconsistent.
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    mod_vks, key_vk = [], None
    lookup = {"ctrl": VK_LCONTROL, "control": VK_LCONTROL, "alt": VK_LMENU,
              "shift": VK_LSHIFT, "win": VK_LWIN, "windows": VK_LWIN,
              "super": VK_LWIN, "meta": VK_LWIN}
    for part in parts:
        if part in lookup:
            mod_vks.append(lookup[part])
        elif part in NAMED_KEYS:
            key_vk = NAMED_KEYS[part]
        elif len(part) == 1:
            # A-Z / 0-9 have VK codes equal to their ASCII upper-case value,
            # independent of layout. VkKeyScanW returns -1 for these under a
            # non-Latin (e.g. Georgian) layout, which broke the synthetic Ctrl+C
            # capture and Ctrl+V paste. Sending the VK fires the physical key.
            ch = part.upper()
            if "A" <= ch <= "Z" or "0" <= ch <= "9":
                key_vk = ord(ch)
            else:
                scan = user32.VkKeyScanW(ctypes.c_wchar(part))
                if scan != -1:
                    key_vk = scan & 0xFF
    if key_vk is None:
        return False

    release_stuck()
    if settle:
        time.sleep(settle)

    events = [_event(vk, up=False) for vk in mod_vks]
    events.append(_event(key_vk, up=False))
    events.append(_event(key_vk, up=True))
    events.extend(_event(vk, up=True) for vk in reversed(mod_vks))
    ok = _send(events)

    if settle:
        time.sleep(settle)
    release_stuck()
    return ok
