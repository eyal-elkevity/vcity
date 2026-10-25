import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from datetime import datetime

import httpx2
from anthropic import APIError, AsyncAnthropic
from anthropic.types import (
    ContentBlock,
    ContentBlockParam,
    Message,
    MessageParam,
    TextBlockParam,
    ThinkingConfigParam,
    ToolResultBlockParam,
    ToolUseBlock,
)

from vcity.agent.prompt import clock_note
from vcity.agent.tools import TOOLS, CallContext, Toolbox, ToolResult

_log = logging.getLogger(__name__)

AGENT_MODEL = "claude-haiku-5-5"
THINKING: ThinkingConfigParam = {"type": "adaptive"}
MAX_TOOL_ROUNDS = 5
FALLBACK_REPLY = "Sorry, I'm having trouble with that. Could you say it another way?"
_NOT_RUN_REPLY = "Not run: the reply was cut off."
_TOOL_FAILED_REPLY = "The tool failed. Apologize and offer another way to help."


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    input: object
    result: ToolResult


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
            self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True, slots=True)
class Turn:
    text: str
    tool_calls: tuple[ToolCall, ...]
    llm_seconds: tuple[float, ...]
    usage: Usage


@dataclass
class _Record:
    calls: list[ToolCall] = field(default_factory=list[ToolCall])
    timings: list[float] = field(default_factory=list[float])
    spoken: list[str] = field(default_factory=list[str])
    usage: Usage = field(default_factory=Usage)

    def add(self, message: Message, seconds: float) -> None:
        self.timings.append(seconds)
        self.usage += Usage(
            message.usage.input_tokens,
            message.usage.cache_read_input_tokens or 0,
            message.usage.cache_creation_input_tokens or 0,
            message.usage.output_tokens,
        )
        self.spoken.extend(b.text for b in message.content if b.type == "text")

    @property
    def text(self) -> str:
        return " ".join(part.strip() for part in self.spoken if part.strip())

    def turn(self, text: str) -> Turn:
        return Turn(text, tuple(self.calls), tuple(self.timings), self.usage)


@dataclass(frozen=True, slots=True)
class Speech:
    text: str


@dataclass(frozen=True, slots=True)
class ToolsRunning:
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Done:
    turn: Turn


type Event = Speech | ToolsRunning | Done


@dataclass
class Conversation:
    client: AsyncAnthropic
    toolbox: Toolbox
    system: str
    call: CallContext
    clock: Callable[[], datetime]
    model: str = AGENT_MODEL
    max_tokens: int = 2048
    history: list[MessageParam] = field(default_factory=list[MessageParam])
    _answered: bool = field(default=False, init=False)
    _hearing: bool = field(default=False, init=False)

    async def reply(self, caller: str) -> Turn:
        async with aclosing(self.stream(caller)) as events:
            async for event in events:
                if isinstance(event, Done):
                    return event.turn
        raise AssertionError

    async def stream(self, caller: str) -> AsyncGenerator[Event]:
        self._hearing = False
        try:
            async with aclosing(self._turn(caller)) as events:
                async for event in events:
                    yield event
        finally:
            self._hearing = True

    async def _turn(self, caller: str) -> AsyncGenerator[Event]:
        self.history.append({"role": "user", "content": caller})
        self._answered = False
        record = _Record()
        for _ in range(MAX_TOOL_ROUNDS):
            message: Message | None = None
            async with aclosing(self._ask()) as rounds:
                async for item in rounds:
                    if isinstance(item, Speech):
                        yield item
                    else:
                        message, seconds = item
                        record.add(message, seconds)
            if message is None:
                break
            assistant: MessageParam = {
                "role": "assistant",
                "content": [_param(b) for b in message.content],
            }
            uses = [b for b in message.content if isinstance(b, ToolUseBlock)]
            if uses and message.stop_reason != "tool_use":
                self.history += [assistant, _not_run(uses)]
                break
            if not uses:
                self.history.append(assistant)
                self._answered = True
                if not record.text:
                    yield Speech(FALLBACK_REPLY)
                yield Done(record.turn(record.text or FALLBACK_REPLY))
                return
            yield ToolsRunning(tuple(u.name for u in uses))
            record.calls.extend(await self._commit_tools(assistant, uses))
        yield Speech(FALLBACK_REPLY)
        yield Done(record.turn(FALLBACK_REPLY))

    async def _ask(self) -> AsyncGenerator[Speech | tuple[Message, float]]:
        started = time.perf_counter()
        try:
            async with self.client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                thinking=THINKING,
                system=self._system_blocks(),
                tools=TOOLS,
                messages=self.history,
            ) as response:
                async for text in response.text_stream:
                    yield Speech(text)
                message = await response.get_final_message()
        except (APIError, httpx2.TransportError) as error:
            _log.warning("model call failed: %s", type(error).__name__)
            return
        yield message, time.perf_counter() - started

    def heard(self, spoken: str) -> None:
        if not self._hearing:
            return
        self._hearing = False
        if self._answered:
            self.history.pop()
            self._answered = False
        if spoken and self.history[-1]["role"] == "user":
            self.history.append({"role": "assistant", "content": spoken})

    def _system_blocks(self) -> list[TextBlockParam]:
        return [
            {"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": clock_note(self.clock())},
        ]

    async def _commit_tools(
        self, assistant: MessageParam, uses: list[ToolUseBlock]
    ) -> list[ToolCall]:
        running = asyncio.create_task(self._run_tools(uses))
        try:
            await asyncio.shield(running)
        finally:
            # A barge-in must not undo a ticket or transfer the caller already triggered.
            calls = await running
            self.history += [assistant, _results(uses, [c.result for c in calls])]
        return calls

    async def _run_tools(self, uses: list[ToolUseBlock]) -> list[ToolCall]:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._run_tool(u)) for u in uses]
        return [
            ToolCall(u.name, u.input, task.result()) for u, task in zip(uses, tasks, strict=True)
        ]

    async def _run_tool(self, use: ToolUseBlock) -> ToolResult:
        try:
            return await self.toolbox.run(use.name, use.input, self.call)
        except Exception as error:  # noqa: BLE001
            _log.warning("tool %s failed: %s", use.name, type(error).__name__)
            return ToolResult(_TOOL_FAILED_REPLY, is_error=True)


def _param(block: ContentBlock) -> ContentBlockParam:
    match block.type:
        case "text":
            return {"type": "text", "text": block.text}
        case "tool_use":
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        case "thinking":
            return {"type": "thinking", "thinking": block.thinking, "signature": block.signature}
        case "redacted_thinking":
            return {"type": "redacted_thinking", "data": block.data}
        case _:
            raise ValueError(f"unexpected content block {block.type}")


def _tool_result(use: ToolUseBlock, result: ToolResult) -> ToolResultBlockParam:
    return {
        "type": "tool_result",
        "tool_use_id": use.id,
        "content": result.content,
        "is_error": result.is_error,
    }


def _results(uses: list[ToolUseBlock], results: list[ToolResult]) -> MessageParam:
    return {
        "role": "user",
        "content": [_tool_result(u, r) for u, r in zip(uses, results, strict=True)],
    }


def _not_run(uses: list[ToolUseBlock]) -> MessageParam:
    return _results(uses, [ToolResult(_NOT_RUN_REPLY, is_error=True)] * len(uses))
