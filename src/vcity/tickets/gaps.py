import asyncio
import logging
import math
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from vcity.domain.tickets import Priority
from vcity.knowledge.embeddings import Embedder, Vector
from vcity.knowledge.gap_log import hash_caller, scrub
from vcity.knowledge.model import SearchStatus, SourceKind
from vcity.tickets.drafts import TicketDraft, gap_ticket

GAP_LABEL = "gap"
ANONYMOUS = "anonymous"
QUEUE_LIMIT = 1000
_ASK_LINE = re.compile(r"^\+1 from caller (?P<caller>\S+): (?P<question>.+)$", re.DOTALL)
_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GapTicket:
    id: str
    reference: str
    question: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Ask:
    caller: str
    question: str
    at: datetime


class GapBoard(Protocol):
    async def open_gaps(self) -> list[GapTicket]: ...

    async def file_gap(self, draft: TicketDraft) -> GapTicket: ...

    async def add_ask(self, gap: GapTicket, body: str) -> None: ...

    async def asks(self, gap: GapTicket) -> list[Ask]: ...

    async def update_gap(self, gap: GapTicket, description: str, priority: Priority) -> None: ...


@dataclass(frozen=True, slots=True)
class GapPolicy:
    similarity: float = 0.48
    window: timedelta = timedelta(days=7)
    thresholds: tuple[tuple[int, Priority], ...] = (
        (10, Priority.HIGH),
        (3, Priority.MEDIUM),
        (1, Priority.LOW),
    )
    samples: int = 3


def ask_line(caller: str, question: str) -> str:
    return f"+1 from caller {caller}: {question}"


def parse_ask(body: str, at: datetime) -> Ask | None:
    match = _ASK_LINE.match(body)
    return Ask(match["caller"], match["question"], at) if match else None


def priority_for(recent_asks: int, policy: GapPolicy) -> Priority:
    return next(
        (priority for minimum, priority in policy.thresholds if recent_asks >= minimum),
        Priority.NONE,
    )


def cosine(a: Vector, b: Vector) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def closest(
    vector: Vector, candidates: Sequence[tuple[GapTicket, Vector]], threshold: float
) -> GapTicket | None:
    scored = [(cosine(vector, other), gap) for gap, other in candidates]
    best = max(scored, key=lambda pair: pair[0], default=None)
    return best[1] if best is not None and best[0] >= threshold else None


def recent_count(asks: Sequence[Ask], now: datetime, policy: GapPolicy) -> int:
    return sum(now - ask.at <= policy.window for ask in asks)


def describe(asks: Sequence[Ask], now: datetime, policy: GapPolicy) -> str:
    ordered = sorted(asks, key=lambda ask: ask.at)
    callers = {ask.caller for ask in asks if ask.caller != ANONYMOUS}
    lines = [
        "Callers asked this and the city's records had no answer.",
        f"**Total asks:** {len(asks)}",
        f"**Asks in the last {policy.window.days} days:** {recent_count(asks, now, policy)}",
        f"**Distinct callers:** {len(callers)}",
        f"**First seen:** {ordered[0].at.astimezone(now.tzinfo):%Y-%m-%d %H:%M %Z}",
        f"**Last seen:** {ordered[-1].at.astimezone(now.tzinfo):%Y-%m-%d %H:%M %Z}",
        "**Recent phrasings:**",
        *(f"- {ask.question}" for ask in reversed(ordered[-policy.samples :])),
    ]
    return "\n\n".join(lines)


@dataclass(frozen=True, slots=True)
class _Miss:
    question: str
    caller: str


@dataclass(eq=False)
class GapBacklog:
    board: GapBoard
    embedder: Embedder
    salt: str = field(repr=False)
    clock: Callable[[], datetime]
    policy: GapPolicy = field(default_factory=GapPolicy)
    queue_limit: int = QUEUE_LIMIT
    _queue: asyncio.Queue[_Miss] = field(init=False)
    _vectors: dict[str, Vector] = field(default_factory=dict[str, Vector], init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        if not self.salt:
            raise ValueError("a gap backlog needs a non-empty salt to hash caller IDs")
        self._queue = asyncio.Queue(maxsize=self.queue_limit)

    def record(
        self,
        *,
        at: datetime,  # noqa: ARG002
        query: str,
        kind: SourceKind | None,  # noqa: ARG002
        status: SearchStatus,
        caller_id: str | None,
    ) -> None:
        if status is not SearchStatus.NO_MATCH:
            return
        caller = hash_caller(caller_id, self.salt) if caller_id else ANONYMOUS
        try:
            self._queue.put_nowait(_Miss(scrub(query), caller))
        except asyncio.QueueFull:
            _log.warning("gap queue full; dropping a miss from caller %s", caller)

    async def run(self) -> None:
        while True:
            miss = await self._queue.get()
            try:
                await self.file(miss.question, miss.caller)
            except Exception as error:  # noqa: BLE001
                _log.warning(
                    "could not file a gap from caller %s; dropping it: %s",
                    miss.caller,
                    type(error).__name__,
                )
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        await self._queue.join()

    async def file(self, question: str, caller: str) -> GapTicket:
        async with self._lock:
            vector = await self.embedder.embed_query(question)
            gaps = await self.board.open_gaps()
            candidates = [(gap, await self._vector(gap)) for gap in gaps]
            match = closest(vector, candidates, self.policy.similarity)
            if match is None:
                created = await self.board.file_gap(
                    gap_ticket(question, GAP_LABEL, ticket_id=uuid.uuid4())
                )
                self._vectors[created.id] = vector
                match = created
            await self.board.add_ask(match, ask_line(caller, question))
            asks = await self.board.asks(match)
            now = self.clock()
            await self.board.update_gap(
                match,
                describe(asks, now, self.policy),
                priority_for(recent_count(asks, now, self.policy), self.policy),
            )
            return match

    async def _vector(self, gap: GapTicket) -> Vector:
        if gap.id not in self._vectors:
            self._vectors[gap.id] = await self.embedder.embed_query(gap.question)
        return self._vectors[gap.id]
