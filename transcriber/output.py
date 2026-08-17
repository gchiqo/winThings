"""Turn Whisper segments into the output formats the user asked for.

A segment is anything with .start, .end (seconds) and .text; faster-whisper's
Segment fits, and so does a plain namedtuple in tests. Every formatter takes the
same list, so the app can emit any combination the checkboxes select.
"""

import os


def _ts(seconds, sep=","):
    """Seconds -> HH:MM:SS,mmm (SRT) or HH:MM:SS.mmm (VTT)."""
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _clock(seconds):
    """Seconds -> [mm:ss] or [h:mm:ss] for readable timestamped text."""
    seconds = int(max(0, seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def plain_text(segments):
    """Just the words. Segments are joined with spaces and squeezed, so the
    result reads as continuous prose rather than one fragment per line."""
    parts = [s.text.strip() for s in segments if s.text.strip()]
    return " ".join(parts).strip()


def timestamped_text(segments):
    lines = []
    for s in segments:
        text = s.text.strip()
        if text:
            lines.append(f"[{_clock(s.start)}] {text}")
    return "\n".join(lines)


def srt(segments):
    blocks = []
    n = 0
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        n += 1
        blocks.append(f"{n}\n{_ts(s.start)} --> {_ts(s.end)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def vtt(segments):
    cues = []
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        cues.append(f"{_ts(s.start, '.')} --> {_ts(s.end, '.')}\n{text}")
    body = "\n\n".join(cues)
    return "WEBVTT\n\n" + body + ("\n" if body else "")


# format key -> (file extension, formatter)
FORMATS = {
    "text": (".txt", plain_text),
    "timestamped": (".timestamped.txt", timestamped_text),
    "srt": (".srt", srt),
    "vtt": (".vtt", vtt),
}


def outputs_for(audio_path, segments, which, out_dir=""):
    """Return {path: content} for each selected format `which` (a set of keys).

    Files land next to the audio unless out_dir is given. The base name is the
    audio's, so recording.mp3 -> recording.txt / recording.srt.
    """
    base = os.path.splitext(os.path.basename(audio_path))[0]
    folder = out_dir or os.path.dirname(os.path.abspath(audio_path))
    result = {}
    for key in which:
        if key not in FORMATS:
            continue
        ext, fmt = FORMATS[key]
        result[os.path.join(folder, base + ext)] = fmt(segments)
    return result
