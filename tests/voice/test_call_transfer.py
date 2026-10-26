from vcity.agent.routing import Department, TransferRequest
from vcity.voice.call_transfer import CallTransfer

REQUEST = TransferRequest(Department.UTILITIES, "+15555550100", "Water bill question")


async def test_take_clears_but_last_remembers() -> None:
    transfers = CallTransfer()
    await transfers.transfer(REQUEST)

    assert transfers.take() == REQUEST
    assert transfers.take() is None
    assert transfers.last == REQUEST


def test_last_is_none_before_any_transfer() -> None:
    assert CallTransfer().last is None
