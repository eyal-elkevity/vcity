import re
from dataclasses import dataclass

from vcity.agent.conversation import Turn
from vcity.agent.tools import FiledRequest
from vcity.knowledge.model import BREADCRUMB_SEPARATOR, Chunk, SourceKind

MAX_CITATIONS = 3


@dataclass(frozen=True, slots=True)
class Citation:
    label: str
    url: str


def cited(turn: Turn) -> tuple[Citation, ...]:
    citations: dict[str, Citation] = {}
    for call in turn.tool_calls:
        found = call.result.found
        named = [c for c in found if c.kind is SourceKind.CODE and _names(turn.text, c)]
        top = found[:1] if found and found[0].kind is not SourceKind.CODE else ()
        for chunk in (*named, *top):
            citations.setdefault(chunk.url, _citation(chunk))
    return tuple(citations.values())[:MAX_CITATIONS]


def filed(turn: Turn) -> FiledRequest | None:
    requests = [c.result.filed for c in turn.tool_calls if c.result.filed]
    return requests[-1] if requests else None


def _section(chunk: Chunk) -> str:
    return chunk.cite_id.removeprefix(f"{SourceKind.CODE}:").partition("#")[0]


def _names(text: str, chunk: Chunk) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(_section(chunk))}(?!\d)", text) is not None


def _citation(chunk: Chunk) -> Citation:
    if chunk.kind is SourceKind.CODE:
        return Citation(f"Municipal Code § {_section(chunk)}", chunk.url)
    return Citation(chunk.breadcrumb.split(BREADCRUMB_SEPARATOR)[-1], chunk.url)
