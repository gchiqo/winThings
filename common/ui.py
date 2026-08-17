"""Settings-window scaffolding shared by all three apps.

Gives every app the same shape: title, live status dot, a stack of labelled
rows (slider / switch / entry / dropdown / hotkey-capture), and a footer for
actions. Apps subclass SettingsWindow and fill in build().

Tk must own the main thread, so apps that also run a tray icon or keyboard hook
should start those as daemon threads and marshal work back with
call_on_ui_thread(). Something has to consume that queue: SettingsWindow does it
for itself, but an app whose long-lived root is a dock panel or a hidden tray
root must call pump(root) once on its own root.
"""

import queue
import tkinter as tk

import customtkinter as ctk

from . import theme
from .hotkey import capture as capture_hotkey, is_valid, normalize, pretty
from .textinput import fix_unicode_input

_ui_queue = queue.Queue()


def call_on_ui_thread(fn, *args, **kwargs):
    """Queue fn to run on the Tk thread. Safe from any thread."""
    _ui_queue.put((fn, args, kwargs))


_pending = {}  # widget key -> outstanding after id


def pump(widget, interval=50):
    """Start draining call_on_ui_thread() work onto `widget`'s event loop.

    Call once per process, on a widget that lives as long as the app.

    The reschedule is cancelled on <Destroy> rather than merely checked on entry.
    Tk resolves the callback's command name before invoking it, so a window torn
    down with a tick still queued fails inside Tk ("invalid command name ...")
    where no guard in this function could ever run.
    """
    key = str(widget)

    def cancel(event=None):
        if event is not None and event.widget is not widget:
            return  # <Destroy> bubbles from children; only ours counts
        after_id = _pending.pop(key, None)
        if after_id is None:
            return
        try:
            widget.after_cancel(after_id)
        except (tk.TclError, ValueError):
            pass

    def schedule():
        try:
            _pending[key] = widget.after(interval, tick)
        except tk.TclError:
            _pending.pop(key, None)  # interpreter went away

    def tick():
        _pending.pop(key, None)
        while True:
            try:
                fn, args, kwargs = _ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kwargs)
            except Exception:
                pass
        schedule()

    cancel()  # re-pumping the same widget must not stack two loops
    try:
        widget.bind("<Destroy>", cancel, add="+")
    except tk.TclError:
        return
    schedule()


_drain = pump  # kept: apps written against the original private name


class Row(ctk.CTkFrame):
    """Label on the left, control on the right."""

    def __init__(self, parent, label, hint=None):
        super().__init__(parent, fg_color="transparent")
        self.grid_columnconfigure(1, weight=1)
        text = ctk.CTkLabel(
            self, text=label, anchor="w",
            font=theme.font(ctk), text_color=theme.FG,
        )
        text.grid(row=0, column=0, sticky="w", padx=(0, theme.PAD))
        if hint:
            ctk.CTkLabel(
                self, text=hint, anchor="w", justify="left",
                font=theme.font(ctk, theme.FONT_SIZE_SMALL),
                text_color=theme.FG_FAINT, wraplength=380,
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 0))
        self.control_cell = (0, 1)


class SettingsWindow(ctk.CTk):
    """Base settings window. Subclasses implement build(body)."""

    APP = "winthings"
    TITLE = "WinThings"
    WIDTH = 480

    def __init__(self, config, on_change=None):
        theme.apply(ctk)
        super().__init__(fg_color=theme.BG)
        self.config_obj = config
        self._on_change = on_change
        self._loading = True

        self.title(self.TITLE)
        self.geometry(f"{self.WIDTH}x640")
        self.minsize(self.WIDTH, 400)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=theme.PAD * 2, pady=(theme.PAD * 2, 0))
        ctk.CTkLabel(
            header, text=self.TITLE, anchor="w",
            font=theme.font(ctk, theme.FONT_SIZE_TITLE, "bold"),
            text_color=theme.FG,
        ).pack(fill="x")
        self.status = ctk.CTkLabel(
            header, text="●  Starting…", anchor="w",
            font=theme.font(ctk, theme.FONT_SIZE_SMALL),
            text_color=theme.FG_DIM,
        )
        self.status.pack(fill="x", pady=(2, 0))

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=theme.PAD, pady=theme.PAD)

        self.footer = ctk.CTkFrame(self, fg_color="transparent")
        self.footer.pack(fill="x", padx=theme.PAD * 2, pady=(0, theme.PAD * 2))

        self.toast = ctk.CTkLabel(
            self, text="", anchor="w",
            font=theme.font(ctk, theme.FONT_SIZE_SMALL),
            text_color=theme.FG_DIM,
        )
        self.toast.pack(fill="x", padx=theme.PAD * 2, pady=(0, theme.PAD))

        # Widgets are built on first show(), not here: the reader keeps its
        # settings window alive-but-hidden for the whole session, and building
        # a dozen rows of sliders/dropdowns at startup is the bulk of its launch
        # cost and idle RAM for a window most sessions never open.
        self._built = False
        pump(self)

    def ensure_built(self):
        """Build the rows once, lazily. Call at the top of show()."""
        if self._built:
            return
        self._built = True
        self.build(self.body)
        self._loading = False

    # -- to override ------------------------------------------------------
    def build(self, body):
        raise NotImplementedError

    # -- row builders -----------------------------------------------------
    def _row(self, label, hint=None):
        row = Row(self.body, label, hint)
        row.pack(fill="x", pady=theme.PAD_SMALL, padx=theme.PAD_SMALL)
        return row

    def add_slider(self, label, key, lo, hi, step=None, fmt="{:.2f}", hint=None):
        row = self._row(label, hint)
        value = ctk.CTkLabel(
            row, text="", width=56, anchor="e",
            font=theme.font(ctk, theme.FONT_SIZE_SMALL), text_color=theme.FG_DIM,
        )
        value.grid(row=0, column=2, padx=(theme.PAD_SMALL, 0))
        steps = int((hi - lo) / step) if step else None
        slider = ctk.CTkSlider(
            row, from_=lo, to=hi, number_of_steps=steps,
            button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
            progress_color=theme.ACCENT,
        )
        slider.set(self.config_obj.num(key, lo, hi))
        value.configure(text=fmt.format(slider.get()))
        slider.grid(row=0, column=1, sticky="ew")

        def changed(val):
            value.configure(text=fmt.format(val))
            self._commit(key, round(float(val), 4))

        slider.configure(command=changed)
        return slider

    def add_switch(self, label, key, hint=None, on_toggle=None):
        row = self._row(label, hint)
        var = tk.BooleanVar(value=bool(self.config_obj[key]))

        def changed():
            self._commit(key, bool(var.get()))
            if on_toggle:
                on_toggle(bool(var.get()))

        switch = ctk.CTkSwitch(
            row, text="", variable=var, command=changed,
            progress_color=theme.ACCENT, width=44,
        )
        switch.grid(row=0, column=1, sticky="e")
        return switch

    def add_dropdown(self, label, key, values, hint=None):
        row = self._row(label, hint)
        current = self.config_obj[key]
        if current not in values:
            current = values[0] if values else ""
        menu = ctk.CTkOptionMenu(
            row, values=list(values) or ["(none)"],
            command=lambda v: self._commit(key, v),
            fg_color=theme.BG_ELEVATED, button_color=theme.BG_HOVER,
            button_hover_color=theme.BG_ACTIVE, text_color=theme.FG,
        )
        if current:
            menu.set(current)
        menu.grid(row=0, column=1, sticky="ew")
        return menu

    def add_entry(self, label, key, hint=None, cast=str):
        row = self._row(label, hint)
        entry = ctk.CTkEntry(
            row, fg_color=theme.BG_ELEVATED, border_color=theme.BORDER,
            text_color=theme.FG,
        )
        entry.insert(0, str(self.config_obj[key]))
        entry.grid(row=0, column=1, sticky="ew")
        fix_unicode_input(entry)  # non-ANSI (e.g. Georgian) typing survives

        def commit(_event=None):
            try:
                self._commit(key, cast(entry.get().strip()))
            except (TypeError, ValueError):
                self.flash("Invalid value", error=True)
                entry.delete(0, "end")
                entry.insert(0, str(self.config_obj[key]))

        entry.bind("<FocusOut>", commit)
        entry.bind("<Return>", commit)
        return entry

    def add_hotkey(self, label, key, hint=None, on_set=None):
        """Click-to-record hotkey field — no typing accelerator syntax."""
        row = self._row(label, hint)
        button = ctk.CTkButton(
            row, text=pretty(self.config_obj[key]),
            fg_color=theme.BG_ELEVATED, hover_color=theme.BG_HOVER,
            text_color=theme.FG, border_color=theme.BORDER, border_width=1,
        )
        button.grid(row=0, column=1, sticky="ew")
        state = {"cancel": None}

        def done(spec):
            state["cancel"] = None

            def apply():
                if spec and is_valid(spec):
                    self._commit(key, normalize(spec))
                    button.configure(text=pretty(spec))
                    if on_set:
                        on_set(normalize(spec))
                else:
                    button.configure(text=pretty(self.config_obj[key]))
                    if spec:
                        self.flash(f"{pretty(spec)} isn't a usable hotkey", error=True)

            call_on_ui_thread(apply)

        def start():
            if state["cancel"]:
                state["cancel"]()
                state["cancel"] = None
                button.configure(text=pretty(self.config_obj[key]))
                return
            button.configure(text="Press keys…  (Esc to cancel)")
            state["cancel"] = capture_hotkey(done)

        button.configure(command=start)
        return button

    def add_button(self, label, action, primary=False, danger=False):
        colors = {
            "fg_color": theme.ACCENT if primary else theme.BG_ELEVATED,
            "hover_color": theme.ACCENT_HOVER if primary else theme.BG_HOVER,
        }
        if danger:
            colors = {"fg_color": theme.ERROR, "hover_color": "#c93b3f"}
        button = ctk.CTkButton(
            self.footer, text=label, command=action, height=36,
            text_color="#ffffff" if (primary or danger) else theme.FG, **colors,
        )
        button.pack(side="left", expand=True, fill="x", padx=3)
        return button

    def add_section(self, label):
        frame = ctk.CTkFrame(self.body, fg_color="transparent")
        frame.pack(fill="x", pady=(theme.PAD, 2), padx=theme.PAD_SMALL)
        ctk.CTkLabel(
            frame, text=label.upper(), anchor="w",
            font=theme.font(ctk, theme.FONT_SIZE_SMALL, "bold"),
            text_color=theme.FG_FAINT,
        ).pack(fill="x")
        return frame

    # -- status / feedback ------------------------------------------------
    def set_status(self, text, color=theme.OK):
        self.status.configure(text=f"●  {text}", text_color=color)

    def flash(self, text, error=False):
        self.toast.configure(
            text=text, text_color=theme.ERROR if error else theme.OK
        )
        self.after(3000, lambda: self.toast.configure(text=""))

    def _commit(self, key, value):
        if self._loading:
            return
        self.config_obj[key] = value
        if self._on_change:
            self._on_change(self.config_obj)
