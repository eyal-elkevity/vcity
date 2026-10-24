import gzip
from datetime import date
from pathlib import Path

import pytest

from vcity.knowledge.code_snapshot import (
    AMENDMENT_HISTORY_NOTE,
    CodeSection,
    CodeSnapshot,
    SnapshotMismatchError,
    SnapshotNode,
    load_snapshot,
    parse_sections,
)

EXCERPT = Path(__file__).parents[1] / "fixtures" / "code" / "title-6-excerpt.json"
COMMITTED = Path(__file__).parents[2] / "data/raw/code/corona-municipal-code.json.gz"
COMMITTED_SECTION_COUNT = 3553


@pytest.fixture
def snapshot() -> CodeSnapshot:
    return load_snapshot(EXCERPT)


def section(sections: list[CodeSection], number: str) -> CodeSection:
    return next(s for s in sections if s.number == number)


def test_every_listed_section_is_parsed(snapshot: CodeSnapshot) -> None:
    assert [s.number for s in parse_sections(snapshot)] == [
        "6.04.010",
        "6.04.020",
        "6.04.030",
        "6.20.010",
        "6.20.020",
        "6.20.030",
        "6.20.040",
    ]


def test_breadcrumb_runs_from_code_to_section(snapshot: CodeSnapshot) -> None:
    chickens = section(parse_sections(snapshot), "6.20.040")
    assert chickens.breadcrumb == (
        "Corona Municipal Code",
        "Title 6 Animals",
        "Chapter 6.20 Keeping of Female Chickens",
        "6.20.040 Criteria and standards",
    )
    assert chickens.chapter == "6.20"
    assert chickens.cite_key == "6.20.040"


def test_lettered_paragraphs_and_tables_are_kept(snapshot: CodeSnapshot) -> None:
    paragraphs = section(parse_sections(snapshot), "6.20.040").paragraphs
    assert paragraphs[0].startswith("(A) Minimum lot size.")
    assert paragraphs[2].startswith(
        "Minimum Lot Size | Maximum Number of Chickens\n5,000 to 9,999 sq. ft | 4\n"
    )


def test_sections_without_anchors_still_parse(snapshot: CodeSnapshot) -> None:
    dog = section(parse_sections(snapshot), "6.04.030")
    assert dog.heading == "Dog"
    assert dog.paragraphs[0].startswith('(A) "Dog" means any male')


def test_amendment_history_is_dropped(snapshot: CodeSnapshot) -> None:
    text = "\n".join(p for s in parse_sections(snapshot) for p in s.paragraphs)
    assert "Ord. 3088" not in text
    assert "`78 Code" not in text


def test_gzipped_snapshot_loads_identically(tmp_path: Path, snapshot: CodeSnapshot) -> None:
    packed = tmp_path / "code.json.gz"
    packed.write_bytes(gzip.compress(EXCERPT.read_bytes()))
    assert load_snapshot(packed) == snapshot


def test_toc_entry_that_does_not_match_its_page_fails(snapshot: CodeSnapshot) -> None:
    broken = _retitle_first_section(snapshot, "6.04.015 Not the page that was rendered.")
    with pytest.raises(SnapshotMismatchError, match=r"6\.04\.015"):
        parse_sections(broken)


def test_misnumbered_section_gets_chapter_qualified_key() -> None:
    misnumbered = CodeSection(
        chapter="15.25",
        number="15.12.150",
        heading="Addition - Section 613",
        breadcrumb=(),
        paragraphs=("text",),
        url="https://example.test",
    )
    assert misnumbered.cite_key == "15.25/15.12.150"


def test_toc_number_that_is_only_a_prefix_of_the_heading_fails(snapshot: CodeSnapshot) -> None:
    broken = _retitle_first_section(snapshot, "6.04.01 Dog")
    with pytest.raises(SnapshotMismatchError, match=r"lists 6\.04\.01 but"):
        parse_sections(broken)


AMENDMENT_HISTORY_EXAMPLES = [
    "(\u201878 Code, § 17.62.110.) (Ord. 2813 …)",
    "(`78 code, § 5.30.110.)",
    "(`78 § Code, 10.08.050.)",
    "(Ord 2945 § 4, 2008.)",
    "((Ord. 3399 § 3, 2024; …)",
]


@pytest.mark.parametrize("note", AMENDMENT_HISTORY_EXAMPLES)
def test_amendment_history_variants_match(note: str) -> None:
    assert AMENDMENT_HISTORY_NOTE.match(note)


def test_ordinary_lettered_paragraph_is_not_amendment_history() -> None:
    assert AMENDMENT_HISTORY_NOTE.match("(A) Ordinary text.") is None


@pytest.mark.parametrize("note", AMENDMENT_HISTORY_EXAMPLES)
def test_amendment_history_variants_are_dropped_from_a_section(note: str) -> None:
    body = (
        '<div classname="rbox Normal-Level">(A) Ordinary text.</div>'
        f'<div classname="rbox Normal-Level">{note}</div>'
    )
    (only,) = parse_sections(_leaf_snapshot(body))
    assert only.paragraphs == ("(A) Ordinary text.",)


def test_text_around_a_table_survives_in_order() -> None:
    body = (
        '<div classname="rbox Normal-Level">Before text'
        "<table><tr><td>A</td><td>B</td></tr><tr><td>1</td><td>2</td></tr></table>"
        "after text</div>"
    )
    (only,) = parse_sections(_leaf_snapshot(body))
    assert only.paragraphs == ("Before text\nA | B\n1 | 2\nafter text",)


def test_every_table_in_a_paragraph_survives() -> None:
    body = (
        '<div classname="rbox Normal-Level">First<table><tr><td>a</td></tr></table>'
        "between<table><tr><td>b</td></tr></table>last</div>"
    )
    (only,) = parse_sections(_leaf_snapshot(body))
    assert only.paragraphs == ("First\na\nbetween\nb\nlast",)


def test_committed_snapshot_parses_every_section() -> None:
    sections = parse_sections(load_snapshot(COMMITTED))
    assert len(sections) == COMMITTED_SECTION_COUNT


def test_committed_snapshot_has_no_amendment_history_paragraphs() -> None:
    sections = parse_sections(load_snapshot(COMMITTED))
    leaked = [p for s in sections for p in s.paragraphs if AMENDMENT_HISTORY_NOTE.match(p)]
    assert leaked == []


def _leaf_snapshot(body: str) -> CodeSnapshot:
    leaf = SnapshotNode(
        doc_id="leaf",
        title="6.04.010 Dog",
        url="https://example.test/6.04.010",
        html=f"<h5>6.04.010 Dog</h5>{body}",
    )
    chapter = SnapshotNode(doc_id="chapter", title="Chapter 6.04 Dogs", children=(leaf,))
    title = SnapshotNode(doc_id="title", title="Title 6 Animals", children=(chapter,))
    return CodeSnapshot(
        source="test", code_uuid="test", saved_on=date(2026, 10, 24), titles=(title,)
    )


def _retitle_first_section(snapshot: CodeSnapshot, title: str) -> CodeSnapshot:
    title_node = snapshot.titles[0]
    chapter = title_node.children[0]
    first, *rest = chapter.children
    renamed: SnapshotNode = first.model_copy(update={"title": title})
    chapter = chapter.model_copy(update={"children": (renamed, *rest)})
    title_node = title_node.model_copy(update={"children": (chapter, *title_node.children[1:])})
    return snapshot.model_copy(update={"titles": (title_node,)})
