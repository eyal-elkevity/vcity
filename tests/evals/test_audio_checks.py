from pathlib import Path

from evals.audio.__main__ import problems
from evals.audio.client import Heard, record_message
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
