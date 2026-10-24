from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from vcity.domain.departments import DepartmentInfo, OpenWindow, Weekday

SEARCH_HORIZON_DAYS = 14


class TicketReason(StrEnum):
    AFTER_HOURS = "after_hours"
    HOLIDAY = "holiday"
    CLOSING_SOON = "closing_soon"
    TRANSFER_FAILED = "transfer_failed"
    CALLER_REQUESTED = "caller_requested"


@dataclass(frozen=True, slots=True)
class Transfer:
    number: str


@dataclass(frozen=True, slots=True)
class Ticket:
    reason: TicketReason
    next_open: datetime | None


type RoutingDecision = Transfer | Ticket


class HolidayCalendar(Protocol):
    def __contains__(self, key: object, /) -> bool: ...


@dataclass(frozen=True, slots=True)
class HoursPolicy:
    tz: ZoneInfo
    closing_soon: timedelta
    holidays: HolidayCalendar


def decide(dept: DepartmentInfo, now: datetime, policy: HoursPolicy) -> RoutingDecision:
    local = _to_local(now, policy.tz)
    if local.date() in policy.holidays:
        return Ticket(TicketReason.HOLIDAY, next_open(dept, now, policy))
    window = dept.hours.get(Weekday(local.weekday()))
    offset = _offset_from_midnight(local)
    if window is None or not window.contains(offset):
        return Ticket(TicketReason.AFTER_HOURS, next_open(dept, now, policy))
    if _is_closing_soon(window, offset, policy):
        return Ticket(TicketReason.CLOSING_SOON, next_open(dept, now, policy))
    return Transfer(dept.transfer_to)


def next_open(dept: DepartmentInfo, now: datetime, policy: HoursPolicy) -> datetime:
    local = _to_local(now, policy.tz)
    for days_ahead in range(SEARCH_HORIZON_DAYS):
        day = local.date() + timedelta(days=days_ahead)
        window = dept.hours.get(Weekday(day.weekday()))
        if window is None or day in policy.holidays:
            continue
        opens = _local_moment(day, window.start, policy.tz)
        if days_ahead > 0 or local < opens:
            return opens
        offset = _offset_from_midnight(local)
        if window.contains(offset) and not _is_closing_soon(window, offset, policy):
            return local
    raise LookupError(f"{dept.name} never opens within {SEARCH_HORIZON_DAYS} days")


def _to_local(now: datetime, tz: ZoneInfo) -> datetime:
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(tz)


def _offset_from_midnight(local: datetime) -> timedelta:
    return datetime.combine(date.min, local.time()) - datetime.combine(date.min, time())


def _local_moment(day: date, offset: timedelta, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(), tzinfo=tz) + offset


def _is_closing_soon(window: OpenWindow, offset: timedelta, policy: HoursPolicy) -> bool:
    return not window.runs_to_midnight and offset >= window.end - policy.closing_soon
