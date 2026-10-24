import asyncio
from pathlib import Path
from uuid import UUID

from vcity.domain.tickets import Priority, TicketRef
from vcity.tickets.drafts import TicketDraft
from vcity.tickets.outbox import OutboxSink, OutboxStore
from vcity.tickets.sinks import MockSink, TicketSinkError

DRAFT = TicketDraft(
    UUID(int=31), "[000031] Utilities: leak", "body", frozenset({"Utilities"}), Priority.NONE
)


class FlakySink:
    def __init__(self, failures: int, *, hang: bool = False) -> None:
        self.failures = failures
        self.hang = hang
        self.attempts = 0
        self.delivered = MockSink()

    async def create(self, draft: TicketDraft) -> TicketRef:
        self.attempts += 1
        if self.attempts <= self.failures:
            if self.hang:
                await asyncio.sleep(10)
            raise TicketSinkError("Linear is down")
        return await self.delivered.create(draft)


def test_store_round_trips_drafts(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    store.put(DRAFT)
    store.put(DRAFT)
    assert store.pending() == [DRAFT]
    store.remove(DRAFT.id)
    assert store.pending() == []


async def test_a_retry_that_succeeds_never_touches_the_outbox(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    sink = FlakySink(failures=1)
    ref = await OutboxSink(sink, store).create(DRAFT)
    assert not ref.queued
    assert sink.attempts == 2
    assert store.pending() == []


async def test_persistent_failure_queues_and_still_gives_a_reference(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    ref = await OutboxSink(FlakySink(failures=5), store).create(DRAFT)
    assert ref == TicketRef("000031", url=None)
    assert ref.queued
    assert store.pending() == [DRAFT]


async def test_a_hanging_sink_times_out_into_the_outbox(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    outbox = OutboxSink(FlakySink(failures=2, hang=True), store, timeout=0.01)
    assert (await outbox.create(DRAFT)).queued
    assert store.pending() == [DRAFT]


async def test_flush_delivers_queued_tickets_once_the_sink_recovers(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    sink = FlakySink(failures=2)
    outbox = OutboxSink(sink, store)
    await outbox.create(DRAFT)
    assert await outbox.flush() == 1
    assert sink.delivered.created == [DRAFT]
    assert store.pending() == []


async def test_flush_keeps_tickets_the_sink_still_rejects(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    outbox = OutboxSink(FlakySink(failures=10), store)
    await outbox.create(DRAFT)
    assert await outbox.flush() == 0
    assert store.pending() == [DRAFT]
