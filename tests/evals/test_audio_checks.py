from pathlib import Path

from evals.audio.__main__ import problems
from evals.audio.client import Heard
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
