"""Reading mode: highlight each word as it is spoken, karaoke style.

Two ways to show it:
  * InPlace: a translucent marker moves over each word right where the user
    selected it, positioned by UI Automation (selection.Source). Click-through,
    never takes focus. Used when the selection came through UI Automation as one
    range and the app can report where its words are.
  * Overlay: the text shown large in a full-screen panel on the monitor the user
    is working on. Used otherwise, or when the user picks it.

Piper emits no word alignment, so timings are estimated per line: the engine
reports where each rendered line sits in the audio stream (add_segment), and
word_timings() spreads that line's words over its real duration by character
length, padding words that end a clause. WordTimeline then follows the engine's
playback position, so the highlight can neither run ahead nor drift.
"""

import ctypes
import difflib
import re
import tkinter as tk
from ctypes import wintypes

import bootstrap  # noqa: F401
from common import theme
from common.ui import call_on_ui_thread

BG = "#101218"
ALPHA = 0.93
COLOR_PAST = "#bbbbbb"
COLOR_FUTURE = "#777777"
COLOR_CURRENT = "#ffd54f"
FONT_BIG = 30
FONT_SMALL = 17
MARK_ALPHA = 0.38
MARK_PAD = 2

SMALL_W, SMALL_H = 640, 260
SMALL_MARGIN = 28
TICK_MS = 40

_u32 = ctypes.WinDLL("user32", use_last_error=True)
_u32.MonitorFromWindow.restype = ctypes.c_void_p
_u32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_u32.GetWindowLongW.restype = ctypes.c_long
_u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_TOOLWINDOW = 0x80
WS_EX_LAYERED = 0x80000
WS_EX_NOACTIVATE = 0x8000000


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_u32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]


def monitor_rect():
    """(x, y, width, height) in physical pixels of the monitor holding the
    foreground window. Tk's winfo_screenwidth() reports a DPI-scaled size
    (1707 on a 2560 px screen at 150 %), so a panel sized from it covered only
    part of the screen."""
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    hmon = _u32.MonitorFromWindow(_u32.GetForegroundWindow(), 2)  # NEAREST
    if hmon and _u32.GetMonitorInfoW(hmon, ctypes.byref(info)):
        r = info.rcMonitor
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    return 0, 0, _u32.GetSystemMetrics(0), _u32.GetSystemMetrics(1)


def _dpi_scale():
    try:
        return _u32.GetDpiForSystem() / 96.0
    except AttributeError:
        return 1.0


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


def _norm(word):
    return "".join(c for c in word.lower() if c.isalnum())


def word_spans(raw, prepared):
    """(start, length) in `raw` for each word of `prepared`.

    prepare_text() drops URLs, spells out emails and splits code-like tokens,
    so the spoken words are lined up with the selected ones by a sequence match
    on their letters and digits; spoken words with no counterpart share the
    nearest selected word."""
    tokens = [(m.start(), m.end() - m.start()) for m in re.finditer(r"\S+", raw)]
    words = prepared.split()
    if not tokens or not words:
        return []
    a = [_norm(raw[s:s + n]) for s, n in tokens]
    b = [_norm(w) for w in words]
    mapping = [0] * len(b)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        for j in range(j1, j2):
            if op == "equal":
                mapping[j] = i1 + (j - j1)
            elif i2 > i1:  # replaced: spread the spoken words over the originals
                mapping[j] = i1 + (j - j1) * (i2 - i1) // (j2 - j1)
            else:  # inserted (e.g. "at" from an email): stay on the word before
                mapping[j] = max(i1 - 1, 0)
    return [tokens[i] for i in mapping]


class WordTimeline:
    """When each word of the prepared text is spoken, filled in line by line
    as the engine reports segments, and which word is current."""

    def __init__(self, text):
        self.words = text.split()
        # (first word, word count) per non-empty line; the engine numbers the
        # lines it reports the same way.
        self._lines = []
        first = 0
        for line in text.split("\n"):
            count = len(line.split())
            if count:
                self._lines.append((first, count))
                first += count
        self._next_line = 0
        self.timings = [None] * len(self.words)
        self.current = -1

    def add_segment(self, line_index, start, duration):
        """Place one line's words, now that the engine knows where the line
        starts in the stream and how long its speech lasts (seconds)."""
        if not 0 <= line_index < len(self._lines):
            return
        # A line that failed to render is never reported; give its words this
        # start so the highlight moves past them instead of stopping there.
        for skipped in range(self._next_line, line_index):
            first, count = self._lines[skipped]
            for k in range(first, first + count):
                self.timings[k] = (start, start)
        self._next_line = line_index + 1
        first, count = self._lines[line_index]
        for k, (s, e) in enumerate(word_timings(self.words[first:first + count], duration)):
            self.timings[first + k] = (start + s, start + e)

    def advance(self, position):
        """Move to the word being spoken `position` seconds into the stream.
        Only moves forward (the engine's position never goes back). True if
        the current word changed."""
        idx = self.current
        while (idx + 1 < len(self.timings) and self.timings[idx + 1] is not None
               and self.timings[idx + 1][0] <= position):
            idx += 1
        changed = idx != self.current
        self.current = idx
        return changed


class Overlay(tk.Toplevel):
    """Word-highlighting panel, full screen or (compact) in the corner of the
    monitor. Driven by set_progress(position)."""

    def __init__(self, master, text, on_close=None, compact=False):
        super().__init__(master, bg=BG)
        self.on_close = on_close
        self.timeline = WordTimeline(text)
        self.words = self.timeline.words
        self.big = True
        self._closed = False
        self._monitor = monitor_rect()  # before we take the foreground

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
        self.paused_label = tk.Label(self, text="", bg=BG, fg=COLOR_CURRENT,
                                     font=(theme.FONT, 12), padx=12, pady=6)

        self.text.bind("<Button-1>", self._toggle_size)
        # Bound on both the toplevel and the text: an overrideredirect window
        # doesn't always get the keyboard focus Windows would give a normal one.
        for widget in (self, self.text):
            for key in ("<Escape>", "<q>", "<Q>"):
                widget.bind(key, lambda _e: self.close())
            widget.bind("<Button-3>", lambda _e: self.close())

        self._resize(big=not compact)
        self.lift()
        if not compact:
            # Full screen takes the keyboard so Esc / Q close it; the compact
            # panel leaves the focus with the app the text came from.
            self.focus_force()
            self.text.focus_set()

    # -- geometry ---------------------------------------------------------
    def _resize(self, big):
        self.big = big
        mx, my, mw, mh = self._monitor
        if big:
            self.geometry(f"{mw}x{mh}+{mx}+{my}")
            self.text.configure(font=(theme.FONT, FONT_BIG), padx=80, pady=60)
            self.text.tag_configure("current", font=(theme.FONT, FONT_BIG, "bold"))
        else:
            scale = _dpi_scale()
            w, h = int(SMALL_W * scale), int(SMALL_H * scale)
            margin = int(SMALL_MARGIN * scale)
            self.geometry(f"{w}x{h}+{mx + mw - w - margin}+{my + mh - h - margin}")
            # Side padding wide enough that lines clear the ✕ in the corner.
            self.text.configure(font=(theme.FONT, FONT_SMALL),
                                padx=int(56 * scale), pady=int(18 * scale))
            self.text.tag_configure("current", font=(theme.FONT, FONT_SMALL, "bold"))
        self._scroll_to(self.timeline.current)

    def _toggle_size(self, _event=None):
        self._resize(not self.big)
        return "break"  # don't let the Text widget move its insert cursor

    # -- progress ---------------------------------------------------------
    def add_segment(self, line_index, start, duration):
        if not self._closed:
            self.timeline.add_segment(line_index, start, duration)

    def set_progress(self, position):
        """Highlight the word being spoken `position` seconds into the stream."""
        if self._closed or not self.timeline.advance(position):
            return
        self._render(self.timeline.current)
        self._scroll_to(self.timeline.current)

    def set_paused(self, paused, hint=""):
        if self._closed:
            return
        if paused:
            self.paused_label.configure(text=f"Paused · {hint}" if hint else "Paused")
            # Bottom-left: at the top it covered the first line in the compact panel.
            self.paused_label.place(x=14, rely=1.0, y=-12, anchor="sw")
        else:
            self.paused_label.place_forget()

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


class _Marker(tk.Toplevel):
    """Translucent box laid over one word. Click-through, no taskbar button,
    never activated, so the app being read keeps the focus."""

    def __init__(self, master):
        super().__init__(master, bg=COLOR_CURRENT)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", MARK_ALPHA)
        self.withdraw()
        self.update_idletasks()
        hwnd = int(self.wm_frame(), 16)
        style = _u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        _u32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
                            | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self._shown = False

    def show(self, rect):
        x, y, w, h = (int(round(v)) for v in rect)
        self.geometry(f"{w + 2 * MARK_PAD}x{h + 2 * MARK_PAD}+{x - MARK_PAD}+{y - MARK_PAD}")
        if not self._shown:
            self._shown = True
            self.deiconify()

    def hide(self):
        if self._shown:
            self._shown = False
            self.withdraw()


class InPlace:
    """Reading mode on the selected text itself: same interface as Overlay,
    but the highlight is a marker over each word where the user selected it.
    Word positions come from UI Automation one word at a time, so the marker
    follows the text if the user scrolls."""

    def __init__(self, master, text, source, spans, on_close=None):
        self.on_close = on_close
        self.timeline = WordTimeline(text)
        self.source = source
        self.spans = spans
        self._closed = False
        self.marker = _Marker(master)

    def add_segment(self, line_index, start, duration):
        if not self._closed:
            self.timeline.add_segment(line_index, start, duration)

    def set_progress(self, position):
        if self._closed or not self.timeline.advance(position):
            return
        idx = self.timeline.current
        if 0 <= idx < len(self.spans):
            start, length = self.spans[idx]
            self.source.rect(start, length,
                             lambda rect, i=idx: call_on_ui_thread(self._place, i, rect))

    def _place(self, idx, rect):
        if self._closed or idx != self.timeline.current:
            return  # a later word has already taken over
        if rect:
            self.marker.show(rect)
        else:
            self.marker.hide()  # scrolled out of view

    def set_paused(self, paused, hint=""):
        pass  # the marker simply stays on the word; the status line says how to continue

    def close(self):
        if self._closed:
            return
        self._closed = True
        callback, self.on_close = self.on_close, None
        try:
            self.marker.destroy()
        except tk.TclError:
            pass
        if callback:
            callback()

    @property
    def closed(self):
        return self._closed
