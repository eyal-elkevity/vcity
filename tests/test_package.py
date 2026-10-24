from importlib.metadata import version


def test_package_installs_with_declared_version() -> None:
    assert version("vcity") == "0.1.0"
