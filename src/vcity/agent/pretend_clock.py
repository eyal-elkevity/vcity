from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import assert_never

from vcity.agent.services import Clock


class HoursMode(StrEnum):
    REAL = "real"
    OPEN = "open"
    CLOSED = "closed"


# Monday to Thursday at 2 PM falls inside every schedule in data/departments.yaml,
# and 8 PM falls outside all of them except the always-open one.
OPEN_AT = time(14)
CLOSED_AT = time(20)
THURSDAY = 3
DAYS_IN_WEEK = 7


def pretend_moment(mode: HoursMode, now: datetime) -> datetime | None:
    match mode:
        case HoursMode.REAL:
            return None
        case HoursMode.OPEN:
            weekday = now.weekday()
            ahead = 0 if weekday <= THURSDAY else DAYS_IN_WEEK - weekday
            return datetime.combine(now.date() + timedelta(days=ahead), OPEN_AT, now.tzinfo)
        case HoursMode.CLOSED:
            return datetime.combine(now.date(), CLOSED_AT, now.tzinfo)
        case _:
            assert_never(mode)


def pretend_clock(clock: Clock, mode: HoursMode) -> Clock:
    now = clock()
    moment = pretend_moment(mode, now)
    if moment is None:
        return clock
    offset = moment - now
    return lambda: clock() + offset
