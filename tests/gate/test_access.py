from pydantic import SecretStr

from vcity.gate.access import AccessCode, GuessLimit


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_gate_is_off_without_a_code() -> None:
    gate = AccessCode(None)
    assert not gate.required
    assert gate.matches("anything")


def test_only_the_configured_code_matches() -> None:
    gate = AccessCode(SecretStr("lemon-42"))
    assert gate.required
    assert gate.matches("lemon-42")
    assert not gate.matches("lemon-43")
    assert not gate.matches("")


def test_ten_wrong_codes_lock_everyone_out_for_a_minute() -> None:
    clock = FakeClock()
    limit = GuessLimit(clock=clock)
    for _ in range(9):
        limit.failed()
    assert not limit.locked
    limit.failed()
    assert limit.locked
    clock.now += 59.9
    assert limit.locked
    clock.now += 0.2
    assert not limit.locked


def test_wrong_codes_older_than_ten_minutes_are_forgotten() -> None:
    clock = FakeClock()
    limit = GuessLimit(clock=clock)
    for _ in range(9):
        limit.failed()
    clock.now += 601
    limit.failed()
    assert not limit.locked


def test_the_count_restarts_after_a_lockout() -> None:
    clock = FakeClock()
    limit = GuessLimit(clock=clock)
    for _ in range(10):
        limit.failed()
    clock.now += 61
    limit.failed()
    assert not limit.locked
