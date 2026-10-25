from pathlib import Path
from typing import Self

from pydantic import SecretStr
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
    elevenlabs_api_key: SecretStr
    elevenlabs_voice_id: str = "XrExE9yKIg1WjnnlVkGX"
    still_working_after: float = 1.5

    @classmethod
    def from_env(cls) -> Self:
        return cls.model_validate({})
