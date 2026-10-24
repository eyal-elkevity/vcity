import asyncio
import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError

from vcity.domain.tickets import Priority, TicketRef
from vcity.tickets.drafts import TicketDraft
from vcity.tickets.sinks import TicketSink

_log = logging.getLogger(__name__)


class OutboxStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with closing(sqlite3.connect(path)) as db, db:
            db.execute(
                "create table if not exists pending(id text primary key, draft text not null)"
            )

    def put(self, draft: TicketDraft) -> None:
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute(
                "insert or replace into pending values (?, ?)", (str(draft.id), _dump(draft))
            )

    def pending(self) -> list[TicketDraft]:
        drafts: list[TicketDraft] = []
        with closing(sqlite3.connect(self._path)) as db:
            for row_id, raw in db.execute("select id, draft from pending"):
                try:
                    drafts.append(_load(str(raw)))
                except ValidationError:
                    _log.warning("skipping undecodable outbox row %s", row_id)
        return drafts

    def remove(self, ticket_id: UUID) -> None:
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("delete from pending where id = ?", (str(ticket_id),))


class OutboxSink:
    def __init__(
        self, inner: TicketSink, store: OutboxStore, *, timeout: float = 3.0, attempts: int = 2
    ) -> None:
        self._inner = inner
        self._store = store
        self._timeout = timeout
        self._attempts = attempts
        self._lock = asyncio.Lock()

    async def create(self, draft: TicketDraft) -> TicketRef:
        for attempt in range(1, self._attempts + 1):
            try:
                async with asyncio.timeout(self._timeout):
                    return await self._inner.create(draft)
            except Exception as error:  # noqa: BLE001
                _log.warning(
                    "ticket %s attempt %d failed: %s",
                    draft.reference,
                    attempt,
                    type(error).__name__,
                )
        self._store.put(draft)
        return TicketRef(draft.reference, url=None)

    async def flush(self) -> int:
        async with self._lock:
            sent = 0
            for draft in self._store.pending():
                try:
                    async with asyncio.timeout(self._timeout):
                        await self._inner.create(draft)
                except Exception as error:  # noqa: BLE001
                    _log.warning(
                        "outbox still cannot send %s: %s",
                        draft.reference,
                        type(error).__name__,
                    )
                    continue
                self._store.remove(draft.id)
                sent += 1
            return sent

    async def run(self, interval: float = 60.0) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await self.flush()
            except Exception as error:  # noqa: BLE001
                _log.warning("outbox flush failed: %s", type(error).__name__)


class _StoredDraft(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    description: str
    labels: frozenset[str]
    priority: Priority


def _dump(draft: TicketDraft) -> str:
    return _StoredDraft(
        id=draft.id,
        title=draft.title,
        description=draft.description,
        labels=draft.labels,
        priority=draft.priority,
    ).model_dump_json()


def _load(raw: str) -> TicketDraft:
    stored = _StoredDraft.model_validate_json(raw)
    return TicketDraft(
        id=stored.id,
        title=stored.title,
        description=stored.description,
        labels=stored.labels,
        priority=stored.priority,
    )
