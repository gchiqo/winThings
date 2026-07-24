"""Shared plumbing for the WinThings tray utilities.

Each app (dock, clipboard, reader) is an independent process with its own tray
icon and config file, but they pull their config handling, theme, tray helper,
hotkey registration and settings-window widgets from here so they look and
behave like one suite.
"""

from .paths import app_dir, config_path, data_path, log_path
from .config import Config
from . import theme

__all__ = ["app_dir", "config_path", "data_path", "log_path", "Config", "theme"]
