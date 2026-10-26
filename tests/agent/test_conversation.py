import asyncio
import contextlib
import json

from pydantic import BaseModel, ConfigDict
from tests.agent.anthropic_fake import (
    ANSWER,
    SEARCH_CALL,
    DroppedAfterFirstWords,
    FakeAnthropic,
    GatedSearch,
    RaisingSearch,
    RecordingSearch,
    message,
)

from vcity.agent.conversation import (
    CUT_OFF_MARK,
    FALLBACK_REPLY,
    MAX_TOOL_ROUNDS,
    Conversation,
    Done,
    Speech,
    ToolsRunning,
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
    assert fake.requests[0].thinking == {"type": "adaptive"}


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


async def interrupted_mid_tool(fake: FakeAnthropic, search: GatedSearch) -> Conversation:
    conversation = fake.conversation(search=search)
    task = asyncio.create_task(conversation.reply("When does Target open?"))
    await search.started.wait()
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    search.release.set()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()
    return conversation


async def test_a_tool_run_cut_off_by_barge_in_finishes_and_is_recorded() -> None:
    search = GatedSearch()
    conversation = await interrupted_mid_tool(FakeAnthropic(iter([SEARCH_CALL])), search)
    history: list[object] = list(conversation.history)
    assert [m.role for m in parse(history)] == ["user", "assistant", "user"]
    assert parse(history)[-1].blocks("tool_result")[0].tool_use_id == "tu_1"
    assert unpaired_tool_uses(history) == []
    assert search.queries == ["target"]


async def test_the_turn_after_an_interrupted_tool_run_sees_its_result() -> None:
    fake = FakeAnthropic(iter([SEARCH_CALL, ANSWER]))
    conversation = await interrupted_mid_tool(fake, GatedSearch())
    conversation.heard("")
    await conversation.reply("Are you there?")
    sent = fake.requests[1].messages
    assert [m.role for m in parse(sent)] == ["user", "assistant", "user", "user"]
    assert unpaired_tool_uses(sent) == []


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


async def test_stream_speaks_as_it_goes_and_flags_the_tool_round() -> None:
    fake = FakeAnthropic(iter([SEARCH_CALL, ANSWER]))
    events = [e async for e in fake.conversation().stream("When does Target open?")]
    assert events[:2] == [Speech("Let me check that."), ToolsRunning(("search_city",))]
    assert events[2] == Speech("I couldn't find that in the city's records.")
    assert isinstance(events[-1], Done)


async def test_an_interrupted_answer_keeps_only_what_was_heard() -> None:
    fake = FakeAnthropic(iter([ANSWER, ANSWER]))
    conversation = fake.conversation()
    await conversation.reply("first")
    conversation.heard("I couldn't")
    assert parse(list(conversation.history))[-1] == Message(
        role="assistant", content=f"I couldn't{CUT_OFF_MARK}"
    )
    await conversation.reply("second")
    assert [m.role for m in parse(fake.requests[1].messages)] == ["user", "assistant", "user"]


async def test_speech_cut_off_during_the_tool_round_is_recorded_after_its_result() -> None:
    conversation = await interrupted_mid_tool(FakeAnthropic(iter([SEARCH_CALL])), GatedSearch())
    conversation.heard("Let me check")
    roles = [m.role for m in parse(list(conversation.history))]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert unpaired_tool_uses(list(conversation.history)) == []


async def test_a_connection_dropped_mid_answer_speaks_the_fallback_and_recovers() -> None:
    fake = FakeAnthropic(iter([DroppedAfterFirstWords(ANSWER), ANSWER]))
    conversation = fake.conversation()
    events = [e async for e in conversation.stream("first")]
    done = events[-1]
    assert isinstance(done, Done)
    assert done.turn.text == FALLBACK_REPLY
    assert events[-2] == Speech(FALLBACK_REPLY)
    assert [m.role for m in parse(list(conversation.history))] == ["user"]
    second = await conversation.reply("second")
    assert second.text == "I couldn't find that in the city's records."


async def test_what_was_heard_is_dropped_once_the_next_turn_has_started() -> None:
    fake = FakeAnthropic(iter([ANSWER, ANSWER]))
    conversation = fake.conversation()
    await conversation.reply("first")
    second = conversation.stream("second")
    await anext(second)
    conversation.heard("I couldn't")
    async for _ in second:
        pass
    assert [m.role for m in parse(list(conversation.history))] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


async def test_what_was_heard_is_recorded_once_per_turn() -> None:
    fake = FakeAnthropic(iter([ANSWER]))
    conversation = fake.conversation()
    await conversation.reply("first")
    conversation.heard("I couldn't")
    conversation.heard("I couldn't find")
    assert parse(list(conversation.history))[-1] == Message(
        role="assistant", content=f"I couldn't{CUT_OFF_MARK}"
    )


async def test_warming_up_sends_the_cached_prompt_without_touching_history() -> None:
    fake = FakeAnthropic(iter([ANSWER]))
    conversation = fake.conversation()
    await conversation.warm_up()
    assert fake.requests[0].system[0].cache_control == {"type": "ephemeral"}
    assert conversation.history == []


async def test_a_failed_warm_up_is_not_fatal() -> None:
    fake = FakeAnthropic(iter([500]))
    await fake.conversation().warm_up()
    assert len(fake.requests) == 1
