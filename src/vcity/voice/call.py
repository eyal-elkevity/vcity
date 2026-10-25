import logging
import uuid
import wave
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
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from vcity.agent.conversation import Conversation
from vcity.agent.services import Services
from vcity.agent.tools import CallContext
from vcity.settings import VoiceSettings
from vcity.voice.agent_processor import AgentProcessor, HoldLines
from vcity.voice.browser_transfer import ScreenTransfer

_log = logging.getLogger(__name__)

SAMPLE_RATE = 24000


async def run_browser_call(websocket: WebSocket, services: Services, voice: VoiceSettings) -> None:
    call = CallContext(f"WEB-{uuid.uuid4().hex[:8]}", None)
    transfers = ScreenTransfer()
    conversation = Conversation(
        services.anthropic,
        services.toolbox(transfers=transfers),
        services.system,
        call,
        services.clock,
    )
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=SAMPLE_RATE,
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
        ),
    )
    stt = DeepgramSTTService(api_key=voice.deepgram_api_key.get_secret_value())
    tts = ElevenLabsTTSService(
        api_key=voice.elevenlabs_api_key.get_secret_value(),
        sample_rate=SAMPLE_RATE,
        settings=ElevenLabsTTSService.Settings(voice=voice.elevenlabs_voice_id),
    )
    user, assistant = LLMContextAggregatorPair(
        LLMContext(), user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    hold = HoldLines(_clip("let_me_check.wav"), _clip("one_moment.wav"), voice.still_working_after)
    agent = AgentProcessor(conversation, transfers, hold)
    latency = UserBotLatencyObserver()
    worker = PipelineWorker(
        Pipeline([transport.input(), stt, user, agent, tts, transport.output(), assistant]),
        params=PipelineParams(enable_metrics=True, audio_out_sample_rate=SAMPLE_RATE),
        observers=[latency],
    )

    async def on_turn_stopped(
        _: LLMAssistantAggregator, message: AssistantTurnStoppedMessage
    ) -> None:
        if message.interrupted:
            conversation.heard(message.content)

    async def on_breakdown(_: UserBotLatencyObserver, breakdown: LatencyBreakdown) -> None:
        stages = {c.label: round(c.duration_secs, 3) for c in breakdown.contributions}
        _log.info("call %s latency %.3fs %s", call.call_id, breakdown.total_secs, stages)
        await worker.queue_frame(
            RTVIServerMessageFrame(
                data={"type": "latency", "total": breakdown.total_secs, "stages": stages}
            )
        )

    async def on_disconnected(*_: object) -> None:
        await runner.cancel()

    assistant.add_event_handler("on_assistant_turn_stopped", on_turn_stopped)
    latency.add_event_handler("on_latency_breakdown", on_breakdown)
    runner = WorkerRunner(handle_sigint=False)
    transport.add_event_handler("on_client_disconnected", on_disconnected)
    await runner.add_workers(worker)
    await runner.run()


def _clip(name: str) -> SpeechOutputAudioRawFrame:
    with wave.open(str(files("vcity.voice").joinpath("audio", name)), "rb") as clip:
        return SpeechOutputAudioRawFrame(
            audio=clip.readframes(clip.getnframes()),
            sample_rate=clip.getframerate(),
            num_channels=clip.getnchannels(),
        )
