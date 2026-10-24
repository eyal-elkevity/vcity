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
