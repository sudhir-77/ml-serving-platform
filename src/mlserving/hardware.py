"""Host facts and a peak RSS sampler for the current process tree."""

from __future__ import annotations

import platform
import threading

import psutil


def hardware_snapshot() -> dict:
    vm = psutil.virtual_memory()
    cpu = platform.processor() or platform.machine()
    return {
        "cpu": cpu,
        "ram_gb": round(vm.total / (1024**3), 2),
        "gpu": None,
        "python": platform.python_version(),
        "os": platform.platform(),
    }


class PeakTracker:
    def __init__(self, interval_s: float = 0.25) -> None:
        self.interval_s = interval_s
        self.peak_bytes = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="peak-rss", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        proc = psutil.Process()
        while not self._stop.wait(self.interval_s):
            self.peak_bytes = max(self.peak_bytes, _tree_rss(proc))

    def stop(self) -> float:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        proc = psutil.Process()
        self.peak_bytes = max(self.peak_bytes, _tree_rss(proc))
        return self.peak_bytes / (1024**3)


def _tree_rss(proc: psutil.Process) -> int:
    total = 0
    try:
        total += int(proc.memory_info().rss)
    except psutil.Error:
        return total
    for child in proc.children(recursive=True):
        try:
            total += int(child.memory_info().rss)
        except psutil.Error:
            continue
    return total
