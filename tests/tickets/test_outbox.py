import asyncio
import sqlite3
from contextlib import closing, suppress
from pathlib import Path
from uuid import UUID

import pytest

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


class RaisingOnceSink:
    def __init__(self) -> None:
        self.attempts = 0
        self.delivered = MockSink()
        self.succeeded = asyncio.Event()

    async def create(self, draft: TicketDraft) -> TicketRef:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("unexpected")
        ref = await self.delivered.create(draft)
        self.succeeded.set()
        return ref


class BrokenSink:
    async def create(self, draft: TicketDraft) -> TicketRef:
        raise RuntimeError(draft.reference)


def test_store_round_trips_drafts(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    store.put(DRAFT)
    store.put(DRAFT)
    assert store.pending() == [DRAFT]
    store.remove(DRAFT.id)
    assert store.pending() == []


def test_pending_skips_undecodable_rows(tmp_path: Path) -> None:
    path = tmp_path / "outbox.sqlite"
    store = OutboxStore(path)
    store.put(DRAFT)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("insert into pending values (?, ?)", ("corrupt", "not json"))
    assert store.pending() == [DRAFT]


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


async def test_an_unexpected_sink_error_queues_the_draft(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    ref = await OutboxSink(BrokenSink(), store).create(DRAFT)
    assert ref.queued
    assert store.pending() == [DRAFT]


async def test_a_failed_queue_write_surfaces_as_a_sink_error(tmp_path: Path) -> None:
    path = tmp_path / "outbox.sqlite"
    store = OutboxStore(path)
    path.chmod(0o444)
    outbox = OutboxSink(FlakySink(failures=5), store)
    with pytest.raises(TicketSinkError):
        await outbox.create(DRAFT)


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


async def test_run_keeps_draining_after_an_unexpected_flush_error(tmp_path: Path) -> None:
    store = OutboxStore(tmp_path / "outbox.sqlite")
    store.put(DRAFT)
    sink = RaisingOnceSink()
    task = asyncio.create_task(OutboxSink(sink, store).run(interval=0))
    try:
        async with asyncio.timeout(2):
            await sink.succeeded.wait()
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    assert sink.delivered.created == [DRAFT]
    assert store.pending() == []
