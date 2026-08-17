"""Whisper transcription via faster-whisper, GPU when it's actually usable.

faster-whisper is multilingual and fully local: give transcribe() an mp3/mp4/
wav/flac/… path and it decodes (PyAV/ffmpeg is bundled, no separate install) and
returns timestamped segments plus a detected language.

GPU is preferred but guarded. ctranslate2 can *see* a CUDA device yet fail to run
if the CUDA runtime DLLs (cuBLAS, cuDNN) aren't loadable — those ship as the
nvidia-*-cu12 pip packages, whose bin folders we add to the DLL search path here.
If loading on CUDA still fails, we fall back to CPU and remember why, so the UI
can say so instead of just being slow or dead.
"""

import os
import threading

_dll_dirs_added = False


def _add_cuda_dll_dirs():
    """Put the nvidia pip packages' DLL folders where CUDA will find them (once).

    add_dll_directory isn't enough on its own: ctranslate2 loads cuBLAS lazily at
    inference time through a path that ignores it, so the model *loads* on the GPU
    and then encode() dies with 'cublas64_12.dll not found'. Prepending the bin
    folders to PATH as well covers that lazy load. Must run before the first
    CUDA model is constructed.
    """
    global _dll_dirs_added
    if _dll_dirs_added:
        return
    _dll_dirs_added = True
    try:
        import nvidia
    except Exception:
        return
    dirs = []
    for root in nvidia.__path__:
        if not os.path.isdir(root):
            continue
        for pkg in os.listdir(root):
            bindir = os.path.join(root, pkg, "bin")
            if os.path.isdir(bindir):
                dirs.append(bindir)
                try:
                    os.add_dll_directory(bindir)
                except (OSError, AttributeError):
                    pass
    if dirs:
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")


def cuda_present():
    """True if a CUDA device is visible at all (not that it's loadable)."""
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


MODELS = ["tiny", "base", "small", "medium", "large-v3"]
# Rough on-disk download sizes, for an honest "this will take a while" message.
MODEL_SIZES = {"tiny": "75 MB", "base": "145 MB", "small": "480 MB",
               "medium": "1.5 GB", "large-v3": "3 GB"}
LANGUAGES = {
    "": "Auto-detect", "en": "English", "ka": "Georgian", "ru": "Russian",
    "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
    "tr": "Turkish", "uk": "Ukrainian", "ar": "Arabic",
}


class Engine:
    """Loads a Whisper model lazily and transcribes files off the UI thread."""

    def __init__(self, config):
        self.config = config
        self._model = None
        self._model_key = None      # (name, device, compute) currently loaded
        self._device = None         # what we actually ended up on
        self._fallback_reason = None
        self._lock = threading.Lock()

    # -- device / model ---------------------------------------------------
    def _wanted_device(self):
        pref = self.config.get("device", "auto")
        if pref == "cpu":
            return "cpu"
        if pref == "cuda":
            return "cuda"
        return "cuda" if cuda_present() else "cpu"  # auto

    def _load(self, name, device):
        from faster_whisper import WhisperModel

        # int8_float16 on GPU, not plain float16: large-v3 in float16 needs ~3.7GB
        # and pins a 4GB laptop card at 91%, which is slow and starves everything
        # else. int8_float16 roughly halves the VRAM for near-identical accuracy.
        compute = self.config.get("compute_type", "") or (
            "int8_float16" if device == "cuda" else "int8")
        model = WhisperModel(name, device=device, compute_type=compute,
                             download_root=self._models_dir())
        return model, compute

    def _models_dir(self):
        from common.paths import app_dir

        return os.path.join(app_dir("transcriber"), "models")

    def _is_downloaded(self, name):
        """Best-effort: is this model already in the local cache? Controls whether
        we warn about a download. A snapshot folder with a model.bin is enough."""
        root = os.path.join(self._models_dir(),
                            f"models--Systran--faster-whisper-{name}", "snapshots")
        if not os.path.isdir(root):
            return False
        for snap in os.listdir(root):
            if os.path.exists(os.path.join(root, snap, "model.bin")):
                return True
        return False

    def ensure_model(self, on_status=None):
        """Load (downloading if needed) the configured model; fall back to CPU if
        CUDA won't run. Returns the loaded model."""
        name = self.config.get("model", "medium")
        device = self._wanted_device()
        with self._lock:
            if self._model is not None and self._model_key == (name, device):
                return self._model
            _add_cuda_dll_dirs()
            if on_status:
                if self._is_downloaded(name):
                    on_status(f"Loading {name} on {device.upper()}…")
                else:
                    on_status(f"Downloading {name} model (~{MODEL_SIZES.get(name, '?')}, "
                              f"one time) — this can take a few minutes…")
            try:
                model, _ = self._load(name, device)
                self._device, self._fallback_reason = device, None
            except Exception as exc:
                if device == "cuda":
                    self._fallback_reason = str(exc).split(".")[0][:120]
                    if on_status:
                        on_status("GPU unavailable, using CPU…")
                    model, _ = self._load(name, "cpu")
                    self._device = "cpu"
                else:
                    raise
            self._model = model
            self._model_key = (name, self._device)
            return model

    def active_device(self):
        return self._device or self._wanted_device()

    def fallback_reason(self):
        return self._fallback_reason

    # -- transcription ----------------------------------------------------
    def transcribe(self, path, on_status=None, on_progress=None, on_segment=None,
                   should_cancel=None):
        """Transcribe `path`. Streams segments to on_segment as they decode and
        reports 0..1 progress. Returns (segments, info) or (None, None) if
        cancelled. Raises on real errors (missing file, bad model)."""
        model = self.ensure_model(on_status)
        language = self.config.get("language", "") or None
        if on_status:
            on_status("Transcribing…")
        gen, info = model.transcribe(
            path,
            language=language,
            beam_size=self.config.integer("beam_size", 1, 10) or 5,
            vad_filter=bool(self.config.get("vad_filter", True)),
        )
        segments = []
        total = getattr(info, "duration", 0) or 0
        for seg in gen:
            if should_cancel and should_cancel():
                return None, None
            segments.append(seg)
            if on_segment:
                on_segment(seg)
            if on_progress and total:
                on_progress(min(1.0, seg.end / total))
        if on_progress:
            on_progress(1.0)
        return segments, info
