"""The transcriber's main window: choose an audio file, pick which outputs you
want, watch the text stream in, then copy or save.

The window is the app (unlike the reader/clipboard, which live in the tray). It
runs the transcription on a worker thread and marshals updates back with
common.ui.call_on_ui_thread, so a long file never freezes the UI and Stop works.
"""

import os
import threading

import customtkinter as ctk

from common import theme
from common.ui import call_on_ui_thread, pump

import output as output_fmt
from engine import LANGUAGES

AUDIO_TYPES = [
    ("Audio / video", "*.mp3 *.wav *.m4a *.mp4 *.flac *.ogg *.aac *.wma *.webm *.mkv *.mov"),
    ("All files", "*.*"),
]

# checkbox key -> label
OUTPUTS = [
    ("text", "Plain text"),
    ("timestamped", "Timestamped text"),
    ("srt", "Subtitles (SRT)"),
    ("vtt", "Subtitles (VTT)"),
]


class TranscriberWindow(ctk.CTk):
    def __init__(self, config, engine, on_settings=None, on_quit=None):
        theme.apply(ctk)
        super().__init__(fg_color=theme.BG)
        self.config_obj = config
        self.engine = engine
        self._on_settings = on_settings
        self._on_quit = on_quit
        self.path = None
        self.segments = []
        self.info = None
        self._worker = None
        self._cancel = threading.Event()

        self.title("Audio to Text")
        self.geometry("720x620")
        self.minsize(560, 460)
        self.protocol("WM_DELETE_WINDOW", self.hide)

        self._build()
        pump(self)

    # -- layout -----------------------------------------------------------
    def _build(self):
        pad = theme.PAD

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=pad * 2, pady=(pad * 2, 0))
        ctk.CTkLabel(header, text="Audio to Text", anchor="w",
                     font=theme.font(ctk, theme.FONT_SIZE_TITLE, "bold"),
                     text_color=theme.FG).pack(side="left")
        self.device_badge = ctk.CTkLabel(
            header, text="", anchor="e",
            font=theme.font(ctk, theme.FONT_SIZE_SMALL), text_color=theme.FG_DIM)
        self.device_badge.pack(side="right")

        # file row
        filerow = ctk.CTkFrame(self, fg_color="transparent")
        filerow.pack(fill="x", padx=pad * 2, pady=(pad, 0))
        self.choose_btn = ctk.CTkButton(
            filerow, text="Choose audio file…", width=160, command=self.choose,
            fg_color=theme.BG_ELEVATED, hover_color=theme.BG_HOVER, text_color=theme.FG)
        self.choose_btn.pack(side="left")
        self.file_label = ctk.CTkLabel(
            filerow, text="no file selected", anchor="w",
            font=theme.font(ctk), text_color=theme.FG_DIM)
        self.file_label.pack(side="left", padx=pad, fill="x", expand=True)

        # options row: language + output checkboxes
        opts = ctk.CTkFrame(self, fg_color="transparent")
        opts.pack(fill="x", padx=pad * 2, pady=(pad, 0))
        ctk.CTkLabel(opts, text="Language", font=theme.font(ctk),
                     text_color=theme.FG_DIM).pack(side="left")
        self.lang = ctk.CTkOptionMenu(
            opts, width=150, values=list(LANGUAGES.values()),
            command=self._set_language, fg_color=theme.BG_ELEVATED,
            button_color=theme.BG_HOVER, button_hover_color=theme.BG_ACTIVE,
            text_color=theme.FG)
        self.lang.set(LANGUAGES.get(self.config_obj.get("language", ""), "Auto-detect"))
        self.lang.pack(side="left", padx=(theme.PAD_SMALL, 0))

        checks = ctk.CTkFrame(self, fg_color="transparent")
        checks.pack(fill="x", padx=pad * 2, pady=(theme.PAD_SMALL, 0))
        ctk.CTkLabel(checks, text="Save:", font=theme.font(ctk),
                     text_color=theme.FG_DIM).pack(side="left", padx=(0, theme.PAD_SMALL))
        self.output_vars = {}
        for key, label in OUTPUTS:
            var = ctk.BooleanVar(value=bool(self.config_obj.get(f"out_{key}", key == "text")))
            ctk.CTkCheckBox(
                checks, text=label, variable=var, font=theme.font(ctk, theme.FONT_SIZE_SMALL),
                checkbox_width=18, checkbox_height=18, text_color=theme.FG,
                fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                command=(lambda k=key, v=var: self.config_obj.__setitem__(f"out_{k}", bool(v.get())))
            ).pack(side="left", padx=(0, theme.PAD))
            self.output_vars[key] = var

        # transcribe + progress
        act = ctk.CTkFrame(self, fg_color="transparent")
        act.pack(fill="x", padx=pad * 2, pady=(pad, 0))
        self.go_btn = ctk.CTkButton(
            act, text="Transcribe", width=140, command=self.start,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, text_color="#ffffff")
        self.go_btn.pack(side="left")
        self.progress = ctk.CTkProgressBar(act, progress_color=theme.ACCENT)
        self.progress.set(0)
        self.progress.pack(side="left", fill="x", expand=True, padx=pad)
        self.status = ctk.CTkLabel(self, text="Ready", anchor="w",
                                   font=theme.font(ctk, theme.FONT_SIZE_SMALL),
                                   text_color=theme.FG_DIM)
        self.status.pack(fill="x", padx=pad * 2, pady=(theme.PAD_SMALL, 0))

        # result
        self.result = ctk.CTkTextbox(
            self, fg_color=theme.BG_ELEVATED, text_color=theme.FG,
            font=theme.font(ctk), wrap="word")
        self.result.pack(fill="both", expand=True, padx=pad * 2, pady=pad)

        # footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=pad * 2, pady=(0, pad * 2))
        for label, cmd, danger in [("Copy", self.copy, False), ("Save files", self.save, False),
                                   ("Open folder", self.open_folder, False)]:
            ctk.CTkButton(foot, text=label, width=100, command=cmd,
                          fg_color=theme.BG_ELEVATED, hover_color=theme.BG_HOVER,
                          text_color=theme.FG).pack(side="left", padx=(0, theme.PAD_SMALL))
        if self._on_settings:
            ctk.CTkButton(foot, text="Settings", width=90, command=self._on_settings,
                          fg_color=theme.BG_ELEVATED, hover_color=theme.BG_HOVER,
                          text_color=theme.FG).pack(side="right")

        self._refresh_device_badge()

    # -- helpers ----------------------------------------------------------
    def _set_language(self, label):
        code = next((c for c, n in LANGUAGES.items() if n == label), "")
        self.config_obj["language"] = code

    def _refresh_device_badge(self):
        dev = self.engine.active_device().upper()
        self.device_badge.configure(text=f"{self.config_obj.get('model','medium')} · {dev}")

    def selected_outputs(self):
        return {k for k, v in self.output_vars.items() if v.get()}

    def _set_status(self, text, color=theme.FG_DIM):
        self.status.configure(text=text, text_color=color)

    # -- actions ----------------------------------------------------------
    def choose(self):
        from tkinter import filedialog

        path = filedialog.askopenfilename(title="Choose audio", filetypes=AUDIO_TYPES)
        if path:
            self.set_file(path)

    def set_file(self, path):
        self.path = path
        self.file_label.configure(text=os.path.basename(path), text_color=theme.FG)
        self._set_status("Ready")

    def start(self):
        if self._worker and self._worker.is_alive():
            self._cancel.set()  # button doubles as Stop while running
            return
        if not self.path or not os.path.exists(self.path):
            self._set_status("Choose an audio file first", theme.WARN)
            return
        self._cancel.clear()
        self.segments = []
        self.result.delete("1.0", "end")
        # Loading (and first-time downloading) the model blocks with no % to
        # show, so pulse the bar until real transcription starts — otherwise a
        # multi-gigabyte download looks like a frozen app.
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self._set_status("Preparing model…")
        self.go_btn.configure(text="Stop", fg_color=theme.ERROR, hover_color="#c93b3f")
        self._worker = threading.Thread(target=self._run, args=(self.path,), daemon=True)
        self._worker.start()

    def _run(self, path):
        try:
            def on_status(t):
                call_on_ui_thread(self._on_status, t)

            def on_progress(p):
                call_on_ui_thread(self.progress.set, p)

            def on_segment(seg):
                call_on_ui_thread(self._append_segment, seg)

            segs, info = self.engine.transcribe(
                path, on_status=on_status, on_progress=on_progress,
                on_segment=on_segment, should_cancel=self._cancel.is_set)
            call_on_ui_thread(self._done, segs, info)
        except Exception as exc:
            call_on_ui_thread(self._failed, str(exc))

    def _on_status(self, text):
        self._set_status(text)
        # Switch the pulsing bar to a real 0..1 progress once decoding begins.
        if text.startswith("Transcribing"):
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)

    def _append_segment(self, seg):
        self.segments.append(seg)
        self.result.insert("end", seg.text.strip() + " ")
        self.result.see("end")

    def _stop_pulse(self):
        try:
            self.progress.stop()
        except Exception:
            pass
        self.progress.configure(mode="determinate")

    def _done(self, segs, info):
        self._stop_pulse()
        self.progress.set(1.0 if segs else 0)
        self.go_btn.configure(text="Transcribe", fg_color=theme.ACCENT,
                              hover_color=theme.ACCENT_HOVER)
        self._refresh_device_badge()
        if segs is None:
            self._set_status("Stopped", theme.WARN)
            return
        self.segments, self.info = segs, info
        lang = getattr(info, "language", "?")
        dur = getattr(info, "duration", 0) or 0
        self._set_status(f"Done — {len(segs)} segments, language {lang}, "
                         f"{dur/60:.1f} min", theme.OK)
        if self.selected_outputs():
            self.save(quiet=True)

    def _failed(self, message):
        self._stop_pulse()
        self.progress.set(0)
        self.go_btn.configure(text="Transcribe", fg_color=theme.ACCENT,
                              hover_color=theme.ACCENT_HOVER)
        self._set_status(f"Failed: {message[:150]}", theme.ERROR)

    def copy(self):
        text = self.result.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status("Copied to clipboard", theme.OK)

    def save(self, quiet=False):
        if not self.segments or not self.path:
            if not quiet:
                self._set_status("Nothing to save yet", theme.WARN)
            return
        which = self.selected_outputs()
        if not which:
            if not quiet:
                self._set_status("Tick at least one output format", theme.WARN)
            return
        out_dir = self.config_obj.get("output_dir", "") or ""
        files = output_fmt.outputs_for(self.path, self.segments, which, out_dir)
        written = []
        for fpath, content in files.items():
            try:
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write(content)
                written.append(fpath)
            except OSError as exc:
                self._set_status(f"Could not write {os.path.basename(fpath)}: {exc}",
                                 theme.ERROR)
                return
        self._set_status(f"Saved {len(written)} file(s) next to the audio", theme.OK)

    def open_folder(self):
        target = self.config_obj.get("output_dir", "") or (
            os.path.dirname(self.path) if self.path else "")
        if target and os.path.isdir(target):
            os.startfile(target)

    # -- window state -----------------------------------------------------
    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        self._refresh_device_badge()

    def hide(self):
        self.withdraw()
