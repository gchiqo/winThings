"""Clipboard manager: a searchable, persistent clipboard history in the tray.

Windows' own Win+V cannot be searched and is emptied on every reboot, which is
the entire reason this exists. History lives in SQLite under
%APPDATA%\\WinThings\\clipboard\\history.db and survives restarts.

Thread layout, because most of the subtlety is here:

  main      Tk. Owns a hidden root window so the popup and the settings window
            always have a running mainloop, even when nothing is on screen.
  listener  a message-only window pumping WM_CLIPBOARDUPDATE (winclip.Listener)
  tray      pystray's own message loop
  hook      `keyboard`'s low-level keyboard hook, where the hotkey lands

Only the main thread may touch Tk, so the other three marshal everything through
common.ui.call_on_ui_thread.
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import customtkinter as ctk

from common import singleton, theme, ui
from common.config import Config
from common.hotkey import HotkeyManager, normalize, pretty, send as send_keys
from common.paths import data_path, log_path
from common.tray import Tray

import winclip
from popup import Popup
from settings import ClipboardSettings
from store import Store

APP = "clipboard"

DEFAULTS = {
    "hotkey": "ctrl+shift+v",
    "suppress_hotkey": True,
    "max_items": 500,
    "max_age_days": 30,
    "auto_paste": True,
    "capture_images": True,
    "exclude_processes": (
        "keepass.exe, keepassxc.exe, 1password.exe, bitwarden.exe, "
        "lastpass.exe, dashlane.exe, protonpass.exe"
    ),
    "start_with_windows": False,
}

log = logging.getLogger(APP)


class ClipboardApp:
    def __init__(self, db_path=None):
        self.cfg = Config(APP, DEFAULTS)
        self.store = Store(db_path or data_path(APP, "history.db"))
        self.hotkeys = HotkeyManager()
        self.prune()

        # Every clipboard write bumps a global counter. Recording it after our
        # own writes is how we tell "the user copied something" apart from
        # "we just put a history item back on the clipboard".
        self.own_seq = winclip.sequence()
        self.prev_hwnd = 0
        self.popup_open = False
        self.settings_win = None
        self.bound_as = None

        theme.apply(ctk)
        self.root = ctk.CTk()
        self.root.withdraw()
        # SettingsWindow normally starts this pump, but the settings window is
        # usually closed and the tray/hook threads still need to reach Tk.
        ui._drain(self.root)

        self.popup = Popup(self.root, self.store, self.on_choose)
        self.listener = winclip.Listener(self.on_clipboard_change)
        self.tray = self.build_tray()

    # -- setup ------------------------------------------------------------
    def build_tray(self):
        tray = Tray(APP, "Clipboard Manager", "V", theme.ACCENT)
        tray.add("Open history", lambda: ui.call_on_ui_thread(self.show_popup),
                 default=True)
        tray.add("Settings…", lambda: ui.call_on_ui_thread(self.show_settings))
        tray.separator()
        tray.add("Clear unpinned", self.clear_unpinned)
        tray.separator()
        tray.add("Quit", self.quit)
        return tray

    def start(self):
        self.listener.start()
        self.tray.start()
        self.bind_hotkey()
        self.cfg.subscribe(self.apply_config)
        return self

    def bind_hotkey(self):
        spec = self.cfg["hotkey"]
        suppress = bool(self.cfg["suppress_hotkey"])
        ok = self.hotkeys.bind("popup", spec, self.on_hotkey, suppress=suppress)
        self.bound_as = (normalize(spec), suppress)
        self.tray.set_tooltip(
            f"Clipboard Manager — {pretty(spec)}" if ok
            else f"Clipboard Manager — {pretty(spec)} NOT BOUND"
        )
        if not ok:
            log.warning("hotkey %s did not bind: %s", spec, self.hotkeys.error("popup"))
        return ok

    def apply_config(self, cfg):
        """Fires on every config save; re-reads everything that matters."""
        wanted = (normalize(cfg["hotkey"]), bool(cfg["suppress_hotkey"]))
        if wanted != self.bound_as or not self.hotkeys.bound("popup"):
            self.bind_hotkey()
        self.prune()

    def prune(self):
        return self.store.prune(
            self.cfg.integer("max_items", 0, 100000),
            self.cfg.integer("max_age_days", 0, 3650),
        )

    def excluded_processes(self):
        raw = self.cfg["exclude_processes"] or ""
        return {p.strip().lower() for p in str(raw).split(",") if p.strip()}

    # -- capture (listener thread) ----------------------------------------
    def on_clipboard_change(self):
        if winclip.sequence() <= self.own_seq:
            return  # our own write, or a stale event from before it

        source = winclip.foreground_process()
        if source and source in self.excluded_processes():
            log.info("skipped clipboard change from %s", source)
            return

        try:
            item = winclip.read()
        except winclip.ClipboardBusy:
            log.info("clipboard was locked by another app; entry dropped")
            return
        if item is None:
            return
        if item["kind"] == "image" and not self.cfg["capture_images"]:
            return

        self.store.add(**item)
        self.prune()
        if self.popup_open:
            ui.call_on_ui_thread(self.popup.refresh)

    # -- hotkey (hook thread) ---------------------------------------------
    def on_hotkey(self):
        if not self.popup_open:
            # Read the foreground window here, before our popup becomes the
            # foreground window and the answer becomes "us".
            self.prev_hwnd = winclip.foreground_window()
        ui.call_on_ui_thread(self.toggle_popup)

    # -- UI thread --------------------------------------------------------
    def toggle_popup(self):
        if self.popup_open:
            self.hide_popup()
        else:
            self.show_popup()

    def show_popup(self):
        self.popup_open = True
        self.popup.show()

    def hide_popup(self):
        self.popup_open = False
        self.popup.hide()

    def on_choose(self, entry_id):
        """Popup selection: put the entry back on the clipboard, then paste it."""
        self.popup_open = False
        row = self.store.get(entry_id)
        if row is None:
            return
        target = self.prev_hwnd
        try:
            if row["kind"] == "image":
                self.own_seq = winclip.write_image(bytes(row["content"]))
            else:
                self.own_seq = winclip.write_text(bytes(row["content"]).decode("utf-8"))
        except (winclip.ClipboardBusy, OSError, ValueError) as exc:
            log.warning("could not put entry %s on the clipboard: %s", entry_id, exc)
            return
        self.store.touch(entry_id)
        if self.cfg["auto_paste"] and target:
            winclip.focus_window(target)
            # Give the target window a moment to actually become foreground;
            # a keystroke sent before that lands in nothing.
            self.root.after(70, self.send_paste)

    def send_paste(self):
        import keyboard

        # The user may still be holding the hotkey's modifiers, which would turn
        # our Ctrl+V into Ctrl+Shift+V in the target app. Windows is left alone
        # on purpose: a lone Win keyup opens the Start menu.
        for mod in ("shift", "ctrl", "alt"):
            try:
                if keyboard.is_pressed(mod):
                    keyboard.release(mod)
            except Exception:
                pass
        send_keys("ctrl+v")

    def show_settings(self):
        if self.settings_win is None or not self.settings_win.winfo_exists():
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
            # No on_change hook: apply_config is already subscribed to the Config
            # itself, so every save reaches it however it was made.
            self.settings_win = ClipboardSettings(self.cfg, self.store, script)
        self.settings_win.show()
        return self.settings_win

    def clear_unpinned(self):
        removed = self.store.clear(keep_pinned=True)
        self.tray.notify(f"Cleared {removed} unpinned entries")

    # -- shutdown ---------------------------------------------------------
    def quit(self):
        self.hotkeys.unbind_all()
        self.listener.stop()
        self.tray.stop()
        ui.call_on_ui_thread(self._teardown_ui)

    def _teardown_ui(self):
        for window in (self.settings_win, self.popup):
            try:
                if window is not None and window.winfo_exists():
                    window.destroy()
            except Exception:
                pass
        self.root.quit()

    def run(self):
        self.root.mainloop()
        self.store.close()


def selftest(app):
    """Exercise every window and then exit, so nothing is left on the desktop."""
    results = []

    def step(delay, label, fn):
        def go():
            try:
                fn()
                results.append(f"ok   {label}")
            except Exception as exc:
                results.append(f"FAIL {label}: {exc!r}")
        app.root.after(delay, go)

    def check_hotkey():
        assert app.hotkeys.bound("popup"), app.hotkeys.error("popup")

    # A fresh install has an empty history, and an empty list is then the
    # correct result rather than a failure. Seed with --db to exercise these
    # properly: see README.
    def seeded():
        return app.store.count()[0] > 0

    def search_popup():
        app.popup.search.insert(0, "a")
        app.popup.refresh()
        rows = app.popup.tree.get_children()
        if not seeded():
            results.append("     (history empty - search assertion skipped)")
            return
        assert rows, "search returned nothing to show"
        results.append(f"     focus={app.popup.focus_get()} rows={len(rows)}")

    def check_rows():
        if not seeded():
            results.append("     (history empty - list assertion skipped)")
            return
        assert app.popup.tree.get_children(), "history list rendered empty"

    step(500, "popup shows", app.show_popup)
    step(900, "popup listed history", check_rows)
    step(1400, "popup search 'a'", search_popup)
    step(1900, "popup hides", app.hide_popup)
    step(2200, "settings opens", app.show_settings)
    step(3200, "settings hides", lambda: app.settings_win.hide())
    step(3400, "settings reopens", app.show_settings)
    step(3600, "hotkey bound", check_hotkey)

    def finish():
        app.quit()
        print("\n".join(results))
        print("SELFTEST DONE")

    app.root.after(4200, finish)


def main():
    parser = argparse.ArgumentParser(description="WinThings clipboard manager")
    parser.add_argument("--selftest", action="store_true",
                        help="open every window briefly, then exit")
    parser.add_argument("--db", help="use an alternate history database")
    args = parser.parse_args()

    if not args.selftest:
        singleton.exit_if_running(APP)

    logging.basicConfig(
        filename=log_path(APP), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    app = ClipboardApp(db_path=args.db).start()
    if args.selftest:
        selftest(app)
    app.run()


if __name__ == "__main__":
    main()
