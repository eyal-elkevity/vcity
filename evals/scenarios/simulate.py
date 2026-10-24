import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import yaml
from anthropic import APIError, AsyncAnthropic
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from evals.scenarios.judge import JUDGE_MODEL
from evals.scenarios.model import CITY_TZ
from vcity.agent.conversation import AGENT_MODEL, Conversation, Turn
from vcity.agent.routing import RecordingTransfer
from vcity.agent.services import Services
from vcity.agent.tools import CallContext
from vcity.tickets.sinks import MockSink

MAX_TURNS = 6
END_OF_CALL = "<hang up>"
_log = logging.getLogger(__name__)


class SimulatedCaller(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    clock: datetime
    persona: str
    goal: str

    @field_validator("clock")
    @classmethod
    def _city_time(cls, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=CITY_TZ)


class GoalVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    goal_met: bool
    note: str


def load_callers(path: Path) -> list[SimulatedCaller]:
    return [
        SimulatedCaller.model_validate(entry) for entry in yaml.safe_load(path.read_text("utf-8"))
    ]


async def simulate(
    services: Services, caller: SimulatedCaller, *, model: str = AGENT_MODEL
) -> tuple[list[tuple[str, Turn]], GoalVerdict | None]:
    conversation = Conversation(
        services.anthropic,
        services.toolbox(
            clock=lambda: caller.clock, tickets=MockSink(), transfers=RecordingTransfer()
        ),
        services.system,
        CallContext(f"SIM-{caller.id}", None),
        lambda: caller.clock,
        model=model,
    )
    exchange: list[tuple[str, Turn]] = []
    try:
        line = await _caller_line(services.anthropic, caller, exchange)
        while line != END_OF_CALL and len(exchange) < MAX_TURNS:
            exchange.append((line, await conversation.reply(line)))
            line = await _caller_line(services.anthropic, caller, exchange)
        return exchange, await _verdict(services.anthropic, caller, exchange)
    except (APIError, ValidationError):
        _log.warning("simulated caller %s failed", caller.id, exc_info=True)
        return exchange, None


async def _caller_line(
    client: AsyncAnthropic, caller: SimulatedCaller, exchange: Sequence[tuple[str, Turn]]
) -> str:
    system = (
        f"You are role-playing a person phoning the City of Corona. {caller.persona} "
        f"Your goal: {caller.goal} Speak in one or two short sentences, like a real phone call. "
        f"When your goal is met or the call is clearly over, reply with exactly {END_OF_CALL}."
    )
    history = (
        "\n".join(f"YOU: {said}\nCITY: {turn.text}" for said, turn in exchange) or "(call starts)"
    )
    response = await client.messages.create(
        model=AGENT_MODEL,
        max_tokens=120,
        thinking={"type": "disabled"},
        system=system,
        messages=[{"role": "user", "content": f"{history}\n\nWhat do you say next?"}],
    )
    text = " ".join(block.text for block in response.content if block.type == "text").strip()
    return END_OF_CALL if END_OF_CALL in text or not text else text


async def _verdict(
    client: AsyncAnthropic, caller: SimulatedCaller, exchange: Sequence[tuple[str, Turn]]
) -> GoalVerdict | None:
    record = "\n".join(f"CALLER: {said}\nAGENT: {turn.text}" for said, turn in exchange)
    response = await client.messages.parse(
        model=JUDGE_MODEL,
        max_tokens=300,
        thinking={"type": "between_tools"},
        system="Decide whether the caller's goal was met in this call. Be strict.",
        messages=[{"role": "user", "content": f"Goal: {caller.goal}\n\n{record}"}],
        output_format=GoalVerdict,
    )
    return response.parsed_output
