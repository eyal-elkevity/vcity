import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import assert_never

from vcity.knowledge.gap_log import GapLog
from vcity.knowledge.model import SearchResult, SearchStatus, SourceKind
from vcity.knowledge.relevance import Relevance, RelevanceClassifier
from vcity.knowledge.retrieval import Retrieval, Retriever

RELEVANCE_THRESHOLD = 0.53
_log = logging.getLogger(__name__)


class SearchUnavailableError(RuntimeError):
    pass


class CitySearch:
    def __init__(
        self,
        *,
        classifier: RelevanceClassifier,
        retriever: Retriever,
        gap_log: GapLog,
        clock: Callable[[], datetime],
        threshold: float = RELEVANCE_THRESHOLD,
    ) -> None:
        self._classifier = classifier
        self._retriever = retriever
        self._gap_log = gap_log
        self._clock = clock
        self._threshold = threshold

    async def search(
        self, query: str, kind: SourceKind | None = None, *, caller_id: str | None = None
    ) -> SearchResult:
        now = self._clock()
        try:
            async with asyncio.TaskGroup() as group:
                relevance = group.create_task(self._classifier.classify(query))
                retrieval = group.create_task(self._retriever.retrieve(query, kind, now))
        except ExceptionGroup as failure:
            raise SearchUnavailableError("knowledge search failed") from failure
        result = self._decide(
            relevance.result(), retrieval.result(), gated=kind is not SourceKind.EVENTS
        )
        if result.status is not SearchStatus.MATCH:
            self._record_gap(now, query, kind, result.status, caller_id)
        return result

    def _record_gap(
        self,
        at: datetime,
        query: str,
        kind: SourceKind | None,
        status: SearchStatus,
        caller_id: str | None,
    ) -> None:
        try:
            self._gap_log.record(at=at, query=query, kind=kind, status=status, caller_id=caller_id)
        except OSError:
            _log.warning("could not record search gap", exc_info=True)

    def _decide(self, relevance: Relevance, retrieval: Retrieval, *, gated: bool) -> SearchResult:
        match relevance:
            case Relevance.NOT_CITY:
                return SearchResult(SearchStatus.OFF_TOPIC, ())
            case Relevance.CITY | Relevance.UNCLEAR:
                too_weak = gated and retrieval.best_similarity < self._threshold
                if not retrieval.chunks or too_weak:
                    return SearchResult(SearchStatus.NO_MATCH, ())
                return SearchResult(SearchStatus.MATCH, retrieval.chunks)
            case _:
                assert_never(relevance)
