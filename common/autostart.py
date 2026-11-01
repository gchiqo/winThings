"""Start-with-Windows: StartupTask API (MSIX packages) or registry Run key.

In development (bare Python): writes HKCU\\Run pointing at pythonw.exe + script.
When frozen as a one-folder bundle (PyInstaller, not in MSIX): writes HKCU\\Run
pointing at the exe itself.
When packaged as MSIX: only the package's StartupTask
(Windows.ApplicationModel.StartupTask via the `winrt` package); Windows ignores
Run values written from inside a package.
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
    """Enable/disable the StartupTask via winrt. True only if it ended up in
    the asked state: Windows won't let an app re-enable a task the user turned
    off in Settings > Apps > Startup (DISABLED_BY_USER), or one policy blocks."""
    try:
        import asyncio
        from winrt.windows.applicationmodel import StartupTask, StartupTaskState
        loop = asyncio.new_event_loop()
        try:
            task = loop.run_until_complete(StartupTask.get_async(STARTUP_TASK_ID))
            if on:
                state = loop.run_until_complete(task.request_enable_async())
                return state in (StartupTaskState.ENABLED,
                                 StartupTaskState.ENABLED_BY_POLICY)
            task.disable()
            return True
        finally:
            loop.close()
    except Exception:
        return False


def is_enabled(name):
    if in_msix_package():
        # Only the package's StartupTask counts. A Run value seen from in here
        # may be the pre-Store script's (registry reads fall through to the real
        # hive), which made the app show "on" while Windows had it off.
        return bool(_msix_is_enabled())
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, name)
        return True
    except OSError:
        return False


def enable(name, script):
    if in_msix_package():
        # The StartupTask is the only autostart Windows honours for a package;
        # a Run value written from inside it is virtualized and never fires.
        return _msix_set_enabled(True)
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
        return _msix_set_enabled(False)
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
