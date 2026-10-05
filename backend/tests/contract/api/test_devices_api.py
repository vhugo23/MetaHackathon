"""Contract tests for ``GET /devices`` (Day 12A1) and
``GET /devices/{device_id}`` (Day 12B).

Each test builds its own isolated ``create_app(...)`` instance — never the
module-level production ``app`` and never ``app.dependency_overrides``, same
convention as ``test_incidents_api.py``.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import NormalizedConfiguration, NormalizedRouting, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)


def _device(
    device_id: str,
    vendor: VendorType = VendorType.CISCO_IOS_XE,
    current_snapshot_id: str | None = None,
    baseline_snapshot_id: str | None = None,
    created_at: datetime = T0,
    updated_at: datetime = T0,
) -> Device:
    return Device(
        device_id=device_id,
        vendor=vendor,
        current_snapshot_id=current_snapshot_id,
        baseline_snapshot_id=baseline_snapshot_id,
        created_at=created_at,
        updated_at=updated_at,
    )


def _test_app(store: InMemoryStore) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    return TestClient(app)


def test_devices_api__empty_store__returns_empty_list() -> None:
    client = _test_app(InMemoryStore())

    response = client.get("/devices")

    assert response.status_code == 200
    assert response.json() == []


def test_devices_api__get_devices__returns_registered_device() -> None:
    store = InMemoryStore()
    store.devices["spine-01"] = _device("spine-01")
    client = _test_app(store)

    response = client.get("/devices")

    assert response.status_code == 200
    devices = response.json()
    assert isinstance(devices, list)
    assert len(devices) == 1
    device = devices[0]
    assert set(device.keys()) == {
        "device_id",
        "vendor",
        "current_snapshot_id",
        "baseline_snapshot_id",
        "created_at",
        "updated_at",
    }
    assert device["device_id"] == "spine-01"
    assert device["vendor"] == "cisco-ios-xe"
    assert device["current_snapshot_id"] is None
    assert device["baseline_snapshot_id"] is None


def test_devices_api__datetimes_serialize_as_iso8601() -> None:
    store = InMemoryStore()
    store.devices["spine-01"] = _device("spine-01", created_at=T0, updated_at=T0)
    client = _test_app(store)

    device = client.get("/devices").json()[0]

    assert device["created_at"] == "2026-07-18T10:00:00Z"
    assert device["updated_at"] == "2026-07-18T10:00:00Z"


def test_devices_api__multiple_devices__ordered_by_created_at_then_device_id() -> None:
    store = InMemoryStore()
    store.devices["zulu"] = _device("zulu", created_at=T0, updated_at=T0)
    store.devices["alpha"] = _device("alpha", created_at=T0, updated_at=T0)
    store.devices["leaf-01"] = _device("leaf-01", created_at=T1, updated_at=T1)
    client = _test_app(store)

    response = client.get("/devices")

    assert response.status_code == 200
    devices = response.json()
    assert [d["device_id"] for d in devices] == ["alpha", "zulu", "leaf-01"]


def test_devices_api__does_not_call_the_clock() -> None:
    calls: list[int] = []

    def spy_clock() -> datetime:
        calls.append(1)
        return T0

    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=spy_clock,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app)

    client.get("/devices")

    assert calls == []


def test_devices_api__query_service_called_exactly_once() -> None:
    store = InMemoryStore()

    class _CountingFactory:
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.call_count = 0

        def __call__(self) -> Any:
            self.call_count += 1
            return self._inner()

    factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    app = create_app(
        unit_of_work_factory=factory,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app)

    client.get("/devices")

    assert factory.call_count == 1


def test_devices_api__query_failure__returns_generic_production_500() -> None:
    class _FailingDevices:
        def list_all(self) -> tuple[Any, ...]:
            raise RuntimeError("boom")

    class _BoomUnitOfWork:
        def __init__(self) -> None:
            self.devices = _FailingDevices()

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    app = create_app(
        unit_of_work_factory=lambda: _BoomUnitOfWork(),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/devices")

    assert response.status_code == 500


def test_devices_api__unsupported_method__returns_framework_controlled_405() -> None:
    client = _test_app(InMemoryStore())

    response = client.post("/devices")

    assert response.status_code == 405


# --- Day 12B: GET /devices/{device_id} ---------------------------------------


def _config(hostname: str = "spine-01") -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname=hostname,
        interfaces=(),
        routing=NormalizedRouting(bgp_neighbors=()),
        acls=(),
    )


def _snapshot(
    snapshot_id: str,
    config: NormalizedConfiguration,
    device_id: str = "spine-01",
    submitted_at: datetime = T0,
) -> ConfigurationSnapshot:
    raw = f"raw-config-{snapshot_id}"
    return ConfigurationSnapshot(
        snapshot_id=snapshot_id,
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=raw,
        raw_text_hash=compute_raw_text_hash(raw),
        normalized_config=config,
        submitted_at=submitted_at,
    )


def _store_with_device_detail(device_id: str = "spine-01") -> InMemoryStore:
    store = InMemoryStore()
    snapshot = _snapshot("snap-1", _config(hostname=device_id), device_id=device_id)
    store.snapshots[snapshot.snapshot_id] = snapshot
    store.devices[device_id] = _device(
        device_id,
        current_snapshot_id=snapshot.snapshot_id,
        baseline_snapshot_id=snapshot.snapshot_id,
        created_at=T0,
        updated_at=T1,
    )
    return store


def test_device_detail_api__missing_device__returns_404_with_exact_body() -> None:
    client = _test_app(InMemoryStore())

    response = client.get("/devices/missing-device")

    assert response.status_code == 404
    assert response.json() == {
        "code": "device_not_found",
        "detail": "device not found: 'missing-device'",
    }


def test_device_detail_api__existing_device__returns_200_with_expected_fields() -> None:
    store = _store_with_device_detail("spine-01")
    client = _test_app(store)

    response = client.get("/devices/spine-01")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "device_id",
        "vendor",
        "current_snapshot_id",
        "baseline_snapshot_id",
        "created_at",
        "updated_at",
        "normalized_config",
    }
    assert body["device_id"] == "spine-01"
    assert body["vendor"] == "cisco-ios-xe"
    assert body["current_snapshot_id"] == "snap-1"
    assert body["baseline_snapshot_id"] == "snap-1"
    assert body["normalized_config"]["hostname"] == "spine-01"


def test_device_detail_api__uses_exact_path_device_id__not_a_fixed_device() -> None:
    store = InMemoryStore()
    spine_snapshot = _snapshot("snap-spine", _config("spine-01"), device_id="spine-01")
    store.snapshots[spine_snapshot.snapshot_id] = spine_snapshot
    store.devices["spine-01"] = _device(
        "spine-01",
        current_snapshot_id=spine_snapshot.snapshot_id,
        baseline_snapshot_id=spine_snapshot.snapshot_id,
    )
    leaf_snapshot = _snapshot("snap-leaf", _config("leaf-02"), device_id="leaf-02")
    store.snapshots[leaf_snapshot.snapshot_id] = leaf_snapshot
    store.devices["leaf-02"] = _device(
        "leaf-02",
        current_snapshot_id=leaf_snapshot.snapshot_id,
        baseline_snapshot_id=leaf_snapshot.snapshot_id,
    )
    client = _test_app(store)

    spine_response = client.get("/devices/spine-01")
    leaf_response = client.get("/devices/leaf-02")

    assert spine_response.json()["normalized_config"]["hostname"] == "spine-01"
    assert leaf_response.json()["normalized_config"]["hostname"] == "leaf-02"


def test_device_detail_api__returns_current_config_not_baseline() -> None:
    store = InMemoryStore()
    baseline_snapshot = _snapshot("snap-baseline", _config("baseline-host"), submitted_at=T0)
    current_snapshot = _snapshot("snap-current", _config("current-host"), submitted_at=T1)
    store.snapshots[baseline_snapshot.snapshot_id] = baseline_snapshot
    store.snapshots[current_snapshot.snapshot_id] = current_snapshot
    store.devices["spine-01"] = _device(
        "spine-01",
        current_snapshot_id=current_snapshot.snapshot_id,
        baseline_snapshot_id=baseline_snapshot.snapshot_id,
    )
    client = _test_app(store)

    response = client.get("/devices/spine-01")

    assert response.json()["normalized_config"]["hostname"] == "current-host"


def test_device_detail_api__does_not_call_the_clock() -> None:
    calls: list[int] = []

    def spy_clock() -> datetime:
        calls.append(1)
        return T0

    store = _store_with_device_detail("spine-01")
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=spy_clock,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app)

    client.get("/devices/spine-01")

    assert calls == []


def test_device_detail_api__unsupported_method__returns_framework_controlled_405() -> None:
    store = _store_with_device_detail("spine-01")
    client = _test_app(store)

    response = client.post("/devices/spine-01")

    assert response.status_code == 405


def test_device_detail_api__response_excludes_envelope_and_evidence_fields() -> None:
    store = _store_with_device_detail("spine-01")
    client = _test_app(store)

    response = client.get("/devices/spine-01")

    body = response.json()
    assert "data" not in body
    assert "error" not in body
    raw_text = response.text
    for forbidden in ("raw_config_text", "raw-config-", "severity", "recommendation", "incident"):
        assert forbidden not in raw_text
