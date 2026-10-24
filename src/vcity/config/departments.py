import re
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from vcity.domain.departments import Department, DepartmentInfo, OpenWindow, Weekday

DEFAULT_DIRECTORY_PATH = Path(__file__).resolve().parents[3] / "data" / "departments.yaml"
E164 = r"^\+[1-9]\d{7,14}$"

type ClockTime = Annotated[str, StringConstraints(pattern=r"^(([01]\d|2[0-3]):[0-5]\d|24:00)$")]
type DayName = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
type PhoneNumber = Annotated[str, StringConstraints(pattern=E164)]


class DepartmentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    public_phone: PhoneNumber
    schedule: str
    closes_on_holidays: bool = True
    source_url: Annotated[str, StringConstraints(pattern=r"^https://")]


class DirectoryFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schedules: dict[str, Annotated[dict[DayName, tuple[ClockTime, ClockTime]], Field(min_length=1)]]
    departments: dict[Department, DepartmentEntry]

    @model_validator(mode="after")
    def _complete_and_consistent(self) -> Self:
        missing = sorted(set(Department) - self.departments.keys())
        if missing:
            raise ValueError(f"missing departments: {missing}")
        unknown = sorted({e.schedule for e in self.departments.values()} - self.schedules.keys())
        if unknown:
            raise ValueError(f"unknown schedules: {unknown}")
        return self


def load_directory(path: Path, transfer_to: str) -> Mapping[Department, DepartmentInfo]:
    if not re.fullmatch(E164, transfer_to):
        raise ValueError(f"transfer number must be E.164, got {transfer_to!r}")
    parsed = DirectoryFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    weeks = {name: _week(days) for name, days in parsed.schedules.items()}
    return {
        department: DepartmentInfo(
            department=department,
            name=entry.name,
            description=entry.description,
            public_phone=entry.public_phone,
            transfer_to=transfer_to,
            hours=weeks[entry.schedule],
            closes_on_holidays=entry.closes_on_holidays,
            source_url=entry.source_url,
        )
        for department, entry in parsed.departments.items()
    }


def _week(days: dict[DayName, tuple[ClockTime, ClockTime]]) -> Mapping[Weekday, OpenWindow]:
    return MappingProxyType(
        {
            Weekday[day.upper()]: OpenWindow(_clock_offset(opens), _clock_offset(closes))
            for day, (opens, closes) in days.items()
        }
    )


def _clock_offset(clock: ClockTime) -> timedelta:
    hours, minutes = clock.split(":")
    return timedelta(hours=int(hours), minutes=int(minutes))
