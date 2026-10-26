from vcity.agent.routing import TransferRequest
from vcity.domain.departments import Department
from vcity.telephony.calls import Outcome, PhoneCalls


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _transfer() -> TransferRequest:
    return TransferRequest(
        department=Department.PUBLIC_WORKS,
        number="+15550100",
        summary="pothole on Main",
    )


def test_an_opened_call_keeps_its_caller() -> None:
    calls = PhoneCalls(clock=FakeClock())
    calls.open("CA1", "+15550199")
    call = calls.get("CA1")
    assert call is not None
    assert call.caller_id == "+15550199"
    assert call.outcome is None
    assert not call.accepted


def test_finish_records_a_transfer() -> None:
    calls = PhoneCalls(clock=FakeClock())
    request = _transfer()
    calls.open("CA1", None)
    calls.finish("CA1", Outcome.TRANSFER, request)
    call = calls.get("CA1")
    assert call is not None
    assert call.outcome is Outcome.TRANSFER
    assert call.transfer is request


def test_accept_marks_the_call() -> None:
    calls = PhoneCalls(clock=FakeClock())
    calls.open("CA1", None)
    calls.accept("CA1")
    call = calls.get("CA1")
    assert call is not None
    assert call.accepted


def test_close_removes_and_returns_the_call() -> None:
    calls = PhoneCalls(clock=FakeClock())
    calls.open("CA1", "+15550199")
    closed = calls.close("CA1")
    assert closed is not None
    assert closed.caller_id == "+15550199"
    assert calls.close("CA1") is None
    assert len(calls) == 0


def test_unknown_calls_are_ignored() -> None:
    calls = PhoneCalls(clock=FakeClock())
    calls.finish("CAnope", Outcome.BUSY)
    calls.accept("CAnope")
    assert calls.get("CAnope") is None


def test_calls_expire_after_their_lifetime() -> None:
    clock = FakeClock()
    calls = PhoneCalls(clock=clock)
    calls.open("CA1", None)
    clock.now += 1801
    assert calls.get("CA1") is None
    assert len(calls) == 0
