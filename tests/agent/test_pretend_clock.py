from datetime import datetime, timedelta

import pytest

from vcity.agent.pretend_clock import HoursMode, pretend_clock, pretend_moment
from vcity.agent.services import CITY_TZ


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=CITY_TZ)


def test_real_hours_keep_the_real_clock() -> None:
    assert pretend_moment(HoursMode.REAL, at(26, 5)) is None


@pytest.mark.parametrize("day", [26, 27, 28, 29])
def test_open_is_two_pm_today_monday_to_thursday(day: int) -> None:
    assert pretend_moment(HoursMode.OPEN, at(day, 5, 29)) == at(day, 14)


@pytest.mark.parametrize("day", [30, 31])
def test_open_rolls_friday_and_saturday_forward_to_monday(day: int) -> None:
    assert pretend_moment(HoursMode.OPEN, at(day, 9)) == datetime(2026, 11, 2, 14, tzinfo=CITY_TZ)


def test_open_rolls_sunday_forward_to_the_next_day() -> None:
    assert pretend_moment(HoursMode.OPEN, at(25, 23)) == at(26, 14)


@pytest.mark.parametrize("now", [at(26, 5), at(26, 14), at(31, 23, 59)])
def test_closed_is_eight_pm_today(now: datetime) -> None:
    assert pretend_moment(HoursMode.CLOSED, now) == now.replace(hour=20, minute=0)


def test_the_pretend_clock_keeps_ticking() -> None:
    real = [at(26, 5, 29)]
    clock = pretend_clock(lambda: real[0], HoursMode.OPEN)
    assert clock() == at(26, 14)
    real[0] += timedelta(minutes=3)
    assert clock() == at(26, 14, 3)


def test_real_hours_return_the_same_clock() -> None:
    def real() -> datetime:
        return at(26, 5)

    assert pretend_clock(real, HoursMode.REAL) is real
