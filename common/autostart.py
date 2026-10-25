"""Start-with-Windows via the per-user Run key.

HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run needs no admin rights and
survives Windows upgrades. Launched with pythonw.exe so no console flashes up.

The registered interpreter is the repo's own venv (.venv, created by uv) when it
exists, whichever interpreter happened to launch the app, so a login start gets
the same dependencies as `.venv\\Scripts\\python.exe app.py` does.
"""

import os
import sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PYTHONW = os.path.join(REPO, ".venv", "Scripts", "pythonw.exe")


def _pythonw():
    """The repo venv's pythonw.exe, else pythonw.exe next to the running
    interpreter, else that interpreter."""
    if os.path.exists(VENV_PYTHONW):
        return VENV_PYTHONW
    exe = sys.executable or "python.exe"
    if os.path.basename(exe).lower() == "python.exe":
        noconsole = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(noconsole):
            return noconsole
    return exe


def command_for(script):
    return f'"{_pythonw()}" "{os.path.abspath(script)}"'


def is_enabled(name):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


def enable(name, script):
    import winreg

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command_for(script))
        return True
    except OSError:
        return False


def disable(name):
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass
    return True


def set_enabled(name, script, on):
    return enable(name, script) if on else disable(name)
