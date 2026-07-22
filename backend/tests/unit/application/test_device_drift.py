"""GetDeviceDriftService behavior (Day 9, Gate 3).

On-demand configuration-drift query: one UnitOfWork per call, no writes, no
commit, no incident creation. See docs/architecture.md Section 8 and
docs/product-spec.md FR-04/AC-05/AC-06. Mirrors ListIncidentsService's
exception-preserving UnitOfWork lifecycle test style (Day 5B) and
test_incident_resolution.py's hand-written fake/spy convention for the
no-write/no-commit assertions.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from meta_rne.application.device_drift import GetDeviceDriftService
from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.detection.drift_detector import DriftDetector
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
T2 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


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
) -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname=DEVICE_ID,
        interfaces=interfaces,
        routing=NormalizedRouting(bgp_neighbors=()),
        acls=acls,
    )


def _snapshot(
    snapshot_id: str,
    config: NormalizedConfiguration,
    vendor: VendorType = VendorType.CISCO_IOS_XE,
    submitted_at: datetime = T0,
) -> ConfigurationSnapshot:
    raw = f"raw-config-{snapshot_id}"
    return ConfigurationSnapshot(
        snapshot_id=snapshot_id,
        device_id=DEVICE_ID,
        vendor=vendor,
        raw_config_text=raw,
        raw_text_hash=compute_raw_text_hash(raw),
        normalized_config=config,
        submitted_at=submitted_at,
    )


def _device(
    baseline_snapshot_id: str,
    current_snapshot_id: str,
    vendor: VendorType = VendorType.CISCO_IOS_XE,
) -> Device:
    return Device(
        device_id=DEVICE_ID,
        vendor=vendor,
        current_snapshot_id=current_snapshot_id,
        baseline_snapshot_id=baseline_snapshot_id,
        created_at=T0,
        updated_at=T1,
    )


def _store_with(
    baseline_config: NormalizedConfiguration,
    current_config: NormalizedConfiguration | None = None,
    vendor: VendorType = VendorType.CISCO_IOS_XE,
) -> InMemoryStore:
    store = InMemoryStore()
    baseline_snapshot = _snapshot("snap-baseline", baseline_config, vendor=vendor, submitted_at=T0)
    store.snapshots[baseline_snapshot.snapshot_id] = baseline_snapshot

    if current_config is None:
        current_snapshot_id = baseline_snapshot.snapshot_id
    else:
        current_snapshot = _snapshot("snap-current", current_config, vendor=vendor, submitted_at=T1)
        store.snapshots[current_snapshot.snapshot_id] = current_snapshot
        current_snapshot_id = current_snapshot.snapshot_id

    store.devices[DEVICE_ID] = _device(
        baseline_snapshot.snapshot_id, current_snapshot_id, vendor=vendor
    )
    return store


def test_get_device_drift__missing_device__raises_device_not_found_error() -> None:
    store = InMemoryStore()
    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))

    with pytest.raises(DeviceNotFoundError) as exc_info:
        service.get_drift(DEVICE_ID)

    assert exc_info.value.device_id == DEVICE_ID
    assert str(exc_info.value) == f"device not found: {DEVICE_ID!r}"


def test_get_device_drift__one_submission__baseline_equals_current__empty_report() -> None:
    config = _config(interfaces=(_interface(),), acls=(_acl(),))
    store = _store_with(baseline_config=config)
    device = store.devices[DEVICE_ID]
    assert device.baseline_snapshot_id == device.current_snapshot_id

    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))
    report = service.get_drift(DEVICE_ID)

    assert report.added == ()
    assert report.removed == ()
    assert report.changed == ()


def test_get_device_drift__later_submission_removes_acl__returns_removed_entry() -> None:
    baseline_config = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    current_config = _config(acls=())
    store = _store_with(baseline_config=baseline_config, current_config=current_config)

    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))
    report = service.get_drift(DEVICE_ID)

    assert report.added == ()
    assert report.changed == ()
    assert len(report.removed) == 1
    entry = report.removed[0]
    assert entry.resource == "acl:ACL-EXTERNAL-IN"
    assert entry.old_value == "ACL-EXTERNAL-IN"
    assert entry.new_value is None


def test_get_device_drift__later_submission_adds_interface__returns_added_entry() -> None:
    baseline_config = _config(interfaces=())
    current_config = _config(interfaces=(_interface(name="GigabitEthernet0/2"),))
    store = _store_with(baseline_config=baseline_config, current_config=current_config)

    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))
    report = service.get_drift(DEVICE_ID)

    assert report.removed == ()
    assert report.changed == ()
    assert len(report.added) == 1
    entry = report.added[0]
    assert entry.resource == "interface:GigabitEthernet0/2"
    assert entry.old_value is None
    assert entry.new_value == "GigabitEthernet0/2"


def test_get_device_drift__later_submission_changes_admin_state__returns_changed_entry() -> None:
    baseline_config = _config(interfaces=(_interface(admin_state=AdminState.UP),))
    current_config = _config(interfaces=(_interface(admin_state=AdminState.DOWN),))
    store = _store_with(baseline_config=baseline_config, current_config=current_config)

    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))
    report = service.get_drift(DEVICE_ID)

    assert report.added == ()
    assert report.removed == ()
    assert len(report.changed) == 1
    entry = report.changed[0]
    assert entry.resource == "interface:GigabitEthernet0/1"
    assert entry.field == "admin_state"
    assert entry.old_value == "up"
    assert entry.new_value == "down"


def test_get_device_drift__uses_device_baseline_pointer_not_iteration_order() -> None:
    baseline_config = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    current_config = _config(acls=())

    store = InMemoryStore()
    # Insert the current snapshot into the store first, baseline second —
    # dict insertion order must never be mistaken for the device's
    # documented baseline/current pointers.
    current_snapshot = _snapshot("snap-current", current_config, submitted_at=T1)
    store.snapshots[current_snapshot.snapshot_id] = current_snapshot
    baseline_snapshot = _snapshot("snap-baseline", baseline_config, submitted_at=T0)
    store.snapshots[baseline_snapshot.snapshot_id] = baseline_snapshot
    store.devices[DEVICE_ID] = _device(baseline_snapshot.snapshot_id, current_snapshot.snapshot_id)

    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))
    report = service.get_drift(DEVICE_ID)

    assert len(report.removed) == 1
    assert report.removed[0].resource == "acl:ACL-EXTERNAL-IN"


def test_get_device_drift__uses_device_current_pointer_not_latest_timestamp() -> None:
    baseline_config = _config(acls=(_acl(name="ACL-EXTERNAL-IN"),))
    current_config = _config(acls=())
    # A decoy snapshot with a later timestamp than the device's actual
    # current_snapshot_id, not referenced by the device at all — if the
    # service picked "the snapshot with the newest timestamp" instead of
    # Device.current_snapshot_id, it would wrongly diff against this one.
    decoy_config = _config(acls=(_acl(name="ACL-EXTERNAL-IN"), _acl(name="ACL-DECOY")))

    store = InMemoryStore()
    baseline_snapshot = _snapshot("snap-baseline", baseline_config, submitted_at=T0)
    store.snapshots[baseline_snapshot.snapshot_id] = baseline_snapshot
    current_snapshot = _snapshot("snap-current", current_config, submitted_at=T1)
    store.snapshots[current_snapshot.snapshot_id] = current_snapshot
    decoy_snapshot = _snapshot("snap-decoy", decoy_config, submitted_at=T2)
    store.snapshots[decoy_snapshot.snapshot_id] = decoy_snapshot
    store.devices[DEVICE_ID] = _device(baseline_snapshot.snapshot_id, current_snapshot.snapshot_id)

    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))
    report = service.get_drift(DEVICE_ID)

    assert len(report.removed) == 1
    assert report.removed[0].resource == "acl:ACL-EXTERNAL-IN"
    assert report.added == ()


@dataclass
class _Calls:
    device_get_by_id: list[str] = field(default_factory=list)
    device_save: list[Device] = field(default_factory=list)
    snapshot_get_by_id: list[str] = field(default_factory=list)
    snapshot_add: list[ConfigurationSnapshot] = field(default_factory=list)
    commit: int = 0
    rollback: int = 0
    close: int = 0


class _FakeDeviceRepository:
    def __init__(self, calls: _Calls, device: Device | None) -> None:
        self._calls = calls
        self._device = device

    def get_by_id(self, device_id: str) -> Device | None:
        self._calls.device_get_by_id.append(device_id)
        return self._device

    def save(self, device: Device) -> None:
        self._calls.device_save.append(device)


class _FakeSnapshotRepository:
    def __init__(self, calls: _Calls, snapshots: dict[str, ConfigurationSnapshot]) -> None:
        self._calls = calls
        self._snapshots = snapshots

    def get_by_id(self, snapshot_id: str) -> ConfigurationSnapshot | None:
        self._calls.snapshot_get_by_id.append(snapshot_id)
        return self._snapshots.get(snapshot_id)

    def add(self, snapshot: ConfigurationSnapshot) -> None:
        self._calls.snapshot_add.append(snapshot)


class _FakeUnitOfWork:
    def __init__(
        self, calls: _Calls, device: Device | None, snapshots: dict[str, ConfigurationSnapshot]
    ) -> None:
        self._calls = calls
        self.devices = _FakeDeviceRepository(calls, device)
        self.configuration_snapshots = _FakeSnapshotRepository(calls, snapshots)

    def commit(self) -> None:
        self._calls.commit += 1

    def rollback(self) -> None:
        self._calls.rollback += 1

    def close(self) -> None:
        self._calls.close += 1


def _fake_uow_with_single_submission() -> tuple[_Calls, _FakeUnitOfWork]:
    calls = _Calls()
    config = _config(interfaces=(_interface(),))
    snapshot = _snapshot("snap-1", config)
    device = _device(snapshot.snapshot_id, snapshot.snapshot_id)
    uow = _FakeUnitOfWork(calls, device, {snapshot.snapshot_id: snapshot})
    return calls, uow


def test_get_device_drift__does_not_call_save_or_add() -> None:
    calls, uow = _fake_uow_with_single_submission()
    service = GetDeviceDriftService(lambda: uow)

    service.get_drift(DEVICE_ID)

    assert calls.device_save == []
    assert calls.snapshot_add == []


def test_get_device_drift__does_not_call_commit() -> None:
    calls, uow = _fake_uow_with_single_submission()
    service = GetDeviceDriftService(lambda: uow)

    service.get_drift(DEVICE_ID)

    assert calls.commit == 0
    assert calls.close == 1


def test_get_device_drift__does_not_mutate_device_or_snapshots() -> None:
    config = _config(interfaces=(_interface(),), acls=(_acl(),))
    store = _store_with(baseline_config=config)
    uow = InMemoryUnitOfWork(store)
    device_before = uow.devices.get_by_id(DEVICE_ID)
    assert device_before is not None
    snapshot_before = uow.configuration_snapshots.get_by_id(device_before.baseline_snapshot_id)  # type: ignore[arg-type]

    service = GetDeviceDriftService(lambda: uow)
    service.get_drift(DEVICE_ID)

    device_after = uow.devices.get_by_id(DEVICE_ID)
    snapshot_after = uow.configuration_snapshots.get_by_id(device_before.baseline_snapshot_id)  # type: ignore[arg-type]
    assert device_after == device_before
    assert snapshot_after == snapshot_before


def test_get_device_drift__vendor_neutral__cisco_and_arista_use_same_path() -> None:
    cisco_baseline = _config(interfaces=(_interface(name="GigabitEthernet0/1"),))
    cisco_current = _config(interfaces=())
    arista_baseline = _config(interfaces=(_interface(name="Ethernet1"),))
    arista_current = _config(interfaces=())

    cisco_store = _store_with(
        baseline_config=cisco_baseline, current_config=cisco_current, vendor=VendorType.CISCO_IOS_XE
    )
    arista_store = _store_with(
        baseline_config=arista_baseline, current_config=arista_current, vendor=VendorType.ARISTA_EOS
    )

    cisco_report = GetDeviceDriftService(lambda: InMemoryUnitOfWork(cisco_store)).get_drift(
        DEVICE_ID
    )
    arista_report = GetDeviceDriftService(lambda: InMemoryUnitOfWork(arista_store)).get_drift(
        DEVICE_ID
    )

    assert len(cisco_report.removed) == 1
    assert len(arista_report.removed) == 1
    assert cisco_report.added == arista_report.added == ()
    assert cisco_report.changed == arista_report.changed == ()


def test_get_device_drift__returns_drift_detector_output_unchanged() -> None:
    baseline_config = _config(
        interfaces=(
            _interface(name="GigabitEthernet0/1", admin_state=AdminState.UP),
            _interface(name="GigabitEthernet0/2"),
        ),
        acls=(_acl(name="ACL-EXTERNAL-IN"),),
    )
    current_config = _config(
        interfaces=(
            _interface(name="GigabitEthernet0/1", admin_state=AdminState.DOWN),
            _interface(name="GigabitEthernet0/3"),
        ),
        acls=(),
    )
    store = _store_with(baseline_config=baseline_config, current_config=current_config)

    service = GetDeviceDriftService(lambda: InMemoryUnitOfWork(store))
    report = service.get_drift(DEVICE_ID)

    expected = DriftDetector.compare(baseline_config, current_config)
    assert report == expected
