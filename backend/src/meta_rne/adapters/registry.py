"""AdapterRegistry (architecture.md Section 5).

Resolves a plain vendor string to a registered adapter. Callers never
need to construct a `VendorType` first — the registry accepts whatever
string the caller (eventually, an HTTP request body) provides and rejects
anything it doesn't have an adapter for, whether that string is entirely
unknown (e.g. "juniper-junos") or a vendor named in VendorType but not
yet wired with an adapter (e.g. "arista-eos", until a later day adds
one).
"""

from collections.abc import Iterable

from meta_rne.domain.errors import UnsupportedVendorError
from meta_rne.domain.ports import VendorConfigAdapter


class AdapterRegistry:
    def __init__(self, adapters: Iterable[VendorConfigAdapter]) -> None:
        self._adapters: dict[str, VendorConfigAdapter] = {
            adapter.vendor_id: adapter for adapter in adapters
        }

    def resolve(self, vendor: str) -> VendorConfigAdapter:
        try:
            return self._adapters[vendor]
        except KeyError:
            raise UnsupportedVendorError(vendor) from None
