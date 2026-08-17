# Audio to Text

Local, free speech-to-text. Give it a recording (mp3, m4a, wav, mp4, flac, …) and
get the transcript — as plain text, timestamped text, or SRT/VTT subtitles.
Multilingual, so it handles **English and Georgian** (and many more).

Built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (OpenAI's
Whisper). Nothing is uploaded anywhere; it all runs on this machine.

## Running it

```
C:\Users\c\AppData\Local\Programs\Python\Python312\pythonw.exe D:\code\winThings\transcriber\app.py
```

Or `python.exe transcriber\app.py <audio-file>` to load a file straight away
(handy as an "Open with" target). A tray icon reopens the window and quits.

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
(`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`, installed via pip). On this machine
they are installed, so `device: auto` runs on the RTX 3050 — several times faster
than CPU, which makes the `medium`/`large-v3` models practical. If the GPU can't
load, it falls back to CPU automatically and the status line says so.

## Notes / limits

- **Georgian** transcription needs a larger model to be good — use `medium` or
  `large-v3`. `tiny`/`base` are English-oriented and weak on Georgian.
- First transcription after launch is slower — it loads (and maybe downloads)
  the model. Subsequent files reuse the loaded model.
- Very long recordings are processed start to finish; the progress bar tracks
  position within the file.
