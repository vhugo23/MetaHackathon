"""Repository conformance tests for DeviceRepository (Day 4B2).

Run against both the in-memory and SQLAlchemy implementations via the
shared ``repositories`` fixture (conftest.py in this directory). Every
rejected lifecycle transition must raise ``DeviceConflictError`` and leave
the stored ``Device`` completely unchanged — no silent preservation, no
partial mutation (Day 4B2 binding decision, CLAUDE.md "Current Phase").
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from meta_rne.domain.config import NormalizedConfiguration, NormalizedRouting, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash
from meta_rne.persistence.errors import DeviceConflictError

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)


def _device(**overrides: object) -> Device:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "vendor": VendorType.CISCO_IOS_XE,
        "current_snapshot_id": None,
        "baseline_snapshot_id": None,
        "created_at": T0,
        "updated_at": T0,
    }
    defaults.update(overrides)
    return Device(**defaults)  # type: ignore[arg-type]


def _snapshot(snapshot_id: str, device_id: str = DEVICE_ID) -> ConfigurationSnapshot:
    raw_text = f"hostname {device_id}\n! {snapshot_id}\n"
    return ConfigurationSnapshot(
        snapshot_id=snapshot_id,
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=raw_text,
        raw_text_hash=compute_raw_text_hash(raw_text),
        normalized_config=NormalizedConfiguration(
            hostname=device_id,
            interfaces=(),
            routing=NormalizedRouting(bgp_neighbors=()),
            acls=(),
        ),
        submitted_at=T0,
    )


def test_device_repository__missing_device__returns_none(repositories: SimpleNamespace) -> None:
    assert repositories.devices.get_by_id("does-not-exist") is None


def test_device_repository__save_new_device_then_get_by_id__returns_equal_device(
    repositories: SimpleNamespace,
) -> None:
    device = _device()

    repositories.devices.save(device)

    assert repositories.devices.get_by_id(DEVICE_ID) == device


def test_device_repository__get_by_id__returns_device_not_orm_model(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())

    fetched = repositories.devices.get_by_id(DEVICE_ID)

    assert isinstance(fetched, Device)


def test_device_repository__resave_equal_device__is_allowed(
    repositories: SimpleNamespace,
) -> None:
    device = _device()
    repositories.devices.save(device)

    repositories.devices.save(device)

    assert repositories.devices.get_by_id(DEVICE_ID) == device


def test_device_repository__vendor_change__raises_device_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())

    with pytest.raises(DeviceConflictError):
        repositories.devices.save(_device(vendor=VendorType.ARISTA_EOS))

    assert repositories.devices.get_by_id(DEVICE_ID) == _device()


def test_device_repository__created_at_change__raises_device_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())

    with pytest.raises(DeviceConflictError):
        repositories.devices.save(_device(created_at=T1, updated_at=T1))

    assert repositories.devices.get_by_id(DEVICE_ID) == _device()


def test_device_repository__updated_at_regression__raises_device_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device(updated_at=T1))

    with pytest.raises(DeviceConflictError):
        repositories.devices.save(_device(updated_at=T0))

    assert repositories.devices.get_by_id(DEVICE_ID) == _device(updated_at=T1)


def test_device_repository__updated_at_equal_or_advancing__is_allowed(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device(updated_at=T0))
    repositories.devices.save(_device(updated_at=T0))  # equal: allowed

    repositories.devices.save(_device(updated_at=T1))  # advancing: allowed

    assert repositories.devices.get_by_id(DEVICE_ID) == _device(updated_at=T1)


def test_device_repository__baseline_null_to_existing_snapshot__is_allowed(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    repositories.snapshots.add(_snapshot("snap-1"))

    repositories.devices.save(
        _device(current_snapshot_id="snap-1", baseline_snapshot_id="snap-1", updated_at=T1)
    )

    fetched = repositories.devices.get_by_id(DEVICE_ID)
    assert fetched is not None
    assert fetched.baseline_snapshot_id == "snap-1"


def test_device_repository__baseline_replacement__raises_device_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    repositories.snapshots.add(_snapshot("snap-1"))
    repositories.snapshots.add(_snapshot("snap-2"))
    with_baseline = _device(
        current_snapshot_id="snap-1", baseline_snapshot_id="snap-1", updated_at=T1
    )
    repositories.devices.save(with_baseline)

    with pytest.raises(DeviceConflictError):
        repositories.devices.save(
            _device(current_snapshot_id="snap-2", baseline_snapshot_id="snap-2", updated_at=T2)
        )

    assert repositories.devices.get_by_id(DEVICE_ID) == with_baseline


def test_device_repository__same_baseline_supplied_again__is_allowed(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    repositories.snapshots.add(_snapshot("snap-1"))
    repositories.snapshots.add(_snapshot("snap-2"))
    repositories.devices.save(
        _device(current_snapshot_id="snap-1", baseline_snapshot_id="snap-1", updated_at=T1)
    )

    repositories.devices.save(
        _device(current_snapshot_id="snap-2", baseline_snapshot_id="snap-1", updated_at=T2)
    )

    fetched = repositories.devices.get_by_id(DEVICE_ID)
    assert fetched is not None
    assert fetched.baseline_snapshot_id == "snap-1"
    assert fetched.current_snapshot_id == "snap-2"


def test_device_repository__current_snapshot_update_to_another_existing_snapshot__is_allowed(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    repositories.snapshots.add(_snapshot("snap-1"))
    repositories.snapshots.add(_snapshot("snap-2"))
    repositories.devices.save(_device(current_snapshot_id="snap-1", updated_at=T1))

    repositories.devices.save(_device(current_snapshot_id="snap-2", updated_at=T2))

    fetched = repositories.devices.get_by_id(DEVICE_ID)
    assert fetched is not None
    assert fetched.current_snapshot_id == "snap-2"


def test_device_repository__current_snapshot_cleared_to_none__raises_device_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    repositories.snapshots.add(_snapshot("snap-1"))
    with_current = _device(current_snapshot_id="snap-1", updated_at=T1)
    repositories.devices.save(with_current)

    with pytest.raises(DeviceConflictError):
        repositories.devices.save(_device(current_snapshot_id=None, updated_at=T2))

    assert repositories.devices.get_by_id(DEVICE_ID) == with_current


def test_device_repository__nonexistent_current_snapshot_reference__raises_device_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    with pytest.raises(DeviceConflictError):
        repositories.devices.save(_device(current_snapshot_id="does-not-exist"))

    assert repositories.devices.get_by_id(DEVICE_ID) is None


def test_device_repository__nonexistent_baseline_snapshot_reference__raises_device_conflict_error(
    repositories: SimpleNamespace,
) -> None:
    with pytest.raises(DeviceConflictError):
        repositories.devices.save(_device(baseline_snapshot_id="does-not-exist"))

    assert repositories.devices.get_by_id(DEVICE_ID) is None
