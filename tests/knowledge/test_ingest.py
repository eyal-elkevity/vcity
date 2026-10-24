from pathlib import Path

from vcity.knowledge.embeddings import Embedder
from vcity.knowledge.index import KnowledgeIndex
from vcity.knowledge.ingest import build, code_chunks
from vcity.knowledge.model import SourceKind

EXCERPT = Path(__file__).parents[1] / "fixtures" / "code" / "title-6-excerpt.json"


def test_code_snapshot_becomes_cited_chunks() -> None:
    cite_ids = [chunk.cite_id for chunk in code_chunks(EXCERPT)]
    assert "code:6.20.030" in cite_ids
    assert any(cite_id.startswith("code:6.04.020#") for cite_id in cite_ids)
    assert len(cite_ids) == len(set(cite_ids))


async def test_build_writes_a_searchable_index(tmp_path: Path, embedder: Embedder) -> None:
    out = tmp_path / "build" / "index.sqlite"
    await build(code_chunks(EXCERPT), embedder, out)
    index = KnowledgeIndex.open(out)
    assert index.model == embedder.model
    assert [c.cite_id for c in index.keyword("roosters", list(SourceKind), 1)] == [
        "code:6.20.040#F"
    ]
