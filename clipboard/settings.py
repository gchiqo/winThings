"""Settings window, opened from the tray icon.

Everything here writes straight through to the Config, and the app re-reads it
via the on_change callback, so there is no Apply button and no way for the
window and the running capture loop to disagree.
"""

from common import autostart
from common.ui import SettingsWindow

APP = "clipboard"
# HKCU\...\Run is a shared namespace, so the entry needs a name no other
# installer would plausibly pick. Matches WinThingsReader / WinThingsMonitors.
RUN_NAME = "WinThingsClipboard"


class ClipboardSettings(SettingsWindow):
    APP = APP
    TITLE = "Clipboard Manager"

    def __init__(self, config, store, script):
        self.store = store
        self.script = script
        self._pending = None  # which destructive button is awaiting confirmation
        super().__init__(config)

    def build(self, body):
        self.add_section("Hotkey")
        self.add_hotkey(
            "Open history", "hotkey",
            hint="Avoid Win+V, which Windows reserves for its own history.",
        )
        self.add_switch(
            "Swallow the hotkey", "suppress_hotkey",
            hint="Stop the combination reaching the app underneath. Turn off if "
                 "it clashes with something you use.",
        )

        self.add_section("History")
        self.add_entry(
            "Keep at most (items)", "max_items", cast=int,
            hint="Oldest unpinned entries are dropped past this. 0 for no limit.",
        )
        self.add_entry(
            "Discard after (days)", "max_age_days", cast=int,
            hint="Unpinned entries older than this are dropped. 0 to keep forever.",
        )
        self.add_switch("Capture images", "capture_images")

        self.add_section("Pasting")
        self.add_switch(
            "Paste after selecting", "auto_paste",
            hint="Restores focus to the window you came from and sends Ctrl+V. "
                 "Off means the item is only copied.",
        )

        self.add_section("Privacy")
        self.add_entry(
            "Never capture from", "exclude_processes",
            hint="Comma-separated executable names. Best-effort only: it reads "
                 "whichever window has focus when the copy lands, and cannot see "
                 "elevated apps. Content that an app marks as excluded is always "
                 "skipped regardless of this list.",
        )

        self.add_section("System")
        # The registry is the source of truth; the config key just mirrors it so
        # add_switch has something to read.
        self.config_obj["start_with_windows"] = autostart.is_enabled(RUN_NAME)
        self.add_switch(
            "Start with Windows", "start_with_windows",
            on_toggle=lambda on: autostart.set_enabled(RUN_NAME, self.script, on),
        )

        self._clear_unpinned = self.add_button("Clear unpinned", self._confirm_unpinned)
        self._clear_all = self.add_button("Clear all", self._confirm_all, danger=True)
        self.add_button("Close", self.hide)
        self.protocol("WM_DELETE_WINDOW", self.hide)
        self.refresh_status()

    def refresh_status(self):
        total, pinned = self.store.count()
        self.set_status(f"{total} entries stored, {pinned} pinned")

    # Hidden rather than destroyed, and reused on the next open: SettingsWindow
    # keeps a repeating after() alive to drain the cross-thread UI queue, and
    # destroying the window leaves that timer to fire into a dead interpreter.
    def hide(self):
        self._disarm()
        self.withdraw()

    def show(self):
        self.refresh_status()
        self.deiconify()
        self.lift()
        self.focus_force()

    # -- destructive actions ----------------------------------------------
    def _confirm(self, button, action):
        """First click arms, second click fires; disarms itself after 4s."""
        if self._pending is not button:
            self._disarm()
            self._pending = button
            button.configure(text="Click again to confirm")
            self.after(4000, self._disarm)
            return
        self._disarm()
        removed = action()
        self.refresh_status()
        self.flash(f"Removed {removed} entries")

    def _disarm(self):
        for button, label in (
            (getattr(self, "_clear_unpinned", None), "Clear unpinned"),
            (getattr(self, "_clear_all", None), "Clear all"),
        ):
            if button is not None:
                button.configure(text=label)
        self._pending = None

    def _confirm_unpinned(self):
        self._confirm(self._clear_unpinned, lambda: self.store.clear(keep_pinned=True))

    def _confirm_all(self):
        self._confirm(self._clear_all, lambda: self.store.clear(keep_pinned=False))
