from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vcity.knowledge.embeddings import Embedder
from vcity.knowledge.index import KnowledgeIndex, write_index
from vcity.knowledge.model import Chunk, SourceKind
from vcity.knowledge.retrieval import HybridRetriever, RetrievalPolicy, reciprocal_rank_fusion

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def test_rrf_rewards_agreement_between_rankings() -> None:
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a"]], k=60)
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62 + 1 / 61)
    assert scores["c"] == pytest.approx(1 / 63)


async def build(tmp_path: Path, embedder: Embedder, chunks: list[Chunk]) -> KnowledgeIndex:
    path = tmp_path / "index.sqlite"
    vectors = await embedder.embed_documents([c.embedding_text for c in chunks])
    write_index(path, chunks, vectors, model=embedder.model)
    return KnowledgeIndex.open(path)


def chunk(cite_id: str, kind: SourceKind, text: str, **dates: datetime) -> Chunk:
    return Chunk(cite_id, kind, "Corona", text, "https://example.test", **dates)


async def test_past_events_are_dropped(tmp_path: Path, embedder: Embedder) -> None:
    index = await build(
        tmp_path,
        embedder,
        [
            chunk(
                "event:past", SourceKind.EVENTS, "dog adoption fair", date=NOW - timedelta(days=2)
            ),
            chunk(
                "event:ongoing",
                SourceKind.EVENTS,
                "dog adoption fair",
                date=NOW - timedelta(hours=1),
                ends_at=NOW + timedelta(hours=1),
            ),
            chunk(
                "event:next", SourceKind.EVENTS, "dog adoption fair", date=NOW + timedelta(days=3)
            ),
        ],
    )
    result = await HybridRetriever(index, embedder).retrieve("dog adoption fair", None, NOW)
    assert sorted(c.cite_id for c in result.chunks) == ["event:next", "event:ongoing"]


async def test_recent_news_outranks_identical_old_news(tmp_path: Path, embedder: Embedder) -> None:
    index = await build(
        tmp_path,
        embedder,
        [
            chunk(
                "news:old",
                SourceKind.NEWS,
                "street closure downtown",
                date=NOW - timedelta(days=720),
            ),
            chunk(
                "news:new", SourceKind.NEWS, "street closure downtown", date=NOW - timedelta(days=3)
            ),
        ],
    )
    result = await HybridRetriever(index, embedder).retrieve("street closure", None, NOW)
    assert [c.cite_id for c in result.chunks] == ["news:new", "news:old"]


async def test_kind_filter_and_result_cap(tmp_path: Path, embedder: Embedder) -> None:
    chunks = [chunk(f"code:{n}", SourceKind.CODE, f"parking rule {n}") for n in range(8)]
    chunks.append(chunk("site:parking#intro", SourceKind.SITE, "parking rule"))
    index = await build(tmp_path, embedder, chunks)
    retriever = HybridRetriever(index, embedder, RetrievalPolicy(results=3))
    result = await retriever.retrieve("parking rule", SourceKind.CODE, NOW)
    assert len(result.chunks) == 3
    assert {c.kind for c in result.chunks} == {SourceKind.CODE}


async def test_best_similarity_reflects_closest_vector(tmp_path: Path, embedder: Embedder) -> None:
    index = await build(tmp_path, embedder, [chunk("code:1", SourceKind.CODE, "chickens")])
    retriever = HybridRetriever(index, embedder)
    close = await retriever.retrieve("Corona\n\nchickens", None, NOW)
    far = await retriever.retrieve("quantum chromodynamics lecture", None, NOW)
    assert close.best_similarity == pytest.approx(1.0, abs=1e-6)
    assert far.best_similarity < 0.5


async def test_event_results_read_in_date_order(tmp_path: Path, embedder: Embedder) -> None:
    index = await build(
        tmp_path,
        embedder,
        [
            chunk(
                "event:later", SourceKind.EVENTS, "dog fair dog fair", date=NOW + timedelta(days=9)
            ),
            chunk("event:sooner", SourceKind.EVENTS, "dog fair", date=NOW + timedelta(days=1)),
        ],
    )
    result = await HybridRetriever(index, embedder).retrieve("dog fair", SourceKind.EVENTS, NOW)
    assert [c.cite_id for c in result.chunks] == ["event:sooner", "event:later"]
