"""Configuration policy domain objects and PolicyEvaluator's output shape.

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. Everything here
is an immutable ``@dataclass(frozen=True, slots=True)`` using ``tuple``
for collections, per the Day 3B engineering constraints. See
docs/domain-model.md Sections 6-7 and 16 for the approved shapes.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from meta_rne.domain.config import AclDirection


class Severity(StrEnum):
    CRITICAL = "Critical"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class ViolationType(StrEnum):
    MISSING_REQUIRED_ACL = "MISSING_REQUIRED_ACL"
    TARGET_INTERFACE_MISSING = "TARGET_INTERFACE_MISSING"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class AclAssignmentEvidence:
    expected_acl_name: str
    actual_acl_name: str | None
    interface_name: str
    direction: AclDirection


@dataclass(frozen=True, slots=True)
class ConfigurationViolation:
    device_id: str
    source_snapshot_id: str
    rule_ref: str
    violation_type: ViolationType
    affected_resource: str
    severity: Severity
    evidence: AclAssignmentEvidence
    recommendation: str
    detected_at: datetime


@dataclass(frozen=True, slots=True)
class RequiredAclRule:
    acl_name: str
    interface_name: str
    direction: AclDirection
    severity: Severity
    recommendation: str

    def __post_init__(self) -> None:
        _require_non_empty(self.acl_name, "RequiredAclRule.acl_name")
        _require_non_empty(self.interface_name, "RequiredAclRule.interface_name")
        _require_non_empty(self.recommendation, "RequiredAclRule.recommendation")


@dataclass(frozen=True, slots=True)
class ConfigurationPolicy:
    policy_id: str
    applies_to: str
    required_acls: tuple[RequiredAclRule, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.policy_id, "ConfigurationPolicy.policy_id")
        _require_non_empty(self.applies_to, "ConfigurationPolicy.applies_to")
        _require_utc(self.created_at, "ConfigurationPolicy.created_at")
