from datetime import UTC, datetime

import pytest

from vcity.knowledge.model import BREADCRUMB_SEPARATOR, Chunk, SourceKind


def make_chunk(*, date: datetime | None = None, ends_at: datetime | None = None) -> Chunk:
    return Chunk(
        cite_id="code:6.20.040",
        kind=SourceKind.CODE,
        breadcrumb=BREADCRUMB_SEPARATOR.join(["Corona Municipal Code", "Title 6 Animals"]),
        text="Chickens are permitted.",
        url="https://example.test/6.20.040",
        date=date,
        ends_at=ends_at,
    )


def test_embedding_text_leads_with_breadcrumb() -> None:
    assert make_chunk().embedding_text == (
        "Corona Municipal Code > Title 6 Animals\n\nChickens are permitted."
    )


def test_naive_dates_are_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        make_chunk(date=datetime(2026, 10, 24, 9, 0))  # noqa: DTZ001


def test_aware_dates_are_kept() -> None:
    when = datetime(2026, 10, 24, 16, 0, tzinfo=UTC)
    assert make_chunk(date=when, ends_at=when).date == when
