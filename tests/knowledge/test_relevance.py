import pytest
from anthropic import AsyncAnthropic

from vcity.knowledge.relevance import ClaudeRelevanceClassifier, Relevance, system_prompt
from vcity.settings import SearchSettings


def test_prompt_is_long_enough_to_cache() -> None:
    assert len(system_prompt().split()) > 300


async def test_api_failure_falls_back_to_unclear() -> None:
    unreachable = AsyncAnthropic(
        api_key="test", base_url="http://127.0.0.1:9", max_retries=0, timeout=2
    )
    assert await ClaudeRelevanceClassifier(unreachable).classify("dog license") is Relevance.UNCLEAR


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
