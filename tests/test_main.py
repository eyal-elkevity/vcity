import pytest
from fastapi import FastAPI

from vcity import __main__ as entry


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    seen: dict[str, object] = {}

    def run(_: FastAPI, *, host: str, port: int) -> None:
        seen.update(host=host, port=port)

    def create_app(*_: object) -> FastAPI:
        return FastAPI()

    monkeypatch.setattr(entry, "create_app", create_app)
    monkeypatch.setattr(entry.AppSettings, "from_env", lambda: None)
    monkeypatch.setattr(entry.VoiceSettings, "from_env", lambda: None)
    monkeypatch.setattr(entry.WebSettings, "from_env", lambda: None)
    monkeypatch.setattr(entry.uvicorn, "run", run)
    return seen


def test_the_server_listens_only_on_this_machine_by_default(
    served: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    entry.main()
    assert served == {"host": "127.0.0.1", "port": 8080}


def test_host_and_port_come_from_the_environment(
    served: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOST", "0.0.0.0")  # noqa: S104
    monkeypatch.setenv("PORT", "9000")
    entry.main()
    assert served == {"host": "0.0.0.0", "port": 9000}  # noqa: S104
