import asyncio
import contextlib
import json
import logging
import threading
import xml.etree.ElementTree as ET

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from vcity.agent.pretend_clock import HoursMode
from vcity.agent.routing import TransferRequest
from vcity.agent.tools import CallContext
from vcity.app import create_app
from vcity.domain.departments import Department
from vcity.domain.tickets import TicketRef
from vcity.gate.line import CallLine
from vcity.recycle import MemoryRecycler
from vcity.settings import AppSettings, TelephonySettings, VoiceSettings, WebSettings
from vcity.telephony.signature import twilio_signature
from vcity.telephony.stream import StreamStart
from vcity.voice.call import CallOutcome, OutcomeRecorder, TimeLimit, Transferred

SETTINGS = AppSettings(
    voyage_api_key=SecretStr("v"),
    anthropic_api_key=SecretStr("a"),
    gap_log_salt=SecretStr("s"),
    transfer_to="+15555550100",
)
VOICE = VoiceSettings(deepgram_api_key=SecretStr("d"))
WEB_OPEN = WebSettings(access_code=None)
TELEPHONY = TelephonySettings(twilio_auth_token=SecretStr("tok"))
BUSY_LINE = "The city line is busy. Please try again in a minute."
TIME_LIMIT_LINE = "Calls on this line are limited to ten minutes. Goodbye."
SID = "CA00000000000000000000000000000001"
REQUEST = TransferRequest(Department.PUBLIC_WORKS, "+15555550100", "Pothole <on> Main & 5th")
POTHOLE_SUMMARY = "Pothole <on> Main & 5th"


class FakePhone:
    def __init__(self, outcome: CallOutcome | None = None) -> None:
        self.outcome = outcome
        self.calls = 0
        self.caller_ids: list[str | None] = []

    async def __call__(
        self,
        _websocket: WebSocket,
        _start: StreamStart,
        caller_id: str | None,
        record: OutcomeRecorder,
    ) -> None:
        self.calls += 1
        self.caller_ids.append(caller_id)
        if self.outcome is not None:
            record(self.outcome)


class SlowTeardownPhone:
    def __init__(self) -> None:
        self.returned = threading.Event()

    async def __call__(
        self,
        websocket: WebSocket,
        _start: StreamStart,
        _caller_id: str | None,
        record: OutcomeRecorder,
    ) -> None:
        record(Transferred(REQUEST))
        await websocket.close()
        await asyncio.sleep(0.5)
        self.returned.set()


class FailingPhone:
    async def __call__(
        self,
        _websocket: WebSocket,
        _start: StreamStart,
        _caller_id: str | None,
        _record: OutcomeRecorder,
    ) -> None:
        raise RuntimeError


class StopSpy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def high_memory(stop: StopSpy) -> MemoryRecycler:
    return MemoryRecycler(1400, measure=lambda: 1500.0, stop=stop)


class FakeTicketer:
    def __init__(self, ref: TicketRef | None) -> None:
        self.ref = ref
        self.filed: list[tuple[TransferRequest, CallContext]] = []

    async def __call__(self, request: TransferRequest, call: CallContext) -> TicketRef | None:
        self.filed.append((request, call))
        return self.ref


def make_client(
    phone: FakePhone | SlowTeardownPhone | FailingPhone | None = None,
    *,
    line: CallLine | None = None,
    ticketer: FakeTicketer | None = None,
    recycler: MemoryRecycler | None = None,
) -> TestClient:
    app = create_app(
        SETTINGS,
        VOICE,
        WEB_OPEN,
        call=FakeBrowserCall(),
        telephony=TELEPHONY,
        phone_call=phone or FakePhone(),
        line=line,
        recycler=recycler,
        transfer_ticketer=ticketer or FakeTicketer(None),
    )
    return TestClient(app)


class FakeBrowserCall:
    async def __call__(self, websocket: WebSocket, _hours: HoursMode) -> None:
        await websocket.close()


def session_ticket(client: TestClient) -> str:
    return client.post("/session", json={"code": ""}).json()["ticket"]


def post(client: TestClient, path: str, params: dict[str, str]) -> Response:
    signature = twilio_signature(f"http://testserver{path}", params, "tok")
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature})


def xml(response_text: str) -> ET.Element:
    return ET.fromstring(response_text.split("?>", 1)[1])  # noqa: S314


def ticket_of(twiml: str) -> str:
    parameter = xml(twiml).find("Connect/Stream/Parameter")
    assert parameter is not None
    return parameter.attrib["value"]


def start_frames(ticket: str, call_sid: str = SID) -> list[str]:
    return [
        json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}),
        json.dumps(
            {
                "event": "start",
                "start": {
                    "streamSid": "MZ1",
                    "callSid": call_sid,
                    "customParameters": {"ticket": ticket},
                },
            }
        ),
    ]


def run_stream(client: TestClient, ticket: str, call_sid: str = SID) -> int | None:
    with client.websocket_connect("/ws/twilio") as socket:
        for frame in start_frames(ticket, call_sid):
            socket.send_text(frame)
        try:
            socket.receive_text()
        except WebSocketDisconnect as closed:
            return closed.code
    return None


def voice_ticket(client: TestClient, **extra: str) -> str:
    response = post(client, "/twilio/voice", {"CallSid": SID, **extra})
    return ticket_of(response.text)


def test_routes_are_absent_without_a_token() -> None:
    client = TestClient(create_app(SETTINGS, VOICE, WEB_OPEN))
    assert client.post("/twilio/voice", data={"CallSid": SID}).status_code == 404


def test_an_unsigned_webhook_is_forbidden() -> None:
    client = make_client()
    assert client.post("/twilio/voice", data={"CallSid": SID}).status_code == 403
    forged = client.post(
        "/twilio/voice", data={"CallSid": SID}, headers={"X-Twilio-Signature": "nope"}
    )
    assert forged.status_code == 403
    assert forged.text == ""


def test_every_webhook_checks_the_signature() -> None:
    client = make_client()
    for path in ("after-stream", "whisper", "whisper-answer", "dial-status"):
        assert client.post(f"/twilio/{path}", data={"CallSid": SID}).status_code == 403


def test_voice_streams_the_call_with_a_ticket() -> None:
    response = post(make_client(), "/twilio/voice", {"CallSid": SID})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = xml(response.text)
    stream = root.find("Connect/Stream")
    assert stream is not None
    assert stream.attrib["url"] == "wss://testserver/ws/twilio"
    connect = root.find("Connect")
    assert connect is not None
    assert connect.attrib["action"] == "/twilio/after-stream"
    assert ticket_of(response.text)


def test_voice_on_a_busy_line_says_busy() -> None:
    line = CallLine()
    line.take()
    response = post(make_client(line=line), "/twilio/voice", {"CallSid": SID})
    root = xml(response.text)
    assert root.findtext("Say") == BUSY_LINE
    assert root.find("Hangup") is not None


def test_stream_without_a_valid_ticket_closes_before_the_call() -> None:
    phone = FakePhone()
    client = make_client(phone)
    assert run_stream(client, "forged") == 1008
    assert phone.calls == 0


def test_stream_runs_the_call_with_the_callers_number() -> None:
    phone = FakePhone()
    client = make_client(phone)
    ticket = voice_ticket(client, From="+972500000000")
    run_stream(client, ticket)
    assert phone.caller_ids == ["+972500000000"]


def test_a_missing_caller_number_runs_the_call_anonymously() -> None:
    phone = FakePhone()
    client = make_client(phone)
    run_stream(client, voice_ticket(client))
    assert phone.caller_ids == [None]


def test_a_transfer_dials_the_transfer_number_with_a_whisper() -> None:
    client = make_client(FakePhone(Transferred(REQUEST)))
    run_stream(client, voice_ticket(client))
    root = xml(post(client, "/twilio/after-stream", {"CallSid": SID}).text)
    dial = root.find("Dial")
    assert dial is not None
    assert dial.attrib["answerOnBridge"] == "true"
    assert dial.attrib["action"] == "/twilio/dial-status"
    assert dial.attrib["timeout"] == "20"
    number = dial.find("Number")
    assert number is not None
    assert number.text == "+15555550100"
    assert number.attrib["url"] == "/twilio/whisper"


def test_the_time_limit_says_goodbye() -> None:
    client = make_client(FakePhone(TimeLimit()))
    run_stream(client, voice_ticket(client))
    after = post(client, "/twilio/after-stream", {"CallSid": SID})
    assert xml(after.text).findtext("Say") == TIME_LIMIT_LINE
    assert xml(post(client, "/twilio/after-stream", {"CallSid": SID}).text).find("Say") is None


def test_a_stream_refused_as_busy_says_busy_after_the_stream() -> None:
    line = CallLine()
    client = make_client(line=line)
    ticket = voice_ticket(client)
    line.take()
    assert run_stream(client, ticket) == 4002
    after = post(client, "/twilio/after-stream", {"CallSid": SID})
    assert xml(after.text).findtext("Say") == BUSY_LINE


def test_a_finished_call_releases_the_line() -> None:
    line = CallLine()
    client = make_client(line=line)
    run_stream(client, voice_ticket(client))
    assert not line.busy


def test_an_unknown_call_just_hangs_up() -> None:
    client = make_client()
    for path in ("/twilio/after-stream", "/twilio/dial-status"):
        response = post(client, path, {"CallSid": "CAnope"})
        assert response.status_code == 200
        assert xml(response.text).find("Hangup") is not None


def test_a_transfer_recorded_before_teardown_finishes_still_dials() -> None:
    phone = SlowTeardownPhone()
    client = make_client(phone)
    ticket = voice_ticket(client)
    with client.websocket_connect("/ws/twilio") as socket:
        for frame in start_frames(ticket):
            socket.send_text(frame)
        with contextlib.suppress(WebSocketDisconnect):
            socket.receive_text()
        after = post(client, "/twilio/after-stream", {"CallSid": SID})
        assert not phone.returned.is_set()
    assert xml(after.text).find("Dial") is not None


def test_an_ended_call_hangs_up_and_is_forgotten() -> None:
    client = make_client(FakePhone())
    run_stream(client, voice_ticket(client))
    response = post(client, "/twilio/after-stream", {"CallSid": SID})
    assert xml(response.text).find("Hangup") is not None
    assert xml(response.text).find("Say") is None


def transferring_client(
    ticketer: FakeTicketer | None = None,
) -> tuple[TestClient, FakeTicketer]:
    ticketer = ticketer or FakeTicketer(None)
    client = make_client(FakePhone(Transferred(REQUEST)), ticketer=ticketer)
    run_stream(client, voice_ticket(client, From="+972500000000"))
    post(client, "/twilio/after-stream", {"CallSid": SID})
    return client, ticketer


def test_the_whisper_reads_the_department_and_summary() -> None:
    client, _ = transferring_client()
    response = post(client, "/twilio/whisper", {"CallSid": "CAchild", "ParentCallSid": SID})
    root = xml(response.text)
    gather = root.find("Gather")
    assert gather is not None
    assert gather.attrib == {"numDigits": "1", "action": "/twilio/whisper-answer", "timeout": "8"}
    assert gather.findtext("Say") == (
        f"Corona city line. public works transfer. The caller says: {POTHOLE_SUMMARY}. "
        "Press 1 to accept."
    )


def test_control_characters_are_stripped_from_the_whisper() -> None:
    dirty = TransferRequest(
        Department.PUBLIC_WORKS, "+15555550100", "Pot\x00hole\x0b\x1f here\tnow"
    )
    client = make_client(FakePhone(Transferred(dirty)))
    run_stream(client, voice_ticket(client))
    post(client, "/twilio/after-stream", {"CallSid": SID})
    response = post(client, "/twilio/whisper", {"CallSid": "CAchild", "ParentCallSid": SID})
    assert "Pothole here\tnow" in (xml(response.text).findtext("Gather/Say") or "")


def test_a_whisper_for_an_unknown_parent_hangs_up() -> None:
    response = post(make_client(), "/twilio/whisper", {"CallSid": "c", "ParentCallSid": "CAnope"})
    assert xml(response.text).find("Hangup") is not None


def test_pressing_1_accepts_and_bridges() -> None:
    client, ticketer = transferring_client()
    answer = post(
        client,
        "/twilio/whisper-answer",
        {"CallSid": "CAchild", "ParentCallSid": SID, "Digits": "1"},
    )
    assert answer.status_code == 200
    assert list(xml(answer.text)) == []
    status = post(client, "/twilio/dial-status", {"CallSid": SID})
    assert xml(status.text).find("Hangup") is not None
    assert xml(status.text).find("Say") is None
    assert ticketer.filed == []


def test_another_digit_hangs_up_the_whisper() -> None:
    client, _ = transferring_client()
    answer = post(
        client,
        "/twilio/whisper-answer",
        {"CallSid": "CAchild", "ParentCallSid": SID, "Digits": "2"},
    )
    assert xml(answer.text).find("Hangup") is not None


def test_unaccepted_dial_files_a_ticket_and_reads_it() -> None:
    client, ticketer = transferring_client(FakeTicketer(TicketRef("870623", url=None)))
    response = post(client, "/twilio/dial-status", {"CallSid": SID})
    assert xml(response.text).findtext("Say") == (
        "Sorry, nobody could take your call right now. "
        "I've filed a ticket, reference 8 7 0 6 2 3. Someone will call you back."
    )
    ((request, call),) = ticketer.filed
    assert request == REQUEST
    assert call == CallContext("TEL-00000001", "+972500000000")
    again = post(client, "/twilio/dial-status", {"CallSid": SID})
    assert xml(again.text).find("Say") is None
    assert len(ticketer.filed) == 1


def test_a_failed_ticket_apologises_without_a_reference() -> None:
    client, _ = transferring_client(FakeTicketer(None))
    response = post(client, "/twilio/dial-status", {"CallSid": SID})
    assert xml(response.text).findtext("Say") == (
        "Sorry, nobody could take your call right now, and I couldn't file a ticket. "
        "Please call again later."
    )


def test_a_phone_call_and_a_browser_call_share_the_line() -> None:
    line = CallLine()
    client = make_client(line=line)
    line.take()
    response = post(client, "/twilio/voice", {"CallSid": SID})
    assert xml(response.text).findtext("Say") == BUSY_LINE


def test_the_process_waits_for_the_dial_before_recycling() -> None:
    stop = StopSpy()
    client = make_client(FakePhone(Transferred(REQUEST)), recycler=high_memory(stop))
    run_stream(client, voice_ticket(client))
    post(client, "/twilio/after-stream", {"CallSid": SID})
    assert stop.calls == 0
    post(client, "/twilio/dial-status", {"CallSid": SID})
    assert stop.calls == 1


def test_the_process_recycles_once_an_ended_call_is_answered() -> None:
    stop = StopSpy()
    client = make_client(recycler=high_memory(stop))
    run_stream(client, voice_ticket(client))
    assert stop.calls == 0
    post(client, "/twilio/after-stream", {"CallSid": SID})
    assert stop.calls == 1


def test_a_browser_call_does_not_recycle_during_a_phone_dial() -> None:
    stop = StopSpy()
    client = make_client(FakePhone(Transferred(REQUEST)), recycler=high_memory(stop))
    run_stream(client, voice_ticket(client))
    post(client, "/twilio/after-stream", {"CallSid": SID})
    browser = f"/ws/browser?ticket={session_ticket(client)}"
    with client.websocket_connect(browser) as socket, contextlib.suppress(WebSocketDisconnect):
        socket.receive_text()
    assert stop.calls == 0


def test_a_failing_phone_call_releases_the_line_and_checks_memory() -> None:
    line = CallLine()
    stop = StopSpy()
    client = make_client(FailingPhone(), line=line, recycler=high_memory(stop))
    with pytest.raises(RuntimeError):
        run_stream(client, session_ticket(client))
    assert not line.busy
    assert stop.calls == 1


CALL_ID = "TEL-00000001"


def logged(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "vcity.telephony.routes"]


def test_each_stream_outcome_is_logged_by_call_id(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    for outcome, word in ((Transferred(REQUEST), "transfer"), (TimeLimit(), "time_limit")):
        client = make_client(FakePhone(outcome))
        run_stream(client, voice_ticket(client))
        assert f"{CALL_ID} outcome {word}" in logged(caplog)
    client = make_client()
    run_stream(client, voice_ticket(client))
    assert f"{CALL_ID} outcome ended" in logged(caplog)


def test_a_busy_stream_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    line = CallLine()
    client = make_client(line=line)
    ticket = voice_ticket(client)
    line.take()
    run_stream(client, ticket)
    assert logged(caplog) == [f"{CALL_ID} outcome busy"]


def test_the_whisper_answer_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    client, _ = transferring_client()
    caplog.set_level(logging.INFO)
    for digits in ("1", "2"):
        post(
            client,
            "/twilio/whisper-answer",
            {"CallSid": "CAchild", "ParentCallSid": SID, "Digits": digits},
        )
    assert logged(caplog) == [
        f"{CALL_ID} whisper accepted True",
        f"{CALL_ID} whisper accepted False",
    ]


def test_an_accepted_dial_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    client, _ = transferring_client()
    post(client, "/twilio/whisper-answer", {"CallSid": "c", "ParentCallSid": SID, "Digits": "1"})
    caplog.set_level(logging.INFO)
    post(client, "/twilio/dial-status", {"CallSid": SID})
    assert logged(caplog) == [f"{CALL_ID} dial accepted"]


def test_an_unanswered_dial_logs_whether_the_ticket_was_filed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    for ref, filed in ((TicketRef("870623", url=None), True), (None, False)):
        client, _ = transferring_client(FakeTicketer(ref))
        caplog.clear()
        post(client, "/twilio/dial-status", {"CallSid": SID})
        assert logged(caplog) == [f"{CALL_ID} dial unanswered, ticket filed {filed}"]


def test_logs_never_carry_numbers_or_the_summary(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    client, _ = transferring_client(FakeTicketer(TicketRef("870623", url=None)))
    post(client, "/twilio/whisper", {"CallSid": "CAchild", "ParentCallSid": SID})
    post(client, "/twilio/dial-status", {"CallSid": SID})
    text = " ".join(logged(caplog))
    for secret in ("+972500000000", "+15555550100", "870623", "Pothole"):
        assert secret not in text
