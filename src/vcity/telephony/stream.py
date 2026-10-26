import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel
from starlette.websockets import WebSocket, WebSocketDisconnect

START_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class StreamStart:
    stream_sid: str
    call_sid: str
    ticket: str | None


class _WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, frozen=True)


class _Connected(_WireModel):
    event: Literal["connected"]


class _StartBody(_WireModel):
    stream_sid: str
    call_sid: str
    custom_parameters: dict[str, str] | None = None


class _Start(_WireModel):
    event: Literal["start"]
    start: _StartBody


def parse_start(messages: Sequence[str]) -> StreamStart | None:
    match messages:
        case [connected, start_frame]:
            return _parse_handshake(connected, start_frame)
        case _:
            return None


def _parse_handshake(connected: str, start_frame: str) -> StreamStart | None:
    try:
        _Connected.model_validate_json(connected)
        start = _Start.model_validate_json(start_frame).start
    except ValidationError:
        return None
    ticket = start.custom_parameters.get("ticket") if start.custom_parameters else None
    return StreamStart(stream_sid=start.stream_sid, call_sid=start.call_sid, ticket=ticket)


async def read_start(websocket: WebSocket) -> StreamStart | None:
    try:
        async with asyncio.timeout(START_TIMEOUT_SECONDS):
            first = await websocket.receive_text()
            second = await websocket.receive_text()
    except (TimeoutError, WebSocketDisconnect):
        return None
    return parse_start([first, second])
