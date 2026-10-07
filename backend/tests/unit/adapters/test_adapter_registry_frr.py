"""FRR is a device identity only (NPE-1C3B1): configuration ingestion for it
must remain unsupported because no FRR config adapter is registered."""

import pytest

from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.dependencies import build_production_adapter_registry
from meta_rne.domain.config import VendorType
from meta_rne.domain.errors import UnsupportedVendorError


def test_production_adapter_registry__frr__raises_unsupported_vendor_error() -> None:
    registry = build_production_adapter_registry()

    with pytest.raises(UnsupportedVendorError):
        registry.resolve(VendorType.FRR.value)


def test_production_adapter_registry__cisco_and_arista__still_resolve() -> None:
    registry = build_production_adapter_registry()

    assert registry.resolve("cisco-ios-xe").vendor_id == "cisco-ios-xe"
    assert registry.resolve("arista-eos").vendor_id == "arista-eos"


def test_empty_adapter_registry__frr__raises_unsupported_vendor_error() -> None:
    with pytest.raises(UnsupportedVendorError):
        AdapterRegistry([]).resolve("frr")
