"""Settings window — customtkinter replacement for the GTK piper-read-gui.py.

Same feature set as the GTK version: voice picker, speed/volume/pause sliders,
the shortcut, Test / Read selection / Stop, and the live status dot that told
you at a glance whether the hotkey was actually bound. The layout comes from
common.ui.SettingsWindow so it matches the other WinThings apps. On top of
that: the Engine picker (Piper / Kokoro), which swaps the voice list.

The window is created once at startup and hidden rather than destroyed on close:
Tk insists on owning the main thread, and this app also runs a tray icon and a
keyboard hook, so there is exactly one root and it lives for the process.
"""

import customtkinter as ctk

import bootstrap  # noqa: F401
import kokoro_tts
import speech
from common import theme
from common.hotkey import pretty
from common.ui import SettingsWindow

ENGINE_LABELS = {"piper": "Piper", "kokoro": "Kokoro"}
KOKORO_UNAVAILABLE = "Kokoro (not installed)"
VOICE_KEYS = {"piper": "voice", "kokoro": "kokoro_voice"}

HINT_ENGINE = ("Piper: quick, and has the Georgian voice. Kokoro: more natural "
               "English; Georgian lines still go to Piper")
HINT_VOICE = {
    "piper": "Piper .onnx voices found in reader\\voices",
    "kokoro": ("From reader\\kokoro\\voices-v1.0.bin. First letter is the language "
               "(a = US English, b = UK English), second is f / m"),
}
HINT_GEORGIAN = ("Piper voice for Georgian text when auto-detect is on — in Kokoro "
                 "mode too, since Kokoro has no Georgian")
HINT_AUTOLANG = "Detect Georgian per line and read it with the Georgian voice"
HINT_SPEED = ("Multiplier. Piper's length_scale is its inverse; Kokoro takes "
              "0.5–2.0x and caps anything faster")
HINT_HOTKEY = "Press once to read the selection, again to stop"
HINT_READING = "Fullscreen overlay that highlights each word as it is read"


class ReaderSettings(SettingsWindow):
    APP = "reader"
    TITLE = "Piper Read Aloud"
    WIDTH = 520

    def __init__(self, config, controller):
        self.controller = controller
        self.engine_menu = None
        self.voice_menu = None
        self.georgian_menu = None
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
        self.engine_menu = self._add_engine_menu()
        name = self._engine()
        self.voice_menu = self.add_dropdown(
            "Voice", VOICE_KEYS[name], self.controller.voice_ids(name),
            hint=HINT_VOICE[name])
        georgian = self.controller.georgian_voice_ids()
        if georgian:
            self.georgian_menu = self.add_dropdown(
                "Georgian voice", "voice_georgian", georgian, hint=HINT_GEORGIAN)
            self.add_switch("Auto-detect Georgian", "auto_language",
                            hint=HINT_AUTOLANG)
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

    # -- engine -----------------------------------------------------------
    def _engine(self):
        return self.controller.engine.engine_name()

    def _add_engine_menu(self):
        """Engine picker. Not add_dropdown(): the menu shows labels, the config
        stores "piper"/"kokoro", and picking a Kokoro that isn't installed must
        be refused rather than committed."""
        row = self._row("Engine", HINT_ENGINE)
        menu = ctk.CTkOptionMenu(
            row, values=list(ENGINE_LABELS.values()), command=self._engine_picked,
            fg_color=theme.BG_ELEVATED, button_color=theme.BG_HOVER,
            button_hover_color=theme.BG_ACTIVE, text_color=theme.FG,
        )
        menu.grid(row=0, column=1, sticky="ew")
        return menu

    def _engine_picked(self, label):
        name = "kokoro" if label in ("Kokoro", KOKORO_UNAVAILABLE) else "piper"
        problem = kokoro_tts.problem() if name == "kokoro" else None
        if problem:
            # The hint under the menu already carries the download instructions.
            self.flash("Kokoro isn't installed — see below Engine", error=True)
            self.refresh_voices()  # puts the menu back on the engine in use
            return
        self._commit("engine", name)
        self.refresh_voices()
        self.set_status(*self.controller.status())  # don't wait for the 1 s tick

    def refresh_voices(self):
        """Re-scan on show (and on an engine switch) so dropping in a voice or
        the Kokoro files doesn't need a restart."""
        if self.voice_menu is None:
            return
        name = self._engine()
        problem = kokoro_tts.problem()
        kokoro_label = KOKORO_UNAVAILABLE if problem else ENGINE_LABELS["kokoro"]
        self.engine_menu.configure(values=[ENGINE_LABELS["piper"], kokoro_label])
        self.engine_menu.set(kokoro_label if name == "kokoro" else ENGINE_LABELS["piper"])
        _set_hint(self.engine_menu, problem or HINT_ENGINE)

        key = VOICE_KEYS[name]
        ids = self.controller.voice_ids(name)
        self.voice_menu.configure(values=ids or ["(none)"],
                                  command=lambda v, k=key: self._commit(k, v))
        self.voice_menu.set(_shown_voice(name, self.config_obj[key], ids))
        _set_hint(self.voice_menu, HINT_VOICE[name])
        if self.georgian_menu is not None:
            ka = self.controller.georgian_voice_ids()
            self.georgian_menu.configure(values=ka or ["(none)"])
            cur = self.config_obj["voice_georgian"]
            self.georgian_menu.set(cur if cur in ka else (ka[0] if ka else "(none)"))

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


def _set_hint(control, text):
    """Reword the hint under a control's Row (common.ui.Row.hint_label)."""
    label = getattr(control.master, "hint_label", None)
    if label is not None:
        label.configure(text=text)


def _shown_voice(engine_name, current, ids):
    """The voice the engine will actually use, so the menu doesn't lie."""
    if not ids:
        return "(none)"
    if current in ids:
        return current
    if engine_name == "kokoro":
        return kokoro_tts.pick_voice(current, ids)
    return speech.PREFERRED_VOICE if speech.PREFERRED_VOICE in ids else ids[0]


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
    engine_label = ENGINE_LABELS[controller.engine.engine_name()]
    return (f"Ready  ·  {engine_label}  ·  hotkey: "
            f"{pretty(controller.hotkeys.spec('read'))}", theme.OK)
