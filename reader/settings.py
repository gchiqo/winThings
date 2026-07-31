"""Settings window — customtkinter replacement for the GTK piper-read-gui.py.

Same feature set as the GTK version: voice picker, speed/volume/pause sliders,
the shortcut, Test / Read selection / Stop, and the live status dot that told
you at a glance whether the hotkey was actually bound. The layout comes from
common.ui.SettingsWindow so it matches the other WinThings apps.

The window is created once at startup and hidden rather than destroyed on close:
Tk insists on owning the main thread, and this app also runs a tray icon and a
keyboard hook, so there is exactly one root and it lives for the process.
"""

import bootstrap  # noqa: F401
from common import theme
from common.hotkey import pretty
from common.ui import SettingsWindow

HINT_VOICE = "Piper .onnx voices found in reader\\voices"
HINT_SPEED = "Multiplier; Piper's length_scale is the inverse of this"
HINT_HOTKEY = "Press once to read the selection, again to stop"
HINT_READING = "Fullscreen overlay that highlights each word as it is read"


class ReaderSettings(SettingsWindow):
    APP = "reader"
    TITLE = "Piper Read Aloud"
    WIDTH = 520

    def __init__(self, config, controller):
        self.controller = controller
        self.voice_menu = None
        self._ticking = False
        super().__init__(config, on_change=controller.on_config_change)
        self.protocol("WM_DELETE_WINDOW", self.hide)
        # The rows and the 1s status tick start on first show() (ensure_built /
        # _start_tick), so a session that never opens settings pays for neither.
        # Note: whether we start hidden is the caller's call. customtkinter's
        # CTk.mainloop() only deiconifies a window that was never withdrawn, so
        # withdraw()ing here would make an --settings launch come up invisible.

    # -- layout -----------------------------------------------------------
    def build(self, body):
        self.add_section("Voice")
        self.voice_menu = self.add_dropdown(
            "Voice", "voice", self.controller.voice_ids(), hint=HINT_VOICE)
        self.add_slider("Speed", "speed", 0.5, 5.0, 0.1, "{:.1f}x", hint=HINT_SPEED)
        self.add_slider("Volume", "volume", 0.0, 1.5, 0.05, "{:.0%}")
        self.add_slider("Pause between sentences", "sentence_silence",
                        0.0, 1.0, 0.05, "{:.2f}s")

        self.add_section("Shortcut")
        self.add_hotkey("Read selection", "hotkey", hint=HINT_HOTKEY,
                        on_set=lambda _spec: self.controller.rebind())

        self.add_section("Behaviour")
        self.add_switch("Reading mode", "reading_mode", hint=HINT_READING)
        self.add_switch("Start with Windows", "autostart",
                        on_toggle=self.controller.set_autostart)

        self.add_button("Read selection", self.controller.read_selection, primary=True)
        self.add_button("Test voice", self.controller.test_voice)
        self.add_button("Stop", self.controller.stop, danger=True)

    # -- show / hide ------------------------------------------------------
    def show(self):
        self.ensure_built()
        self.refresh_voices()
        self.deiconify()
        self.lift()
        self.focus_force()
        self._start_tick()

    def hide(self):
        self.withdraw()  # _tick sees it's no longer viewable and stops itself

    def _start_tick(self):
        if not self._ticking:
            self._ticking = True
            self._tick()

    def refresh_voices(self):
        """Re-scan on show so dropping a voice in doesn't need a restart."""
        if self.voice_menu is None:
            return
        ids = self.controller.voice_ids()
        self.voice_menu.configure(values=ids or ["(none)"])
        current = self.config_obj["voice"]
        self.voice_menu.set(current if current in ids else (ids[0] if ids else "(none)"))

    # -- live status ------------------------------------------------------
    def _tick(self):
        # Only runs while the window is visible; hide() lets it lapse and show()
        # restarts it, so there is no 1-second wakeup during the long stretches
        # the settings window sits closed.
        if not self.winfo_viewable():
            self._ticking = False
            return
        self.set_status(*self.controller.status())
        self.after(1000, self._tick)


def status_for(controller):
    """The status line, in the same precedence order the GTK version used."""
    if controller.engine.is_speaking():
        return "Reading…  (press Stop or the shortcut to interrupt)", theme.OK
    problem = controller.engine.problem()
    if problem:
        return problem, theme.WARN
    if not controller.hotkeys.bound("read"):
        error = controller.hotkeys.error("read") or "not bound"
        return f"Hotkey not bound ({error}) — use the Read selection button", theme.ERROR
    return f"Ready  ·  hotkey: {pretty(controller.hotkeys.spec('read'))}", theme.OK
