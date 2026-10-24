import httpx
import pytest

from vcity.knowledge.crawl import fetch_pages, site_urls

SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.coronaca.gov/</loc></url>
  <url><loc>https://www.coronaca.gov/departments/animal-services</loc></url>
  <url><loc>https://www.coronaca.gov/es/departments/animal-services</loc></url>
  <url><loc>https://www.coronaca.gov/es</loc></url>
  <url><loc>https://www.coronaca.gov/news</loc></url>
  <url><loc>https://www.coronaca.gov/news/2026-state-of-the-city</loc></url>
  <url><loc>https://www.coronaca.gov/events</loc></url>
  <url><loc>https://www.coronaca.gov/search</loc></url>
  <url><loc>https://www.coronaca.gov/departments/animal-services</loc></url>
</urlset>"""


def test_sitemap_keeps_english_content_pages_once() -> None:
    assert site_urls(SITEMAP) == [
        "https://www.coronaca.gov/",
        "https://www.coronaca.gov/departments/animal-services",
        "https://www.coronaca.gov/news/2026-state-of-the-city",
    ]


async def test_fetch_skips_failed_pages_and_keeps_order(caplog: pytest.LogCaptureFixture) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(200, text=f"<html>{request.url.path}</html>")

    urls = ["https://city.test/a", "https://city.test/missing", "https://city.test/b"]
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        pages = await fetch_pages(client, urls, concurrency=2)
    assert [(page.url, page.html) for page in pages] == [
        ("https://city.test/a", "<html>/a</html>"),
        ("https://city.test/b", "<html>/b</html>"),
    ]
    assert "skipping https://city.test/missing" in caplog.text
