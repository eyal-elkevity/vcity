import json
from pathlib import Path
from uuid import UUID

import pytest

from evals.scenarios.checks import MAX_SPOKEN_WORDS, Transcript, failures
from evals.scenarios.model import Expect, Outcome, load_scenarios
from vcity.agent.conversation import ToolCall, Turn, Usage
from vcity.agent.routing import TransferRequest
from vcity.agent.tools import ToolResult
from vcity.domain.departments import Department
from vcity.domain.hours import TicketReason
from vcity.domain.tickets import Priority
from vcity.tickets.drafts import TicketDraft

CASES = Path(__file__).parents[2] / "evals" / "scenarios" / "cases.yaml"
DRAFT = TicketDraft(
    UUID(int=1), "[000001] Public Works: pothole", "d", frozenset({"Public Works"}), Priority.NONE
)


def route(department: str, reason: str | None) -> ToolCall:
    result = {"outcome": "ticket", "reason": reason} if reason else {"outcome": "transfer"}
    return ToolCall(
        "route_request",
        {"department": department, "summary": "Leaking hydrant"},
        ToolResult(json.dumps(result)),
    )


def transcript(
    text: str, *calls: ToolCall, tickets: tuple[TicketDraft, ...] = (), transfers: int = 0
) -> Transcript:
    turn = Turn(text, calls, (0.5,), Usage(10, 0, 0, 5))
    requests = tuple(TransferRequest(Department.UTILITIES, "+1", "x") for _ in range(transfers))
    return Transcript(("hello",), (turn,), tickets, requests)


def test_committed_scenarios_cover_every_ask_with_unique_ids() -> None:
    scenarios = load_scenarios(CASES)
    assert len(scenarios) >= 40
    assert {scenario.ask for scenario in scenarios} == {1, 2, 3, 4, 5, 6}
    assert all(scenario.clock.tzinfo is not None for scenario in scenarios)


def test_a_matching_ticket_passes_every_check() -> None:
    expect = Expect(
        tools=("route_request",),
        outcome=Outcome.TICKET,
        department=Department.PUBLIC_WORKS,
        ticket_reason=TicketReason.AFTER_HOURS,
        mentions=("Monday|Tuesday",),
        route_mentions=("hydrant",),
    )
    result = transcript(
        "Reopens Monday at 7.", route("public_works", "after_hours"), tickets=(DRAFT,)
    )
    assert failures(expect, result) == []


def test_each_mismatch_is_reported() -> None:
    expect = Expect(
        tools=("emergency_guidance",),
        not_tools=("route_request",),
        outcome=Outcome.NONE,
        department=Department.FINANCE,
        ticket_reason=TicketReason.HOLIDAY,
        mentions=("911",),
        never_says=("prefer_ticket",),
    )
    result = transcript(
        "prefer_ticket is true", route("public_works", "after_hours"), tickets=(DRAFT,)
    )
    assert failures(expect, result) == [
        "did not call emergency_guidance",
        "called route_request",
        "outcome ticket instead of none",
        "never routed to finance",
        "no ticket with reason holiday",
        "never said '911'",
        "said 'prefer_ticket'",
    ]


@pytest.mark.parametrize(
    ("text", "transfers", "ok"),
    [
        ("Sorry, I couldn't find that.", 0, True),
        ("I couldn\u2019t find that", 0, True),
        ("Target opens in May.", 0, False),
        ("I couldn't find it.", 1, False),
    ],
)
def test_abstaining_means_declining_without_routing(text: str, transfers: int, *, ok: bool) -> None:
    assert (failures(Expect(abstains=True), transcript(text, transfers=transfers)) == []) is ok


def test_every_turn_must_be_short_enough_to_speak() -> None:
    long_turn = transcript(" ".join(["word"] * (MAX_SPOKEN_WORDS + 1)))
    assert failures(Expect(), long_turn) == [f"turn 1 runs {MAX_SPOKEN_WORDS + 1} words"]


@pytest.mark.parametrize("leak", ["The caller wants Public Works.", "I'll call route_request now."])
def test_backstage_reasoning_is_never_spoken(leak: str) -> None:
    assert failures(Expect(), transcript(leak))[0].startswith("spoke backstage text")
