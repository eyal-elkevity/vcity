import pytest

from vcity.settings import EmbeddingSettings, SearchSettings, TicketSettings


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
