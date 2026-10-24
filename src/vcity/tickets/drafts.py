from dataclasses import dataclass
from datetime import datetime
from typing import assert_never
from uuid import UUID

from vcity.domain.hours import TicketReason
from vcity.domain.tickets import Priority, ServiceRequest, reference_code

MAX_TITLE_SUMMARY = 80
GAP_TITLE_PREFIX = "Unanswered: "


@dataclass(frozen=True, slots=True)
class TicketDraft:
    id: UUID
    title: str
    description: str
    labels: frozenset[str]
    priority: Priority

    @property
    def reference(self) -> str:
        return reference_code(self.id)


def service_ticket(
    request: ServiceRequest,
    reason: TicketReason,
    department_name: str,
    *,
    ticket_id: UUID,
    next_open: datetime | None,
) -> TicketDraft:
    lines = [
        f"**Call:** {request.call_id}",
        f"**Why a ticket:** {_reason_text(reason)}",
        f"**Summary:** {request.summary}",
        f"**Location:** {request.location or 'not given'}",
        f"**Callback:** {request.callback_number or 'none (browser call)'}",
    ]
    if next_open is not None:
        lines.append(f"**Office reopens:** {next_open:%A, %B %-d at %-I:%M %p}")
    return TicketDraft(
        id=ticket_id,
        title=f"[{reference_code(ticket_id)}] {department_name}: {_shorten(request.summary)}",
        description="\n\n".join(lines),
        labels=frozenset({department_name}),
        priority=Priority.NONE,
    )


def gap_ticket(question: str, label: str, *, ticket_id: UUID) -> TicketDraft:
    return TicketDraft(
        id=ticket_id,
        title=f"{GAP_TITLE_PREFIX}{_shorten(question)}",
        description=(
            "Callers asked this and the city's records had no answer. "
            "Each further ask is added as a comment."
        ),
        labels=frozenset({label}),
        priority=Priority.LOW,
    )


def _reason_text(reason: TicketReason) -> str:
    match reason:
        case TicketReason.AFTER_HOURS:
            return "called outside office hours"
        case TicketReason.HOLIDAY:
            return "called on a city holiday"
        case TicketReason.CLOSING_SOON:
            return "called just before the office closed"
        case TicketReason.TRANSFER_FAILED:
            return "the transfer was not answered"
        case TicketReason.CALLER_REQUESTED:
            return "the caller asked for a ticket"
        case _:
            assert_never(reason)


def _shorten(text: str) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= MAX_TITLE_SUMMARY else f"{flat[: MAX_TITLE_SUMMARY - 1]}…"
