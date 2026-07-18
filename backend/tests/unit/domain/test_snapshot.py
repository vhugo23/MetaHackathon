"""ConfigurationSnapshot and compute_raw_text_hash invariants (Day 4B1).

Immutability enforcement at insertion time (``SnapshotAlreadyExistsError``)
is a repository-level concern, deferred to Day 4B2 — these tests only cover
the dataclass's own construction-time invariants. See docs/domain-model.md
Section 4.
"""

from datetime import UTC, datetime

import pytest

from meta_rne.domain import (
    ConfigurationSnapshot,
    NormalizedConfiguration,
    NormalizedRouting,
    VendorType,
    compute_raw_text_hash,
)

_RAW_TEXT = "hostname spine-01\ninterface GigabitEthernet0/1\n"


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _empty_config() -> NormalizedConfiguration:
    return NormalizedConfiguration(
        hostname="spine-01",
        interfaces=(),
        routing=NormalizedRouting(bgp_neighbors=()),
        acls=(),
    )


def test_compute_raw_text_hash__is_lowercase_64_char_sha256_hex_digest() -> None:
    digest = compute_raw_text_hash(_RAW_TEXT)

    assert len(digest) == 64
    assert digest == digest.lower()
    assert all(c in "0123456789abcdef" for c in digest)

    import hashlib

    assert digest == hashlib.sha256(_RAW_TEXT.encode("utf-8")).hexdigest()


def test_compute_raw_text_hash__same_text_twice__returns_identical_digest() -> None:
    assert compute_raw_text_hash(_RAW_TEXT) == compute_raw_text_hash(_RAW_TEXT)


def test_configuration_snapshot__hash_matches_raw_text__constructs_successfully() -> None:
    snapshot = ConfigurationSnapshot(
        snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        device_id="spine-01",
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=_RAW_TEXT,
        raw_text_hash=compute_raw_text_hash(_RAW_TEXT),
        normalized_config=_empty_config(),
        submitted_at=_utc(2026, 7, 18, 10, 0, 0),
    )

    assert snapshot.raw_text_hash == compute_raw_text_hash(_RAW_TEXT)


def test_configuration_snapshot__hash_does_not_match_raw_text__raises_value_error() -> None:
    with pytest.raises(ValueError, match="raw_text_hash"):
        ConfigurationSnapshot(
            snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            raw_config_text=_RAW_TEXT,
            raw_text_hash=compute_raw_text_hash("different text"),
            normalized_config=_empty_config(),
            submitted_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_configuration_snapshot__uppercase_hash__raises_value_error() -> None:
    with pytest.raises(ValueError, match="raw_text_hash"):
        ConfigurationSnapshot(
            snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            raw_config_text=_RAW_TEXT,
            raw_text_hash=compute_raw_text_hash(_RAW_TEXT).upper(),
            normalized_config=_empty_config(),
            submitted_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_configuration_snapshot__non_hex_hash__raises_value_error() -> None:
    with pytest.raises(ValueError, match="raw_text_hash"):
        ConfigurationSnapshot(
            snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            raw_config_text=_RAW_TEXT,
            raw_text_hash="g" * 64,
            normalized_config=_empty_config(),
            submitted_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_configuration_snapshot__empty_raw_config_text__raises_value_error() -> None:
    with pytest.raises(ValueError, match="raw_config_text"):
        ConfigurationSnapshot(
            snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            raw_config_text="",
            raw_text_hash=compute_raw_text_hash(""),
            normalized_config=_empty_config(),
            submitted_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_configuration_snapshot__empty_snapshot_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="snapshot_id"):
        ConfigurationSnapshot(
            snapshot_id="",
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            raw_config_text=_RAW_TEXT,
            raw_text_hash=compute_raw_text_hash(_RAW_TEXT),
            normalized_config=_empty_config(),
            submitted_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_configuration_snapshot__empty_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="device_id"):
        ConfigurationSnapshot(
            snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
            device_id="",
            vendor=VendorType.CISCO_IOS_XE,
            raw_config_text=_RAW_TEXT,
            raw_text_hash=compute_raw_text_hash(_RAW_TEXT),
            normalized_config=_empty_config(),
            submitted_at=_utc(2026, 7, 18, 10, 0, 0),
        )


def test_configuration_snapshot__naive_submitted_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="submitted_at"):
        ConfigurationSnapshot(
            snapshot_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
            device_id="spine-01",
            vendor=VendorType.CISCO_IOS_XE,
            raw_config_text=_RAW_TEXT,
            raw_text_hash=compute_raw_text_hash(_RAW_TEXT),
            normalized_config=_empty_config(),
            submitted_at=datetime(2026, 7, 18, 10, 0, 0),
        )
