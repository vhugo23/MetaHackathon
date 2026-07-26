"""AnomalyIncidentMapper — narrow Anomaly-to-IncidentCandidate mapping
(Gate H2).

A fully separate module from ``IncidentFactory`` (never modified, never
called) — mirrors its "stateless single static method" shape without
touching it, per the Gate H0 audit's recommendation. No fingerprint,
repository, UnitOfWork, incident_id, or persistence behavior belongs here;
those remain Gate H3 (telemetry-ingestion integration) and later.

``Anomaly.__post_init__`` already enforces rule/evidence consistency at
construction (``domain/anomaly.py``'s ``_EVIDENCE_TYPE_BY_RULE_ID`` check) —
an ``Anomaly`` with a rule_id/evidence mismatch cannot exist, so this file
tests only the three reachable, supported (rule_id, evidence) pairings; it
does not attempt to bypass that domain-level guarantee with unsafe
construction merely to exercise an unreachable mapper branch.
"""

from datetime import UTC, datetime

from meta_rne.detection.anomaly_incident_mapper import AnomalyIncidentMapper
from meta_rne.domain.anomaly import (
    Anomaly,
    BgpDownEvidence,
    CpuHighEvidence,
    CpuSampleEvidence,
    InterfaceTransitionEvidence,
    LinkFlapEvidence,
    RuleId,
)
from meta_rne.domain.incident import IncidentSource
from meta_rne.domain.policy import Severity
from meta_rne.domain.telemetry import BgpState, LinkState

DEVICE_ID = "spine-01"
DETECTED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _cpu_high_anomaly() -> Anomaly:
    return Anomaly(
        device_id=DEVICE_ID,
        rule_id=RuleId.CPU_HIGH,
        evidence=CpuHighEvidence(
            samples=(
                CpuSampleEvidence(timestamp=DETECTED_AT, cpu_utilization_pct=95.0),
                CpuSampleEvidence(timestamp=DETECTED_AT, cpu_utilization_pct=96.0),
            )
        ),
        detected_at=DETECTED_AT,
    )


def _link_flap_anomaly(interface_name: str = "GigabitEthernet0/1") -> Anomaly:
    return Anomaly(
        device_id=DEVICE_ID,
        rule_id=RuleId.LINK_FLAP,
        evidence=LinkFlapEvidence(
            interface_name=interface_name,
            transitions=(
                InterfaceTransitionEvidence(timestamp=DETECTED_AT, oper_state=LinkState.DOWN),
                InterfaceTransitionEvidence(timestamp=DETECTED_AT, oper_state=LinkState.UP),
            ),
        ),
        detected_at=DETECTED_AT,
    )


def _bgp_down_anomaly(neighbor_ip: str = "10.0.0.1") -> Anomaly:
    return Anomaly(
        device_id=DEVICE_ID,
        rule_id=RuleId.BGP_DOWN,
        evidence=BgpDownEvidence(
            neighbor_ip=neighbor_ip, state=BgpState.IDLE, previous_state=BgpState.ESTABLISHED
        ),
        detected_at=DETECTED_AT,
    )


# --- CPU candidate -------------------------------------------------------------


def test_build_candidate__cpu_high__device_id_copied_from_anomaly() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate.device_id == DEVICE_ID


def test_build_candidate__cpu_high__source_is_anomaly() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate.source == IncidentSource.ANOMALY


def test_build_candidate__cpu_high__severity_is_high() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate.severity == Severity.HIGH


def test_build_candidate__cpu_high__rule_ref_is_exact_rule_id_value() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate.rule_ref == "RULE-CPU-HIGH"
    assert candidate.rule_ref == RuleId.CPU_HIGH.value


def test_build_candidate__cpu_high__affected_resource_is_device() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate.affected_resource == "device"


def test_build_candidate__cpu_high__recommendation_is_exact_approved_text() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate.recommendation == "Investigate sustained high CPU utilization on spine-01."


def test_build_candidate__cpu_high__evidence_is_preserved_exactly() -> None:
    anomaly = _cpu_high_anomaly()

    candidate = AnomalyIncidentMapper.build_candidate(anomaly)

    assert candidate.evidence is anomaly.evidence


def test_build_candidate__cpu_high__observed_at_equals_detected_at() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate.observed_at == DETECTED_AT


# --- Link-flap candidate --------------------------------------------------------


def test_build_candidate__link_flap__all_fields_exact() -> None:
    anomaly = _link_flap_anomaly(interface_name="GigabitEthernet0/2")

    candidate = AnomalyIncidentMapper.build_candidate(anomaly)

    assert candidate.device_id == DEVICE_ID
    assert candidate.source == IncidentSource.ANOMALY
    assert candidate.severity == Severity.HIGH
    assert candidate.rule_ref == "RULE-LINK-FLAP"
    assert candidate.rule_ref == RuleId.LINK_FLAP.value
    assert candidate.affected_resource == "interface:GigabitEthernet0/2"
    assert candidate.recommendation == (
        "Investigate unstable link state on spine-01 interface GigabitEthernet0/2."
    )
    assert candidate.evidence is anomaly.evidence
    assert candidate.observed_at == DETECTED_AT


def test_build_candidate__link_flap__affected_resource_uses_evidence_interface_name() -> None:
    anomaly = _link_flap_anomaly(interface_name="Ethernet1")

    candidate = AnomalyIncidentMapper.build_candidate(anomaly)

    assert candidate.affected_resource == "interface:Ethernet1"


# --- BGP-down candidate ----------------------------------------------------------


def test_build_candidate__bgp_down__all_fields_exact() -> None:
    anomaly = _bgp_down_anomaly(neighbor_ip="192.0.2.1")

    candidate = AnomalyIncidentMapper.build_candidate(anomaly)

    assert candidate.device_id == DEVICE_ID
    assert candidate.source == IncidentSource.ANOMALY
    assert candidate.severity == Severity.CRITICAL
    assert candidate.rule_ref == "RULE-BGP-DOWN"
    assert candidate.rule_ref == RuleId.BGP_DOWN.value
    assert candidate.affected_resource == "bgp-neighbor:192.0.2.1"
    assert candidate.recommendation == (
        "Investigate BGP session down on spine-01 neighbor 192.0.2.1."
    )
    assert candidate.evidence is anomaly.evidence
    assert candidate.observed_at == DETECTED_AT


def test_build_candidate__bgp_down__affected_resource_uses_evidence_neighbor_ip() -> None:
    anomaly = _bgp_down_anomaly(neighbor_ip="10.10.10.10")

    candidate = AnomalyIncidentMapper.build_candidate(anomaly)

    assert candidate.affected_resource == "bgp-neighbor:10.10.10.10"


# --- General behavior ------------------------------------------------------------


def test_build_candidate__evidence_type_is_not_converted_to_another_shape() -> None:
    anomaly = _cpu_high_anomaly()

    candidate = AnomalyIncidentMapper.build_candidate(anomaly)

    assert type(candidate.evidence) is CpuHighEvidence


def test_build_candidate__does_not_produce_a_fingerprint_attribute() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert not hasattr(candidate, "fingerprint")


def test_build_candidate__does_not_produce_an_incident_id_attribute() -> None:
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert not hasattr(candidate, "incident_id")


def test_build_candidate__does_not_require_a_repository_or_unit_of_work() -> None:
    # No fixture, mock, or argument of any repository/UnitOfWork shape is
    # passed anywhere in this file — build_candidate takes only an Anomaly.
    candidate = AnomalyIncidentMapper.build_candidate(_cpu_high_anomaly())

    assert candidate is not None


def test_build_candidate__repeated_calls_with_same_anomaly__produce_equal_candidates() -> None:
    anomaly = _cpu_high_anomaly()

    first = AnomalyIncidentMapper.build_candidate(anomaly)
    second = AnomalyIncidentMapper.build_candidate(anomaly)

    assert first == second


def test_build_candidate__does_not_mutate_the_input_anomaly() -> None:
    anomaly = _cpu_high_anomaly()
    anomaly_before = anomaly

    AnomalyIncidentMapper.build_candidate(anomaly)

    assert anomaly == anomaly_before
    assert anomaly.evidence == anomaly_before.evidence


def test_build_candidate__does_not_mutate_the_input_evidence() -> None:
    anomaly = _cpu_high_anomaly()
    evidence_before = anomaly.evidence

    AnomalyIncidentMapper.build_candidate(anomaly)

    assert anomaly.evidence == evidence_before
    assert anomaly.evidence is evidence_before
