import json
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict

from vcity.agent.conversation import FALLBACK_REPLY, MAX_TOOL_ROUNDS, Conversation
from vcity.agent.routing import RecordingTransfer
from vcity.agent.tools import CallContext, Toolbox
from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.config.policy import corona_policy
from vcity.knowledge.model import SearchResult, SearchStatus, SourceKind
from vcity.tickets.sinks import MockSink

LA = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 27, 10, 0, tzinfo=LA)


class NoMatchSearch:
    async def search(
        self,
        query: str,  # noqa: ARG002
        kind: SourceKind | None = None,  # noqa: ARG002
        *,
        caller_id: str | None = None,  # noqa: ARG002
    ) -> SearchResult:
        return SearchResult(SearchStatus.NO_MATCH, ())


def message(content: list[dict[str, object]], stop_reason: str) -> dict[str, object]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100},
    }


SEARCH_CALL = message(
    [
        {"type": "text", "text": "Let me check that."},
        {"type": "tool_use", "id": "tu_1", "name": "search_city", "input": {"query": "target"}},
    ],
    "tool_use",
)
ANSWER = message(
    [{"type": "text", "text": "I couldn't find that in the city's records."}], "end_turn"
)


class SystemBlock(BaseModel):
    type: str
    text: str
    cache_control: dict[str, str] | None = None


class Request(BaseModel):
    model_config = ConfigDict(extra="ignore")

    system: list[SystemBlock]
    thinking: dict[str, str]
    messages: list[object]


class FakeAnthropic:
    def __init__(self, replies: Iterator[dict[str, object]]) -> None:
        self.replies = replies
        self.requests: list[Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(Request.model_validate_json(request.content))
        return httpx2.Response(200, json=next(self.replies))

    def conversation(self, model: str = "claude-haiku-5-5") -> Conversation:
        client = AsyncAnthropic(
            api_key="test",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self)),
        )
        toolbox = Toolbox(
            search=NoMatchSearch(),
            directory=load_directory(DEFAULT_DIRECTORY_PATH, transfer_to="+15555550100"),
            policy=corona_policy(),
            tickets=MockSink(),
            transfers=RecordingTransfer(),
            clock=lambda: NOW,
        )
        return Conversation(
            client, toolbox, "SYSTEM PROMPT", CallContext("CA1", None), lambda: NOW, model=model
        )


async def test_tool_round_trip_speaks_both_parts_and_records_the_call() -> None:
    fake = FakeAnthropic(iter([SEARCH_CALL, ANSWER]))
    turn = await fake.conversation().reply("When does Target open?")
    assert turn.text == "Let me check that. I couldn't find that in the city's records."
    assert [(c.name, c.input) for c in turn.tool_calls] == [("search_city", {"query": "target"})]
    assert turn.usage.cache_read_tokens == 200
    assert len(turn.llm_seconds) == 2
    assert '"tool_use_id": "tu_1"' in json.dumps(fake.requests[1].messages[-1])


async def test_system_prompt_is_cached_and_the_clock_is_not() -> None:
    fake = FakeAnthropic(iter([ANSWER]))
    await fake.conversation().reply("hi")
    first, second = fake.requests[0].system
    assert (first.text, first.cache_control) == ("SYSTEM PROMPT", {"type": "ephemeral"})
    assert second.cache_control is None
    assert second.text.startswith("Current date and time in Corona: Tuesday, October 27, 2026")
    assert fake.requests[0].thinking == {"type": "disabled"}


async def test_sonnet_gets_its_lowest_thinking_setting() -> None:
    fake = FakeAnthropic(iter([ANSWER]))
    await fake.conversation("claude-sonnet-5-5").reply("hi")
    assert fake.requests[0].thinking == {"type": "between_tools"}


async def test_runaway_tool_loops_stop_with_a_fallback() -> None:
    silent_call = message(
        [{"type": "tool_use", "id": "tu", "name": "search_city", "input": {"query": "x"}}],
        "tool_use",
    )
    fake = FakeAnthropic(iter([silent_call] * MAX_TOOL_ROUNDS))
    turn = await fake.conversation().reply("loop")
    assert turn.text == FALLBACK_REPLY
    assert len(fake.requests) == MAX_TOOL_ROUNDS
