import pytest
from fastapi.testclient import TestClient

from meta_rne.api.app import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)
