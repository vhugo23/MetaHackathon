"""Domain-level error contract for configuration ingestion.

Two distinct shapes, both deliberate (architecture.md Section 5):

- ``ParseError`` is a returned *value*, never raised — it is the second
  member of a vendor adapter's ``parse() -> NormalizedConfiguration |
  ParseError`` union.
- ``UnsupportedVendorError`` is a raised *exception* from
  ``AdapterRegistry.resolve`` — resolving an unregistered vendor is not a
  per-parse business outcome to inspect field-by-field, it is an invalid
  call.
"""

from dataclasses import dataclass
from enum import StrEnum


class ParseErrorCode(StrEnum):
    EMPTY_CONFIGURATION = "EMPTY_CONFIGURATION"
    MISSING_HOSTNAME = "MISSING_HOSTNAME"
    MALFORMED_HOSTNAME = "MALFORMED_HOSTNAME"
    MALFORMED_INTERFACE = "MALFORMED_INTERFACE"
    INVALID_INTERFACE_IP = "INVALID_INTERFACE_IP"
    INVALID_SUBNET_MASK = "INVALID_SUBNET_MASK"
    INVALID_ACL_DIRECTION = "INVALID_ACL_DIRECTION"
    UNDECLARED_ACL_REFERENCE = "UNDECLARED_ACL_REFERENCE"
    INVALID_BGP_NEIGHBOR_IP = "INVALID_BGP_NEIGHBOR_IP"
    INVALID_BGP_REMOTE_AS = "INVALID_BGP_REMOTE_AS"


@dataclass(frozen=True, slots=True)
class ParseError:
    code: ParseErrorCode
    message: str
    line_number: int | None = None
    line: str | None = None


class UnsupportedVendorError(Exception):
    """Raised by AdapterRegistry.resolve for any vendor string with no
    registered adapter — including syntactically unknown strings (e.g.
    "juniper-junos") and vendors named in VendorType but not yet wired
    with an adapter (e.g. "arista-eos")."""

    def __init__(self, vendor: str) -> None:
        super().__init__(f"unsupported vendor: {vendor!r}")
        self.vendor = vendor
