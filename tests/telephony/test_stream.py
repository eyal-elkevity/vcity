import json
from typing import Any

from starlette.types import Message
from starlette.websockets import WebSocket

from vcity.telephony.stream import StreamStart, parse_start, read_start

CONNECTED = json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"})


def _start(*, call_sid: str | None = "CA1", custom: dict[str, Any] | None = None) -> str:
    start: dict[str, Any] = {
        "accountSid": "AC1",
        "streamSid": "MZ1",
        "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
    }
    if call_sid is not None:
        start["callSid"] = call_sid
    if custom is not None:
        start["customParameters"] = custom
    return json.dumps({"event": "start", "sequenceNumber": "1", "start": start, "streamSid": "MZ1"})


def test_reads_the_ticket_from_custom_parameters() -> None:
    result = parse_start([CONNECTED, _start(custom={"ticket": "tk-42"})])
    assert result is not None
    assert result.stream_sid == "MZ1"
    assert result.call_sid == "CA1"
    assert result.ticket == "tk-42"


def test_a_missing_ticket_is_none_not_an_error() -> None:
    result = parse_start([CONNECTED, _start()])
    assert result is not None
    assert result.ticket is None


def test_start_before_connected_is_rejected() -> None:
    assert parse_start([_start(custom={"ticket": "tk"}), CONNECTED]) is None


def test_garbage_is_rejected() -> None:
    assert parse_start(["not json", "{}"]) is None


def test_a_start_without_call_sid_is_rejected() -> None:
    assert parse_start([CONNECTED, _start(call_sid=None)]) is None


async def _socket(*messages: Message) -> WebSocket:
    queue: list[Message] = [{"type": "websocket.connect"}, *messages]

    async def receive() -> Message:
        return queue.pop(0)

    async def send(_message: Message) -> None:
        return None

    websocket = WebSocket({"type": "websocket", "path": "/", "headers": []}, receive, send)
    await websocket.accept()
    return websocket


def _text(payload: str) -> Message:
    return {"type": "websocket.receive", "text": payload}


async def test_read_start_returns_the_stream_start_from_text_frames() -> None:
    websocket = await _socket(_text(CONNECTED), _text(_start(custom={"ticket": "tk-7"})))
    result = await read_start(websocket)
    assert result == StreamStart(stream_sid="MZ1", call_sid="CA1", ticket="tk-7")


async def test_read_start_ignores_a_binary_frame_during_the_handshake() -> None:
    websocket = await _socket({"type": "websocket.receive", "bytes": b"\x00\x01"})
    assert await read_start(websocket) is None


async def test_read_start_returns_none_when_the_caller_disconnects() -> None:
    websocket = await _socket(_text(CONNECTED), {"type": "websocket.disconnect", "code": 1000})
    assert await read_start(websocket) is None
