"""Shared test fixtures."""

import sys
from pathlib import Path

import pytest

sys.dont_write_bytecode = True

FIXTURES = Path(__file__).parent / "fixtures"


def _clean_python_bytecode() -> None:
    for root in (Path("tests"), Path("legacy")):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".pyc", ".pyo"}:
                path.unlink()
        for path in sorted(root.rglob("__pycache__"), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass


def pytest_sessionstart(session) -> None:  # type: ignore[no-untyped-def]
    _clean_python_bytecode()


def pytest_sessionfinish(session, exitstatus) -> None:  # type: ignore[no-untyped-def]
    _clean_python_bytecode()


@pytest.fixture
def python_fixtures():
    return FIXTURES / "python"


@pytest.fixture
def javascript_fixtures():
    return FIXTURES / "javascript"


@pytest.fixture
def java_fixtures():
    return FIXTURES / "java"


@pytest.fixture
def go_fixtures():
    return FIXTURES / "go"


@pytest.fixture
def typescript_fixtures():
    return FIXTURES / "typescript"


@pytest.fixture
def rust_fixtures():
    return FIXTURES / "rust"


@pytest.fixture
def c_fixtures():
    return FIXTURES / "c"


@pytest.fixture
def cpp_fixtures():
    return FIXTURES / "cpp"
