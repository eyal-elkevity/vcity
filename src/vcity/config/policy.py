from datetime import timedelta
from zoneinfo import ZoneInfo

import holidays

from vcity.domain.hours import HoursPolicy


def corona_policy() -> HoursPolicy:
    return HoursPolicy(
        tz=ZoneInfo("America/Los_Angeles"),
        closing_soon=timedelta(minutes=10),
        holidays=holidays.country_holidays("US", subdiv="CA"),
    )
