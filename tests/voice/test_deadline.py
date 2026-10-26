import asyncio
import contextlib

from vcity.voice.deadline import end_at_deadline


async def test_the_caller_is_told_before_the_line_hangs_up() -> None:
    events: list[str] = []

    async def notify() -> None:
        events.append("notify")

    async def hang_up() -> None:
        events.append("hang up")

    await end_at_deadline(0.01, notify=notify, hang_up=hang_up, notice_seconds=0.01)
    assert events == ["notify", "hang up"]


async def test_a_call_that_ends_first_is_never_cut() -> None:
    events: list[str] = []

    async def notify() -> None:
        events.append("notify")

    async def hang_up() -> None:
        events.append("hang up")

    task = asyncio.create_task(end_at_deadline(5, notify=notify, hang_up=hang_up))
    await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert events == []
