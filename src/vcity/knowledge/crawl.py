import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

SITE_ROOT = "https://www.coronaca.gov"
SITEMAP_URL = f"{SITE_ROOT}/sitemap.xml"
USER_AGENT = "vcity-ingest/0.1 (+https://github.com/eyal-elkevity/vcity)"
SKIPPED_PATHS = frozenset({"/news", "/events", "/search", "/es"})
SKIPPED_PREFIXES = ("/es/",)
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    url: str
    html: str


def site_urls(sitemap_xml: str) -> list[str]:
    locations: list[str] = _LOC.findall(sitemap_xml)
    return sorted({url for url in locations if _wanted(urlparse(url).path)})


def _wanted(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return normalized not in SKIPPED_PATHS and not normalized.startswith(SKIPPED_PREFIXES)


async def fetch_pages(
    client: httpx.AsyncClient, urls: Sequence[str], concurrency: int = 8
) -> list[FetchedPage]:
    gate = asyncio.Semaphore(concurrency)

    async def fetch(url: str) -> FetchedPage | None:
        async with gate:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as error:
                _log.warning("skipping %s: %s", url, error)
                return None
            return FetchedPage(url=url, html=response.text)

    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(fetch(url)) for url in urls]
    return [page for task in tasks if (page := task.result()) is not None]
