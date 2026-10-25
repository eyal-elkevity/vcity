import json
from contextlib import aclosing
from typing import cast, override

import pytest
from pipecat.frames.frames import Frame, TTSStoppedFrame
from pipecat.utils.context.word_timestamp_tracker import WordTimestamp
from websockets.asyncio.client import ClientConnection

from vcity.voice.timed_tts import SentencePacer, TimedDeepgramTTSService

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
        self.sent: list[dict[str, str]] = []

    async def send(self, message: str) -> None:
        self.sent.append(json.loads(message))


class _Service(TimedDeepgramTTSService):
    def __init__(self) -> None:
        super().__init__(
            api_key="k", sample_rate=RATE, voice="aura-2-thalia-en", text_transforms=[]
        )
        self.socket = _Socket()
        self.words: list[tuple[str, float]] = []
        self.queued: list[Frame] = []
        self.removed: list[str | None] = []

    @override
    def _socket(self) -> ClientConnection:
        return cast("ClientConnection", self.socket)

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


async def _speak(service: _Service, text: str) -> None:
    async with aclosing(service.run_tts(text, "turn")) as frames:
        async for _ in frames:
            pass


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
