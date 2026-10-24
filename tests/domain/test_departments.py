from datetime import timedelta

import pytest

from vcity.domain.departments import FULL_DAY, OpenWindow


def hours(value: float) -> timedelta:
    return timedelta(hours=value)


def test_window_is_half_open() -> None:
    window = OpenWindow(hours(7), hours(18))
    assert window.contains(hours(7))
    assert window.contains(hours(17.99))
    assert not window.contains(hours(18))
    assert not window.contains(hours(6.99))


def test_window_reaching_midnight_is_marked() -> None:
    assert OpenWindow(timedelta(0), FULL_DAY).runs_to_midnight
    assert not OpenWindow(hours(7), hours(18)).runs_to_midnight


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (hours(18), hours(7)),
        (hours(9), hours(9)),
        (hours(-1), hours(5)),
        (hours(8), hours(25)),
    ],
)
def test_invalid_window_is_rejected(start: timedelta, end: timedelta) -> None:
    with pytest.raises(ValueError, match="invalid opening window"):
        OpenWindow(start, end)
