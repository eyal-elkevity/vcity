from vcity.gate.line import CallLine


def test_the_line_holds_one_call() -> None:
    line = CallLine()
    assert line.take()
    assert line.busy
    assert not line.take()
    line.release()
    assert not line.busy
    assert line.take()
