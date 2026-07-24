"""Fullscreen "reading mode" overlay — Tk port of the GTK reader_overlay.py.

Shows the text large and highlights each word as it is spoken, karaoke style.
Piper emits no word alignment, so timings are estimated from each word's
character length spread over the utterance's real duration — word_timings() is
carried over unchanged from the GTK version, as is the estimator's behaviour of
padding words that end a clause.

Differences from the GTK original, all forced by the toolkit:
  * Tk on Windows only does whole-window alpha (`-alpha`), not per-pixel alpha,
    so the background is a uniformly translucent panel rather than the
    cairo CLEAR-then-OVER paint the GTK version needed to fight Mutter.
  * None of the X11/Wayland workarounds (GDK_BACKEND, hide/reconfigure/show to
    make Mutter honour a move) apply here; `geometry()` just works.
  * It is a Toplevel of the app's existing Tk root instead of its own process,
    because on Windows the hotkey and the playback live in that same process.
"""

import tkinter as tk

import bootstrap  # noqa: F401
from common import theme

BG = "#101218"
ALPHA = 0.93
COLOR_PAST = "#bbbbbb"
COLOR_FUTURE = "#777777"
COLOR_CURRENT = "#ffd54f"
FONT_BIG = 30
FONT_SMALL = 17

SMALL_W, SMALL_H = 640, 260
SMALL_MARGIN = 28
TICK_MS = 40


def word_timings(words, total_duration):
    """Estimate (start, end) seconds for each word based on its char length
    and trailing punctuation. Piper doesn't expose alignments, so this is
    approximate but usually within ~150 ms per word."""
    if not words or total_duration <= 0:
        return []
    weights = []
    for w in words:
        base = max(2, len(w))
        if w and w[-1] in ",;:":
            base += 3
        elif w and w[-1] in ".!?":
            base += 5
        weights.append(base)
    tot = sum(weights)
    out, t = [], 0.0
    for wt in weights:
        dur = total_duration * wt / tot
        out.append((t, t + dur))
        t += dur
    return out


class Overlay(tk.Toplevel):
    """Word-highlighting overlay. Driven by set_progress(seconds_elapsed)."""

    def __init__(self, master, text, on_close=None):
        super().__init__(master, bg=BG)
        self.on_close = on_close
        self.words = text.split()
        self.timings = []
        self.current = -1
        self.big = True
        self._closed = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", ALPHA)

        self.text = tk.Text(
            self, bg=BG, fg=COLOR_FUTURE, bd=0, highlightthickness=0,
            wrap="word", cursor="hand2", padx=80, pady=60, spacing1=8, spacing3=8,
            font=(theme.FONT, FONT_BIG),
        )
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("past", foreground=COLOR_PAST)
        self.text.tag_configure("future", foreground=COLOR_FUTURE)
        self.text.tag_configure(
            "current", foreground=COLOR_CURRENT, font=(theme.FONT, FONT_BIG, "bold"))
        self.text.tag_configure("body", justify="center")

        self._spans = []
        for i, word in enumerate(self.words):
            start = self.text.index("end-1c")
            self.text.insert("end", word + (" " if i < len(self.words) - 1 else ""))
            self._spans.append((start, self.text.index("end-1c")))
        self.text.tag_add("body", "1.0", "end")
        self.text.tag_add("future", "1.0", "end")
        self.text.configure(state="disabled")

        close = tk.Label(self, text="✕", bg=BG, fg="#dddddd",
                         font=(theme.FONT, 14), cursor="hand2", padx=10, pady=4)
        close.place(relx=1.0, x=-14, y=12, anchor="ne")
        close.bind("<Button-1>", lambda _e: self.close())

        self.text.bind("<Button-1>", self._toggle_size)
        # Bound on both the toplevel and the text: an overrideredirect window
        # doesn't always get the keyboard focus Windows would give a normal one.
        for widget in (self, self.text):
            for key in ("<Escape>", "<q>", "<Q>"):
                widget.bind(key, lambda _e: self.close())
            widget.bind("<Button-3>", lambda _e: self.close())

        self._resize(big=True)
        self.lift()
        self.focus_force()
        self.text.focus_set()

    # -- geometry ---------------------------------------------------------
    def _resize(self, big):
        self.big = big
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        if big:
            self.geometry(f"{sw}x{sh}+0+0")
            self.text.configure(font=(theme.FONT, FONT_BIG), padx=80, pady=60)
            self.text.tag_configure("current", font=(theme.FONT, FONT_BIG, "bold"))
        else:
            self.geometry(f"{SMALL_W}x{SMALL_H}"
                          f"+{sw - SMALL_W - SMALL_MARGIN}+{sh - SMALL_H - SMALL_MARGIN}")
            self.text.configure(font=(theme.FONT, FONT_SMALL), padx=24, pady=18)
            self.text.tag_configure("current", font=(theme.FONT, FONT_SMALL, "bold"))
        self._scroll_to(self.current)

    def _toggle_size(self, _event=None):
        self._resize(not self.big)
        return "break"  # don't let the Text widget move its insert cursor

    # -- progress ---------------------------------------------------------
    def begin(self, duration):
        """Called once the audio device is actually running."""
        self.timings = word_timings(self.words, duration)

    def set_progress(self, elapsed):
        """Highlight the word being spoken at `elapsed` seconds. True while reading."""
        if self._closed or not self.timings:
            return not self._closed
        idx = -1
        for i, (start, _end) in enumerate(self.timings):
            if elapsed >= start:
                idx = i
            else:
                break
        if idx != self.current:
            self.current = idx
            self._render(idx)
            self._scroll_to(idx)
        return elapsed <= self.timings[-1][1] + 0.4

    def _render(self, idx):
        if idx < 0 or idx >= len(self._spans):
            return
        start, end = self._spans[idx]
        self.text.configure(state="normal")
        # Clear the whole tag rather than just the previous word: a slow tick or
        # a jump in the estimate can skip words, and "current" outranks "past".
        self.text.tag_remove("current", "1.0", "end")
        self.text.tag_add("past", "1.0", start)
        self.text.tag_add("current", start, end)
        self.text.configure(state="disabled")

    def _scroll_to(self, idx):
        if 0 <= idx < len(self._spans):
            self.text.see(self._spans[idx][0])

    # -- teardown ---------------------------------------------------------
    def close(self):
        if self._closed:
            return
        self._closed = True
        callback, self.on_close = self.on_close, None
        try:
            self.destroy()
        except tk.TclError:
            pass
        if callback:
            callback()

    @property
    def closed(self):
        return self._closed
