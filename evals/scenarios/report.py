import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path

from pydantic import BaseModel

from evals.scenarios.judge import Verdict
from evals.scenarios.runner import Run
from evals.scenarios.simulate import GoalVerdict, SimulatedCaller
from vcity.agent.conversation import Usage

# USD per million tokens: input, cache read, cache write, output (Anthropic list prices).
PRICES = {
    "claude-haiku-5-5": (0.10, 0.01, 0.125, 0.50),
    "claude-sonnet-5-5": (2.00, 0.20, 2.50, 10.00),
}
ASKS = {
    1: "Municipal code and city website",
    2: "Current events and news",
    3: "Tickets",
    4: "Routing to departments",
    5: "Business hours",
    6: "Traps",
}


@dataclass(frozen=True, slots=True)
class Summary:
    scenarios: int
    passing: int
    known_issues: int
    blocking_failures: tuple[str, ...]
    llm_p50: float
    llm_p95: float
    usage: Usage
    cost: float

    @property
    def gate_passed(self) -> bool:
        return not self.blocking_failures


def summarise(results: Mapping[str, Sequence[Run]], model: str) -> Summary:
    runs = [run for scenario_runs in results.values() for run in scenario_runs]
    passing = {sid for sid, rs in results.items() if all(not r.problems for r in rs)}
    known = {sid for sid, rs in results.items() if rs[0].scenario.known_issue}
    timings = sorted(t for run in runs for turn in run.transcript.turns for t in turn.llm_seconds)
    usage = sum((turn.usage for run in runs for turn in run.transcript.turns), Usage())
    return Summary(
        scenarios=len(results),
        passing=len(passing),
        known_issues=len(known),
        blocking_failures=tuple(sorted(set(results) - passing - known)),
        llm_p50=statistics.median(timings) if timings else 0.0,
        llm_p95=timings[int(len(timings) * 0.95)] if timings else 0.0,
        usage=usage,
        cost=_cost(usage, model),
    )


@dataclass(frozen=True, slots=True)
class EvalReport:
    model: str
    results: Mapping[str, Sequence[Run]]
    summary: Summary
    verdicts: Mapping[str, Verdict | None]
    simulations: Sequence[tuple[SimulatedCaller, GoalVerdict | None]]
    retrieval: Path | None


class _SweepPoint(BaseModel):
    threshold: float
    abstain_recall: float
    answer_retention: float


class _RetrievalReport(BaseModel):
    hit_at_3: float
    mrr: float
    configured_threshold: float
    sweep: list[_SweepPoint]


def render(report: EvalReport) -> str:
    summary = report.summary
    gate = "passed" if summary.gate_passed else "FAILED"
    body = [
        f"<h1>Scenario eval: {escape(report.model)}</h1>",
        (
            f"<p class=meta>{datetime.now().astimezone():%Y-%m-%d %H:%M %Z} · "
            f"{summary.passing}/{summary.scenarios} scenarios pass every run · gate {gate}</p>"
        ),
        _stats(summary),
        _asks(report.results),
        _scenarios(report.results, report.verdicts),
        _simulations(report.simulations),
        _retrieval(report.retrieval),
    ]
    css = Path(__file__).with_name("report.css").read_text(encoding="utf-8")
    return _PAGE.replace("{css}", css).replace("{body}", "\n".join(body))


def _stats(summary: Summary) -> str:
    u = summary.usage
    return (
        "<section><h2>Latency and cost</h2><table>"
        f"<tr><th>LLM call p50</th><td>{summary.llm_p50:.2f} s</td></tr>"
        f"<tr><th>LLM call p95</th><td>{summary.llm_p95:.2f} s</td></tr>"
        f"<tr><th>Tokens (input / cache read / output)</th><td>{u.input_tokens:,} / "
        f"{u.cache_read_tokens:,} / {u.output_tokens:,}</td></tr>"
        f"<tr><th>Agent cost for this run</th><td>${summary.cost:.3f}</td></tr>"
        "</table></section>"
    )


def _asks(results: Mapping[str, Sequence[Run]]) -> str:
    rows: list[str] = []
    for ask, title in ASKS.items():
        ids = [sid for sid, rs in results.items() if rs[0].scenario.ask == ask]
        ok = sum(all(not r.problems for r in results[sid]) for sid in ids)
        rows.append(f"<tr><td>{ask}</td><td>{escape(title)}</td><td>{ok}/{len(ids)}</td></tr>")
    return (
        "<section><h2>By ask</h2><table><tr><th>Ask</th><th>Area</th><th>Passing</th></tr>"
        + "".join(rows)
        + "</table></section>"
    )


def _scenarios(results: Mapping[str, Sequence[Run]], verdicts: Mapping[str, Verdict | None]) -> str:
    rows: list[str] = []
    for sid, runs in results.items():
        scenario = runs[0].scenario
        good = sum(not r.problems for r in runs)
        problems = next((r.problems for r in runs if r.problems), ())
        verdict = verdicts.get(sid)
        scores = (
            f"{verdict.correct}/{verdict.grounded}/{verdict.dated}/{verdict.speakable}"
            if verdict
            else "-"
        )
        status = "pass" if good == len(runs) else ("known" if scenario.known_issue else "fail")
        note = scenario.known_issue or "; ".join(problems)
        rows.append(
            f"<tr class={status}><td>{escape(sid)}</td><td>{scenario.ask}</td>"
            f"<td>{good}/{len(runs)}</td><td>{scores}</td><td>{escape(note)}</td></tr>"
        )
    return (
        "<section><h2>Scenarios</h2><p>Judge scores: correct / grounded / dated / speakable "
        "(1 to 5, report-only).</p><table><tr><th>Scenario</th><th>Ask</th><th>Runs passed</th>"
        "<th>Judge</th><th>Problems</th></tr>" + "".join(rows) + "</table></section>"
    )


def _simulations(simulations: Sequence[tuple[SimulatedCaller, GoalVerdict | None]]) -> str:
    if not simulations:
        return ""
    rows = "".join(
        f"<tr class={'pass' if v and v.goal_met else 'fail'}><td>{escape(c.id)}</td>"
        f"<td>{escape(c.goal)}</td><td>{'yes' if v and v.goal_met else 'no'}</td>"
        f"<td>{escape(v.note if v else 'no verdict')}</td></tr>"
        for c, v in simulations
    )
    return (
        "<section><h2>Simulated callers (report-only)</h2><table><tr><th>Caller</th>"
        f"<th>Goal</th><th>Met</th><th>Note</th></tr>{rows}</table></section>"
    )


def _retrieval(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    report = _RetrievalReport.model_validate_json(path.read_text(encoding="utf-8"))
    width, height = 480, 200
    low, high = report.sweep[0].threshold, report.sweep[-1].threshold

    def line(name: str, values: Sequence[float]) -> str:
        points = " ".join(
            f"{(p.threshold - low) / (high - low) * width:.1f},{height - v * height:.1f}"
            for p, v in zip(report.sweep, values, strict=True)
        )
        return f'<polyline class="{name}" points="{points}" />'

    recall = line("abstain_recall", [p.abstain_recall for p in report.sweep])
    retention = line("answer_retention", [p.answer_retention for p in report.sweep])
    return (
        "<section><h2>Retrieval</h2>"
        f"<p>hit@3 {report.hit_at_3:.2f} · MRR {report.mrr:.2f} · "
        f"τ = {report.configured_threshold:.2f}</p>"
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Abstention and answer '
        f'retention across thresholds">{recall}{retention}</svg>'
        "<p class=legend><span class=abstain_recall>abstain recall</span> · "
        f"<span class=answer_retention>answer retention</span> · τ from {low:.2f} to "
        f"{high:.2f}</p></section>"
    )


def _cost(usage: Usage, model: str) -> float:
    fresh, read, write, out = PRICES.get(model, PRICES["claude-haiku-5-5"])
    return (
        usage.input_tokens * fresh
        + usage.cache_read_tokens * read
        + usage.cache_write_tokens * write
        + usage.output_tokens * out
    ) / 1_000_000


_PAGE = (
    "<!doctype html><html lang=en><head><meta charset=utf-8>"
    '<meta name=viewport content="width=device-width, initial-scale=1">'
    "<title>vcity eval report</title><style>{css}</style></head><body>{body}</body></html>"
)
