"""PostgreSQL-only proof that the Session remains usable after a
translated duplicate-snapshot conflict (Day 4B2 binding decision).

The SAVEPOINT (``session.begin_nested()``) used inside
``SqlAlchemyConfigurationSnapshotRepository.add`` must only unwind that one
failed INSERT — never the caller's outer transaction — so a subsequent,
unrelated repository call on the exact same Session must still succeed.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from meta_rne.domain.config import NormalizedConfiguration, NormalizedRouting, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash
from meta_rne.persistence.errors import SnapshotAlreadyExistsError
from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
from meta_rne.persistence.sqlalchemy.snapshot_repository import (
    SqlAlchemyConfigurationSnapshotRepository,
)

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _empty_config() -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname="spine-01", interfaces=(), routing=NormalizedRouting(bgp_neighbors=()), acls=()
    )


def _snapshot(snapshot_id: str) -> ConfigurationSnapshot:
    raw_text = f"hostname spine-01\n! {snapshot_id}\n"
    return ConfigurationSnapshot(
        snapshot_id=snapshot_id,
        device_id="spine-01",
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=raw_text,
        raw_text_hash=compute_raw_text_hash(raw_text),
        normalized_config=_empty_config(),
        submitted_at=T0,
    )


def test_snapshot_repository_sqlalchemy__session_remains_usable_after_translated_conflict(
    sqlalchemy_session: Session,
) -> None:
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    snapshots = SqlAlchemyConfigurationSnapshotRepository(sqlalchemy_session)
    devices.save(
        Device(
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    snapshots.add(_snapshot("snap-1"))

    with pytest.raises(SnapshotAlreadyExistsError):
        snapshots.add(_snapshot("snap-1"))

    # The Session must still be fully usable after the translated conflict —
    # only the SAVEPOINT unwound, not the outer transaction.
    snapshots.add(_snapshot("snap-2"))
    assert snapshots.get_by_id("snap-2") is not None
    assert snapshots.get_by_id("snap-1") is not None
