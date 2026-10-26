import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from anthropic.types import ToolParam
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vcity.agent.routing import TransferPort, TransferRequest
from vcity.domain.departments import Department, DepartmentInfo
from vcity.domain.hours import HoursPolicy, Ticket, TicketReason, Transfer, decide
from vcity.domain.tickets import ServiceRequest, TicketRef
from vcity.knowledge.model import Chunk, SearchResult, SourceKind
from vcity.knowledge.search import SearchUnavailableError
from vcity.tickets.drafts import service_ticket
from vcity.tickets.sinks import TicketSink, TicketSinkError

_log = logging.getLogger(__name__)

EMERGENCY_SCRIPT = (
    "If anyone is in danger, hurt, or there is a fire or a crime happening right now, "
    "please hang up and dial 9 1 1 immediately."
)
_TICKET_FAILED_INSTRUCTION = (
    "Apologize: the request could not be filed. Offer to connect them or suggest calling back."
)


class ToolName(StrEnum):
    SEARCH_CITY = "search_city"
    ROUTE_REQUEST = "route_request"
    EMERGENCY_GUIDANCE = "emergency_guidance"


TOOLS: list[ToolParam] = [
    {
        "name": ToolName.SEARCH_CITY,
        "description": (
            "Search Corona's municipal code, city website, city news and events calendar. "
            "Use it for every question about city rules, services, fees, permits, news or events."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The caller's question, rephrased as a search.",
                },
                "kind": {
                    "type": "string",
                    "enum": [kind.value for kind in SourceKind],
                    "description": "Optional: restrict to code, site, news or events.",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": ToolName.ROUTE_REQUEST,
        "description": (
            "Connect the caller to a city department, or file a ticket when the office is "
            "closed or the caller asks for one. Office hours decide between the two."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "department": {"type": "string", "enum": [d.value for d in Department]},
                "summary": {
                    "type": "string",
                    "description": "One sentence: what the caller needs.",
                },
                "location": {"type": "string", "description": "Where, if the request has a place."},
                "prefer_ticket": {
                    "type": "boolean",
                    "description": "True when the caller wants a ticket instead of a transfer.",
                },
            },
            "required": ["department", "summary"],
        },
    },
    {
        "name": ToolName.EMERGENCY_GUIDANCE,
        "description": "The script to read when anyone may be in danger. Never route these.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


class _SearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1)
    kind: SourceKind | None = None


class _RouteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    department: Department
    summary: str = Field(min_length=1)
    location: str | None = None
    prefer_ticket: bool = False


class CitySearchPort(Protocol):
    async def search(
        self, query: str, kind: SourceKind | None = None, *, caller_id: str | None = None
    ) -> SearchResult: ...


@dataclass(frozen=True, slots=True)
class ToolResult:
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class CallContext:
    call_id: str
    caller_id: str | None


@dataclass(frozen=True, slots=True)
class Toolbox:
    search: CitySearchPort
    directory: Mapping[Department, DepartmentInfo]
    policy: HoursPolicy
    tickets: TicketSink
    transfers: TransferPort
    clock: Callable[[], datetime]

    async def run(self, name: str, raw_input: object, call: CallContext) -> ToolResult:
        try:
            tool = ToolName(name)
        except ValueError:
            return ToolResult(f"Unknown tool {name}.", is_error=True)
        try:
            match tool:
                case ToolName.SEARCH_CITY:
                    return await self._search_city(_SearchInput.model_validate(raw_input), call)
                case ToolName.ROUTE_REQUEST:
                    return await self._route(_RouteInput.model_validate(raw_input), call)
                case ToolName.EMERGENCY_GUIDANCE:
                    return ToolResult(_json({"script": EMERGENCY_SCRIPT}))
        except ValidationError as error:
            return ToolResult(_invalid(name, error), is_error=True)

    async def file_transfer_failed(
        self, request: TransferRequest, call: CallContext
    ) -> TicketRef | None:
        draft = service_ticket(
            ServiceRequest(call.call_id, request.department, request.summary, None, call.caller_id),
            TicketReason.TRANSFER_FAILED,
            self.directory[request.department].name,
            ticket_id=uuid.uuid4(),
            next_open=None,
        )
        try:
            return await self.tickets.create(draft)
        except TicketSinkError:
            _log.warning("ticket %s could not be filed", draft.reference)
            return None

    async def _search_city(self, request: _SearchInput, call: CallContext) -> ToolResult:
        try:
            result = await self.search.search(request.query, request.kind, caller_id=call.caller_id)
        except SearchUnavailableError:
            return ToolResult(_json({"status": "unavailable"}))
        return ToolResult(
            _json({"status": result.status.value, "results": [_chunk(c) for c in result.chunks]})
        )

    async def _route(self, request: _RouteInput, call: CallContext) -> ToolResult:
        info = self.directory[request.department]
        now = self.clock()
        decision = (
            Ticket(TicketReason.CALLER_REQUESTED, None)
            if request.prefer_ticket
            else decide(info, now, self.policy)
        )
        match decision:
            case Transfer(number=number):
                await self.transfers.transfer(
                    TransferRequest(request.department, number, request.summary)
                )
                return ToolResult(_json({"outcome": "transfer", "department": info.name}))
            case Ticket(reason=reason, next_open=next_open):
                draft = service_ticket(
                    ServiceRequest(
                        call.call_id,
                        request.department,
                        request.summary,
                        request.location,
                        call.caller_id,
                    ),
                    reason,
                    info.name,
                    ticket_id=uuid.uuid4(),
                    next_open=next_open,
                )
                try:
                    ref = await self.tickets.create(draft)
                except TicketSinkError:
                    _log.warning("ticket %s could not be filed", draft.reference)
                    return ToolResult(
                        _json(
                            {
                                "outcome": "ticket_failed",
                                "department": info.name,
                                "instruction": _TICKET_FAILED_INSTRUCTION,
                            }
                        ),
                        is_error=True,
                    )
                return ToolResult(
                    _json(
                        {
                            "outcome": "ticket",
                            "department": info.name,
                            "reference": " ".join(ref.reference),
                            "reason": reason.value,
                            "next_open": f"{next_open:%A, %B %-d at %-I:%M %p}"
                            if next_open
                            else None,
                            "queued": ref.queued,
                        }
                    )
                )


def _chunk(chunk: Chunk) -> dict[str, str | None]:
    return {
        "cite": chunk.cite_id,
        "source": chunk.breadcrumb,
        "text": chunk.text,
        "date": f"{chunk.date:%B %-d, %Y}" if chunk.date else None,
    }


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _invalid(name: str, error: ValidationError) -> str:
    fields = ", ".join(".".join(map(str, e["loc"])) for e in error.errors(include_input=False))
    return f"Invalid input for {name}: check {fields}."
