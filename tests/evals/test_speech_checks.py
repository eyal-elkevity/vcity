from evals.speech.__main__ import Phrase, missing


def test_every_expected_reading_must_be_heard() -> None:
    phrase = Phrase(text="", heard=("five thousand", "nine nine"))
    assert missing(phrase, "Lots of five-thousand square feet.") == ["nine nine"]


def test_any_alternative_reading_counts() -> None:
    phrase = Phrase(text="", heard=("point o twenty|point oh twenty",))
    assert missing(phrase, "six point oh twenty") == []
