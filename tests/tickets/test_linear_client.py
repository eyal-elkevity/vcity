import json

import httpx
import pytest

from vcity.tickets.linear_client import (
    LinearClient,
    LinearConflictError,
    LinearError,
    LinearModel,
)


class Viewer(LinearModel):
    display_name: str


class ViewerResult(LinearModel):
    viewer: Viewer


def client_answering(status: int, body: object) -> LinearClient:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "lin_test"
        assert json.loads(request.content)["variables"] == {"x": 1}
        return httpx.Response(status, json=body)

    return LinearClient(
        httpx.AsyncClient(
            base_url="https://linear.test/graphql",
            headers={"Authorization": "lin_test"},
            transport=httpx.MockTransport(respond),
        )
    )


async def test_data_is_parsed_into_the_requested_model() -> None:
    client = client_answering(200, {"data": {"viewer": {"displayName": "Eyal"}}})
    result = await client.execute("{ viewer { displayName } }", {"x": 1}, ViewerResult)
    assert result.viewer.display_name == "Eyal"


async def test_graphql_errors_raise_with_their_messages() -> None:
    client = client_answering(400, {"errors": [{"message": 'Cannot query field "bogus"'}]})
    with pytest.raises(LinearError, match="bogus"):
        await client.execute("{ bogus }", {"x": 1}, ViewerResult)


async def test_duplicate_id_is_a_conflict() -> None:
    body = {"errors": [{"message": "conflict on insert of Issue"}], "data": None}
    client = client_answering(200, body)
    with pytest.raises(LinearConflictError):
        await client.execute("mutation", {"x": 1}, ViewerResult)


async def test_http_failure_without_body_raises() -> None:
    client = client_answering(503, "unavailable")
    with pytest.raises(LinearError, match="HTTP 503"):
        await client.execute("{ viewer { displayName } }", {"x": 1}, ViewerResult)


async def test_unexpected_shape_raises() -> None:
    client = client_answering(200, {"data": {"viewer": {}}})
    with pytest.raises(LinearError, match="unexpected"):
        await client.execute("{ viewer { displayName } }", {"x": 1}, ViewerResult)


async def test_transport_failure_raises() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = LinearClient(
        httpx.AsyncClient(base_url="https://linear.test", transport=httpx.MockTransport(refuse))
    )
    with pytest.raises(LinearError, match="request failed"):
        await client.execute("{ viewer { displayName } }", {}, ViewerResult)
