#!/usr/bin/env python3
"""Audio-to-text: give it a recording, get the transcript.

Local and free via faster-whisper (Whisper), multilingual — English, Georgian
and more. The main window is the app; a tray icon reopens it and quits. A path
on the command line (Open with / Send to) loads that file straight away.

  app.py                 open the window
  app.py <audio-file>    open and load that file
  app.py --settings      open with the settings window showing
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common import autostart, singleton, theme  # noqa: E402
from common.config import Config  # noqa: E402
from common.paths import log_path  # noqa: E402
from common import tray as tray_mod  # noqa: E402
from common.tray import Tray  # noqa: E402
from common.ui import call_on_ui_thread  # noqa: E402

import engine as engine_mod  # noqa: E402
from settings import TranscriberSettings  # noqa: E402
from window import TranscriberWindow  # noqa: E402

APP = "transcriber"
log = logging.getLogger(APP)

DEFAULTS = {
    "model": "medium",           # tiny/base/small/medium/large-v3
    "device": "auto",            # auto/cuda/cpu
    "compute_type": "",          # "" = int8_float16 on GPU / int8 on CPU
    "language": "",              # "" = auto-detect
    "vad_filter": True,
    "beam_size": 5,
    "output_dir": "",            # "" = next to the audio
    "out_text": True,            # which formats the checkboxes default to
    "out_timestamped": False,
    "out_srt": False,
    "out_vtt": False,
    "autostart": False,
}


class TranscriberApp:
    def __init__(self, config):
        self.config = config
        self.engine = engine_mod.Engine(config)
        self.script_path = os.path.abspath(__file__)
        self.window = TranscriberWindow(
            config, self.engine, on_settings=self.open_settings, on_quit=self.quit)
        self.settings = None
        self.tray = None

    def start(self, initial_file=None, open_settings=False):
        tray_mod.set_app_id("WinThings.Transcriber")
        self.tray = (
            Tray(APP, "Audio to Text", "transcriber", theme.ACCENT)
            .add("Open", self.show, default=True)
            .add("Settings", self.open_settings)
            .separator()
            .add("Quit", self.quit)
            .start()
        )
        if initial_file:
            self.window.set_file(initial_file)
        if open_settings:
            self.window.after(200, self.open_settings)
        return self

    def show(self):
        call_on_ui_thread(self.window.show)

    def open_settings(self):
        if self.settings is None:
            self.settings = TranscriberSettings(self.config, self)
        self.settings.show()

    def quit(self):
        call_on_ui_thread(self.window.quit)

    def shutdown(self):
        if self.tray is not None:
            self.tray.stop()
        for w in (self.settings, self.window):
            try:
                if w is not None:
                    w.destroy()
            except Exception:
                pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog="app.py", description="Audio to text (Whisper).")
    ap.add_argument("file", nargs="?", help="audio/video file to load")
    ap.add_argument("--settings", action="store_true", help="open settings on start")
    args = ap.parse_args(argv)

    config = Config(APP, DEFAULTS)

    if not singleton.acquire(APP):
        # a second launch just hands its file to... nothing yet; for now, note it
        print("Audio to Text is already running.", file=sys.stderr)
        return 0

    logging.basicConfig(
        filename=log_path(APP), filemode="a", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
    log.info("transcriber starting")

    app = TranscriberApp(config)
    app.start(initial_file=args.file, open_settings=args.settings)
    try:
        app.window.mainloop()
    except Exception:
        log.exception("transcriber crashed in mainloop")
        raise
    finally:
        log.info("transcriber shutting down")
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
