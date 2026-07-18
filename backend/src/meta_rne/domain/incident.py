"""Incident-candidate and persisted Incident domain objects, plus
deterministic fingerprinting.

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. See
docs/domain-model.md Sections 10-11 and 16-17 for the approved shapes.
The atomic ``upsert_open_incident`` write path and its concrete
repositories are not implemented yet (Day 4B1 scope — see CLAUDE.md
"Current Phase"); this module only defines the persisted ``Incident``
dataclass and the ``IncidentUpsertOutcome``/``IncidentUpsertResult`` values
that repository will return.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import Severity, ViolationType

_FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")


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


@dataclass(frozen=True, slots=True)
class Incident:
    incident_id: str
    fingerprint: str
    device_id: str
    source: IncidentSource
    rule_ref: str
    affected_resource: str
    severity: Severity
    status: IncidentStatus
    evidence: PolicyViolationIncidentEvidence
    recommendation: str
    created_at: datetime
    last_seen_at: datetime
    occurrence_count: int

    def __post_init__(self) -> None:
        _require_non_empty(self.incident_id, "Incident.incident_id")
        _require_non_empty(self.device_id, "Incident.device_id")
        _require_non_empty(self.rule_ref, "Incident.rule_ref")
        _require_non_empty(self.affected_resource, "Incident.affected_resource")
        _require_non_empty(self.recommendation, "Incident.recommendation")
        _require_utc(self.created_at, "Incident.created_at")
        _require_utc(self.last_seen_at, "Incident.last_seen_at")

        if not _FINGERPRINT_PATTERN.fullmatch(self.fingerprint):
            raise ValueError(
                "Incident.fingerprint must be a lowercase 64-character hexadecimal "
                "SHA-256 digest"
            )
        if self.occurrence_count < 1:
            raise ValueError("Incident.occurrence_count must be >= 1")
        if self.last_seen_at < self.created_at:
            raise ValueError("Incident.last_seen_at must not precede Incident.created_at")


class IncidentUpsertOutcome(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"


@dataclass(frozen=True, slots=True)
class IncidentUpsertResult:
    incident: Incident
    outcome: IncidentUpsertOutcome
