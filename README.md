# WinThings

Independent Windows 11 tray utilities that share a look, a settings pattern and
a config layout. Each runs as its own background process with its own tray icon;
none touches Explorer, the Start menu or the taskbar.

| App | What it does | Default hotkey |
|---|---|---|
| [reader](reader/) | Reads the selected text aloud with a Piper neural voice | `Ctrl+Shift+A` |
| [transcriber](transcriber/) | Local speech-to-text for audio files (faster-whisper) | — |
| [clipboard](clipboard/) | **Retired** in favour of [CopyQ](https://hluk.github.io/CopyQ/). Kept in the repo, not started by default | `Ctrl+Shift+V` |

Each can start with Windows via `HKCU\...\Run` (`WinThingsReader`, …), toggled
from its settings window; nothing is registered by default.

There is no system-monitor or dock app. Earlier versions had a floating dock
panel and a set of CPU/RAM/GPU tray readouts; both were removed as unwanted. They
remain in git history at commit `c094aba` (`git checkout c094aba -- dock monitors`)
if anyone ever wants them back.

---

## Screenshots

| | |
|---|---|
| ![clipboard](screenshots/clipboard-search.png) | Clipboard popup filtering live as you type. |
| ![reader](screenshots/reader-settings.png) | Reader settings, with the live status dot reporting the bound hotkey. |

Also in [screenshots/](screenshots/): `clipboard-popup.png`,
`clipboard-settings.png`.

---

## Setup

The repo lives at `C:\dev\scraps\winThings` and uses a [uv](https://docs.astral.sh/uv/)
virtualenv in `.venv` (Python 3.12). From the repo root:

```
uv venv --python 3.12                 (once; creates .venv)
uv pip install -r requirements.txt
```

Every command below runs from the repo root with the venv's interpreter,
`.venv\Scripts\python.exe` (or `pythonw.exe` for no console window). Nothing
needs Python on PATH.

The reader needs `piper.exe` and at least one voice in `reader\piper\` and
`reader\voices\`. They are gitignored and **not installed in this checkout yet**;
see [reader/README.md](reader/README.md#installing-piperexe-and-voices). Without
them the reader runs, but its status line says `piper.exe not found`.

---

## Running

Each app is standalone:

```
.venv\Scripts\python.exe reader\app.py
.venv\Scripts\python.exe transcriber\app.py
```

Or drive them with the launcher:

```
.venv\Scripts\python.exe winthings.py              start reader + transcriber if not running
.venv\Scripts\python.exe winthings.py reader       start just that one
.venv\Scripts\python.exe winthings.py clipboard    the retired clipboard app, only when named
.venv\Scripts\python.exe winthings.py --status     what is running
.venv\Scripts\python.exe winthings.py --stop       stop everything (clipboard included)
.venv\Scripts\python.exe winthings.py --console    start with consoles attached, to see tracebacks
```

Use `.venv\Scripts\pythonw.exe` instead to avoid a console window. Each app holds a
named mutex, so launching one twice is a no-op rather than a duplicate tray icon.
"Start with Windows" in any settings window registers that app under the per-user
`HKCU\...\CurrentVersion\Run` key, always with `.venv\Scripts\pythonw.exe`.

---

## Layout

```
common/       shared by all apps - treat as the suite's API
  config.py     JSON config: defaults, atomic writes, change subscriptions
  ui.py         SettingsWindow base + the row widgets + cross-thread pump()
  theme.py      one palette, fonts and metrics
  tray.py       pystray wrapper, icons drawn at runtime
  hotkey.py     the public hotkey surface: normalise, display, record
  winhotkey.py  RegisterHotKey implementation - no keyboard hook
  keysend.py    SendInput key synthesis + stuck-modifier release
  autostart.py  start-with-Windows via the Run key
  singleton.py  named-mutex single-instance guard
  paths.py      %APPDATA%\WinThings\<app>\
reader/       app.py speech.py engine.py selection.py overlay.py ipc.py settings.py
transcriber/  app.py engine.py output.py window.py settings.py
clipboard/    app.py store.py winclip.py popup.py settings.py   (retired)
winthings.py  launcher
```

Config and data live in `%APPDATA%\WinThings\<app>\` — `config.json`, plus
`history.db` for the clipboard. Deleting that folder resets an app to defaults.

Tk insists on owning the main thread, so each app keeps one long-lived root, runs
its tray icon and pollers on daemon threads, and marshals work back with
`common.ui.call_on_ui_thread()`. Anything with a long-lived non-settings root must
call `common.ui.pump(root)` once.

---

## Known limitations

These are real constraints of running unelevated Python on Windows, not
oversights — they are documented per-app in more detail.

**All apps.** Global hotkeys go through `RegisterHotKey`, so each combination has
exactly one owner system-wide. If something else already owns yours, the bind
fails and the settings window says so instead of the hotkey silently doing
nothing — measured on the original dev machine, `Win+Shift+A` was taken by another
application. A hotkey also will not fire while an elevated window has focus,
unless the app is elevated too.

No app in this suite installs a keyboard hook. That is deliberate: the earlier
implementation used the `keyboard` package's `suppress=True`, which hooks
`WH_KEYBOARD_LL` and — because it cannot know on the Ctrl key-down whether a
hotkey is coming — blocks the modifier prefix and re-injects it once the next key
rules the combination out. Pressing Tab or Caps Lock after a modifier hits that
replay path, and with two apps each running their own block-and-replay state
machine, a dropped key-up left Ctrl or Shift stuck down. `RegisterHotKey` has the
OS match and consume the combination, so there is no hook, no suppression state
and nothing to replay. Synthetic keystrokes go through `SendInput`
([common/keysend.py](common/keysend.py)), which always releases what it pressed
and force-releases any modifier left held.

**Clipboard.** The sensitive-app exclude list works two ways: it honours the
`ExcludeClipboardContentFromMonitorProcessing` and `CanIncludeInClipboardHistory`
clipboard formats that real password managers set — the correct mechanism — and
additionally skips a configurable list of process names. The name list races the
clipboard event and cannot see elevated processes, so treat it as convenience,
not a security boundary. Auto-paste cannot focus elevated windows. Search covers
the first 4096 characters of an entry.

**Reader.** The selection is read through UI Automation where the app supports it
(browsers, Notepad, VS Code, Windows Terminal — see
[reader/README.md](reader/README.md#how-the-selection-is-read)), with no clipboard
involved. Elsewhere it falls back to synthesising `Ctrl+C` and restoring the
previous clipboard afterwards — every plain-data format, images and file lists
included, marked so clipboard history skips the restore. OLE objects and virtual
files can't be restored, and the `Ctrl+C` itself still lands in clipboard history
(CopyQ, Win+V); details in
[reader/README.md](reader/README.md#known-windows-limitations). Reading mode has to
fully synthesise before it can start highlighting, because Piper emits no
word-level timing.
