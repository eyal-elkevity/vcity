from vcity.agent.citations import Citation, cited, filed
from vcity.agent.conversation import ToolCall, Turn, Usage
from vcity.agent.tools import FiledRequest, ToolResult
from vcity.knowledge.model import Chunk, SourceKind

CHICKENS = Chunk(
    "code:6.20.040#A",
    SourceKind.CODE,
    "Corona Municipal Code > Title 6 Animals > 6.20.040 Criteria and standards",
    "Four hens.",
    "https://code.test/6.20.040",
)
CHICKENS_F = Chunk("code:6.20.040#F", SourceKind.CODE, "Code > 6.20.040", "Ten hens.", CHICKENS.url)
PARCELS = Chunk(
    "code:16.20.040", SourceKind.CODE, "Code > 16.20.040", "Waivers.", "https://code.test/16"
)
FAIR = Chunk(
    "events:fair",
    SourceKind.EVENTS,
    "City of Corona events > **UPDATED** Fall Festival",
    "Saturday.",
    "https://e.test/f",
)
PARK = Chunk(
    "site:park",
    SourceKind.SITE,
    "coronaca.gov > City Park > When is it open?",
    "Open daily.",
    "https://s.test/p",
)


def turn(text: str, *results: ToolResult) -> Turn:
    calls = tuple(ToolCall("tool", {}, r) for r in results)
    return Turn(text, calls, (), Usage())


def test_code_sections_are_cited_only_when_the_reply_names_them() -> None:
    found = ToolResult("{}", found=(CHICKENS, CHICKENS_F, PARCELS))
    assert cited(turn("Under section 6.20.040 you can keep four.", found)) == (
        Citation("Municipal Code § 6.20.040", CHICKENS.url),
    )
    assert cited(turn("You can keep four.", found)) == ()


def test_a_longer_section_number_does_not_cite_its_suffix() -> None:
    found = ToolResult("{}", found=(CHICKENS, PARCELS))
    assert cited(turn("Section 16.20.040 covers waivers.", found)) == (
        Citation("Municipal Code § 16.20.040", PARCELS.url),
    )


def test_news_events_and_pages_are_cited_only_when_the_reply_names_their_title() -> None:
    found = ToolResult("{}", found=(FAIR, PARK))
    assert cited(turn("The fall festival is on Saturday.", found)) == (
        Citation("Fall Festival", FAIR.url),
    )
    assert cited(turn("City Park, open daily.", found)) == (Citation("City Park", PARK.url),)
    assert cited(turn("I couldn't find a concert in the calendar.", found)) == ()


def test_citations_are_capped() -> None:
    results = [
        ToolResult(
            "{}", found=(Chunk(f"site:{n}", SourceKind.SITE, f"Site > Page {n}", "", f"u{n}"),)
        )
        for n in range(5)
    ]
    assert len(cited(turn("Page 0, page 1, page 2, page 3 and page 4.", *results))) == 3


def test_filed_is_the_last_request_of_the_turn() -> None:
    first = FiledRequest("111111", "Public Works", None)
    second = FiledRequest("222222", "Public Works", "Monday, November 2 at 7:00 AM")
    assert filed(turn("Done.", ToolResult("{}", filed=first), ToolResult("{}"))) == first
    assert (
        filed(turn("Done.", ToolResult("{}", filed=first), ToolResult("{}", filed=second)))
        == second
    )
    assert filed(turn("Done.")) is None
