import argparse
import asyncio
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from voyageai.client_async import AsyncClient

from vcity.knowledge.chunking import chunk_code_section, chunk_page
from vcity.knowledge.code_snapshot import load_snapshot, parse_sections
from vcity.knowledge.crawl import SITEMAP_URL, USER_AGENT, fetch_pages, site_urls
from vcity.knowledge.embeddings import CachedEmbedder, Embedder, VoyageEmbedder
from vcity.knowledge.events import EVENTS_FEED_URL, parse_events
from vcity.knowledge.index import write_index
from vcity.knowledge.model import Chunk
from vcity.knowledge.site_pages import parse_page
from vcity.settings import EmbeddingSettings

CITY_TZ = ZoneInfo("America/Los_Angeles")
DEFAULT_CODE_SNAPSHOT = Path("data/raw/code/corona-municipal-code.json.gz")
DEFAULT_INDEX = Path("build/index.sqlite")
DEFAULT_EMBEDDING_CACHE = Path("build/embeddings.sqlite")
_log = logging.getLogger(__name__)


def code_chunks(snapshot: Path) -> list[Chunk]:
    return [
        chunk
        for section in parse_sections(load_snapshot(snapshot))
        for chunk in chunk_code_section(section)
    ]


async def web_chunks(client: httpx.AsyncClient) -> list[Chunk]:
    sitemap = await client.get(SITEMAP_URL)
    sitemap.raise_for_status()
    pages = await fetch_pages(client, site_urls(sitemap.text))
    feed = await client.get(EVENTS_FEED_URL)
    feed.raise_for_status()
    site = [
        chunk
        for fetched in pages
        if (page := parse_page(fetched.url, fetched.html)) is not None
        for chunk in chunk_page(page, CITY_TZ)
    ]
    return site + parse_events(feed.content, CITY_TZ)


async def build(chunks: Sequence[Chunk], embedder: Embedder, out: Path) -> None:
    vectors = await embedder.embed_documents([chunk.embedding_text for chunk in chunks])
    write_index(out, chunks, vectors, model=embedder.model)


async def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the Corona knowledge index.")
    parser.add_argument("--code", type=Path, default=DEFAULT_CODE_SNAPSHOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--cache", type=Path, default=DEFAULT_EMBEDDING_CACHE)
    args = parser.parse_args(argv)
    code: Path = args.code
    out: Path = args.out
    cache: Path = args.cache

    settings = EmbeddingSettings.from_env()
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
    ) as client:
        chunks = code_chunks(code) + await web_chunks(client)
    _log.info("chunks by kind: %s", dict(Counter(chunk.kind.value for chunk in chunks)))
    voyage = AsyncClient(api_key=settings.voyage_api_key.get_secret_value(), max_retries=3)
    embedder = CachedEmbedder(VoyageEmbedder(voyage), cache)
    try:
        await build(chunks, embedder, out)
    finally:
        embedder.close()
    _log.info("wrote %d chunks to %s", len(chunks), out)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main())
