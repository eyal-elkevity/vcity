import re
from collections.abc import Iterable, Sequence
from datetime import datetime, time
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from vcity.knowledge.code_snapshot import CodeSection
from vcity.knowledge.model import BREADCRUMB_SEPARATOR, Chunk, SourceKind
from vcity.knowledge.site_pages import Page, PageSection

MAX_CHUNK_CHARS = 2_000
MIN_SECTION_CHARS = 200
MAX_SLUG_CHARS = 48
SITE_BREADCRUMB = "coronaca.gov"
NEWS_BREADCRUMB = "City of Corona news"
_LETTERED = re.compile(r"^\(([A-Z])\)")

type Block = tuple[str, str]


def chunk_code_section(section: CodeSection) -> list[Chunk]:
    breadcrumb = BREADCRUMB_SEPARATOR.join(section.breadcrumb)
    cite_id = f"code:{section.cite_key}"
    text = "\n".join(section.paragraphs)
    if len(text) <= MAX_CHUNK_CHARS:
        return [_code_chunk(cite_id, breadcrumb, text, section.url)]
    used: set[str] = set()
    return [
        _code_chunk(f"{cite_id}#{_unique(label, used)}", breadcrumb, body, section.url)
        for label, body in _pack(_split_oversized(_lettered_blocks(section.paragraphs)), "\n")
    ]


def chunk_page(page: Page, tz: ZoneInfo) -> list[Chunk]:
    path = urlparse(page.url).path.strip("/")
    is_news = path.startswith("news/")
    kind = SourceKind.NEWS if is_news else SourceKind.SITE
    root = NEWS_BREADCRUMB if is_news else SITE_BREADCRUMB
    prefix = f"news:{path.removeprefix('news/')}" if is_news else f"site:{path}"
    published = datetime.combine(page.published, time(), tzinfo=tz) if page.published else None
    used: set[str] = set()
    return [
        Chunk(
            cite_id=f"{prefix}#{_unique(_slug(heading or 'intro'), used)}",
            kind=kind,
            breadcrumb=BREADCRUMB_SEPARATOR.join([root, page.title, *filter(None, [heading])]),
            text=body,
            url=page.url,
            date=published,
        )
        for heading, text in _merge_small(page.sections)
        for _, body in _pack(_paragraph_blocks(text), "\n\n")
    ]


def _code_chunk(cite_id: str, breadcrumb: str, text: str, url: str) -> Chunk:
    return Chunk(cite_id=cite_id, kind=SourceKind.CODE, breadcrumb=breadcrumb, text=text, url=url)


def _lettered_blocks(paragraphs: Sequence[str]) -> list[Block]:
    blocks: list[Block] = []
    for paragraph in paragraphs:
        letter = _LETTERED.match(paragraph)
        if letter or not blocks:
            blocks.append((letter[1] if letter else "intro", paragraph))
        else:
            label, body = blocks[-1]
            blocks[-1] = (label, f"{body}\n{paragraph}")
    return blocks


def _split_oversized(blocks: Iterable[Block]) -> list[Block]:
    return [
        (label, paragraph)
        for label, body in blocks
        for paragraph in (body.split("\n") if len(body) > MAX_CHUNK_CHARS else [body])
    ]


def _paragraph_blocks(text: str) -> list[Block]:
    return [("", paragraph) for paragraph in text.split("\n\n")]


def _pack(blocks: Iterable[Block], joiner: str) -> list[Block]:
    packed: list[Block] = []
    for label, body in blocks:
        if packed and len(packed[-1][1]) + len(joiner) + len(body) <= MAX_CHUNK_CHARS:
            packed[-1] = (packed[-1][0], f"{packed[-1][1]}{joiner}{body}")
        else:
            packed.append((label, body))
    return packed


def _merge_small(sections: Sequence[PageSection]) -> list[tuple[str | None, str]]:
    merged: list[tuple[str | None, str]] = []
    for section in sections:
        if merged and len(section.text) < MIN_SECTION_CHARS:
            heading, text = merged[-1]
            lead = f"{section.heading}\n" if section.heading else ""
            merged[-1] = (heading, f"{text}\n\n{lead}{section.text}")
        else:
            merged.append((section.heading, section.text))
    return merged


def _unique(base: str, used: set[str]) -> str:
    candidate, suffix = base, 1
    while candidate in used:
        suffix += 1
        candidate = f"{base}-{suffix}"
    used.add(candidate)
    return candidate


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:MAX_SLUG_CHARS].rstrip("-")
    return slug or "section"
