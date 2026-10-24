import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from voyageai.client_async import AsyncClient

from evals.retrieval.metrics import (
    TOP_K_HIT,
    Case,
    Outcome,
    ThresholdScore,
    best_threshold,
    hit_rate,
    load_cases,
    mean_reciprocal_rank,
    outcome,
    score_threshold,
    sweep,
)
from vcity.knowledge.embeddings import VoyageEmbedder
from vcity.knowledge.index import KnowledgeIndex
from vcity.knowledge.retrieval import HybridRetriever
from vcity.knowledge.search import RELEVANCE_THRESHOLD
from vcity.settings import EmbeddingSettings

CASES = Path(__file__).with_name("cases.yaml")
THRESHOLDS = [round(0.30 + step * 0.01, 2) for step in range(41)]
HIT_AT_3_GATE = 0.90
MRR_GATE = 0.80
ABSTAIN_RECALL_GATE = 0.80
ANSWER_RETENTION_GATE = 0.85
CONCURRENCY = 4


async def run(index: KnowledgeIndex, cases: Sequence[Case]) -> list[Outcome]:
    settings = EmbeddingSettings.from_env()
    voyage = AsyncClient(api_key=settings.voyage_api_key.get_secret_value(), max_retries=3)
    retriever = HybridRetriever(index, VoyageEmbedder(voyage))
    now = datetime.now(UTC)
    gate = asyncio.Semaphore(CONCURRENCY)

    async def evaluate(case: Case) -> Outcome:
        async with gate:
            retrieval = await retriever.retrieve(case.query, case.kind, now)
        return outcome(case, retrieval.chunks, retrieval.best_similarity)

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(evaluate(case)) for case in cases]
    return [task.result() for task in tasks]


def report(outcomes: Sequence[Outcome], scores: Sequence[ThresholdScore]) -> tuple[str, bool]:
    hits, mrr = hit_rate(outcomes), mean_reciprocal_rank(outcomes)
    configured = score_threshold(outcomes, RELEVANCE_THRESHOLD)
    suggested = best_threshold(scores)
    checks = {
        f"hit@3 {hits:.2f} >= {HIT_AT_3_GATE:.2f}": hits >= HIT_AT_3_GATE,
        f"MRR {mrr:.2f} >= {MRR_GATE:.2f}": mrr >= MRR_GATE,
        f"abstain recall {configured.abstain_recall:.2f} >= {ABSTAIN_RECALL_GATE:.2f}": (
            configured.abstain_recall >= ABSTAIN_RECALL_GATE
        ),
        f"answer retention {configured.answer_retention:.2f} >= {ANSWER_RETENTION_GATE:.2f}": (
            configured.answer_retention >= ANSWER_RETENTION_GATE
        ),
    }
    lines = [
        "## Retrieval eval",
        "",
        f"- cases: {len(outcomes)} ({sum(o.case.abstain for o in outcomes)} should abstain)",
        f"- configured τ = {RELEVANCE_THRESHOLD:.2f}",
        (
            f"- best τ on this run = {suggested.threshold:.2f} "
            f"(balanced accuracy {suggested.balanced_accuracy:.2f})"
        ),
        *(f"- {'PASS' if ok else 'FAIL'} {check}" for check, ok in checks.items()),
        "",
        "| τ | abstain recall | abstain precision | answer retention |",
        "|---|---|---|---|",
        *(
            f"| {s.threshold:.2f} | {s.abstain_recall:.2f} | {s.abstain_precision:.2f} "
            f"| {s.answer_retention:.2f} |"
            for s in scores[::5]
        ),
        "",
        "| case | rank | similarity | top results |",
        "|---|---|---|---|",
        *(
            f"| {o.case.id} | {o.rank or '-'} | {o.best_similarity:.2f} "
            f"| {', '.join(o.cite_ids[:TOP_K_HIT])} |"
            for o in outcomes
            if (o.case.abstain and o.best_similarity >= RELEVANCE_THRESHOLD)
            or (not o.case.abstain and (o.rank is None or o.rank > TOP_K_HIT))
        ),
    ]
    return "\n".join(lines), all(checks.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Score retrieval against the eval cases.")
    parser.add_argument("--index", type=Path, default=Path("build/index.sqlite"))
    parser.add_argument("--report", type=Path, default=Path("reports/retrieval.json"))
    args = parser.parse_args()
    index_path: Path = args.index
    report_path: Path = args.report

    cases = load_cases(CASES)
    outcomes = asyncio.run(run(KnowledgeIndex.open(index_path), cases))
    scores = sweep(outcomes, THRESHOLDS)
    summary, passed = report(outcomes, scores)
    print(summary)
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(step_summary).open("a", encoding="utf-8") as sink:
            sink.write(summary + "\n")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "hit_at_3": hit_rate(outcomes),
                "mrr": mean_reciprocal_rank(outcomes),
                "configured_threshold": RELEVANCE_THRESHOLD,
                "sweep": [asdict(score) for score in scores],
                "outcomes": [
                    {
                        "id": o.case.id,
                        "rank": o.rank,
                        "best_similarity": o.best_similarity,
                        "cite_ids": o.cite_ids,
                    }
                    for o in outcomes
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
