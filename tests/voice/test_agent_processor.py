import asyncio
from collections.abc import Callable, Sequence

from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    SpeechOutputAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.filters.function_filter import FunctionFilter
from pipecat.processors.frame_processor import FrameDirection
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.tests.utils import SleepFrame, run_test
from tests.agent.anthropic_fake import (
    ANSWER,
    SEARCH_CALL,
    FakeAnthropic,
    NoMatchSearch,
    StalledAfterFirstWords,
    message,
)

from vcity.agent.routing import TransferRequest
from vcity.knowledge.model import Chunk, SearchResult, SearchStatus, SourceKind
from vcity.voice.agent_processor import AgentProcessor, HoldLines
from vcity.voice.call_transfer import CallTransfer

LEAD_IN = SpeechOutputAudioRawFrame(audio=b"\1\0" * 240, sample_rate=24000, num_channels=1)
STILL_WORKING = SpeechOutputAudioRawFrame(audio=b"\2\0" * 240, sample_rate=24000, num_channels=1)
SILENT_SEARCH = message(
    [{"type": "tool_use", "id": "tu_1", "name": "search_city", "input": {"query": "x"}}],
    "tool_use",
)
TRANSFER = message(
    [
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "route_request",
            "input": {"department": "police_non_emergency", "summary": "Wants police"},
        }
    ],
    "tool_use",
)
CHICKENS = Chunk(
    "code:6.20.040#A", SourceKind.CODE, "Code > 6.20.040", "Four hens.", "https://code.test/6.20"
)
CHICKEN_SEARCH = message(
    [{"type": "tool_use", "id": "tu_1", "name": "search_city", "input": {"query": "chickens"}}],
    "tool_use",
)
CHICKEN_ANSWER = message(
    [{"type": "text", "text": "Under section 6.20.040 you can keep four hens."}], "end_turn"
)
FILE_REQUEST = message(
    [
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "route_request",
            "input": {"department": "public_works", "summary": "Pothole", "prefer_ticket": True},
        }
    ],
    "tool_use",
)
FILED = message([{"type": "text", "text": "I've passed on your request."}], "end_turn")
CONNECTING = message([{"type": "text", "text": "Connecting you now."}], "end_turn")


class SlowSearch(NoMatchSearch):
    async def search(
        self,
        query: str,  # noqa: ARG002
        kind: SourceKind | None = None,  # noqa: ARG002
        *,
        caller_id: str | None = None,  # noqa: ARG002
    ) -> SearchResult:
        await asyncio.sleep(0.2)
        return SearchResult(SearchStatus.NO_MATCH, ())


class ChickenSearch(NoMatchSearch):
    async def search(
        self,
        query: str,  # noqa: ARG002
        kind: SourceKind | None = None,  # noqa: ARG002
        *,
        caller_id: str | None = None,  # noqa: ARG002
    ) -> SearchResult:
        return SearchResult(SearchStatus.MATCH, (CHICKENS,))


def caller_said(text: str, *, speculation: bool = False) -> LLMContextFrame:
    return LLMContextFrame(
        LLMContext(messages=[{"role": "user", "content": text}]), speculation=speculation
    )


class PushLog(AgentProcessor):
    def __init__(
        self,
        transfers: CallTransfer,
        on_transfer: Callable[[TransferRequest], None],
        events: list[str],
    ) -> None:
        conversation = FakeAnthropic(iter([TRANSFER, CONNECTING])).conversation(transfers=transfers)
        hold = HoldLines(LEAD_IN, STILL_WORKING, still_working_after=0.05)
        super().__init__(conversation, transfers, hold, on_transfer=on_transfer)
        self.events = events

    async def push_frame(
        self, frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM
    ) -> None:
        self.events.append(type(frame).__name__)
        await super().push_frame(frame, direction)


def processor(fake: FakeAnthropic, **kwargs: object) -> tuple[AgentProcessor, CallTransfer]:
    transfers = CallTransfer()
    search = kwargs.get("search")
    conversation = fake.conversation(
        search=search if isinstance(search, NoMatchSearch) else None, transfers=transfers
    )
    hold = HoldLines(LEAD_IN, STILL_WORKING, still_working_after=0.05)
    return AgentProcessor(conversation, transfers, hold), transfers


async def test_streams_the_answer_between_response_markers() -> None:
    agent, _ = processor(FakeAnthropic(iter([ANSWER])))
    down, _ = await run_test(
        agent,
        frames_to_send=[caller_said("hi")],
        expected_down_frames=[LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame],
    )
    assert isinstance(down[1], LLMTextFrame)
    assert down[1].text == "I couldn't find that in the city's records."


async def test_speculative_turns_are_not_answered() -> None:
    fake = FakeAnthropic(iter([ANSWER]))
    agent, _ = processor(fake)
    await run_test(
        agent, frames_to_send=[caller_said("hi", speculation=True)], expected_down_frames=[]
    )
    assert fake.requests == []


async def test_a_silent_tool_call_gets_the_lead_in_and_a_slow_one_the_hold_line() -> None:
    agent, _ = processor(FakeAnthropic(iter([SILENT_SEARCH, ANSWER])), search=SlowSearch())
    down, _ = await run_test(agent, frames_to_send=[caller_said("When does Target open?")])
    audio = [f for f in down if isinstance(f, SpeechOutputAudioRawFrame)]
    assert audio == [LEAD_IN, STILL_WORKING]


async def test_a_spoken_lead_in_replaces_the_recorded_one() -> None:
    agent, _ = processor(FakeAnthropic(iter([SEARCH_CALL, ANSWER])))
    down, _ = await run_test(agent, frames_to_send=[caller_said("When does Target open?")])
    assert not any(isinstance(f, SpeechOutputAudioRawFrame) for f in down)
    assert next(f.text for f in down if isinstance(f, LLMTextFrame)) == "Let me check that."


def messages_shown(frames: Sequence[Frame]) -> list[object]:
    return [f.data for f in frames if isinstance(f, RTVIServerMessageFrame)]


async def test_a_transfer_is_shown_on_screen_after_the_reply() -> None:
    agent, transfers = processor(FakeAnthropic(iter([TRANSFER, CONNECTING])))
    down, _ = await run_test(agent, frames_to_send=[caller_said("Police please")])
    kinds = [type(f) for f in down]
    assert kinds[-3:] == [LLMFullResponseEndFrame, RTVIServerMessageFrame, EndWorkerFrame]
    shown = down[-2]
    assert isinstance(shown, RTVIServerMessageFrame)
    assert shown.data == {"type": "transfer", "department": "police_non_emergency"}
    assert transfers.requests == []


async def test_a_transfer_interrupted_mid_reply_still_ends_the_call() -> None:
    agent, _ = processor(FakeAnthropic(iter([TRANSFER, StalledAfterFirstWords(CONNECTING)])))
    down, _ = await run_test(
        agent,
        frames_to_send=[caller_said("Police please"), SleepFrame(0.3), InterruptionFrame()],
    )
    assert messages_shown(down) == [{"type": "transfer", "department": "police_non_emergency"}]
    assert sum(isinstance(f, EndWorkerFrame) for f in down) == 1
    kinds = [type(f) for f in down]
    assert kinds.index(InterruptionFrame) < kinds.index(RTVIServerMessageFrame)


async def test_a_shown_transfer_does_not_fire_again_on_a_later_turn() -> None:
    agent, _ = processor(FakeAnthropic(iter([TRANSFER, CONNECTING, ANSWER])))

    async def not_end_worker(frame: Frame) -> bool:
        return not isinstance(frame, EndWorkerFrame)

    down, _ = await run_test(
        Pipeline([agent, FunctionFilter(not_end_worker)]),
        frames_to_send=[caller_said("Police please"), SleepFrame(0.3), caller_said("Thanks")],
    )
    assert len(messages_shown(down)) == 1
    spoken = [f.text for f in down if isinstance(f, LLMTextFrame)]
    assert spoken[-1] == "I couldn't find that in the city's records."


async def test_a_transfer_is_reported_before_the_worker_is_ended() -> None:
    events: list[str] = []
    reported: list[TransferRequest] = []

    def on_transfer(request: TransferRequest) -> None:
        reported.append(request)
        events.append("reported")

    agent = PushLog(CallTransfer(), on_transfer, events)
    await run_test(agent, frames_to_send=[caller_said("Police please")])
    assert [r.department.value for r in reported] == ["police_non_emergency"]
    assert events.index("reported") < events.index("EndWorkerFrame")


async def test_the_sources_an_answer_names_are_shown() -> None:
    agent, _ = processor(
        FakeAnthropic(iter([CHICKEN_SEARCH, CHICKEN_ANSWER])), search=ChickenSearch()
    )
    down, _ = await run_test(agent, frames_to_send=[caller_said("Chickens?")])
    assert messages_shown(down) == [
        {
            "type": "sources",
            "items": [{"label": "Municipal Code § 6.20.040", "url": "https://code.test/6.20"}],
        }
    ]


async def test_a_filed_request_shows_its_reference() -> None:
    agent, _ = processor(FakeAnthropic(iter([FILE_REQUEST, FILED])))
    down, _ = await run_test(agent, frames_to_send=[caller_said("Pothole on Main")])
    (shown,) = [f.data for f in down if isinstance(f, RTVIServerMessageFrame)]
    assert shown == {
        "type": "request",
        "reference": shown["reference"],
        "department": "Public Works",
        "next_open": None,
    }
    assert len(shown["reference"]) == 6
