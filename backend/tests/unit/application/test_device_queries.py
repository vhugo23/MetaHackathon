"""Unit tests for ``ListDevicesService`` (Day 12A1) against a real
``InMemoryUnitOfWork`` — mirrors ``ListIncidentsService``'s
exception-preserving lifecycle test style (Day 5B).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from meta_rne.application.device_queries import GetDeviceDetailService, ListDevicesService
from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.domain.config import NormalizedConfiguration, NormalizedRouting, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)


def _device(device_id: str, created_at: datetime = T0) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=created_at,
        updated_at=created_at,
    )


def _seed_device(store: InMemoryStore, device_id: str, created_at: datetime = T0) -> None:
    store.devices[device_id] = _device(device_id, created_at)


@dataclass
class _LifecycleCounts:
    commit: int = 0
    rollback: int = 0
    close: int = 0


class _FailingDevicesRepository:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def list_all(self) -> tuple[Any, ...]:
        raise self._error

    def get_by_id(self, device_id: str) -> None:
        return None

    def save(self, device: Any) -> None:
        raise NotImplementedError


@dataclass
class _LifecycleSpyUnitOfWork:
    _wrapped: Any
    _counts: _LifecycleCounts
    _fail_rollback: Exception | None = None
    _fail_close: Exception | None = None
    _devices_override: Any = None
    devices: Any = field(init=False)
    configuration_snapshots: Any = field(init=False)
    configuration_policies: Any = field(init=False)
    incidents: Any = field(init=False)

    def __post_init__(self) -> None:
        self.devices = self._devices_override or self._wrapped.devices
        self.configuration_snapshots = self._wrapped.configuration_snapshots
        self.configuration_policies = self._wrapped.configuration_policies
        self.incidents = self._wrapped.incidents

    def commit(self) -> None:
        self._counts.commit += 1
        self._wrapped.commit()

    def rollback(self) -> None:
        self._counts.rollback += 1
        if self._fail_rollback is not None:
            raise self._fail_rollback
        self._wrapped.rollback()

    def close(self) -> None:
        self._counts.close += 1
        if self._fail_close is not None:
            raise self._fail_close
        self._wrapped.close()


class _CountingFactory:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.call_count = 0

    def __call__(self) -> Any:
        self.call_count += 1
        return self._inner()


def test_list_devices_service__empty_store__returns_empty_tuple() -> None:
    store = InMemoryStore()
    service = ListDevicesService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    assert service.list_all() == ()


def test_list_devices_service__populated_store__returns_all_devices_in_order() -> None:
    store = InMemoryStore()
    _seed_device(store, "spine-01", T0)
    _seed_device(store, "leaf-01", T0)
    service = ListDevicesService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    devices = service.list_all()

    assert {d.device_id for d in devices} == {"spine-01", "leaf-01"}


def test_list_devices_service__creates_exactly_one_unit_of_work() -> None:
    store = InMemoryStore()
    factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    service = ListDevicesService(unit_of_work_factory=factory)

    service.list_all()

    assert factory.call_count == 1


def test_list_devices_service__never_commits() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    service = ListDevicesService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.list_all()

    assert counts.commit == 0


def test_list_devices_service__closes_exactly_once_after_success() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    service = ListDevicesService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.list_all()

    assert counts.close == 1
    assert counts.rollback == 0


def test_list_devices_service__read_failure__preserves_original_exception() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    read_error = RuntimeError("read boom")
    service = ListDevicesService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store),
            counts,
            _devices_override=_FailingDevicesRepository(read_error),
        )
    )

    with pytest.raises(RuntimeError, match="read boom") as exc_info:
        service.list_all()

    assert exc_info.value is read_error
    assert counts.rollback == 1
    assert counts.close == 1


def test_list_devices_service__rollback_also_fails__original_exception_preserved() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    read_error = RuntimeError("read boom")
    rollback_error = RuntimeError("rollback boom")
    service = ListDevicesService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store),
            counts,
            _fail_rollback=rollback_error,
            _devices_override=_FailingDevicesRepository(read_error),
        )
    )

    with pytest.raises(RuntimeError, match="read boom") as exc_info:
        service.list_all()

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("rollback also failed" in note for note in notes)
    assert counts.close == 1


def test_list_devices_service__close_also_fails__original_exception_preserved() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    read_error = RuntimeError("read boom")
    close_error = RuntimeError("close boom")
    service = ListDevicesService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store),
            counts,
            _fail_close=close_error,
            _devices_override=_FailingDevicesRepository(read_error),
        )
    )

    with pytest.raises(RuntimeError, match="read boom") as exc_info:
        service.list_all()

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("close also failed" in note for note in notes)
    assert counts.close == 1


# --- Day 12B: GetDeviceDetailService -----------------------------------------


DEVICE_ID = "spine-01"


def _config(hostname: str = DEVICE_ID) -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname=hostname,
        interfaces=(),
        routing=NormalizedRouting(bgp_neighbors=()),
        acls=(),
    )


def _snapshot(
    snapshot_id: str,
    config: NormalizedConfiguration,
    device_id: str = DEVICE_ID,
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


def _device_with_snapshots(
    baseline_snapshot_id: str,
    current_snapshot_id: str,
    device_id: str = DEVICE_ID,
) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=current_snapshot_id,
        baseline_snapshot_id=baseline_snapshot_id,
        created_at=T0,
        updated_at=T1,
    )


def _store_with_current_snapshot(config: NormalizedConfiguration) -> InMemoryStore:
    store = InMemoryStore()
    snapshot = _snapshot("snap-1", config)
    store.snapshots[snapshot.snapshot_id] = snapshot
    store.devices[DEVICE_ID] = _device_with_snapshots(snapshot.snapshot_id, snapshot.snapshot_id)
    return store


def test_get_device_detail_service__missing_device__raises_device_not_found_error() -> None:
    store = InMemoryStore()
    service = GetDeviceDetailService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    with pytest.raises(DeviceNotFoundError) as exc_info:
        service.get(DEVICE_ID)

    assert exc_info.value.device_id == DEVICE_ID
    assert str(exc_info.value) == f"device not found: {DEVICE_ID!r}"


def test_get_device_detail_service__returns_device_fields_and_current_config() -> None:
    config = _config()
    store = _store_with_current_snapshot(config)
    device = store.devices[DEVICE_ID]
    service = GetDeviceDetailService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    result = service.get(DEVICE_ID)

    assert result.device == device
    assert result.normalized_config == config


def test_get_device_detail_service__uses_current_pointer_not_baseline() -> None:
    baseline_config = _config(hostname="baseline-host")
    current_config = _config(hostname="current-host")
    store = InMemoryStore()
    baseline_snapshot = _snapshot("snap-baseline", baseline_config, submitted_at=T0)
    current_snapshot = _snapshot("snap-current", current_config, submitted_at=T1)
    store.snapshots[baseline_snapshot.snapshot_id] = baseline_snapshot
    store.snapshots[current_snapshot.snapshot_id] = current_snapshot
    store.devices[DEVICE_ID] = _device_with_snapshots(
        baseline_snapshot.snapshot_id, current_snapshot.snapshot_id
    )
    service = GetDeviceDetailService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    result = service.get(DEVICE_ID)

    assert result.normalized_config == current_config
    assert result.normalized_config != baseline_config


def test_get_device_detail_service__creates_exactly_one_unit_of_work() -> None:
    store = _store_with_current_snapshot(_config())
    factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    service = GetDeviceDetailService(unit_of_work_factory=factory)

    service.get(DEVICE_ID)

    assert factory.call_count == 1


def test_get_device_detail_service__never_commits_and_closes_exactly_once() -> None:
    store = _store_with_current_snapshot(_config())
    counts = _LifecycleCounts()
    service = GetDeviceDetailService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.get(DEVICE_ID)

    assert counts.commit == 0
    assert counts.close == 1
    assert counts.rollback == 0


def test_get_device_detail_service__missing_device__closes_after_rollback() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    service = GetDeviceDetailService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    with pytest.raises(DeviceNotFoundError):
        service.get(DEVICE_ID)

    assert counts.rollback == 1
    assert counts.close == 1
    assert counts.commit == 0


def test_get_device_detail_service__does_not_mutate_device_or_snapshot() -> None:
    store = _store_with_current_snapshot(_config())
    uow = InMemoryUnitOfWork(store)
    device_before = uow.devices.get_by_id(DEVICE_ID)
    assert device_before is not None
    snapshot_before = uow.configuration_snapshots.get_by_id(device_before.current_snapshot_id)  # type: ignore[arg-type]

    service = GetDeviceDetailService(unit_of_work_factory=lambda: uow)
    service.get(DEVICE_ID)

    device_after = uow.devices.get_by_id(DEVICE_ID)
    snapshot_after = uow.configuration_snapshots.get_by_id(device_before.current_snapshot_id)  # type: ignore[arg-type]
    assert device_after == device_before
    assert snapshot_after == snapshot_before


def test_get_device_detail_service__current_snapshot_id_none__raises_runtime_error() -> None:
    store = InMemoryStore()
    store.devices[DEVICE_ID] = _device(DEVICE_ID)
    service = GetDeviceDetailService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    with pytest.raises(RuntimeError, match="no current_snapshot_id"):
        service.get(DEVICE_ID)


def test_get_device_detail_service__current_snapshot_missing__raises_runtime_error() -> None:
    store = InMemoryStore()
    store.devices[DEVICE_ID] = _device_with_snapshots("snap-missing", "snap-missing")
    service = GetDeviceDetailService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    with pytest.raises(RuntimeError, match="does not exist"):
        service.get(DEVICE_ID)


def test_get_device_detail_service__snapshot_belongs_to_other_device__raises_runtime_error() -> (
    None
):
    store = InMemoryStore()
    snapshot = _snapshot("snap-1", _config(), device_id="leaf-02")
    store.snapshots[snapshot.snapshot_id] = snapshot
    store.devices[DEVICE_ID] = _device_with_snapshots(snapshot.snapshot_id, snapshot.snapshot_id)
    service = GetDeviceDetailService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    with pytest.raises(RuntimeError, match="device_id"):
        service.get(DEVICE_ID)


def test_get_device_detail_service__snapshot_vendor_differs__raises_runtime_error() -> None:
    store = InMemoryStore()
    raw = "raw-config-snap-1"
    snapshot = ConfigurationSnapshot(
        snapshot_id="snap-1",
        device_id=DEVICE_ID,
        vendor=VendorType.ARISTA_EOS,
        raw_config_text=raw,
        raw_text_hash=compute_raw_text_hash(raw),
        normalized_config=_config(),
        submitted_at=T0,
    )
    store.snapshots[snapshot.snapshot_id] = snapshot
    store.devices[DEVICE_ID] = _device_with_snapshots(snapshot.snapshot_id, snapshot.snapshot_id)
    service = GetDeviceDetailService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    with pytest.raises(RuntimeError, match="vendor"):
        service.get(DEVICE_ID)
