from pathlib import Path
from typing import Self

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    voyage_api_key: SecretStr

    @classmethod
    def from_env(cls) -> Self:
        # pyright can't see that BaseSettings fills required fields from the environment.
        return cls.model_validate({})


class SearchSettings(EmbeddingSettings):
    anthropic_api_key: SecretStr
    gap_log_salt: SecretStr


class TicketSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    linear_api_key: SecretStr

    @classmethod
    def from_env(cls) -> Self:
        return cls.model_validate({})


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    voyage_api_key: SecretStr
    anthropic_api_key: SecretStr
    gap_log_salt: SecretStr
    linear_api_key: SecretStr | None = None
    transfer_to: str = "+15555550100"
    index_path: Path = Path("build/index.sqlite")
    gap_log_path: Path = Path("build/gaps.jsonl")
    outbox_path: Path = Path("build/outbox.sqlite")

    @classmethod
    def from_env(cls) -> Self:
        return cls.model_validate({})


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    deepgram_api_key: SecretStr
    voice_model: str = "aura-2-thalia-en"
    still_working_after: float = 1.5

    @classmethod
    def from_env(cls) -> Self:
        return cls.model_validate({})


class WebSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    access_code: SecretStr | None = None
    max_call_seconds: float = 600.0
    recycle_rss_mb: int = 1400

    @field_validator("access_code")
    @classmethod
    def _reject_blank_code(cls, code: SecretStr | None) -> SecretStr | None:
        if code is not None and not code.get_secret_value().strip():
            raise ValueError("ACCESS_CODE must not be blank")
        return code

    @classmethod
    def from_env(cls) -> Self:
        return cls.model_validate({})
