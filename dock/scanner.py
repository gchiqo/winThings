"""Builds the dock's model — one entry per app — on a background thread.

Enumeration, psutil lookups and icon extraction all touch other processes and
the GDI heap, which is slow enough to stutter the UI if done inline. This runs
them on a worker and hands the result over as plain data plus a Pillow image;
turning that into a Tk image stays on the UI thread, where it belongs.

Rebuilding widgets on every tick would flicker, so each scan produces a
signature and the callback only fires when something the user can actually see
has changed.
"""

import os
import threading

from . import icons, winlist


class AppEntry:
    """One dock button: an executable, its icon, and its open windows."""

    __slots__ = ("key", "name", "exe", "image", "windows", "pinned", "focused")

    def __init__(self, key, name, exe, image, windows, pinned, focused):
        self.key = key
        self.name = name
        self.exe = exe
        self.image = image
        self.windows = windows
        self.pinned = pinned
        self.focused = focused

    @property
    def running(self):
        return bool(self.windows)

    def __repr__(self):
        return f"<AppEntry {self.name} windows={len(self.windows)} pinned={self.pinned}>"


def _display_name(exe, fallback):
    stem = os.path.splitext(os.path.basename(exe or ""))[0]
    return stem or fallback or "?"


def build_entries(config, icon_size, skip_pids=()):
    """Current apps, running first in z-order, then pinned-but-closed."""
    windows = winlist.enumerate_windows(skip_pids=skip_pids)
    groups = winlist.group_windows(windows)
    focused = winlist.foreground_hwnd()
    pinned = [p.lower() for p in (config["pinned"] or []) if p]

    entries = []
    for key, group in groups.items():
        first = group[0]
        entries.append(AppEntry(
            key=key,
            name=_display_name(first.exe, first.name),
            exe=first.exe,
            image=icons.get(first.exe, first.hwnd, first.name, icon_size),
            windows=group,
            pinned=key in pinned,
            focused=any(w.hwnd == focused for w in group),
        ))

    for path in pinned:
        if path in groups:
            continue
        entries.append(AppEntry(
            key=path,
            name=_display_name(path, path),
            exe=path,
            image=icons.get(path, 0, os.path.basename(path), icon_size, dim=True),
            windows=[],
            pinned=True,
            focused=False,
        ))
    return entries


def signature(entries):
    """What a redraw actually depends on. Ignores z-order churn."""
    return tuple(
        (e.key, e.pinned, e.focused, tuple((w.hwnd, w.title, w.minimized) for w in e.windows))
        for e in entries
    )


class Scanner:
    """Polls the window list and calls on_change(entries) when it differs."""

    def __init__(self, config, on_change, skip_pids=()):
        self.config = config
        self.on_change = on_change
        self.skip_pids = tuple(skip_pids)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._last = None
        self._thread = None

    def interval(self):
        return self.config.num("window_refresh", 0.25, 10.0)

    def icon_size(self):
        return self.config.integer("icon_size", 16, 64)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="scanner")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()
        self._wake.set()

    def refresh_now(self):
        """Force the next tick to redraw — used after a config change."""
        self._last = None
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                entries = build_entries(self.config, self.icon_size(), self.skip_pids)
                sig = signature(entries)
                if sig != self._last:
                    self._last = sig
                    self.on_change(entries)
            except Exception:
                pass  # a transient win32 error must not end the scan loop
            self._wake.wait(self.interval())
            self._wake.clear()
