import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from voyageai.client_async import AsyncClient

from vcity.domain.departments import Department
from vcity.domain.hours import TicketReason
from vcity.domain.tickets import ServiceRequest
from vcity.knowledge.embeddings import VoyageEmbedder
from vcity.settings import EmbeddingSettings, TicketSettings
from vcity.tickets.drafts import service_ticket
from vcity.tickets.gaps import GapBacklog
from vcity.tickets.linear_client import LinearClient, LinearModel
from vcity.tickets.linear_sink import LinearSink

pytestmark = pytest.mark.live

_DELETE = "mutation Delete($id: String!) { issueDelete(id: $id) { success } }"


class _Deleted(LinearModel):
    issue_delete: dict[str, bool]


@pytest.fixture
async def linear() -> AsyncIterator[tuple[LinearClient, list[str]]]:
    created: list[str] = []
    key = TicketSettings.from_env().linear_api_key.get_secret_value()
    async with LinearClient.connect(key) as client:
        yield client, created
        for issue_id in created:
            await client.execute(_DELETE, {"id": issue_id}, _Deleted)


async def test_service_ticket_is_created_once(linear: tuple[LinearClient, list[str]]) -> None:
    client, created = linear
    sink = await LinearSink.connect(client)
    request = ServiceRequest("LIVE-TEST", Department.PUBLIC_WORKS, "Live test pothole", None, None)
    draft = service_ticket(
        request, TicketReason.AFTER_HOURS, "Public Works", ticket_id=uuid.uuid4(), next_open=None
    )
    created.append(str(draft.id))
    first, second = await sink.create(draft), await sink.create(draft)
    assert first == second
    assert first.url is not None
    assert "/issue/COR-" in first.url


async def test_paraphrased_gaps_share_one_ticket(linear: tuple[LinearClient, list[str]]) -> None:
    client, created = linear
    sink = await LinearSink.connect(client)
    voyage = AsyncClient(api_key=EmbeddingSettings.from_env().voyage_api_key.get_secret_value())
    backlog = GapBacklog(sink, VoyageEmbedder(voyage), salt="live", clock=lambda: datetime.now(UTC))
    first = await backlog.file("When will the new Target store open?", "caller-a")
    created.append(first.id)
    again = await backlog.file("is the target on main street opening soon", "caller-b")
    assert again.id == first.id
    assert len(await sink.asks(first)) == 2
