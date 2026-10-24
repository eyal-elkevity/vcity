from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from vcity.knowledge.model import Chunk, SourceKind

TOP_K_HIT = 3


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    query: str
    kind: SourceKind | None = None
    expect: tuple[str, ...] = ()
    expect_breadcrumb: str | None = None
    abstain: bool = False

    @model_validator(mode="after")
    def _exactly_one_expectation(self) -> Self:
        given = [bool(self.expect), self.expect_breadcrumb is not None, self.abstain]
        if sum(given) != 1:
            raise ValueError(
                f"case {self.id} needs exactly one of expect, expect_breadcrumb, abstain"
            )
        return self

    @property
    def gated(self) -> bool:
        return self.kind is not SourceKind.EVENTS

    def matches(self, chunk: Chunk) -> bool:
        if self.expect_breadcrumb is not None:
            return self.expect_breadcrumb in chunk.breadcrumb
        return any(chunk.cite_id == e or chunk.cite_id.startswith(f"{e}#") for e in self.expect)


@dataclass(frozen=True, slots=True)
class Outcome:
    case: Case
    cite_ids: tuple[str, ...]
    rank: int | None
    best_similarity: float


@dataclass(frozen=True, slots=True)
class ThresholdScore:
    threshold: float
    abstain_recall: float
    abstain_precision: float
    answer_retention: float

    @property
    def balanced_accuracy(self) -> float:
        return (self.abstain_recall + self.answer_retention) / 2


def load_cases(path: Path) -> list[Case]:
    cases = [Case.model_validate(raw) for raw in yaml.safe_load(path.read_text(encoding="utf-8"))]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("case ids must be unique")
    return cases


def outcome(case: Case, chunks: Sequence[Chunk], best_similarity: float) -> Outcome:
    rank = next((position for position, c in enumerate(chunks, start=1) if case.matches(c)), None)
    return Outcome(case, tuple(c.cite_id for c in chunks), rank, best_similarity)


def hit_rate(outcomes: Sequence[Outcome], k: int = TOP_K_HIT) -> float:
    answerable = [o for o in outcomes if not o.case.abstain]
    return sum(o.rank is not None and o.rank <= k for o in answerable) / len(answerable)


def mean_reciprocal_rank(outcomes: Sequence[Outcome]) -> float:
    answerable = [o for o in outcomes if not o.case.abstain]
    return sum(1 / o.rank for o in answerable if o.rank is not None) / len(answerable)


def score_threshold(outcomes: Sequence[Outcome], threshold: float) -> ThresholdScore:
    gated = [o for o in outcomes if o.case.gated]
    abstained = [o for o in gated if o.best_similarity < threshold]
    should_abstain = [o for o in gated if o.case.abstain]
    answerable = [o for o in gated if not o.case.abstain]
    correct = [o for o in abstained if o.case.abstain]
    return ThresholdScore(
        threshold=threshold,
        abstain_recall=len(correct) / len(should_abstain) if should_abstain else 1.0,
        abstain_precision=len(correct) / len(abstained) if abstained else 1.0,
        answer_retention=sum(o.best_similarity >= threshold for o in answerable) / len(answerable),
    )


def sweep(outcomes: Sequence[Outcome], thresholds: Sequence[float]) -> list[ThresholdScore]:
    return [score_threshold(outcomes, threshold) for threshold in thresholds]


def best_threshold(scores: Sequence[ThresholdScore]) -> ThresholdScore:
    return max(scores, key=lambda score: (score.balanced_accuracy, -score.threshold))
