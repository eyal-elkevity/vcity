import json
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import cast, override

from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.utils.text.transforms.text_transform import TextTransform
from websockets.asyncio.client import ClientConnection

BYTES_PER_SAMPLE = 2
FIRST_SECONDS_PER_LETTER = 0.06

type WordTimes = list[tuple[str, float]]


@dataclass
class SentencePacer:
    words: list[str]
    seconds_per_letter: float
    heard_seconds: float = 0.0
    released: int = 0
    starts: list[float] = field(default_factory=list[float])

    def __post_init__(self) -> None:
        elapsed = 0.0
        for word in self.words:
            self.starts.append(elapsed)
            elapsed += (len(word) + 1) * self.seconds_per_letter

    @property
    def letters(self) -> int:
        return sum(len(word) + 1 for word in self.words)

    def audio(self, seconds: float) -> WordTimes:
        self.heard_seconds += seconds
        due = self.released
        while due < len(self.words) and self.starts[due] <= self.heard_seconds:
            due += 1
        return self._release(due - self.released)

    def finish(self) -> WordTimes:
        left = range(self.released, len(self.words))
        if left and self.starts[-1] >= self.heard_seconds:
            last = self.starts[self.released - 1] if self.released else 0.0
            step = (self.heard_seconds - last) / (len(left) + 1)
            for k, i in enumerate(left, start=1):
                self.starts[i] = last + step * k
        return self._release(len(left))

    def _release(self, count: int) -> WordTimes:
        out = [(self.words[i], self.starts[i]) for i in range(self.released, self.released + count)]
        self.released += count
        return out


# Deepgram's speak socket sends no word timings. Flushing each sentence on its own marks where its
# audio ends, and its words are spread over that audio as it arrives, so Pipecat can follow
# playback word by word.
class TimedDeepgramTTSService(DeepgramTTSService):
    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int,
        voice: str,
        text_transforms: list[tuple[str, TextTransform]],
    ) -> None:
        super().__init__(
            api_key=api_key,
            sample_rate=sample_rate,
            settings=DeepgramTTSService.Settings(voice=voice),
            text_transforms=text_transforms,
            push_text_frames=False,
        )
        self._bytes_per_second = BYTES_PER_SAMPLE * sample_rate
        self._sentences: deque[SentencePacer] = deque()
        self._seconds_per_letter = FIRST_SECONDS_PER_LETTER
        self._offset = 0.0
        self._closing = False
        self._clearing = False

    @override
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None]:
        if not self._sentences and not self._closing:
            self._offset = 0.0
        self._sentences.append(SentencePacer(text.split(), self._seconds_per_letter))
        try:
            websocket = self._socket()
            await websocket.send(json.dumps({"type": "Speak", "text": text}))
            await websocket.send(json.dumps({"type": "Flush"}))
        except Exception as error:  # noqa: BLE001
            yield ErrorFrame(error=f"{self} error: {type(error).__name__}")
        yield None

    @override
    async def flush_audio(self, context_id: str | None = None) -> None:
        self._closing = True
        if not self._sentences:
            await self._finish_context()

    @override
    async def on_audio_context_interrupted(self, context_id: str) -> None:
        self._sentences.clear()
        self._closing = False
        self._clearing = True
        await super().on_audio_context_interrupted(context_id)

    @override
    async def _receive_messages(self) -> None:
        async for message in self._socket():
            if isinstance(message, bytes):
                await self.on_audio(message)
            else:
                await self.on_control(json.loads(message))

    async def on_audio(self, audio: bytes) -> None:
        if self._clearing or not self._sentences:
            return
        context_id = self.get_active_audio_context_id()
        await self.append_to_audio_context(
            context_id, TTSAudioRawFrame(audio, self.sample_rate, 1, context_id=context_id)
        )
        await self._time_words(self._sentences[0].audio(len(audio) / self._bytes_per_second))

    async def on_control(self, message: dict[str, object]) -> None:
        match message.get("type"):
            case "Cleared":
                self._clearing = False
            case "Flushed" if not self._clearing and self._sentences:
                sentence = self._sentences.popleft()
                await self._time_words(sentence.finish())
                if sentence.letters:
                    self._seconds_per_letter = sentence.heard_seconds / sentence.letters
                self._offset += sentence.heard_seconds
                if self._closing and not self._sentences:
                    await self._finish_context()
            case _:
                pass

    def _socket(self) -> ClientConnection:
        return cast("ClientConnection", self._get_websocket())

    async def _time_words(self, timed: WordTimes) -> None:
        if timed:
            await self.add_word_timestamps(
                [(word, self._offset + start) for word, start in timed],
                self.get_active_audio_context_id(),
            )

    async def _finish_context(self) -> None:
        self._closing = False
        context_id = self.get_active_audio_context_id()
        if context_id is None:
            return
        await self.append_to_audio_context(context_id, TTSStoppedFrame(context_id=context_id))
        await self.remove_audio_context(context_id)
