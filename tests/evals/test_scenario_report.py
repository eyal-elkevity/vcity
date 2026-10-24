from datetime import datetime
from zoneinfo import ZoneInfo

from evals.scenarios.checks import Transcript
from evals.scenarios.model import Expect, Scenario
from evals.scenarios.report import EvalReport, render, summarise
from evals.scenarios.runner import Run
from vcity.agent.conversation import Turn, Usage

LA = ZoneInfo("America/Los_Angeles")


def transcript(text: str) -> Transcript:
    return Transcript(("hello",), (Turn(text, (), (0.5,), Usage(10, 0, 0, 5)),), (), ())


def test_report_escapes_text_and_flags_known_issues() -> None:
    scenario = Scenario(
        id="x<script>",
        ask=1,
        clock=datetime(2026, 10, 27, 10, 0, tzinfo=LA),
        caller=("hi",),
        expect=Expect(mentions=("never",)),
        reference="r",
        known_issue="retrieval gate",
    )
    run = Run(scenario, transcript("hello"), ("never said 'never'",))
    results = {scenario.id: [run]}
    summary = summarise(results, "claude-haiku-5-5")
    assert summary.gate_passed
    assert summary.known_issues == 1
    html = render(EvalReport("claude-haiku-5-5", results, summary, {}, [], None))
    assert "x&lt;script&gt;" in html
    assert "<tr class=known>" in html
