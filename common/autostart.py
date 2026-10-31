"""Start-with-Windows: StartupTask API (MSIX packages) or registry Run key.

In development (bare Python): writes HKCU\\Run pointing at pythonw.exe + script.
When frozen as a one-folder bundle (PyInstaller, not in MSIX): writes HKCU\\Run
pointing at the exe itself.
When packaged as MSIX: uses the Windows.ApplicationModel.StartupTask API via
the optional `winrt` package, falling back to the registry if unavailable.
"""

import os
import sys

STARTUP_TASK_ID = "WinThingsReaderStartup"
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


def in_msix_package():
    """True when running inside an MSIX container (GetCurrentPackageFullName)."""
    try:
        import ctypes
        size = ctypes.c_uint32(256)
        buf = ctypes.create_unicode_buffer(256)
        # Returns 0 (ERROR_SUCCESS) when a package identity is found.
        return ctypes.windll.kernel32.GetCurrentPackageFullName(
            ctypes.byref(size), buf) == 0
    except Exception:
        return False


def _msix_is_enabled():
    """StartupTask enabled state via winrt, or None if winrt unavailable."""
    try:
        import asyncio
        from winrt.windows.applicationmodel import StartupTask, StartupTaskState
        loop = asyncio.new_event_loop()
        task = loop.run_until_complete(StartupTask.get_async(STARTUP_TASK_ID))
        loop.close()
        return task.state not in (
            StartupTaskState.DISABLED,
            StartupTaskState.DISABLED_BY_USER,
            StartupTaskState.DISABLED_BY_POLICY,
        )
    except Exception:
        return None


def _msix_set_enabled(on):
    """Enable/disable StartupTask via winrt. Returns True on success."""
    try:
        import asyncio
        from winrt.windows.applicationmodel import StartupTask
        loop = asyncio.new_event_loop()
        task = loop.run_until_complete(StartupTask.get_async(STARTUP_TASK_ID))
        if on:
            loop.run_until_complete(task.request_enable_async())
        else:
            task.disable()
        loop.close()
        return True
    except Exception:
        return False


def is_enabled(name):
    if in_msix_package():
        result = _msix_is_enabled()
        if result is not None:
            return result
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


def enable(name, script):
    if in_msix_package():
        if _msix_set_enabled(True):
            return True
    if getattr(sys, 'frozen', False):
        cmd = f'"{sys.executable}"'
    else:
        cmd = command_for(script)
    import winreg
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
        return True
    except OSError:
        return False


def disable(name):
    if in_msix_package():
        _msix_set_enabled(False)
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
