import re
from datetime import date, datetime, time
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


def parse_events(ical: bytes, tz: ZoneInfo) -> list[Chunk]:
    return [_to_chunk(event, tz) for event in Calendar.from_ical(ical).events]


def _to_chunk(event: Event, tz: ZoneInfo) -> Chunk:
    title = str(event.get("SUMMARY", "")).strip()
    starts = _aware(event.decoded("DTSTART"), tz)
    ends = _aware(event.decoded("DTEND"), tz) if "DTEND" in event else None
    location = str(event.get("LOCATION", "")).strip()
    description = _FEED_BOILERPLATE.sub("", str(event.get("DESCRIPTION", "")))
    description = description.replace(_ZERO_WIDTH_JOINER, " ").strip()
    when = f"{starts:%A, %B %-d, %Y at %-I:%M %p}" + (f" until {ends:%-I:%M %p}" if ends else "")
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


def _aware(moment: object, tz: ZoneInfo) -> datetime:
    match moment:
        case datetime() if moment.tzinfo is None:
            return moment.replace(tzinfo=tz)
        case datetime():
            return moment
        case date():
            return datetime.combine(moment, time(), tzinfo=tz)
        case _:
            raise ValueError(f"unsupported iCal date value: {moment!r}")
