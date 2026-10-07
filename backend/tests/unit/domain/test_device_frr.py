"""FRR device identity (NPE-1C3B1): VendorType.FRR, snapshot-less Device, and
the ConfigurationSnapshot guard."""

from datetime import UTC, datetime

import pytest

from meta_rne.domain import (
    ConfigurationSnapshot,
    Device,
    NormalizedConfiguration,
    NormalizedRouting,
    VendorType,
    compute_raw_text_hash,
)

_T = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)


def _frr_device(**overrides: object) -> Device:
    fields: dict[str, object] = {
        "device_id": "lab1-leaf-1",
        "vendor": VendorType.FRR,
        "current_snapshot_id": None,
        "baseline_snapshot_id": None,
        "created_at": _T,
        "updated_at": _T,
    }
    fields.update(overrides)
    return Device(**fields)  # type: ignore[arg-type]


def test_vendor_type__existing_values_unchanged_and_frr_added() -> None:
    assert VendorType.CISCO_IOS_XE.value == "cisco-ios-xe"
    assert VendorType.ARISTA_EOS.value == "arista-eos"
    assert VendorType.FRR.value == "frr"
    assert [member.value for member in VendorType] == ["cisco-ios-xe", "arista-eos", "frr"]


def test_device__frr_without_snapshots__constructs_successfully() -> None:
    device = _frr_device()

    assert device.vendor is VendorType.FRR
    assert device.current_snapshot_id is None
    assert device.baseline_snapshot_id is None


@pytest.mark.parametrize("field", ["current_snapshot_id", "baseline_snapshot_id"])
def test_device__frr_with_snapshot_reference__raises_value_error(field: str) -> None:
    with pytest.raises(ValueError, match="frr"):
        _frr_device(**{field: "snap-1"})


def test_device__cisco_without_snapshots__still_constructs() -> None:
    """Cisco/Arista device construction is unchanged by the FRR guard."""
    device = _frr_device(vendor=VendorType.CISCO_IOS_XE)

    assert device.vendor is VendorType.CISCO_IOS_XE


def test_configuration_snapshot__frr_vendor__raises_value_error() -> None:
    raw = "hostname lab1-leaf-1\n"
    with pytest.raises(ValueError, match="frr"):
        ConfigurationSnapshot(
            snapshot_id="snap-1",
            device_id="lab1-leaf-1",
            vendor=VendorType.FRR,
            raw_config_text=raw,
            raw_text_hash=compute_raw_text_hash(raw),
            normalized_config=NormalizedConfiguration(
                hostname="lab1-leaf-1",
                interfaces=(),
                routing=NormalizedRouting(bgp_neighbors=()),
                acls=(),
            ),
            submitted_at=_T,
        )
