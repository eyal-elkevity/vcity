from collections.abc import Callable
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic

from vcity.knowledge.relevance import ClaudeRelevanceClassifier, Relevance, system_prompt
from vcity.settings import SearchSettings


def _classifier(handler: Callable[[httpx2.Request], httpx2.Response]) -> ClaudeRelevanceClassifier:
    client = AsyncAnthropic(
        api_key="test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        max_retries=0,
    )
    return ClaudeRelevanceClassifier(client)


def _message(text: str) -> httpx2.Response:
    body: dict[str, Any] = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }
    return httpx2.Response(200, json=body)


def test_prompt_is_long_enough_to_cache() -> None:
    assert len(system_prompt().split()) > 300


async def test_http_error_falls_back_to_unclear() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        error = {"type": "api_error", "message": "boom"}
        return httpx2.Response(500, json={"type": "error", "error": error})

    assert await _classifier(handler).classify("dog license") is Relevance.UNCLEAR


async def test_unparseable_reply_falls_back_to_unclear() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return _message("not json")

    assert await _classifier(handler).classify("dog license") is Relevance.UNCLEAR


async def test_well_formed_reply_is_parsed() -> None:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return _message('{"relevance": "not_city"}')

    assert await _classifier(handler).classify("tell me a joke") is Relevance.NOT_CITY


@pytest.mark.live
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("Can I keep bees in my backyard?", Relevance.CITY),
        ("What time does City Hall open on Friday?", Relevance.CITY),
        ("Who is the president of France?", Relevance.NOT_CITY),
        ("Tell me a joke about cats.", Relevance.NOT_CITY),
    ],
)
async def test_live_classification(query: str, expected: Relevance) -> None:
    key = SearchSettings.from_env().anthropic_api_key.get_secret_value()
    assert await ClaudeRelevanceClassifier(AsyncAnthropic(api_key=key)).classify(query) is expected
