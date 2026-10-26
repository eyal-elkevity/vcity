from vcity.agent.routing import Department, TransferRequest
from vcity.voice.call_transfer import CallTransfer

REQUEST = TransferRequest(Department.UTILITIES, "+15555550100", "Water bill question")


async def test_take_returns_the_latest_request_once() -> None:
    transfers = CallTransfer()
    await transfers.transfer(REQUEST)

    assert transfers.take() == REQUEST
    assert transfers.take() is None
