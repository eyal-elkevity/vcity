import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from vcity.knowledge.model import SearchStatus, SourceKind

_SCRUBBERS = (
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b"), "[phone]"),
    (re.compile(r"\d{5,}"), "[number]"),
)


class GapLog(Protocol):
    def record(
        self,
        *,
        at: datetime,
        query: str,
        kind: SourceKind | None,
        status: SearchStatus,
        caller_id: str | None,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class GapEntry:
    at: datetime
    query: str
    kind: SourceKind | None
    status: SearchStatus
    caller: str | None


def scrub(text: str) -> str:
    for pattern, replacement in _SCRUBBERS:
        text = pattern.sub(replacement, text)
    return text


def hash_caller(caller_id: str, salt: str) -> str:
    return hmac.new(salt.encode(), caller_id.encode(), hashlib.sha256).hexdigest()[:16]


class JsonlGapLog:
    def __init__(self, path: Path, salt: str) -> None:
        if not salt:
            raise ValueError("a gap log needs a non-empty salt to hash caller IDs")
        self._path = path
        self._salt = salt

    def record(
        self,
        *,
        at: datetime,
        query: str,
        kind: SourceKind | None,
        status: SearchStatus,
        caller_id: str | None,
    ) -> None:
        entry = GapEntry(
            at=at,
            query=scrub(query),
            kind=kind,
            status=status,
            caller=hash_caller(caller_id, self._salt) if caller_id else None,
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(asdict(entry), default=str) + "\n")
