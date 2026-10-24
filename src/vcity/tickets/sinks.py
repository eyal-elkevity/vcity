from typing import Protocol

from vcity.domain.tickets import TicketRef
from vcity.tickets.drafts import TicketDraft


class TicketSinkError(RuntimeError):
    pass


class TicketSink(Protocol):
    async def create(self, draft: TicketDraft) -> TicketRef: ...


class MockSink:
    def __init__(self) -> None:
        self.created: list[TicketDraft] = []

    async def create(self, draft: TicketDraft) -> TicketRef:
        self.created.append(draft)
        return TicketRef(draft.reference, url=f"mock://tickets/{draft.id}")
