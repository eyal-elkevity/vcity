from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from vcity.domain.departments import FULL_DAY, Department, DepartmentInfo, OpenWindow, Weekday
from vcity.domain.hours import (
    HoursPolicy,
    Ticket,
    TicketReason,
    Transfer,
    decide,
    next_open,
)

LA = ZoneInfo("America/Los_Angeles")
THANKSGIVING_2026 = date(2026, 11, 26)
VETERANS_DAY_2026 = date(2026, 11, 11)
POLICY = HoursPolicy(
    tz=LA,
    closing_soon=timedelta(minutes=10),
    holidays=frozenset({THANKSGIVING_2026, VETERANS_DAY_2026}),
)
DEMO_PHONE = "+15550100000"


def at(hour: float) -> timedelta:
    return timedelta(hours=hour)


def department(hours: dict[Weekday, OpenWindow]) -> DepartmentInfo:
    return DepartmentInfo(
        department=Department.PUBLIC_WORKS,
        name="Test Department",
        description="Test",
        public_phone="+19515550000",
        transfer_to=DEMO_PHONE,
        hours=hours,
        source_url="https://www.coronaca.gov/",
    )


CITY_HALL_WEEK = {
    day: OpenWindow(at(7), at(18)) for day in (Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU)
}
CITY_HALL = department(CITY_HALL_WEEK)
SHELTER = department(
    {day: OpenWindow(at(11), at(17.5)) for day in Weekday if day is not Weekday.SUN},
)
ALWAYS_OPEN = department({day: OpenWindow(timedelta(0), FULL_DAY) for day in Weekday})


def la(year: int, month: int, day: int, hour: int, minute: int = 0, *, second: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=LA)


def test_thursday_morning_transfers() -> None:
    assert decide(CITY_HALL, la(2026, 10, 22, 10), POLICY) == Transfer(DEMO_PHONE)


def test_friday_is_closed_and_reopens_monday_morning() -> None:
    assert decide(CITY_HALL, la(2026, 10, 23, 10), POLICY) == Ticket(
        TicketReason.AFTER_HOURS, la(2026, 10, 26, 7)
    )


def test_last_transfer_before_cutoff() -> None:
    assert decide(CITY_HALL, la(2026, 10, 22, 17, 49, second=59), POLICY) == Transfer(DEMO_PHONE)


@pytest.mark.parametrize(("minute", "second"), [(50, 0), (59, 59)])
def test_closing_soon_files_ticket(minute: int, second: int) -> None:
    assert decide(CITY_HALL, la(2026, 10, 22, 17, minute, second=second), POLICY) == Ticket(
        TicketReason.CLOSING_SOON, la(2026, 10, 26, 7)
    )


def test_closing_time_itself_is_closed() -> None:
    assert decide(CITY_HALL, la(2026, 10, 22, 18), POLICY) == Ticket(
        TicketReason.AFTER_HOURS, la(2026, 10, 26, 7)
    )


def test_before_opening_reopens_same_morning() -> None:
    assert decide(CITY_HALL, la(2026, 10, 26, 6, 59), POLICY) == Ticket(
        TicketReason.AFTER_HOURS, la(2026, 10, 26, 7)
    )


def test_thanksgiving_thursday_is_a_holiday() -> None:
    assert decide(CITY_HALL, la(2026, 11, 26, 10), POLICY) == Ticket(
        TicketReason.HOLIDAY, la(2026, 11, 30, 7)
    )


def test_midweek_holiday_reopens_next_day() -> None:
    assert decide(CITY_HALL, la(2026, 11, 11, 10), POLICY) == Ticket(
        TicketReason.HOLIDAY, la(2026, 11, 12, 7)
    )


def test_reopening_after_daylight_saving_ends_uses_standard_time() -> None:
    reopens = next_open(CITY_HALL, la(2026, 10, 31, 20), POLICY)
    assert reopens == la(2026, 11, 2, 7)
    assert reopens.utcoffset() == timedelta(hours=-8)


def test_reopening_after_daylight_saving_starts_uses_daylight_time() -> None:
    reopens = next_open(CITY_HALL, la(2027, 3, 13, 12), POLICY)
    assert reopens == la(2027, 3, 15, 7)
    assert reopens.utcoffset() == timedelta(hours=-7)


def test_utc_input_is_judged_in_city_time() -> None:
    thursday_10am_in_corona = datetime(2026, 10, 22, 17, tzinfo=UTC)
    assert decide(CITY_HALL, thursday_10am_in_corona, POLICY) == Transfer(DEMO_PHONE)


def test_naive_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        decide(CITY_HALL, datetime(2026, 10, 22, 10), POLICY)  # noqa: DTZ001


def test_shelter_open_on_saturday() -> None:
    assert decide(SHELTER, la(2026, 10, 24, 12), POLICY) == Transfer(DEMO_PHONE)


def test_shelter_closing_soon_on_saturday() -> None:
    assert decide(SHELTER, la(2026, 10, 24, 17, 25), POLICY) == Ticket(
        TicketReason.CLOSING_SOON, la(2026, 10, 26, 11)
    )


def test_shelter_closed_sunday_reopens_monday_late_morning() -> None:
    assert decide(SHELTER, la(2026, 10, 25, 12), POLICY) == Ticket(
        TicketReason.AFTER_HOURS, la(2026, 10, 26, 11)
    )


@pytest.mark.parametrize(("hour", "minute"), [(3, 0), (23, 55)])
def test_always_open_line_never_closes_soon(hour: int, minute: int) -> None:
    assert decide(ALWAYS_OPEN, la(2026, 10, 25, hour, minute), POLICY) == Transfer(DEMO_PHONE)


def test_next_open_while_open_is_now() -> None:
    now = la(2026, 10, 22, 10)
    assert next_open(CITY_HALL, now, POLICY) == now


def test_department_that_never_opens_is_a_lookup_error() -> None:
    with pytest.raises(LookupError, match="never opens"):
        next_open(department({}), la(2026, 10, 22, 10), POLICY)
