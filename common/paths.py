"""Where each app keeps its config, data and logs.

Everything lives under %APPDATA%\\WinThings\\<app>\\ so the three utilities sit
together and can be wiped in one go.
"""

import os

SUITE = "WinThings"


def _roaming():
    return os.environ.get("APPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Roaming"
    )


def app_dir(app):
    """Return (and create) the per-app directory, e.g. .../WinThings/clipboard."""
    path = os.path.join(_roaming(), SUITE, app)
    os.makedirs(path, exist_ok=True)
    return path


def config_path(app):
    return os.path.join(app_dir(app), "config.json")


def data_path(app, filename):
    return os.path.join(app_dir(app), filename)


def log_path(app):
    return os.path.join(app_dir(app), f"{app}.log")


def repo_dir():
    """Root of the checkout — used to find bundled assets (voices, icons)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
