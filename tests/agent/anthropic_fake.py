import asyncio
import json
from collections.abc import AsyncIterator, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx2
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, TypeAdapter

from vcity.agent.conversation import (
    Conversation,
)
from vcity.agent.routing import RecordingTransfer, TransferPort
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


class GatedSearch(NoMatchSearch):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.queries: list[str] = []

    async def search(
        self,
        query: str,
        kind: SourceKind | None = None,  # noqa: ARG002
        *,
        caller_id: str | None = None,  # noqa: ARG002
    ) -> SearchResult:
        self.queries.append(query)
        self.started.set()
        await self.release.wait()
        return SearchResult(SearchStatus.NO_MATCH, ())


@dataclass(frozen=True, slots=True)
class DroppedAfterFirstWords:
    body: dict[str, object]


@dataclass(frozen=True, slots=True)
class StalledAfterFirstWords:
    body: dict[str, object]


type CutOff = DroppedAfterFirstWords | StalledAfterFirstWords
type Reply = dict[str, object] | int | CutOff


BLOCKS = TypeAdapter(list[dict[str, object]])


def sse(body: dict[str, object]) -> bytes:
    events: list[Mapping[str, object]] = [
        {"type": "message_start", "message": {**body, "content": [], "stop_reason": None}}
    ]
    for index, block in enumerate(BLOCKS.validate_python(body["content"])):
        events += [
            {"type": "content_block_start", "index": index, "content_block": _empty(block)},
            *({"type": "content_block_delta", "index": index, "delta": d} for d in _deltas(block)),
            {"type": "content_block_stop", "index": index},
        ]
    events += [
        {
            "type": "message_delta",
            "delta": {"stop_reason": body["stop_reason"], "stop_sequence": None},
            "usage": body["usage"],
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


class _CutStream(httpx2.AsyncByteStream):
    def __init__(self, cut: CutOff) -> None:
        events = sse(cut.body).split(b"\n\n")
        first_text = next(i for i, e in enumerate(events) if b"text_delta" in e)
        self.head = b"\n\n".join(events[: first_text + 1]) + b"\n\n"
        self.stalls = isinstance(cut, StalledAfterFirstWords)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.head
        if self.stalls:
            await asyncio.Event().wait()
        raise httpx2.ReadError("connection reset")


def _empty(block: dict[str, object]) -> dict[str, object]:
    match block["type"]:
        case "text":
            return {"type": "text", "text": ""}
        case "tool_use":
            return {**block, "input": {}}
        case _:
            return {**block, "thinking": "", "signature": ""}


def _deltas(block: dict[str, object]) -> list[dict[str, object]]:
    match block["type"]:
        case "text":
            return [{"type": "text_delta", "text": block["text"]}]
        case "tool_use":
            return [{"type": "input_json_delta", "partial_json": json.dumps(block["input"])}]
        case _:
            return [
                {"type": "thinking_delta", "thinking": block["thinking"]},
                {"type": "signature_delta", "signature": block["signature"]},
            ]


class FakeAnthropic:
    def __init__(self, replies: Iterator[Reply]) -> None:
        self.replies = replies
        self.requests: list[Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(Request.model_validate_json(request.content))
        match next(self.replies):
            case int(status):
                return httpx2.Response(status, json={"type": "error", "error": {}})
            case DroppedAfterFirstWords() | StalledAfterFirstWords() as cut:
                return httpx2.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=_CutStream(cut)
                )
            case body:
                return httpx2.Response(
                    200, headers={"content-type": "text/event-stream"}, content=sse(body)
                )

    def conversation(
        self,
        model: str = "claude-haiku-5-5",
        search: CitySearchPort | None = None,
        transfers: TransferPort | None = None,
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
            transfers=transfers or RecordingTransfer(),
            clock=lambda: NOW,
        )
        return Conversation(
            client, toolbox, "SYSTEM PROMPT", CallContext("CA1", None), lambda: NOW, model=model
        )
