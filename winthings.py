#!/usr/bin/env python3
"""Launcher for the WinThings suite.

Each app is an independent process with its own tray icon, so they can be run
individually. This just starts/stops them together for convenience.

    python winthings.py              start the default apps not already running
    python winthings.py reader       start only that one
    python winthings.py clipboard    retired apps still start when named
    python winthings.py --status     what's running
    python winthings.py --stop       stop everything
    python winthings.py --console    start with consoles attached (for debugging)

Apps guard themselves with a named mutex (common/singleton.py), so starting one
that's already up is a no-op rather than a duplicate tray icon.

The clipboard app is retired in favour of CopyQ: it stays in the repo and runs
if asked for by name, but a bare `winthings.py` no longer starts it.
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APPS = {
    "clipboard": "Clipboard history with search",
    "reader": "Read selected text aloud",
    "transcriber": "Audio-to-text (Whisper)",
}
RETIRED = {"clipboard"}  # not started by default; see the module docstring
DEFAULT_APPS = [app for app in APPS if app not in RETIRED]


def entry_point(app):
    return os.path.join(HERE, app, "app.py")


def interpreter(console):
    """pythonw.exe keeps background apps from flashing a console window."""
    exe = sys.executable or "python.exe"
    if console:
        return exe
    noconsole = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return noconsole if os.path.exists(noconsole) else exe


def running(app):
    """True if the app's singleton mutex is already held."""
    try:
        import win32event
        import win32api
        import winerror
    except ImportError:
        return False
    handle = win32event.CreateMutex(None, False, f"Local\\WinThings.{app}")
    exists = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
    win32api.CloseHandle(handle)
    return exists


def start(app, console=False):
    script = entry_point(app)
    if not os.path.exists(script):
        print(f"  {app}: not built yet ({script} missing)")
        return False
    if running(app):
        print(f"  {app}: already running")
        return False
    flags = 0 if console else subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen([interpreter(console), script], cwd=HERE, creationflags=flags)
    except OSError as exc:
        print(f"  {app}: failed to start - {exc}")
        return False
    print(f"  {app}: started")
    return True


def stop(app):
    """Terminate any python process whose command line points at the app."""
    try:
        import psutil
    except ImportError:
        print("psutil not installed - cannot stop apps")
        return False
    target = os.path.normcase(entry_point(app))
    killed = 0
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            cmdline = proc.info["cmdline"] or []
            if any(os.path.normcase(str(a)) == target for a in cmdline):
                proc.terminate()
                killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    print(f"  {app}: {'stopped' if killed else 'not running'}")
    return bool(killed)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("apps", nargs="*", metavar="APP",
                    help=f"apps to act on: {', '.join(APPS)} (default: "
                         f"{', '.join(DEFAULT_APPS)} to start, all to stop)")
    ap.add_argument("--stop", action="store_true", help="stop instead of start")
    ap.add_argument("--status", action="store_true", help="show what's running")
    ap.add_argument("--console", action="store_true",
                    help="attach a console (shows tracebacks)")
    args = ap.parse_args()

    unknown = [a for a in args.apps if a not in APPS]
    if unknown:
        ap.error(f"unknown app(s): {', '.join(unknown)}. Choose from: {', '.join(APPS)}")
    # Stopping with no names still stops a retired app that was started by hand.
    targets = args.apps or (list(APPS) if args.stop else DEFAULT_APPS)

    if args.status:
        for app in APPS:
            note = "" if os.path.exists(entry_point(app)) else "  (not built)"
            if app in RETIRED:
                note += "  (retired; start by name)"
            print(f"  {app:<11} {'running' if running(app) else 'stopped'}{note}")
        return

    print("Stopping:" if args.stop else "Starting:")
    for app in targets:
        if args.stop:
            stop(app)
        else:
            start(app, args.console)


if __name__ == "__main__":
    main()
