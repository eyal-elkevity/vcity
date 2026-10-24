import hashlib
import math
from collections.abc import Sequence

import pytest

from vcity.knowledge.embeddings import Embedder, Vector

DIMENSION = 32


class BagOfWordsEmbedder:
    @property
    def model(self) -> str:
        return f"bag-of-words@{DIMENSION}"

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        return [self._vector(text) for text in texts]

    async def embed_query(self, text: str) -> Vector:
        return self._vector(text)

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * DIMENSION
        for word in text.lower().split():
            bucket = int(hashlib.sha256(word.strip(".,?>").encode()).hexdigest(), 16) % DIMENSION
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


@pytest.fixture
def embedder() -> Embedder:
    return BagOfWordsEmbedder()
