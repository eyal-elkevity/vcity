import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from vcity.agent.routing import RecordingTransfer, TransferRequest
from vcity.agent.tools import EMERGENCY_SCRIPT, CallContext, Toolbox, ToolName
from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.config.policy import corona_policy
from vcity.domain.departments import Department
from vcity.domain.hours import TicketReason
from vcity.domain.tickets import TicketRef
from vcity.knowledge.model import Chunk, SearchResult, SearchStatus, SourceKind
from vcity.knowledge.search import SearchUnavailableError
from vcity.tickets.drafts import TicketDraft
from vcity.tickets.sinks import MockSink, TicketSinkError

LA = ZoneInfo("America/Los_Angeles")
TUESDAY_10AM = datetime(2026, 10, 27, 10, 0, tzinfo=LA)
FRIDAY_EVENING = datetime(2026, 10, 30, 19, 30, tzinfo=LA)
CALL = CallContext("CA-TEST", "+19515550142")
DIRECTORY = load_directory(DEFAULT_DIRECTORY_PATH, transfer_to="+15555550100")
CHICKENS = Chunk(
    "code:6.20.040", SourceKind.CODE, "Code > Chickens", "Up to ten.", "https://example.test"
)


class FakeSearch:
    def __init__(self, result: SearchResult | None = None) -> None:
        self.result = result
        self.queries: list[tuple[str, SourceKind | None, str | None]] = []

    async def search(
        self, query: str, kind: SourceKind | None = None, *, caller_id: str | None = None
    ) -> SearchResult:
        self.queries.append((query, kind, caller_id))
        if self.result is None:
            raise SearchUnavailableError("down")
        return self.result


def toolbox(
    now: datetime, search: FakeSearch | None = None
) -> tuple[Toolbox, MockSink, RecordingTransfer]:
    tickets, transfers = MockSink(), RecordingTransfer()
    box = Toolbox(
        search=search or FakeSearch(SearchResult(SearchStatus.MATCH, (CHICKENS,))),
        directory=DIRECTORY,
        policy=corona_policy(),
        tickets=tickets,
        transfers=transfers,
        clock=lambda: now,
    )
    return box, tickets, transfers


async def test_search_passes_kind_and_caller_and_returns_citable_chunks() -> None:
    search = FakeSearch(SearchResult(SearchStatus.MATCH, (CHICKENS,)))
    box, _, _ = toolbox(TUESDAY_10AM, search)
    result = await box.run(ToolName.SEARCH_CITY, {"query": "chickens", "kind": "code"}, CALL)
    assert search.queries == [("chickens", SourceKind.CODE, "+19515550142")]
    assert json.loads(result.content) == {
        "status": "match",
        "results": [
            {
                "cite": "code:6.20.040",
                "source": "Code > Chickens",
                "text": "Up to ten.",
                "date": None,
            }
        ],
    }


async def test_search_outage_is_reported_not_raised() -> None:
    box, _, _ = toolbox(TUESDAY_10AM, FakeSearch(None))
    result = await box.run(ToolName.SEARCH_CITY, {"query": "chickens"}, CALL)
    assert json.loads(result.content) == {"status": "unavailable"}
    assert not result.is_error


async def test_open_office_transfers_the_call() -> None:
    box, tickets, transfers = toolbox(TUESDAY_10AM)
    result = await box.run(
        ToolName.ROUTE_REQUEST, {"department": "utilities", "summary": "Water bill question"}, CALL
    )
    assert json.loads(result.content) == {"outcome": "transfer", "department": "Utilities"}
    assert transfers.requests == [
        TransferRequest(Department.UTILITIES, "+15555550100", "Water bill question")
    ]
    assert tickets.created == []


async def test_closed_office_files_a_ticket_with_reference_and_reopening() -> None:
    box, tickets, transfers = toolbox(FRIDAY_EVENING)
    result = await box.run(
        ToolName.ROUTE_REQUEST,
        {"department": "public_works", "summary": "Pothole", "location": "Main St and 6th St"},
        CALL,
    )
    payload = json.loads(result.content)
    (draft,) = tickets.created
    assert transfers.requests == []
    assert payload == {
        "outcome": "ticket",
        "department": "Public Works",
        "reference": " ".join(draft.reference),
        "reason": "after_hours",
        "next_open": "Monday, November 2 at 7:00 AM",
        "queued": False,
    }
    assert draft.labels == frozenset({"Public Works"})
    assert "**Callback:** +19515550142" in draft.description


class UnreachableSink:
    async def create(self, draft: TicketDraft) -> TicketRef:
        raise TicketSinkError(draft.reference)


async def test_a_failed_ticket_write_is_a_tool_error_not_an_exception() -> None:
    box = Toolbox(
        search=FakeSearch(SearchResult(SearchStatus.MATCH, (CHICKENS,))),
        directory=DIRECTORY,
        policy=corona_policy(),
        tickets=UnreachableSink(),
        transfers=RecordingTransfer(),
        clock=lambda: FRIDAY_EVENING,
    )
    result = await box.run(
        ToolName.ROUTE_REQUEST, {"department": "public_works", "summary": "Pothole"}, CALL
    )
    assert result.is_error
    assert json.loads(result.content) == {
        "outcome": "ticket_failed",
        "department": "Public Works",
        "instruction": (
            "Apologize: the request could not be filed. "
            "Offer to connect them or suggest calling back."
        ),
    }


async def test_caller_can_ask_for_a_ticket_while_the_office_is_open() -> None:
    box, tickets, transfers = toolbox(TUESDAY_10AM)
    result = await box.run(
        ToolName.ROUTE_REQUEST,
        {"department": "code_compliance", "summary": "Dumping", "prefer_ticket": True},
        CALL,
    )
    assert json.loads(result.content)["reason"] == TicketReason.CALLER_REQUESTED
    assert json.loads(result.content)["next_open"] is None
    assert len(tickets.created) == 1
    assert transfers.requests == []


async def test_emergency_returns_the_fixed_script() -> None:
    box, _, _ = toolbox(TUESDAY_10AM)
    result = await box.run(ToolName.EMERGENCY_GUIDANCE, {}, CALL)
    assert json.loads(result.content) == {"script": EMERGENCY_SCRIPT}


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("route_request", {"department": "parks", "summary": "x"}, "department"),
        ("route_request", {"department": "finance"}, "summary"),
        ("search_city", {"query": ""}, "query"),
        ("transfer_money", {}, "Unknown tool"),
    ],
)
async def test_bad_tool_calls_come_back_as_errors(
    name: str, arguments: dict[str, object], message: str
) -> None:
    box, _, _ = toolbox(TUESDAY_10AM)
    result = await box.run(name, arguments, CALL)
    assert result.is_error
    assert message in result.content


async def test_file_transfer_failed_files_without_next_open() -> None:
    box, tickets, _ = toolbox(TUESDAY_10AM)
    request = TransferRequest(Department.PUBLIC_WORKS, "+15555550100", "Pothole on Main")
    ref = await box.file_transfer_failed(request, CALL)
    (draft,) = tickets.created
    assert ref is not None
    assert ref.reference == draft.reference
    assert "the transfer was not answered" in draft.description
    assert "Next open" not in draft.description
    assert "**Callback:** +19515550142" in draft.description
    assert draft.labels == frozenset({"Public Works"})


async def test_file_transfer_failed_returns_none_when_the_sink_fails() -> None:
    box = Toolbox(
        search=FakeSearch(SearchResult(SearchStatus.MATCH, (CHICKENS,))),
        directory=DIRECTORY,
        policy=corona_policy(),
        tickets=UnreachableSink(),
        transfers=RecordingTransfer(),
        clock=lambda: TUESDAY_10AM,
    )
    request = TransferRequest(Department.PUBLIC_WORKS, "+15555550100", "Pothole on Main")
    assert await box.file_transfer_failed(request, CALL) is None
