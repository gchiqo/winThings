"""Single-instance guard via a named Windows mutex.

Each app calls acquire() at startup; a second launch exits instead of stacking
a duplicate tray icon and a duplicate keyboard hook (two hooks on one hotkey
means the callback fires twice).
"""

import sys

_held = []  # keep handles alive for process lifetime


def acquire(name):
    """True if we own the lock; False if another instance is already running."""
    try:
        import win32api
        import win32event
        import winerror
    except ImportError:
        return True  # pywin32 missing — don't block the app over a nicety

    handle = win32event.CreateMutex(None, False, f"Global\\WinThings.{name}")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return False
    _held.append(handle)
    return True


def exit_if_running(name, message=None):
    if not acquire(name):
        print(message or f"{name} is already running.", file=sys.stderr)
        sys.exit(0)
