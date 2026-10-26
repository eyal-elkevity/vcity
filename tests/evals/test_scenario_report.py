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


def scenario(sid: str = "s1", known_issue: str | None = None, ask: int = 1) -> Scenario:
    return Scenario(
        id=sid,
        ask=ask,
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


def runs(case: Scenario, misses: int, total: int = 3) -> list[Run]:
    return [
        Run(case, transcript("hello"), ("never said 'never'",) if i < misses else ())
        for i in range(total)
    ]


def test_one_missed_run_of_an_ordinary_scenario_is_tolerated() -> None:
    results = {"a": runs(scenario("a"), 1), "b": runs(scenario("b"), 0)}
    summary = summarise(results, "claude-haiku-5-5")
    assert summary.gate_passed
    assert summary.passing == 1
    assert summary.wobbly == ("a",)


def test_two_missed_runs_fail_the_gate() -> None:
    summary = summarise({"a": runs(scenario("a"), 2)}, "claude-haiku-5-5")
    assert not summary.gate_passed
    assert summary.blocking_failures == ("a",)


def test_a_trap_must_pass_every_run() -> None:
    summary = summarise({"t": runs(scenario("t", ask=6), 1)}, "claude-haiku-5-5")
    assert not summary.gate_passed
    assert summary.blocking_failures == ("t",)


def test_several_wobbly_scenarios_fail_the_gate_together() -> None:
    results = {"a": runs(scenario("a"), 1), "b": runs(scenario("b"), 1)}
    summary = summarise(results, "claude-haiku-5-5")
    assert summary.blocking_failures == ()
    assert summary.wobbly == ("a", "b")
    assert not summary.gate_passed


def test_a_single_run_tolerates_no_miss() -> None:
    summary = summarise({"a": runs(scenario("a"), 1, total=1)}, "claude-haiku-5-5")
    assert not summary.gate_passed


def test_a_known_issue_is_neither_blocking_nor_wobbly() -> None:
    results = {"k": runs(scenario("k", known_issue="gate"), 3), "a": runs(scenario("a"), 1)}
    summary = summarise(results, "claude-haiku-5-5")
    assert summary.gate_passed
    assert summary.wobbly == ("a",)


def test_the_report_marks_wobbly_scenarios() -> None:
    results = {"a": runs(scenario("a"), 1)}
    summary = summarise(results, "claude-haiku-5-5")
    html = render(EvalReport("claude-haiku-5-5", results, summary, {}, [], None))
    assert "<tr class=wobbly>" in html
