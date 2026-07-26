"""Candidate source/evidence consistency validation (Gate H1B).

``validate_candidate_consistency`` (``persistence/incident_validation.py``)
is shared, unmodified-call-site, by both ``InMemoryIncidentRepository`` and
``SqlAlchemyIncidentRepository`` — this file tests the shared function
directly, once, rather than duplicating the same assertions per backend
(backend-specific behavior, e.g. actual persistence, is covered separately
by ``tests/contract/persistence/test_incident_repository_contract.py``).

Source/evidence family rules (Gate H1B, explicit allow-list — no other
source may pass): POLICY_VIOLATION requires PolicyViolationIncidentEvidence;
ANOMALY requires exactly one of RULE-CPU-HIGH/CpuHighEvidence,
RULE-LINK-FLAP/LinkFlapEvidence, RULE-BGP-DOWN/BgpDownEvidence (rule_ref and
evidence subtype must match); DRIFT remains rejected outright (drift
incidents are not implemented). No severity, recommendation,
affected_resource, or fingerprint mapping decision is exercised here — every
candidate below uses neutral, already-existing values.
"""

from datetime import UTC, datetime

import pytest

from meta_rne.domain.anomaly import BgpDownEvidence, CpuHighEvidence, LinkFlapEvidence
from meta_rne.domain.config import AclDirection
from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentSource,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.domain.telemetry import BgpState
from meta_rne.persistence.incident_validation import validate_candidate_consistency

DEVICE_ID = "spine-01"
OBSERVED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)

_POLICY_EVIDENCE = PolicyViolationIncidentEvidence(
    source_snapshot_id="snap-1",
    violation_type=ViolationType.MISSING_REQUIRED_ACL,
    expected_acl_name="ACL-EXTERNAL-IN",
    actual_acl_name=None,
    interface_name="GigabitEthernet0/1",
    direction=AclDirection.IN,
)
_CPU_HIGH_EVIDENCE = CpuHighEvidence(samples=())
_LINK_FLAP_EVIDENCE = LinkFlapEvidence(interface_name="GigabitEthernet0/1", transitions=())
_BGP_DOWN_EVIDENCE = BgpDownEvidence(
    neighbor_ip="10.0.0.1", state=BgpState.IDLE, previous_state=BgpState.ESTABLISHED
)


def _candidate(
    *,
    source: IncidentSource,
    rule_ref: str,
    affected_resource: str,
    evidence: object,
) -> IncidentCandidate:
    return IncidentCandidate(
        device_id=DEVICE_ID,
        source=source,
        rule_ref=rule_ref,
        affected_resource=affected_resource,
        severity=Severity.MEDIUM,
        evidence=evidence,  # type: ignore[arg-type]
        recommendation="test recommendation",
        observed_at=OBSERVED_AT,
    )


def _validate(candidate: IncidentCandidate) -> None:
    fingerprint = compute_fingerprint(
        candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
    )
    validate_candidate_consistency(candidate, fingerprint, OBSERVED_AT)


# --- Accepted combinations ----------------------------------------------------


def test_validate__policy_violation_with_policy_evidence__is_accepted() -> None:
    candidate = _candidate(
        source=IncidentSource.POLICY_VIOLATION,
        rule_ref="policy-acl-external-in",
        affected_resource="interface:GigabitEthernet0/1:acl_in",
        evidence=_POLICY_EVIDENCE,
    )

    _validate(candidate)  # must not raise


def test_validate__anomaly_cpu_high_rule_with_cpu_high_evidence__is_accepted() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-CPU-HIGH",
        affected_resource="device",
        evidence=_CPU_HIGH_EVIDENCE,
    )

    _validate(candidate)  # must not raise


def test_validate__anomaly_link_flap_rule_with_link_flap_evidence__is_accepted() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-LINK-FLAP",
        affected_resource="interface:GigabitEthernet0/1",
        evidence=_LINK_FLAP_EVIDENCE,
    )

    _validate(candidate)  # must not raise


def test_validate__anomaly_bgp_down_rule_with_bgp_down_evidence__is_accepted() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-BGP-DOWN",
        affected_resource="bgp-neighbor:10.0.0.1",
        evidence=_BGP_DOWN_EVIDENCE,
    )

    _validate(candidate)  # must not raise


# --- Rejected combinations -----------------------------------------------------


def test_validate__anomaly_source_with_policy_evidence__is_rejected() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-CPU-HIGH",
        affected_resource="device",
        evidence=_POLICY_EVIDENCE,
    )

    with pytest.raises(ValueError):
        _validate(candidate)


def test_validate__policy_violation_source_with_anomaly_evidence__is_rejected() -> None:
    candidate = _candidate(
        source=IncidentSource.POLICY_VIOLATION,
        rule_ref="policy-acl-external-in",
        affected_resource="interface:GigabitEthernet0/1:acl_in",
        evidence=_CPU_HIGH_EVIDENCE,
    )

    with pytest.raises(ValueError):
        _validate(candidate)


def test_validate__cpu_high_rule_with_link_flap_evidence__is_rejected() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-CPU-HIGH",
        affected_resource="device",
        evidence=_LINK_FLAP_EVIDENCE,
    )

    with pytest.raises(ValueError):
        _validate(candidate)


def test_validate__link_flap_rule_with_bgp_down_evidence__is_rejected() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-LINK-FLAP",
        affected_resource="interface:GigabitEthernet0/1",
        evidence=_BGP_DOWN_EVIDENCE,
    )

    with pytest.raises(ValueError):
        _validate(candidate)


def test_validate__bgp_down_rule_with_cpu_high_evidence__is_rejected() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-BGP-DOWN",
        affected_resource="bgp-neighbor:10.0.0.1",
        evidence=_CPU_HIGH_EVIDENCE,
    )

    with pytest.raises(ValueError):
        _validate(candidate)


def test_validate__unknown_anomaly_rule_ref__is_rejected() -> None:
    candidate = _candidate(
        source=IncidentSource.ANOMALY,
        rule_ref="RULE-DOES-NOT-EXIST",
        affected_resource="device",
        evidence=_CPU_HIGH_EVIDENCE,
    )

    with pytest.raises(ValueError):
        _validate(candidate)


def test_validate__drift_source__is_rejected() -> None:
    candidate = _candidate(
        source=IncidentSource.DRIFT,
        rule_ref="acls.removed",
        affected_resource="acls.removed:ACL-EXTERNAL-IN",
        evidence=_POLICY_EVIDENCE,
    )

    with pytest.raises(ValueError):
        _validate(candidate)
