"""Persisted Device domain object invariants (Day 4B1).

Lifecycle enforcement (baseline set-once, vendor immutable, created_at
immutable across an upsert) is a repository-level concern, deferred to
Day 4B2 — these tests only cover the dataclass's own construction-time
invariants. See docs/domain-model.md Section 2.
"""

from datetime import UTC, datetime

import pytest

from meta_rne.domain import Device, VendorType


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_device__valid_fields__constructs_successfully() -> None:
    device = Device(
        device_id="spine-01",
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=_utc(2026, 7, 18, 10, 0, 0),
        updated_at=_utc(2026, 7, 18, 10, 0, 0),
    )

    assert device.device_id == "spine-01"
    assert device.current_snapshot_id is None
    assert device.baseline_snapshot_id is None


def test_device__empty_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="device_id"):
        Device(
            device_id="",
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=_utc(2026, 7, 18, 10, 0, 0),
            updated_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_device__naive_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="created_at"):
        Device(
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=datetime(2026, 7, 18, 10, 0, 0),
            updated_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_device__updated_at_before_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="updated_at"):
        Device(
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=_utc(2026, 7, 18, 10, 0, 0),
            updated_at=_utc(2026, 7, 18, 9, 0, 0),
        )
