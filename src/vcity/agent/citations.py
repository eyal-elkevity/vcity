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
        for chunk in call.result.found:
            if _names(turn.text, chunk):
                citations.setdefault(chunk.url, _citation(chunk))
    return tuple(citations.values())[:MAX_CITATIONS]


def filed(turn: Turn) -> FiledRequest | None:
    requests = [c.result.filed for c in turn.tool_calls if c.result.filed]
    return requests[-1] if requests else None


def _section(chunk: Chunk) -> str:
    return chunk.cite_id.removeprefix(f"{SourceKind.CODE}:").partition("#")[0]


def _title(chunk: Chunk) -> str:
    trail = chunk.breadcrumb.split(BREADCRUMB_SEPARATOR)
    return re.sub(r"\*\*[^*]*\*\*", "", trail[1] if len(trail) > 1 else trail[0]).strip()


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _names(text: str, chunk: Chunk) -> bool:
    if chunk.kind is SourceKind.CODE:
        return re.search(rf"(?<![\d.]){re.escape(_section(chunk))}(?!\d)", text) is not None
    title = _words(_title(chunk))
    return bool(title) and f" {title} " in f" {_words(text)} "


def _citation(chunk: Chunk) -> Citation:
    if chunk.kind is SourceKind.CODE:
        return Citation(f"Municipal Code § {_section(chunk)}", chunk.url)
    return Citation(_title(chunk), chunk.url)
