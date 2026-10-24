from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.config.policy import corona_policy
from vcity.domain.departments import Department
from vcity.domain.hours import Ticket, TicketReason, Transfer, decide

LA = ZoneInfo("America/Los_Angeles")
DEMO_PHONE = "+15550100000"
FEDERAL_CLOSURES_2027 = {
    date(2027, 1, 1),
    date(2027, 1, 18),
    date(2027, 2, 15),
    date(2027, 5, 31),
    date(2027, 6, 18),
    date(2027, 6, 19),
    date(2027, 7, 4),
    date(2027, 7, 5),
    date(2027, 9, 6),
    date(2027, 10, 11),
    date(2027, 11, 11),
    date(2027, 11, 25),
    date(2027, 12, 24),
    date(2027, 12, 25),
    date(2027, 12, 31),
}


def test_calendar_includes_federal_holidays() -> None:
    holidays = corona_policy().holidays
    assert date(2026, 11, 26) in holidays
    assert date(2026, 11, 11) in holidays
    assert date(2026, 10, 22) not in holidays


def test_closure_days_are_the_federal_holidays() -> None:
    holidays = corona_policy().holidays
    year_days = (date(2027, 1, 1) + timedelta(days=n) for n in range(365))
    assert {day for day in year_days if day in holidays} == FEDERAL_CLOSURES_2027


def test_pothole_reported_friday_morning_becomes_ticket_for_monday() -> None:
    public_works = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)[Department.PUBLIC_WORKS]
    friday_morning = datetime(2026, 10, 23, 10, tzinfo=LA)
    assert decide(public_works, friday_morning, corona_policy()) == Ticket(
        TicketReason.AFTER_HOURS, datetime(2026, 10, 26, 7, tzinfo=LA)
    )


def test_thursday_morning_rings_the_demo_phone() -> None:
    public_works = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)[Department.PUBLIC_WORKS]
    thursday_morning = datetime(2026, 10, 22, 10, tzinfo=LA)
    assert decide(public_works, thursday_morning, corona_policy()) == Transfer(DEMO_PHONE)
