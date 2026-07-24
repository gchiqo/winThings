# Clipboard Manager

A tray-resident clipboard history for Windows 11 with **search** and
**persistence** — the two things Windows' own `Win+V` doesn't give you. History
lives in SQLite and survives reboots; every entry is searchable, not just pinned
ones.

## Running it

```
C:\Users\c\AppData\Local\Programs\Python\Python312\python.exe D:\code\winThings\clipboard\app.py
```

For a background launch with no console window, use `pythonw.exe` instead — that
is what the **Start with Windows** switch registers.

Flags:

| Flag | Purpose |
| --- | --- |
| `--selftest` | Open the popup and settings window briefly, then exit. Leaves nothing running. |
| `--db PATH` | Use an alternate history database instead of the real one. |

Only one instance runs at a time; a second launch exits immediately.

## Hotkeys

| Key | Action |
| --- | --- |
| `Ctrl+Shift+V` | Open the history popup (configurable). Press again to close. |
| *type anything* | Live search. The search box is focused the moment the popup opens. |
| `Up` / `Down` | Move through the list. `PgUp` / `PgDn` jump ten. |
| `Enter` | Copy the entry, restore focus to the window you came from, and paste. |
| `Ctrl+P` | Pin / unpin. Pinned entries sort to the top, are marked `*`, and are never pruned. |
| `Ctrl+Delete` | Delete the selected entry. |
| `Esc` | Close. |

Search matches whole words in any order: `api key` finds a line containing both.

## Tray menu

Open history · Settings… · Clear unpinned · Quit

## Config

`%APPDATA%\WinThings\clipboard\config.json`, editable from the settings window.

| Key | Default | Meaning |
| --- | --- | --- |
| `hotkey` | `ctrl+shift+v` | Combination that opens the popup. |
| `suppress_hotkey` | `true` | Swallow the combination so it doesn't also reach the app underneath. |
| `max_items` | `500` | Keep at most this many unpinned entries. `0` disables the cap. |
| `max_age_days` | `30` | Drop unpinned entries older than this. `0` keeps them forever. |
| `auto_paste` | `true` | After selecting, restore the previous window and send `Ctrl+V`. |
| `capture_images` | `true` | Store images as well as text. |
| `exclude_processes` | password managers | Comma-separated exe names to never capture from. |
| `start_with_windows` | `false` | Mirrors the `HKCU\...\Run` entry. |

Data files, all under `%APPDATA%\WinThings\clipboard\`:
`history.db` (SQLite), `config.json`, `clipboard.log`.

## How it works, and what it can't do

**Capture is event-driven, not polled.** A message-only window registered with
`AddClipboardFormatListener` receives `WM_CLIPBOARDUPDATE`. pywin32 doesn't wrap
that call, so it comes from `ctypes.windll.user32`; the window and message pump
are pywin32.

**The exclude list is a convenience, not a security boundary.** Two separate
mechanisms are implemented, and they are not equally good:

* *Reliable:* if the copying application marks its content with the
  `ExcludeClipboardContentFromMonitorProcessing` or `CanIncludeInClipboardHistory`
  clipboard formats, the entry is never read or stored. This is the mechanism
  Windows actually provides for opting out of clipboard managers, and real
  password managers use it. It is checked first, before anything is read.
* *Best-effort:* the `exclude_processes` list checks which process owns the
  foreground window when the clipboard changes. It **races** — the copy event
  and the focus check are not atomic, so a fast focus change can defeat it — and
  it **cannot see elevated processes**, because a normal-integrity process can't
  query them. Treat it as a convenience for well-behaved apps, not a guarantee.

**Other limits worth knowing:**

* Only `CF_UNICODETEXT` and `CF_DIB` are captured. Rich text, HTML and files are
  stored as their plain-text form if the source app also offers one, otherwise
  skipped. Pasting an image back writes `CF_DIB`, which has no alpha channel:
  transparency is flattened onto white.
* Full text is stored, but only the first 4096 characters are searchable —
  putting multi-megabyte pastes into the `LIKE` scan makes typing in the popup
  crawl.
* Auto-paste depends on `SetForegroundWindow`, which Windows restricts. It works
  when the popup is what you came from, and falls back to `AttachThreadInput`,
  but it cannot give focus to an elevated window from this non-elevated process.
  In that case the entry is still copied — press `Ctrl+V` yourself.
* The global hotkey uses a `WH_KEYBOARD_LL` hook, which does not see keys pressed
  while an elevated window has focus unless this process is also elevated.
