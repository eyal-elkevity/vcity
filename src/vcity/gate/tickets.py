import secrets
import time

from vcity.gate.access import Clock


class TicketBook:
    def __init__(self, *, clock: Clock = time.monotonic, lifetime: float = 60.0) -> None:
        self._clock = clock
        self._lifetime = lifetime
        self._expiries: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self._expiries)

    def issue(self) -> str:
        self._prune()
        ticket = secrets.token_urlsafe(32)
        self._expiries[ticket] = self._clock() + self._lifetime
        return ticket

    def redeem(self, ticket: str | None) -> bool:
        self._prune()
        if ticket is None:
            return False
        return self._expiries.pop(ticket, None) is not None

    def _prune(self) -> None:
        now = self._clock()
        self._expiries = {t: e for t, e in self._expiries.items() if e > now}
