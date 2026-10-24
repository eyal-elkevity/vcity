from dataclasses import dataclass
from typing import Protocol

from vcity.domain.departments import Department


@dataclass(frozen=True, slots=True)
class TransferRequest:
    department: Department
    number: str
    summary: str


class TransferPort(Protocol):
    async def transfer(self, request: TransferRequest) -> None: ...


class RecordingTransfer:
    def __init__(self) -> None:
        self.requests: list[TransferRequest] = []

    async def transfer(self, request: TransferRequest) -> None:
        self.requests.append(request)
