import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from vcity.app import create_app
from vcity.gate.line import CallLine
from vcity.recycle import MemoryRecycler
from vcity.settings import AppSettings, VoiceSettings, WebSettings

SETTINGS = AppSettings(
    voyage_api_key=SecretStr("v"), anthropic_api_key=SecretStr("a"), gap_log_salt=SecretStr("s")
)
VOICE = VoiceSettings(deepgram_api_key=SecretStr("d"), voice_model="aura-2-thalia-en")
WEB_OPEN = WebSettings(access_code=None)
WEB_GATED = WebSettings(access_code=SecretStr("lemon-42"))
CODE = "lemon-42"


class FakeCall:
    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self.error = error

    async def __call__(self, websocket: WebSocket) -> None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        await websocket.close()


def ticket_from(client: TestClient, code: str = CODE) -> str:
    response = client.post("/session", json={"code": code})
    assert response.status_code == 200
    return response.json()["ticket"]


def close_code(client: TestClient, path: str) -> int:
    with client.websocket_connect(path) as socket, pytest.raises(WebSocketDisconnect) as closed:
        socket.receive_text()
    return closed.value.code


def test_health_answers_without_touching_any_service() -> None:
    response = TestClient(create_app(SETTINGS, VOICE, WEB_OPEN)).get("/health")
    assert response.json() == {"status": "ok"}


def test_call_page_connects_to_the_browser_socket() -> None:
    page = TestClient(create_app(SETTINGS, VOICE, WEB_OPEN)).get("/call")
    assert page.headers["content-type"].startswith("text/html")
    assert "/ws/browser" in page.text


def test_the_page_learns_whether_a_code_is_needed() -> None:
    open_gate = TestClient(create_app(SETTINGS, VOICE, WEB_OPEN))
    gated = TestClient(create_app(SETTINGS, VOICE, WEB_GATED))
    assert open_gate.get("/session").json() == {"code_required": False}
    assert gated.get("/session").json() == {"code_required": True}


def test_any_code_gets_a_ticket_when_the_gate_is_off() -> None:
    client = TestClient(create_app(SETTINGS, VOICE, WEB_OPEN))
    response = client.post("/session", json={"code": ""})
    assert response.status_code == 200
    assert isinstance(response.json()["ticket"], str)


def test_a_wrong_code_is_refused() -> None:
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED))
    assert client.post("/session", json={"code": "nope"}).status_code == 401


def test_the_right_code_gets_a_ticket() -> None:
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED))
    assert client.post("/session", json={"code": CODE}).status_code == 200


def test_a_lockout_refuses_even_the_right_code() -> None:
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED))
    for _ in range(10):
        assert client.post("/session", json={"code": "nope"}).status_code == 401
    assert client.post("/session", json={"code": CODE}).status_code == 429


def test_a_socket_without_a_valid_ticket_is_closed_before_the_call() -> None:
    call = FakeCall()
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED, call=call))
    assert close_code(client, "/ws/browser?ticket=forged") == 1008
    assert close_code(client, "/ws/browser") == 1008
    assert call.calls == 0


def test_a_used_ticket_is_refused() -> None:
    call = FakeCall()
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED, call=call))
    ticket = ticket_from(client)
    assert close_code(client, f"/ws/browser?ticket={ticket}") == 1000
    assert close_code(client, f"/ws/browser?ticket={ticket}") == 1008
    assert call.calls == 1


def test_a_busy_line_refuses_new_callers() -> None:
    call = FakeCall()
    line = CallLine()
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED, call=call, line=line))
    ticket = ticket_from(client)
    assert line.take()
    assert close_code(client, f"/ws/browser?ticket={ticket}") == 4002
    assert client.post("/session", json={"code": CODE}).status_code == 409
    assert call.calls == 0


def test_the_line_is_freed_when_the_call_fails() -> None:
    call = FakeCall(RuntimeError())
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED, call=call))
    ticket = ticket_from(client)
    with pytest.raises(RuntimeError):
        close_code(client, f"/ws/browser?ticket={ticket}")
    assert client.post("/session", json={"code": CODE}).status_code == 200


def test_the_process_recycles_after_a_call_when_memory_is_high() -> None:
    stops: list[None] = []
    recycler = MemoryRecycler(1400, measure=lambda: 1500.0, stop=lambda: stops.append(None))
    client = TestClient(create_app(SETTINGS, VOICE, WEB_GATED, call=FakeCall(), recycler=recycler))
    assert close_code(client, f"/ws/browser?ticket={ticket_from(client)}") == 1000
    assert len(stops) == 1
