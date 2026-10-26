import asyncio
from collections.abc import Awaitable, Callable


async def end_at_deadline(
    seconds: float,
    *,
    notify: Callable[[], Awaitable[None]],
    hang_up: Callable[[], Awaitable[None]],
    notice_seconds: float = 1.0,
) -> None:
    await asyncio.sleep(seconds)
    await notify()
    await asyncio.sleep(notice_seconds)
    await hang_up()
