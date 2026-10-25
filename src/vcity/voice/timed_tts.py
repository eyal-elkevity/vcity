import json
import time
from collections import deque
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from typing import cast, override

from loguru import logger
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.utils.text.transforms.text_transform import TextTransform
from websockets.asyncio.client import ClientConnection
from websockets.protocol import State

BYTES_PER_SAMPLE = 2
FIRST_SECONDS_PER_LETTER = 0.06
# Deepgram accepts 20 Flush messages per 60 s: https://developers.deepgram.com/docs/tts-ws-flush
FLUSH_BUDGET = 18
FLUSH_WINDOW_SECONDS = 60.0

type WordTimes = list[tuple[str, float]]


@dataclass(eq=False)
class SentencePacer:
    words: list[str]
    seconds_per_letter: float
    heard_seconds: float = 0.0
    released: int = 0
    starts: list[float] = field(default_factory=list[float])

    def __post_init__(self) -> None:
        self._time_from(0)

    @property
    def letters(self) -> int:
        return sum(len(word) + 1 for word in self.words)

    def extend(self, words: list[str]) -> None:
        first = len(self.words)
        self.words += words
        self._time_from(first)

    def truncate(self, count: int) -> None:
        del self.words[count:]
        del self.starts[count:]
        self.released = min(self.released, count)

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

    def _time_from(self, first: int) -> None:
        elapsed = self.heard_seconds
        if first:
            elapsed = max(elapsed, self.starts[first - 1] + self._length(self.words[first - 1]))
        for word in self.words[first:]:
            self.starts.append(elapsed)
            elapsed += self._length(word)

    def _length(self, word: str) -> float:
        return (len(word) + 1) * self.seconds_per_letter

    def _release(self, count: int) -> WordTimes:
        out = [(self.words[i], self.starts[i]) for i in range(self.released, self.released + count)]
        self.released += count
        return out


# Deepgram's speak socket sends no word timings. Flushing each sentence on its own marks where its
# audio ends, and its words are spread over that audio as it arrives, so Pipecat can follow
# playback word by word. Past the flush budget, sentences share the next Flush and its audio.
class TimedDeepgramTTSService(DeepgramTTSService):
    def __init__(
        self,
        *,
        api_key: str,
        sample_rate: int,
        voice: str,
        text_transforms: list[tuple[str, TextTransform]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(
            api_key=api_key,
            sample_rate=sample_rate,
            settings=DeepgramTTSService.Settings(model=voice, voice=voice),
            text_transforms=text_transforms,
            push_text_frames=False,
        )
        self._bytes_per_second = BYTES_PER_SAMPLE * sample_rate
        self._sentences: deque[SentencePacer] = deque()
        self._unflushed: SentencePacer | None = None
        self._clock = clock
        self._flush_times: deque[float] = deque()
        self._seconds_per_letter = FIRST_SECONDS_PER_LETTER
        self._offset = 0.0
        self._closing = False
        self._clearing = False

    @override
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None]:
        try:
            if not self._websocket or self._websocket.state is State.CLOSED:
                await self._connect()
            await self._speak(text)
        except Exception as error:  # noqa: BLE001
            yield ErrorFrame(error=f"{self} error: {type(error).__name__}")
        yield None

    @override
    async def flush_audio(self, context_id: str | None = None) -> None:
        self._closing = True
        if self._unflushed is not None:
            self._unflushed = None
            try:
                await self._flush()
            except Exception as error:  # noqa: BLE001
                logger.error(f"{self} error sending Flush: {type(error).__name__}")
                self._forget_turn()
                await self._finish_context()
        elif not self._sentences:
            await self._finish_context()

    @override
    async def on_audio_context_completed(self, context_id: str) -> None:
        self._offset = 0.0
        await super().on_audio_context_completed(context_id)

    @override
    async def on_audio_context_interrupted(self, context_id: str) -> None:
        self._forget_turn()
        self._clearing = True
        await super().on_audio_context_interrupted(context_id)

    @override
    async def _connect_websocket(self) -> None:
        was_open = self._websocket is not None and self._websocket.state is State.OPEN
        await super()._connect_websocket()
        if not was_open:
            self._forget_turn()
            self._clearing = False

    @override
    async def _receive_messages(self) -> None:
        async for message in self._socket():
            if isinstance(message, bytes):
                await self.on_audio(message)
                continue
            try:
                control: object = json.loads(message)
            except json.JSONDecodeError:
                control = None
            if not isinstance(control, dict):
                logger.warning(f"{self} skipped a message that is not a JSON object")
                continue
            await self.on_control(cast("dict[str, object]", control))

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
            case "Warning" if _is_flush_limit(message):
                logger.warning(f"{self} hit Deepgram's flush limit; word timing may drift")
            case "Warning":
                description = message.get("warn_msg") or message.get("description")
                logger.warning(f"{self} warning: {description or 'Unknown warning'}")
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

    async def _speak(self, text: str) -> None:
        words = text.split()
        pacer = self._unflushed
        spoken_before = len(pacer.words) if pacer else 0
        if pacer is None:
            pacer = SentencePacer(words, self._seconds_per_letter)
            self._sentences.append(pacer)
        else:
            pacer.extend(words)
        flush = self._flush_allowed()
        try:
            await self._socket().send(json.dumps({"type": "Speak", "text": text}))
            if flush:
                await self._flush()
        except Exception:
            self._take_back(pacer, spoken_before)
            raise
        self._unflushed = None if flush or not self._queued(pacer) else pacer

    def _take_back(self, pacer: SentencePacer, spoken_before: int) -> None:
        if spoken_before:
            pacer.truncate(spoken_before)
        elif self._queued(pacer):
            self._sentences.remove(pacer)

    def _queued(self, pacer: SentencePacer) -> bool:
        return any(queued is pacer for queued in self._sentences)

    def _flush_allowed(self) -> bool:
        now = self._clock()
        while self._flush_times and now - self._flush_times[0] >= FLUSH_WINDOW_SECONDS:
            self._flush_times.popleft()
        return len(self._flush_times) < FLUSH_BUDGET

    async def _flush(self) -> None:
        await self._socket().send(json.dumps({"type": "Flush"}))
        self._flush_times.append(self._clock())

    def _socket(self) -> ClientConnection:
        return cast("ClientConnection", self._get_websocket())

    async def _time_words(self, timed: WordTimes) -> None:
        if timed:
            await self.add_word_timestamps(
                [(word, self._offset + start) for word, start in timed],
                self.get_active_audio_context_id(),
            )

    def _forget_turn(self) -> None:
        self._sentences.clear()
        self._unflushed = None
        self._closing = False
        self._offset = 0.0

    async def _finish_context(self) -> None:
        self._closing = False
        self._offset = 0.0
        context_id = self.get_active_audio_context_id()
        if context_id is None:
            return
        await self.append_to_audio_context(context_id, TTSStoppedFrame(context_id=context_id))
        await self.remove_audio_context(context_id)


def _is_flush_limit(warning: dict[str, object]) -> bool:
    fields = ("code", "warn_code", "description", "warn_msg")
    return any("flush" in str(warning.get(name, "")).casefold() for name in fields)
