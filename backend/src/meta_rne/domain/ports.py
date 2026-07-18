"""Vendor adapter port (architecture.md Section 5).

A structural (``typing.Protocol``) interface, not an ABC — any object
with a matching ``vendor_id`` attribute and ``parse`` method satisfies it,
without inheriting from anything in this module. This is also what lets
registry contract tests assert "satisfies VendorConfigAdapter" without
asserting a concrete class.
"""

from typing import Protocol, runtime_checkable

from meta_rne.domain.config import NormalizedConfiguration
from meta_rne.domain.errors import ParseError


@runtime_checkable
class VendorConfigAdapter(Protocol):
    vendor_id: str

    def parse(self, raw_text: str) -> NormalizedConfiguration | ParseError: ...
