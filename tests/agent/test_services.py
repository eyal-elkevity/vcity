import asyncio
from pathlib import Path

from pydantic import SecretStr

from vcity.agent.services import open_services
from vcity.agent.tools import CallContext, ToolName
from vcity.knowledge.index import write_index
from vcity.knowledge.model import Chunk, SourceKind
from vcity.settings import AppSettings
from vcity.tickets.outbox import OutboxSink
from vcity.tickets.sinks import MockSink


def settings(tmp_path: Path, *, linear: bool) -> AppSettings:
    index = tmp_path / "index.sqlite"
    chunk = Chunk("code:1", SourceKind.CODE, "Code", "text", "https://example.test")
    write_index(index, [chunk], [[1.0, 0.0]], model="voyage-4-lite@1024")
    return AppSettings(
        voyage_api_key=SecretStr("v"),
        anthropic_api_key=SecretStr("a"),
        gap_log_salt=SecretStr("s"),
        linear_api_key=SecretStr("l") if linear else None,
        index_path=index,
        gap_log_path=tmp_path / "gaps.jsonl",
        outbox_path=tmp_path / "outbox.sqlite",
    )


async def test_without_linear_tickets_go_to_the_mock(tmp_path: Path) -> None:
    async with open_services(settings(tmp_path, linear=False)) as services:
        assert isinstance(services.tickets, MockSink)
        result = await services.toolbox().run(
            ToolName.EMERGENCY_GUIDANCE, {}, CallContext("c", None)
        )
        assert "9 1 1" in result.content


async def test_with_linear_tickets_go_through_the_outbox_and_workers_stop(tmp_path: Path) -> None:
    async with asyncio.timeout(5):
        async with open_services(settings(tmp_path, linear=True)) as services:
            assert isinstance(services.tickets, OutboxSink)
        assert asyncio.all_tasks() == {asyncio.current_task()}
