"""FRR device persistence conformance (NPE-1C3B1), run against both the
in-memory and SQLAlchemy repositories via the shared ``repositories``
fixture."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.errors import DeviceConflictError

T0 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 7, 11, 0, 0, tzinfo=UTC)


def _frr(device_id: str = "lab1-leaf-1", updated_at: datetime = T0) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.FRR,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=updated_at,
    )


def test_device_repository__frr_snapshot_less_device__round_trips(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_frr())

    stored = repositories.devices.get_by_id("lab1-leaf-1")

    assert stored == _frr()
    assert stored.vendor is VendorType.FRR
    assert stored.current_snapshot_id is None
    assert stored.baseline_snapshot_id is None


def test_device_repository__frr_device__appears_in_list_all(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_frr("lab1-spine-1"))
    repositories.devices.save(_frr("lab1-leaf-1"))

    assert {device.device_id for device in repositories.devices.list_all()} == {
        "lab1-spine-1",
        "lab1-leaf-1",
    }


def test_device_repository__frr_device__vendor_cannot_change(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_frr())
    changed = Device(
        device_id="lab1-leaf-1",
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T1,
    )

    with pytest.raises(DeviceConflictError):
        repositories.devices.save(changed)

    assert repositories.devices.get_by_id("lab1-leaf-1") == _frr()
