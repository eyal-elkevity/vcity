from dataclasses import dataclass, field

from vcity.agent.routing import TransferRequest


@dataclass
class CallTransfer:
    requests: list[TransferRequest] = field(default_factory=list[TransferRequest])
    _last: TransferRequest | None = None

    async def transfer(self, request: TransferRequest) -> None:
        self.requests.append(request)
        self._last = request

    def take(self) -> TransferRequest | None:
        latest = self.requests[-1] if self.requests else None
        self.requests.clear()
        return latest

    @property
    def last(self) -> TransferRequest | None:
        return self._last
