from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

BREADCRUMB_SEPARATOR = " > "


class SourceKind(StrEnum):
    CODE = "code"
    SITE = "site"
    NEWS = "news"
    EVENTS = "events"


@dataclass(frozen=True, slots=True)
class Chunk:
    cite_id: str
    kind: SourceKind
    breadcrumb: str
    text: str
    url: str
    date: datetime | None = None
    ends_at: datetime | None = None

    def __post_init__(self) -> None:
        if any(
            moment is not None and moment.tzinfo is None for moment in (self.date, self.ends_at)
        ):
            raise ValueError("chunk dates must be timezone-aware")

    @property
    def embedding_text(self) -> str:
        return f"{self.breadcrumb}\n\n{self.text}"


class SearchStatus(StrEnum):
    MATCH = "match"
    NO_MATCH = "no_match"
    OFF_TOPIC = "off_topic"


@dataclass(frozen=True, slots=True)
class SearchResult:
    status: SearchStatus
    chunks: tuple[Chunk, ...]
