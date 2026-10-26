import json
from typing import Any

from vcity.telephony.stream import parse_start

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
