import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from importlib.resources import files

from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr

from vcity.agent.conversation import Conversation
from vcity.agent.pretend_clock import HoursMode
from vcity.agent.routing import TransferRequest
from vcity.agent.services import Services, open_services
from vcity.agent.tools import CallContext
from vcity.domain.tickets import TicketRef
from vcity.gate.access import AccessCode, GuessLimit
from vcity.gate.line import CallLine
from vcity.gate.tickets import TicketBook
from vcity.recycle import MemoryRecycler
from vcity.settings import AppSettings, TelephonySettings, VoiceSettings, WebSettings
from vcity.telephony.calls import PhoneCalls
from vcity.telephony.routes import PhoneRunner, TransferTicketer, telephony_router
from vcity.telephony.stream import StreamStart
from vcity.voice.call import CallOutcome, browser_surface, phone_surface, run_call

CALL_PAGE = files("vcity.voice").joinpath("static", "call.html").read_text(encoding="utf-8")

type CallRunner = Callable[[WebSocket, HoursMode], Awaitable[None]]

POLICY_VIOLATION = 1008
BUSY = 4002


class SessionRequest(BaseModel):
    code: str = Field(max_length=200)


def create_app(  # noqa: PLR0913
    settings: AppSettings,
    voice: VoiceSettings,
    web: WebSettings,
    *,
    call: CallRunner | None = None,
    line: CallLine | None = None,
    recycler: MemoryRecycler | None = None,
    telephony: TelephonySettings | None = None,
    phone_call: PhoneRunner | None = None,
    transfer_ticketer: TransferTicketer | None = None,
) -> FastAPI:
    running: list[Services] = []
    access = AccessCode(web.access_code)
    guesses = GuessLimit()
    tickets = TicketBook()
    line = line or CallLine()
    recycler = recycler or MemoryRecycler(web.recycle_rss_mb)

    async def serve_browser_call(websocket: WebSocket, hours: HoursMode) -> None:
        if call is not None:
            await call(websocket, hours)
        else:
            await run_call(
                websocket, running[0], voice, web.max_call_seconds, hours, browser_surface()
            )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with open_services(settings) as services:
            await _warm_up(services)
            running.append(services)
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/call", response_class=HTMLResponse)
    async def call_page() -> str:
        return CALL_PAGE

    @app.get("/session")
    async def session_requirements() -> dict[str, bool]:
        return {"code_required": access.required}

    @app.post("/session")
    async def open_session(request: SessionRequest) -> JSONResponse:
        return _open_session(request, access, guesses, line, tickets)

    @app.websocket("/ws/browser")
    async def browser_call(websocket: WebSocket) -> None:
        await websocket.accept()
        if hours := await _admit(websocket, tickets, line):
            try:
                await serve_browser_call(websocket, hours)
            finally:
                line.release()
                recycler.after_call(line)

    wiring = _PhoneWiring(running, voice, web, phone_call, transfer_ticketer)
    _mount_telephony(app, telephony, wiring, settings.transfer_to, line, tickets, recycler)

    return app


@dataclass(frozen=True, slots=True)
class _PhoneWiring:
    running: list[Services]
    voice: VoiceSettings
    web: WebSettings
    runner: PhoneRunner | None
    ticketer: TransferTicketer | None

    def router(
        self,
        token: SecretStr,
        transfer_to: str,
        line: CallLine,
        tickets: TicketBook,
        recycler: MemoryRecycler,
    ) -> APIRouter:
        return telephony_router(
            token=token,
            transfer_to=transfer_to,
            line=line,
            tickets=tickets,
            calls=PhoneCalls(),
            run=self.serve_call,
            file_ticket=self.file_transfer_ticket,
            after_call=lambda: _recycle_if_idle(recycler, line),
        )

    async def serve_call(
        self, websocket: WebSocket, start: StreamStart, caller_id: str | None
    ) -> CallOutcome:
        if self.runner is not None:
            return await self.runner(websocket, start, caller_id)
        return await run_call(
            websocket,
            self.running[0],
            self.voice,
            self.web.max_call_seconds,
            HoursMode.REAL,
            phone_surface(start, caller_id),
        )

    async def file_transfer_ticket(
        self, request: TransferRequest, call: CallContext
    ) -> TicketRef | None:
        if self.ticketer is not None:
            return await self.ticketer(request, call)
        return await self.running[0].toolbox().file_transfer_failed(request, call)


def _mount_telephony(  # noqa: PLR0913, PLR0917
    app: FastAPI,
    telephony: TelephonySettings | None,
    wiring: _PhoneWiring,
    transfer_to: str,
    line: CallLine,
    tickets: TicketBook,
    recycler: MemoryRecycler,
) -> None:
    if telephony is not None and telephony.twilio_auth_token is not None:
        app.include_router(
            wiring.router(telephony.twilio_auth_token, transfer_to, line, tickets, recycler)
        )


def _recycle_if_idle(recycler: MemoryRecycler, line: CallLine) -> None:
    recycler.after_call(line)


def _open_session(
    request: SessionRequest,
    access: AccessCode,
    guesses: GuessLimit,
    line: CallLine,
    tickets: TicketBook,
) -> JSONResponse:
    if guesses.locked:
        return JSONResponse({"error": "locked"}, status_code=429)
    if not access.matches(request.code):
        guesses.failed()
        return JSONResponse({"error": "wrong code"}, status_code=401)
    if line.busy:
        return JSONResponse({"error": "busy"}, status_code=409)
    return JSONResponse({"ticket": tickets.issue()})


def _hours(websocket: WebSocket) -> HoursMode | None:
    try:
        return HoursMode(websocket.query_params.get("hours", HoursMode.REAL))
    except ValueError:
        return None


async def _admit(websocket: WebSocket, tickets: TicketBook, line: CallLine) -> HoursMode | None:
    hours = _hours(websocket)
    if hours is None or not tickets.redeem(websocket.query_params.get("ticket")):
        await websocket.close(code=POLICY_VIOLATION)
        return None
    if not line.take():
        await websocket.close(code=BUSY, reason="busy")
        return None
    return hours


async def _warm_up(services: Services) -> None:
    call = CallContext("WARM-UP", None)
    toolbox = services.toolbox()
    await Conversation(services.anthropic, toolbox, services.system, call, services.clock).warm_up()
