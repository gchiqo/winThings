"""Settings window: voice, the two shortcuts, how reading behaves, startup, and
where to get more voices.

Laid out by what people come here to do rather than by subsystem: pick and
tune the voice, learn or change the keys, adjust reading. Voices are listed by
friendly name ("Ryan · English (US) · high quality"); the config still stores
the voice id. The layout comes from common.ui.SettingsWindow so it matches the
other WinThings apps.

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

ENGINE_LABELS = {"piper": "Piper", "kokoro": "Kokoro"}
KOKORO_UNAVAILABLE = "Kokoro (not installed)"
VOICE_KEYS = {"piper": "voice", "kokoro": "kokoro_voice"}
SHORTCUT_NAMES = {"hotkey": "Read / stop", "pause_hotkey": "Pause / continue"}

HINT_ENGINE = ("Piper: quick, and has the Georgian voice. Kokoro: more natural "
               "English; Georgian lines still go to Piper")
HINT_VOICE = {
    "piper": "Want another voice or language? See More voices below",
    "kokoro": ("From reader\\kokoro\\voices-v1.0.bin. First letter is the language "
               "(a = US English, b = UK English), second is f / m"),
}
HINT_SPEED = "1.0× is normal speed"
HINT_READ_KEY = "Select text in any app, then press this. Press it again to stop"
HINT_PAUSE_KEY = "Pause in the middle and continue from the same word"
HINT_HIGHLIGHT = ("Marks each word as it is spoken. Apps that can't say where their "
                  "text is get the full-screen view instead")
HIGHLIGHT_CHOICES = {  # label -> (reading_mode, highlight_style)
    "Off": (False, None),
    "In the text I selected": (True, "inplace"),
    "Full screen": (True, "fullscreen"),
}
HINT_SILENCE = "Extra silence after each sentence"
HINT_AUTOLANG = "Georgian text in a selection is read with the Georgian voice"

_VOICES_HINT = (
    "Listen to the samples, download both files of a voice you like (.onnx and "
    ".onnx.json) and put them in your voices folder. Open Settings again to see "
    "it in the list — no restart needed."
)

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
    HEIGHT = 760

    def __init__(self, config, controller):
        self.controller = controller
        self.engine_menu = None
        self.voice_menu = None
        self.georgian_menu = None
        self._ticking = False
        super().__init__(config, on_change=controller.on_config_change)
        # Must happen within 200 ms of creation, or customtkinter puts its own
        # default icon on the title bar and taskbar.
        try:
            self.iconbitmap(bootstrap.ICON_PATH)
        except tk.TclError:
            pass
        self.protocol("WM_DELETE_WINDOW", self.hide)
        # The rows and the 1s status tick start on first show() (ensure_built /
        # _start_tick), so a session that never opens settings pays for neither.
        # Note: whether we start hidden is the caller's call. customtkinter's
        # CTk.mainloop() only deiconifies a window that was never withdrawn, so
        # withdraw()ing here would make an --settings launch come up invisible.

    # -- layout -----------------------------------------------------------
    def build(self, body):
        self.add_section("Voice")
        # Only offer the engine picker when Kokoro is actually installed; in the
        # Store build there is nothing to switch to.
        if kokoro_tts.problem() is None:
            self.engine_menu = self._add_engine_menu()
        self.voice_menu = self._add_voice_menu("Voice", HINT_VOICE[self._engine()])
        self.add_slider("Speed", "speed", 0.5, 5.0, 0.1, "{:.1f}×", hint=HINT_SPEED)
        self.add_slider("Volume", "volume", 0.0, 1.5, 0.05, "{:.0%}")

        self.add_section("Shortcuts")
        for key, hint in (("hotkey", HINT_READ_KEY), ("pause_hotkey", HINT_PAUSE_KEY)):
            self.add_hotkey(SHORTCUT_NAMES[key], key, hint=hint,
                            on_set=lambda _spec: self.controller.rebind(),
                            validate=lambda spec, k=key: self._shortcut_taken(k, spec))

        self.add_section("While reading")
        self._add_highlight_menu()
        self.add_slider("Pause between sentences", "sentence_silence",
                        0.0, 1.0, 0.05, "{:.2f} s", hint=HINT_SILENCE)
        if self.controller.georgian_voice_ids():
            self.add_switch("Switch voice for Georgian", "auto_language",
                            hint=HINT_AUTOLANG)
            self.georgian_menu = self._add_voice_menu("Georgian voice")

        self.add_section("Startup")
        self.add_switch("Start with Windows", "autostart",
                        on_toggle=self.controller.set_autostart)

        self._add_voices_guide()

        self.add_button("Read selection", self.controller.read_selection, primary=True)
        self.add_button("Test voice", self.controller.test_voice)
        self.add_button("Stop", self.controller.stop, danger=True)

    def _add_highlight_menu(self):
        """One choice instead of a switch plus a style: the config keeps
        reading_mode (on/off) and highlight_style apart."""
        row = self._row("Highlight words", HINT_HIGHLIGHT)
        menu = ctk.CTkOptionMenu(
            row, values=list(HIGHLIGHT_CHOICES), command=self._highlight_picked,
            fg_color=theme.BG_ELEVATED, button_color=theme.BG_HOVER,
            button_hover_color=theme.BG_ACTIVE, text_color=theme.FG,
        )
        menu.grid(row=0, column=1, sticky="ew")
        if not self.config_obj["reading_mode"]:
            menu.set("Off")
        elif self.config_obj["highlight_style"] == "fullscreen":
            menu.set("Full screen")
        else:
            menu.set("In the text I selected")
        return menu

    def _highlight_picked(self, label):
        on, style = HIGHLIGHT_CHOICES[label]
        if style:
            self._commit("highlight_style", style)
        self._commit("reading_mode", on)

    def _shortcut_taken(self, key, spec):
        other = "pause_hotkey" if key == "hotkey" else "hotkey"
        if normalize(self.config_obj[other]) == spec:
            return f"{pretty(spec)} is already the {SHORTCUT_NAMES[other]} shortcut"
        return None

    def _add_voice_menu(self, label, hint=None):
        """A voice picker; refresh_voices() fills it with friendly names."""
        row = self._row(label, hint)
        menu = ctk.CTkOptionMenu(
            row, values=["(none)"], dynamic_resizing=False,
            fg_color=theme.BG_ELEVATED, button_color=theme.BG_HOVER,
            button_hover_color=theme.BG_ACTIVE, text_color=theme.FG,
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

    def _add_voices_guide(self):
        self.add_section("More voices")
        ctk.CTkLabel(
            self.body, text=_VOICES_HINT, anchor="w", justify="left",
            font=theme.font(ctk, theme.FONT_SIZE_SMALL),
            text_color=theme.FG_FAINT, wraplength=460,
        ).pack(fill="x", padx=theme.PAD_SMALL, pady=(0, theme.PAD_SMALL))
        ctk.CTkButton(
            self.body, text="Open voices folder", command=_open_user_voices,
            fg_color=theme.BG_ELEVATED, hover_color=theme.BG_HOVER,
            text_color=theme.FG, height=30,
        ).pack(anchor="w", padx=theme.PAD_SMALL, pady=(0, theme.PAD_SMALL))
        link_row = ctk.CTkFrame(self.body, fg_color="transparent")
        link_row.pack(fill="x", padx=theme.PAD_SMALL, pady=(0, theme.PAD))
        for label, url in _LINKS:
            ctk.CTkButton(
                link_row, text=label,
                fg_color="transparent", hover_color=theme.BG_ELEVATED,
                text_color=theme.ACCENT, anchor="w", height=26,
                command=lambda u=url: webbrowser.open(u),
            ).pack(side="left", padx=(0, 6))

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
        if self.engine_menu is not None:
            problem = kokoro_tts.problem()
            kokoro_label = KOKORO_UNAVAILABLE if problem else ENGINE_LABELS["kokoro"]
            self.engine_menu.configure(values=[ENGINE_LABELS["piper"], kokoro_label])
            self.engine_menu.set(kokoro_label if name == "kokoro" else ENGINE_LABELS["piper"])
            _set_hint(self.engine_menu, problem or HINT_ENGINE)

        key = VOICE_KEYS[name]
        ids = self.controller.voice_ids(name)
        self._fill_voice_menu(self.voice_menu, key, ids,
                              _shown_voice(name, self.config_obj[key], ids),
                              friendly=name == "piper")
        _set_hint(self.voice_menu, HINT_VOICE[name])
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


def _set_hint(control, text):
    """Reword the hint under a control's Row (common.ui.Row.hint_label)."""
    label = getattr(control.master, "hint_label", None)
    if label is not None:
        label.configure(text=text)


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
