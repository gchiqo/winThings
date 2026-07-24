"""The always-on-top bar: app buttons, window chips and the metric row.

Three Windows details shape this file.

WS_EX_NOACTIVATE is the important one. Without it, clicking a dock button makes
the dock the foreground window, so the SetForegroundWindow that follows is
trying to focus a window while we hold focus ourselves — the click-to-focus
feature fights its own click. With it, the previously active window is still
the foreground when the handler runs, which is also what makes the "click the
focused app to minimise it" toggle possible at all.

Geometry has to be computed in physical pixels but handed to customtkinter in
scaled units: CTk multiplies width/height by the window scaling factor and
leaves x/y alone. Sizes therefore come from Tk's own requested size (already
physical) divided back out, so the bar is the right thickness at any DPI.

Redraws destroy and rebuild the app row rather than diffing it. The scanner
only calls in when something visible actually changed, so this happens a few
times a minute, not a few times a second.
"""

import os
import subprocess

import customtkinter as ctk
import win32api
import win32con
import win32gui

from common import theme

from . import winlist
from .icons import NATIVE
from .metrics import format_sample, gpu_available
from .winlist import WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW

EDGES = ("bottom", "top", "left", "right")
SPANS = ("full", "fit")

# Order and label for each readout; keys match theme.METRIC_COLORS.
METRIC_ROW = (
    ("cpu", "CPU"),
    ("ram", "RAM"),
    ("gpu", "GPU"),
    ("net_up", "↑"),
    ("net_down", "↓"),
)


def truncate(text, limit):
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def work_area():
    """Primary monitor's usable rectangle, in physical pixels.

    Using the work area rather than the full monitor means the dock lands above
    a visible taskbar and flush to the edge once the user auto-hides it.
    """
    info = win32api.GetMonitorInfo(
        win32api.MonitorFromPoint((0, 0), win32con.MONITOR_DEFAULTTOPRIMARY)
    )
    return info["Work"]


class Dock(ctk.CTk):
    def __init__(self, config, on_settings=None, on_quit=None):
        theme.apply(ctk)
        super().__init__(fg_color=theme.BG)
        self.config_obj = config
        self.on_settings = on_settings
        self.on_quit = on_quit
        self._entries = []
        self._metric_labels = {}
        self._metric_keys = ()
        self._last_sample = None
        self._styled = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.resizable(False, False)

        self.shell = ctk.CTkFrame(self, fg_color=theme.BG, corner_radius=0)
        self.shell.pack(fill="both", expand=True)
        self.apps_holder = ctk.CTkFrame(self.shell, fg_color="transparent")
        self.metrics_holder = ctk.CTkFrame(self.shell, fg_color="transparent")
        self._pack_holders()

        self.build_metrics()
        self.after(50, self._apply_window_styles)
        self.after(3000, self._keep_on_top)

    # -- config-derived values -------------------------------------------
    @property
    def edge(self):
        value = self.config_obj["edge"]
        return value if value in EDGES else "bottom"

    @property
    def vertical(self):
        return self.edge in ("left", "right")

    @property
    def icon_size(self):
        return self.config_obj.integer("icon_size", 16, NATIVE)

    # -- window plumbing --------------------------------------------------
    def hwnd(self):
        handle = self.winfo_id()
        parent = win32gui.GetParent(handle)
        return parent or handle

    def _apply_window_styles(self):
        """Never take focus, never appear in alt-tab or in our own window list."""
        if self._styled:
            return
        try:
            handle = self.hwnd()
            style = win32gui.GetWindowLong(handle, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                handle, win32con.GWL_EXSTYLE,
                style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
            )
            self._styled = True
        except win32gui.error:
            pass  # cosmetic; the dock still works, it just steals focus

    def _keep_on_top(self):
        """Other topmost windows can shuffle above us; re-assert periodically."""
        try:
            win32gui.SetWindowPos(
                self.hwnd(), win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
            )
        except win32gui.error:
            pass
        self.after(3000, self._keep_on_top)

    def _scaling(self):
        try:
            return ctk.ScalingTracker.get_window_scaling(self) or 1.0
        except Exception:
            return 1.0

    def reposition(self):
        left, top, right, bottom = work_area()
        span_full = self.config_obj["span"] != "fit"
        margin = self.config_obj.integer("margin", 0, 200)

        self.update_idletasks()
        thickness = max(
            24,
            self.shell.winfo_reqwidth() if self.vertical else self.shell.winfo_reqheight(),
        )
        length = (right - left) if not self.vertical else (bottom - top)
        if span_full:
            extent = length - margin * 2
        else:
            natural = (
                self.shell.winfo_reqheight() if self.vertical
                else self.shell.winfo_reqwidth()
            )
            extent = min(length - margin * 2, natural)

        if self.vertical:
            width, height = thickness, extent
            x = left + margin if self.edge == "left" else right - thickness - margin
            y = top + (length - extent) // 2 if not span_full else top + margin
        else:
            width, height = extent, thickness
            y = top + margin if self.edge == "top" else bottom - thickness - margin
            x = left + (length - extent) // 2 if not span_full else left + margin

        scale = self._scaling()
        self.geometry(f"{int(width / scale)}x{int(height / scale)}+{int(x)}+{int(y)}")

    def _pack_holders(self):
        self.apps_holder.pack_forget()
        self.metrics_holder.pack_forget()
        pad = theme.PAD_SMALL
        if self.vertical:
            self.apps_holder.pack(side="top", padx=pad, pady=(pad, 0))
            self.metrics_holder.pack(side="bottom", padx=pad, pady=(0, pad))
        else:
            self.apps_holder.pack(side="left", padx=(pad, 0), pady=pad)
            self.metrics_holder.pack(side="right", padx=(0, pad), pady=pad)

    # -- metric readouts --------------------------------------------------
    def enabled_metrics(self):
        keys = []
        for key, _ in METRIC_ROW:
            if key == "gpu" and not gpu_available():
                continue  # no NVML: drop the readout rather than show a dash
            if self.config_obj[f"show_{key}"]:
                keys.append(key)
        return tuple(keys)

    def build_metrics(self):
        for child in self.metrics_holder.winfo_children():
            child.destroy()
        self._metric_labels.clear()
        self._metric_keys = self.enabled_metrics()

        labels = dict(METRIC_ROW)
        small = theme.font(ctk, theme.FONT_SIZE_SMALL, "bold")
        value_font = theme.font(ctk, theme.FONT_SIZE_SMALL)
        for key in self._metric_keys:
            cell = ctk.CTkFrame(self.metrics_holder, fg_color="transparent")
            cell.pack(side="top" if self.vertical else "left",
                      padx=0 if self.vertical else (0, theme.PAD_SMALL))
            ctk.CTkLabel(
                cell, text=labels[key], font=small,
                text_color=theme.METRIC_COLORS[key],
            ).pack(side="left", padx=(0, 3))
            value = ctk.CTkLabel(
                cell, text="--", font=value_font, text_color=theme.FG,
                anchor="e", width=58 if key.startswith("net") else 32,
            )
            value.pack(side="left")
            self._metric_labels[key] = value
        if self._last_sample:
            self.set_metrics(self._last_sample)  # don't blank on a relayout

    def set_metrics(self, sample):
        self._last_sample = sample
        text = format_sample(sample)
        for key, label in self._metric_labels.items():
            label.configure(text=text.get(key, "--"))

    # -- app buttons ------------------------------------------------------
    def set_entries(self, entries):
        self._entries = entries
        for child in self.apps_holder.winfo_children():
            child.destroy()
        show_chips = bool(self.config_obj["show_chips"])
        side = "top" if self.vertical else "left"
        for entry in entries:
            group = ctk.CTkFrame(self.apps_holder, fg_color="transparent")
            group.pack(side=side, padx=1, pady=1)
            self._build_group(group, entry, show_chips)
        self.reposition()

    def _build_group(self, parent, entry, show_chips):
        size = self.icon_size
        column = ctk.CTkFrame(parent, fg_color="transparent")
        column.pack(side="top" if self.vertical else "left")

        image = ctk.CTkImage(
            light_image=entry.image, dark_image=entry.image, size=(size, size)
        )
        button = ctk.CTkButton(
            column, text="", image=image, width=size + 10, height=size + 10,
            corner_radius=theme.RADIUS,
            fg_color=theme.BG_ACTIVE if entry.focused else "transparent",
            hover_color=theme.BG_HOVER,
            command=lambda e=entry: self.on_icon_click(e),
        )
        button.pack()
        button.bind("<Button-3>", lambda _e, entry=entry: self.toggle_pin(entry))

        # Running indicator: a bar under (or beside) the button, taskbar-style.
        bar = ctk.CTkFrame(
            column, height=3, width=max(6, size // (1 if len(entry.windows) > 1 else 2)),
            corner_radius=2,
            fg_color=theme.ACCENT if entry.running else theme.FG_FAINT,
        )
        bar.pack(pady=(2, 0))

        if not show_chips or len(entry.windows) < 2:
            return
        chips = ctk.CTkFrame(parent, fg_color="transparent")
        chips.pack(side="top" if self.vertical else "left", padx=(3, 0))
        limit = self.config_obj.integer("chip_chars", 6, 80)
        font = theme.font(ctk, theme.FONT_SIZE_SMALL)
        for window in entry.windows:
            label = truncate(window.title, limit)
            ctk.CTkButton(
                chips, text=label, anchor="w", font=font,
                width=font.measure(label) + theme.PAD,
                height=max(18, (size + 10) // 2 - 2),
                corner_radius=theme.RADIUS - 2,
                fg_color=theme.BG_ELEVATED if not window.minimized else theme.BG,
                hover_color=theme.BG_HOVER,
                border_width=1 if window.minimized else 0,
                border_color=theme.BORDER,
                text_color=theme.FG if not window.minimized else theme.FG_DIM,
                command=lambda h=window.hwnd: winlist.toggle(h),
            ).pack(side="top" if self.vertical else "left", padx=1, pady=1)

    # -- interactions -----------------------------------------------------
    def on_icon_click(self, entry):
        if not entry.windows:
            self.launch(entry)
            return
        handles = [w.hwnd for w in entry.windows]
        current = winlist.foreground_hwnd()
        if current in handles:
            if len(handles) == 1:
                winlist.minimize(current)
            else:
                # Second click on a multi-window app cycles rather than
                # minimising, which is what makes chips-plus-icon usable.
                winlist.activate(handles[(handles.index(current) + 1) % len(handles)])
            return
        winlist.activate(handles[0])

    def launch(self, entry):
        if not entry.exe or not os.path.exists(entry.exe):
            return
        try:
            subprocess.Popen([entry.exe], close_fds=True)
        except OSError:
            pass  # pinned target moved or needs elevation

    def toggle_pin(self, entry):
        key = (entry.exe or "").lower()
        if not key:
            return
        pinned = [p for p in (self.config_obj["pinned"] or []) if p]
        lowered = [p.lower() for p in pinned]
        if key in lowered:
            pinned = [p for p in pinned if p.lower() != key]
        else:
            pinned.append(entry.exe)
        self.config_obj["pinned"] = pinned

    # -- live config ------------------------------------------------------
    def apply_config(self):
        """Re-read everything that affects layout. Called from Config.subscribe."""
        self._pack_holders()
        # Rebuilt unconditionally: switching between a horizontal and a vertical
        # edge changes how the readout cells stack, not just which ones exist.
        self.build_metrics()
        self.set_entries(self._entries)
