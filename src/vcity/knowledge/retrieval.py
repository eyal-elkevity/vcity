from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from vcity.knowledge.embeddings import Embedder
from vcity.knowledge.index import KnowledgeIndex
from vcity.knowledge.model import Chunk, SourceKind


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    candidates: int = 20
    results: int = 5
    rrf_k: int = 60
    news_half_life: timedelta = timedelta(days=180)


@dataclass(frozen=True, slots=True)
class Retrieval:
    chunks: tuple[Chunk, ...]
    best_similarity: float


class Retriever(Protocol):
    async def retrieve(self, query: str, kind: SourceKind | None, now: datetime) -> Retrieval: ...


def reciprocal_rank_fusion(rankings: Iterable[Sequence[str]], k: int) -> dict[str, float]:
    scores: defaultdict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] += 1.0 / (k + rank)
    return dict(scores)


class HybridRetriever:
    def __init__(
        self,
        index: KnowledgeIndex,
        embedder: Embedder,
        policy: RetrievalPolicy = RetrievalPolicy(),  # noqa: B008
    ) -> None:
        if index.model != embedder.model:
            msg = f"index was built with {index.model!r} but the embedder is {embedder.model!r}"
            raise ValueError(msg)
        self._index = index
        self._embedder = embedder
        self._policy = policy

    async def retrieve(self, query: str, kind: SourceKind | None, now: datetime) -> Retrieval:
        kinds = [kind] if kind else list(SourceKind)
        limit = self._policy.candidates
        vector = await self._embedder.embed_query(query)
        lexical = [c for c in self._index.keyword(query, kinds, limit) if _is_current(c, now)]
        semantic = [
            (c, similarity)
            for c, similarity in self._index.semantic(vector, kinds, limit)
            if _is_current(c, now)
        ]
        fused = reciprocal_rank_fusion(
            [[c.cite_id for c in lexical], [c.cite_id for c, _ in semantic]], self._policy.rrf_k
        )
        candidates = {c.cite_id: c for c in lexical} | {c.cite_id: c for c, _ in semantic}
        ranked = sorted(
            candidates.values(),
            key=lambda c: fused[c.cite_id] * self._recency(c, now),
            reverse=True,
        )
        top = ranked[: self._policy.results]
        if kind is SourceKind.EVENTS:
            top.sort(key=lambda c: c.date or now)
        return Retrieval(
            chunks=tuple(top),
            best_similarity=max((similarity for _, similarity in semantic), default=0.0),
        )

    def _recency(self, chunk: Chunk, now: datetime) -> float:
        if chunk.kind is not SourceKind.NEWS or chunk.date is None:
            return 1.0
        age = max(now - chunk.date, timedelta())
        return 0.5 + 0.5 * 0.5 ** (age / self._policy.news_half_life)


def _is_current(chunk: Chunk, now: datetime) -> bool:
    if chunk.kind is not SourceKind.EVENTS:
        return True
    last_moment = chunk.ends_at or chunk.date
    return last_moment is None or last_moment >= now
