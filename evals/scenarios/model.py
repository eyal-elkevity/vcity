from datetime import datetime
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from vcity.domain.departments import Department
from vcity.domain.hours import TicketReason

CITY_TZ = ZoneInfo("America/Los_Angeles")


class Outcome(StrEnum):
    TRANSFER = "transfer"
    TICKET = "ticket"
    NONE = "none"


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tools: tuple[str, ...] = ()
    not_tools: tuple[str, ...] = ()
    outcome: Outcome | None = None
    department: Department | None = None
    ticket_reason: TicketReason | None = None
    mentions: tuple[str, ...] = ()
    never_says: tuple[str, ...] = ()
    route_mentions: tuple[str, ...] = ()
    abstains: bool = False


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    ask: int = Field(ge=1, le=6)
    clock: datetime
    caller: tuple[str, ...] = Field(min_length=1)
    expect: Expect
    reference: str
    known_issue: str | None = None

    @field_validator("clock")
    @classmethod
    def _city_time(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=CITY_TZ)


def load_scenarios(path: Path) -> list[Scenario]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = [Scenario.model_validate(entry) for entry in raw]
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario ids must be unique")
    return scenarios
