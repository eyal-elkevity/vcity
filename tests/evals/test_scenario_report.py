import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from evals.scenarios.checks import Transcript
from evals.scenarios.model import Expect, Scenario
from evals.scenarios.report import EvalReport, render, summarise
from evals.scenarios.runner import Run
from vcity.agent.conversation import Turn, Usage

LA = ZoneInfo("America/Los_Angeles")


def transcript(text: str) -> Transcript:
    return Transcript(("hello",), (Turn(text, (), (0.5,), Usage(10, 0, 0, 5)),), (), ())


def scenario(sid: str = "s1", known_issue: str | None = None) -> Scenario:
    return Scenario(
        id=sid,
        ask=1,
        clock=datetime(2026, 10, 27, 10, 0, tzinfo=LA),
        caller=("hi",),
        expect=Expect(mentions=("never",)),
        reference="r",
        known_issue=known_issue,
    )


def test_report_escapes_text_and_flags_known_issues() -> None:
    case = scenario("x<script>", known_issue="retrieval gate")
    run = Run(case, transcript("hello"), ("never said 'never'",))
    results = {case.id: [run]}
    summary = summarise(results, "claude-haiku-5-5")
    assert summary.gate_passed
    assert summary.known_issues == 1
    html = render(EvalReport("claude-haiku-5-5", results, summary, {}, [], None))
    assert "x&lt;script&gt;" in html
    assert "<tr class=known>" in html


def test_an_unpriced_model_reports_an_unknown_cost_and_keeps_the_gate() -> None:
    results = {"s1": [Run(scenario(), transcript("hello"), ())]}
    summary = summarise(results, "claude-unknown-model")
    assert summary.cost is None
    assert summary.gate_passed
    html = render(EvalReport("claude-unknown-model", results, summary, {}, [], None))
    assert "<td>unknown</td>" in html


def test_a_priced_model_reports_its_cost() -> None:
    results = {"s1": [Run(scenario(), transcript("hello"), ())]}
    assert summarise(results, "claude-haiku-5-5").cost == pytest.approx(3.5e-6)


@pytest.mark.parametrize("thresholds", [[], [0.5], [0.4, 0.4]])
def test_a_degenerate_retrieval_sweep_is_left_out(tmp_path: Path, thresholds: list[float]) -> None:
    sweep = [{"threshold": t, "abstain_recall": 0.5, "answer_retention": 0.5} for t in thresholds]
    path = tmp_path / "retrieval.json"
    path.write_text(
        json.dumps({"hit_at_3": 0.9, "mrr": 0.8, "configured_threshold": 0.4, "sweep": sweep})
    )
    results = {"s1": [Run(scenario(), transcript("hello"), ())]}
    summary = summarise(results, "claude-haiku-5-5")
    html = render(EvalReport("claude-haiku-5-5", results, summary, {}, [], path))
    assert "<h2>Retrieval</h2>" not in html
