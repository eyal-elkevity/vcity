import vcity


def test_package_imports_from_src_layout() -> None:
    assert vcity.__name__ == "vcity"
