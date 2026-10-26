from pathlib import Path

import pytest

from vcity.settings import (
    EmbeddingSettings,
    SearchSettings,
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
