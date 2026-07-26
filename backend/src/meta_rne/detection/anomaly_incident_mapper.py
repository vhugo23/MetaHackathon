"""Maps an Anomaly to an IncidentCandidate (Gate H2).

Pure domain/detection logic: a single finding in, a candidate out, no I/O,
no clock access, no repository access, no fingerprinting, no ID generation
— mirrors ``IncidentFactory.build_candidate``'s shape without touching that
class (a fully separate module, per the Gate H0 audit's recommendation to
prefer a narrow mapping layer over reopening stable policy-incident
behavior). Severity, ``affected_resource``, and recommendation text below
are fixed, approved product decisions (not computed dynamically, not
templated beyond simple field substitution).

``Anomaly.__post_init__`` (``domain/anomaly.py``) already guarantees
``rule_id``/``evidence`` consistency at construction — an ``Anomaly`` with a
mismatched pair cannot exist. The three explicit branches below still each
narrow ``evidence`` with an ``isinstance`` check (never an unsafe cast)
before reading its rule-specific fields, and an unrecognized ``rule_id``
raises ``ValueError`` with a stable, rule-specific message rather than
silently falling through.
"""

from meta_rne.domain.anomaly import (
    Anomaly,
    BgpDownEvidence,
    CpuHighEvidence,
    LinkFlapEvidence,
    RuleId,
)
from meta_rne.domain.incident import IncidentCandidate, IncidentSource
from meta_rne.domain.policy import Severity


class AnomalyIncidentMapper:
    """Stateless; see docs/domain-model.md Section 17 for the sibling
    ``IncidentFactory`` contract this mirrors."""

    @staticmethod
    def build_candidate(anomaly: Anomaly) -> IncidentCandidate:
        if anomaly.rule_id is RuleId.CPU_HIGH:
            if not isinstance(anomaly.evidence, CpuHighEvidence):
                raise ValueError(
                    "AnomalyIncidentMapper: RULE-CPU-HIGH requires CpuHighEvidence, got "
                    f"{type(anomaly.evidence).__name__}"
                )
            affected_resource = "device"
            severity = Severity.HIGH
            recommendation = f"Investigate sustained high CPU utilization on {anomaly.device_id}."
        elif anomaly.rule_id is RuleId.LINK_FLAP:
            if not isinstance(anomaly.evidence, LinkFlapEvidence):
                raise ValueError(
                    "AnomalyIncidentMapper: RULE-LINK-FLAP requires LinkFlapEvidence, got "
                    f"{type(anomaly.evidence).__name__}"
                )
            affected_resource = f"interface:{anomaly.evidence.interface_name}"
            severity = Severity.HIGH
            recommendation = (
                f"Investigate unstable link state on {anomaly.device_id} interface "
                f"{anomaly.evidence.interface_name}."
            )
        elif anomaly.rule_id is RuleId.BGP_DOWN:
            if not isinstance(anomaly.evidence, BgpDownEvidence):
                raise ValueError(
                    "AnomalyIncidentMapper: RULE-BGP-DOWN requires BgpDownEvidence, got "
                    f"{type(anomaly.evidence).__name__}"
                )
            affected_resource = f"bgp-neighbor:{anomaly.evidence.neighbor_ip}"
            severity = Severity.CRITICAL
            recommendation = (
                f"Investigate BGP session down on {anomaly.device_id} neighbor "
                f"{anomaly.evidence.neighbor_ip}."
            )
        else:
            raise ValueError(f"AnomalyIncidentMapper: unsupported RuleId: {anomaly.rule_id!r}")

        return IncidentCandidate(
            device_id=anomaly.device_id,
            source=IncidentSource.ANOMALY,
            rule_ref=anomaly.rule_id.value,
            affected_resource=affected_resource,
            severity=severity,
            evidence=anomaly.evidence,
            recommendation=recommendation,
            observed_at=anomaly.detected_at,
        )
