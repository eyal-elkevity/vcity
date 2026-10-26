from pathlib import Path

import pytest
from pydantic import ValidationError

from vcity.settings import (
    EmbeddingSettings,
    SearchSettings,
    TelephonySettings,
    TicketSettings,
    VoiceSettings,
    WebSettings,
)


def test_reads_keys_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-test")
    monkeypatch.setenv("GAP_LOG_SALT", "salt-test")
    settings = SearchSettings.from_env()
    assert settings.voyage_api_key.get_secret_value() == "voyage-test"
    assert settings.anthropic_api_key.get_secret_value() == "anthropic-test"
    assert settings.gap_log_salt.get_secret_value() == "salt-test"


def test_secrets_are_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "voyage-test")
    assert "voyage-test" not in repr(EmbeddingSettings.from_env())


def test_ticket_settings_read_the_linear_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_test")
    assert TicketSettings.from_env().linear_api_key.get_secret_value() == "lin_test"


def test_voice_settings_mask_the_key_and_default_the_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    monkeypatch.delenv("VOICE_MODEL", raising=False)
    settings = VoiceSettings.from_env()
    assert settings.deepgram_api_key.get_secret_value() == "dg-test"
    assert "dg-test" not in repr(settings)
    assert settings.voice_model == "aura-2-thalia-en"
    assert settings.still_working_after == 1.5


def test_web_settings_default_to_an_open_ten_minute_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("ACCESS_CODE", "MAX_CALL_SECONDS", "RECYCLE_RSS_MB"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    settings = WebSettings.from_env()
    assert settings.access_code is None
    assert settings.max_call_seconds == 600.0
    assert settings.recycle_rss_mb == 1400


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_access_code_fails_at_load(monkeypatch: pytest.MonkeyPatch, blank: str) -> None:
    monkeypatch.setenv("ACCESS_CODE", blank)
    with pytest.raises(ValidationError):
        WebSettings.from_env()


def test_telephony_is_off_without_a_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    assert TelephonySettings.from_env().twilio_auth_token is None


def test_telephony_reads_the_token_and_masks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tw-secret")
    settings = TelephonySettings.from_env()
    assert settings.twilio_auth_token is not None
    assert settings.twilio_auth_token.get_secret_value() == "tw-secret"
    assert "tw-secret" not in repr(settings)


def test_phone_hours_default_to_the_real_clock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("PHONE_HOURS", raising=False)
    monkeypatch.chdir(tmp_path)
    assert TelephonySettings.from_env().phone_hours == "real"


def test_phone_hours_can_pretend_offices_are_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHONE_HOURS", "open")
    assert TelephonySettings.from_env().phone_hours == "open"


def test_unknown_phone_hours_fail_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHONE_HOURS", "noon")
    with pytest.raises(ValidationError):
        TelephonySettings.from_env()
