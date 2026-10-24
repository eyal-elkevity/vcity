from datetime import date, datetime
from zoneinfo import ZoneInfo

from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.config.policy import corona_policy
from vcity.domain.departments import Department
from vcity.domain.hours import Ticket, TicketReason, Transfer, decide

LA = ZoneInfo("America/Los_Angeles")
DEMO_PHONE = "+15550100000"


def test_calendar_includes_federal_holidays() -> None:
    holidays = corona_policy().holidays
    assert date(2026, 11, 26) in holidays
    assert date(2026, 11, 11) in holidays
    assert date(2026, 10, 22) not in holidays


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
