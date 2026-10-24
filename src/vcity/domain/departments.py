from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import IntEnum, StrEnum

FULL_DAY = timedelta(hours=24)


class Department(StrEnum):
    PUBLIC_WORKS = "public_works"
    MAINTENANCE_SERVICES = "maintenance_services"
    UTILITIES = "utilities"
    CODE_COMPLIANCE = "code_compliance"
    ANIMAL_SERVICES = "animal_services"
    BUILDING_PLANNING = "building_planning"
    CITY_CLERK = "city_clerk"
    FINANCE = "finance"
    POLICE_NON_EMERGENCY = "police_non_emergency"


class Weekday(IntEnum):
    MON = 0
    TUE = 1
    WED = 2
    THU = 3
    FRI = 4
    SAT = 5
    SUN = 6


@dataclass(frozen=True, slots=True)
class OpenWindow:
    start: timedelta
    end: timedelta

    def __post_init__(self) -> None:
        if not timedelta(0) <= self.start < self.end <= FULL_DAY:
            raise ValueError(f"invalid opening window {self.start}-{self.end}")

    def contains(self, offset: timedelta) -> bool:
        return self.start <= offset < self.end

    @property
    def runs_to_midnight(self) -> bool:
        return self.end == FULL_DAY


@dataclass(frozen=True, slots=True)
class DepartmentInfo:
    department: Department
    name: str
    description: str
    public_phone: str
    transfer_to: str
    hours: Mapping[Weekday, OpenWindow]
    source_url: str
