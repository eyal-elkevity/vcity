from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vcity.knowledge.events import parse_events
from vcity.knowledge.model import SourceKind

LA = ZoneInfo("America/Los_Angeles")
FEED = (Path(__file__).parents[1] / "fixtures" / "events" / "corona.ics").read_bytes()
LIBRARY_BOARD = "event:e8fab8b6-85a7-4e59-9e1f-3dc2ea1791a3"


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
    feed = (
        b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n"
        b"BEGIN:VEVENT\r\nUID:all-day\r\nDTSTAMP:20261024T000000Z\r\n"
        b"DTSTART;VALUE=DATE:20261111\r\nSUMMARY:Veterans Day Ceremony\r\n"
        b"END:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    (chunk,) = parse_events(feed, LA)
    assert chunk.date == datetime(2026, 11, 11, tzinfo=LA)
    assert chunk.ends_at is None
