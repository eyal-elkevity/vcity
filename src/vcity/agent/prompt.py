from collections.abc import Mapping
from datetime import datetime, time, timedelta
from functools import cache
from importlib.resources import files

from vcity.domain.departments import FULL_DAY, Department, DepartmentInfo, OpenWindow, Weekday

_DAY_NAMES = {
    Weekday.MON: "Mon",
    Weekday.TUE: "Tue",
    Weekday.WED: "Wed",
    Weekday.THU: "Thu",
    Weekday.FRI: "Fri",
    Weekday.SAT: "Sat",
    Weekday.SUN: "Sun",
}


@cache
def _template() -> str:
    return files("vcity.agent").joinpath("prompts/system.md").read_text(encoding="utf-8")


def system_prompt(directory: Mapping[Department, DepartmentInfo]) -> str:
    lines = (
        f"- {info.department.value}: {info.name}. {info.description}. "
        f"Hours: {describe_hours(info.hours)}."
        for info in directory.values()
    )
    return _template().replace("{departments}", "\n".join(lines))


def clock_note(now: datetime) -> str:
    return f"Current date and time in Corona: {now:%A, %B %-d, %Y, %-I:%M %p} (Pacific time)."


def describe_hours(hours: Mapping[Weekday, OpenWindow]) -> str:
    if len(hours) == len(Weekday) and all(
        w.runs_to_midnight and w.start == timedelta() for w in hours.values()
    ):
        return "open 24 hours, every day"
    groups: list[tuple[list[Weekday], OpenWindow]] = []
    for day in sorted(hours):
        window = hours[day]
        if groups and groups[-1][1] == window and groups[-1][0][-1] == day - 1:
            groups[-1][0].append(day)
        else:
            groups.append(([day], window))
    return "; ".join(f"{_days(days)} {_clock(w.start)} to {_clock(w.end)}" for days, w in groups)


def _days(days: list[Weekday]) -> str:
    first, last = _DAY_NAMES[days[0]], _DAY_NAMES[days[-1]]
    return first if len(days) == 1 else f"{first} to {last}"


def _clock(offset: timedelta) -> str:
    if offset == FULL_DAY:
        return "midnight"
    hours, seconds = divmod(int(offset.total_seconds()), 3600)
    return f"{time(hours, seconds // 60):%-I:%M %p}"
