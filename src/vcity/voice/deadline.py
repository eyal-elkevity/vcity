import asyncio
import logging
from collections.abc import Awaitable, Callable

_log = logging.getLogger(__name__)


async def end_at_deadline(
    seconds: float,
    *,
    notify: Callable[[], Awaitable[None]],
    hang_up: Callable[[], Awaitable[None]],
    notice_seconds: float = 1.0,
) -> None:
    await asyncio.sleep(seconds)
    try:
        await notify()
    except Exception as error:  # noqa: BLE001
        _log.warning("time limit notice failed: %s", type(error).__name__)
    await asyncio.sleep(notice_seconds)
    await hang_up()
