"""Finding the windows a taskbar should show, and switching to them.

EnumWindows returns every top-level window in the session, most of which are
invisible message-only helpers, tool palettes or the cloaked shells Windows
keeps around for suspended UWP apps. The filter here is the usual alt-tab
ruleset: visible, titled, unowned, not WS_EX_TOOLWINDOW (unless it explicitly
opts back in with WS_EX_APPWINDOW), and not DWM-cloaked.

Activation is the other half. SetForegroundWindow is deliberately crippled by
Windows: it only succeeds for the process that already owns the foreground, so
a dock that never takes focus (which is what we want) can never call it
straight. activate() walks a ladder of the documented-and-undocumented
workarounds until GetForegroundWindow agrees.
"""

import ctypes
from ctypes import wintypes

import psutil
import win32api
import win32con
import win32gui
import win32process

WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
DWMWA_CLOAKED = 14
GW_OWNER = 4

_dwmapi = ctypes.WinDLL("dwmapi")
_user32 = ctypes.WinDLL("user32")

# Undocumented but stable since XP and what Explorer's own taskbar uses; it is
# the only call that reliably un-minimises and foregrounds in one step.
_user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
_user32.SwitchToThisWindow.restype = None


class WindowInfo:
    __slots__ = ("hwnd", "title", "pid", "exe", "name", "minimized")

    def __init__(self, hwnd, title, pid, exe, name, minimized):
        self.hwnd = hwnd
        self.title = title
        self.pid = pid
        self.exe = exe
        self.name = name
        self.minimized = minimized

    @property
    def key(self):
        """Grouping key — one dock icon per executable."""
        return (self.exe or self.name or "?").lower()

    def __repr__(self):
        return f"<WindowInfo {self.name} {self.hwnd:#x} {self.title[:40]!r}>"


def _is_cloaked(hwnd):
    """UWP apps leave a titled, visible, but cloaked shell window behind.

    Without this check every packaged app the user has ever launched shows up
    in the dock forever.
    """
    value = ctypes.c_int(0)
    hr = _dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), ctypes.c_int(DWMWA_CLOAKED),
        ctypes.byref(value), ctypes.sizeof(value),
    )
    return hr == 0 and value.value != 0


def is_taskbar_window(hwnd):
    if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
        return False
    if not win32gui.GetWindowText(hwnd):
        return False
    if win32gui.GetWindow(hwnd, GW_OWNER):
        return False  # dialogs and palettes belong to their owner's button
    try:
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    except win32gui.error:
        return False
    if ex_style & WS_EX_TOOLWINDOW and not ex_style & WS_EX_APPWINDOW:
        return False
    return not _is_cloaked(hwnd)


def _real_pid(hwnd, frame_pid):
    """Unwrap ApplicationFrameHost, which hosts UWP windows for other processes.

    The visible window belongs to ApplicationFrameHost.exe; the app itself owns
    a child CoreWindow. Without this every store app would share one icon.
    """
    found = []

    def child(child_hwnd, _):
        _, pid = win32process.GetWindowThreadProcessId(child_hwnd)
        if pid and pid != frame_pid:
            found.append(pid)
        return True

    try:
        win32gui.EnumChildWindows(hwnd, child, None)
    except win32gui.error:
        pass  # window died mid-enumeration
    return found[0] if found else frame_pid


def _process_info(hwnd, cache):
    """(exe_path, friendly_name) for a window, tolerating denied access."""
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
    except win32gui.error:
        return None, None, None
    if not pid:
        return None, None, None

    try:
        proc = psutil.Process(pid)
        name = proc.name()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return pid, None, None

    if name.lower() == "applicationframehost.exe":
        pid = _real_pid(hwnd, pid)
        if pid in cache:
            return (pid,) + cache[pid]
        try:
            proc = psutil.Process(pid)
            name = proc.name()
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            return pid, None, None

    if pid in cache:
        return (pid,) + cache[pid]
    try:
        exe = proc.exe()
    except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
        exe = None  # elevated process: name still works, path doesn't
    cache[pid] = (exe, name)
    return pid, exe, name


def enumerate_windows(skip_pids=()):
    """All taskbar-worthy windows, in z-order (topmost first, as EnumWindows gives)."""
    result = []
    proc_cache = {}
    skip = set(skip_pids)

    def visit(hwnd, _):
        if not is_taskbar_window(hwnd):
            return True
        pid, exe, name = _process_info(hwnd, proc_cache)
        if pid is None or pid in skip:
            return True
        result.append(WindowInfo(
            hwnd=hwnd,
            title=win32gui.GetWindowText(hwnd),
            pid=pid,
            exe=exe,
            name=name or f"pid {pid}",
            minimized=bool(win32gui.IsIconic(hwnd)),
        ))
        return True

    win32gui.EnumWindows(visit, None)
    return result


def group_windows(windows):
    """Windows bucketed by executable, preserving first-seen order."""
    groups = {}
    for win in windows:
        groups.setdefault(win.key, []).append(win)
    return groups


# -- activation -----------------------------------------------------------
def foreground_hwnd():
    return win32gui.GetForegroundWindow()


def _nudge_input():
    """Synthesise an ALT tap.

    Windows lifts the foreground lock for a process that "received the last
    input event", and a bare ALT press is the least intrusive way to claim it.
    """
    win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
    win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)


def activate(hwnd):
    """Bring hwnd to the foreground. Returns True if Windows actually agreed."""
    if not win32gui.IsWindow(hwnd):
        return False
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    our_tid = win32api.GetCurrentThreadId()
    fg = win32gui.GetForegroundWindow()
    fg_tid = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
    attached = False
    if fg_tid and fg_tid != our_tid:
        try:
            attached = bool(win32process.AttachThreadInput(our_tid, fg_tid, True))
        except win32gui.error:
            attached = False
    try:
        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except win32gui.error:
            pass
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(our_tid, fg_tid, False)
            except win32gui.error:
                pass
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    _nudge_input()
    try:
        win32gui.SetForegroundWindow(hwnd)
    except win32gui.error:
        pass
    if win32gui.GetForegroundWindow() == hwnd:
        return True

    _user32.SwitchToThisWindow(hwnd, True)
    return win32gui.GetForegroundWindow() == hwnd


def minimize(hwnd):
    if win32gui.IsWindow(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)


def toggle(hwnd):
    """Taskbar button behaviour: focus, or minimise if already focused."""
    if win32gui.GetForegroundWindow() == hwnd and not win32gui.IsIconic(hwnd):
        minimize(hwnd)
        return False
    return activate(hwnd)


def close_window(hwnd):
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except win32gui.error:
        pass
