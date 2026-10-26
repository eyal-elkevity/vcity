import asyncio
import contextlib
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic
from voyageai.client_async import AsyncClient

from vcity.agent.prompt import system_prompt
from vcity.agent.routing import RecordingTransfer, TransferPort
from vcity.agent.tools import Toolbox
from vcity.config.departments import DEFAULT_DIRECTORY_PATH, load_directory
from vcity.config.policy import corona_policy
from vcity.domain.departments import Department, DepartmentInfo
from vcity.domain.hours import HoursPolicy
from vcity.knowledge.embeddings import VoyageEmbedder
from vcity.knowledge.gap_log import CompositeGapLog, GapLog, JsonlGapLog
from vcity.knowledge.index import KnowledgeIndex
from vcity.knowledge.relevance import ClaudeRelevanceClassifier, RelevanceClassifier
from vcity.knowledge.retrieval import HybridRetriever, Retriever
from vcity.knowledge.search import CitySearch
from vcity.settings import AppSettings
from vcity.tickets.gaps import GapBacklog
from vcity.tickets.linear_client import LinearClient
from vcity.tickets.linear_sink import LazyLinearSink
from vcity.tickets.outbox import OutboxSink, OutboxStore
from vcity.tickets.sinks import MockSink, TicketSink

CITY_TZ = ZoneInfo("America/Los_Angeles")
AGENT_TIMEOUT = 15.0
CLASSIFIER_TIMEOUT = 3.0
VOYAGE_TIMEOUT = 3.0
LINEAR_TIMEOUT = 3.0
# Cloud Run allows 10 s between SIGTERM and SIGKILL.
SHUTDOWN_DRAIN_SECONDS = 8.0

type Clock = Callable[[], datetime]


def city_clock() -> datetime:
    return datetime.now(CITY_TZ)


@dataclass(frozen=True, slots=True)
class Services:
    anthropic: AsyncAnthropic
    system: str
    directory: Mapping[Department, DepartmentInfo]
    policy: HoursPolicy
    classifier: RelevanceClassifier
    retriever: Retriever
    gap_log: GapLog
    tickets: TicketSink
    transfers: TransferPort
    clock: Clock

    def toolbox(
        self,
        *,
        clock: Clock | None = None,
        tickets: TicketSink | None = None,
        transfers: TransferPort | None = None,
    ) -> Toolbox:
        now = clock or self.clock
        search = CitySearch(
            classifier=self.classifier, retriever=self.retriever, gap_log=self.gap_log, clock=now
        )
        return Toolbox(
            search=search,
            directory=self.directory,
            policy=self.policy,
            tickets=tickets or self.tickets,
            transfers=transfers or self.transfers,
            clock=now,
        )


@contextlib.asynccontextmanager
async def open_services(
    settings: AppSettings, *, clock: Clock = city_clock, file_to_linear: bool = True
) -> AsyncGenerator[Services]:
    directory = load_directory(DEFAULT_DIRECTORY_PATH, transfer_to=settings.transfer_to)
    anthropic_key = settings.anthropic_api_key.get_secret_value()
    voyage = AsyncClient(
        api_key=settings.voyage_api_key.get_secret_value(), timeout=VOYAGE_TIMEOUT, max_retries=1
    )
    embedder = VoyageEmbedder(voyage)
    salt = settings.gap_log_salt.get_secret_value()
    gap_logs: list[GapLog] = [JsonlGapLog(settings.gap_log_path, salt)]
    tickets: TicketSink = MockSink()
    async with contextlib.AsyncExitStack() as stack:
        index = KnowledgeIndex.open(settings.index_path)
        stack.callback(index.close)
        if file_to_linear and settings.linear_api_key is not None:
            linear = await stack.enter_async_context(
                LinearClient.connect(
                    settings.linear_api_key.get_secret_value(), timeout=LINEAR_TIMEOUT
                )
            )
            board = LazyLinearSink(linear)
            backlog = GapBacklog(board, embedder, salt=salt, clock=clock)
            outbox = OutboxSink(board, OutboxStore(settings.outbox_path))
            stack.push_async_callback(outbox.drain, SHUTDOWN_DRAIN_SECONDS)
            workers = [asyncio.create_task(backlog.run()), asyncio.create_task(outbox.run())]
            stack.push_async_callback(_stop, workers)
            gap_logs.append(backlog)
            tickets = outbox
        agent = AsyncAnthropic(api_key=anthropic_key, timeout=AGENT_TIMEOUT, max_retries=1)
        stack.push_async_callback(agent.close)
        classifier = AsyncAnthropic(
            api_key=anthropic_key, timeout=CLASSIFIER_TIMEOUT, max_retries=0
        )
        stack.push_async_callback(classifier.close)
        yield Services(
            anthropic=agent,
            system=system_prompt(directory),
            directory=directory,
            policy=corona_policy(),
            classifier=ClaudeRelevanceClassifier(classifier),
            retriever=HybridRetriever(index, embedder),
            gap_log=CompositeGapLog(*gap_logs),
            tickets=tickets,
            transfers=RecordingTransfer(),
            clock=clock,
        )


async def _stop(workers: list[asyncio.Task[None]]) -> None:
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
