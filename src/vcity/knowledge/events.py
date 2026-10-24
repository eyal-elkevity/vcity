import logging
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event

from vcity.knowledge.model import BREADCRUMB_SEPARATOR, Chunk, SourceKind

EVENTS_FEED_URL = (
    "https://api.withapps.io/api/v2/organizations/34/calendar/ical"
    "?calendarId=6900072526e9894ab08026a5&communityIds%5B0%5D=208"
)
EVENTS_BREADCRUMB = "City of Corona events"
_FEED_BOILERPLATE = re.compile(r"^(Community:|Google Maps:).*$", re.MULTILINE)
# The feed pads descriptions with zero-width joiners where the source had links.
_ZERO_WIDTH_JOINER = "‍"
_log = logging.getLogger(__name__)


def parse_events(ical: bytes, tz: ZoneInfo) -> list[Chunk]:
    chunks: list[Chunk] = []
    for event in Calendar.from_ical(ical).events:
        if "UID" not in event or "DTSTART" not in event:
            _log.warning("skipping iCal event without UID or DTSTART: %s", event.get("SUMMARY", ""))
            continue
        chunks.append(_to_chunk(event, tz))
    return chunks


def _to_chunk(event: Event, tz: ZoneInfo) -> Chunk:
    title = str(event.get("SUMMARY", "")).strip()
    start = event.decoded("DTSTART")
    end = event.decoded("DTEND") if "DTEND" in event else None
    starts, ends, when = _schedule(start, end, tz)
    location = str(event.get("LOCATION", "")).strip()
    description = _FEED_BOILERPLATE.sub("", str(event.get("DESCRIPTION", "")))
    description = description.replace(_ZERO_WIDTH_JOINER, " ").strip()
    lines = [f"{title}.", f"When: {when}."]
    if location:
        lines.append(f"Where: {location}.")
    if description:
        lines.append(description)
    return Chunk(
        cite_id=f"event:{event['UID']}",
        kind=SourceKind.EVENTS,
        breadcrumb=BREADCRUMB_SEPARATOR.join([EVENTS_BREADCRUMB, title]),
        text="\n".join(lines),
        url=str(event.get("URL", "")),
        date=starts,
        ends_at=ends,
    )


def _schedule(start: date, end: date | None, tz: ZoneInfo) -> tuple[datetime, datetime | None, str]:
    if isinstance(start, datetime):
        starts = _local(start, tz)
        ends = _local(end, tz) if end is not None else None
        when = f"{starts:%A, %B %-d, %Y at %-I:%M %p}"
        if ends is not None:
            when += f" until {ends:%-I:%M %p}"
        return starts, ends, when
    return (
        _local(start, tz),
        _local(end, tz) if end is not None else None,
        _all_day_when(start, end),
    )


def _all_day_when(first: date, end: date | None) -> str:
    # RFC 5545: an all-day DTEND is exclusive, so the last day is the one before it.
    last = end - timedelta(days=1) if end is not None else first
    day = f"{first:%A, %B %-d, %Y}"
    if last <= first:
        return f"{day} (all day)"
    return f"{day} through {last:%A, %B %-d, %Y} (all day)"


def _local(moment: date, tz: ZoneInfo) -> datetime:
    match moment:
        case datetime(tzinfo=None):
            return moment.replace(tzinfo=tz)
        case datetime():
            return moment.astimezone(tz)
        case _:
            return datetime.combine(moment, time(), tzinfo=tz)
