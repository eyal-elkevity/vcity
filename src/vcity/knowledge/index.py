import re
import sqlite3
from collections.abc import Collection, Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import sqlite_vec

from vcity.knowledge.model import Chunk, SourceKind

_SCHEMA = """
create table meta(key text primary key, value text not null);
create table chunks(
    id integer primary key,
    cite_id text not null unique,
    kind text not null,
    breadcrumb text not null,
    body text not null,
    url text not null,
    date text,
    ends_at text
);
create virtual table chunk_text using fts5(
    breadcrumb, body, content=chunks, content_rowid=id, tokenize="porter unicode61"
);
"""
_COLUMNS = "c.cite_id, c.kind, c.breadcrumb, c.body, c.url, c.date, c.ends_at"
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from", "how",
    "i", "if", "in", "is", "it", "me", "my", "of", "on", "or", "our", "the", "to", "what",
    "when", "where", "which", "who", "why", "will", "with", "you", "your",
})  # fmt: skip
_WORD = re.compile(r"\w+")


def write_index(
    path: Path, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]], *, model: str
) -> None:
    if not chunks or len(chunks) != len(vectors):
        raise ValueError("an index needs at least one chunk and exactly one vector per chunk")
    dimension = len(vectors[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with closing(_connect(str(path))) as db, db:
        db.executescript(_SCHEMA)
        db.execute(
            "create virtual table chunk_vectors using vec0(id integer primary key, kind text, "
            f"embedding float[{dimension}] distance_metric=cosine)"
        )
        db.executemany(
            "insert into meta values (?, ?)",
            [
                ("model", model),
                ("dimension", str(dimension)),
                ("built_at", datetime.now(UTC).isoformat()),
            ],
        )
        rows = list(enumerate(zip(chunks, vectors, strict=True), start=1))
        db.executemany(
            "insert into chunks values (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (row, c.cite_id, c.kind, c.breadcrumb, c.text, c.url, _iso(c.date), _iso(c.ends_at))
                for row, (c, _) in rows
            ],
        )
        db.execute("insert into chunk_text(chunk_text) values ('rebuild')")
        db.executemany(
            "insert into chunk_vectors values (?, ?, ?)",
            [(row, c.kind, sqlite_vec.serialize_float32(v)) for row, (c, v) in rows],
        )


class KnowledgeIndex:
    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        meta = {str(key): str(value) for key, value in db.execute("select key, value from meta")}
        self.model = meta["model"]
        self.dimension = int(meta["dimension"])
        self.built_at = datetime.fromisoformat(meta["built_at"])

    @classmethod
    def open(cls, path: Path) -> Self:
        if not path.is_file():
            raise FileNotFoundError(path)
        return cls(_connect(f"{path.resolve().as_uri()}?mode=ro", uri=True))

    def close(self) -> None:
        self._db.close()

    def keyword(self, query: str, kinds: Collection[SourceKind], limit: int) -> list[Chunk]:
        expression = _fts_expression(query)
        if expression is None:
            return []
        rows = self._db.execute(
            f"select {_COLUMNS} from chunk_text join chunks c on c.id = chunk_text.rowid "  # noqa: S608
            f"where chunk_text match ? and c.kind in ({_placeholders(kinds)}) "
            "order by bm25(chunk_text) limit ?",
            [expression, *kinds, limit],
        )
        return [_chunk(row) for row in rows]

    def semantic(
        self, vector: Sequence[float], kinds: Collection[SourceKind], limit: int
    ) -> list[tuple[Chunk, float]]:
        rows = self._db.execute(
            f"select {_COLUMNS}, v.distance from chunk_vectors v join chunks c on c.id = v.id "  # noqa: S608
            f"where v.embedding match ? and k = ? and v.kind in ({_placeholders(kinds)}) "
            "order by v.distance",
            [sqlite_vec.serialize_float32(vector), limit, *kinds],
        )
        return [(_chunk(row[:-1]), 1.0 - float(row[-1])) for row in rows]

    def chunk(self, cite_id: str) -> Chunk | None:
        row = self._db.execute(
            f"select {_COLUMNS} from chunks c where c.cite_id = ?",  # noqa: S608
            [cite_id],
        ).fetchone()
        return _chunk(row) if row else None


def _connect(database: str, *, uri: bool = False) -> sqlite3.Connection:
    db = sqlite3.connect(database, uri=uri)
    db.enable_load_extension(True)  # noqa: FBT003
    sqlite_vec.load(db)
    db.enable_load_extension(False)  # noqa: FBT003
    return db


def _fts_expression(query: str) -> str | None:
    terms = dict.fromkeys(t for t in _WORD.findall(query.lower()) if t not in _STOPWORDS)
    return " OR ".join(f'"{term}"' for term in terms) or None


def _placeholders(items: Collection[object]) -> str:
    return ", ".join("?" * len(items))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _chunk(row: Sequence[object]) -> Chunk:
    cite_id, kind, breadcrumb, body, url, date, ends_at = row
    return Chunk(
        cite_id=str(cite_id),
        kind=SourceKind(kind),
        breadcrumb=str(breadcrumb),
        text=str(body),
        url=str(url),
        date=datetime.fromisoformat(str(date)) if date else None,
        ends_at=datetime.fromisoformat(str(ends_at)) if ends_at else None,
    )
