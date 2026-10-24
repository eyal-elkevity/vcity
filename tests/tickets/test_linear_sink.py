import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from vcity.domain.tickets import Priority, TicketRef
from vcity.tickets.drafts import TicketDraft
from vcity.tickets.gaps import GapTicket
from vcity.tickets.linear_client import LinearClient, LinearError
from vcity.tickets.linear_sink import LinearSink

type Responder = Callable[[Mapping[str, object]], object]

ISSUE = {
    "id": "issue-1",
    "identifier": "COR-7",
    "url": "https://linear.app/vcity-corona/issue/COR-7",
    "title": "Unanswered: When will Target open?",
    "createdAt": "2026-10-24T19:00:00.000Z",
}
DRAFT = TicketDraft(
    UUID(int=99),
    "[000099] Public Works: pothole",
    "body",
    frozenset({"Public Works"}),
    Priority.NONE,
)
ISSUE_CREATED = datetime(2026, 10, 24, 19, 0, tzinfo=UTC)
OPERATION = re.compile(r"(?:query|mutation)\s+(\w+)")


class FakeLinear:
    def __init__(self, responders: Mapping[str, Responder]) -> None:
        self.responders = responders
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def client(self) -> LinearClient:
        return LinearClient(
            httpx.AsyncClient(base_url="https://linear.test", transport=httpx.MockTransport(self))
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        match = OPERATION.search(body["query"])
        assert match is not None
        name, variables = match[1], body["variables"]
        self.calls.append((name, variables))
        return httpx.Response(200, json=self.responders[name](variables))


def data(payload: object) -> Responder:
    return lambda _variables: {"data": payload}


TEAM = {
    "FindTeam": data({"teams": {"nodes": [{"id": "team-1", "name": "Corona 311"}]}}),
    "Labels": data(
        {
            "issueLabels": {
                "nodes": [{"id": "l-pw", "name": "Public Works"}, {"id": "l-gap", "name": "gap"}]
            }
        }
    ),
}


async def test_connect_fails_with_setup_hint_when_team_is_missing() -> None:
    fake = FakeLinear({"FindTeam": data({"teams": {"nodes": []}})})
    with pytest.raises(LinearError, match="linear_setup"):
        await LinearSink.connect(fake.client())


async def test_create_sends_client_id_team_and_label_ids() -> None:
    fake = FakeLinear({**TEAM, "CreateIssue": data({"issueCreate": {"issue": ISSUE}})})
    sink = await LinearSink.connect(fake.client())
    ref = await sink.create(DRAFT)
    assert ref == TicketRef("000099", url=ISSUE["url"])
    name, variables = fake.calls[-1]
    assert name == "CreateIssue"
    assert variables["input"] == {
        "id": str(DRAFT.id),
        "teamId": "team-1",
        "title": DRAFT.title,
        "description": "body",
        "priority": 0,
        "labelIds": ["l-pw"],
    }


async def test_create_after_a_conflict_returns_the_existing_issue() -> None:
    def conflict(_variables: Mapping[str, object]) -> object:
        return {"errors": [{"message": "conflict on insert of Issue"}]}

    fake = FakeLinear({**TEAM, "CreateIssue": conflict, "Issue": data({"issue": ISSUE})})
    sink = await LinearSink.connect(fake.client())
    assert (await sink.create(DRAFT)).url == ISSUE["url"]
    assert fake.calls[-1] == ("Issue", {"id": str(DRAFT.id)})


async def test_unknown_label_is_rejected_before_calling_linear() -> None:
    fake = FakeLinear(TEAM)
    sink = await LinearSink.connect(fake.client())
    draft = TicketDraft(UUID(int=1), "t", "d", frozenset({"Parks"}), Priority.NONE)
    with pytest.raises(LinearError, match="Parks"):
        await sink.create(draft)


async def test_open_gaps_strip_the_title_prefix() -> None:
    fake = FakeLinear({**TEAM, "OpenGaps": data({"issues": {"nodes": [ISSUE]}})})
    sink = await LinearSink.connect(fake.client())
    (gap,) = await sink.open_gaps()
    assert (gap.id, gap.reference, gap.question) == ("issue-1", "COR-7", "When will Target open?")
    assert fake.calls[-1][1] == {"team": "team-1", "label": "gap"}


async def test_asks_skip_comments_that_are_not_ask_lines() -> None:
    comments = {
        "issue": {
            "comments": {
                "nodes": [
                    {"body": "+1 from caller abc: target?", "createdAt": "2026-10-24T19:00:00Z"},
                    {"body": "Staff note: checking", "createdAt": "2026-10-24T20:00:00Z"},
                ]
            }
        }
    }
    fake = FakeLinear({**TEAM, "Comments": data(comments)})
    sink = await LinearSink.connect(fake.client())
    gap = GapTicket("issue-1", "COR-7", "target?", ISSUE_CREATED)
    asks = await sink.asks(gap)
    assert [(ask.caller, ask.question) for ask in asks] == [("abc", "target?")]


async def test_update_gap_sends_description_and_priority() -> None:
    fake = FakeLinear({**TEAM, "Update": data({"issueUpdate": {"success": True}})})
    sink = await LinearSink.connect(fake.client())
    await sink.update_gap(
        GapTicket("issue-1", "COR-7", "q", ISSUE_CREATED), "stats", Priority.MEDIUM
    )
    assert fake.calls[-1] == (
        "Update",
        {"id": "issue-1", "input": {"description": "stats", "priority": 3}},
    )


async def test_add_ask_comments_on_the_issue() -> None:
    fake = FakeLinear({**TEAM, "Comment": data({"commentCreate": {"success": True}})})
    sink = await LinearSink.connect(fake.client())
    await sink.add_ask(GapTicket("issue-1", "COR-7", "q", ISSUE_CREATED), "+1 from caller x: q")
    assert fake.calls[-1] == (
        "Comment",
        {"input": {"issueId": "issue-1", "body": "+1 from caller x: q"}},
    )


async def test_refused_comment_raises() -> None:
    fake = FakeLinear({**TEAM, "Comment": data({"commentCreate": {"success": False}})})
    sink = await LinearSink.connect(fake.client())
    gap = GapTicket("issue-1", "COR-7", "q", ISSUE_CREATED)
    with pytest.raises(LinearError, match="Linear refused to comment on COR-7"):
        await sink.add_ask(gap, "+1 from caller x: q")


async def test_refused_update_raises() -> None:
    fake = FakeLinear({**TEAM, "Update": data({"issueUpdate": {"success": False}})})
    sink = await LinearSink.connect(fake.client())
    gap = GapTicket("issue-1", "COR-7", "q", ISSUE_CREATED)
    with pytest.raises(LinearError, match="Linear refused to update COR-7"):
        await sink.update_gap(gap, "stats", Priority.MEDIUM)
