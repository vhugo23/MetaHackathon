"""Contract tests for ``GET /devices/{device_id}/drift`` (Day 9, Gate 4).

Each test builds its own isolated ``create_app(...)`` instance — never the
module-level production ``app`` and never ``app.dependency_overrides``, same
convention as ``test_incidents_api.py``/``test_incident_resolution_api.py``.
Baseline/current snapshots are established either directly against the
``InMemoryStore`` (mirroring ``tests/unit/application/test_device_drift.py``'s
Gate 3 convention, when the test needs precise control over snapshot
identity/content) or through the real ``POST /devices/{device_id}/config``
endpoint (when the test only needs "a submission happened").
"""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import (
    AclAction,
    AdminState,
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
    VendorType,
)
from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)


def _interface(**overrides: object) -> NormalizedInterface:
    defaults: dict[str, object] = {
        "name": "GigabitEthernet0/1",
        "description": None,
        "ip_address": "10.0.0.1/30",
        "mtu": None,
        "admin_state": AdminState.UP,
        "acl_in": None,
        "acl_out": None,
    }
    defaults.update(overrides)
    return NormalizedInterface(**defaults)  # type: ignore[arg-type]


def _acl_entry(**overrides: object) -> NormalizedAclEntry:
    defaults: dict[str, object] = {
        "sequence": 10,
        "action": AclAction.PERMIT,
        "protocol": "tcp",
        "source": "any",
        "destination": "any",
    }
    defaults.update(overrides)
    return NormalizedAclEntry(**defaults)  # type: ignore[arg-type]


def _acl(**overrides: object) -> NormalizedAcl:
    defaults: dict[str, object] = {
        "name": "ACL-EXTERNAL-IN",
        "entries": (_acl_entry(),),
    }
    defaults.update(overrides)
    return NormalizedAcl(**defaults)  # type: ignore[arg-type]


def _config(
    interfaces: tuple[NormalizedInterface, ...] = (),
    acls: tuple[NormalizedAcl, ...] = (),
    hostname: str = DEVICE_ID,
) -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname=hostname,
        interfaces=interfaces,
        routing=NormalizedRouting(bgp_neighbors=()),
        acls=acls,
    )


def _snapshot(
    snapshot_id: str,
    config: NormalizedConfiguration,
    device_id: str = DEVICE_ID,
    vendor: VendorType = VendorType.CISCO_IOS_XE,
    submitted_at: datetime = T0,
) -> ConfigurationSnapshot:
    raw = f"raw-config-{snapshot_id}"
    return ConfigurationSnapshot(
        snapshot_id=snapshot_id,
        device_id=device_id,
        vendor=vendor,
        raw_config_text=raw,
        raw_text_hash=compute_raw_text_hash(raw),
        normalized_config=config,
        submitted_at=submitted_at,
    )


def _device(
    device_id: str,
    baseline_snapshot_id: str,
    current_snapshot_id: str,
    vendor: VendorType = VendorType.CISCO_IOS_XE,
) -> Device:
    return Device(
        device_id=device_id,
        vendor=vendor,
        current_snapshot_id=current_snapshot_id,
        baseline_snapshot_id=baseline_snapshot_id,
        created_at=T0,
        updated_at=T1,
    )


def _store_with(
    baseline_config: NormalizedConfiguration,
    current_config: NormalizedConfiguration | None = None,
    device_id: str = DEVICE_ID,
) -> InMemoryStore:
    store = InMemoryStore()
    baseline_snapshot = _snapshot(
        f"snap-baseline-{device_id}", baseline_config, device_id=device_id, submitted_at=T0
    )
    store.snapshots[baseline_snapshot.snapshot_id] = baseline_snapshot

    if current_config is None:
        current_snapshot_id = baseline_snapshot.snapshot_id
    else:
        current_snapshot = _snapshot(
            f"snap-current-{device_id}", current_config, device_id=device_id, submitted_at=T1
        )
        store.snapshots[current_snapshot.snapshot_id] = current_snapshot
        current_snapshot_id = current_snapshot.snapshot_id

    store.devices[device_id] = _device(
        device_id, baseline_snapshot.snapshot_id, current_snapshot_id
    )
    return store


def _test_app(store: InMemoryStore) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    return TestClient(app)


def test_device_drift_api__missing_device__returns_404_with_exact_body() -> None:
    client = _test_app(InMemoryStore())

    response = client.get("/devices/missing-device/drift")

    assert response.status_code == 404
    body: dict[str, Any] = response.json()
    assert body == {
        "code": "device_not_found",
        "detail": "device not found: 'missing-device'",
    }


def test_device_drift_api__one_submission__returns_200_with_empty_arrays() -> None:
    config = _config(interfaces=(_interface(),), acls=(_acl(),))
    store = _store_with(baseline_config=config)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body == {"added": [], "removed": [], "changed": []}


def test_device_drift_api__removed_acl__returns_exact_removed_entry() -> None:
    baseline_config = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    current_config = _config(acls=())
    store = _store_with(baseline_config=baseline_config, current_config=current_config)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["added"] == []
    assert body["changed"] == []
    assert body["removed"] == [
        {
            "resource": "acl:ACL-EXTERNAL-IN",
            "field": None,
            "old_value": "ACL-EXTERNAL-IN",
            "new_value": None,
        }
    ]


def test_device_drift_api__added_interface__returns_exact_added_entry() -> None:
    baseline_config = _config(interfaces=())
    current_config = _config(interfaces=(_interface(name="GigabitEthernet0/2"),))
    store = _store_with(baseline_config=baseline_config, current_config=current_config)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["removed"] == []
    assert body["changed"] == []
    assert body["added"] == [
        {
            "resource": "interface:GigabitEthernet0/2",
            "field": None,
            "old_value": None,
            "new_value": "GigabitEthernet0/2",
        }
    ]


def test_device_drift_api__changed_admin_state__returns_exact_changed_entry() -> None:
    baseline_config = _config(interfaces=(_interface(admin_state=AdminState.UP),))
    current_config = _config(interfaces=(_interface(admin_state=AdminState.DOWN),))
    store = _store_with(baseline_config=baseline_config, current_config=current_config)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["added"] == []
    assert body["removed"] == []
    assert body["changed"] == [
        {
            "resource": "interface:GigabitEthernet0/1",
            "field": "admin_state",
            "old_value": "up",
            "new_value": "down",
        }
    ]


def test_device_drift_api__multiple_changed_fields__json_array_order_matches_report_order() -> None:
    baseline_config = _config(
        interfaces=(
            _interface(
                name="GigabitEthernet0/1",
                description=None,
                ip_address="10.0.0.1/30",
                admin_state=AdminState.UP,
            ),
        )
    )
    current_config = _config(
        interfaces=(
            _interface(
                name="GigabitEthernet0/1",
                description="uplink",
                ip_address="10.0.0.2/30",
                admin_state=AdminState.DOWN,
            ),
        )
    )
    store = _store_with(baseline_config=baseline_config, current_config=current_config)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert [entry["field"] for entry in body["changed"]] == [
        "description",
        "ip_address",
        "admin_state",
    ]


def test_device_drift_api__null_field_and_value_members__serialize_as_json_null() -> None:
    baseline_config = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    current_config = _config(acls=())
    store = _store_with(baseline_config=baseline_config, current_config=current_config)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    body: dict[str, Any] = response.json()
    entry = body["removed"][0]
    assert entry["field"] is None
    assert entry["new_value"] is None
    # Confirmed present as explicit JSON null, not merely absent.
    assert "field" in entry
    assert "new_value" in entry


def test_device_drift_api__response_body__exposes_only_approved_fields() -> None:
    baseline_config = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    current_config = _config(acls=())
    store = _store_with(baseline_config=baseline_config, current_config=current_config)
    client = _test_app(store)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    body: dict[str, Any] = response.json()
    assert set(body.keys()) == {"added", "removed", "changed"}
    entry = body["removed"][0]
    assert set(entry.keys()) == {"resource", "field", "old_value", "new_value"}
    raw_text = response.text
    for forbidden in (
        "snap-baseline",
        "snap-current",
        "raw_config_text",
        "raw-config-",
        "vendor",
        "cisco-ios-xe",
        "severity",
        "recommendation",
        "incident",
    ):
        assert forbidden not in raw_text


def test_device_drift_api__unsupported_method__returns_framework_controlled_405() -> None:
    store = _store_with(baseline_config=_config())
    client = _test_app(store)

    response = client.post(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 405


def test_device_drift_api__uses_exact_path_device_id__not_a_fixed_device() -> None:
    spine_config = _config(interfaces=(_interface(),))
    leaf_baseline = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    leaf_current = _config(acls=())

    store = InMemoryStore()
    spine_snapshot = _snapshot("snap-spine", spine_config, device_id="spine-01")
    store.snapshots[spine_snapshot.snapshot_id] = spine_snapshot
    store.devices["spine-01"] = _device(
        "spine-01", spine_snapshot.snapshot_id, spine_snapshot.snapshot_id
    )

    leaf_baseline_snapshot = _snapshot(
        "snap-leaf-baseline", leaf_baseline, device_id="leaf-02", submitted_at=T0
    )
    leaf_current_snapshot = _snapshot(
        "snap-leaf-current", leaf_current, device_id="leaf-02", submitted_at=T1
    )
    store.snapshots[leaf_baseline_snapshot.snapshot_id] = leaf_baseline_snapshot
    store.snapshots[leaf_current_snapshot.snapshot_id] = leaf_current_snapshot
    store.devices["leaf-02"] = _device(
        "leaf-02", leaf_baseline_snapshot.snapshot_id, leaf_current_snapshot.snapshot_id
    )

    client = _test_app(store)

    spine_response = client.get("/devices/spine-01/drift")
    leaf_response = client.get("/devices/leaf-02/drift")

    assert spine_response.json() == {"added": [], "removed": [], "changed": []}
    leaf_body = leaf_response.json()
    assert leaf_body["removed"] == [
        {
            "resource": "acl:ACL-EXTERNAL-IN",
            "field": None,
            "old_value": "ACL-EXTERNAL-IN",
            "new_value": None,
        }
    ]


def test_device_drift_api__missing_referenced_snapshot__returns_generic_500_not_404() -> None:
    store = InMemoryStore()
    # A Device inserted directly (bypassing DeviceRepository.save()'s
    # referenced-snapshot validation) whose baseline_snapshot_id points to a
    # snapshot that does not exist — an internal invariant violation, never
    # reachable through the public API's own write paths.
    store.devices[DEVICE_ID] = _device(DEVICE_ID, "snap-does-not-exist", "snap-does-not-exist")
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    # The unmapped RuntimeError falls through to FastAPI/Starlette's own
    # generic 500 handling — plain text, never this API's ApiErrorResponse
    # JSON schema, and never a leaked traceback.
    assert response.status_code == 500
    assert response.status_code != 404
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text == "Internal Server Error"
    assert "Traceback" not in response.text
