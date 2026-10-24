from datetime import date
from pathlib import Path

from vcity.knowledge.site_pages import parse_page

FIXTURES = Path(__file__).parents[1] / "fixtures" / "site"
ANIMAL_SERVICES_URL = "https://www.coronaca.gov/departments/animal-services"
STATE_OF_THE_CITY_URL = "https://www.coronaca.gov/news/2026-state-of-the-city"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_department_page_keeps_title_and_splits_at_headings() -> None:
    page = parse_page(ANIMAL_SERVICES_URL, load("animal-services.html"))
    assert page is not None
    assert page.title == "Animal Services & Enforcement"
    assert [section.heading for section in page.sections] == [
        None,
        "Monthly Vaccine Clinic at the Corona Animal Shelter",
    ]


def test_hours_and_contacts_survive_extraction() -> None:
    page = parse_page(ANIMAL_SERVICES_URL, load("animal-services.html"))
    assert page is not None
    intro = page.sections[0].text
    assert "Hours of Operation Monday through Saturday" in intro
    assert "11:00 a.m. until 5:30 p.m." in intro
    assert "(951) 279-3618" in intro


def test_navigation_markdown_and_link_labels_are_dropped() -> None:
    page = parse_page(ANIMAL_SERVICES_URL, load("animal-services.html"))
    assert page is not None
    text = "\n".join(section.text for section in page.sections)
    assert "Open Menu" not in text
    assert "EMAIL »" not in text
    assert "**" not in text
    assert "--------" not in text


def test_department_page_has_no_publish_date() -> None:
    page = parse_page(ANIMAL_SERVICES_URL, load("animal-services.html"))
    assert page is not None
    assert page.published is None


def test_news_article_carries_its_publish_date() -> None:
    page = parse_page(STATE_OF_THE_CITY_URL, load("2026-state-of-the-city.html"))
    assert page is not None
    assert page.title == "2026 State of the City"
    assert page.published == date(2026, 9, 10)


def test_page_without_content_yields_none() -> None:
    assert parse_page(ANIMAL_SERVICES_URL, "<html><body></body></html>") is None
