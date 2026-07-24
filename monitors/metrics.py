"""Background sampler for the CPU / RAM / GPU / network tray readouts.

Runs on its own thread because psutil.cpu_percent and the NVML calls both
block, and a stalled sensor must never freeze the UI. The thread only ever
hands finished values to a callback.

NVML is optional. If nvmlInit fails — no NVIDIA card, no driver, a laptop with
only integrated graphics — the gpu key is absent from every sample and that
readout is dropped instead of showing a dash forever.

Optimus laptops need more than that. When the discrete GPU parks, the driver
quietly invalidates our NVML session and every query returns NVML_ERROR_UNKNOWN
until the library is shut down and re-initialised. That recovery costs ~600ms,
so it is rate-limited: hammering it once a second would burn a core and keep
the dGPU from sleeping, which is the opposite of what a laptop wants.
"""

import threading
import time

import psutil


class Gpu:
    """NVML utilisation with lazy init and rate-limited session recovery."""

    RECOVER_EVERY = 10.0

    def __init__(self):
        self.nvml = None
        self.handle = None
        self.tried = False
        self.last_value = None
        self.last_ok = 0.0
        self.last_recover = 0.0

    def _open(self):
        self.nvml.nvmlInit()
        self.handle = self.nvml.nvmlDeviceGetHandleByIndex(0)

    def available(self):
        """False means hide the readout entirely; True means show it."""
        if self.tried:
            return self.handle is not None
        self.tried = True
        try:
            import pynvml

            self.nvml = pynvml
            self._open()
        except Exception:
            self.nvml, self.handle = None, None
        return self.handle is not None

    def name(self):
        if not self.available():
            return None
        try:
            value = self.nvml.nvmlDeviceGetName(self.handle)
            return value.decode() if isinstance(value, bytes) else value
        except Exception:
            return None

    def _query(self):
        return float(self.nvml.nvmlDeviceGetUtilizationRates(self.handle).gpu)

    def read(self):
        """Utilisation percent, or None when the card is present but not answering."""
        if not self.available():
            return None
        now = time.monotonic()
        try:
            self.last_value, self.last_ok = self._query(), now
            return self.last_value
        except Exception:
            pass
        if now - self.last_recover >= self.RECOVER_EVERY:
            self.last_recover = now
            try:
                self.nvml.nvmlShutdown()
            except Exception:
                pass
            try:
                self._open()
                self.last_value, self.last_ok = self._query(), now
                return self.last_value
            except Exception:
                self.last_value = None
        # Between recovery attempts, a recent reading beats a flickering dash.
        if now - self.last_ok <= self.RECOVER_EVERY * 1.5:
            return self.last_value
        return None


_gpu = Gpu()


def gpu_available():
    return _gpu.available()


def gpu_name():
    return _gpu.name()


def format_rate(bytes_per_sec):
    """Auto-scaled throughput that stays four characters wide where it can."""
    value = max(0.0, bytes_per_sec)
    for unit, step in (("B", 1024), ("K", 1024 * 1024), ("M", 1024 ** 3)):
        if value < step:
            scaled = value / (step / 1024)
            if unit == "B":
                return f"{value:.0f} B/s"
            return f"{scaled:.1f} {unit}B/s" if scaled < 10 else f"{scaled:.0f} {unit}B/s"
    return f"{value / 1024 ** 3:.1f} GB/s"


class Poller:
    """Samples every `interval` seconds and calls on_sample(dict)."""

    def __init__(self, config, on_sample):
        self.config = config
        self.on_sample = on_sample
        self._stop = threading.Event()
        self._thread = None
        self._last_net = None
        self._last_time = None

    def interval(self):
        return self.config.num("poll_interval", 0.5, 10.0)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="metrics")
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _net_rates(self, now):
        counters = psutil.net_io_counters()
        sample = (counters.bytes_sent, counters.bytes_recv)
        if self._last_net is None or self._last_time is None:
            self._last_net, self._last_time = sample, now
            return 0.0, 0.0
        elapsed = max(1e-6, now - self._last_time)
        # Counters are unsigned and wrap or reset when an adapter cycles.
        up = max(0, sample[0] - self._last_net[0]) / elapsed
        down = max(0, sample[1] - self._last_net[1]) / elapsed
        self._last_net, self._last_time = sample, now
        return up, down

    def sample(self):
        now = time.monotonic()
        up, down = self._net_rates(now)
        data = {
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "net_up": up,
            "net_down": down,
        }
        if _gpu.available():
            data["gpu"] = _gpu.read()  # None = card present but parked
        return data

    def _run(self):
        psutil.cpu_percent(interval=None)  # prime the delta
        self._net_rates(time.monotonic())
        while not self._stop.wait(self.interval()):
            try:
                self.on_sample(self.sample())
            except Exception:
                pass  # a bad frame must not kill the poller


def format_sample(sample):
    """Sample dict -> display strings keyed the same as theme.METRIC_COLORS."""
    out = {}
    for key in ("cpu", "ram", "gpu"):
        if key in sample:
            value = sample[key]
            out[key] = "--" if value is None else f"{value:.0f}%"
    if "net_up" in sample:
        out["net_up"] = format_rate(sample["net_up"])
    if "net_down" in sample:
        out["net_down"] = format_rate(sample["net_down"])
    return out
