"""API contract for snapshot-less FRR Lab 1 routers (NPE-1C3B1): the existing
device endpoints represent them with existing optional fields, and drift
returns an explicit 409 ``drift_not_applicable``. Isolated ``create_app``
instance per test, as in ``test_devices_api.py``."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.application.lab_device_registration import (
    LAB1_ROUTER_DEVICE_IDS,
    RegisterLabDevicesService,
)
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)


def _client(store: InMemoryStore) -> TestClient:
    return TestClient(
        create_app(
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            adapter_registry=AdapterRegistry([CiscoAdapter()]),
            seed_on_startup=False,
        )
    )


def _registered_store() -> InMemoryStore:
    store = InMemoryStore()
    RegisterLabDevicesService(lambda: InMemoryUnitOfWork(store)).register(T0)
    return store


def test_list_devices__lists_the_four_registered_routers() -> None:
    response = _client(_registered_store()).get("/devices")

    assert response.status_code == 200
    body = response.json()
    assert sorted(item["device_id"] for item in body) == sorted(LAB1_ROUTER_DEVICE_IDS)
    for item in body:
        assert item["vendor"] == "frr"
        assert item["current_snapshot_id"] is None
        assert item["baseline_snapshot_id"] is None


def test_get_device__frr_router__returns_200_with_null_normalized_config() -> None:
    response = _client(_registered_store()).get("/devices/lab1-leaf-1")

    assert response.status_code == 200
    assert response.json() == {
        "device_id": "lab1-leaf-1",
        "vendor": "frr",
        "current_snapshot_id": None,
        "baseline_snapshot_id": None,
        "created_at": "2026-10-07T10:00:00Z",
        "updated_at": "2026-10-07T10:00:00Z",
        "normalized_config": None,
    }


def test_get_device_drift__frr_router__returns_409_drift_not_applicable() -> None:
    response = _client(_registered_store()).get("/devices/lab1-leaf-1/drift")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "drift_not_applicable"
    assert "no configuration snapshot" in body["detail"]


def test_get_device_and_drift__unknown_router__still_404() -> None:
    client = _client(InMemoryStore())

    assert client.get("/devices/lab1-leaf-1").status_code == 404
    assert client.get("/devices/lab1-leaf-1/drift").status_code == 404


def test_submit_configuration__frr_vendor__rejected_as_unsupported_vendor() -> None:
    client = _client(_registered_store())

    response = client.post(
        "/devices/lab1-leaf-1/config",
        json={"vendor": "frr", "raw_config_text": "hostname lab1-leaf-1\n"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_vendor"


def test_get_device__snapshotless_cisco__still_unmapped_500() -> None:
    store = InMemoryStore()
    store.devices["spine-01"] = Device(
        device_id="spine-01",
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )
    client = TestClient(
        create_app(
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            adapter_registry=AdapterRegistry([CiscoAdapter()]),
            seed_on_startup=False,
        ),
        raise_server_exceptions=False,
    )

    assert client.get("/devices/spine-01").status_code == 500
