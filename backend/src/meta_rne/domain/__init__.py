"""Canonical models and repository/adapter interfaces (ports).

Pure data + logic; must not import a web framework or a database driver
(NFR-02, domain-model.md). Persistence-facing entities (Device,
ConfigurationSnapshot, ...) are not implemented yet — prohibited until a
later day (see CLAUDE.md "Current Phase").
"""

from meta_rne.domain.config import (
    AclAction,
    AclDirection,
    AdminState,
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
    VendorType,
)
from meta_rne.domain.errors import ParseError, ParseErrorCode, UnsupportedVendorError
from meta_rne.domain.ports import VendorConfigAdapter

__all__ = [
    "AclAction",
    "AclDirection",
    "AdminState",
    "NormalizedAcl",
    "NormalizedAclEntry",
    "NormalizedBgpNeighbor",
    "NormalizedConfiguration",
    "NormalizedInterface",
    "NormalizedRouting",
    "ParseError",
    "ParseErrorCode",
    "UnsupportedVendorError",
    "VendorConfigAdapter",
    "VendorType",
]
