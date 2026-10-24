from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest

from vcity.domain.departments import Department
from vcity.domain.hours import TicketReason
from vcity.domain.tickets import Priority, ServiceRequest
from vcity.tickets.drafts import gap_ticket, service_ticket

LA = ZoneInfo("America/Los_Angeles")
TICKET_ID = UUID(int=417209)
POTHOLE = ServiceRequest(
    call_id="CA123",
    department=Department.PUBLIC_WORKS,
    summary="Deep pothole in the right lane",
    location="Main St and 6th St",
    callback_number="+19515550142",
)


def test_service_ticket_carries_everything_staff_need() -> None:
    draft = service_ticket(
        POTHOLE,
        TicketReason.AFTER_HOURS,
        "Public Works",
        ticket_id=TICKET_ID,
        next_open=datetime(2026, 10, 26, 7, 0, tzinfo=LA),
    )
    assert draft.title == "[417209] Public Works: Deep pothole in the right lane"
    assert draft.reference == "417209"
    assert draft.labels == frozenset({"Public Works"})
    assert draft.priority is Priority.NONE
    assert draft.description.split("\n\n") == [
        "**Call:** CA123",
        "**Why a ticket:** called outside office hours",
        "**Summary:** Deep pothole in the right lane",
        "**Location:** Main St and 6th St",
        "**Callback:** +19515550142",
        "**Office reopens:** Monday, October 26 at 7:00 AM",
    ]


def test_browser_call_without_location_or_reopening() -> None:
    request = ServiceRequest("WEB1", Department.CITY_CLERK, "Records request", None, None)
    draft = service_ticket(
        request, TicketReason.TRANSFER_FAILED, "City Clerk", ticket_id=TICKET_ID, next_open=None
    )
    assert "**Location:** not given" in draft.description
    assert "**Callback:** none (browser call)" in draft.description
    assert "the transfer was not answered" in draft.description
    assert "Office reopens" not in draft.description


@pytest.mark.parametrize("reason", list(TicketReason))
def test_every_reason_has_wording(reason: TicketReason) -> None:
    draft = service_ticket(POTHOLE, reason, "Public Works", ticket_id=TICKET_ID, next_open=None)
    assert "**Why a ticket:** " in draft.description


def test_long_summaries_are_shortened_in_the_title() -> None:
    request = ServiceRequest("CA1", Department.UTILITIES, "word " * 40, None, None)
    draft = service_ticket(
        request, TicketReason.CALLER_REQUESTED, "Utilities", ticket_id=TICKET_ID, next_open=None
    )
    assert draft.title.endswith("…")
    assert len(draft.title) <= len("[417209] Utilities: ") + 80


def test_gap_ticket_is_low_priority_and_labelled() -> None:
    draft = gap_ticket("When will the new Target open?", "gap", ticket_id=TICKET_ID)
    assert draft.title == "Unanswered: When will the new Target open?"
    assert draft.labels == frozenset({"gap"})
    assert draft.priority is Priority.LOW
