from uuid import UUID

from vcity.domain.tickets import Priority, TicketRef, reference_code


def test_reference_is_six_stable_digits() -> None:
    ticket_id = UUID("e428b1a8-aca1-4e3e-b636-9a715853e2ba")
    assert reference_code(ticket_id) == "687162"
    assert len(reference_code(ticket_id)) == 6


def test_reference_keeps_leading_zeros() -> None:
    assert reference_code(UUID(int=42)) == "000042"


def test_ticket_without_url_is_queued() -> None:
    assert TicketRef("000042", url=None).queued
    assert not TicketRef("000042", url="https://linear.app/x").queued


def test_priority_follows_linear_scale() -> None:
    assert [p.value for p in Priority] == [0, 1, 2, 3, 4]
    assert Priority.URGENT < Priority.LOW
