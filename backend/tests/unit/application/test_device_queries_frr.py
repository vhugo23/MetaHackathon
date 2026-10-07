"""Snapshot-less FRR Lab 1 router behavior (NPE-1C3B1) for the list, detail and
drift use cases, plus proof the Cisco/Arista invariants are unchanged."""

from datetime import UTC, datetime
from typing import Any

import pytest

from meta_rne.application.device_drift import GetDeviceDriftService
from meta_rne.application.device_queries import GetDeviceDetailService, ListDevicesService
from meta_rne.application.errors import DeviceNotFoundError, DriftNotApplicableError
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)
ROUTER = "lab1-leaf-1"


def _frr(device_id: str = ROUTER) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.FRR,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


def _snapshotless_cisco() -> Device:
    return Device(
        device_id="spine-01",
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


class _SpyUnitOfWork(InMemoryUnitOfWork):
    def __init__(self, store: InMemoryStore, log: list[str]) -> None:
        super().__init__(store)
        self._log = log

    def rollback(self) -> None:
        self._log.append("rollback")
        super().rollback()

    def close(self) -> None:
        self._log.append("close")
        super().close()


def _factory(store: InMemoryStore, log: list[str] | None = None) -> Any:
    if log is None:
        return lambda: InMemoryUnitOfWork(store)
    return lambda: _SpyUnitOfWork(store, log)


def test_list_devices__includes_frr_router_normally() -> None:
    store = InMemoryStore()
    store.devices[ROUTER] = _frr()

    devices = ListDevicesService(_factory(store)).list_all()

    assert devices == (store.devices[ROUTER],)
    assert devices[0].vendor is VendorType.FRR


def test_device_detail__frr_router__returns_device_with_no_configuration() -> None:
    store = InMemoryStore()
    store.devices[ROUTER] = _frr()
    log: list[str] = []

    result = GetDeviceDetailService(_factory(store, log)).get(ROUTER)

    assert result.device == store.devices[ROUTER]
    assert result.normalized_config is None
    assert log == ["close"]  # closed exactly once, no rollback


def test_device_detail__missing_frr_router__still_raises_device_not_found() -> None:
    with pytest.raises(DeviceNotFoundError):
        GetDeviceDetailService(_factory(InMemoryStore())).get(ROUTER)


def test_device_detail__snapshotless_cisco__still_a_broken_invariant() -> None:
    store = InMemoryStore()
    store.devices["spine-01"] = _snapshotless_cisco()

    with pytest.raises(RuntimeError, match="no current_snapshot_id"):
        GetDeviceDetailService(_factory(store)).get("spine-01")


def test_drift__frr_router__raises_drift_not_applicable_and_cleans_up() -> None:
    store = InMemoryStore()
    store.devices[ROUTER] = _frr()
    log: list[str] = []

    with pytest.raises(DriftNotApplicableError) as exc_info:
        GetDeviceDriftService(_factory(store, log)).get_drift(ROUTER)

    assert exc_info.value.device_id == ROUTER
    assert "no configuration snapshot" in str(exc_info.value)
    assert log == ["rollback", "close"]


def test_drift__missing_frr_router__still_raises_device_not_found() -> None:
    with pytest.raises(DeviceNotFoundError):
        GetDeviceDriftService(_factory(InMemoryStore())).get_drift(ROUTER)


def test_drift__snapshotless_cisco__still_a_broken_invariant() -> None:
    store = InMemoryStore()
    store.devices["spine-01"] = _snapshotless_cisco()

    with pytest.raises(RuntimeError, match="no baseline_snapshot_id"):
        GetDeviceDriftService(_factory(store)).get_drift("spine-01")
