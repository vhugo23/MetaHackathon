import pytest

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.domain.config import NormalizedConfiguration, VendorType
from meta_rne.domain.errors import UnsupportedVendorError
from meta_rne.domain.ports import VendorConfigAdapter


def test_adapter_registry__resolve_cisco__satisfies_vendor_config_adapter_contract() -> None:
    registry = AdapterRegistry([CiscoAdapter()])

    adapter = registry.resolve("cisco-ios-xe")

    # Structural (Protocol) check only — never assert the concrete class.
    assert isinstance(adapter, VendorConfigAdapter)
    assert adapter.vendor_id == "cisco-ios-xe"
    result = adapter.parse("hostname spine-01\n")
    assert isinstance(result, NormalizedConfiguration)
    assert result.hostname == "spine-01"


def test_adapter_registry__resolve_unknown_vendor__raises_unsupported_vendor_error() -> None:
    registry = AdapterRegistry([CiscoAdapter()])

    with pytest.raises(UnsupportedVendorError):
        registry.resolve("juniper-junos")


def test_adapter_registry__resolve_arista__raises_unsupported_vendor_error() -> None:
    """VendorType.ARISTA_EOS is a recognized future vendor value, but no
    adapter is registered for it in Day 3A — the registry rejects it the
    same way it rejects a wholly unknown string. Callers are never
    required to construct VendorType before calling resolve()."""
    registry = AdapterRegistry([CiscoAdapter()])

    with pytest.raises(UnsupportedVendorError):
        registry.resolve(VendorType.ARISTA_EOS.value)
