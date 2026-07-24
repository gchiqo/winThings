"""JSON config with defaults, atomic writes and change notification.

Usage:

    cfg = Config("clipboard", {"hotkey": "ctrl+shift+v", "cap": 500})
    cfg["cap"]                  # -> 500
    cfg["cap"] = 1000           # saves immediately, fires watchers
    cfg.subscribe(on_change)    # called with the Config after every save

Unknown keys already in the file are preserved; missing keys fall back to the
defaults rather than raising, so a hand-edited or truncated file never takes an
app down.
"""

import json
import os
import tempfile
import threading


class Config:
    def __init__(self, app, defaults):
        from .paths import config_path

        self.app = app
        self.defaults = dict(defaults)
        self.path = config_path(app)
        self._lock = threading.RLock()
        self._watchers = []
        self._data = dict(self.defaults)
        self.load()

    # -- read/write -------------------------------------------------------
    def load(self):
        with self._lock:
            data = dict(self.defaults)
            try:
                with open(self.path, encoding="utf-8") as fh:
                    on_disk = json.load(fh)
                if isinstance(on_disk, dict):
                    data.update(on_disk)
            except (OSError, ValueError):
                pass  # missing or corrupt -> defaults
            self._data = data
        return self

    def save(self):
        """Write via temp file + replace so a crash mid-write can't truncate."""
        with self._lock:
            snapshot = dict(self._data)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(snapshot, fh, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return self
        self._notify()
        return self

    # -- dict-ish access --------------------------------------------------
    def __getitem__(self, key):
        with self._lock:
            return self._data.get(key, self.defaults.get(key))

    def __setitem__(self, key, value):
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
        self.save()

    def get(self, key, fallback=None):
        with self._lock:
            return self._data.get(key, self.defaults.get(key, fallback))

    def update(self, mapping):
        """Set several keys and save once."""
        with self._lock:
            changed = any(self._data.get(k) != v for k, v in mapping.items())
            self._data.update(mapping)
        if changed:
            self.save()
        return self

    def as_dict(self):
        with self._lock:
            return dict(self._data)

    def reset(self):
        with self._lock:
            self._data = dict(self.defaults)
        self.save()
        return self

    # -- clamping helpers -------------------------------------------------
    def num(self, key, lo, hi, fallback=None):
        """Config values are user-editable JSON; never trust the range."""
        try:
            return max(lo, min(hi, float(self[key])))
        except (TypeError, ValueError):
            return self.defaults.get(key, fallback)

    def integer(self, key, lo, hi, fallback=None):
        val = self.num(key, lo, hi, fallback)
        try:
            return int(val)
        except (TypeError, ValueError):
            return fallback

    # -- watchers ---------------------------------------------------------
    def subscribe(self, fn):
        """Register fn(config); called after every successful save."""
        with self._lock:
            self._watchers.append(fn)
        return fn

    def _notify(self):
        with self._lock:
            watchers = list(self._watchers)
        for fn in watchers:
            try:
                fn(self)
            except Exception:
                pass  # a broken watcher must not break saving
