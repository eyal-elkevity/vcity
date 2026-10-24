import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vcity.knowledge.gap_log import JsonlGapLog, hash_caller, scrub
from vcity.knowledge.model import SearchStatus, SourceKind

AT = datetime(2026, 10, 24, 18, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "scrubbed"),
    [
        ("email me at jane.doe+city@example.com", "email me at [email]"),
        ("call (951) 555-0142 tonight", "call [phone] tonight"),
        ("my number is +1 951.555.0142", "my number is [phone]"),
        ("account 123456789 is late", "account [number] is late"),
        ("is 2026 the year of the fair", "is 2026 the year of the fair"),
    ],
)
def test_scrub_removes_contact_details(raw: str, scrubbed: str) -> None:
    assert scrub(raw) == scrubbed


def test_caller_hash_is_stable_salted_and_short() -> None:
    first = hash_caller("+19515550142", "salt-a")
    assert first == hash_caller("+19515550142", "salt-a")
    assert first != hash_caller("+19515550142", "salt-b")
    assert len(first) == 16
    assert "9515550142" not in first


def test_record_appends_scrubbed_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "gaps.jsonl"
    log = JsonlGapLog(path, salt="salt-a")
    log.record(
        at=AT,
        query="when is trash pickup? call 951-555-0142",
        kind=None,
        status=SearchStatus.NO_MATCH,
        caller_id="+19515550142",
    )
    log.record(
        at=AT,
        query="tell me a joke",
        kind=SourceKind.SITE,
        status=SearchStatus.OFF_TOPIC,
        caller_id=None,
    )
    first, second = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    assert first == {
        "at": "2026-10-24T18:30:00+00:00",
        "query": "when is trash pickup? call [phone]",
        "kind": None,
        "status": "no_match",
        "caller": hash_caller("+19515550142", "salt-a"),
    }
    assert second["caller"] is None
    assert second["kind"] == "site"


def test_empty_salt_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="salt"):
        JsonlGapLog(tmp_path / "gaps.jsonl", salt="")
