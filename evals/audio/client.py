import asyncio
import contextlib
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import websockets
from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputTransportMessageFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pydantic import BaseModel, ValidationError

CHUNK_SECONDS = 0.02
LEAD_SILENCE_SECONDS = 0.5
LISTEN_SECONDS = 12.0
CLIENT_READY = {
    "label": "rtvi-ai",
    "type": "client-ready",
    "data": {"version": "1.0.0", "about": {"library": "vcity-audio-smoke"}},
}


class _Message(BaseModel):
    type: str
    data: dict[str, object] = {}


@dataclass
class Heard:
    first_audio_seconds: float | None = None
    caller_transcript: list[str] = field(default_factory=list[str])
    agent_said: list[str] = field(default_factory=list[str])
    transfers: list[str] = field(default_factory=list[str])
    interruptions: int = 0
    answered_at: float | None = None
    speaking_at: float | None = None

    @property
    def spoken(self) -> str:
        return " ".join(self.agent_said)

    def speaking_for(self, seconds: float) -> bool:
        return self.speaking_at is not None and time.perf_counter() - self.speaking_at >= seconds


async def call(url: str, wav: Path, aside: tuple[Path, float] | None = None) -> Heard:
    rate, pcm = _read(wav)
    serializer = ProtobufFrameSerializer()
    heard = Heard()
    step = int(rate * CHUNK_SECONDS) * 2
    async with websockets.connect(url, max_size=None) as socket:

        async def send(frame: OutputAudioRawFrame | OutputTransportMessageUrgentFrame) -> None:
            if data := await serializer.serialize(frame):
                await socket.send(data)

        async def stream(audio: bytes) -> None:
            for start in range(0, len(audio), step):
                await send(OutputAudioRawFrame(audio[start : start + step], rate, 1))
                await asyncio.sleep(CHUNK_SECONDS)

        async def listen(spoke_at: list[float]) -> None:
            async for raw in socket:
                frame = await serializer.deserialize(raw)
                if (
                    isinstance(frame, InputAudioRawFrame)
                    and spoke_at
                    and heard.first_audio_seconds is None
                ):
                    heard.answered_at = time.perf_counter()
                    heard.first_audio_seconds = heard.answered_at - spoke_at[0]
                elif isinstance(frame, InputTransportMessageFrame):
                    record_message(heard, frame.message)

        await send(
            OutputTransportMessageUrgentFrame(message={**CLIENT_READY, "id": uuid.uuid4().hex})
        )
        spoke_at: list[float] = []
        listener = asyncio.create_task(listen(spoke_at))
        silence = bytes(step)
        await stream(silence * int(LEAD_SILENCE_SECONDS / CHUNK_SECONDS))
        await stream(pcm)
        spoke_at.append(time.perf_counter())
        with contextlib.suppress(websockets.ConnectionClosed):
            if aside is not None:
                aside_wav, after = aside
                await _idle_until_speaking_for(heard, after, lambda: stream(silence))
                await stream(_read(aside_wav)[1])
            await stream(silence * int(LISTEN_SECONDS / CHUNK_SECONDS))
        listener.cancel()
    return heard


def _read(wav: Path) -> tuple[int, bytes]:
    with wave.open(str(wav), "rb") as clip:
        return clip.getframerate(), clip.readframes(clip.getnframes())


async def _idle_until_speaking_for(
    heard: Heard, seconds: float, idle: Callable[[], Awaitable[None]]
) -> None:
    deadline = time.perf_counter() + LISTEN_SECONDS
    while not heard.speaking_for(seconds) and time.perf_counter() < deadline:
        await idle()


def record_message(heard: Heard, raw: object) -> None:
    try:
        message = _Message.model_validate(raw)
    except ValidationError:
        return
    match message.type, message.data:
        case "user-transcription", {"final": True, "text": str(text)}:
            heard.caller_transcript.append(text)
        case "bot-output", {"aggregated_by": "sentence", "text": str(text)}:
            heard.agent_said.append(text)
            heard.speaking_at = heard.speaking_at or time.perf_counter()
        case "server-message", {"type": "transfer", "department": str(department)}:
            heard.transfers.append(department)
        case "bot-interrupted", _ if heard.answered_at is not None:
            heard.interruptions += 1
        case _:
            pass
