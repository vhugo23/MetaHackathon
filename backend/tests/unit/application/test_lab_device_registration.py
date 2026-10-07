"""Unit tests for ``RegisterLabDevicesService`` (NPE-1C3B1) against a real
``InMemoryUnitOfWork``."""

import inspect
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meta_rne.application.errors import LabDeviceConflictError
from meta_rne.application.lab_device_registration import (
    LAB1_ROUTER_DEVICE_IDS,
    RegisterLabDevicesService,
)
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)


def _service(store: InMemoryStore) -> RegisterLabDevicesService:
    return RegisterLabDevicesService(lambda: InMemoryUnitOfWork(store))


def _existing(device_id: str, **overrides: Any) -> Device:
    fields: dict[str, Any] = {
        "device_id": device_id,
        "vendor": VendorType.CISCO_IOS_XE,
        "current_snapshot_id": None,
        "baseline_snapshot_id": None,
        "created_at": T0,
        "updated_at": T0,
    }
    fields.update(overrides)
    return Device(**fields)


def test_lab1_router_ids__are_exactly_the_four_fixed_routers() -> None:
    assert LAB1_ROUTER_DEVICE_IDS == (
        "lab1-spine-1",
        "lab1-spine-2",
        "lab1-leaf-1",
        "lab1-leaf-2",
    )


def test_register__empty_store__registers_four_snapshot_less_frr_routers() -> None:
    store = InMemoryStore()

    result = _service(store).register(T0)

    assert result.registered == LAB1_ROUTER_DEVICE_IDS
    assert result.already_registered == ()
    assert set(store.devices) == set(LAB1_ROUTER_DEVICE_IDS)
    for device in store.devices.values():
        assert device.vendor is VendorType.FRR
        assert device.current_snapshot_id is None
        assert device.baseline_snapshot_id is None
        assert device.created_at == T0
        assert device.updated_at == T0


def test_register__repeated__is_idempotent_and_leaves_rows_untouched() -> None:
    store = InMemoryStore()
    service = _service(store)
    service.register(T0)
    before = dict(store.devices)

    result = service.register(T1)

    assert result.registered == ()
    assert result.already_registered == LAB1_ROUTER_DEVICE_IDS
    assert store.devices == before
    assert all(device.updated_at == T0 for device in store.devices.values())


def test_register__partially_registered__registers_only_missing() -> None:
    store = InMemoryStore()
    service = _service(store)
    service.register(T0)
    del store.devices["lab1-leaf-2"]

    result = service.register(T1)

    assert result.registered == ("lab1-leaf-2",)
    assert result.already_registered == ("lab1-spine-1", "lab1-spine-2", "lab1-leaf-1")
    assert store.devices["lab1-leaf-2"].created_at == T1


def test_register__existing_device_with_other_vendor__fails_closed_and_writes_nothing() -> None:
    store = InMemoryStore()
    conflicting = _existing("lab1-leaf-1")
    store.devices["lab1-leaf-1"] = conflicting

    with pytest.raises(LabDeviceConflictError) as exc_info:
        _service(store).register(T0)

    assert exc_info.value.device_id == "lab1-leaf-1"
    assert store.devices == {"lab1-leaf-1": conflicting}
    assert store.devices["lab1-leaf-1"].vendor is VendorType.CISCO_IOS_XE


def test_register__conflict_on_last_router__earlier_routers_not_created() -> None:
    store = InMemoryStore()
    store.devices["lab1-leaf-2"] = _existing("lab1-leaf-2", vendor=VendorType.ARISTA_EOS)

    with pytest.raises(LabDeviceConflictError):
        _service(store).register(T0)

    assert set(store.devices) == {"lab1-leaf-2"}


def test_register__existing_frr_device_with_snapshot_reference__fails_closed() -> None:
    # A Device(vendor=FRR) cannot be constructed with a snapshot pointer, so
    # simulate corrupt persisted state with a stand-in row object.
    store = InMemoryStore()

    @dataclass
    class _Corrupt:
        device_id: str = "lab1-spine-1"
        vendor: VendorType = VendorType.FRR
        current_snapshot_id: str | None = "snap-1"
        baseline_snapshot_id: str | None = None

    store.devices["lab1-spine-1"] = _Corrupt()  # type: ignore[assignment]

    with pytest.raises(LabDeviceConflictError, match="snapshot"):
        _service(store).register(T0)

    assert set(store.devices) == {"lab1-spine-1"}


def test_register__naive_observed_at__rejected_before_any_unit_of_work() -> None:
    created: list[int] = []

    def factory() -> InMemoryUnitOfWork:
        created.append(1)
        return InMemoryUnitOfWork(InMemoryStore())

    with pytest.raises(ValueError, match="observed_at"):
        RegisterLabDevicesService(factory).register(datetime(2026, 10, 7, 10, 0, 0))

    assert created == []


def test_register__accepts_no_device_id_or_vendor_input() -> None:
    parameters = list(inspect.signature(RegisterLabDevicesService.register).parameters)

    assert parameters == ["self", "observed_at"]


class _RecordingUnitOfWork(InMemoryUnitOfWork):
    def __init__(self, store: InMemoryStore, log: list[str], fail_commit: bool = False) -> None:
        super().__init__(store)
        self._log = log
        self._fail_commit = fail_commit

    def commit(self) -> None:
        self._log.append("commit")
        if self._fail_commit:
            raise RuntimeError("commit boom")
        super().commit()

    def rollback(self) -> None:
        self._log.append("rollback")
        super().rollback()

    def close(self) -> None:
        self._log.append("close")
        super().close()


def test_register__success__commits_once_then_closes_without_rollback() -> None:
    log: list[str] = []
    store = InMemoryStore()
    service = RegisterLabDevicesService(lambda: _RecordingUnitOfWork(store, log))

    service.register(T0)

    assert log == ["commit", "close"]


def test_register__commit_failure__rolls_back_closes_and_preserves_original_error() -> None:
    log: list[str] = []
    store = InMemoryStore()
    service = RegisterLabDevicesService(lambda: _RecordingUnitOfWork(store, log, fail_commit=True))

    with pytest.raises(RuntimeError, match="commit boom"):
        service.register(T0)

    assert log == ["commit", "rollback", "close"]
    assert store.devices == {}


def test_register__conflict__rolls_back_and_closes_never_commits() -> None:
    log: list[str] = []
    store = InMemoryStore()
    store.devices["lab1-spine-2"] = _existing("lab1-spine-2")
    service = RegisterLabDevicesService(lambda: _RecordingUnitOfWork(store, log))

    with pytest.raises(LabDeviceConflictError):
        service.register(T0)

    assert log == ["rollback", "close"]
