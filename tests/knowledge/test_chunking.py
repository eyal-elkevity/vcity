from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from vcity.knowledge.chunking import MAX_CHUNK_CHARS, chunk_code_section, chunk_page
from vcity.knowledge.code_snapshot import CodeSection, load_snapshot, parse_sections
from vcity.knowledge.model import SourceKind
from vcity.knowledge.site_pages import Page, PageSection, parse_page

LA = ZoneInfo("America/Los_Angeles")
FIXTURES = Path(__file__).parents[1] / "fixtures"
BREADCRUMB = ("Corona Municipal Code", "Title 6 Animals", "6.04.020 Definitions")


def code_section(*paragraphs: str) -> CodeSection:
    return CodeSection(
        chapter="6.04",
        number="6.04.020",
        heading="Definitions",
        breadcrumb=BREADCRUMB,
        paragraphs=paragraphs,
        url="https://example.test/6.04.020",
    )


def test_short_code_section_is_one_chunk() -> None:
    sections = parse_sections(load_snapshot(FIXTURES / "code" / "title-6-excerpt.json"))
    license_required = next(s for s in sections if s.number == "6.20.030")
    (chunk,) = chunk_code_section(license_required)
    assert chunk.cite_id == "code:6.20.030"
    assert chunk.kind is SourceKind.CODE
    assert chunk.breadcrumb == (
        "Corona Municipal Code > Title 6 Animals > "
        "Chapter 6.20 Keeping of Female Chickens > 6.20.030 License required"
    )


def test_long_code_section_splits_at_lettered_subsections() -> None:
    lettered = [f"({letter}) " + letter.lower() * 1_200 for letter in "ABCD"]
    chunks = chunk_code_section(code_section("Preamble.", *lettered, "(1) numbered detail"))
    assert [c.cite_id for c in chunks] == [
        "code:6.04.020#intro",
        "code:6.04.020#B",
        "code:6.04.020#C",
        "code:6.04.020#D",
    ]
    assert chunks[0].text.startswith("Preamble.\n(A) ")
    assert chunks[-1].text.endswith("(1) numbered detail")
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)


def test_oversized_single_paragraph_is_kept_whole() -> None:
    giant = "(A) " + "x" * (MAX_CHUNK_CHARS * 2)
    (chunk,) = chunk_code_section(code_section(giant))
    assert chunk.cite_id == "code:6.04.020#A"
    assert chunk.text == giant


def test_section_without_lettering_splits_by_paragraph() -> None:
    chunks = chunk_code_section(code_section(*["p" * 600] * 10))
    assert [c.cite_id for c in chunks] == [
        "code:6.04.020#intro",
        "code:6.04.020#intro-2",
        "code:6.04.020#intro-3",
        "code:6.04.020#intro-4",
    ]
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)
    assert "\n".join(c.text for c in chunks) == "\n".join(["p" * 600] * 10)


def test_oversized_lettered_block_splits_by_paragraph() -> None:
    numbered = [f"({number}) " + "n" * 700 for number in range(1, 9)]
    chunks = chunk_code_section(code_section("(A) Lead.", *numbered))
    ids = [c.cite_id for c in chunks]
    assert ids == [f"code:6.04.020#{label}" for label in ("A", "A-2", "A-3", "A-4")]
    assert all(len(c.text) <= MAX_CHUNK_CHARS for c in chunks)


def test_restarted_lettering_gets_unique_labels() -> None:
    paragraphs = [f"({letter}) " + "w" * 1_500 for letter in "ABAB"]
    assert [c.cite_id for c in chunk_code_section(code_section(*paragraphs))] == [
        "code:6.04.020#A",
        "code:6.04.020#B",
        "code:6.04.020#A-2",
        "code:6.04.020#B-2",
    ]


def test_site_page_chunks_cite_path_and_heading() -> None:
    html = (FIXTURES / "site" / "animal-services.html").read_text(encoding="utf-8")
    page = parse_page("https://www.coronaca.gov/departments/animal-services", html)
    assert page is not None
    chunks = chunk_page(page, LA)
    assert [c.cite_id for c in chunks] == [
        "site:departments/animal-services#intro",
        "site:departments/animal-services#monthly-vaccine-clinic-at-the-corona-animal-shel",
    ]
    assert chunks[1].breadcrumb == (
        "coronaca.gov > Animal Services & Enforcement > "
        "Monthly Vaccine Clinic at the Corona Animal Shelter"
    )
    assert {c.kind for c in chunks} == {SourceKind.SITE}


def test_long_headings_make_bounded_anchors() -> None:
    page = Page(
        url="https://www.coronaca.gov/faq",
        title="FAQ",
        published=None,
        sections=(PageSection("Why " + "very " * 30 + "long?", "a" * 300),),
    )
    (chunk,) = chunk_page(page, LA)
    anchor = chunk.cite_id.split("#")[1]
    assert len(anchor) <= 48
    assert not anchor.endswith("-")


def test_news_chunks_are_dated_at_local_midnight() -> None:
    html = (FIXTURES / "site" / "2026-state-of-the-city.html").read_text(encoding="utf-8")
    page = parse_page("https://www.coronaca.gov/news/2026-state-of-the-city", html)
    assert page is not None
    chunks = chunk_page(page, LA)
    assert chunks[0].cite_id == "news:2026-state-of-the-city#intro"
    assert {c.kind for c in chunks} == {SourceKind.NEWS}
    assert {c.date for c in chunks} == {datetime(2026, 9, 10, tzinfo=LA)}


def test_small_sections_merge_into_the_previous_one() -> None:
    page = Page(
        url="https://www.coronaca.gov/parks",
        title="Parks",
        published=None,
        sections=(
            PageSection(heading=None, text="p" * 300),
            PageSection(heading="Hours", text="Dawn to dusk."),
        ),
    )
    (chunk,) = chunk_page(page, LA)
    assert chunk.cite_id == "site:parks#intro"
    assert chunk.text.endswith("\n\nHours\nDawn to dusk.")


def test_repeated_headings_get_unique_anchors() -> None:
    body = "q" * 300
    page = Page(
        url="https://www.coronaca.gov/faq",
        title="FAQ",
        published=date(2026, 1, 1),
        sections=(PageSection("Question", body), PageSection("Question", body)),
    )
    assert [c.cite_id for c in chunk_page(page, LA)] == [
        "site:faq#question",
        "site:faq#question-2",
    ]
