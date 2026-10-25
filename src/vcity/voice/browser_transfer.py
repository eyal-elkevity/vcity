from dataclasses import dataclass, field

from vcity.agent.routing import TransferRequest


@dataclass
class ScreenTransfer:
    requests: list[TransferRequest] = field(default_factory=list[TransferRequest])

    async def transfer(self, request: TransferRequest) -> None:
        self.requests.append(request)

    def take(self) -> TransferRequest | None:
        latest = self.requests[-1] if self.requests else None
        self.requests.clear()
        return latest
