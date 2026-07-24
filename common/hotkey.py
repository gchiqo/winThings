"""Global hotkey registration on top of the `keyboard` package.

`keyboard` installs a low-level WH_KEYBOARD_LL hook, which means:
  * hotkeys fire regardless of which window has focus
  * it does NOT see keys pressed while an elevated window is focused, unless
    this process is also elevated (Windows blocks cross-integrity-level hooks)
  * suppression (swallowing the keystroke) works, but a suppressed combo that
    an app also wants will simply never reach that app

Callbacks run on `keyboard`'s hook thread, so anything touching Tk must be
marshalled back to the UI thread (see ui.call_on_ui_thread).
"""

import threading

_lock = threading.Lock()


def normalize(spec):
    """'Ctrl + Shift + V' -> 'ctrl+shift+v'."""
    if not spec:
        return ""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    aliases = {
        "control": "ctrl", "win": "windows", "super": "windows",
        "meta": "windows", "return": "enter", "escape": "esc",
    }
    return "+".join(aliases.get(p, p) for p in parts)


def pretty(spec):
    """'ctrl+shift+v' -> 'Ctrl + Shift + V'."""
    if not spec:
        return "(none)"
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "windows": "Win",
             "esc": "Esc", "enter": "Enter", "space": "Space"}
    return " + ".join(
        names.get(p, p.upper() if len(p) == 1 else p.capitalize())
        for p in normalize(spec).split("+")
    )


def is_valid(spec):
    """Can `keyboard` parse this? Checked without registering anything."""
    spec = normalize(spec)
    if not spec:
        return False
    try:
        import keyboard

        keyboard.parse_hotkey(spec)
        return True
    except Exception:
        return False


class HotkeyManager:
    """Register/rebind named hotkeys, tracking whether each actually bound.

    Binding can fail (bad spec, or another process holding an exclusive
    registration), so apps can surface a "hotkey not bound" state instead of
    silently doing nothing — the same indicator the GTK version had.
    """

    def __init__(self):
        self._handles = {}   # name -> keyboard handle
        self._specs = {}     # name -> normalized spec
        self._failed = {}    # name -> error string

    def bind(self, name, spec, callback, suppress=False):
        """(Re)bind `name` to `spec`. Returns True if the hook took."""
        import keyboard

        spec = normalize(spec)
        with _lock:
            self.unbind(name)
            if not spec:
                self._failed[name] = "no hotkey set"
                return False
            try:
                handle = keyboard.add_hotkey(
                    spec, callback, suppress=suppress, trigger_on_release=False
                )
            except Exception as exc:
                self._failed[name] = str(exc) or "could not register"
                return False
            self._handles[name] = handle
            self._specs[name] = spec
            self._failed.pop(name, None)
            return True

    def unbind(self, name):
        import keyboard

        handle = self._handles.pop(name, None)
        self._specs.pop(name, None)
        if handle is not None:
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass

    def bound(self, name):
        return name in self._handles

    def spec(self, name):
        return self._specs.get(name, "")

    def error(self, name):
        return self._failed.get(name)

    def unbind_all(self):
        for name in list(self._handles):
            self.unbind(name)


def send(combo):
    """Synthesize a keystroke (e.g. 'ctrl+c'). Best-effort."""
    try:
        import keyboard

        keyboard.send(normalize(combo))
        return True
    except Exception:
        return False


def capture(on_done, cancel_key="esc"):
    """Record the next chord the user presses; for the settings hotkey field.

    Calls on_done(spec_or_None) on the hook thread once a non-modifier key is
    released. Returns a function that aborts the capture.
    """
    import keyboard

    pressed, state = set(), {"stopped": False}
    mods = {"ctrl", "left ctrl", "right ctrl", "alt", "left alt", "right alt",
            "shift", "left shift", "right shift", "windows", "left windows",
            "right windows"}

    def finish(spec):
        if state["stopped"]:
            return
        state["stopped"] = True
        try:
            keyboard.unhook(hook)
        except (KeyError, ValueError):
            pass
        on_done(spec)

    def hook(event):
        name = (event.name or "").lower()
        if event.event_type == "down":
            if name == cancel_key and not pressed:
                finish(None)
                return
            pressed.add(name)
            if name not in mods:
                order = {"ctrl": 0, "alt": 1, "shift": 2, "windows": 3}
                keys = sorted(
                    {k.replace("left ", "").replace("right ", "") for k in pressed},
                    key=lambda k: order.get(k, 9),
                )
                finish(normalize("+".join(keys)))

    hook = keyboard.hook(hook)
    return lambda: finish(None)
