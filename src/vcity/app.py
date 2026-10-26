import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from importlib.resources import files

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from vcity.agent.conversation import Conversation
from vcity.agent.services import Services, open_services
from vcity.agent.tools import CallContext
from vcity.gate.access import AccessCode, GuessLimit
from vcity.gate.line import CallLine
from vcity.gate.tickets import TicketBook
from vcity.recycle import MemoryRecycler
from vcity.settings import AppSettings, VoiceSettings, WebSettings
from vcity.voice.call import run_browser_call

CALL_PAGE = files("vcity.voice").joinpath("static", "call.html").read_text(encoding="utf-8")

type CallRunner = Callable[[WebSocket], Awaitable[None]]

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
) -> FastAPI:
    running: list[Services] = []
    access = AccessCode(web.access_code)
    guesses = GuessLimit()
    tickets = TicketBook()
    line = line or CallLine()
    recycler = recycler or MemoryRecycler(web.recycle_rss_mb)

    async def run_call(websocket: WebSocket) -> None:
        if call is not None:
            await call(websocket)
        else:
            await run_browser_call(websocket, running[0], voice, web.max_call_seconds)

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
        if await _admit(websocket, tickets, line):
            try:
                await run_call(websocket)
            finally:
                line.release()
                recycler.after_call(line)

    return app


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


async def _admit(websocket: WebSocket, tickets: TicketBook, line: CallLine) -> bool:
    if not tickets.redeem(websocket.query_params.get("ticket")):
        await websocket.close(code=POLICY_VIOLATION)
        return False
    if not line.take():
        await websocket.close(code=BUSY, reason="busy")
        return False
    return True


async def _warm_up(services: Services) -> None:
    call = CallContext("WARM-UP", None)
    toolbox = services.toolbox()
    await Conversation(services.anthropic, toolbox, services.system, call, services.clock).warm_up()
