"""Settings window, laid out like the original GTK one: voice and sound at the
top, reading options as checkboxes and radio buttons, the big Read button with
Test / Stop under it, then shortcuts, theme and where to get more voices.

Voices are listed by friendly name ("Ryan · English (US) · high quality"); the
config still stores the voice id. "Start with Windows" shows what Windows will
really do at sign-in, not just what the config says. The window follows the
"appearance" setting (system / light / dark).

The window is created once at startup and hidden rather than destroyed on close:
Tk insists on owning the main thread, and this app also runs a tray icon and a
hotkey thread, so there is exactly one root and it lives for the process.
"""

import os
import tkinter as tk
import webbrowser

import customtkinter as ctk

import bootstrap
import kokoro_tts
import speech
from common import theme
from common.hotkey import normalize, pretty
from common.ui import SettingsWindow

P = theme.pair

VOICE_KEYS = {"piper": "voice", "kokoro": "kokoro_voice"}
SHORTCUT_NAMES = {"hotkey": "Read / stop", "pause_hotkey": "Pause / continue"}
ENGINE_OPTIONS = [("piper", "Piper"), ("kokoro", "Kokoro")]
HIGHLIGHT_OPTIONS = [("off", "Off"), ("inplace", "In the text I selected"),
                     ("fullscreen", "Full screen")]
THEME_OPTIONS = [("system", "System"), ("light", "Light"), ("dark", "Dark")]

STARTUP_REFUSED = ("Windows has startup turned off for this app. Turn it on in "
                   "Settings › Apps › Startup.")
_VOICES_HINT = ("Download both files of a voice (.onnx and .onnx.json) into the "
                "voices folder, then open Settings again")

_LINKS = [
    ("Listen to voice samples", "https://rhasspy.github.io/piper-samples/#en_US-lessac-high"),
    ("Download voices",         "https://docs.gladecore.com/files/piper-voice-models"),
    ("Piper project (GPL) ↗",   "https://github.com/OHF-Voice/piper1-gpl"),
]

_LANGUAGES = {
    "en_US": "English (US)", "en_GB": "English (UK)", "ka_GE": "Georgian",
    "de_DE": "German", "fr_FR": "French", "es_ES": "Spanish (Spain)",
    "es_MX": "Spanish (Mexico)", "it_IT": "Italian", "pt_BR": "Portuguese (Brazil)",
    "pt_PT": "Portuguese (Portugal)", "nl_NL": "Dutch", "pl_PL": "Polish",
    "ru_RU": "Russian", "uk_UA": "Ukrainian", "tr_TR": "Turkish",
    "zh_CN": "Chinese", "ar_JO": "Arabic",
}
_VOICE_NAMES = {"libritts_r": "LibriTTS-R", "libritts": "LibriTTS"}


def voice_label(vid):
    """'en_US-ryan-high' -> 'Ryan · English (US) · high quality'. Ids that
    don't follow Piper's lang-name-quality pattern are shown as they are."""
    parts = vid.split("-")
    if len(parts) != 3:
        return vid
    lang, name, quality = parts
    name = _VOICE_NAMES.get(name, name.replace("_", " ").title())
    language = _LANGUAGES.get(lang, lang)
    return f"{name} · {language} · {quality.replace('_', ' ')} quality"


class ReaderSettings(SettingsWindow):
    APP = "reader"
    TITLE = "Read Selected"
    WIDTH = 580
    HEIGHT = 820
    APPEARANCE_KEY = "appearance"

    def __init__(self, config, controller):
        self.controller = controller
        self.engine_var = None
        self.voice_menu = None
        self.georgian_menu = None
        self.autostart_box = None
        self._ticking = False
        super().__init__(config, on_change=controller.on_config_change)
        # Must happen within 200 ms of creation, or customtkinter puts its own
        # default icon on the title bar and taskbar.
        try:
            self.iconbitmap(bootstrap.ICON_PATH)
        except tk.TclError:
            pass
        self.footer.pack_forget()  # the buttons sit among the rows, as in the GTK window
        self.protocol("WM_DELETE_WINDOW", self.hide)
        # The rows and the 1s status tick start on first show() (ensure_built /
        # _start_tick), so a session that never opens settings pays for neither.
        # Note: whether we start hidden is the caller's call. customtkinter's
        # CTk.mainloop() only deiconifies a window that was never withdrawn, so
        # withdraw()ing here would make an --settings launch come up invisible.

    # -- layout -----------------------------------------------------------
    def build(self, body):
        # The engine choice only exists where Kokoro is installed (not in the
        # Store build).
        if kokoro_tts.problem() is None:
            self.engine_var = self.add_radios("Engine", ENGINE_OPTIONS,
                                              self._engine(), self._engine_picked)
        self.voice_menu = self._add_voice_menu("Voice")
        if self.controller.georgian_voice_ids():
            self.georgian_menu = self._add_voice_menu("Georgian voice")
        self.add_slider("Speed", "speed", 0.5, 5.0, 0.1, "{:.1f}×")
        self.add_slider("Volume", "volume", 0.0, 1.5, 0.05, "{:.0%}")
        self.add_slider("Sentence pause", "sentence_silence", 0.0, 1.0, 0.05, "{:.2f} s")
        self.add_radios("Highlight words", HIGHLIGHT_OPTIONS, self._highlight_value(),
                        self._highlight_picked)
        if self.georgian_menu is not None:
            self.add_checkbox("Read Georgian text with the Georgian voice (auto-detect)",
                              "auto_language")
        self.autostart_box = self.add_checkbox("Start with Windows", "autostart",
                                               on_toggle=self._autostart_toggled)

        self.add_buttons(("▶   Read selection", self.controller.read_selection, "primary"),
                         height=42)
        self.add_buttons(("Test voice", self.controller.test_voice, None),
                         ("■   Stop", self.controller.stop, "danger"))

        self.add_separator()
        for key in ("hotkey", "pause_hotkey"):
            self.add_hotkey(SHORTCUT_NAMES[key], key,
                            on_set=lambda _spec: self.controller.rebind(),
                            validate=lambda spec, k=key: self._shortcut_taken(k, spec))
        self.add_radios("Theme", THEME_OPTIONS, self.config_obj["appearance"],
                        self._theme_picked)

        self.add_separator()
        self._add_more_voices()

    def _add_voice_menu(self, label):
        """A voice picker; refresh_voices() fills it with friendly names."""
        row = self._row(label)
        menu = ctk.CTkOptionMenu(
            row, values=["(none)"], dynamic_resizing=False,
            fg_color=P(theme.BG_ELEVATED), button_color=P(theme.BG_HOVER),
            button_hover_color=P(theme.BG_ACTIVE), text_color=P(theme.FG),
        )
        menu.grid(row=0, column=1, sticky="ew")
        return menu

    def _fill_voice_menu(self, menu, key, ids, current, friendly=True):
        by_label = {(voice_label(v) if friendly else v): v for v in ids}
        menu.configure(
            values=list(by_label) or ["(none)"],
            command=lambda label: (self._commit(key, by_label[label])
                                   if label in by_label else None))
        menu.set(next((label for label, v in by_label.items() if v == current), "(none)"))

    def _add_more_voices(self):
        row = self._row("More voices", _VOICES_HINT)
        ctk.CTkButton(
            row, text="Open voices folder", command=_open_user_voices,
            fg_color=P(theme.BG_ELEVATED), hover_color=P(theme.BG_HOVER),
            text_color=P(theme.FG), border_color=P(theme.BORDER), border_width=1,
        ).grid(row=0, column=1, sticky="w")
        links = ctk.CTkFrame(self.body, fg_color="transparent")
        links.pack(fill="x", padx=theme.PAD_SMALL, pady=(0, theme.PAD))
        for label, url in _LINKS:
            ctk.CTkButton(
                links, text=label, width=0,
                fg_color="transparent", hover_color=P(theme.BG_HOVER),
                text_color=P(theme.ACCENT), anchor="w", height=26,
                command=lambda u=url: webbrowser.open(u),
            ).pack(side="left", padx=(0, 6))

    # -- choices ----------------------------------------------------------
    def _highlight_value(self):
        if not self.config_obj["reading_mode"]:
            return "off"
        return "fullscreen" if self.config_obj["highlight_style"] == "fullscreen" else "inplace"

    def _highlight_picked(self, value):
        """One choice for what the config keeps as two keys."""
        if value != "off":
            self._commit("highlight_style", value)
        self._commit("reading_mode", value != "off")

    def _theme_picked(self, mode):
        self._commit("appearance", mode)
        ctk.set_appearance_mode(mode)

    def _autostart_toggled(self, on):
        if self.controller.set_autostart(on):
            return True
        self.flash(STARTUP_REFUSED if on else "Couldn't change startup", error=True)
        return False

    def _sync_autostart(self):
        """Show what Windows will actually do at sign-in."""
        actual = self.controller.autostart_enabled()
        if self.autostart_box is not None:
            self.autostart_box.var.set(actual)
        if bool(self.config_obj["autostart"]) != actual:
            self._commit("autostart", actual)

    def _shortcut_taken(self, key, spec):
        other = "pause_hotkey" if key == "hotkey" else "hotkey"
        if normalize(self.config_obj[other]) == spec:
            return f"{pretty(spec)} is already the {SHORTCUT_NAMES[other]} shortcut"
        return None

    # -- show / hide ------------------------------------------------------
    def show(self):
        self.ensure_built()
        self.refresh_voices()
        self._sync_autostart()
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

    # -- engine and voices ------------------------------------------------
    def _engine(self):
        return self.controller.engine.engine_name()

    def _engine_picked(self, name):
        self._commit("engine", name)
        self.refresh_voices()
        self.set_status(*self.controller.status())  # don't wait for the 1 s tick

    def refresh_voices(self):
        """Re-scan on show (and on an engine switch) so dropping in a voice
        doesn't need a restart."""
        if self.voice_menu is None:
            return
        name = self._engine()
        if self.engine_var is not None:
            self.engine_var.set(name)
        key = VOICE_KEYS[name]
        ids = self.controller.voice_ids(name)
        self._fill_voice_menu(self.voice_menu, key, ids,
                              _shown_voice(name, self.config_obj[key], ids),
                              friendly=name == "piper")
        if self.georgian_menu is not None:
            ka = self.controller.georgian_voice_ids()
            cur = self.config_obj["voice_georgian"]
            self._fill_voice_menu(self.georgian_menu, "voice_georgian", ka,
                                  cur if cur in ka else (ka[0] if ka else None))

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


def _open_user_voices():
    os.makedirs(speech.USER_VOICES_DIR, exist_ok=True)
    os.startfile(speech.USER_VOICES_DIR)


def _shown_voice(engine_name, current, ids):
    """The voice the engine will actually use, so the menu doesn't lie."""
    if not ids:
        return None
    if current in ids:
        return current
    if engine_name == "kokoro":
        return kokoro_tts.pick_voice(current, ids)
    return speech.PREFERRED_VOICE if speech.PREFERRED_VOICE in ids else ids[0]


def status_for(controller):
    """The status line: what is happening now and which key to press next."""
    read_key = pretty(controller.config["hotkey"])
    if controller.engine.is_paused():
        return (f"Paused — press {pretty(controller.config['pause_hotkey'])} "
                f"to continue", theme.WARN)
    if controller.engine.is_speaking():
        return f"Reading — press {read_key} to stop", theme.OK
    problem = controller.engine.problem()
    if problem:
        return problem, theme.WARN
    if not controller.hotkeys.bound("read"):
        error = controller.hotkeys.error("read") or "in use by another app"
        return (f"{read_key} isn't available ({error}) — choose another shortcut "
                f"or use Read selection", theme.ERROR)
    if not controller.hotkeys.bound("pause"):
        error = controller.hotkeys.error("pause") or "in use by another app"
        return (f"Pause shortcut isn't available ({error}) — choose another "
                f"below", theme.WARN)
    return f"Ready — select text in any app and press {read_key}", theme.OK
