"""Shared caller-consistency validation for ``IncidentRepository.
upsert_open_incident`` (Day 4B3), used identically by both the in-memory and
SQLAlchemy implementations so the two never drift.

Every check here is a caller-programming-error, not a stored-state conflict
— plain ``ValueError``, validated before any lock/transaction/mutation.
"""

from datetime import datetime

from meta_rne.domain.incident import IncidentCandidate, IncidentSource, compute_fingerprint


def validate_candidate_consistency(
    candidate: IncidentCandidate, fingerprint: str, observed_at: datetime
) -> None:
    expected_fingerprint = compute_fingerprint(
        candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
    )
    if fingerprint != expected_fingerprint:
        raise ValueError(
            "fingerprint does not match compute_fingerprint(candidate.device_id, "
            "candidate.source, candidate.rule_ref, candidate.affected_resource)"
        )
    if observed_at != candidate.observed_at:
        raise ValueError("observed_at does not match candidate.observed_at")
    if candidate.source is not IncidentSource.POLICY_VIOLATION:
        raise ValueError(
            f"unsupported IncidentCandidate.source for upsert_open_incident: {candidate.source!r}"
        )


def require_non_empty_incident_id(incident_id: str) -> None:
    if not incident_id.strip():
        raise ValueError("incident_id_factory produced an empty or whitespace-only ID")
