from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.retrieval.metrics import (
    Case,
    Outcome,
    best_threshold,
    hit_rate,
    load_cases,
    mean_reciprocal_rank,
    outcome,
    score_threshold,
    sweep,
)
from vcity.knowledge.model import Chunk, SourceKind

CASES = Path(__file__).parents[2] / "evals" / "retrieval" / "cases.yaml"


def chunk(cite_id: str, breadcrumb: str = "Corona") -> Chunk:
    return Chunk(cite_id, SourceKind.CODE, breadcrumb, "text", "https://example.test")


def answerable(case_id: str, rank: int | None, similarity: float) -> Outcome:
    case = Case(id=case_id, query="q", expect=("code:1.01.010",))
    return Outcome(case, (), rank, similarity)


def unanswerable(case_id: str, similarity: float) -> Outcome:
    return Outcome(Case(id=case_id, query="q", abstain=True), (), None, similarity)


def test_committed_cases_are_valid_and_balanced() -> None:
    cases = load_cases(CASES)
    assert len(cases) >= 40
    assert sum(case.abstain for case in cases) >= 10


def test_expectation_must_be_exactly_one() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Case(id="x", query="q", expect=("code:1",), abstain=True)
    with pytest.raises(ValidationError, match="exactly one"):
        Case(id="x", query="q")


def test_expected_prefix_matches_split_chunks_only_at_part_boundary() -> None:
    case = Case(id="x", query="q", expect=("code:6.20.040",))
    assert case.matches(chunk("code:6.20.040"))
    assert case.matches(chunk("code:6.20.040#B"))
    assert not case.matches(chunk("code:6.20.0400"))


def test_breadcrumb_expectation_matches_events() -> None:
    case = Case(id="x", query="q", kind=SourceKind.EVENTS, expect_breadcrumb="Library Board")
    assert case.matches(chunk("event:abc", "City of Corona events > Library Board of Trustees"))
    assert not case.gated


def test_outcome_records_first_matching_rank() -> None:
    case = Case(id="x", query="q", expect=("code:2",))
    result = outcome(case, [chunk("code:1"), chunk("code:2#A"), chunk("code:2")], 0.6)
    assert result.rank == 2
    assert result.cite_ids == ("code:1", "code:2#A", "code:2")


def test_hit_rate_and_mrr_ignore_abstain_cases() -> None:
    outcomes = [
        answerable("a", 1, 0.7),
        answerable("b", 3, 0.6),
        answerable("c", 5, 0.6),
        answerable("d", None, 0.5),
        unanswerable("e", 0.2),
    ]
    assert hit_rate(outcomes) == pytest.approx(2 / 4)
    assert mean_reciprocal_rank(outcomes) == pytest.approx((1 + 1 / 3 + 1 / 5) / 4)


def test_threshold_score_counts_abstentions() -> None:
    outcomes = [
        answerable("a", 1, 0.70),
        answerable("b", 1, 0.45),
        unanswerable("c", 0.40),
        unanswerable("d", 0.55),
    ]
    score = score_threshold(outcomes, 0.5)
    assert score.abstain_recall == pytest.approx(1 / 2)
    assert score.abstain_precision == pytest.approx(1 / 2)
    assert score.answer_retention == pytest.approx(1 / 2)


def test_best_threshold_prefers_balance_then_the_lower_value() -> None:
    outcomes = [answerable("a", 1, 0.70), unanswerable("b", 0.40)]
    scores = sweep(outcomes, [0.30, 0.45, 0.50, 0.80])
    assert best_threshold(scores).threshold == 0.45
