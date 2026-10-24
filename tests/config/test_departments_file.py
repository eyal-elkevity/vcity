from datetime import timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.domain.departments import FULL_DAY, Department, OpenWindow, Weekday

DEMO_PHONE = "+15550100000"


def at(hour: float) -> timedelta:
    return timedelta(hours=hour)


def test_directory_covers_every_department() -> None:
    directory = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)
    assert set(directory) == set(Department)


def test_city_hall_departments_are_closed_on_friday() -> None:
    public_works = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)[Department.PUBLIC_WORKS]
    assert public_works.hours == {
        day: OpenWindow(at(7), at(18))
        for day in (Weekday.MON, Weekday.TUE, Weekday.WED, Weekday.THU)
    }


def test_shelter_hours_run_monday_to_saturday() -> None:
    shelter = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)[Department.ANIMAL_SERVICES]
    assert shelter.hours == {
        day: OpenWindow(at(11), at(17.5)) for day in Weekday if day is not Weekday.SUN
    }


def test_police_line_is_open_around_the_clock() -> None:
    police = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)[Department.POLICE_NON_EMERGENCY]
    assert police.hours == {day: OpenWindow(timedelta(0), FULL_DAY) for day in Weekday}


def test_only_the_police_line_stays_open_on_holidays() -> None:
    directory = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)
    open_on_holidays = {d for d, info in directory.items() if not info.closes_on_holidays}
    assert open_on_holidays == {Department.POLICE_NON_EMERGENCY}


def test_transfer_number_comes_from_the_caller_not_the_file() -> None:
    directory = load_directory(DEFAULT_DIRECTORY_PATH, DEMO_PHONE)
    assert {info.transfer_to for info in directory.values()} == {DEMO_PHONE}


def test_invalid_transfer_number_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"E\.164"):
        load_directory(DEFAULT_DIRECTORY_PATH, "054-1234567")


VALID_ENTRY = """
    name: Test
    description: Test
    public_phone: "+19515550000"
    schedule: city_hall
    source_url: https://www.coronaca.gov/
"""


def write_directory(tmp_path: Path, schedules: str, departments: list[str]) -> Path:
    body = "".join(f"  {dept}:{VALID_ENTRY}" for dept in departments)
    path = tmp_path / "departments.yaml"
    path.write_text(f"schedules:\n{schedules}\ndepartments:\n{body}", encoding="utf-8")
    return path


CITY_HALL_SCHEDULE = '  city_hall:\n    mon: ["07:00", "18:00"]\n'


def test_missing_department_is_rejected(tmp_path: Path) -> None:
    path = write_directory(tmp_path, CITY_HALL_SCHEDULE, ["public_works"])
    with pytest.raises(ValidationError, match="missing departments"):
        load_directory(path, DEMO_PHONE)


def test_unknown_schedule_is_rejected(tmp_path: Path) -> None:
    schedules = '  weekends:\n    sat: ["09:00", "12:00"]\n'
    path = write_directory(tmp_path, schedules, list(Department))
    with pytest.raises(ValidationError, match="unknown schedules"):
        load_directory(path, DEMO_PHONE)


@pytest.mark.parametrize("bad_time", ["25:00", "7:00", "24:30"])
def test_malformed_time_is_rejected(tmp_path: Path, bad_time: str) -> None:
    schedules = f'  city_hall:\n    mon: ["{bad_time}", "18:00"]\n'
    path = write_directory(tmp_path, schedules, list(Department))
    with pytest.raises(ValidationError):
        load_directory(path, DEMO_PHONE)
