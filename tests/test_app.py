from fastapi.testclient import TestClient
from pydantic import SecretStr

from vcity.app import create_app
from vcity.settings import AppSettings, VoiceSettings

SETTINGS = AppSettings(
    voyage_api_key=SecretStr("v"), anthropic_api_key=SecretStr("a"), gap_log_salt=SecretStr("s")
)
VOICE = VoiceSettings(deepgram_api_key=SecretStr("d"), voice_model="aura-2-thalia-en")


def test_health_answers_without_touching_any_service() -> None:
    response = TestClient(create_app(SETTINGS, VOICE)).get("/health")
    assert response.json() == {"status": "ok"}


def test_call_page_connects_to_the_browser_socket() -> None:
    page = TestClient(create_app(SETTINGS, VOICE)).get("/call")
    assert page.headers["content-type"].startswith("text/html")
    assert "/ws/browser" in page.text
