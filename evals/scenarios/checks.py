from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from evals.scenarios.model import Expect, Outcome
from vcity.agent.conversation import ToolCall, Turn
from vcity.agent.routing import TransferRequest
from vcity.agent.tools import ToolName
from vcity.tickets.drafts import TicketDraft

_OBJECT = TypeAdapter(dict[str, object])
ABSTENTIONS = (
    "couldn't find",
    "could not find",
    "can only help",
    "can't",
    "cannot",
    "don't have",
    "unable",
)


@dataclass(frozen=True, slots=True)
class Transcript:
    callers: tuple[str, ...]
    turns: tuple[Turn, ...]
    tickets: tuple[TicketDraft, ...]
    transfers: tuple[TransferRequest, ...]

    @property
    def calls(self) -> list[ToolCall]:
        return [call for turn in self.turns for call in turn.tool_calls]

    @property
    def spoken(self) -> str:
        return " ".join(turn.text for turn in self.turns)

    @property
    def outcome(self) -> Outcome:
        if self.transfers:
            return Outcome.TRANSFER
        return Outcome.TICKET if self.tickets else Outcome.NONE


def failures(expect: Expect, transcript: Transcript) -> list[str]:
    called = {call.name for call in transcript.calls}
    routes = [_payload(c) for c in transcript.calls if c.name == ToolName.ROUTE_REQUEST]
    spoken = transcript.spoken.casefold()
    problems = [f"did not call {tool}" for tool in expect.tools if tool not in called]
    problems += [f"called {tool}" for tool in expect.not_tools if tool in called]
    if expect.outcome is not None and transcript.outcome is not expect.outcome:
        problems.append(f"outcome {transcript.outcome} instead of {expect.outcome}")
    if expect.department is not None and not any(
        _input(c).get("department") == expect.department for c in transcript.calls
    ):
        problems.append(f"never routed to {expect.department}")
    if expect.ticket_reason is not None and not any(
        r.get("reason") == expect.ticket_reason for r in routes
    ):
        problems.append(f"no ticket with reason {expect.ticket_reason}")
    problems += [
        f"never said {mention!r}"
        for mention in expect.mentions
        if not any(option.casefold() in spoken for option in mention.split("|"))
    ]
    problems += [f"said {phrase!r}" for phrase in expect.never_says if phrase.casefold() in spoken]
    summaries = " ".join(str(_input(c).get("summary", "")) for c in transcript.calls).casefold()
    problems += [
        f"route summary lacks {word!r}"
        for word in expect.route_mentions
        if word.casefold() not in summaries
    ]
    if expect.abstains and (
        transcript.outcome is not Outcome.NONE or not any(p in spoken for p in ABSTENTIONS)
    ):
        problems.append("did not decline or say it couldn't find the answer")
    return problems


def passed(results: Sequence[Sequence[str]]) -> bool:
    return all(not problems for problems in results)


def _payload(call: ToolCall) -> dict[str, object]:
    try:
        return _OBJECT.validate_json(call.result.content)
    except ValidationError:
        return {}


def _input(call: ToolCall) -> dict[str, object]:
    try:
        return _OBJECT.validate_python(call.input)
    except ValidationError:
        return {}
