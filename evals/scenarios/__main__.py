import argparse
import asyncio
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from evals.scenarios.judge import Verdict, judge
from evals.scenarios.model import Scenario, load_scenarios
from evals.scenarios.report import EvalReport, Summary, render, summarise
from evals.scenarios.runner import Run, run_all
from evals.scenarios.simulate import GoalVerdict, SimulatedCaller, load_callers, simulate
from vcity.agent.conversation import AGENT_MODEL
from vcity.agent.services import Services, open_services
from vcity.settings import AppSettings

HERE = Path(__file__).parent
CONCURRENCY = 8


async def evaluate(args: argparse.Namespace, scenarios: Sequence[Scenario]) -> EvalReport:
    model: str = args.model
    with tempfile.TemporaryDirectory(prefix="vcity-eval-") as scratch:
        settings = AppSettings.from_env().model_copy(
            update={"gap_log_path": Path(scratch) / "gaps.jsonl"}
        )
        async with open_services(settings, file_to_linear=False) as services:
            results = await run_all(
                services, scenarios, model=model, runs=args.runs, concurrency=CONCURRENCY
            )
            verdicts = await _judge(services, results) if args.judge else {}
            simulations = await _simulate(services, model) if args.simulate else []
    return EvalReport(
        model, results, summarise(results, model), verdicts, simulations, args.retrieval
    )


async def _judge(services: Services, results: dict[str, list[Run]]) -> dict[str, Verdict | None]:
    async with asyncio.TaskGroup() as group:
        tasks = {
            sid: group.create_task(judge(services.anthropic, runs[0].scenario, runs[0].transcript))
            for sid, runs in results.items()
        }
    return {sid: task.result() for sid, task in tasks.items()}


async def _simulate(
    services: Services, model: str
) -> list[tuple[SimulatedCaller, GoalVerdict | None]]:
    callers = load_callers(HERE / "callers.yaml")
    async with asyncio.TaskGroup() as group:
        tasks = [(c, group.create_task(simulate(services, c, model=model))) for c in callers]
    return [(caller, task.result()[1]) for caller, task in tasks]


def _print(summary: Summary, report: Path) -> None:
    lines = [
        (
            f"{summary.passing}/{summary.scenarios} scenarios pass every run, "
            f"{len(summary.wobbly)} missed one run ({summary.known_issues} known issues)"
        ),
        (
            f"LLM call p50 {summary.llm_p50:.2f}s p95 {summary.llm_p95:.2f}s, "
            f"cost {summary.cost_label}"
        ),
        *(f"FAIL {sid}" for sid in summary.blocking_failures),
        *(f"MISSED ONE RUN {sid}" for sid in summary.wobbly),
        f"report: {report}",
    ]
    print("\n".join(lines))
    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(step_summary).open("a", encoding="utf-8") as sink:
            sink.write("## Scenario eval\n\n" + "\n".join(f"- {line}" for line in lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent against scripted callers.")
    parser.add_argument("--model", default=AGENT_MODEL)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", help="comma-separated scenario ids")
    parser.add_argument("--judge", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--simulate", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--report", type=Path, default=Path("reports/scenarios.html"))
    parser.add_argument("--retrieval", type=Path, default=Path("reports/retrieval.json"))
    args = parser.parse_args()
    scenarios = load_scenarios(HERE / "cases.yaml")
    if args.only:
        only = set(args.only.split(","))
        if unknown := only - {s.id for s in scenarios}:
            parser.error(f"unknown scenario ids: {', '.join(sorted(unknown))}")
        scenarios = [s for s in scenarios if s.id in only]
    report = asyncio.run(evaluate(args, scenarios))
    path: Path = args.report
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(report), encoding="utf-8")
    _print(report.summary, path)
    sys.exit(0 if report.summary.gate_passed else 1)


if __name__ == "__main__":
    main()
