"""Architecture checks must be read-only and must not bless live violations."""

from tests.architecture.boundaries import PACKAGE_ROOT, collect_imports


def test_scanner_does_not_modify_source_files():
    before = {p: p.read_bytes() for p in PACKAGE_ROOT.rglob("*.py")}
    collect_imports()
    assert all(p.read_bytes() == value for p, value in before.items())
