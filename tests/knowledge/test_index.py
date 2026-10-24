from datetime import UTC, datetime
from pathlib import Path

import pytest

from vcity.knowledge.embeddings import Embedder
from vcity.knowledge.index import KnowledgeIndex, write_index
from vcity.knowledge.model import Chunk, SourceKind

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
ALL_KINDS = list(SourceKind)
CHUNKS = [
    Chunk("code:6.20.040", SourceKind.CODE, "Code > Chickens", "Up to ten chickens per lot.", "u1"),
    Chunk("site:animals#intro", SourceKind.SITE, "Site > Animals", "Adopt a dog today.", "u2"),
    Chunk("news:dog-park#intro", SourceKind.NEWS, "News > Dog park", "Dog park opens.", "u3", NOW),
]


@pytest.fixture
async def index(tmp_path: Path, embedder: Embedder) -> KnowledgeIndex:
    path = tmp_path / "index.sqlite"
    vectors = await embedder.embed_documents([c.embedding_text for c in CHUNKS])
    write_index(path, CHUNKS, vectors, model=embedder.model)
    return KnowledgeIndex.open(path)


def test_metadata_is_recorded(index: KnowledgeIndex) -> None:
    assert index.model == "bag-of-words@32"
    assert index.dimension == 32


def test_round_trip_preserves_every_field(index: KnowledgeIndex) -> None:
    assert index.chunk("news:dog-park#intro") == CHUNKS[2]
    assert index.chunk("missing") is None


def test_keyword_search_ignores_stopwords_and_filters_kind(index: KnowledgeIndex) -> None:
    assert [c.cite_id for c in index.keyword("how many chickens", ALL_KINDS, 5)] == [
        "code:6.20.040"
    ]
    assert index.keyword("dog", [SourceKind.SITE], 5) == [CHUNKS[1]]
    assert index.keyword("what is the", ALL_KINDS, 5) == []


def test_keyword_search_tolerates_fts_syntax(index: KnowledgeIndex) -> None:
    assert index.keyword('dog" OR (park AND', ALL_KINDS, 5) != []


async def test_semantic_search_returns_cosine_similarity(
    index: KnowledgeIndex, embedder: Embedder
) -> None:
    exact = await embedder.embed_query(CHUNKS[0].embedding_text)
    (best, similarity), *_ = index.semantic(exact, ALL_KINDS, 2)
    assert best.cite_id == "code:6.20.040"
    assert similarity == pytest.approx(1.0, abs=1e-6)


async def test_semantic_search_filters_kind(index: KnowledgeIndex, embedder: Embedder) -> None:
    hits = index.semantic(await embedder.embed_query("dog"), [SourceKind.NEWS], 5)
    assert [c.cite_id for c, _ in hits] == ["news:dog-park#intro"]


def test_missing_index_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        KnowledgeIndex.open(tmp_path / "absent.sqlite")


def test_vectors_must_match_chunks(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one vector per chunk"):
        write_index(tmp_path / "index.sqlite", CHUNKS, [[1.0]], model="m")
