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
from meta_rne.domain.device import Device
from meta_rne.domain.errors import ParseError, ParseErrorCode, UnsupportedVendorError
from meta_rne.domain.incident import (
    Incident,
    IncidentCandidate,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import (
    AclAssignmentEvidence,
    ConfigurationPolicy,
    ConfigurationViolation,
    RequiredAclRule,
    Severity,
    ViolationType,
)
from meta_rne.domain.ports import VendorConfigAdapter
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash

__all__ = [
    "AclAction",
    "AclAssignmentEvidence",
    "AclDirection",
    "AdminState",
    "ConfigurationPolicy",
    "ConfigurationSnapshot",
    "ConfigurationViolation",
    "Device",
    "Incident",
    "IncidentCandidate",
    "IncidentSource",
    "IncidentStatus",
    "IncidentUpsertOutcome",
    "IncidentUpsertResult",
    "NormalizedAcl",
    "NormalizedAclEntry",
    "NormalizedBgpNeighbor",
    "NormalizedConfiguration",
    "NormalizedInterface",
    "NormalizedRouting",
    "ParseError",
    "ParseErrorCode",
    "PolicyViolationIncidentEvidence",
    "RequiredAclRule",
    "Severity",
    "UnsupportedVendorError",
    "VendorConfigAdapter",
    "VendorType",
    "ViolationType",
    "compute_fingerprint",
    "compute_raw_text_hash",
]
