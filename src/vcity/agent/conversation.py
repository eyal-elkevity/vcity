import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from anthropic import APIError, AsyncAnthropic
from anthropic.types import (
    ContentBlock,
    ContentBlockParam,
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
# Claude Sonnet 5.5 rejects disabled thinking; between_tools is its lowest setting.
_LOWEST_THINKING: dict[str, ThinkingConfigParam] = {"claude-sonnet-5-5": {"type": "between_tools"}}
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
class Conversation:
    client: AsyncAnthropic
    toolbox: Toolbox
    system: str
    call: CallContext
    clock: Callable[[], datetime]
    model: str = AGENT_MODEL
    max_tokens: int = 512
    history: list[MessageParam] = field(default_factory=list[MessageParam])

    async def reply(self, caller: str) -> Turn:
        self.history.append({"role": "user", "content": caller})
        calls: list[ToolCall] = []
        timings: list[float] = []
        spoken: list[str] = []
        usage = Usage()
        for _ in range(MAX_TOOL_ROUNDS):
            started = time.perf_counter()
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    thinking=_LOWEST_THINKING.get(self.model, {"type": "disabled"}),
                    system=self._system_blocks(),
                    tools=TOOLS,
                    messages=self.history,
                )
            except APIError as error:
                _log.warning("model call failed: %s", type(error).__name__)
                return Turn(FALLBACK_REPLY, tuple(calls), tuple(timings), usage)
            timings.append(time.perf_counter() - started)
            usage += Usage(
                response.usage.input_tokens,
                response.usage.cache_read_input_tokens or 0,
                response.usage.cache_creation_input_tokens or 0,
                response.usage.output_tokens,
            )
            assistant: MessageParam = {
                "role": "assistant",
                "content": [_param(b) for b in response.content],
            }
            uses = [b for b in response.content if isinstance(b, ToolUseBlock)]
            if uses and response.stop_reason != "tool_use":
                self.history += [assistant, _not_run(uses)]
                return Turn(FALLBACK_REPLY, tuple(calls), tuple(timings), usage)
            spoken.extend(b.text for b in response.content if b.type == "text")
            if not uses:
                self.history.append(assistant)
                text = " ".join(part.strip() for part in spoken if part.strip())
                return Turn(text or FALLBACK_REPLY, tuple(calls), tuple(timings), usage)
            results = await self._run_tools(uses)
            calls.extend(results)
            self.history += [assistant, _results(uses, [c.result for c in results])]
        return Turn(FALLBACK_REPLY, tuple(calls), tuple(timings), usage)

    def _system_blocks(self) -> list[TextBlockParam]:
        return [
            {"type": "text", "text": self.system, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": clock_note(self.clock())},
        ]

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
