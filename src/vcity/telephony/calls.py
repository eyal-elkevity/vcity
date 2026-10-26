import time
from dataclasses import dataclass
from enum import StrEnum

from vcity.agent.routing import TransferRequest
from vcity.gate.access import Clock

CALL_ID_SUFFIX_LENGTH = 8


def phone_call_id(call_sid: str) -> str:
    return f"TEL-{call_sid[-CALL_ID_SUFFIX_LENGTH:]}"


class Outcome(StrEnum):
    TRANSFER = "transfer"
    TIME_LIMIT = "time_limit"
    BUSY = "busy"


@dataclass(slots=True)
class PhoneCall:
    caller_id: str | None
    opened: float
    outcome: Outcome | None = None
    transfer: TransferRequest | None = None
    accepted: bool = False


class PhoneCalls:
    def __init__(self, *, clock: Clock = time.monotonic, lifetime: float = 1800.0) -> None:
        self._clock = clock
        self._lifetime = lifetime
        self._calls: dict[str, PhoneCall] = {}

    def __len__(self) -> int:
        self._prune()
        return len(self._calls)

    def open(self, call_sid: str, caller_id: str | None) -> None:
        self._prune()
        self._calls[call_sid] = PhoneCall(caller_id=caller_id, opened=self._clock())

    def get(self, call_sid: str) -> PhoneCall | None:
        self._prune()
        return self._calls.get(call_sid)

    def finish(
        self, call_sid: str, outcome: Outcome, transfer: TransferRequest | None = None
    ) -> None:
        self._prune()
        call = self._calls.get(call_sid)
        if call is None or call.outcome is not None:
            return
        call.outcome = outcome
        call.transfer = transfer

    def accept(self, call_sid: str) -> None:
        self._prune()
        call = self._calls.get(call_sid)
        if call is not None:
            call.accepted = True

    def close(self, call_sid: str) -> PhoneCall | None:
        self._prune()
        return self._calls.pop(call_sid, None)

    def _prune(self) -> None:
        now = self._clock()
        self._calls = {
            sid: call for sid, call in self._calls.items() if call.opened + self._lifetime > now
        }
