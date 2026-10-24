from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from vcity.agent.prompt import clock_note, describe_hours, system_prompt
from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.domain.departments import Department, OpenWindow, Weekday

LA = ZoneInfo("America/Los_Angeles")
DIRECTORY = load_directory(DEFAULT_DIRECTORY_PATH, transfer_to="+15555550100")


def hours(start: int, end: int, *days: Weekday) -> dict[Weekday, OpenWindow]:
    return {day: OpenWindow(timedelta(hours=start), timedelta(hours=end)) for day in days}


def test_consecutive_days_with_equal_hours_are_grouped() -> None:
    city_hall = hours(7, 18, Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU)
    assert describe_hours(city_hall) == "Mon to Thu 7:00 AM to 6:00 PM"


def test_gaps_and_different_hours_stay_separate() -> None:
    week = hours(9, 12, Weekday.MON) | hours(13, 17, Weekday.WED, Weekday.THU)
    assert describe_hours(week) == "Mon 9:00 AM to 12:00 PM; Wed to Thu 1:00 PM to 5:00 PM"


def test_round_the_clock_line_reads_naturally() -> None:
    assert describe_hours(hours(0, 24, *Weekday)) == "open 24 hours, every day"


def test_prompt_lists_every_department_key_with_its_hours() -> None:
    prompt = system_prompt(DIRECTORY)
    assert "{departments}" not in prompt
    for department in Department:
        assert f"- {department.value}: " in prompt
    assert "Animal Services. Stray and lost animals" in prompt
    assert "Hours: Mon to Sat 11:00 AM to 5:30 PM." in prompt


def test_clock_note_names_the_day_and_time() -> None:
    note = clock_note(datetime(2026, 10, 30, 19, 30, tzinfo=LA))
    assert (
        note == "Current date and time in Corona: Friday, October 30, 2026, 7:30 PM (Pacific time)."
    )
