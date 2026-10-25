"""Kokoro TTS, the second voice engine next to Piper.

Kokoro-82M through the kokoro-onnx package: ONNX Runtime in-process, no
PyTorch and no subprocess. Where Piper is a separate piper.exe per line,
Kokoro is one model loaded once into this process (~1-2 s, ~350 MB RAM) and
then called per line, so it is loaded lazily and shared.

Files live in reader\\kokoro\\ (gitignored, like reader\\piper\\):

    kokoro-v1.0.onnx    the model
    voices-v1.0.bin     every voice's style vectors, as a numpy .npz archive

Kokoro emits 24 kHz float32 audio. synth_pcm() converts each line to the
signed-16-bit mono PCM that engine._pump() already plays, so playback, volume,
stop and the reading-mode overlay are shared with Piper unchanged.
"""

import logging
import os
import threading
import time

log = logging.getLogger("reader.kokoro")
# phonemizer warns "words count mismatch" on many lines Kokoro reads (harmless:
# punctuation is kept, so its word count differs). A filter, not setLevel():
# phonemizer resets its logger's level and handlers on every call, but leaves
# filters alone. Keeps reader.log readable; real errors still get through.
logging.getLogger("phonemizer").addFilter(lambda rec: rec.levelno >= logging.ERROR)

HERE = os.path.dirname(os.path.abspath(__file__))
KOKORO_DIR = os.path.join(HERE, "kokoro")
MODEL_FILE = "kokoro-v1.0.onnx"
VOICES_FILE = "voices-v1.0.bin"
MODEL_PATH = os.path.join(KOKORO_DIR, MODEL_FILE)
VOICES_PATH = os.path.join(KOKORO_DIR, VOICES_FILE)
RELEASE_URL = ("https://github.com/thewh1teagle/kokoro-onnx/releases/tag/"
               "model-files-v1.0")

SAMPLE_RATE = 24000
DEFAULT_VOICE = "af_heart"
# kokoro-onnx rejects anything outside this range with a ValueError.
MIN_SPEED, MAX_SPEED = 0.5, 2.0

# A voice id's first letter is its language (af_heart = American English,
# female). The phonemizer needs to be told which.
LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi",
         "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}


def problem():
    """Why Kokoro can't be used right now, phrased as what to do, or None."""
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return "kokoro-onnx not installed — run: uv pip install -r requirements.txt"
    missing = [name for name, path in ((MODEL_FILE, MODEL_PATH),
                                       (VOICES_FILE, VOICES_PATH))
               if not os.path.exists(path)]
    if missing:
        return (f"Kokoro not installed — download {' and '.join(missing)} from "
                f"{RELEASE_URL} into reader\\kokoro\\")
    return None


_voices = {"stamp": None, "ids": []}


def voices():
    """Voice ids in voices-v1.0.bin, read without loading the model.

    Cached on the file's size and mtime, so the settings window can ask on
    every show and still pick up a replaced file without a restart.
    """
    try:
        st = os.stat(VOICES_PATH)
    except OSError:
        return []
    stamp = (st.st_size, st.st_mtime_ns)
    if _voices["stamp"] != stamp:
        try:
            import numpy as np

            with np.load(VOICES_PATH) as npz:
                ids = sorted(npz.files)
        except Exception:
            log.exception("cannot read %s", VOICES_PATH)
            ids = []
        _voices.update(stamp=stamp, ids=ids)
    return list(_voices["ids"])


def pick_voice(requested, available=None):
    ids = voices() if available is None else available
    if requested and requested in ids:
        return requested
    if DEFAULT_VOICE in ids:
        return DEFAULT_VOICE
    return ids[0] if ids else None


def lang_for(voice):
    return LANGS.get((voice or "a")[:1], "en-us")


def clamp_speed(speed):
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        speed = 1.0
    return max(MIN_SPEED, min(MAX_SPEED, speed))


_model = None
_model_lock = threading.Lock()


def load():
    """The shared Kokoro instance, loading it on first use."""
    global _model
    with _model_lock:
        if _model is None:
            from kokoro_onnx import Kokoro

            t0 = time.perf_counter()
            _model = Kokoro(MODEL_PATH, VOICES_PATH)
            log.info("kokoro loaded in %.1fs, providers %s",
                     time.perf_counter() - t0, _model.sess.get_providers())
        return _model


def loaded():
    return _model is not None


def warm_up():
    """Load the model on a background thread, if Kokoro is usable at all, so
    the first read doesn't wait for it."""
    if _model is not None or problem() is not None:
        return

    def run():
        try:
            load()
        except Exception:
            log.exception("kokoro warm-up failed")

    threading.Thread(target=run, name="kokoro-load", daemon=True).start()


def unload():
    """Drop the model (frees its memory) when the engine is switched away."""
    global _model
    with _model_lock:
        _model = None


def synth_pcm(text, voice, speed):
    """One line of prepared text -> signed-16-bit mono PCM at SAMPLE_RATE.

    Returns b"" for a line that has nothing pronounceable in it (kokoro-onnx
    raises for those rather than returning silence).
    """
    import numpy as np

    model = load()
    try:
        audio, rate = model.create(text, voice=voice, speed=clamp_speed(speed),
                                   lang=lang_for(voice))
    except ValueError as exc:
        log.info("kokoro skipped a line: %s", exc)
        return b""
    if rate != SAMPLE_RATE:  # never, for v1.0; guards a future model swap
        raise RuntimeError(f"unexpected Kokoro sample rate {rate}")
    pcm = np.clip(np.asarray(audio, dtype=np.float32) * 32767.0, -32768, 32767)
    return pcm.astype("<i2").tobytes()
