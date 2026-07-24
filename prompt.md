# Build a Windows 11 utility suite: custom taskbar dock, clipboard manager, text-to-speech reader

I'm on Windows 11 (coming from a customized Debian/GNOME setup) and want three Python-based
background utilities. Build them as separate apps that can each run independently from the
system tray, but share a common look/feel and, where it makes sense, a common settings pattern.

Target stack: Python 3.11+, Windows-only (pywin32, `keyboard` or `pynput` for hotkeys/hooks,
`psutil` for system stats, Tkinter or `customtkinter` for UI — pick whichever renders cleanest
for a small always-on-top panel). Each app should run as a background process with a tray icon
(use `pystray`) for quick access to settings/quit, and should be able to start with Windows.

Do NOT attempt to patch or replace Windows Explorer, the Start menu, or the native taskbar
itself — that requires shell-level hacking (like ExplorerPatcher) that's out of scope. Instead,
build a custom floating panel that sits alongside the real taskbar (which I'll auto-hide via
Windows settings myself).

---

## 1. Taskbar dock (custom panel)

A slim, always-on-top bar (dock) that replaces the *visual/functional* role of the taskbar for me,
docked to one edge of the screen (default bottom, but make the edge configurable).

Requirements:
- **Pinned + running app icons**, similar to a normal taskbar. Enumerate open top-level windows
  (via `pywin32`'s `win32gui.EnumWindows` / `win32process`) and show their icons.
- **Adjustable icon size** — a setting (slider or config value) that scales all icons uniformly,
  live-updating without restart.
- **Expanded running-app view**: when an app has multiple open windows, show them "expanded"
  next to that app's icon (e.g. small window-title chips fanning out beside the icon) instead of
  Windows' default grouped-flyout-on-hover behavior — I want them visible/expanded by default,
  not hidden behind a hover popup.
- Clicking an icon/chip focuses that window (`SetForegroundWindow` + restore if minimized).
  Clicking the icon of a currently-focused app minimizes it (standard taskbar toggle behavior).
- **System monitors built into the dock**, updating every 1–2 seconds:
  - CPU usage % (`psutil.cpu_percent`)
  - RAM usage % (`psutil.virtual_memory`)
  - GPU usage % (try `pynvml`/`nvidia-ml-py` for NVIDIA GPUs first; fall back gracefully — e.g.
    hide the GPU metric — if no supported GPU/driver is found; note this limitation clearly)
  - Network throughput, upload and download separately, in a readable unit that auto-scales
    (KB/s vs MB/s) (`psutil.net_io_counters`, delta over the poll interval)
  - Each metric should be a small compact readout (icon/label + number), not a full graph —
    think GNOME's Vitals extension tray compactness, not a dashboard.
- Config file (JSON) for: icon size, dock edge/position, which metrics are enabled, poll interval.
- A lightweight settings window (opened from the tray icon) to edit the above without hand-editing JSON.

Known hard limits — mention these back to me rather than silently working around them: getting
icons for every window reliably can be flaky for some apps (UWP apps, elevated processes you
don't have access to); handle failures by falling back to a generic icon rather than crashing.

---

## 2. Clipboard manager

Background utility that maintains a searchable clipboard history, since Windows' native Win+V
has no search and gets wiped on reboot (only pinned items persist).

Requirements:
- Poll or hook clipboard changes (`win32clipboard` polling loop is fine; note any better event-
  driven approach if one exists on Windows) and store each new text/image entry.
- Persist history to a local SQLite DB (survives reboot — this is the main thing missing from
  the native tool), storing at minimum: content, type (text/image), timestamp.
- Global hotkey (configurable, default something that doesn't collide with Win+V, e.g.
  `Ctrl+Shift+V`) opens a small popup list of history, most recent first.
- **Live search-as-you-type** filtering by content — this is the core feature I actually need.
- **Pin** items so they don't get pruned/cleared.
- Selecting an item from the popup copies it to clipboard and (optionally, if a text field is
  focused) auto-pastes it via simulated `Ctrl+V`.
- Cap history size (configurable, default something reasonable like 500 items or 30 days) and
  prune oldest unpinned entries past that cap.
- Basic settings window: hotkey, history cap, clear-all button, exclude-list for sensitive apps
  (e.g. password managers) if feasible — flag if this is impractical on Windows rather than
  half-implementing it.

---

## 3. Text-to-speech reader ("select text anywhere, read it aloud")

I already have a working Linux/GNOME version of this (files attached/described below) using
Piper neural TTS. Port the *logic* to Windows; only the OS-integration layer needs replacing.

**Reuse as-is** (already pure Python, no OS dependency): the text-cleaning pipeline
(`clean_for_speech`, `clean_token` — strips URLs/emails/code-like tokens, expands camelCase,
etc.) and the sentence-chunking logic (`split_sentences`, `break_long`, `MAX_CHUNK`) from my
existing `read-aloud.py`. Carry these over largely unchanged.

**Needs Windows-native replacement:**
- **Selection capture**: Windows has no PRIMARY-selection equivalent. Simulate `Ctrl+C` via
  `keyboard`/`pynput`'s `send`, read the clipboard afterward, then restore whatever was
  previously on the clipboard so I don't lose my last real copy. Handle the case where nothing
  was selected (clipboard doesn't change) without erroring.
- **Global hotkey**: register via `keyboard` or `pynput` instead of GNOME's `gsettings`
  mechanism. Default `Ctrl+Shift+A` (or similar), configurable. Same toggle behavior as before:
  press to read selection, press again while speaking to stop.
- **Audio playback**: Piper has official Windows builds — use the Windows `piper.exe` +
  its voice `.onnx`/`.onnx.json` files (same voice-discovery logic as before, scanning a
  `voices/` folder). Replace `pw-play`/`aplay`/`ffplay` piping with something Windows-friendly —
  either shell out to `ffplay` (if I have ffmpeg installed) or use `sounddevice`/`simpleaudio`
  to play the raw PCM directly in-process; prefer the in-process option if reasonably simple,
  since it avoids an external dependency.
- **Settings GUI**: rebuild the GTK settings window (voice picker, speed/volume/pause sliders,
  hotkey field, Test/Read/Stop buttons, live "Ready / Reading… / Hotkey not bound" indicator)
  in Tkinter or `customtkinter` instead of GTK — same feature set, doesn't need to look
  identical.
- **"Reading mode" overlay** (fullscreen overlay highlighting words as read) — port this too if
  reasonably scoped, otherwise flag it as a stretch goal I can defer.

Ask me for the actual script files (`read-aloud.py`, `reader_overlay.py`, `piper-read-gui.py`,
`setup-hotkey.py`) if you need to see the exact current implementation before porting it.

---

## Suggested build order

1. Clipboard manager (self-contained, lowest risk, most immediately useful)
2. Text reader (mostly a port, logic already proven)
3. Taskbar dock (most novel, most UI work, most likely to need iteration)

For each, start with a minimal working version (core hotkey + core function, no settings UI),
confirm it works, then layer in the settings window and polish. Ask me clarifying questions
about any Windows API limitation before working around it silently.