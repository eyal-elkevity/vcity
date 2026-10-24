import re
from dataclasses import dataclass
from datetime import date, datetime

import trafilatura
from selectolax.lexbor import LexborHTMLParser

NEWS_DATE_SELECTOR = ".blog-post-header1_author-text > div"
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1(?=(\w)?)")
_STRAY_MARKERS = re.compile(r"^[*_]+|[*_]+$")
_RULE_OR_LINK_LABEL = re.compile(r"-{3,}|[*_]{3,}|.{0,30}»")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class PageSection:
    heading: str | None
    text: str


@dataclass(frozen=True, slots=True)
class Page:
    url: str
    title: str
    published: date | None
    sections: tuple[PageSection, ...]


def parse_page(url: str, html: str) -> Page | None:
    markdown = trafilatura.extract(
        html, url=url, output_format="markdown", include_tables=True, favor_recall=True
    )
    if not markdown:
        return None
    tree = LexborHTMLParser(html)
    title = _page_title(tree)
    return Page(
        url=url,
        title=title,
        published=_news_date(tree),
        sections=_split_at_headings(markdown, title),
    )


def _page_title(tree: LexborHTMLParser) -> str:
    for heading in tree.css("h1"):
        if text := _normalize(heading.text()):
            return text
    title = tree.css_first("title")
    return _normalize(title.text()).split(" | ")[0] if title else ""


def _split_at_headings(markdown: str, title: str) -> tuple[PageSection, ...]:
    heading: str | None = None
    body: list[str] = []
    sections: list[PageSection] = []

    def flush() -> None:
        text = _EXTRA_BLANK_LINES.sub("\n\n", "\n".join(body)).strip()
        if text:
            sections.append(PageSection(heading=heading, text=text))
        body.clear()

    for line in markdown.splitlines():
        match = _HEADING.match(line)
        if match is None:
            body.append(_clean(line))
            continue
        flush()
        text = _clean(match[1])
        heading = None if text.casefold() == title.casefold() else text
    flush()
    return tuple(sections)


def _clean(line: str) -> str:
    unbolded = _BOLD.sub(lambda m: m[2] + (" " if m[3] else ""), line.strip())
    stripped = _STRAY_MARKERS.sub("", unbolded).strip()
    return "" if _RULE_OR_LINK_LABEL.fullmatch(stripped) else stripped


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _news_date(tree: LexborHTMLParser) -> date | None:
    for node in tree.css(NEWS_DATE_SELECTOR):
        try:
            return datetime.strptime(node.text(strip=True), "%B %d, %Y").date()  # noqa: DTZ007
        except ValueError:
            continue
    return None
