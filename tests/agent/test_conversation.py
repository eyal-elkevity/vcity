import asyncio
import contextlib
import json
from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict

from vcity.agent.conversation import FALLBACK_REPLY, MAX_TOOL_ROUNDS, Conversation
from vcity.agent.routing import RecordingTransfer
from vcity.agent.tools import CallContext, CitySearchPort, Toolbox
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


class RecordingSearch(NoMatchSearch):
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(
        self,
        query: str,
        kind: SourceKind | None = None,  # noqa: ARG002
        *,
        caller_id: str | None = None,  # noqa: ARG002
    ) -> SearchResult:
        self.queries.append(query)
        return SearchResult(SearchStatus.NO_MATCH, ())


class RaisingSearch(NoMatchSearch):
    async def search(
        self,
        query: str,  # noqa: ARG002
        kind: SourceKind | None = None,  # noqa: ARG002
        *,
        caller_id: str | None = None,  # noqa: ARG002
    ) -> SearchResult:
        raise RuntimeError("index offline")


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


class HangingSearch(NoMatchSearch):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def search(
        self,
        query: str,  # noqa: ARG002
        kind: SourceKind | None = None,  # noqa: ARG002
        *,
        caller_id: str | None = None,  # noqa: ARG002
    ) -> SearchResult:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError


type Reply = dict[str, object] | int


class FakeAnthropic:
    def __init__(self, replies: Iterator[Reply]) -> None:
        self.replies = replies
        self.requests: list[Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(Request.model_validate_json(request.content))
        match next(self.replies):
            case int(status):
                return httpx2.Response(status, json={"type": "error", "error": {}})
            case body:
                return httpx2.Response(200, json=body)

    def conversation(
        self, model: str = "claude-haiku-5-5", search: CitySearchPort | None = None
    ) -> Conversation:
        client = AsyncAnthropic(
            api_key="test",
            max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(self)),
        )
        toolbox = Toolbox(
            search=search or NoMatchSearch(),
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


async def test_truncated_round_pairs_its_tool_use_without_running_it() -> None:
    truncated = message(
        [
            {"type": "text", "text": "Let me check that."},
            {"type": "tool_use", "id": "tu_1", "name": "search_city", "input": {"query": "x"}},
        ],
        "max_tokens",
    )
    search = RecordingSearch()
    fake = FakeAnthropic(iter([truncated, ANSWER]))
    conversation = fake.conversation(search=search)
    first = await conversation.reply("first")
    assert first.text == FALLBACK_REPLY
    assert first.tool_calls == ()
    assert search.queries == []
    await conversation.reply("second")
    paired = json.dumps(fake.requests[1].messages[-2])
    assert '"tool_use_id": "tu_1"' in paired
    assert '"is_error": true' in paired


async def test_exhausted_tool_budget_speaks_the_fallback_not_the_preamble() -> None:
    preamble_call = message(
        [
            {"type": "text", "text": "Let me check that."},
            {"type": "tool_use", "id": "tu", "name": "search_city", "input": {"query": "x"}},
        ],
        "tool_use",
    )
    fake = FakeAnthropic(iter([preamble_call] * MAX_TOOL_ROUNDS))
    turn = await fake.conversation().reply("loop")
    assert turn.text == FALLBACK_REPLY
    assert len(turn.tool_calls) == MAX_TOOL_ROUNDS


async def test_crashing_tool_returns_an_error_result_and_the_turn_completes() -> None:
    fake = FakeAnthropic(iter([SEARCH_CALL, ANSWER]))
    turn = await fake.conversation(search=RaisingSearch()).reply("When does Target open?")
    assert turn.text == "Let me check that. I couldn't find that in the city's records."
    assert turn.tool_calls[0].result.is_error
    paired = json.dumps(fake.requests[1].messages[-1])
    assert '"tool_use_id": "tu_1"' in paired
    assert '"is_error": true' in paired


class Block(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    id: str | None = None
    tool_use_id: str | None = None


class Message(BaseModel):
    role: str
    content: str | list[Block]

    def blocks(self, kind: str) -> list[Block]:
        return [b for b in self.content if b.type == kind] if isinstance(self.content, list) else []


def parse(messages: list[object]) -> list[Message]:
    return [Message.model_validate(m) for m in messages]


def unpaired_tool_uses(history: list[object]) -> list[str | None]:
    messages = parse(history)
    following = [*messages[1:], Message(role="user", content=[])]
    return [
        use.id
        for current, after in zip(messages, following, strict=True)
        for use in current.blocks("tool_use")
        if use.id not in {r.tool_use_id for r in after.blocks("tool_result")}
    ]


async def test_api_failure_speaks_the_fallback_and_the_next_turn_recovers() -> None:
    fake = FakeAnthropic(iter([500, ANSWER]))
    conversation = fake.conversation()
    failed = await conversation.reply("first")
    assert failed.text == FALLBACK_REPLY
    assert failed.llm_seconds == ()
    second = await conversation.reply("second")
    assert second.text == "I couldn't find that in the city's records."
    assert [m.role for m in parse(fake.requests[1].messages)] == ["user", "user"]


async def test_cancelled_tool_run_leaves_no_unpaired_tool_use() -> None:
    search = HangingSearch()
    fake = FakeAnthropic(iter([SEARCH_CALL]))
    conversation = fake.conversation(search=search)
    task = asyncio.create_task(conversation.reply("When does Target open?"))
    await search.started.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
    assert [m.role for m in parse(list(conversation.history))] == ["user"]
    assert unpaired_tool_uses(list(conversation.history)) == []


async def test_thinking_block_survives_the_round_trip_before_its_tool_use() -> None:
    thinking_call = message(
        [
            {"type": "thinking", "thinking": "Search first.", "signature": "sig_1"},
            {"type": "tool_use", "id": "tu_1", "name": "search_city", "input": {"query": "x"}},
        ],
        "tool_use",
    )
    fake = FakeAnthropic(iter([thinking_call, ANSWER]))
    await fake.conversation("claude-sonnet-5-5").reply("hi")
    assistant = parse(fake.requests[1].messages)[-2]
    assert assistant.role == "assistant"
    assert isinstance(assistant.content, list)
    assert [b.type for b in assistant.content] == ["thinking", "tool_use"]
    assert assistant.content[0].model_dump(exclude_none=True) == {
        "type": "thinking",
        "thinking": "Search first.",
        "signature": "sig_1",
    }
