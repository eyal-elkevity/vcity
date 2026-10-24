import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from vcity.domain.tickets import Priority
from vcity.knowledge.embeddings import Vector
from vcity.knowledge.model import SearchStatus, SourceKind
from vcity.tickets.drafts import GAP_TITLE_PREFIX, TicketDraft
from vcity.tickets.gaps import (
    ANONYMOUS,
    Ask,
    GapBacklog,
    GapPolicy,
    GapTicket,
    ask_line,
    closest,
    describe,
    parse_ask,
    priority_for,
)
from vcity.tickets.sinks import TicketSinkError

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
LA = ZoneInfo("America/Los_Angeles")
POLICY = GapPolicy()


def gap(gap_id: str, question: str) -> GapTicket:
    return GapTicket(gap_id, f"COR-{gap_id}", question, NOW)


@pytest.mark.parametrize(
    ("asks", "priority"),
    [
        (0, Priority.NONE),
        (1, Priority.LOW),
        (2, Priority.LOW),
        (3, Priority.MEDIUM),
        (9, Priority.MEDIUM),
        (10, Priority.HIGH),
    ],
)
def test_priority_rises_with_recent_asks(asks: int, priority: Priority) -> None:
    assert priority_for(asks, POLICY) is priority


def test_ask_lines_round_trip() -> None:
    line = ask_line("a1b2c3d4e5f6a7b8", "When will Target open?")
    assert parse_ask(line, NOW) == Ask("a1b2c3d4e5f6a7b8", "When will Target open?", NOW)
    assert parse_ask("a human comment", NOW) is None


def test_closest_respects_the_similarity_threshold() -> None:
    target, costco = gap("1", "target"), gap("2", "costco")
    candidates = [(target, [1.0, 0.0]), (costco, [0.0, 1.0])]
    assert closest([0.9, 0.1], candidates, threshold=0.48) == target
    assert closest([0.7, 0.7], candidates, threshold=0.8) is None
    assert closest([1.0, 0.0], [], threshold=0.48) is None


def test_description_summarises_asks_in_city_time() -> None:
    asks = [
        Ask("caller-a", "first phrasing", NOW - timedelta(days=9)),
        Ask("caller-b", "second phrasing", NOW - timedelta(days=1)),
        Ask(ANONYMOUS, "third phrasing", NOW),
    ]
    text = describe(asks, NOW.astimezone(LA), POLICY)
    assert "**Total asks:** 3" in text
    assert "**Asks in the last 7 days:** 2" in text
    assert "**Distinct callers:** 2" in text
    assert "**First seen:** 2026-10-15 05:00 PDT" in text
    assert "**Last seen:** 2026-10-24 05:00 PDT" in text
    assert text.endswith("- third phrasing\n\n- second phrasing\n\n- first phrasing")


class KeywordEmbedder:
    @property
    def model(self) -> str:
        return "keywords@2"

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [await self.embed_query(text) for text in texts]

    async def embed_query(self, text: str) -> Vector:
        lowered = text.lower()
        return [float("target" in lowered), float("costco" in lowered) + 0.01]


class FakeBoard:
    def __init__(self) -> None:
        self.gaps: dict[str, GapTicket] = {}
        self.comments: dict[str, list[Ask]] = {}
        self.updates: dict[str, tuple[str, Priority]] = {}
        self.fail = False

    async def open_gaps(self) -> list[GapTicket]:
        if self.fail:
            raise TicketSinkError("down")
        return list(self.gaps.values())

    async def file_gap(self, draft: TicketDraft) -> GapTicket:
        created = gap(str(len(self.gaps) + 1), draft.title.removeprefix(GAP_TITLE_PREFIX))
        self.gaps[created.id] = created
        self.comments[created.id] = []
        return created

    async def add_ask(self, gap: GapTicket, body: str) -> None:
        parsed = parse_ask(body, NOW)
        assert parsed is not None
        self.comments[gap.id].append(parsed)

    async def asks(self, gap: GapTicket) -> list[Ask]:
        return self.comments[gap.id]

    async def update_gap(self, gap: GapTicket, description: str, priority: Priority) -> None:
        self.updates[gap.id] = (description, priority)


def backlog(board: FakeBoard) -> GapBacklog:
    return GapBacklog(board, KeywordEmbedder(), salt="salt", clock=lambda: NOW)


async def test_paraphrases_join_one_gap_and_new_topics_open_another() -> None:
    board = FakeBoard()
    log = backlog(board)
    first = await log.file("When will the new Target open?", "caller-a")
    again = await log.file("is target opening soon", "caller-b")
    other = await log.file("Costco hours?", "caller-a")
    assert first == again
    assert other != first
    assert [ask.question for ask in board.comments[first.id]] == [
        "When will the new Target open?",
        "is target opening soon",
    ]
    assert board.updates[first.id][1] is Priority.LOW
    assert "**Distinct callers:** 2" in board.updates[first.id][0]


async def test_third_recent_ask_raises_priority_to_medium() -> None:
    board = FakeBoard()
    log = backlog(board)
    for caller in ("a", "b", "c"):
        filed = await log.file("target opening?", caller)
    assert board.updates[filed.id][1] is Priority.MEDIUM


async def test_record_queues_only_no_match_with_scrubbed_query_and_hashed_caller() -> None:
    board = FakeBoard()
    log = backlog(board)
    log.record(
        at=NOW,
        query="target opening? call 951-555-0142",
        kind=None,
        status=SearchStatus.NO_MATCH,
        caller_id="+19515550142",
    )
    log.record(
        at=NOW,
        query="tell me a joke",
        kind=SourceKind.SITE,
        status=SearchStatus.OFF_TOPIC,
        caller_id=None,
    )
    worker = asyncio.create_task(log.run())
    await log.drain()
    worker.cancel()
    (only,) = board.gaps.values()
    (ask,) = board.comments[only.id]
    assert ask.question == "target opening? call [phone]"
    assert ask.caller not in {"+19515550142", ANONYMOUS}
    assert len(ask.caller) == 16


async def test_board_failures_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    board = FakeBoard()
    board.fail = True
    log = backlog(board)
    log.record(at=NOW, query="target?", kind=None, status=SearchStatus.NO_MATCH, caller_id=None)
    worker = asyncio.create_task(log.run())
    await log.drain()
    worker.cancel()
    assert board.gaps == {}
    assert "could not file gap" in caplog.text
