import asyncio
from datetime import UTC, datetime

import pytest

from vcity.knowledge.model import Chunk, SearchStatus, SourceKind
from vcity.knowledge.relevance import Relevance
from vcity.knowledge.retrieval import Retrieval
from vcity.knowledge.search import CitySearch

NOW = datetime(2026, 10, 24, 18, 30, tzinfo=UTC)
CHICKENS = Chunk("code:6.20.040", SourceKind.CODE, "Code", "Up to ten chickens.", "u")


class FixedClassifier:
    def __init__(self, verdict: Relevance) -> None:
        self.verdict = verdict
        self.started = asyncio.Event()

    async def classify(self, query: str) -> Relevance:  # noqa: ARG002
        self.started.set()
        return self.verdict


class FixedRetriever:
    def __init__(self, retrieval: Retrieval, classifier: FixedClassifier) -> None:
        self.retrieval = retrieval
        self.classifier = classifier
        self.calls: list[tuple[str, SourceKind | None, datetime]] = []

    async def retrieve(self, query: str, kind: SourceKind | None, now: datetime) -> Retrieval:
        self.calls.append((query, kind, now))
        await asyncio.wait_for(self.classifier.started.wait(), timeout=1)
        return self.retrieval


class RecordingGapLog:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    def record(self, **entry: object) -> None:
        self.entries.append(entry)


def make_search(
    verdict: Relevance, retrieval: Retrieval
) -> tuple[CitySearch, FixedRetriever, RecordingGapLog]:
    classifier = FixedClassifier(verdict)
    retriever = FixedRetriever(retrieval, classifier)
    gaps = RecordingGapLog()
    search = CitySearch(
        classifier=classifier, retriever=retriever, gap_log=gaps, clock=lambda: NOW, threshold=0.4
    )
    return search, retriever, gaps


@pytest.mark.parametrize("verdict", [Relevance.CITY, Relevance.UNCLEAR])
async def test_close_match_is_returned_without_logging(verdict: Relevance) -> None:
    search, retriever, gaps = make_search(verdict, Retrieval((CHICKENS,), best_similarity=0.7))
    result = await search.search("how many chickens", SourceKind.CODE, caller_id="+1951")
    assert result.status is SearchStatus.MATCH
    assert result.chunks == (CHICKENS,)
    assert retriever.calls == [("how many chickens", SourceKind.CODE, NOW)]
    assert gaps.entries == []


async def test_weak_match_abstains_and_logs_the_gap() -> None:
    search, _, gaps = make_search(Relevance.CITY, Retrieval((CHICKENS,), best_similarity=0.39))
    result = await search.search("when does the new Target open", caller_id="+1951")
    assert result.status is SearchStatus.NO_MATCH
    assert result.chunks == ()
    assert gaps.entries == [
        {
            "at": NOW,
            "query": "when does the new Target open",
            "kind": None,
            "status": SearchStatus.NO_MATCH,
            "caller_id": "+1951",
        }
    ]


async def test_off_topic_discards_retrieval_and_logs() -> None:
    search, _, gaps = make_search(Relevance.NOT_CITY, Retrieval((CHICKENS,), best_similarity=0.9))
    result = await search.search("tell me a joke")
    assert result.status is SearchStatus.OFF_TOPIC
    assert result.chunks == ()
    assert [entry["status"] for entry in gaps.entries] == [SearchStatus.OFF_TOPIC]


async def test_event_browsing_is_not_gated() -> None:
    fair = Chunk("event:fair", SourceKind.EVENTS, "Events", "Dog adoption fair.", "u")
    search, _, gaps = make_search(Relevance.CITY, Retrieval((fair,), best_similarity=0.2))
    result = await search.search("what's happening this weekend", SourceKind.EVENTS)
    assert result.status is SearchStatus.MATCH
    assert result.chunks == (fair,)
    assert gaps.entries == []


async def test_empty_retrieval_is_no_match() -> None:
    search, _, _ = make_search(Relevance.CITY, Retrieval((), best_similarity=0.0))
    assert (await search.search("anything")).status is SearchStatus.NO_MATCH
