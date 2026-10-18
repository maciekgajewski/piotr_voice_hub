"""GPU memory sampling via NVML.

Uses the v2 memory query: its `used` excludes the memory the driver reserves (464 MiB on piotr),
which the v1 query counts as used. NVML values are device-wide, not per process.
"""

import threading
import time

import pynvml

MIB = 1024 * 1024


class GpuMemorySampler:
    """Samples device memory used in a background thread and records the peak."""

    def __init__(self, interval_seconds: float = 0.05) -> None:
        pynvml.nvmlInit()
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        self._interval = interval_seconds
        self._stop = threading.Event()
        self.baseline_mib = self.used_mib()
        self.peak_mib = self.baseline_mib
        self._thread = threading.Thread(target=self._run, daemon=True)

    def describe(self) -> str:
        name = pynvml.nvmlDeviceGetName(self._handle)
        major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(self._handle)
        driver = pynvml.nvmlSystemGetDriverVersion()
        total = self._memory().total // MIB
        return f"{name}, sm_{major}{minor}, driver {driver}, {total} MiB total"

    def used_mib(self) -> int:
        return int(self._memory().used) // MIB

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        assert not self._thread.is_alive()

    def _memory(self):
        return pynvml.nvmlDeviceGetMemoryInfo(self._handle, version=pynvml.nvmlMemory_v2)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.peak_mib = max(self.peak_mib, self.used_mib())
            time.sleep(self._interval)
