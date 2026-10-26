import logging
import os
import signal
from collections.abc import Callable
from pathlib import Path

from vcity.gate.line import CallLine

logger = logging.getLogger(__name__)

STATM = Path("/proc/self/statm")
BYTES_PER_MB = 1024 * 1024


def resident_mb() -> float | None:
    if not STATM.exists():
        return None
    resident_pages = int(STATM.read_text().split()[1])
    return resident_pages * os.sysconf("SC_PAGE_SIZE") / BYTES_PER_MB


def stop_process() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


class MemoryRecycler:
    def __init__(
        self,
        limit_mb: int,
        *,
        measure: Callable[[], float | None] = resident_mb,
        stop: Callable[[], None] = stop_process,
    ) -> None:
        self._limit_mb = limit_mb
        self._measure = measure
        self._stop = stop

    def after_call(self, line: CallLine) -> bool:
        if line.busy:
            return False
        resident = self._measure()
        if resident is None or resident <= self._limit_mb:
            return False
        logger.warning("recycling at %d MB", resident)
        self._stop()
        return True
