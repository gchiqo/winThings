"""Settings window for the dock.

Everything here writes straight through common.Config, and the dock subscribes
to that, so each change lands on the bar as soon as the slider moves — there is
no apply button and no restart.

The pinned-apps list is built by hand rather than with add_switch because it is
driven by a list value in the config, not one key per row; it is refreshed each
time the window is shown so newly launched apps appear.
"""

import os

import customtkinter as ctk

from common import autostart, theme
from common.ui import SettingsWindow

from .dockwindow import EDGES, SPANS
from .metrics import gpu_available, gpu_name

APP_NAME = "WinThingsDock"


class DockSettings(SettingsWindow):
    APP = "dock"
    TITLE = "Dock"

    def __init__(self, config, script_path, list_apps, on_change=None):
        self.script_path = script_path
        self.list_apps = list_apps
        self._pin_rows = None
        super().__init__(config, on_change)
        self.protocol("WM_DELETE_WINDOW", self.hide)

    def build(self, body):
        self.add_section("Layout")
        self.add_dropdown("Screen edge", "edge", list(EDGES))
        self.add_dropdown("Width", "span", list(SPANS),
                          hint="full spans the whole edge; fit hugs the icons")
        self.add_slider("Icon size", "icon_size", 16, 64, step=2, fmt="{:.0f} px")
        self.add_slider("Edge margin", "margin", 0, 80, step=2, fmt="{:.0f} px")

        self.add_section("Windows")
        self.add_switch("Expand windows into chips", "show_chips",
                        hint="Shows every window of an app beside its icon "
                             "instead of hiding them behind a hover flyout")
        self.add_slider("Chip title length", "chip_chars", 8, 60, step=1,
                        fmt="{:.0f} ch")
        self.add_slider("Window scan interval", "window_refresh", 0.25, 5.0,
                        step=0.25, fmt="{:.2f} s")

        self.add_section("Monitors")
        self.add_slider("Poll interval", "poll_interval", 0.5, 10.0, step=0.5,
                        fmt="{:.1f} s")
        self.add_switch("CPU", "show_cpu")
        self.add_switch("RAM", "show_ram")
        if gpu_available():
            self.add_switch("GPU", "show_gpu", hint=gpu_name() or "NVIDIA via NVML")
        else:
            self._row("GPU", "No NVIDIA GPU found through NVML - readout hidden")
        self.add_switch("Network up", "show_net_up")
        self.add_switch("Network down", "show_net_down")

        self.add_section("Pinned apps")
        ctk.CTkLabel(
            body, text="Pinned apps stay on the dock when closed. "
                       "Right-click any dock icon to pin or unpin it.",
            anchor="w", justify="left", wraplength=400,
            font=theme.font(ctk, theme.FONT_SIZE_SMALL), text_color=theme.FG_FAINT,
        ).pack(fill="x", padx=theme.PAD_SMALL, pady=(0, theme.PAD_SMALL))
        self._pin_rows = ctk.CTkFrame(body, fg_color="transparent")
        self._pin_rows.pack(fill="x")
        self.refresh_pins()

        self.add_section("General")
        self.add_switch("Start with Windows", "autostart",
                        on_toggle=self._set_autostart)

        self.add_button("Refresh list", self.refresh_pins)
        self.add_button("Close", self.hide, primary=True)
        self.set_status("Running")

    # -- pinned apps ------------------------------------------------------
    def _pinned(self):
        return [p for p in (self.config_obj["pinned"] or []) if p]

    def _set_pinned(self, exe, on):
        pinned = self._pinned()
        lowered = [p.lower() for p in pinned]
        if on and exe.lower() not in lowered:
            pinned.append(exe)
        elif not on:
            pinned = [p for p in pinned if p.lower() != exe.lower()]
        self.config_obj["pinned"] = pinned
        if self._on_change:
            self._on_change(self.config_obj)

    def refresh_pins(self):
        if self._pin_rows is None:
            return
        for child in self._pin_rows.winfo_children():
            child.destroy()

        pinned = self._pinned()
        known = {}
        for exe in pinned:
            known[exe.lower()] = exe
        for entry in self.list_apps():
            if entry.exe:
                known.setdefault(entry.exe.lower(), entry.exe)

        if not known:
            ctk.CTkLabel(
                self._pin_rows, text="No apps with a readable path are running.",
                anchor="w", font=theme.font(ctk, theme.FONT_SIZE_SMALL),
                text_color=theme.FG_FAINT,
            ).pack(fill="x", padx=theme.PAD_SMALL)
            return

        lowered = {p.lower() for p in pinned}
        for key in sorted(known, key=lambda k: os.path.basename(k)):
            exe = known[key]
            row = ctk.CTkFrame(self._pin_rows, fg_color="transparent")
            row.pack(fill="x", padx=theme.PAD_SMALL, pady=1)
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                row, text=os.path.splitext(os.path.basename(exe))[0], anchor="w",
                font=theme.font(ctk), text_color=theme.FG,
            ).grid(row=0, column=0, sticky="w")
            switch = ctk.CTkSwitch(
                row, text="", width=44, progress_color=theme.ACCENT,
                command=lambda e=exe, s=key: self._set_pinned(
                    e, s not in {p.lower() for p in self._pinned()}
                ),
            )
            if key in lowered:
                switch.select()
            switch.grid(row=0, column=1, sticky="e")

    # -- misc -------------------------------------------------------------
    def _set_autostart(self, enabled):
        autostart.set_enabled(APP_NAME, self.script_path, enabled)

    def show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        self.refresh_pins()

    def hide(self):
        """Withdraw instead of destroy: common.ui schedules a repeating after()
        that errors out if its interpreter dies, and reopening is instant."""
        self.withdraw()
