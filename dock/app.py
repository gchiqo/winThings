"""WinThings dock — an always-on-top bar along one screen edge.

Run it with:  pythonw.exe dock\\app.py

This is a plain floating window, not a shell extension: nothing here hooks,
patches or replaces Explorer, the Start menu or the real taskbar. Auto-hide the
Windows taskbar yourself and this sits where it used to be.

Threading: Tk owns the main thread, pystray gets its own (its Windows backend
needs a message loop), and the window scanner and metric poller each get one
because EnumWindows, psutil and NVML all block. Worker results come back
through common.ui.call_on_ui_thread, which needs a drain loop running on a live
widget — common.ui only starts one for SettingsWindow, so the dock starts its
own against the dock window.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import Config, autostart, singleton, theme, ui  # noqa: E402
from common.tray import Tray  # noqa: E402
from dock.dockwindow import Dock  # noqa: E402
from dock.metrics import Poller, gpu_available, gpu_name  # noqa: E402
from dock.scanner import Scanner, build_entries  # noqa: E402
from dock.settings import APP_NAME, DockSettings  # noqa: E402

APP = "dock"

DEFAULTS = {
    "edge": "bottom",          # bottom | top | left | right
    "span": "full",            # full edge, or fit to content
    "margin": 0,               # gap between the dock and the screen edge, px
    "icon_size": 32,           # 16-64, live-scales every icon
    "show_chips": True,        # expand multi-window apps into title chips
    "chip_chars": 22,          # truncate chip titles to this many characters
    "window_refresh": 1.0,     # seconds between window-list scans
    "poll_interval": 2.0,      # seconds between metric samples
    "show_cpu": True,
    "show_ram": True,
    "show_gpu": True,
    "show_net_up": True,
    "show_net_down": True,
    "pinned": [],              # executable paths that stay on the dock
    "autostart": False,
}


class App:
    def __init__(self, lifetime=None):
        self.config = Config(APP, DEFAULTS)
        self.config["autostart"] = autostart.is_enabled(APP_NAME)
        self.dock = Dock(self.config)
        self.settings = None
        self.entries = []

        self.scanner = Scanner(self.config, self._on_entries, skip_pids=[os.getpid()])
        self.poller = Poller(self.config, self._on_sample)
        self.tray = self._build_tray()
        self.config.subscribe(self._on_config)

        ui._drain(self.dock)  # pump call_on_ui_thread for the dock's lifetime
        if lifetime:
            self.dock.after(int(lifetime * 1000), self.quit)

    # -- worker callbacks (background threads) ----------------------------
    def _on_entries(self, entries):
        self.entries = entries
        ui.call_on_ui_thread(self.dock.set_entries, entries)

    def _on_sample(self, sample):
        ui.call_on_ui_thread(self.dock.set_metrics, sample)

    def _on_config(self, _config):
        ui.call_on_ui_thread(self._reload)

    def _reload(self):
        from dock import icons

        icons.forget_scaled()
        self.dock.apply_config()
        self.scanner.refresh_now()

    # -- tray -------------------------------------------------------------
    def _build_tray(self):
        tray = Tray(APP, "WinThings Dock", "D", theme.ACCENT)
        tray.add("Settings", self.open_settings, default=True)
        tray.add("Refresh now", self.scanner_refresh)
        tray.separator()
        tray.add("Hide dock", self.toggle_dock)
        tray.separator()
        tray.add("Quit", self.quit)
        return tray

    def scanner_refresh(self):
        self.scanner.refresh_now()

    def toggle_dock(self):
        def flip():
            if self.dock.winfo_viewable():
                self.dock.withdraw()
            else:
                self.dock.deiconify()
                self.dock.reposition()

        ui.call_on_ui_thread(flip)

    def open_settings(self):
        ui.call_on_ui_thread(self._open_settings)

    def _open_settings(self):
        if self.settings is None:
            self.settings = DockSettings(
                self.config,
                script_path=os.path.abspath(__file__),
                list_apps=lambda: self.entries,
            )
        self.settings.show()

    # -- lifecycle --------------------------------------------------------
    def run(self):
        self.scanner.start()
        self.poller.start()
        self.tray.start()
        self.dock.mainloop()

    def quit(self):
        self.scanner.stop()
        self.poller.stop()
        self.tray.stop()
        for window in (self.settings, self.dock):
            try:
                if window is not None:
                    window.destroy()
            except Exception:
                pass


def selftest():
    """Headless check of enumeration, icons and metrics — no window shown."""
    import time

    from dock import icons

    config = Config(APP, DEFAULTS)
    entries = build_entries(config, 32, skip_pids=[os.getpid()])
    print(f"windows: {sum(len(e.windows) for e in entries)} in {len(entries)} apps")
    resolved = 0
    for entry in entries:
        ok = icons.resolved(entry.exe, entry.windows[0].hwnd if entry.windows else 0,
                            entry.name)
        resolved += ok
        print(f"  {'icon ' if ok else 'PLACE'} {entry.name:<24} "
              f"x{len(entry.windows)} exe={'yes' if entry.exe else 'DENIED'}")
        for window in entry.windows:
            print(f"        {window.title[:60]!r}")
    print(f"icons resolved {resolved}/{len(entries)}")

    print(f"gpu: {gpu_name() if gpu_available() else 'unavailable (readout hidden)'}")
    poller = Poller(config, lambda s: None)
    poller.start()
    from dock.metrics import format_sample

    for _ in range(3):
        time.sleep(config.num("poll_interval", 0.5, 10.0))
        print("  metrics:", format_sample(poller.sample()))
    poller.stop()


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    singleton.exit_if_running(APP, "The dock is already running.")
    lifetime = None
    for arg in sys.argv[1:]:
        if arg.startswith("--lifetime="):
            lifetime = float(arg.split("=", 1)[1])
    App(lifetime=lifetime).run()


if __name__ == "__main__":
    main()
