import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from evals.scenarios.checks import Transcript, failures
from evals.scenarios.model import Scenario
from vcity.agent.conversation import Conversation
from vcity.agent.routing import RecordingTransfer
from vcity.agent.services import Services
from vcity.agent.tools import CallContext
from vcity.tickets.sinks import MockSink

EVAL_CALLER_ID = "+19515550100"


@dataclass(frozen=True, slots=True)
class Run:
    scenario: Scenario
    transcript: Transcript
    problems: tuple[str, ...]


async def run_once(services: Services, scenario: Scenario, model: str) -> Run:
    tickets, transfers = MockSink(), RecordingTransfer()
    toolbox = services.toolbox(clock=lambda: scenario.clock, tickets=tickets, transfers=transfers)
    conversation = Conversation(
        services.anthropic,
        toolbox,
        services.system,
        CallContext(f"EVAL-{scenario.id}", EVAL_CALLER_ID),
        lambda: scenario.clock,
        model=model,
    )
    turns = [await conversation.reply(line) for line in scenario.caller]
    transcript = Transcript(
        scenario.caller, tuple(turns), tuple(tickets.created), tuple(transfers.requests)
    )
    return Run(scenario, transcript, tuple(failures(scenario.expect, transcript)))


async def run_all(
    services: Services, scenarios: Sequence[Scenario], *, model: str, runs: int, concurrency: int
) -> dict[str, list[Run]]:
    gate = asyncio.Semaphore(concurrency)

    async def one(scenario: Scenario) -> Run:
        async with gate:
            return await run_once(services, scenario, model)

    async with asyncio.TaskGroup() as group:
        tasks = [(s.id, group.create_task(one(s))) for s in scenarios for _ in range(runs)]
    results: dict[str, list[Run]] = {s.id: [] for s in scenarios}
    for scenario_id, task in tasks:
        results[scenario_id].append(task.result())
    return results
