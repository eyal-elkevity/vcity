import logging
from enum import StrEnum
from functools import cache
from importlib.resources import files
from typing import Protocol

from anthropic import APIError, AsyncAnthropic
from pydantic import BaseModel, ConfigDict, ValidationError

CLASSIFIER_MODEL = "claude-haiku-5-5"
_log = logging.getLogger(__name__)


class Relevance(StrEnum):
    CITY = "city"
    NOT_CITY = "not_city"
    UNCLEAR = "unclear"


class RelevanceClassifier(Protocol):
    async def classify(self, query: str) -> Relevance: ...


class _Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relevance: Relevance


@cache
def system_prompt() -> str:
    return files("vcity.knowledge").joinpath("prompts/relevance.md").read_text(encoding="utf-8")


class ClaudeRelevanceClassifier:
    def __init__(self, client: AsyncAnthropic, model: str = CLASSIFIER_MODEL) -> None:
        self._client = client
        self._model = model

    async def classify(self, query: str) -> Relevance:
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=32,
                thinking={"type": "disabled"},
                system=[
                    {
                        "type": "text",
                        "text": system_prompt(),
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[{"role": "user", "content": query}],
                output_format=_Verdict,
            )
        except (APIError, ValidationError):
            _log.warning("relevance classifier failed; searching anyway", exc_info=True)
            return Relevance.UNCLEAR
        verdict = response.parsed_output
        return verdict.relevance if verdict else Relevance.UNCLEAR
