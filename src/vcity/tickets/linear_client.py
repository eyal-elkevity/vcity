from collections.abc import Mapping
from types import TracebackType
from typing import Self

import httpx
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from pydantic.alias_generators import to_camel

from vcity.tickets.sinks import TicketSinkError

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


class LinearError(TicketSinkError):
    pass


class LinearConflictError(LinearError):
    pass


class LinearModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, frozen=True)


class _GraphQLError(BaseModel):
    message: str
    extensions: dict[str, object] = {}


class _Envelope(BaseModel):
    data: object = None
    errors: list[_GraphQLError] = []


class LinearClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @classmethod
    def connect(cls, api_key: str, *, timeout: float = 5.0) -> Self:
        return cls(
            httpx.AsyncClient(
                base_url=LINEAR_GRAPHQL_URL,
                headers={"Authorization": api_key},
                timeout=timeout,
            )
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def execute[T](self, query: str, variables: Mapping[str, object], result: type[T]) -> T:
        try:
            response = await self._http.post("", json={"query": query, "variables": variables})
        except httpx.HTTPError as error:
            raise LinearError(f"Linear request failed: {error}") from error
        envelope = _envelope(response)
        if envelope.errors:
            messages = "; ".join(error.message for error in envelope.errors)
            # Linear reports a duplicate client-supplied id only through this message text.
            if any(error.message.startswith("conflict on insert") for error in envelope.errors):
                raise LinearConflictError(messages)
            raise LinearError(f"Linear rejected the request: {messages}")
        if response.is_error:
            raise LinearError(f"Linear returned HTTP {response.status_code}")
        try:
            return TypeAdapter(result).validate_python(envelope.data)
        except ValidationError as error:
            fields = "; ".join(
                f"{'.'.join(str(part) for part in problem['loc'])}: {problem['type']}"
                for problem in error.errors(include_input=False)
            )
            raise LinearError(f"unexpected Linear response: {fields}") from None


def _envelope(response: httpx.Response) -> _Envelope:
    try:
        return _Envelope.model_validate_json(response.content)
    except ValidationError:
        return _Envelope()
