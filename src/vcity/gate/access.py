import hmac
import time
from collections import deque
from collections.abc import Callable

from pydantic import SecretStr

type Clock = Callable[[], float]


class AccessCode:
    def __init__(self, code: SecretStr | None) -> None:
        self._code = code.get_secret_value().encode() if code is not None else None

    @property
    def required(self) -> bool:
        return self._code is not None

    def matches(self, attempt: str) -> bool:
        if self._code is None:
            return True
        return hmac.compare_digest(attempt.encode(), self._code)


class GuessLimit:
    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        failures: int = 10,
        window: float = 600.0,
        lockout: float = 60.0,
    ) -> None:
        self._clock = clock
        self._failures = failures
        self._window = window
        self._lockout = lockout
        self._recent: deque[float] = deque()
        self._locked_until = 0.0

    @property
    def locked(self) -> bool:
        return self._clock() < self._locked_until

    def failed(self) -> None:
        now = self._clock()
        while self._recent and now - self._recent[0] > self._window:
            self._recent.popleft()
        self._recent.append(now)
        if len(self._recent) >= self._failures:
            self._locked_until = now + self._lockout
            self._recent.clear()
