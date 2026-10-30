"""Read the selected text aloud, anywhere in Windows, with Piper or Kokoro voices.

Windows port of the Linux/GNOME read-aloud.py. Press the hotkey (default
Ctrl+Shift+A) to read whatever is selected in any app; press it again to stop.
The text-cleaning and chunking that made Piper behave (see speech.py) is carried
over unchanged; everything OS-facing is new:

  selection   X11 PRIMARY  ->  UI Automation TextPattern, else Ctrl+C + restore
  hotkey      gsettings    ->  common.hotkey / the `keyboard` low-level hook
  playback    pw-play pipe ->  sounddevice, in-process (no ffmpeg on this box)
  stop        killpg       ->  worker thread + threading.Event (see engine.py)
  settings    GTK          ->  customtkinter via common.ui

Unlike the Linux version this is one long-lived tray process rather than a
script fired per keypress, because the keyboard hook and the audio stream both
have to live somewhere. The CLI kept from the Linux version therefore talks to
that process over a loopback socket (ipc.py) instead of a PID file.

  app.py                    run the tray app (this is the entry point)
  app.py --settings         run it with the settings window open
  app.py --stop             stop any current playback
  app.py --read             read the current selection
  app.py --text "..."       read the given text
  app.py --voice ID --speed 1.2
  app.py --list-voices
"""

import argparse
import logging
import logging.handlers
import sys
import threading
import time

import bootstrap  # noqa: F401  (repo root on sys.path)

import engine
import foreground
import ipc
import kokoro_tts
import overlay
import selection
import speech
from settings import ReaderSettings, status_for

from common import autostart, singleton, theme
from common.config import Config
from common.hotkey import HotkeyManager
from common.paths import log_path
from common import tray as tray_mod
from common.tray import Tray
from common.ui import call_on_ui_thread

APP = "reader"

log = logging.getLogger(APP)


def _is_georgian_voice(vid):
    return vid.startswith("ka_") or vid.startswith("ka-")

DEFAULTS = {
    "engine": "piper",           # "piper" or "kokoro" (see engine.py)
    "voice": "",                 # Piper default/English voice; "" -> preferred, else first
    "kokoro_voice": "",          # Kokoro voice; "" -> af_heart, else first
    "voice_georgian": "",        # Piper voice for Georgian text (both engines); "" -> first ka_
    "auto_language": True,       # detect Georgian per line and switch voice
    "speed": 1.0,                # multiplier; piper wants its inverse
    "volume": 1.0,               # applied to the PCM ourselves
    "sentence_silence": 0.2,     # seconds of silence between sentences
    "reading_mode": False,       # fullscreen word-highlighting overlay
    "hotkey": "ctrl+shift+a",
    "autostart": False,
    "voices_dir": "",            # blank -> reader\voices
    "piper_exe": "",             # blank -> reader\piper\piper.exe, then PATH
    # Silence padding to mask Bluetooth link latency (see engine.py). Raise
    # lead_in_silence if the first word still garbles on a slow-waking headset.
    "lead_in_silence": 0.8,
    "tail_out_silence": 0.6,
    # Piper randomness. Lower than piper's own 0.667 / 0.8 to stop the occasional
    # garbled/mumbled render; lower still (e.g. 0.2) for maximum consistency, or
    # raise toward the defaults for more natural variation.
    "noise_scale": 0.5,
    "noise_w": 0.4,
}

TEST_PHRASE = "This is a test of the selected voice."


class ReaderApp:
    """Wires config, engine, hotkey, tray, overlay and settings together."""

    def __init__(self, config):
        self.config = config
        self.engine = engine.Engine(config, on_state=self._on_state)
        self.hotkeys = HotkeyManager()
        self.tracker = foreground.Tracker()
        self.control = ipc.ControlServer(APP, self._on_command)
        self.tray = None
        self.window = None
        self.overlay = None
        self._overlay_t0 = 0.0
        self._engine_name = self.engine.engine_name()

    # -- lifecycle --------------------------------------------------------
    def start(self, open_settings=False):
        selection.warm_up()  # comtypes + UIA object, off the hotkey path
        if self._engine_name == "kokoro":
            kokoro_tts.warm_up()  # model load, so the first read doesn't wait
        self.tracker.start()
        self.control.start()
        self.rebind()
        tray_mod.set_app_id("WinThings.Reader")
        self.tray = (
            Tray(APP, "Read Selected", "reader", theme.ACCENT)
            .add("Read selection", self.read_selection, default=True)
            .add("Stop", self.stop)
            .add("Settings", self.open_settings)
            .separator()
            .add("Quit", self.quit)
            .start()
        )
        self.window = ReaderSettings(self.config, self)
        if open_settings:
            self.window.after(0, self.window.show)
        else:
            self.window.withdraw()
        return self

    def shutdown(self):
        self.hotkeys.unbind_all()
        self.control.stop()
        self.tracker.stop()
        self.engine.stop()
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass

    def quit(self):
        call_on_ui_thread(self.window.quit)

    def open_settings(self):
        call_on_ui_thread(self.window.show)

    # -- config -----------------------------------------------------------
    def voice_ids(self, engine_name=None):
        """Voices for the main picker: Kokoro's, or Piper's non-Georgian ones."""
        if (engine_name or self.engine.engine_name()) == "kokoro":
            return kokoro_tts.voices()
        return [v for v in self.engine.voices() if not _is_georgian_voice(v)]

    def georgian_voice_ids(self):
        return [v for v in self.engine.voices() if _is_georgian_voice(v)]

    def rebind(self):
        """(Re)register the global hotkey. Suppressed so the focused app doesn't
        also act on the combo — otherwise e.g. an app's own Ctrl+Shift+A could
        change the selection out from under the Ctrl+C we are about to send."""
        self.hotkeys.bind("read", self.config["hotkey"], self.toggle, suppress=True)

    def set_autostart(self, enabled):
        autostart.set_enabled("WinThingsReader", __file__, enabled)

    def on_config_change(self, _config):
        name = self.engine.engine_name()
        if name != self._engine_name:
            self._engine_name = name
            # Load Kokoro's model as soon as it is picked; free it (~350 MB)
            # when switching back to Piper.
            if name == "kokoro":
                kokoro_tts.warm_up()
            else:
                kokoro_tts.unload()
        if self.tray is not None:
            self.tray.set_tooltip(f"Read Selected — {status_for(self)[0]}")

    def status(self):
        return status_for(self)

    # -- actions ----------------------------------------------------------
    def toggle(self):
        """Hotkey callback (runs on the hotkey thread): read, or stop."""
        try:
            if self.stop():
                return
            self._in_background(self._read_job, False)
        except Exception:
            log.exception("toggle failed")

    def read_selection(self):
        """Same, but started from our own UI, so focus has to go back first."""
        self._in_background(self._read_job, True)

    def stop(self):
        stopped = self.engine.stop()
        if self.overlay is not None:
            call_on_ui_thread(self.close_overlay)
            stopped = True
        return stopped

    def test_voice(self):
        self._in_background(self.speak, TEST_PHRASE)

    def speak(self, raw, voice=None, speed=None):
        text = speech.prepare_text(raw or "")
        if not text:
            return False
        if self.config["reading_mode"] and self.window is not None:
            call_on_ui_thread(self._start_reading_mode, text, voice, speed)
            return True
        ok, detail = self.engine.speak(text, voice=voice, speed=speed)
        if not ok:
            self.notify(detail)
        return ok

    def _read_job(self, restore_focus):
        if restore_focus:
            self.tracker.restore()
        raw = selection.capture()
        if raw.strip():
            self.speak(raw)

    def _in_background(self, fn, *args):
        """Selection capture sleeps and playback blocks; keep both off the UI,
        tray and hotkey threads. Exceptions are logged, never swallowed silently:
        a daemon thread dying quietly is how a 'crash' hides."""
        def guarded():
            try:
                fn(*args)
            except Exception:
                log.exception("background job %s failed", getattr(fn, "__name__", fn))

        threading.Thread(target=guarded, daemon=True).start()

    def notify(self, message):
        if self.tray is not None and message:
            self.tray.notify(str(message))
        elif message:
            print(message, file=sys.stderr)

    def _on_state(self, state, detail):
        if state == "error":
            self.notify(detail)

    # -- reading mode (all of this runs on the Tk thread) -----------------
    def _start_reading_mode(self, text, voice, speed):
        self.close_overlay()
        self.overlay = overlay.Overlay(self.window, text, on_close=self._overlay_closed)
        ok, detail = self.engine.read_aloud(
            text, voice=voice, speed=speed,
            on_ready=lambda seconds: call_on_ui_thread(self._overlay_begin, seconds),
            on_done=lambda: call_on_ui_thread(self.close_overlay),
        )
        if not ok:
            self.close_overlay()
            self.notify(detail)

    def _overlay_begin(self, duration):
        if self.overlay is None:
            return
        self.overlay.begin(duration)
        self._overlay_t0 = time.monotonic()
        self.window.after(overlay.TICK_MS, self._overlay_tick)

    def _overlay_tick(self):
        if self.overlay is None or self.overlay.closed:
            return
        if not self.overlay.set_progress(time.monotonic() - self._overlay_t0):
            self.close_overlay()
            return
        self.window.after(overlay.TICK_MS, self._overlay_tick)

    def close_overlay(self):
        current, self.overlay = self.overlay, None
        if current is not None:
            current.close()

    def _overlay_closed(self):
        """The overlay was dismissed by the user — silence the audio too."""
        self.overlay = None
        if self.engine.is_speaking():
            self._in_background(self.engine.stop)

    # -- CLI channel ------------------------------------------------------
    def _on_command(self, cmd, payload):
        if cmd == "ping":
            return "ok"
        if cmd == "stop":
            return "stopped" if self.stop() else "idle"
        if cmd == "read":
            self._in_background(self._read_job, False)
            return "reading"
        if cmd == "text":
            self._in_background(self.speak, payload.get("text", ""),
                                payload.get("voice"), payload.get("speed"))
            return "reading"
        if cmd == "voices":
            return self.voice_ids()
        if cmd == "settings":
            self.open_settings()
            return "shown"
        if cmd == "quit":
            self.quit()
            return "quitting"
        return f"unknown command: {cmd}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def speak_standalone(config, text, voice=None, speed=None):
    """No tray app running: synthesise here and wait for it to finish."""
    prepared = speech.prepare_text(text or "")
    if not prepared:
        return 0
    ok, detail = engine.Engine(config).speak(
        prepared, voice=voice, speed=speed, blocking=True)
    if not ok:
        print(detail, file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="app.py", description="Read the selected text aloud with Piper.")
    ap.add_argument("--stop", action="store_true", help="stop any current playback")
    ap.add_argument("--read", action="store_true", help="read the current selection")
    ap.add_argument("--text", help="read the given text")
    ap.add_argument("--voice", help="voice id (see --list-voices)")
    ap.add_argument("--speed", type=float, help="speed multiplier, 0.5-5.0")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--settings", action="store_true", help="open the settings window")
    args = ap.parse_args(argv)

    config = Config(APP, DEFAULTS)

    if args.list_voices:
        for vid in speech.discover_voices(config["voices_dir"]):
            print(f"piper   {vid}")
        for vid in kokoro_tts.voices():
            print(f"kokoro  {vid}")
        return 0

    if args.stop:
        ipc.send(APP, "stop")
        return 0

    if args.text is not None:
        if ipc.send(APP, "text", text=args.text,
                    voice=args.voice, speed=args.speed) is None:
            return speak_standalone(config, args.text, args.voice, args.speed)
        return 0

    if args.read:
        if ipc.send(APP, "read") is None:
            return speak_standalone(config, selection.capture(), args.voice, args.speed)
        return 0

    # Tray mode. A second launch hands its request to the first instance rather
    # than stacking a duplicate tray icon and a duplicate keyboard hook.
    if not singleton.acquire(APP):
        ipc.send(APP, "settings")
        print("Read Selected is already running.", file=sys.stderr)
        return 0

    # File logging so a crash leaves a trace: "it crashed" with nothing on disk
    # is undiagnosable, and this runs under pythonw with no console to catch a
    # traceback.
    logging.basicConfig(
        handlers=[logging.handlers.RotatingFileHandler(
            log_path(APP), maxBytes=1_000_000, backupCount=1, encoding="utf-8")],
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    )
    log.info("reader starting")

    app = ReaderApp(config)
    app.start(open_settings=args.settings)
    try:
        app.window.mainloop()
    except Exception:
        log.exception("reader crashed in mainloop")
        raise
    finally:
        log.info("reader shutting down")
        app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
