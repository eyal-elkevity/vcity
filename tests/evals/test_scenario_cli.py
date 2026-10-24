import pytest

from evals.scenarios.__main__ import main


def test_unknown_only_ids_are_rejected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["scenarios", "--only", "no-such-case"])
    with pytest.raises(SystemExit) as exit_:
        main()
    assert exit_.value.code == 2
    assert "unknown scenario ids: no-such-case" in capsys.readouterr().err
