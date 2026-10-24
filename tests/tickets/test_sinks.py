from uuid import UUID

from vcity.domain.tickets import Priority
from vcity.tickets.drafts import TicketDraft
from vcity.tickets.sinks import MockSink

DRAFT = TicketDraft(
    UUID(int=7), "[000007] Finance: refund", "body", frozenset({"Finance"}), Priority.NONE
)


async def test_mock_sink_records_drafts_and_returns_their_reference() -> None:
    sink = MockSink()
    ref = await sink.create(DRAFT)
    assert sink.created == [DRAFT]
    assert ref.reference == "000007"
    assert not ref.queued
