import asyncio
from dataclasses import dataclass

from pipecat.frames.frames import (
    EndWorkerFrame,
    Frame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    SpeechOutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.processors.frameworks.rtvi.frames import RTVIServerMessageFrame
from pydantic import BaseModel, ValidationError

from vcity.agent.conversation import Conversation, Done, Speech, ToolsRunning
from vcity.voice.browser_transfer import ScreenTransfer


@dataclass(frozen=True, slots=True)
class HoldLines:
    lead_in: SpeechOutputAudioRawFrame
    still_working: SpeechOutputAudioRawFrame
    still_working_after: float


class AgentProcessor(FrameProcessor):
    def __init__(
        self, conversation: Conversation, transfers: ScreenTransfer, hold: HoldLines
    ) -> None:
        super().__init__(name="agent")
        self._conversation = conversation
        self._transfers = transfers
        self._hold = hold

    def can_generate_metrics(self) -> bool:
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            if not frame.speculation and (caller := _last_user_text(frame)):
                await self._answer(caller)
            return
        await self.push_frame(frame, direction)

    async def _answer(self, caller: str) -> None:
        await self.push_frame(LLMFullResponseStartFrame())
        await self.start_ttfb_metrics()
        waiting: asyncio.Task[None] | None = None
        spoke = False
        try:
            async for event in self._conversation.stream(caller):
                if waiting:
                    waiting.cancel()
                    waiting = None
                match event:
                    case Speech(text=text):
                        await self.stop_ttfb_metrics()
                        await self.push_frame(LLMTextFrame(text))
                        spoke = True
                    case ToolsRunning():
                        await self.push_frame(LLMFullResponseEndFrame())
                        if not spoke:
                            await self.stop_ttfb_metrics()
                            await self.push_frame(self._hold.lead_in)
                            spoke = True
                        await self.push_frame(LLMFullResponseStartFrame())
                        waiting = asyncio.create_task(self._hold_on())
                    case Done():
                        pass
        finally:
            if waiting:
                waiting.cancel()
        await self.push_frame(LLMFullResponseEndFrame())
        if self._transfers.requests:
            department = self._transfers.requests[-1].department
            await self.push_frame(
                RTVIServerMessageFrame(data={"type": "transfer", "department": department.value})
            )
            await self.push_frame(EndWorkerFrame())

    async def _hold_on(self) -> None:
        await asyncio.sleep(self._hold.still_working_after)
        await self.push_frame(self._hold.still_working)


class _CallerMessage(BaseModel):
    role: str
    content: str


def _last_user_text(frame: LLMContextFrame) -> str:
    messages: list[object] = list(frame.context.get_messages())
    if not messages:
        return ""
    try:
        message = _CallerMessage.model_validate(messages[-1])
    except ValidationError:
        return ""
    return message.content.strip() if message.role == "user" else ""
