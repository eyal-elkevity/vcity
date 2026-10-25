import contextlib
from collections.abc import AsyncGenerator
from importlib.resources import files

from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse

from vcity.agent.services import Services, open_services
from vcity.settings import AppSettings, VoiceSettings
from vcity.voice.call import run_browser_call

CALL_PAGE = files("vcity.voice").joinpath("static", "call.html").read_text(encoding="utf-8")


def create_app(settings: AppSettings, voice: VoiceSettings) -> FastAPI:
    running: list[Services] = []

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        async with open_services(settings) as services:
            running.append(services)
            yield

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/call", response_class=HTMLResponse)
    async def call_page() -> str:
        return CALL_PAGE

    @app.websocket("/ws/browser")
    async def browser_call(websocket: WebSocket) -> None:
        await websocket.accept()
        await run_browser_call(websocket, running[0], voice)

    return app
