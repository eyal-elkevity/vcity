from pathlib import Path

import pytest

from vcity.gate.line import CallLine
from vcity.recycle import MemoryRecycler, resident_mb


class StopSpy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def test_a_big_idle_process_recycles() -> None:
    stop = StopSpy()
    recycler = MemoryRecycler(1400, measure=lambda: 1500.0, stop=stop)
    assert recycler.after_call(CallLine())
    assert stop.calls == 1


def test_a_small_process_keeps_running() -> None:
    stop = StopSpy()
    recycler = MemoryRecycler(1400, measure=lambda: 900.0, stop=stop)
    assert not recycler.after_call(CallLine())
    assert stop.calls == 0


def test_a_process_with_a_call_on_the_line_keeps_running() -> None:
    stop = StopSpy()
    line = CallLine()
    assert line.take()
    recycler = MemoryRecycler(1400, measure=lambda: 1500.0, stop=stop)
    assert not recycler.after_call(line)
    assert stop.calls == 0


def test_no_reading_never_recycles() -> None:
    stop = StopSpy()
    recycler = MemoryRecycler(1400, measure=lambda: None, stop=stop)
    assert not recycler.after_call(CallLine())
    assert stop.calls == 0


@pytest.mark.skipif(not Path("/proc/self/statm").exists(), reason="needs procfs")
def test_resident_memory_is_read_where_proc_exists() -> None:
    reading = resident_mb()
    assert reading is not None
    assert reading > 10
