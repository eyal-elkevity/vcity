import logging

from anthropic import APIError, AsyncAnthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.scenarios.checks import Transcript
from evals.scenarios.model import Scenario

JUDGE_MODEL = "claude-sonnet-5-5"
_log = logging.getLogger(__name__)

_RUBRIC = """You grade one phone call between a caller and the City of Corona's voice agent.
Score each criterion from 1 (poor) to 5 (excellent):
- correct: does the agent's answer or action match the reference outcome?
- grounded: is every factual claim supported by the tool results shown? Invented facts score 1.
- dated: when news, events or office hours matter, does the agent give the date or time? \
Score 5 when dates don't matter.
- speakable: would this sound natural read aloud on a phone call: short, no lists or URLs?
Then give a one-sentence note on the biggest weakness."""


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    correct: int = Field(ge=1, le=5)
    grounded: int = Field(ge=1, le=5)
    dated: int = Field(ge=1, le=5)
    speakable: int = Field(ge=1, le=5)
    note: str


async def judge(
    client: AsyncAnthropic, scenario: Scenario, transcript: Transcript
) -> Verdict | None:
    try:
        response = await client.messages.parse(
            model=JUDGE_MODEL,
            max_tokens=400,
            thinking={"type": "between_tools"},
            system=_RUBRIC,
            messages=[{"role": "user", "content": _call_record(scenario, transcript)}],
            output_format=Verdict,
        )
    except (APIError, ValidationError):
        _log.warning("judge failed on %s", scenario.id, exc_info=True)
        return None
    return response.parsed_output


def _call_record(scenario: Scenario, transcript: Transcript) -> str:
    lines = [
        f"Reference outcome: {scenario.reference}",
        f"Local time: {scenario.clock:%A %Y-%m-%d %H:%M}",
    ]
    for caller, turn in zip(transcript.callers, transcript.turns, strict=True):
        lines.append(f"CALLER: {caller}")
        lines.extend(
            f"TOOL {call.name}({call.input}) -> {call.result.content}" for call in turn.tool_calls
        )
        lines.append(f"AGENT: {turn.text}")
    return "\n".join(lines)
