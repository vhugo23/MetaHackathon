"""Repository conformance tests for ConfigurationSnapshotRepository (Day 4B2).

Run against both the in-memory and SQLAlchemy implementations via the
shared ``repositories`` fixture (conftest.py in this directory). See
docs/domain-model.md Section 4 and CLAUDE.md "Current Phase" for the
approved contract.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from meta_rne.domain.config import (
    AclAction,
    AdminState,
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
    VendorType,
)
from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash
from meta_rne.persistence.errors import ReferencedDeviceNotFoundError, SnapshotAlreadyExistsError

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _device() -> Device:
    return Device(
        device_id=DEVICE_ID,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


def _config(hostname: str = "spine-01") -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname=hostname,
        interfaces=(
            NormalizedInterface(
                name="GigabitEthernet0/1",
                description='weird "quoted" desc \\ with backslash : colon',
                ip_address="10.0.0.1/30",
                mtu=1500,
                admin_state=AdminState.UP,
                acl_in="ACL-EXTERNAL-IN",
                acl_out=None,
            ),
        ),
        routing=NormalizedRouting(
            bgp_neighbors=(NormalizedBgpNeighbor(neighbor_ip="10.0.0.2", remote_as=65001),)
        ),
        acls=(
            NormalizedAcl(
                name="ACL-EXTERNAL-IN",
                entries=(
                    NormalizedAclEntry(
                        sequence=10,
                        action=AclAction.PERMIT,
                        protocol="tcp",
                        source="any",
                        destination="any",
                    ),
                ),
            ),
        ),
    )


def _snapshot(
    snapshot_id: str = "snap-1",
    device_id: str = DEVICE_ID,
    raw_config_text: str | None = None,
) -> ConfigurationSnapshot:
    text = raw_config_text if raw_config_text is not None else f"hostname {device_id}\n"
    return ConfigurationSnapshot(
        snapshot_id=snapshot_id,
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=text,
        raw_text_hash=compute_raw_text_hash(text),
        normalized_config=_config(),
        submitted_at=T0,
    )


def test_snapshot_repository__missing_snapshot__returns_none(
    repositories: SimpleNamespace,
) -> None:
    assert repositories.snapshots.get_by_id("does-not-exist") is None


def test_snapshot_repository__add_then_get_by_id__returns_equal_snapshot(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    snapshot = _snapshot()

    repositories.snapshots.add(snapshot)

    assert repositories.snapshots.get_by_id("snap-1") == snapshot


def test_snapshot_repository__get_by_id__returns_snapshot_not_orm_model(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    repositories.snapshots.add(_snapshot())

    fetched = repositories.snapshots.get_by_id("snap-1")

    assert isinstance(fetched, ConfigurationSnapshot)


def test_snapshot_repository__get_by_id__normalized_config_round_trips_without_reparsing(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    snapshot = _snapshot()
    repositories.snapshots.add(snapshot)

    fetched = repositories.snapshots.get_by_id("snap-1")

    assert fetched is not None
    assert fetched.normalized_config == snapshot.normalized_config


def test_snapshot_repository__get_by_id__raw_config_text_preserved_exactly(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    raw_text = "hostname spine-01\ninterface GigabitEthernet0/1\n"
    snapshot = _snapshot(raw_config_text=raw_text)
    repositories.snapshots.add(snapshot)

    fetched = repositories.snapshots.get_by_id("snap-1")

    assert fetched is not None
    assert fetched.raw_config_text == raw_text


def test_snapshot_repository__get_by_id__raw_text_hash_preserved_exactly(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    snapshot = _snapshot()
    repositories.snapshots.add(snapshot)

    fetched = repositories.snapshots.get_by_id("snap-1")

    assert fetched is not None
    assert fetched.raw_text_hash == snapshot.raw_text_hash


def test_snapshot_repository__duplicate_snapshot_id__raises_snapshot_already_exists_error(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    repositories.snapshots.add(_snapshot(raw_config_text="original text\n"))

    with pytest.raises(SnapshotAlreadyExistsError):
        repositories.snapshots.add(_snapshot(raw_config_text="different text\n"))


def test_snapshot_repository__identical_duplicate_snapshot__also_raises(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    snapshot = _snapshot()
    repositories.snapshots.add(snapshot)

    with pytest.raises(SnapshotAlreadyExistsError):
        repositories.snapshots.add(snapshot)


def test_snapshot_repository__duplicate_add__does_not_overwrite_existing_data(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    original = _snapshot(raw_config_text="original text\n")
    repositories.snapshots.add(original)

    try:
        repositories.snapshots.add(_snapshot(raw_config_text="different text\n"))
    except SnapshotAlreadyExistsError:
        pass

    assert repositories.snapshots.get_by_id("snap-1") == original


def test_snapshot_repository__unknown_device__raises_referenced_device_not_found_error(
    repositories: SimpleNamespace,
) -> None:
    with pytest.raises(ReferencedDeviceNotFoundError):
        repositories.snapshots.add(_snapshot(device_id="does-not-exist"))


def test_snapshot_repository__unknown_device__is_not_mislabeled_as_duplicate(
    repositories: SimpleNamespace,
) -> None:
    with pytest.raises(ReferencedDeviceNotFoundError) as exc_info:
        repositories.snapshots.add(_snapshot(device_id="does-not-exist"))

    assert not isinstance(exc_info.value, SnapshotAlreadyExistsError)


def test_snapshot_repository__unicode_quotes_backslashes_and_colons__survive_persistence(
    repositories: SimpleNamespace,
) -> None:
    repositories.devices.save(_device())
    raw_text = 'hostname spine-01\n! Gi0/1-é "weird" \\backslash\\ a:b\n'
    snapshot = ConfigurationSnapshot(
        snapshot_id="snap-unicode",
        device_id=DEVICE_ID,
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=raw_text,
        raw_text_hash=compute_raw_text_hash(raw_text),
        normalized_config=_config(hostname="spine-01-é"),
        submitted_at=T0,
    )

    repositories.snapshots.add(snapshot)
    fetched = repositories.snapshots.get_by_id("snap-unicode")

    assert fetched is not None
    assert fetched.raw_config_text == raw_text
    assert fetched.normalized_config == snapshot.normalized_config
