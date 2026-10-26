import json
from pathlib import Path

import httpx

from evals.audio.__main__ import problems
from evals.audio.client import Heard, record_message, ticketed_url
from evals.audio.model import AudioCase, Expect, load_cases

CASE = AudioCase(id="chickens", wav=Path("c.wav"), says="", expect=Expect(mentions=("hen",)))


def test_an_answer_that_was_never_cut_off_passes() -> None:
    heard = Heard(first_audio_seconds=1.1, agent_said=["Hens are allowed."])
    assert problems(CASE, heard) == []


def test_an_answer_cut_off_by_an_aside_fails() -> None:
    heard = Heard(first_audio_seconds=1.1, agent_said=["Hens are"], interruptions=1)
    assert problems(CASE, heard) == ["the agent was cut off 1 time(s)"]


def test_the_aside_case_plays_a_short_word_after_the_answer_starts() -> None:
    aside = next(case for case in load_cases() if case.id == "aside").aside
    assert aside is not None
    assert aside.wav == Path("callers/aside.wav")


def _sentence_output(text: str) -> dict[str, object]:
    return {"type": "bot-output", "data": {"aggregated_by": "sentence", "text": text}}


def test_the_aside_is_timed_from_the_first_agent_sentence() -> None:
    heard = Heard(answered_at=1.0)
    assert not heard.speaking_for(0.0)
    record_message(heard, _sentence_output("Let me check."))
    assert heard.speaking_at is not None
    assert heard.speaking_for(0.0)


def test_later_sentences_do_not_move_the_aside_anchor() -> None:
    heard = Heard()
    record_message(heard, _sentence_output("First."))
    anchor = heard.speaking_at
    record_message(heard, _sentence_output("Second."))
    assert heard.speaking_at == anchor


def test_an_early_hang_up_is_a_problem() -> None:
    heard = Heard(first_audio_seconds=1.1, agent_said=["Hens are allowed."], ended="time-limit")
    assert problems(CASE, heard) == ["call ended early: time-limit"]


def test_the_time_limit_message_is_recorded() -> None:
    heard = Heard()
    record_message(
        heard,
        {
            "label": "rtvi-ai",
            "type": "server-message",
            "data": {"type": "call-ended", "reason": "time-limit"},
        },
    )
    assert heard.ended == "time-limit"


async def test_tickets_come_from_the_session_endpoint() -> None:
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ticket": "t1"})

    url = await ticketed_url("https://x.run.app", "c", transport=httpx.MockTransport(answer))
    assert url == "wss://x.run.app/ws/browser?ticket=t1"
    assert str(requests[0].url) == "https://x.run.app/session"
    assert json.loads(requests[0].content) == {"code": "c"}
