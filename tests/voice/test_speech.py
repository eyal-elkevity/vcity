import pytest

from vcity.voice.speech import speakable


@pytest.mark.parametrize(
    ("written", "spoken"),
    [
        ("section 6.20.020", "section six point twenty point oh twenty"),
        ("section 17.84.100.", "section seventeen point eighty four point one hundred."),
        ("(951) 736-2400", "nine five one, seven three six, two four zero zero"),
        ("951-736-2400", "nine five one, seven three six, two four zero zero"),
        ("call +19517362400.", "call nine five one, seven three six, two four zero zero."),
        ("+1 951-736-2400", "nine five one, seven three six, two four zero zero"),
        ("$25", "twenty five dollars"),
        ("$1", "one dollar"),
        ("$1,250.50", "one thousand two hundred and fifty dollars and fifty cents"),
        ("$40.00, due now", "forty dollars, due now"),
        ("at 5:30 PM.", "at five thirty P M."),
        ("8 a.m. to 5 p.m. daily", "eight A M to five P M daily"),
        ("at 9:05 am", "at nine oh five A M"),
        ("about 2.5% of", "about two point five percent of"),
        ("on October 27, 2026.", "on October twenty seventh, twenty twenty six."),
        ("on March 3rd", "on March third"),
        ("since 1998", "since nineteen ninety eight"),
        ("lots of 1,950 square feet", "lots of one thousand nine hundred and fifty square feet"),
        ("5,000 to 9,999 feet", "five thousand to nine thousand nine hundred and ninety nine feet"),
        ("at least 7,000, or", "at least seven thousand, or"),
        ("four hens and 10 feet", "four hens and ten feet"),
    ],
)
def test_numbers_are_written_out_as_spoken(written: str, spoken: str) -> None:
    assert speakable(written) == spoken


def test_text_without_numbers_is_unchanged() -> None:
    text = "Roosters aren't allowed, and eggs can't be sold."
    assert speakable(text) == text
