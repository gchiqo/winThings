"""Remember which window the user was actually working in.

The hotkey path doesn't need this: when Ctrl+Shift+A fires, the app holding the
selection still has focus. But "Read selection" clicked in our settings window
or in the tray menu does need it — by then *we* are the foreground window (or
the shell's tray is), so a synthetic Ctrl+C would copy from the wrong place.

Windows gives no "previously active window" API, so we poll GetForegroundWindow
and keep the last handle that wasn't ours, wasn't the taskbar, and wasn't a
tray/menu popup.
"""

import os
import threading
import time

import win32gui
import win32process

# Shell windows that briefly become foreground when a tray menu opens.
_SHELL_CLASSES = {"Shell_TrayWnd", "NotifyIconOverflowWindow", "#32768",
                  "Windows.UI.Core.CoreWindow", "Progman", "WorkerW"}


class Tracker:
    def __init__(self, interval=0.4):
        self.interval = interval
        self._hwnd = None
        self._pid = os.getpid()
        self._thread = None
        self._running = False

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._running = False

    def _poll(self):
        while self._running:
            hwnd = self._candidate()
            if hwnd:
                self._hwnd = hwnd
            time.sleep(self.interval)

    def _candidate(self):
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd or not win32gui.IsWindowVisible(hwnd):
                return None
            if win32gui.GetClassName(hwnd) in _SHELL_CLASSES:
                return None
            if win32process.GetWindowThreadProcessId(hwnd)[1] == self._pid:
                return None
            return hwnd
        except Exception:
            return None

    def restore(self, settle=0.15):
        """Bring the remembered window back to the front. True if we tried."""
        hwnd = self._hwnd
        if not hwnd:
            return False
        try:
            if not win32gui.IsWindow(hwnd):
                return False
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            return False  # Windows refuses this unless we are the foreground app
        time.sleep(settle)
        return True
