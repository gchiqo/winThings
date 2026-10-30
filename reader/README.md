# Read Selected — Windows

Select text anywhere in Windows, press `Ctrl+Shift+A`, and it is read aloud with a
neural voice — [Piper](#installing-piperexe-and-voices) or
[Kokoro](#3-kokoro-optional-second-engine--readerkokoro), picked under Settings →
Engine. Press the hotkey again to stop.

This is a port of a working Linux/GNOME tool (`read-aloud.py`, not in this repo).
The text-cleaning and sentence-chunking pipeline is carried over verbatim — it was
tuned against real Piper output — and everything OS-facing was rewritten for
Windows.

---

## Running it

From the repo root (`C:\dev\scraps\winThings`), with the uv venv set up as in the
[top-level README](../README.md#setup):

```
.venv\Scripts\python.exe reader\app.py
```

It runs as a tray app: the icon sits in the notification area, right-click for
Read selection / Stop / Settings / Quit.

For no console window, use `.venv\Scripts\pythonw.exe` instead (that is what the
"Start with Windows" switch registers).

The app starts fine with no Piper installed — the settings window just shows
`piper.exe not found` in the status line instead of reading anything.

Dependencies are in the repo's [requirements.txt](../requirements.txt)
(`uv pip install -r requirements.txt`).

---

## Installing piper.exe and voices

Nothing is bundled; both go in this folder.

### 1. piper.exe → `reader\piper\`

Download `piper_windows_amd64.zip` from the Piper releases page:

  https://github.com/rhasspy/piper/releases

Unzip it so the layout is:

```
reader\piper\piper.exe
reader\piper\onnxruntime.dll        (and the other DLLs from the zip)
reader\piper\espeak-ng-data\        (folder from the zip)
```

This app drives the **classic `piper.exe` CLI** — the one with `--output_raw`,
`--length_scale`, `--sentence_silence` and `--espeak_data`. The newer `piper1-tts`
Python package uses different flag spellings and will not work as a drop-in; if you
want to use it, point the `piper_exe` config key at a wrapper script that translates
the arguments.

`--espeak_data` is only passed when `reader\piper\espeak-ng-data\` exists, so builds
that find their own phoneme data still work.

### 2. Voices → `reader\voices\`

Each voice is a **pair** of files that must sit next to each other:

```
reader\voices\en_US-ryan-high.onnx
reader\voices\en_US-ryan-high.onnx.json
```

Download from https://huggingface.co/rhasspy/piper-voices (browse by language, then
quality: `low` / `medium` / `high`). Audition them first at
https://rhasspy.github.io/piper-samples/ .

The default voice is `en_US-ryan-high` when present; otherwise the first voice found,
alphabetically. The sample rate comes from `audio.sample_rate` in the `.onnx.json`.

Voice files are large. If you keep them elsewhere, set the `voices_dir` config key to
that folder instead of copying them in.

### 3. Kokoro (optional second engine) → `reader\kokoro\`

[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is a second engine, chosen
under Settings → **Engine**. Its English voices sound noticeably more natural than
Piper's; it has **no Georgian voice**, so Georgian lines keep going to the Piper
Georgian voice (see below). It runs inside the reader process through the
`kokoro-onnx` package — ONNX Runtime, no PyTorch, already in requirements.txt — and
needs two files from the kokoro-onnx
[`model-files-v1.0` release](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0):

```
reader\kokoro\kokoro-v1.0.onnx     325 MB, the model
reader\kokoro\voices-v1.0.bin       28 MB, all 54 voices
```

From the repo root (curl ships with Windows):

```
curl -L --create-dirs -o reader\kokoro\kokoro-v1.0.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -L -o reader\kokoro\voices-v1.0.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Until both files are there, the Engine menu lists **Kokoro (not installed)** with
these instructions under it and won't switch to it; nothing else changes. Once they
are, reopen Settings — no restart needed.

Voice ids encode language and gender: `af_heart` is **a**merican English,
**f**emale; `bm_george` is **b**ritish English, **m**ale. Other first letters are
Spanish, French, Hindi, Italian, Japanese, Portuguese and Mandarin. The default is
`af_heart`.

---

## Hotkeys

| Key | Action |
| --- | --- |
| `Ctrl+Shift+A` (configurable) | Read the current selection; press again to stop |
| `Esc` / `Q` / right-click / the ✕ | Close the reading-mode overlay |
| Click the overlay text | Toggle fullscreen ↔ small bottom-right panel |

Set the hotkey in Settings → Shortcut: click the field and press the combination.

The hotkey is registered **suppressed**, meaning the focused app never sees it. That
is deliberate: some editors bind `Ctrl+Shift+A` themselves, and if that command
changed the selection first, we would read the wrong thing.

---

## How the selection is read

Two routes, tried in order (`selection.py`):

1. **UI Automation.** The focused element's `TextPattern` is asked for its selection
   ranges. Nothing touches the clipboard, no keys are sent, and clipboard-history
   tools (CopyQ, Win+V) see nothing. Warm reads take 2–15 ms.
2. **Synthetic `Ctrl+C`**, with the clipboard snapshotted first and put back
   afterwards — used only when UIA returns nothing (element has no TextPattern,
   empty selection, timeout, error). Typically 100–150 ms. Every format that is
   plain data is restored byte-for-byte (text, HTML, RTF, images, Explorer file
   lists, EMF drawings, app-specific formats), an empty clipboard is restored as
   empty, and the restore is marked so clipboard history skips it — see the
   limitations below for what can't come back.

UIA runs on its own thread with UIA's connection/transaction timeouts at 150 ms and
a 250 ms hard deadline, so a hung app costs a fallback, never a frozen hotkey.

Chromium (Chrome, Edge, VS Code and other Electron apps) and Firefox build their
accessibility tree lazily, on the first UIA request. So the first read from a window
gets a short retry budget (150 ms) for those; later reads from the same window
don't, so "nothing selected" stays cheap.

Every read logs which route answered, to `%APPDATA%\WinThings\reader\reader.log`
(the text itself is never logged):

```
selection via uia (ok) app=chrome.exe chars=166 3ms
selection via uia (ok after 1 retry) app=msedge.exe chars=166 40ms
selection via clipboard (uia: no-textpattern) app=someapp.exe chars=42 120ms
```

`ok+1up` means the TextPattern was found on an ancestor of the focused element (the
Edge PDF viewer does this). Set the environment variable `WINTHINGS_NO_UIA=1` to
force the `Ctrl+C` route, e.g. to compare behaviour in an app.

Measured on this machine (Windows 11, October 2026), selecting text and reading it:

| App | Route | Notes |
| --- | --- | --- |
| Notepad | UIA | 3–15 ms |
| Chrome | UIA | First read in a fresh window needs one retry (~40 ms) |
| Edge | UIA | Same as Chrome |
| Firefox | UIA | One run returned an empty selection on a new page's first read, fine on retry |
| Edge PDF viewer | UIA | TextPattern sits one level up (`ok+1up`); tree takes ~130 ms to appear on first read. `Ctrl+A` selects nothing in the viewer — drag to select |
| VS Code (editor) | UIA | Its first read after VS Code started fell back to `Ctrl+C` once, before the retry existed; UIA every time since |
| Windows Terminal | UIA | Select-all includes the buffer's trailing padding; the speech cleanup collapses it |
| Word | — | Not installed here, untested. Word implements TextPattern, so UIA is expected |

---

## Configuration

`%APPDATA%\WinThings\reader\config.json`, written by the settings window. Edit it by
hand if you prefer; unknown keys are preserved and out-of-range values are clamped.

| Key | Default | Meaning |
| --- | --- | --- |
| `engine` | `"piper"` | `piper` or `kokoro` |
| `voice` | `""` | Piper voice id (filename without `.onnx`). Blank = `en_US-ryan-high`, else first found |
| `kokoro_voice` | `""` | Kokoro voice id. Blank = `af_heart`, else first in `voices-v1.0.bin` |
| `voice_georgian` | `""` | Piper voice for Georgian lines, in both engines. Blank = first `ka_` voice |
| `auto_language` | `true` | Detect Georgian per line and switch to `voice_georgian` |
| `speed` | `1.0` | Speed multiplier, 0.5–5.0. Piper's `--length_scale` is its inverse; Kokoro caps it at 2.0 |
| `volume` | `1.0` | 0.0–1.5, applied to the PCM samples before playback |
| `sentence_silence` | `0.2` | Seconds of silence between sentences (Piper's `--sentence_silence`; added by hand for Kokoro) |
| `reading_mode` | `false` | Fullscreen overlay that highlights each word as it is read |
| `hotkey` | `"ctrl+shift+a"` | Global shortcut |
| `autostart` | `false` | Register under `HKCU\...\CurrentVersion\Run` |
| `voices_dir` | `""` | Blank = `reader\voices` |
| `piper_exe` | `""` | Blank = `reader\piper\piper.exe`, then whatever is on PATH |

---

## Command line

The CLI from the Linux version is kept, for scripting. If the tray app is running the
command is handed to it over a loopback socket; if it is not, `--text` synthesizes in
place and waits.

```
app.py                      run the tray app
app.py --settings           run it with the settings window open
app.py --stop               stop any current playback
app.py --read               read the current selection
app.py --text "..."         read the given text
app.py --voice ID --speed 1.2 --text "..."
app.py --list-voices        every Piper and Kokoro voice, prefixed with its engine
```

`--text` / `--read` use the engine set in the config; `--voice` only applies if it
names a voice of that engine.

A second bare `app.py` while one is already running opens the first one's settings
window instead of starting a duplicate tray icon and hotkey.

---

## What was ported, and what changed

**Carried over verbatim** (`speech.py`) — at port time a regression test diffed these
against the original Linux `read-aloud.py` (not in this repo) on ~3000 fuzz inputs
plus hand-picked nasty cases, and every result was identical:

- `clean_for_speech`, `clean_token` and the regex constants `ABBREV`, `CTRL_RE`,
  `URL_RE`, `WWW_RE`, `EMAIL_RE`, `RULE_RE`, `DASHES_RE`, `TOKEN_RE`, `CODEY_RE`,
  `KEEP_PUNCT`, `_has_letter_or_digit`
- `split_sentences`, `break_long`, `prepare_text`, `MAX_CHUNK`
- `discover_voices` / `pick_voice`

**Replaced for Windows:**

| Linux | Windows |
| --- | --- |
| X11 PRIMARY selection | UI Automation `TextPattern`, else synthetic `Ctrl+C` + clipboard restore (`selection.py`) |
| `gsettings` custom keybinding | `RegisterHotKey` via `common.winhotkey` (no keyboard hook) |
| `pw-play` / `aplay` / `ffplay` pipe | `sounddevice` streaming, in-process (`engine.py`) |
| process group + `killpg` + PID file | worker thread + `threading.Event` (`engine.py`) |
| PID file for `--stop` from a second process | loopback control socket (`ipc.py`) |
| GTK settings window | customtkinter via `common.ui` (`settings.py`) |
| GTK fullscreen overlay | Tk `Toplevel` overlay (`overlay.py`) |

**Shipped, not deferred:** the reading-mode overlay. Tk needs none of the Wayland /
Mutter workarounds the GTK version was full of, so it came out smaller than the
original. Turn it on with the Reading mode switch.

---

## Known Windows limitations

These are real limits, not oversights:

- **The `Ctrl+C` fallback can't restore everything.** When UIA can't read the
  selection, capturing it means pressing `Ctrl+C` in the focused app, which replaces
  the clipboard. It is snapshotted first and put back, but some formats are not data
  and can't be:
  - GDI-handle formats (`CF_BITMAP`, `CF_PALETTE`, `CF_METAFILEPICT`). Windows
    re-synthesises them from the DIB / EMF that *is* restored, so nothing visible
    is lost.
  - OLE object formats (`Ole Private Data`, `Embed Source`, `Object Descriptor`,
    `Link Source`, …). They refer to the source app's live object, so after a
    fallback Office's *Paste Special → linked/embedded object* for the earlier copy
    is gone; ordinary paste still works.
  - Virtual files (`FileContents` / `FileGroupDescriptor`): e.g. an Outlook
    attachment or a file inside a zip copied in Explorer. Those are lost.
  - Any single format over 64 MB.

  Snapshotting asks apps that render lazily (Excel) to produce every format they
  offered, which takes a moment for large copies, and Excel's "marching ants" copy
  ends. The UIA route never touches the clipboard.
- **The fallback's `Ctrl+C` still shows up in clipboard history.** The *restore* is
  marked with `ExcludeClipboardContentFromMonitorProcessing`,
  `CanIncludeInClipboardHistory = 0` and `CanUploadToCloudClipboard = 0`. CopyQ
  17.0 honours them (measured); Win+V history is documented to. The captured selection itself is the
  target app's own clipboard write and can't be marked; CopyQ records it even when
  it is on the clipboard for only 30 ms. `copyq disable` / `copyq enable` around the
  capture would stop that (measured ~35 ms each), but isn't done here.
- **Nothing gets read from an elevated window.** A hotkey registered by a
  normal-integrity process does not fire while an admin window has focus, and the
  synthetic `Ctrl+C` would not reach it either. Run the app elevated too if you
  need that (and accept the UAC prompt at every start).
- **The hotkey has one owner system-wide.** `RegisterHotKey` refuses a
  combination another application already holds, and the status line reports it
  rather than binding silently and never firing. `Win+Shift+A` was taken on the
  original dev machine.
- **Reading mode has to synthesize the whole selection first.** Piper emits no word
  alignment, so word timings are estimated from the total audio duration — which
  means the overlay waits for synthesis to finish before it starts speaking. Normal
  (non-overlay) reading streams and starts as soon as the first audio arrives.
  Same for Kokoro: the overlay gets the total duration once every line is rendered.
- **Word highlighting is an estimate**, weighted by word length and trailing
  punctuation. Usually within ~150 ms; it drifts on text with unusual pronunciation.
  (kokoro-onnx can report per-phoneme timings, `create_timed()`, which could make
  Kokoro's highlighting exact; not used yet.)
- **Kokoro lives inside the reader process.** The model loads in ~1 s (in the
  background, at start and when you switch to it) and holds ~400–470 MB of RAM while
  Kokoro is the selected engine; switching back to Piper frees it. On this machine's
  CPU a sentence takes 0.25–0.55 s to synthesise, about 5× faster than real time, so
  streaming never waits after the first line.
- **Kokoro's speed range is 0.5–2.0×.** The Speed slider goes to 5× for Piper;
  Kokoro is capped at 2×.
- **Stopping Kokoro mid-sentence** silences the audio at once, like Piper. The line
  being synthesised at that moment can't be interrupted (it is a single ONNX run),
  so the worker finishes it in the background — `stop()` measured ~100 ms.
- **Kokoro has no Georgian.** In Kokoro mode, Georgian lines (with Auto-detect
  Georgian on) are read by the Piper Georgian voice and resampled from 22.05 to
  24 kHz into the same stream; without Piper installed, Kokoro attempts them itself,
  badly.
- **Bluetooth audio adds latency.** Opening the output stream on a BT headset
  measured ~0.8 s on the original dev machine, so the first word lags. Wired/USB devices open in
  a few tens of milliseconds.
- **"Read selection" from the tray or settings** has to restore focus to the window
  you were last using before it can copy. That is polled (`foreground.py`) and is
  best-effort; the hotkey is always the reliable path because focus is already right.
- **A hotkey can fail to register** if another app holds the same combination. The
  status line says so explicitly rather than silently doing nothing.
