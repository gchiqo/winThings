"""Piper synthesis plus in-process audio playback.

The Linux version piped piper's raw PCM into `pw-play`/`aplay`/`ffplay`. None of
those exist on a stock Windows box (and ffmpeg is not installed here), so we
stream piper's stdout straight into a sounddevice output stream instead. That
removes the external audio dependency entirely and lets us apply the volume
setting ourselves, which the raw-PCM players could not all do.

Stopping needed rethinking too. On Linux a speak() ran in its own process group
and the toggle hotkey killed it with killpg. Windows has no equivalent, and here
the hotkey lives in the same long-running process anyway, so a job runs on a
worker thread guarded by a threading.Event: stop() sets the event, kills piper
and *aborts* (not drains) the audio stream, so the sound cuts immediately rather
than finishing the sentence already handed to the device.

Two engines feed that same player, picked by the `engine` config key: Piper
(piper.exe per line, the voice's own rate, usually 22.05 kHz) and Kokoro
(kokoro_tts.py, in-process, 24 kHz). Both get the same prepared lines from
speech.prepare_text and are stopped the same way. Kokoro has no Georgian voice,
so in Kokoro mode Georgian lines are still rendered by Piper and resampled into
the 24 kHz stream.
"""

import logging
import os
import subprocess
import threading
import wave

import kokoro_tts
import speech

log = logging.getLogger("reader.engine")

HERE = os.path.dirname(os.path.abspath(__file__))
PIPER_DIR = os.path.join(HERE, "piper")
PIPER_EXE = os.path.join(PIPER_DIR, "piper.exe")
ESPEAK_DATA = os.path.join(PIPER_DIR, "espeak-ng-data")

# Piper emits 16-bit mono; 4096 bytes is ~2048 frames, about 90 ms at 22 kHz.
CHUNK_BYTES = 4096
FRAME_BYTES = 2  # int16 mono

# Silence padding around every utterance, to hide Bluetooth's link latency.
#
# A Bluetooth headset (the common default output here) suspends its audio link
# when idle. Waking it takes a beat during which the first audio is corrupted —
# the garbled "bla bla / vsbvsbsb" at the start — and closing it drops whatever
# is still in the high-latency buffer, which eats the last word. A wired device
# just sees a short silence at each end.
#
# So the stream opens immediately on LEAD_IN_SECONDS of silence (which overlaps
# piper's synthesis, so it costs little wall time) and the link warms up on that
# silence instead of the first word; TAIL_OUT_SECONDS of trailing silence means
# the buffer Bluetooth discards on close is silence, not speech.
LEAD_IN_SECONDS = 0.8
TAIL_OUT_SECONDS = 0.6

# No console window when launched from pythonw, and no flash when launched
# from python.exe.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ENGINES = ("piper", "kokoro")


def piper_exe(override=None):
    """Path to piper.exe: explicit config, bundled copy, or one on PATH."""
    if override:
        return os.path.abspath(os.path.expandvars(os.path.expanduser(override)))
    if os.path.exists(PIPER_EXE):
        return PIPER_EXE
    import shutil

    return shutil.which("piper") or shutil.which("piper.exe") or PIPER_EXE


# Piper's own defaults (from most voice configs). noise_w especially drives the
# random "bubbling / mumbling" — Piper samples fresh noise every run, so the same
# text occasionally renders garbled. Lower values trade a little prosody variety
# for markedly more consistent, artifact-free speech.
DEFAULT_NOISE_SCALE = 0.5
DEFAULT_NOISE_W = 0.4


def piper_command(exe, info, ls, pause, out_file=None,
                  noise_scale=DEFAULT_NOISE_SCALE, noise_w=DEFAULT_NOISE_W):
    cmd = [exe, "-m", info["model"], "-c", info["config"]]
    # Older piper builds need espeak-ng-data pointed at explicitly; newer ones
    # find it next to the exe. Only pass the flag when the directory is really
    # there, otherwise piper refuses to start.
    if os.path.isdir(ESPEAK_DATA):
        cmd += ["--espeak_data", ESPEAK_DATA]
    cmd += ["-q", "--length_scale", f"{ls}", "--sentence_silence", f"{pause}"]
    if noise_scale is not None:
        cmd += ["--noise_scale", f"{noise_scale}"]
    if noise_w is not None:
        cmd += ["--noise_w", f"{noise_w}"]
    cmd += ["-f", out_file] if out_file else ["--output_raw"]
    return cmd


def scale_pcm(data, volume):
    """Apply the volume setting to signed-16-bit little-endian samples."""
    if volume is None or abs(float(volume) - 1.0) < 1e-3:
        return data
    import numpy as np

    samples = np.frombuffer(data, dtype="<i2").astype(np.float32) * float(volume)
    np.clip(samples, -32768, 32767, out=samples)
    return samples.astype("<i2").tobytes()


class Engine:
    """One speaking job at a time, startable from any thread, stoppable at once.

    on_state(state, detail) is called with "speaking", "idle" or "error" from
    the worker thread — callers with a GUI must marshal it onto the UI thread.
    """

    def __init__(self, config=None, on_state=None):
        self.config = config
        self.on_state = on_state
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None
        self._proc = None
        self._stream = None

    # -- setup checks -----------------------------------------------------
    def engine_name(self):
        """"piper" or "kokoro", from config; anything else means piper."""
        name = str(self._cfg("engine", "piper") or "piper").lower()
        return name if name in ENGINES else "piper"

    def voices(self):
        """Piper voices (id -> info). Kokoro's are kokoro_tts.voices()."""
        return speech.discover_voices(self._cfg("voices_dir", ""))

    def problem(self):
        """Human-readable reason we cannot speak right now, or None."""
        if self.engine_name() == "kokoro":
            return kokoro_tts.problem()
        if not os.path.exists(piper_exe(self._cfg("piper_exe", ""))):
            return "piper.exe not found — see README (reader\\piper\\)"
        if not self.voices():
            return "no voices found — see README (reader\\voices\\)"
        return None

    def _cfg(self, key, fallback):
        if self.config is None:
            return fallback
        value = self.config[key]
        return fallback if value is None else value

    def _cfg_num(self, key, fallback, lo=0.0, hi=5.0):
        """Numeric config value, clamped; the constant fallback when unset."""
        if self.config is None:
            return fallback
        try:
            return max(lo, min(hi, float(self.config.get(key, fallback))))
        except (TypeError, ValueError):
            return fallback

    def _noise(self):
        """(noise_scale, noise_w) for piper — lower than piper's own defaults to
        curb the random bubbling. Tunable via config for users who want more
        natural variation back."""
        return (self._cfg_num("noise_scale", DEFAULT_NOISE_SCALE, 0.0, 1.5),
                self._cfg_num("noise_w", DEFAULT_NOISE_W, 0.0, 1.5))

    def _georgian_voice_id(self, voices):
        """The configured Georgian voice, or the first ka_ voice available."""
        configured = self._cfg("voice_georgian", "")
        if configured and configured in voices:
            return configured
        return next((v for v in voices
                     if v.startswith("ka_") or v.startswith("ka-")), None)

    def _pick_voice_info(self, line, voices, default_info):
        """Per-line voice: Georgian text -> the Georgian voice, else the default.
        Decided per line so a mixed selection reads each line in the right voice."""
        if self.config is None or not self.config.get("auto_language", True):
            return default_info
        if speech.detect_script(line) != "ka":
            return default_info
        vid = self._georgian_voice_id(voices)
        return voices.get(vid, default_info) if vid else default_info

    def _settings(self, voice=None, speed=None, volume=None, pause=None):
        if self.config is not None:
            voice = voice or self.config["voice"]
            speed = speed if speed is not None else self.config.num("speed", 0.5, 5.0)
            volume = volume if volume is not None else self.config.num("volume", 0.0, 2.0)
            pause = pause if pause is not None else self.config.num(
                "sentence_silence", 0.0, 2.0)
        return (voice or None, float(speed or 1.0),
                1.0 if volume is None else float(volume),
                0.2 if pause is None else float(pause))

    def resolve(self, voice=None):
        """(voice_id, info) for the requested/preferred/first voice, or (None, None)."""
        voices = self.voices()
        vid = speech.pick_voice(voices, voice)
        return (vid, voices[vid]) if vid else (None, None)

    # -- state ------------------------------------------------------------
    def is_speaking(self):
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def stop(self):
        """Kill the current job. True if something was actually speaking."""
        with self._lock:
            thread = self._thread
            proc, stream = self._proc, self._stream
            if thread is None or not thread.is_alive():
                self._thread = None
                return False
            self._stop.set()
        if proc is not None:
            try:
                proc.kill()  # unblocks the worker's blocking read on stdout
            except OSError:
                pass
        if stream is not None:
            try:
                stream.abort()  # drop what the device already has, don't drain
            except Exception:
                pass
        thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
        return True

    # -- jobs -------------------------------------------------------------
    def speak(self, text, voice=None, speed=None, volume=None, pause=None,
              blocking=False):
        """Synthesise and play `text` (already prepared, one utterance per line)."""
        return self._start("stream", text, voice, speed, volume, pause,
                           blocking=blocking, extra=())

    def read_aloud(self, text, on_ready=None, on_done=None, voice=None, speed=None,
                   volume=None, pause=None, blocking=False):
        """Reading-mode job: synthesise the whole text to a temp wav first so the
        overlay can be told the total duration, then play it.

        Piper emits no word alignment, so the overlay estimates word timings from
        the total duration — which means we have to know it up front, i.e. no
        streaming for this one path. Same trade-off the GTK overlay made."""
        return self._start("render", text, voice, speed, volume, pause,
                           blocking=blocking, extra=(on_ready, on_done))

    def _start(self, kind, text, voice, speed, volume, pause, blocking, extra):
        """kind is "stream" (play as it synthesises) or "render" (synthesise
        everything, then play: reading mode)."""
        self.stop()
        text = (text or "").strip()
        if not text:
            return False, "nothing to read"

        if self.engine_name() == "kokoro":
            problem = kokoro_tts.problem()
            if problem:
                return False, problem
            _, speed, volume, pause = self._settings(None, speed, volume, pause)
            ids = kokoro_tts.voices()
            # An explicit voice (CLI --voice) only counts if it is a Kokoro one.
            vid = kokoro_tts.pick_voice(
                voice if voice in ids else self._cfg("kokoro_voice", ""), ids)
            if not vid:
                return False, "no voices in reader\\kokoro\\voices-v1.0.bin"
            target = self._stream_kokoro if kind == "stream" else self._kokoro_then_play
            args = (text, vid, speed, pause, volume) + extra
        else:
            exe = piper_exe(self._cfg("piper_exe", ""))
            if not os.path.exists(exe):
                return False, "piper.exe not found — see README"
            voice, speed, volume, pause = self._settings(voice, speed, volume, pause)
            vid, info = self.resolve(voice)
            if not info:
                return False, "no voices found — see README"
            target = self._stream_piper if kind == "stream" else self._synth_then_play
            args = (text, exe, info, speech.length_scale(speed), pause, volume) + extra

        self._stop.clear()
        thread = threading.Thread(target=self._guard, args=(target, args), daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()
        if blocking:
            thread.join()
        return True, vid

    def _guard(self, target, args):
        self._emit("speaking", None)
        try:
            target(*args)
        except Exception as exc:  # a worker crash must not take the tray down
            log.exception("synthesis/playback worker failed")
            self._emit("error", str(exc))
        finally:
            with self._lock:
                self._proc = self._stream = None
            self._emit("idle", None)

    def _emit(self, state, detail):
        if self.on_state:
            try:
                self.on_state(state, detail)
            except Exception:
                pass

    # -- workers ----------------------------------------------------------
    def _stream_piper(self, text, exe, info, ls, pause, volume):
        """One piper process PER LINE, streamed back-to-back into the player.

        Piper's neural state leaks across lines within a single process: feeding
        it several utterances at once destabilises later ones into garbled "bla
        bla" noise, while the same lines each rendered by a fresh process come
        out clean (verified by transcribing the audio). So each line gets its
        own invocation. Piper synthesises several times faster than real time, so
        the next line is ready well before the current one finishes playing and
        the seam is inaudible; the callback player zero-fills any gap as silence.
        """
        noise_scale, noise_w = self._noise()
        lines = [ln for ln in text.split("\n") if ln.strip()]
        voices = self.voices()  # snapshot once, not per line
        state = {"proc": None, "idx": 0}
        errors = []

        def read(n):
            while not self._stop.is_set():
                proc = state["proc"]
                if proc is None:
                    if state["idx"] >= len(lines):
                        return b""
                    line = lines[state["idx"]]
                    state["idx"] += 1
                    line_info = self._pick_voice_info(line, voices, info)
                    proc = subprocess.Popen(
                        piper_command(exe, line_info, ls, pause,
                                      noise_scale=noise_scale, noise_w=noise_w),
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, cwd=os.path.dirname(exe) or None,
                        creationflags=_NO_WINDOW,
                    )
                    state["proc"] = proc
                    with self._lock:
                        self._proc = proc  # so stop() kills the current line
                    threading.Thread(target=_drain, args=(proc.stderr, errors),
                                     daemon=True).start()
                    threading.Thread(target=_feed, args=(proc, line),
                                     daemon=True).start()
                data = proc.stdout.read(n)
                if data:
                    return data
                _reap(proc)
                state["proc"] = None
            return b""

        try:
            self._pump(read, info["rate"], volume)
        finally:
            if state["proc"] is not None:
                _reap(state["proc"])
        return None

    def _synth_then_play(self, text, exe, info, ls, pause, volume, on_ready, on_done):
        """Reading-mode: render the whole selection to PCM up front (the overlay
        needs the total duration), then play. Rendered one piper process per line
        for the same reason as _stream_piper — a single invocation over several
        lines garbles later ones."""
        noise_scale, noise_w = self._noise()
        rate = info["rate"]
        voices = self.voices()
        try:
            pcm = bytearray()
            errors = []
            for line in (ln for ln in text.split("\n") if ln.strip()):
                if self._stop.is_set():
                    return None
                line_info = self._pick_voice_info(line, voices, info)
                pcm += self._render_piper_line(exe, line_info, line, ls, pause,
                                               noise_scale, noise_w, errors)
            if self._stop.is_set():
                return None
            if not pcm:
                message = (b"".join(errors).decode("utf-8", "replace").strip()
                           or "synthesis failed")
                self._emit("error", message)
                return message

            duration = len(pcm) / 2 / float(rate or 1)
            reader = _bytes_reader(bytes(pcm))
            # Tell the overlay only once the device is actually running, so its
            # word clock isn't offset by the stream-open latency.
            self._pump(reader, rate, volume,
                       on_start=(lambda: on_ready(duration)) if on_ready else None)
        finally:
            if on_done:
                on_done()
        return None

    def _render_piper_line(self, exe, info, line, ls, pause, noise_scale, noise_w,
                           errors):
        """One line through its own piper process, to completion: PCM bytes at
        info["rate"]. stop() can kill it mid-way (it is published as _proc)."""
        proc = subprocess.Popen(
            piper_command(exe, info, ls, pause, noise_scale=noise_scale,
                          noise_w=noise_w),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=os.path.dirname(exe) or None,
            creationflags=_NO_WINDOW,
        )
        with self._lock:
            self._proc = proc
        threading.Thread(target=_drain, args=(proc.stderr, errors),
                         daemon=True).start()
        out, _ = proc.communicate((line + "\n").encode("utf-8"))
        return out

    # -- Kokoro -----------------------------------------------------------
    def _kokoro_lines(self, text, vid, speed, pause, errors):
        """PCM (int16, 24 kHz) per prepared line, synthesised one at a time so
        a caller streaming it plays line n while line n+1 is being made.

        Stops between lines when stop() is called. A Kokoro inference itself
        can't be interrupted, but each is one sentence-sized line (0.25-0.55 s
        on CPU, measured), and stop() has already silenced the audio by then.
        """
        silence = b"\x00\x00" * int(kokoro_tts.SAMPLE_RATE * pause)
        piper = self._piper_for_georgian()
        for line in (ln for ln in text.split("\n") if ln.strip()):
            if self._stop.is_set():
                return
            if piper and speech.detect_script(line) == "ka":
                exe, info, noise = piper
                pcm = self._render_piper_line(exe, info, line,
                                              speech.length_scale(speed), pause,
                                              noise[0], noise[1], errors)
                pcm = resample_pcm(pcm, info["rate"], kokoro_tts.SAMPLE_RATE)
            else:
                pcm = kokoro_tts.synth_pcm(line, vid, speed)
                if pcm:
                    pcm += silence  # Piper's --sentence_silence, by hand
            if pcm and not self._stop.is_set():
                yield pcm

    def _piper_for_georgian(self):
        """(exe, voice info, noise) for Georgian lines in Kokoro mode, or None
        when auto-detect is off or Piper / a Georgian voice isn't installed —
        Kokoro then reads those lines itself, as best it can."""
        if self.config is None or not self.config.get("auto_language", True):
            return None
        exe = piper_exe(self._cfg("piper_exe", ""))
        voices = self.voices()
        vid = self._georgian_voice_id(voices)
        if not vid or not os.path.exists(exe):
            return None
        return exe, voices[vid], self._noise()

    def _stream_kokoro(self, text, vid, speed, pause, volume):
        """Kokoro counterpart of _stream_piper: same player, 24 kHz."""
        errors = []
        lines = self._kokoro_lines(text, vid, speed, pause, errors)
        self._pump(_chunked_reader(lines), kokoro_tts.SAMPLE_RATE, volume)
        return None

    def _kokoro_then_play(self, text, vid, speed, pause, volume, on_ready, on_done):
        """Kokoro counterpart of _synth_then_play (reading mode): render every
        line, then play, so the overlay knows the total duration up front."""
        rate = kokoro_tts.SAMPLE_RATE
        try:
            errors = []
            pcm = bytearray()
            for chunk in self._kokoro_lines(text, vid, speed, pause, errors):
                pcm += chunk
            if self._stop.is_set():
                return None
            if not pcm:
                message = (b"".join(errors).decode("utf-8", "replace").strip()
                           or "synthesis failed")
                self._emit("error", message)
                return message
            duration = len(pcm) / 2 / float(rate)
            self._pump(_bytes_reader(bytes(pcm)), rate, volume,
                       on_start=(lambda: on_ready(duration)) if on_ready else None)
        finally:
            if on_done:
                on_done()
        return None

    def _pump(self, read, rate, volume, on_start=None):
        """Feed PCM from `read(nbytes)` into the output device until it dries up.

        A callback stream, not blocking writes: the device pulls from a shared
        buffer on its own thread and zero-fills when the buffer is momentarily
        empty. A slow producer therefore yields a brief silence rather than the
        garbled replay a starved blocking stream produced. The feeder here keeps
        that buffer topped up, then waits for it to drain so the final word is
        never cut off.
        """
        import sounddevice as sd

        buf = bytearray()
        buf_lock = threading.Lock()
        producing = {"on": True}
        drained = threading.Event()
        started = {"real": False}

        def callback(outdata, frames, _time, _status):
            need = frames * FRAME_BYTES
            with buf_lock:
                take = min(need, len(buf))
                outdata[:take] = buf[:take]
                del buf[:take]
                empty = not buf
            if take < need:
                outdata[take:] = b"\x00" * (need - take)  # silence, not garbage
                if not producing["on"] and empty:
                    drained.set()
                    raise sd.CallbackStop

        # Lead-in / tail-out are tunable: a slower-waking headset may need more.
        lead = self._cfg_num("lead_in_silence", LEAD_IN_SECONDS)
        tail = self._cfg_num("tail_out_silence", TAIL_OUT_SECONDS)

        # Seed the lead-in silence and open the device now, before any speech is
        # ready, so a Bluetooth link wakes on this silence while piper is still
        # synthesising. The callback zero-fills anyway, so if synthesis outruns
        # the lead-in the extra silence is seamless.
        with buf_lock:
            buf += b"\x00" * (int(rate * lead) * FRAME_BYTES)
        stream = None
        odd = b""  # a pipe read can split a sample across two chunks
        try:
            stream = self._open_stream(sd, rate, callback)
            while not self._stop.is_set():
                data = read(CHUNK_BYTES)
                if not data:
                    break
                data = odd + data
                odd, data = (data[-1:], data[:-1]) if len(data) % 2 else (b"", data)
                scaled = scale_pcm(data, volume)
                with buf_lock:
                    buf += scaled
                if not started["real"]:
                    started["real"] = True
                    if on_start:
                        on_start()  # real audio queued: start the overlay clock

            if not self._stop.is_set():
                # Trailing silence so a Bluetooth close drops silence, not speech.
                with buf_lock:
                    buf += b"\x00" * (int(rate * tail) * FRAME_BYTES)
                producing["on"] = False
                # Piper often synthesises far faster than real time, so the
                # buffer can still hold most of the clip here. Wait for the
                # callback to actually drain it (unbounded), but keep checking
                # stop() so an interrupt is still immediate.
                while not drained.is_set():
                    if self._stop.is_set():
                        break
                    drained.wait(timeout=0.1)
        except Exception:
            if not self._stop.is_set():
                raise  # a real device error; a stop() aborting us is expected
        finally:
            producing["on"] = False
            with self._lock:
                self._stream = None
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def _open_stream(self, sd, rate, callback):
        stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16",
                                    blocksize=0, callback=callback)
        stream.start()
        with self._lock:
            self._stream = stream
        return stream


def _feed(proc, text):
    try:
        proc.stdin.write((text + "\n").encode("utf-8"))
        proc.stdin.close()
    except (OSError, ValueError):
        pass  # piper was killed mid-write; the stop path already handled it


def _bytes_reader(data):
    """A read(n) over an in-memory PCM buffer, so _pump can play a pre-rendered
    clip the same way it plays a live stream."""
    pos = {"i": 0}

    def read(n):
        i = pos["i"]
        chunk = data[i:i + n]
        pos["i"] = i + n
        return chunk

    return read


def _chunked_reader(chunks):
    """A read(n) over an iterator of PCM byte strings (one per line), pulling
    the next one only when the current one is used up — so a lazily
    synthesising iterator renders line n+1 while _pump plays line n."""
    it = iter(chunks)
    state = {"buf": b""}

    def read(n):
        while not state["buf"]:
            nxt = next(it, None)
            if nxt is None:
                return b""
            state["buf"] = nxt
        data, state["buf"] = state["buf"][:n], state["buf"][n:]
        return data

    return read


def resample_pcm(data, src_rate, dst_rate):
    """int16 mono PCM from src_rate to dst_rate by linear interpolation.

    Only used for Piper's 22.05 kHz Georgian lines inside a 24 kHz Kokoro
    stream: a 9% upsample of speech, where linear interpolation is inaudible
    and pulls in no resampling library.
    """
    if not data or src_rate == dst_rate:
        return data
    import numpy as np

    src = np.frombuffer(data[:len(data) - len(data) % 2], dtype="<i2").astype(np.float32)
    n_out = int(round(len(src) * dst_rate / float(src_rate)))
    positions = np.linspace(0, len(src) - 1, n_out, dtype=np.float64)
    out = np.interp(positions, np.arange(len(src)), src)
    return np.clip(np.round(out), -32768, 32767).astype("<i2").tobytes()


def _drain(pipe, sink, cap=8192):
    try:
        for line in pipe:
            if sum(len(x) for x in sink) < cap:
                sink.append(line)
    except (OSError, ValueError):
        pass


def _reap(proc):
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
    for pipe in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if pipe is not None:
                pipe.close()
        except OSError:
            pass
