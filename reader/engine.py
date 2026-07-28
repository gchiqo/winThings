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
"""

import logging
import os
import subprocess
import threading
import wave

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
    def voices(self):
        return speech.discover_voices(self._cfg("voices_dir", ""))

    def problem(self):
        """Human-readable reason we cannot speak right now, or None."""
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
        return self._start(self._stream_piper, text, voice, speed, volume, pause,
                           blocking=blocking, extra=())

    def read_aloud(self, text, on_ready=None, on_done=None, voice=None, speed=None,
                   volume=None, pause=None, blocking=False):
        """Reading-mode job: synthesise the whole text to a temp wav first so the
        overlay can be told the total duration, then play it.

        Piper emits no word alignment, so the overlay estimates word timings from
        the total duration — which means we have to know it up front, i.e. no
        streaming for this one path. Same trade-off the GTK overlay made."""
        return self._start(self._synth_then_play, text, voice, speed, volume, pause,
                           blocking=blocking, extra=(on_ready, on_done))

    def _start(self, target, text, voice, speed, volume, pause, blocking, extra):
        self.stop()
        text = (text or "").strip()
        if not text:
            return False, "nothing to read"

        exe = piper_exe(self._cfg("piper_exe", ""))
        if not os.path.exists(exe):
            return False, "piper.exe not found — see README"
        voice, speed, volume, pause = self._settings(voice, speed, volume, pause)
        vid, info = self.resolve(voice)
        if not info:
            return False, "no voices found — see README"

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
        noise_scale, noise_w = self._noise()
        proc = subprocess.Popen(
            piper_command(exe, info, ls, pause,
                          noise_scale=noise_scale, noise_w=noise_w),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=os.path.dirname(exe) or None, creationflags=_NO_WINDOW,
        )
        with self._lock:
            self._proc = proc
        errors = []
        threading.Thread(target=_drain, args=(proc.stderr, errors), daemon=True).start()
        # Feed on a separate thread: piper's stdin pipe is small, so a long
        # selection would block us before we ever start reading stdout.
        threading.Thread(target=_feed, args=(proc, text), daemon=True).start()

        try:
            self._pump(proc.stdout.read, info["rate"], volume)
        finally:
            _reap(proc)
        if proc.returncode not in (0, None) and not self._stop.is_set():
            message = (b"".join(errors).decode("utf-8", "replace").strip()
                       or f"piper exited with {proc.returncode}")
            self._emit("error", message)
            return message
        return None

    def _synth_then_play(self, text, exe, info, ls, pause, volume, on_ready, on_done):
        import tempfile

        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="piper-read-")
        os.close(fd)
        noise_scale, noise_w = self._noise()
        try:
            proc = subprocess.Popen(
                piper_command(exe, info, ls, pause, out_file=wav_path,
                              noise_scale=noise_scale, noise_w=noise_w),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, cwd=os.path.dirname(exe) or None,
                creationflags=_NO_WINDOW,
            )
            with self._lock:
                self._proc = proc
            errors = []
            threading.Thread(target=_drain, args=(proc.stderr, errors), daemon=True).start()
            try:
                proc.stdin.write((text + "\n").encode("utf-8"))
                proc.stdin.close()
            except OSError:
                pass
            proc.wait()
            if self._stop.is_set():
                return None
            if proc.returncode != 0 or not os.path.getsize(wav_path):
                message = (b"".join(errors).decode("utf-8", "replace").strip()
                           or "synthesis failed")
                self._emit("error", message)
                return message

            with wave.open(wav_path, "rb") as wf:
                rate = wf.getframerate()
                duration = wf.getnframes() / float(rate or 1)
                # Tell the overlay only once the device is actually running, so
                # its word clock isn't offset by the stream-open latency.
                self._pump(lambda n: wf.readframes(n // 2), rate, volume,
                           on_start=(lambda: on_ready(duration)) if on_ready else None)
        finally:
            if on_done:
                on_done()
            try:
                os.unlink(wav_path)
            except OSError:
                pass
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
