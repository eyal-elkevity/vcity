from vcity.gate.tickets import TicketBook


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_ticket_works_once() -> None:
    book = TicketBook(clock=FakeClock())
    ticket = book.issue()
    assert book.redeem(ticket)
    assert not book.redeem(ticket)


def test_a_ticket_expires_after_a_minute() -> None:
    clock = FakeClock()
    book = TicketBook(clock=clock)
    ticket = book.issue()
    clock.now += 60.1
    assert not book.redeem(ticket)


def test_unknown_or_missing_tickets_are_refused() -> None:
    book = TicketBook(clock=FakeClock())
    book.issue()
    assert not book.redeem("forged")
    assert not book.redeem(None)


def test_expired_tickets_are_pruned() -> None:
    clock = FakeClock()
    book = TicketBook(clock=clock)
    for _ in range(100):
        book.issue()
    clock.now += 61
    book.issue()
    assert len(book) == 1
