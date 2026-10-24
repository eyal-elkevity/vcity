import hashlib
import sqlite3
from array import array
from collections.abc import Sequence
from itertools import batched
from pathlib import Path
from typing import Literal, Protocol

from voyageai.client_async import AsyncClient

VOYAGE_MODEL = "voyage-4-lite"
VOYAGE_DIMENSION = 1024
VOYAGE_BATCH_SIZE = 128
_SQLITE_BATCH = 500

type Vector = Sequence[float]
type InputType = Literal["document", "query"]


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]: ...

    async def embed_query(self, text: str) -> Vector: ...


class VoyageEmbedder:
    def __init__(
        self,
        client: AsyncClient,
        model: str = VOYAGE_MODEL,
        dimension: int = VOYAGE_DIMENSION,
    ) -> None:
        self._client = client
        self._model = model
        self._dimension = dimension

    @property
    def model(self) -> str:
        return f"{self._model}@{self._dimension}"

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        vectors: list[Vector] = []
        for batch in batched(texts, VOYAGE_BATCH_SIZE):
            vectors.extend(await self._embed(list(batch), "document"))
        return vectors

    async def embed_query(self, text: str) -> Vector:
        return (await self._embed([text], "query"))[0]

    async def _embed(self, texts: list[str], input_type: InputType) -> list[Vector]:
        result = await self._client.embed(
            texts, model=self._model, input_type=input_type, output_dimension=self._dimension
        )
        return [[float(value) for value in vector] for vector in result.embeddings]


class CachedEmbedder:
    def __init__(self, inner: Embedder, path: Path) -> None:
        self._inner = inner
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.execute(
            "create table if not exists vectors(key text primary key, vector blob not null)"
        )

    @property
    def model(self) -> str:
        return self._inner.model

    async def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        keys = [self._key(text) for text in texts]
        known = self._lookup(keys)
        missing = {key: text for key, text in zip(keys, texts, strict=True) if key not in known}
        if missing:
            fresh = dict(
                zip(missing, await self._inner.embed_documents(list(missing.values())), strict=True)
            )
            with self._db:
                self._db.executemany(
                    "insert or replace into vectors values (?, ?)",
                    [(key, array("f", vector).tobytes()) for key, vector in fresh.items()],
                )
            known |= fresh
        return [known[key] for key in keys]

    async def embed_query(self, text: str) -> Vector:
        return await self._inner.embed_query(text)

    def close(self) -> None:
        self._db.close()

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self.model}\0{text}".encode()).hexdigest()

    def _lookup(self, keys: Sequence[str]) -> dict[str, Vector]:
        found: dict[str, Vector] = {}
        for batch in batched(keys, _SQLITE_BATCH):
            rows = self._db.execute(
                f"select key, vector from vectors where key in ({', '.join('?' * len(batch))})",  # noqa: S608
                batch,
            )
            found |= {str(key): array("f", blob).tolist() for key, blob in rows}
        return found
