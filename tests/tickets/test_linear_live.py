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
from vcity.tickets.linear_client import LinearClient, LinearError, LinearModel
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
        await _delete_all(client, created)


async def _delete_all(client: LinearClient, issue_ids: list[str]) -> None:
    failures: list[str] = []
    for issue_id in issue_ids:
        try:
            deleted = await client.execute(_DELETE, {"id": issue_id}, _Deleted)
        except LinearError as error:
            failures.append(f"{issue_id}: {error}")
            continue
        if not deleted.issue_delete["success"]:
            failures.append(f"{issue_id}: Linear refused the delete")
    if failures:
        raise AssertionError("failed to delete live test issues: " + "; ".join(failures))


async def test_service_ticket_is_created_once(linear: tuple[LinearClient, list[str]]) -> None:
    client, created = linear
    sink = await LinearSink.connect(client)
    request = ServiceRequest("LIVE-TEST", Department.PUBLIC_WORKS, "Live test pothole", None, None)
    draft = service_ticket(
        request, TicketReason.AFTER_HOURS, "Public Works", ticket_id=uuid.uuid4(), next_open=None
    )
    first = await sink.create(draft)
    created.append(str(draft.id))
    second = await sink.create(draft)
    assert first == second
    assert first.url is not None
    assert "/issue/COR-" in first.url


async def test_paraphrased_gaps_share_one_ticket(linear: tuple[LinearClient, list[str]]) -> None:
    client, created = linear
    sink = await LinearSink.connect(client)
    existing = {gap.id for gap in await sink.open_gaps()}
    probe = f"vcity live probe {uuid.uuid4().hex[:8]}: when does the zorblax festival open?"
    voyage = AsyncClient(api_key=EmbeddingSettings.from_env().voyage_api_key.get_secret_value())
    backlog = GapBacklog(sink, VoyageEmbedder(voyage), salt="live", clock=lambda: datetime.now(UTC))
    try:
        first = await backlog.file(probe, "caller-a")
        if first.id in existing:
            pytest.fail("probe joined a pre-existing gap; refusing to delete it")
        again = await backlog.file("is the zorblax festival opening soon", "caller-b")
        assert again.id == first.id
        assert len(await sink.asks(first)) == 2
    finally:
        created.extend(
            gap.id
            for gap in await sink.open_gaps()
            if gap.id not in existing and gap.id not in created
        )
