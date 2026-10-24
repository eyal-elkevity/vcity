from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from vcity.domain.departments import Department

REFERENCE_DIGITS = 6


class Priority(IntEnum):
    # Linear's priority scale: 0 is unset and 1 is the most urgent.
    NONE = 0
    URGENT = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


@dataclass(frozen=True, slots=True)
class ServiceRequest:
    call_id: str
    department: Department
    summary: str
    location: str | None
    callback_number: str | None


@dataclass(frozen=True, slots=True)
class TicketRef:
    reference: str
    url: str | None

    @property
    def queued(self) -> bool:
        return self.url is None


def reference_code(ticket_id: UUID) -> str:
    return f"{ticket_id.int % 10**REFERENCE_DIGITS:0{REFERENCE_DIGITS}d}"
