import wave
from importlib.resources import files

from pipecat.observers.user_bot_latency_observer import MeasuredFrom
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy

from vcity.agent.routing import Department, TransferRequest
from vcity.telephony.stream import StreamStart
from vcity.voice.call import (
    LEAD_INS,
    STILL_WORKING,
    UNFINISHED_TURN_SILENCE_SECONDS,
    CallOutcome,
    TimeLimit,
    Transferred,
    browser_surface,
    measured_from_caller,
    phone_surface,
    turn_analyzer,
    user_turn_strategies,
)


def test_a_reply_measured_from_the_caller_counts() -> None:
    assert measured_from_caller(MeasuredFrom.USER_SILENCE, {"stt": 0.21, "llm": 0.9, "tts": 0.35})


def test_a_reply_anchored_on_the_greeting_span_is_skipped() -> None:
    stages = {"first request": 37.1, "llm": 0.9, "tts": 0.35}
    assert not measured_from_caller(MeasuredFrom.USER_SILENCE, stages)


def test_the_spoken_greeting_is_not_measured_as_a_reply() -> None:
    assert not measured_from_caller(MeasuredFrom.CLIENT_CONNECTED, {"output transport": 0.02})


def test_phone_surface_greets_the_caller() -> None:
    start = StreamStart(stream_sid="MZ1", call_sid="CA0123456789abcdef", ticket=None)

    surface = phone_surface(start, None, lambda _: None)

    assert surface.greeting == "Thanks for calling the City of Corona. How can I help you today?"


def test_browser_surface_does_not_greet() -> None:
    assert browser_surface().greeting is None


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


def test_a_turn_smart_turn_finds_unfinished_still_ends_after_a_short_silence() -> None:
    assert turn_analyzer().params.stop_secs == UNFINISHED_TURN_SILENCE_SECONDS == 1.5


def test_turns_end_by_smart_turn_and_start_after_two_words() -> None:
    strategies = user_turn_strategies()
    assert strategies.stop is not None
    assert [type(s) for s in strategies.stop] == [TurnAnalyzerUserTurnStopStrategy]
    assert strategies.start is not None
    assert [type(s) for s in strategies.start] == [MinWordsUserTurnStartStrategy]


def test_every_hold_line_is_a_24_khz_mono_clip() -> None:
    for name in LEAD_INS + STILL_WORKING:
        with wave.open(str(files("vcity.voice").joinpath("audio", name)), "rb") as clip:
            assert (clip.getframerate(), clip.getnchannels(), clip.getsampwidth()) == (24000, 1, 2)
            assert 0.5 < clip.getnframes() / 24000 < 1.5
