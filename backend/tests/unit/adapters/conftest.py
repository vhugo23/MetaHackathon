from collections.abc import Callable
from pathlib import Path

import pytest

_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "containerlab_frr"


@pytest.fixture(scope="session")
def containerlab_fixture_root() -> Path:
    return _FIXTURE_ROOT


@pytest.fixture(scope="session")
def read_fixture() -> Callable[[str, str, str], str]:
    """``read_fixture(set, node, filename)`` -> captured command output."""

    def read(fixture_set: str, node: str, filename: str) -> str:
        return (_FIXTURE_ROOT / fixture_set / node / filename).read_text(encoding="utf-8")

    return read
