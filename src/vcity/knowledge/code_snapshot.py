import gzip
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict
from selectolax.lexbor import LexborHTMLParser, LexborNode

CODE_NAME = "Corona Municipal Code"
_SECTION_NUMBER = re.compile(r"^\d+[A-Z]?\.\d+[A-Z]?\.\d+[A-Z]?")
_CHAPTER_NUMBER = re.compile(r"^CHAPTER (\d+[A-Z]?\.\d+[A-Z]?)\b", re.IGNORECASE)
_ROMAN_NUMERAL = re.compile(r"^[IVXLC]+\.?$")
_MINOR_WORDS = frozenset(
    {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}
)
# American Legal appends amendment history ("(`78 Code, § 6.04.030.)", "(Ord. 3088 ...)").
_HISTORY_NOTE = re.compile(r"^\((`\d{2} Code|Ord\.)")
_WHITESPACE = re.compile(r"\s+")


class SnapshotNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str
    title: str
    url: str | None = None
    html: str | None = None
    children: tuple["SnapshotNode", ...] = ()


class CodeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    code_uuid: str
    saved_on: date
    titles: tuple[SnapshotNode, ...]


@dataclass(frozen=True, slots=True)
class CodeSection:
    chapter: str
    number: str
    heading: str
    breadcrumb: tuple[str, ...]
    paragraphs: tuple[str, ...]
    url: str

    @property
    def cite_key(self) -> str:
        # The published code misnumbers a few sections, e.g. chapter 15.25 contains a "15.12.150".
        if self.number.startswith(f"{self.chapter}."):
            return self.number
        return f"{self.chapter}/{self.number}"


class SnapshotMismatchError(ValueError):
    pass


def load_snapshot(path: Path) -> CodeSnapshot:
    raw = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    return CodeSnapshot.model_validate_json(raw)


def parse_sections(snapshot: CodeSnapshot) -> list[CodeSection]:
    sections = [
        section for title in snapshot.titles for section in _walk(title, (CODE_NAME,), None)
    ]
    keys = Counter(section.cite_key for section in sections)
    duplicates = sorted(key for key, count in keys.items() if count > 1)
    if duplicates:
        raise SnapshotMismatchError(f"sections appear more than once: {duplicates}")
    return sections


def _walk(node: SnapshotNode, trail: tuple[str, ...], chapter: str | None) -> Iterator[CodeSection]:
    if node.children:
        chapter_match = _CHAPTER_NUMBER.match(node.title)
        for child in node.children:
            yield from _walk(
                child,
                (*trail, _title_case(node.title)),
                chapter_match[1] if chapter_match else chapter,
            )
        return
    number = _SECTION_NUMBER.match(node.title)
    if number is None or chapter is None or node.html is None or node.url is None:
        return
    section = _parse_leaf(chapter, number[0], node.html, node.url, trail)
    if section is not None:
        yield section


def _parse_leaf(
    chapter: str, number: str, html: str, url: str, trail: tuple[str, ...]
) -> CodeSection | None:
    tree = LexborHTMLParser(html)
    heading_node = tree.css_first("h5")
    heading_text = _normalize(heading_node.text()) if heading_node else ""
    if not heading_text.startswith(number):
        raise SnapshotMismatchError(
            f"table of contents lists {number} but its page is headed {heading_text!r}"
        )
    heading = heading_text.removeprefix(number).strip().rstrip(".")
    paragraphs = tuple(
        text
        for node in tree.css('[classname~="Normal-Level"]')
        if (text := _paragraph_text(node)) and not _HISTORY_NOTE.match(text)
    )
    if not paragraphs or "(Reserved)" in heading:
        return None
    return CodeSection(
        chapter=chapter,
        number=number,
        heading=heading,
        breadcrumb=(*trail, f"{number} {heading}"),
        paragraphs=paragraphs,
        url=url,
    )


def _paragraph_text(node: LexborNode) -> str:
    table = node.css_first("table")
    if table is None:
        return _normalize(node.text())
    rows = (
        " | ".join(_normalize(cell.text()) for cell in row.css("td, th")) for row in table.css("tr")
    )
    return "\n".join(rows)


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text.replace("\xa0", " ")).strip()


def _title_case(title: str) -> str:
    words = title.split()
    return " ".join(
        word
        if _ROMAN_NUMERAL.match(word) or any(ch.isdigit() for ch in word)
        else word.lower()
        if index and word.lower() in _MINOR_WORDS
        else word.capitalize()
        for index, word in enumerate(words)
    )
