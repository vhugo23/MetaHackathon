"""Shared caller-consistency validation for ``IncidentRepository.
upsert_open_incident`` (Day 4B3), used identically by both the in-memory and
SQLAlchemy implementations so the two never drift.

Every check here is a caller-programming-error, not a stored-state conflict
— plain ``ValueError``, validated before any lock/transaction/mutation.

Gate H1B adds explicit source/evidence-family validation (allow-list, not a
removal of the prior guard): POLICY_VIOLATION requires
``PolicyViolationIncidentEvidence``; ANOMALY requires exactly one of
RULE-CPU-HIGH/``CpuHighEvidence``, RULE-LINK-FLAP/``LinkFlapEvidence``,
RULE-BGP-DOWN/``BgpDownEvidence`` (an unknown ``rule_ref``, or a
``RuleEvidence`` subtype that doesn't match the given ``rule_ref``, is
rejected); DRIFT remains rejected outright (drift incidents are not
implemented). No severity/recommendation/affected_resource/fingerprint
mapping decision is validated here — those remain unapproved product
decisions (Gate H2 and later).
"""

from datetime import datetime

from meta_rne.domain.anomaly import BgpDownEvidence, CpuHighEvidence, LinkFlapEvidence, RuleId
from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentSource,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)

_ANOMALY_EVIDENCE_TYPE_BY_RULE_ID: dict[RuleId, type] = {
    RuleId.CPU_HIGH: CpuHighEvidence,
    RuleId.LINK_FLAP: LinkFlapEvidence,
    RuleId.BGP_DOWN: BgpDownEvidence,
}


def _validate_source_and_evidence(candidate: IncidentCandidate) -> None:
    if candidate.source is IncidentSource.POLICY_VIOLATION:
        if not isinstance(candidate.evidence, PolicyViolationIncidentEvidence):
            raise ValueError(
                "IncidentCandidate.evidence must be a PolicyViolationIncidentEvidence for "
                f"source POLICY_VIOLATION, got {type(candidate.evidence).__name__}"
            )
        return

    if candidate.source is IncidentSource.ANOMALY:
        try:
            rule_id = RuleId(candidate.rule_ref)
        except ValueError:
            raise ValueError(
                "unsupported IncidentCandidate.rule_ref for source ANOMALY: "
                f"{candidate.rule_ref!r}"
            ) from None
        expected_evidence_type = _ANOMALY_EVIDENCE_TYPE_BY_RULE_ID[rule_id]
        if not isinstance(candidate.evidence, expected_evidence_type):
            raise ValueError(
                f"IncidentCandidate.evidence must be a {expected_evidence_type.__name__} for "
                f"rule_ref {candidate.rule_ref!r}, got {type(candidate.evidence).__name__}"
            )
        return

    raise ValueError(
        f"unsupported IncidentCandidate.source for upsert_open_incident: {candidate.source!r}"
    )


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
    _validate_source_and_evidence(candidate)


def require_non_empty_incident_id(incident_id: str) -> None:
    if not incident_id.strip():
        raise ValueError("incident_id_factory produced an empty or whitespace-only ID")
