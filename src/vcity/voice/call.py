import asyncio
import contextlib
import logging
import uuid
import wave
from dataclasses import dataclass
from importlib.resources import files

from fastapi import WebSocket
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import SpeechOutputAudioRawFrame
from pipecat.observers.user_bot_latency_observer import LatencyBreakdown, UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    AssistantTurnStoppedMessage,
    LLMAssistantAggregator,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.workers.runner import WorkerRunner
from starlette.websockets import WebSocketState

from vcity.agent.conversation import Conversation
from vcity.agent.pretend_clock import HoursMode, pretend_clock
from vcity.agent.routing import TransferRequest
from vcity.agent.services import Services
from vcity.agent.tools import CallContext
from vcity.settings import VoiceSettings
from vcity.telephony.stream import StreamStart
from vcity.voice.agent_processor import AgentProcessor, HoldLines
from vcity.voice.call_transfer import CallTransfer
from vcity.voice.deadline import end_at_deadline
from vcity.voice.speech import speakable
from vcity.voice.timed_tts import TimedDeepgramTTSService

_log = logging.getLogger(__name__)

BROWSER_SAMPLE_RATE = 24000
PHONE_SAMPLE_RATE = 8000
CALL_ID_SUFFIX_LENGTH = 8
INTERRUPT_MIN_WORDS = 2
# Pipecat's span for a bot greeting; with no greeting on this line it is anchored too early.
GREETING_SPAN = "first request"
TIME_LIMIT_CLOSE_CODE = 4003
TIME_LIMIT_MESSAGE = {"type": "call-ended", "reason": "time-limit"}


@dataclass(frozen=True, slots=True)
class CallSurface:
    call_id: str
    caller_id: str | None
    serializer: FrameSerializer
    sample_rate: int


def browser_surface() -> CallSurface:
    return CallSurface(
        call_id=f"WEB-{uuid.uuid4().hex[:CALL_ID_SUFFIX_LENGTH]}",
        caller_id=None,
        serializer=ProtobufFrameSerializer(),
        sample_rate=BROWSER_SAMPLE_RATE,
    )


def phone_surface(start: StreamStart, caller_id: str | None) -> CallSurface:
    return CallSurface(
        call_id=f"TEL-{start.call_sid[-CALL_ID_SUFFIX_LENGTH:]}",
        caller_id=caller_id,
        serializer=TwilioFrameSerializer(
            start.stream_sid,
            call_sid=start.call_sid,
            params=TwilioFrameSerializer.InputParams(auto_hang_up=False),
        ),
        sample_rate=PHONE_SAMPLE_RATE,
    )


@dataclass(frozen=True, slots=True)
class Transferred:
    request: TransferRequest


@dataclass(frozen=True, slots=True)
class TimeLimit: ...


@dataclass(frozen=True, slots=True)
class Ended: ...


type CallOutcome = Transferred | TimeLimit | Ended


async def run_call(  # noqa: PLR0913, PLR0917
    websocket: WebSocket,
    services: Services,
    voice: VoiceSettings,
    max_seconds: float,
    hours: HoursMode,
    surface: CallSurface,
) -> CallOutcome:
    call = CallContext(surface.call_id, surface.caller_id)
    transfers = CallTransfer()
    clock = pretend_clock(services.clock, hours)
    conversation = Conversation(
        services.anthropic,
        services.toolbox(clock=clock, transfers=transfers),
        services.system,
        call,
        clock,
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=surface.sample_rate,
            add_wav_header=False,
            serializer=surface.serializer,
        ),
    )
    stt = DeepgramSTTService(api_key=voice.deepgram_api_key.get_secret_value())
    tts = TimedDeepgramTTSService(
        api_key=voice.deepgram_api_key.get_secret_value(),
        sample_rate=surface.sample_rate,
        voice=voice.voice_model,
        text_transforms=[("*", _speakable)],
    )
    user, assistant = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=INTERRUPT_MIN_WORDS)]
            ),
        ),
    )
    hold = HoldLines(_clip("let_me_check.wav"), _clip("one_moment.wav"), voice.still_working_after)
    agent = AgentProcessor(conversation, transfers, hold)
    latency = UserBotLatencyObserver()
    worker = PipelineWorker(
        Pipeline([transport.input(), stt, user, agent, tts, transport.output(), assistant]),
        params=PipelineParams(
            enable_metrics=True,
            audio_out_sample_rate=surface.sample_rate,
        ),
        observers=[latency],
    )

    async def on_turn_stopped(
        _: LLMAssistantAggregator, message: AssistantTurnStoppedMessage
    ) -> None:
        if message.interrupted:
            conversation.heard(message.content)

    async def on_breakdown(_: UserBotLatencyObserver, breakdown: LatencyBreakdown) -> None:
        stages = {c.label: round(c.duration_secs, 3) for c in breakdown.contributions}
        if not measured_from_caller(stages):
            return
        _log.info("call %s latency %.3fs %s", call.call_id, breakdown.total_secs, stages)
        await worker.queue_frame(
            RTVIServerMessageFrame(
                data={"type": "latency", "total": breakdown.total_secs, "stages": stages}
            )
        )

    async def on_disconnected(*_: object) -> None:
        await runner.cancel()

    async def notify_time_limit() -> None:
        _log.info("call %s reached the time limit", call.call_id)
        await worker.queue_frame(RTVIServerMessageFrame(data=TIME_LIMIT_MESSAGE))

    async def hang_up() -> None:
        timed_out.append(True)
        if _both_connected(websocket):
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=TIME_LIMIT_CLOSE_CODE, reason="time limit")
        await runner.cancel()

    timed_out: list[bool] = []
    assistant.add_event_handler("on_assistant_turn_stopped", on_turn_stopped)
    latency.add_event_handler("on_latency_breakdown", on_breakdown)
    runner = WorkerRunner(handle_sigint=False)
    transport.add_event_handler("on_client_disconnected", on_disconnected)
    await runner.add_workers(worker)
    deadline = asyncio.create_task(
        end_at_deadline(max_seconds, notify=notify_time_limit, hang_up=hang_up)
    )
    try:
        await runner.run()
    finally:
        deadline.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await deadline
    return _outcome(timed_out=bool(timed_out), transfers=transfers)


def _outcome(*, timed_out: bool, transfers: CallTransfer) -> CallOutcome:
    if timed_out:
        return TimeLimit()
    if transfers.last is not None:
        return Transferred(transfers.last)
    return Ended()


def _both_connected(websocket: WebSocket) -> bool:
    return (
        websocket.client_state == WebSocketState.CONNECTED
        and websocket.application_state == WebSocketState.CONNECTED
    )


def measured_from_caller(stages: dict[str, float]) -> bool:
    return GREETING_SPAN not in stages


def _clip(name: str) -> SpeechOutputAudioRawFrame:
    with wave.open(str(files("vcity.voice").joinpath("audio", name)), "rb") as clip:
        return SpeechOutputAudioRawFrame(
            audio=clip.readframes(clip.getnframes()),
            sample_rate=clip.getframerate(),
            num_channels=clip.getnchannels(),
        )


async def _speakable(text: str, _: object) -> str:
    return speakable(text)
