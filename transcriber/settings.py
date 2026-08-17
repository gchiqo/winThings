"""Setup-side options for the transcriber: which model, which device, where to
save. The per-file choices (language, output formats) live in the main window;
this is the stuff you set once.
"""

import os

from common import autostart, theme
from common.ui import SettingsWindow

from engine import MODELS, cuda_present

APP_NAME = "WinThingsTranscriber"

HINT_MODEL = ("Bigger = more accurate (esp. Georgian) but slower and more VRAM. "
              "medium is a good balance; large-v3 is best on the GPU.")
HINT_DEVICE = "auto uses the GPU when it's usable, else the CPU"
HINT_VAD = "Skip silent stretches — faster, and avoids invented text in silence"
HINT_OUTDIR = "Where to save output files. Blank = next to the audio file"


class TranscriberSettings(SettingsWindow):
    APP = "transcriber"
    TITLE = "Transcriber Settings"
    WIDTH = 520

    def __init__(self, config, controller):
        self.controller = controller
        super().__init__(config)
        self.protocol("WM_DELETE_WINDOW", self.hide)

    def build(self, body):
        self.add_section("Model")
        self.add_dropdown("Model", "model", MODELS, hint=HINT_MODEL)
        devices = ["auto", "cuda", "cpu"] if cuda_present() else ["cpu"]
        self.add_dropdown("Device", "device", devices, hint=HINT_DEVICE)
        gpu = "GPU detected" if cuda_present() else "no CUDA GPU detected — CPU only"
        self.set_status(gpu, theme.OK if cuda_present() else theme.WARN)

        self.add_section("Quality")
        self.add_switch("Skip silence (VAD)", "vad_filter", hint=HINT_VAD)
        self.add_slider("Beam size", "beam_size", 1, 10, 1, "{:.0f}",
                        hint="Higher can be more accurate but slower")

        self.add_section("Output")
        self.add_entry("Save to folder", "output_dir", hint=HINT_OUTDIR)

        self.add_section("Startup")
        self.config_obj["autostart"] = autostart.is_enabled(APP_NAME)
        self.add_switch("Start with Windows", "autostart",
                        on_toggle=self.set_autostart)

        self.add_button("Close", self.hide, primary=True)

    def set_autostart(self, enabled):
        autostart.set_enabled(APP_NAME, self.controller.script_path, enabled)

    def show(self):
        self.ensure_built()
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide(self):
        self.withdraw()
