from collections.abc import Sequence
from pathlib import Path

from vcity.knowledge.embeddings import CachedEmbedder, Vector


class CountingEmbedder:
    def __init__(self) -> None:
        self.document_calls: list[list[str]] = []

    @property
    def model(self) -> str:
        return "fake@3"

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        self.document_calls.append(list(texts))
        return [[float(len(text)), 0.5, -1.0] for text in texts]

    async def embed_query(self, text: str) -> Vector:
        return [0.0, 0.0, float(len(text))]


async def test_cache_embeds_each_text_once(tmp_path: Path) -> None:
    inner = CountingEmbedder()
    cached = CachedEmbedder(inner, tmp_path / "cache.sqlite")
    first = await cached.embed_documents(["dog", "chickens"])
    second = await cached.embed_documents(["chickens", "bees", "dog"])
    assert inner.document_calls == [["dog", "chickens"], ["bees"]]
    assert first == [[3.0, 0.5, -1.0], [8.0, 0.5, -1.0]]
    assert second == [[8.0, 0.5, -1.0], [4.0, 0.5, -1.0], [3.0, 0.5, -1.0]]


async def test_cache_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite"
    first = CachedEmbedder(CountingEmbedder(), path)
    await first.embed_documents(["dog"])
    first.close()
    inner = CountingEmbedder()
    assert await CachedEmbedder(inner, path).embed_documents(["dog"]) == [[3.0, 0.5, -1.0]]
    assert inner.document_calls == []


async def test_duplicate_texts_are_embedded_once(tmp_path: Path) -> None:
    inner = CountingEmbedder()
    vectors = await CachedEmbedder(inner, tmp_path / "c.sqlite").embed_documents(["a", "a"])
    assert inner.document_calls == [["a"]]
    assert vectors == [[1.0, 0.5, -1.0], [1.0, 0.5, -1.0]]


async def test_queries_bypass_the_cache(tmp_path: Path) -> None:
    cached = CachedEmbedder(CountingEmbedder(), tmp_path / "cache.sqlite")
    assert await cached.embed_query("dog") == [0.0, 0.0, 3.0]
