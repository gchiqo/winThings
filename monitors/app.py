#!/usr/bin/env python3
"""System monitors in the notification area.

Replaces the floating dock panel. Windows 11 removed deskbands, so nothing can
add content to the taskbar strip itself without shell-level patching — but the
notification area is part of the taskbar and takes as many icons as you like,
which is the same place GNOME's Vitals puts its readouts.

One tray icon per enabled metric, redrawn on every sample. Hovering any of them
shows the full readout, network included.

  python monitors\\app.py              run it
  python monitors\\app.py --settings   run it with the settings window open
  python monitors\\app.py --lifetime=N quit after N seconds
"""

import argparse
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import customtkinter as ctk
import pystray

from common import Config, autostart, singleton, theme, ui

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import trayicons
from metrics import Poller, format_rate, gpu_available, gpu_name

APP = "monitors"
APP_NAME = "WinThingsMonitors"

DEFAULTS = {
    "poll_interval": 2.0,
    "show_cpu": True,
    "show_ram": True,
    "show_gpu": True,
    "show_net_up": False,   # off by default: five tray icons is a lot
    "show_net_down": False,
    "autostart": False,
}

# key -> (tray label, config flag)
METRICS = [
    ("cpu", "CPU", "show_cpu"),
    ("ram", "RAM", "show_ram"),
    ("gpu", "GPU", "show_gpu"),
    ("net_up", "Upload", "show_net_up"),
    ("net_down", "Download", "show_net_down"),
]


class MetricIcon:
    """One notification-area icon, on its own pystray thread."""

    def __init__(self, key, label, on_settings, on_quit):
        self.key = key
        self.label = label
        self.color = theme.METRIC_COLORS[key]
        self.icon = pystray.Icon(
            f"{APP}_{key}",
            icon=self._render(None),
            title=label,
            menu=pystray.Menu(
                pystray.MenuItem("Settings", lambda *_: on_settings(), default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda *_: on_quit()),
            ),
        )
        self._thread = None

    def _render(self, value):
        if self.key in ("net_up", "net_down"):
            return trayicons.rate_icon(
                value or 0.0, self.color,
                "up" if self.key == "net_up" else "down")
        return trayicons.percent_icon(value, self.color)

    def start(self):
        self._thread = threading.Thread(target=self.icon.run, daemon=True,
                                        name=f"tray-{self.key}")
        self._thread.start()

    def update(self, value, tooltip):
        try:
            self.icon.icon = self._render(value)
            self.icon.title = tooltip
        except Exception:
            pass  # a dropped frame is not worth taking the app down for

    def stop(self):
        try:
            self.icon.stop()
        except Exception:
            pass


def tooltip_for(sample):
    """Every metric on one line, so any icon's hover tells the whole story."""
    parts = []
    for key, label, _flag in METRICS[:3]:
        if key in sample:
            value = sample[key]
            parts.append(f"{label} {'--' if value is None else f'{value:.0f}%'}")
    if "net_up" in sample:
        parts.append(f"Up {format_rate(sample['net_up'])}")
    if "net_down" in sample:
        parts.append(f"Down {format_rate(sample['net_down'])}")
    return "   ".join(parts)


class App:
    def __init__(self, lifetime=None):
        self.config = Config(APP, DEFAULTS)
        self.config["autostart"] = autostart.is_enabled(APP_NAME)

        # Hidden Tk root: settings needs a main thread, tray icons do not.
        self.root = ctk.CTk()
        self.root.withdraw()
        ui.pump(self.root)

        self.icons = []
        self.settings = None
        self.poller = Poller(self.config, self._on_sample)
        self._build_icons()
        self.config.subscribe(lambda _c: ui.call_on_ui_thread(self._rebuild))

        if lifetime:
            self.root.after(int(lifetime * 1000), self.quit)

    # -- tray icon set ----------------------------------------------------
    def enabled_keys(self):
        keys = []
        for key, _label, flag in METRICS:
            if not self.config[flag]:
                continue
            if key == "gpu" and not gpu_available():
                continue  # no NVIDIA/NVML: drop it rather than show a dash
            keys.append(key)
        return keys

    def _build_icons(self):
        for key, label, _flag in METRICS:
            if key not in self.enabled_keys():
                continue
            icon = MetricIcon(key, label, self.open_settings, self.quit)
            icon.start()
            self.icons.append(icon)

    def _rebuild(self):
        """Config changed which metrics are shown — swap the icon set out."""
        wanted = self.enabled_keys()
        if [i.key for i in self.icons] == wanted:
            return
        for icon in self.icons:
            icon.stop()
        self.icons = []
        self._build_icons()

    # -- sampling ---------------------------------------------------------
    def _on_sample(self, sample):
        tooltip = tooltip_for(sample)
        for icon in self.icons:
            icon.update(sample.get(icon.key), tooltip)

    # -- settings ---------------------------------------------------------
    def open_settings(self):
        ui.call_on_ui_thread(self._open_settings)

    def _open_settings(self):
        from settings import MonitorSettings

        if self.settings is None:
            self.settings = MonitorSettings(
                self.config, script_path=os.path.abspath(__file__))
        self.settings.show()

    # -- lifecycle --------------------------------------------------------
    def run(self):
        self.poller.start()
        self.root.mainloop()

    def quit(self):
        self.poller.stop()
        for icon in self.icons:
            icon.stop()
        try:
            if self.settings is not None:
                self.settings.destroy()
        except Exception:
            pass
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:
            pass


def selftest():
    """Print what would be shown, and three real samples. No icons, no window."""
    config = Config(APP, DEFAULTS)
    print("gpu:", gpu_name() if gpu_available() else "unavailable (readout hidden)")
    poller = Poller(config, lambda s: None)
    poller.start()
    import time

    for _ in range(3):
        time.sleep(config.num("poll_interval", 0.5, 10.0))
        sample = poller.sample()
        print("  ", tooltip_for(sample))
    poller.stop()

    for key, label, _flag in METRICS:
        img = (trayicons.rate_icon(2048, theme.METRIC_COLORS[key], "up")
               if key.startswith("net")
               else trayicons.percent_icon(42, theme.METRIC_COLORS[key]))
        print(f"   icon {label:<9} rendered {img.size[0]}x{img.size[1]}")


def main():
    parser = argparse.ArgumentParser(description="WinThings system monitors")
    parser.add_argument("--settings", action="store_true",
                        help="open the settings window at startup")
    parser.add_argument("--selftest", action="store_true",
                        help="headless check, then exit")
    parser.add_argument("--lifetime", type=float, help="quit after N seconds")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        return

    singleton.exit_if_running(APP)
    app = App(lifetime=args.lifetime)
    if args.settings:
        app.root.after(300, app.open_settings)
    app.run()


if __name__ == "__main__":
    main()
