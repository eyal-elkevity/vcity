import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from vcity.knowledge.events import parse_events
from vcity.knowledge.model import SourceKind

LA = ZoneInfo("America/Los_Angeles")
FEED = (Path(__file__).parents[1] / "fixtures" / "events" / "corona.ics").read_bytes()
LIBRARY_BOARD = "event:e8fab8b6-85a7-4e59-9e1f-3dc2ea1791a3"


def _calendar(*events: str) -> bytes:
    body = "".join(f"BEGIN:VEVENT\r\n{event}END:VEVENT\r\n" for event in events)
    return (
        f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n{body}END:VCALENDAR\r\n".encode()
    )


def test_every_event_becomes_one_chunk() -> None:
    chunks = parse_events(FEED, LA)
    assert len(chunks) == 2
    assert {chunk.kind for chunk in chunks} == {SourceKind.EVENTS}


def test_event_chunk_carries_times_place_and_link() -> None:
    chunk = next(c for c in parse_events(FEED, LA) if c.cite_id == LIBRARY_BOARD)
    assert chunk.breadcrumb == "City of Corona events > Library Board of Trustees Meeting"
    assert chunk.date == datetime(2026, 10, 27, 17, 30, tzinfo=LA)
    assert chunk.ends_at == datetime(2026, 10, 27, 19, 30, tzinfo=LA)
    assert chunk.url == "https://community.coronaca.gov/coronaca_main/260127903"
    assert "When: Tuesday, October 27, 2026 at 5:30 PM until 7:30 PM." in chunk.text
    assert "Where: 400 South Vicentia Avenue, Corona, CA, USA." in chunk.text


def test_feed_boilerplate_is_stripped() -> None:
    chunk = next(c for c in parse_events(FEED, LA) if c.cite_id == LIBRARY_BOARD)
    assert "Community:" not in chunk.text
    assert "Google Maps" not in chunk.text
    assert "‍" not in chunk.text


def test_all_day_events_start_at_local_midnight() -> None:
    feed = _calendar(
        "UID:all-day\r\nDTSTAMP:20261024T000000Z\r\n"
        "DTSTART;VALUE=DATE:20261111\r\nSUMMARY:Veterans Day Ceremony\r\n"
    )
    (chunk,) = parse_events(feed, LA)
    assert chunk.date == datetime(2026, 11, 11, tzinfo=LA)
    assert chunk.ends_at is None
    assert "When: Wednesday, November 11, 2026 (all day)." in chunk.text


def test_one_day_all_day_event_ends_at_exclusive_dtend() -> None:
    feed = _calendar(
        "UID:one-day\r\nDTSTART;VALUE=DATE:20261111\r\nDTEND;VALUE=DATE:20261112\r\n"
        "SUMMARY:Veterans Day Ceremony\r\n"
    )
    (chunk,) = parse_events(feed, LA)
    assert chunk.date == datetime(2026, 11, 11, tzinfo=LA)
    assert chunk.ends_at == datetime(2026, 11, 12, tzinfo=LA)
    assert "When: Wednesday, November 11, 2026 (all day)." in chunk.text


def test_multi_day_all_day_event_names_its_last_day() -> None:
    feed = _calendar(
        "UID:two-day\r\nDTSTART;VALUE=DATE:20261111\r\nDTEND;VALUE=DATE:20261113\r\n"
        "SUMMARY:Veterans Day Weekend Fair\r\n"
    )
    (chunk,) = parse_events(feed, LA)
    assert chunk.ends_at == datetime(2026, 11, 13, tzinfo=LA)
    assert (
        "When: Wednesday, November 11, 2026 through Thursday, November 12, 2026 (all day)."
        in chunk.text
    )


def test_utc_start_is_converted_to_city_time() -> None:
    feed = _calendar("UID:utc\r\nDTSTART:20261028T010000Z\r\nSUMMARY:Evening Session\r\n")
    (chunk,) = parse_events(feed, LA)
    assert chunk.date == datetime(2026, 10, 27, 18, tzinfo=LA)
    assert chunk.date is not None
    assert chunk.date.tzinfo == LA
    assert "When: Tuesday, October 27, 2026 at 6:00 PM." in chunk.text


def test_event_without_uid_is_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    feed = _calendar(
        "UID:kept\r\nDTSTART:20261028T010000Z\r\nSUMMARY:Kept\r\n",
        "DTSTART:20261028T010000Z\r\nSUMMARY:No id\r\n",
    )
    with caplog.at_level(logging.WARNING, logger="vcity.knowledge.events"):
        chunks = parse_events(feed, LA)
    assert [chunk.cite_id for chunk in chunks] == ["event:kept"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_event_without_start_is_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    feed = _calendar(
        "UID:kept\r\nDTSTART:20261028T010000Z\r\nSUMMARY:Kept\r\n",
        "UID:no-start\r\nSUMMARY:No start\r\n",
    )
    with caplog.at_level(logging.WARNING, logger="vcity.knowledge.events"):
        chunks = parse_events(feed, LA)
    assert [chunk.cite_id for chunk in chunks] == ["event:kept"]
    assert any(record.levelno == logging.WARNING for record in caplog.records)
