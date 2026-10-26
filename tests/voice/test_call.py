from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer

from vcity.agent.routing import Department, TransferRequest
from vcity.telephony.stream import StreamStart
from vcity.voice.call import (
    CallOutcome,
    TimeLimit,
    Transferred,
    browser_surface,
    measured_from_caller,
    phone_surface,
)


def test_a_reply_measured_from_the_caller_counts() -> None:
    assert measured_from_caller({"stt": 0.21, "llm": 0.9, "tts": 0.35})


def test_a_reply_anchored_on_the_greeting_span_is_skipped() -> None:
    assert not measured_from_caller({"first request": 37.1, "llm": 0.9, "tts": 0.35})


def test_browser_surface_uses_protobuf_at_24k() -> None:
    surface = browser_surface()

    assert isinstance(surface.serializer, ProtobufFrameSerializer)
    assert surface.sample_rate == 24000
    assert surface.call_id.startswith("WEB-")
    assert surface.caller_id is None


def test_phone_surface_uses_twilio_without_auto_hang_up() -> None:
    start = StreamStart(stream_sid="MZ1", call_sid="CA0123456789abcdef", ticket=None)

    surface = phone_surface(start, "+972500000000", lambda _: None)

    assert isinstance(surface.serializer, TwilioFrameSerializer)
    assert surface.sample_rate == 8000
    assert surface.call_id == "TEL-" + "CA0123456789abcdef"[-8:]
    assert surface.caller_id == "+972500000000"


def test_phone_surface_reports_outcomes_to_its_recorder() -> None:
    start = StreamStart(stream_sid="MZ1", call_sid="CA0123456789abcdef", ticket=None)
    recorded: list[CallOutcome] = []
    request = TransferRequest(Department.UTILITIES, "+15555550100", "Water bill")

    surface = phone_surface(start, None, recorded.append)
    surface.record(Transferred(request))
    surface.record(TimeLimit())

    assert recorded == [Transferred(request), TimeLimit()]


def test_browser_surface_has_no_recorder() -> None:
    surface = browser_surface()

    surface.record(TimeLimit())

    assert surface.record_outcome is None
