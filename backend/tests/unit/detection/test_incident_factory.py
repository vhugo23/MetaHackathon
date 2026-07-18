from datetime import UTC, datetime

from meta_rne.detection.incident_factory import IncidentFactory
from meta_rne.domain.config import AclDirection
from meta_rne.domain.incident import IncidentSource
from meta_rne.domain.policy import (
    AclAssignmentEvidence,
    ConfigurationViolation,
    Severity,
    ViolationType,
)

DEVICE_ID = "spine-01"
SNAPSHOT_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
DETECTED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _violation(**overrides: object) -> ConfigurationViolation:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "source_snapshot_id": SNAPSHOT_ID,
        "rule_ref": "policy-acl-external-in",
        "violation_type": ViolationType.MISSING_REQUIRED_ACL,
        "affected_resource": "interface:GigabitEthernet0/1:acl_in",
        "severity": Severity.MEDIUM,
        "evidence": AclAssignmentEvidence(
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name=None,
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
        ),
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        "detected_at": DETECTED_AT,
    }
    defaults.update(overrides)
    return ConfigurationViolation(**defaults)  # type: ignore[arg-type]


def test_incident_factory__policy_violation__creates_policy_violation_candidate() -> None:
    violation = _violation()

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.source == IncidentSource.POLICY_VIOLATION


def test_incident_factory__candidate_contains_device_id() -> None:
    violation = _violation()

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.device_id == DEVICE_ID


def test_incident_factory__candidate_contains_rule_ref() -> None:
    violation = _violation(rule_ref="policy-acl-external-in")

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.rule_ref == "policy-acl-external-in"


def test_incident_factory__candidate_affected_resource_is_copied_verbatim() -> None:
    violation = _violation(affected_resource="interface:GigabitEthernet0/1:acl_in")

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.affected_resource == "interface:GigabitEthernet0/1:acl_in"


def test_incident_factory__candidate_contains_severity() -> None:
    violation = _violation(severity=Severity.MEDIUM)

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.severity == Severity.MEDIUM


def test_incident_factory__candidate_recommendation_is_copied_verbatim() -> None:
    violation = _violation(recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1")

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.recommendation == "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1"


def test_incident_factory__candidate_observed_at_equals_violation_detected_at() -> None:
    violation = _violation(detected_at=DETECTED_AT)

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.observed_at == DETECTED_AT


def test_incident_factory__evidence_preserves_expected_acl_name() -> None:
    violation = _violation(
        evidence=AclAssignmentEvidence(
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name=None,
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
        )
    )

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.evidence.expected_acl_name == "ACL-EXTERNAL-IN"


def test_incident_factory__evidence_preserves_actual_acl_name() -> None:
    violation = _violation(
        evidence=AclAssignmentEvidence(
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name="ACL-OTHER",
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
        )
    )

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.evidence.actual_acl_name == "ACL-OTHER"


def test_incident_factory__evidence_preserves_interface_and_direction() -> None:
    violation = _violation(
        evidence=AclAssignmentEvidence(
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name=None,
            interface_name="GigabitEthernet0/2",
            direction=AclDirection.OUT,
        )
    )

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.evidence.interface_name == "GigabitEthernet0/2"
    assert candidate.evidence.direction == AclDirection.OUT


def test_incident_factory__evidence_preserves_violation_type() -> None:
    violation = _violation(violation_type=ViolationType.TARGET_INTERFACE_MISSING)

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.evidence.violation_type == ViolationType.TARGET_INTERFACE_MISSING


def test_incident_factory__evidence_preserves_source_snapshot_id() -> None:
    violation = _violation(source_snapshot_id=SNAPSHOT_ID)

    candidate = IncidentFactory.build_candidate(violation)

    assert candidate.evidence.source_snapshot_id == SNAPSHOT_ID


def test_incident_factory__build_candidate__does_not_mutate_violation() -> None:
    violation = _violation()
    violation_before = violation

    IncidentFactory.build_candidate(violation)

    assert violation == violation_before


def test_incident_factory__equal_violations__produce_equal_candidates() -> None:
    first = IncidentFactory.build_candidate(_violation())
    second = IncidentFactory.build_candidate(_violation())

    assert first == second
