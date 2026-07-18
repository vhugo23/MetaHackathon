"""Maps a ConfigurationViolation to an IncidentCandidate (FR-07).

Pure domain/detection logic: a single finding in, a candidate out, no I/O,
no clock access, no repository access, no fingerprinting, no ID
generation. See docs/architecture.md Section 9 and docs/domain-model.md
Section 17 for the approved contract.
"""

from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentSource,
    PolicyViolationIncidentEvidence,
)
from meta_rne.domain.policy import ConfigurationViolation


class IncidentFactory:
    """Stateless; see docs/domain-model.md Section 17."""

    @staticmethod
    def build_candidate(violation: ConfigurationViolation) -> IncidentCandidate:
        return IncidentCandidate(
            device_id=violation.device_id,
            source=IncidentSource.POLICY_VIOLATION,
            rule_ref=violation.rule_ref,
            affected_resource=violation.affected_resource,
            severity=violation.severity,
            evidence=PolicyViolationIncidentEvidence(
                source_snapshot_id=violation.source_snapshot_id,
                violation_type=violation.violation_type,
                expected_acl_name=violation.evidence.expected_acl_name,
                actual_acl_name=violation.evidence.actual_acl_name,
                interface_name=violation.evidence.interface_name,
                direction=violation.evidence.direction,
            ),
            recommendation=violation.recommendation,
            observed_at=violation.detected_at,
        )
