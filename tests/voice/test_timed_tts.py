import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from typing import TYPE_CHECKING, cast, override

import pytest
from loguru import logger
from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSStoppedFrame,
    TTSTextFrame,
)
from pipecat.tests.utils import SleepFrame, run_test
from pipecat.utils.context.word_timestamp_tracker import WordTimestamp
from websockets.protocol import State

from vcity.voice.timed_tts import SentencePacer, TimedDeepgramTTSService

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

RATE = 24000
ONE_SECOND = bytes(2 * RATE)


def test_words_are_released_once_the_audio_reaches_them() -> None:
    pacer = SentencePacer(["Hens", "are", "fine."], seconds_per_letter=0.1)
    assert pacer.audio(0.1) == [("Hens", 0.0)]
    assert pacer.audio(0.5) == [("are", pytest.approx(0.5))]
    assert pacer.audio(0.4) == [("fine.", pytest.approx(0.9))]


def test_finishing_fits_unreleased_words_into_the_real_audio_length() -> None:
    pacer = SentencePacer(["Hens", "are", "allowed."], seconds_per_letter=1.0)
    pacer.audio(0.6)
    assert pacer.finish() == [("are", pytest.approx(0.2)), ("allowed.", pytest.approx(0.4))]


class _Socket:
    def __init__(self) -> None:
        self.state = State.OPEN
        self.sent: list[dict[str, str]] = []
        self.fail_on_text: str | None = None
        self.before_failing: Callable[[], Awaitable[None]] | None = None
        self.incoming: list[str | bytes] = []

    async def send(self, message: str) -> None:
        payload: dict[str, str] = json.loads(message)
        if self.fail_on_text is not None and payload.get("text") == self.fail_on_text:
            self.fail_on_text = None
            if self.before_failing is not None:
                await self.before_failing()
            raise ConnectionError("socket lost")
        self.sent.append(payload)

    async def __aiter__(self) -> AsyncIterator[str | bytes]:
        for message in self.incoming:
            yield message

    @property
    def flushes(self) -> int:
        return sum(payload["type"] == "Flush" for payload in self.sent)


class _Service(TimedDeepgramTTSService):
    def __init__(self) -> None:
        self.now = 0.0
        super().__init__(
            api_key="k",
            sample_rate=RATE,
            voice="aura-2-thalia-en",
            text_transforms=[],
            clock=lambda: self.now,
        )
        self.socket = _Socket()
        self._websocket = cast("ClientConnection", self.socket)
        self.words: list[tuple[str, float]] = []
        self.queued: list[Frame] = []
        self.removed: list[str | None] = []

    @override
    def get_active_audio_context_id(self) -> str | None:
        return "turn"

    @override
    async def append_to_audio_context(
        self, context_id: str | None, frame: Frame | WordTimestamp | None
    ) -> None:
        if isinstance(frame, Frame):
            self.queued.append(frame)

    @override
    async def remove_audio_context(self, context_id: str | None) -> None:
        self.removed.append(context_id)

    @override
    async def add_word_timestamps(
        self,
        word_times: list[tuple[str, float]],
        context_id: str | None = None,
        includes_inter_frame_spaces: bool | None = None,
        pre_merge_tokens: bool = False,
    ) -> None:
        self.words += word_times

    async def receive(self) -> None:
        await self._receive_messages()


async def _speak(service: _Service, text: str) -> list[Frame | None]:
    async with aclosing(service.run_tts(text, "turn")) as stream:
        return [frame async for frame in stream]


async def test_each_sentence_is_flushed_so_its_audio_length_is_known() -> None:
    service = _Service()
    await _speak(service, "Hens are fine.")
    assert service.socket.sent == [{"type": "Speak", "text": "Hens are fine."}, {"type": "Flush"}]


async def test_the_second_sentence_is_timed_after_the_first() -> None:
    service = _Service()
    await _speak(service, "Yes.")
    await _speak(service, "Hens are fine.")
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    assert [word for word, _ in service.words] == ["Yes.", "Hens", "are", "fine."]
    assert service.words[1][1] == pytest.approx(1.0)


async def test_a_sentence_after_a_gap_keeps_the_turn_clock() -> None:
    service = _Service()
    await _speak(service, "Yes.")
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    await _speak(service, "Hens are fine.")
    await service.on_audio(ONE_SECOND)
    await service.flush_audio("turn")
    await service.on_control({"type": "Flushed"})
    assert service.words[1] == ("Hens", pytest.approx(1.0))


async def test_a_sentence_that_fails_to_send_is_not_queued() -> None:
    service = _Service()
    service.socket.fail_on_text = "Lost."
    frames = await _speak(service, "Lost.")
    assert any(isinstance(frame, ErrorFrame) for frame in frames)
    await _speak(service, "Hens are fine.")
    await service.on_audio(ONE_SECOND)
    await service.flush_audio("turn")
    await service.on_control({"type": "Flushed"})
    assert [word for word, _ in service.words] == ["Hens", "are", "fine."]
    assert service.removed == ["turn"]


async def test_the_turn_ends_when_its_last_sentence_is_flushed() -> None:
    service = _Service()
    await _speak(service, "Yes.")
    await service.flush_audio("turn")
    assert service.removed == []
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    assert service.removed == ["turn"]
    assert isinstance(service.queued[-1], TTSStoppedFrame)


async def test_audio_after_an_interruption_is_dropped_until_deepgram_clears() -> None:
    service = _Service()
    await _speak(service, "Hens are fine.")
    await service.on_audio_context_interrupted("turn")
    await service.on_audio(ONE_SECOND)
    await service.on_control({"type": "Flushed"})
    assert service.words == []
    await service.on_control({"type": "Cleared"})
    await _speak(service, "Okay.")
    await service.on_audio(ONE_SECOND)
    assert service.words == [("Okay.", 0.0)]


async def test_a_failed_send_after_an_interruption_reports_the_send_error() -> None:
    service = _Service()
    await _speak(service, "Yes.")
    service.socket.fail_on_text = "Lost."
    service.socket.before_failing = lambda: service.on_audio_context_interrupted("turn")
    frames = await _speak(service, "Lost.")
    errors = [frame for frame in frames if isinstance(frame, ErrorFrame)]
    assert "ConnectionError" in errors[0].error


async def _speak_flushed_sentences(service: _Service, count: int) -> None:
    for _ in range(count):
        await _speak(service, "Ok.")
        await service.on_audio(ONE_SECOND)
        await service.on_control({"type": "Flushed"})


async def test_sentences_past_the_flush_budget_are_spoken_without_a_flush() -> None:
    service = _Service()
    await _speak_flushed_sentences(service, 18)
    await _speak(service, "Hens are fine.")
    await _speak(service, "Yes.")
    assert service.socket.flushes == 18
    assert service.socket.sent[-1] == {"type": "Speak", "text": "Yes."}


async def test_sentences_are_flushed_again_once_the_window_passes() -> None:
    service = _Service()
    await _speak_flushed_sentences(service, 18)
    service.now = 60.0
    await _speak(service, "Hens are fine.")
    assert service.socket.sent[-1] == {"type": "Flush"}


async def test_the_turn_end_flush_times_merged_sentences_across_their_audio() -> None:
    service = _Service()
    await _speak_flushed_sentences(service, 18)
    await service.flush_audio("turn")
    service.words.clear()
    await _speak(service, "Yes.")
    await _speak(service, "Hens are fine.")
    await service.on_audio(ONE_SECOND)
    await service.on_audio(ONE_SECOND)
    await service.flush_audio("turn")
    assert service.socket.flushes == 19
    await service.on_control({"type": "Flushed"})
    assert service.words == [
        ("Yes.", 0.0),
        ("Hens", pytest.approx(1.25)),
        ("are", pytest.approx(1.5)),
        ("fine.", pytest.approx(1.75)),
    ]
    assert service.removed == ["turn", "turn"]


async def test_deepgrams_flush_limit_warning_is_logged_as_such() -> None:
    service = _Service()
    logged: list[str] = []
    sink = logger.add(lambda message: logged.append(str(message)), level="WARNING")
    try:
        await service.on_control(
            {"type": "Warning", "code": "FLUSH_LIMIT", "description": "Too many flushes"}
        )
    finally:
        logger.remove(sink)
    assert any("flush limit" in line for line in logged)


async def test_control_messages_that_are_not_objects_are_skipped() -> None:
    service = _Service()
    await _speak(service, "Yes.")
    service.socket.incoming = ["[1]", '"Flushed"', json.dumps({"type": "Flushed"})]
    await service.flush_audio("turn")
    await service.receive()
    assert service.removed == ["turn"]


def test_usage_metrics_name_the_voice_model() -> None:
    assert _Service().settings.model == "aura-2-thalia-en"


CHUNK = bytes(2 * RATE // 10)


class _StreamingSocket:
    def __init__(self) -> None:
        self.state = State.OPEN
        self._incoming: asyncio.Queue[str | bytes] = asyncio.Queue()
        self._unflushed: list[str] = []
        self._replies: set[asyncio.Task[None]] = set()

    async def send(self, message: str) -> None:
        payload: dict[str, str] = json.loads(message)
        if payload["type"] == "Speak":
            self._unflushed.append(payload["text"])
        elif payload["type"] == "Flush":
            text, self._unflushed = " ".join(self._unflushed), []
            reply = asyncio.get_running_loop().create_task(self._reply(text))
            self._replies.add(reply)
            reply.add_done_callback(self._replies.discard)

    async def _reply(self, text: str) -> None:
        for _ in text.split():
            await self._incoming.put(CHUNK)
        await self._incoming.put(json.dumps({"type": "Flushed"}))

    async def close(self) -> None:
        pass

    def __aiter__(self) -> "_StreamingSocket":
        return self

    async def __anext__(self) -> str | bytes:
        return await self._incoming.get()


class _StreamingService(TimedDeepgramTTSService):
    def __init__(self) -> None:
        super().__init__(api_key="k", sample_rate=RATE, voice="v", text_transforms=[])
        self._stop_frame_timeout_s = 0.2
        self.socket = _StreamingSocket()

    @override
    async def _connect_websocket(self) -> None:
        self._websocket = cast("ClientConnection", self.socket)


async def test_words_after_a_mid_reply_stall_start_on_the_new_audio() -> None:
    stall = 0.8
    down, _ = await run_test(
        _StreamingService(),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("Sure, I can file that. Hens"),
            SleepFrame(stall),
            LLMTextFrame(" are allowed here."),
            LLMFullResponseEndFrame(),
            SleepFrame(0.5),
        ],
    )
    pts = {
        frame.text: frame.pts
        for frame in down
        if isinstance(frame, TTSTextFrame) and frame.pts is not None
    }
    assert (pts["Hens"] - pts["Sure,"]) / 1e9 == pytest.approx(stall, abs=0.2)
