# Audio to Text

Local, free speech-to-text. Give it a recording (mp3, m4a, wav, mp4, flac, …) and
get the transcript — as plain text, timestamped text, or SRT/VTT subtitles.
Multilingual, so it handles **English and Georgian** (and many more).

Built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (OpenAI's
Whisper). Nothing is uploaded anywhere; it all runs on this machine.

## Running it

From the repo root (`C:\dev\scraps\winThings`), with the uv venv set up as in the
[top-level README](../README.md#setup) — `faster-whisper` is in requirements.txt:

```
.venv\Scripts\pythonw.exe transcriber\app.py
```

Or `.venv\Scripts\python.exe transcriber\app.py <audio-file>` to load a file
straight away (handy as an "Open with" target). A tray icon reopens the window and
quits.

## Using it

1. **Choose audio file…**
2. Pick the **Language** (Auto-detect works; set it explicitly if detection slips).
3. Tick which outputs you want to **Save**: Plain text, Timestamped text, SRT, VTT.
4. **Transcribe** — the text streams in as it decodes; the button becomes **Stop**.
5. **Copy**, or the selected files are saved next to the audio (e.g.
   `meeting.mp3` → `meeting.txt`, `meeting.srt`).

## Settings

`%APPDATA%\WinThings\transcriber\config.json`, or the Settings window.

| Key | Default | Meaning |
| --- | --- | --- |
| `model` | `medium` | `tiny`/`base`/`small`/`medium`/`large-v3`. Bigger = more accurate (especially Georgian), slower, more VRAM. |
| `device` | `auto` | `auto` uses the GPU when usable, else CPU. |
| `language` | `""` | Blank = auto-detect. |
| `vad_filter` | `true` | Skip silent stretches — faster and avoids invented text in silence. |
| `beam_size` | `5` | Higher can be more accurate but slower. |
| `output_dir` | `""` | Blank = save next to the audio file. |

Models download automatically on first use into
`%APPDATA%\WinThings\transcriber\models\` (medium ≈ 1.5 GB, large-v3 ≈ 3 GB).

## GPU

The app uses the NVIDIA GPU when the CUDA runtime libraries are present
(`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`; optional, commented in
requirements.txt — `uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`). They are
not installed in this venv yet, so the GPU path (this machine has an RTX 5070
Laptop GPU) isn't set up. On the original dev machine (RTX 3050) the GPU was
several times faster than CPU, which makes the
`medium`/`large-v3` models practical. If the GPU can't load, it falls back to CPU
automatically and the status line says so.

## Notes / limits

- **Georgian** transcription needs a larger model to be good — use `medium` or
  `large-v3`. `tiny`/`base` are English-oriented and weak on Georgian.
- First transcription after launch is slower — it loads (and maybe downloads)
  the model. Subsequent files reuse the loaded model.
- Very long recordings are processed start to finish; the progress bar tracks
  position within the file.
