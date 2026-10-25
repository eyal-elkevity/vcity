import re
from collections.abc import Callable

from num2words import num2words

type Rule = tuple[re.Pattern[str], Callable[[re.Match[str]], str]]

_PHONE = r"(?:\+1[\s.-]?|(?<![\d+]))\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)"
_AMOUNT = r"\d{1,3}(?:,\d{3})+|\d+"
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def speakable(text: str) -> str:
    for pattern, say in _RULES:
        text = pattern.sub(say, text)
    return text


def _words(number: int) -> str:
    return num2words(number, lang="en").replace(",", "").replace("-", " ")


def _digits(digits: str) -> str:
    return " ".join(_words(int(digit)) for digit in digits)


def _padded(digits: str) -> str:
    if len(digits) > 1 and digits.startswith("0"):
        return f"oh {_words(int(digits))}"
    return _words(int(digits))


def _number(written: str) -> str:
    whole, _, fraction = written.replace(",", "").partition(".")
    spoken = _words(int(whole))
    return f"{spoken} point {_digits(fraction)}" if fraction else spoken


def _year(written: str) -> str:
    return num2words(int(written), lang="en", to="year").replace("-", " ")


def _section(match: re.Match[str]) -> str:
    return " point ".join(_padded(part) for part in match.groups())


def _phone(match: re.Match[str]) -> str:
    return ", ".join(_digits(group) for group in match.groups())


def _money(match: re.Match[str]) -> str:
    dollars = int(match[1].replace(",", ""))
    spoken = f"{_words(dollars)} dollar{'' if dollars == 1 else 's'}"
    if match[2] and int(match[2]):
        spoken += f" and {_words(int(match[2]))} cents"
    return spoken


def _time(match: re.Match[str]) -> str:
    hour, minutes, half = match[1], match[2], match[3].upper()
    spoken = _words(int(hour))
    if minutes and minutes != "00":
        spoken += f" {_padded(minutes)}"
    ends_sentence = match[0].endswith(".") and match.end() == len(match.string)
    return f"{spoken} {half} M{'.' if ends_sentence else ''}"


def _percent(match: re.Match[str]) -> str:
    return f"{_number(match[1])} percent"


def _date(match: re.Match[str]) -> str:
    day = num2words(int(match[2]), lang="en", to="ordinal").replace("-", " ")
    year = f", {_year(match[3])}" if match[3] else ""
    return f"{match[1]} {day}{year}"


def _year_after_preposition(match: re.Match[str]) -> str:
    return f"{match[1]} {_year(match[2])}"


_RULES: tuple[Rule, ...] = (
    (re.compile(r"\b(\d+)\.(\d+)\.(\d+)\b"), _section),
    (re.compile(_PHONE), _phone),
    (re.compile(rf"\$({_AMOUNT})(?:\.(\d{{2}}))?"), _money),
    (re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.IGNORECASE), _time),
    (re.compile(r"(\d+(?:\.\d+)?)\s?%"), _percent),
    (re.compile(rf"\b({_MONTHS}) (\d{{1,2}})(?:st|nd|rd|th)?\b(?:, ((?:19|20)\d\d)\b)?"), _date),
    (re.compile(r"\b(in|since|by|until|before|after) ((?:19|20)\d\d)\b"), _year_after_preposition),
    (re.compile(rf"(?:{_AMOUNT})(?:\.\d+)?"), lambda match: _number(match[0])),
)
