"""Incident-candidate domain objects and deterministic fingerprinting.

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. See
docs/domain-model.md Sections 10-11 and 16-17 for the approved shapes.
Persisted ``Incident`` is not implemented yet (Day 4A scope, see
CLAUDE.md "Current Phase").
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import Severity, ViolationType


class IncidentSource(StrEnum):
    POLICY_VIOLATION = "POLICY_VIOLATION"
    DRIFT = "DRIFT"
    ANOMALY = "ANOMALY"


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


@dataclass(frozen=True, slots=True)
class PolicyViolationIncidentEvidence:
    source_snapshot_id: str
    violation_type: ViolationType
    expected_acl_name: str
    actual_acl_name: str | None
    interface_name: str
    direction: AclDirection

    def __post_init__(self) -> None:
        _require_non_empty(
            self.source_snapshot_id, "PolicyViolationIncidentEvidence.source_snapshot_id"
        )


@dataclass(frozen=True, slots=True)
class IncidentCandidate:
    device_id: str
    source: IncidentSource
    rule_ref: str
    affected_resource: str
    severity: Severity
    evidence: PolicyViolationIncidentEvidence
    recommendation: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.device_id, "IncidentCandidate.device_id")
        _require_non_empty(self.rule_ref, "IncidentCandidate.rule_ref")
        _require_non_empty(self.affected_resource, "IncidentCandidate.affected_resource")
        _require_non_empty(self.recommendation, "IncidentCandidate.recommendation")
        _require_utc(self.observed_at, "IncidentCandidate.observed_at")


def compute_fingerprint(
    device_id: str,
    source: IncidentSource,
    rule_ref: str,
    affected_resource: str,
) -> str:
    _require_non_empty(device_id, "device_id")
    _require_non_empty(rule_ref, "rule_ref")
    _require_non_empty(affected_resource, "affected_resource")

    payload = json.dumps(
        [device_id, source.value, rule_ref, affected_resource],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
