"""Settings window for the tray monitors.

Which readouts appear, how often they sample, and whether it starts with
Windows. Everything applies immediately — the icon set is rebuilt from the
config subscription in app.py, so there is no apply button.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import autostart, theme
from common.ui import SettingsWindow

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metrics import gpu_available, gpu_name

APP_NAME = "WinThingsMonitors"

HINT_GPU_OK = "NVIDIA only. On Optimus laptops the card parks, so this refreshes ~10s"
HINT_GPU_NONE = "No NVIDIA GPU or NVML unavailable - this readout stays hidden"
HINT_NET = "Adds another tray icon; the rate is in every icon's tooltip anyway"
HINT_POLL = "Seconds between samples"


class MonitorSettings(SettingsWindow):
    APP = "monitors"
    TITLE = "System Monitors"
    WIDTH = 480

    def __init__(self, config, script_path):
        self.script_path = script_path
        super().__init__(config)
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self._tick()

    def build(self, body):
        self.add_section("Readouts")
        self.add_switch("CPU", "show_cpu")
        self.add_switch("RAM", "show_ram")
        self.add_switch(
            "GPU", "show_gpu",
            hint=HINT_GPU_OK if gpu_available() else HINT_GPU_NONE)
        self.add_switch("Upload", "show_net_up", hint=HINT_NET)
        self.add_switch("Download", "show_net_down", hint=HINT_NET)

        self.add_section("Sampling")
        self.add_slider("Poll interval", "poll_interval", 0.5, 10.0, 0.5,
                        "{:.1f}s", hint=HINT_POLL)

        self.add_section("Startup")
        self.add_switch("Start with Windows", "autostart",
                        on_toggle=self.set_autostart)

        self.add_button("Close", self.hide, primary=True)

    def set_autostart(self, enabled):
        autostart.set_enabled(APP_NAME, self.script_path, enabled)

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide(self):
        self.withdraw()

    def _tick(self):
        name = gpu_name()
        if name:
            self.set_status(f"Running  -  GPU: {name}", theme.OK)
        else:
            self.set_status("Running  -  no NVIDIA GPU detected", theme.WARN)
        self.after(5000, self._tick)
