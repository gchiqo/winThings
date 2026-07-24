"""Global hotkeys via Windows' RegisterHotKey, with no keyboard hook.

This replaces the `keyboard` package's suppress=True hotkeys, which caused
modifiers to stick. That mechanism installs a WH_KEYBOARD_LL hook and, because
it cannot know whether a ctrl+shift prefix will complete into a registered
combination, it blocks those keystrokes and re-injects them once the next key
rules the hotkey out. Pressing Tab or Caps Lock after a modifier is exactly that
case. With two apps each running their own block-and-replay state machine over
the same physical keyboard, a dropped key-up leaves Ctrl or Shift down.

RegisterHotKey has none of that: the OS matches the combination itself and
consumes it, so there is no hook, no suppression state and nothing to replay.
It cannot corrupt modifier state because it never blocks an ordinary keystroke.

Trade-offs, deliberately accepted:
  * only modifier+key combinations, which is all these apps use
  * one owner per combination system-wide; a clash surfaces as a bind failure
    rather than two apps silently fighting, which is the better outcome
  * a hotkey whose modifiers include Win may collide with a Windows shortcut,
    and Windows wins

Registration is thread-affine: WM_HOTKEY is delivered to the thread that
registered it, so everything happens on one owned thread with its own message
loop, and bind/unbind are marshalled onto it.
"""

import ctypes
import queue
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # don't fire repeatedly while the key is held

WM_HOTKEY = 0x0312
WM_APP_CMD = 0x8001  # our own: "a bind/unbind request is waiting"
WM_APP_QUIT = 0x8002

MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "windows": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}

# Keys whose names don't map through VkKeyScan.
NAMED_KEYS = {
    "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20, "enter": 0x0D,
    "return": 0x0D, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "printscreen": 0x2C, "pause": 0x13,
    **{f"f{n}": 0x6F + n for n in range(1, 25)},
}


def parse(spec):
    """'ctrl+shift+v' -> (mods, vk). Raises ValueError on anything unusable."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")

    mods, key = 0, None
    for part in parts:
        if part in MODIFIER_NAMES:
            mods |= MODIFIER_NAMES[part]
        elif key is None:
            key = part
        else:
            raise ValueError(f"more than one non-modifier key: {key!r} and {part!r}")

    if key is None:
        raise ValueError("hotkey needs a key besides modifiers")
    if not mods:
        # A bare key would swallow it globally; refuse rather than hijack it.
        raise ValueError("hotkey needs at least one modifier")

    if key in NAMED_KEYS:
        return mods, NAMED_KEYS[key]
    if len(key) == 1:
        scan = user32.VkKeyScanW(ctypes.c_wchar(key))
        if scan != -1:
            return mods, scan & 0xFF
    raise ValueError(f"unknown key: {key!r}")


class HotkeyManager:
    """Register/rebind named hotkeys. Same surface as the old hook-based one."""

    def __init__(self):
        self._names = {}      # name -> id
        self._specs = {}      # name -> spec
        self._callbacks = {}  # id -> callback
        self._failed = {}     # name -> error string
        self._next_id = 1
        self._commands = queue.Queue()
        self._results = queue.Queue()
        self._lock = threading.RLock()
        self._tid = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="hotkeys")
        self._thread.start()
        self._ready.wait(5.0)

    # -- the owning thread -------------------------------------------------
    def _run(self):
        # Force a message queue to exist before anyone posts to this thread.
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._tid = threading.get_native_id()
        self._ready.set()

        while True:
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                callback = self._callbacks.get(msg.wParam)
                if callback:
                    threading.Thread(target=callback, daemon=True).start()
            elif msg.message == WM_APP_CMD:
                self._drain_commands()
            elif msg.message == WM_APP_QUIT:
                break

    def _drain_commands(self):
        while True:
            try:
                action, hotkey_id, mods, vk = self._commands.get_nowait()
            except queue.Empty:
                return
            if action == "register":
                ok = bool(user32.RegisterHotKey(None, hotkey_id, mods, vk))
                err = ctypes.get_last_error() if not ok else 0
                self._results.put((ok, err))
            elif action == "unregister":
                user32.UnregisterHotKey(None, hotkey_id)
                self._results.put((True, 0))

    def _submit(self, action, hotkey_id, mods=0, vk=0, timeout=3.0):
        self._commands.put((action, hotkey_id, mods, vk))
        user32.PostThreadMessageW(self._tid, WM_APP_CMD, 0, 0)
        try:
            return self._results.get(timeout=timeout)
        except queue.Empty:
            return False, -1

    # -- public API --------------------------------------------------------
    def bind(self, name, spec, callback, suppress=None):
        """(Re)bind `name`. `suppress` is accepted and ignored: RegisterHotKey
        always consumes the combination, which is the behaviour that flag asked
        for, and there is no longer a mode that leaks the keystroke through."""
        with self._lock:
            self.unbind(name)
            try:
                mods, vk = parse(spec)
            except ValueError as exc:
                self._failed[name] = str(exc)
                return False

            hotkey_id = self._next_id
            self._next_id += 1
            self._callbacks[hotkey_id] = callback
            ok, err = self._submit("register", hotkey_id, mods | MOD_NOREPEAT, vk)
            if not ok:
                self._callbacks.pop(hotkey_id, None)
                self._failed[name] = (
                    "already taken by another app" if err == 1409
                    else f"could not register (error {err})")
                return False

            self._names[name] = hotkey_id
            self._specs[name] = spec
            self._failed.pop(name, None)
            return True

    def unbind(self, name):
        with self._lock:
            hotkey_id = self._names.pop(name, None)
            self._specs.pop(name, None)
            if hotkey_id is None:
                return
            self._submit("unregister", hotkey_id)
            self._callbacks.pop(hotkey_id, None)

    def bound(self, name):
        return name in self._names

    def spec(self, name):
        return self._specs.get(name, "")

    def error(self, name):
        return self._failed.get(name)

    def unbind_all(self):
        for name in list(self._names):
            self.unbind(name)


def available(spec):
    """Could this combination be registered right now? Leaves nothing behind."""
    try:
        mods, vk = parse(spec)
    except ValueError:
        return False
    manager_id = 0xBFFF
    if not user32.RegisterHotKey(None, manager_id, mods | MOD_NOREPEAT, vk):
        return False
    user32.UnregisterHotKey(None, manager_id)
    return True
