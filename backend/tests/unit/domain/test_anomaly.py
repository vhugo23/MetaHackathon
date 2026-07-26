"""Anomaly/RuleEvidence domain value object invariants (Gate B).

Pure construction-time invariants only. No rule trigger logic exists yet —
see docs/domain-model.md Section 9 and docs/product-spec.md FR-06/
AC-07-AC-09. RULE-CPU-HIGH/RULE-LINK-FLAP/RULE-BGP-DOWN threshold logic is a
later gate.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.anomaly import (
    Anomaly,
    BgpDownEvidence,
    CpuHighEvidence,
    CpuSampleEvidence,
    InterfaceTransitionEvidence,
    LinkFlapEvidence,
    RuleId,
)
from meta_rne.domain.telemetry import BgpState, LinkState

DEVICE_ID = "spine-01"
DETECTED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _cpu_evidence(**overrides: object) -> CpuHighEvidence:
    defaults: dict[str, object] = {
        "samples": (
            CpuSampleEvidence(timestamp=DETECTED_AT, cpu_utilization_pct=95.0),
            CpuSampleEvidence(
                timestamp=DETECTED_AT + timedelta(seconds=30), cpu_utilization_pct=96.0
            ),
        ),
    }
    defaults.update(overrides)
    return CpuHighEvidence(**defaults)  # type: ignore[arg-type]


def _link_flap_evidence(**overrides: object) -> LinkFlapEvidence:
    defaults: dict[str, object] = {
        "interface_name": "GigabitEthernet0/1",
        "transitions": (
            InterfaceTransitionEvidence(timestamp=DETECTED_AT, oper_state=LinkState.DOWN),
            InterfaceTransitionEvidence(
                timestamp=DETECTED_AT + timedelta(seconds=10), oper_state=LinkState.UP
            ),
        ),
    }
    defaults.update(overrides)
    return LinkFlapEvidence(**defaults)  # type: ignore[arg-type]


def _bgp_down_evidence(**overrides: object) -> BgpDownEvidence:
    defaults: dict[str, object] = {
        "neighbor_ip": "10.0.0.1",
        "state": BgpState.IDLE,
        "previous_state": BgpState.ESTABLISHED,
    }
    defaults.update(overrides)
    return BgpDownEvidence(**defaults)  # type: ignore[arg-type]


def _anomaly(**overrides: object) -> Anomaly:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "rule_id": RuleId.CPU_HIGH,
        "evidence": _cpu_evidence(),
        "detected_at": DETECTED_AT,
    }
    defaults.update(overrides)
    return Anomaly(**defaults)  # type: ignore[arg-type]


# --- RuleId ---------------------------------------------------------------


def test_rule_id__has_approved_members_only() -> None:
    assert {member.value for member in RuleId} == {
        "RULE-CPU-HIGH",
        "RULE-LINK-FLAP",
        "RULE-BGP-DOWN",
    }


# --- Evidence-item field preservation --------------------------------------


def test_cpu_sample_evidence__valid_fields__preserves_fields() -> None:
    item = CpuSampleEvidence(timestamp=DETECTED_AT, cpu_utilization_pct=95.5)

    assert item.timestamp == DETECTED_AT
    assert item.cpu_utilization_pct == 95.5


def test_interface_transition_evidence__valid_fields__preserves_fields() -> None:
    item = InterfaceTransitionEvidence(timestamp=DETECTED_AT, oper_state=LinkState.DOWN)

    assert item.timestamp == DETECTED_AT
    assert item.oper_state == LinkState.DOWN


def test_bgp_down_evidence__valid_fields__preserves_fields() -> None:
    evidence = BgpDownEvidence(
        neighbor_ip="10.0.0.1", state=BgpState.ACTIVE, previous_state=BgpState.ESTABLISHED
    )

    assert evidence.neighbor_ip == "10.0.0.1"
    assert evidence.state == BgpState.ACTIVE
    assert evidence.previous_state == BgpState.ESTABLISHED


# --- Evidence-container tuple order preservation ---------------------------


def test_cpu_high_evidence__preserves_sample_tuple_order() -> None:
    first = CpuSampleEvidence(timestamp=DETECTED_AT, cpu_utilization_pct=91.0)
    second = CpuSampleEvidence(
        timestamp=DETECTED_AT + timedelta(seconds=30), cpu_utilization_pct=92.0
    )

    evidence = CpuHighEvidence(samples=(first, second))

    assert evidence.samples == (first, second)


def test_link_flap_evidence__preserves_transition_tuple_order() -> None:
    first = InterfaceTransitionEvidence(timestamp=DETECTED_AT, oper_state=LinkState.UP)
    second = InterfaceTransitionEvidence(
        timestamp=DETECTED_AT + timedelta(seconds=10), oper_state=LinkState.DOWN
    )

    evidence = LinkFlapEvidence(interface_name="GigabitEthernet0/1", transitions=(first, second))

    assert evidence.transitions == (first, second)
    assert evidence.interface_name == "GigabitEthernet0/1"


# --- Frozen / immutability -------------------------------------------------


def test_cpu_sample_evidence__is_immutable() -> None:
    item = CpuSampleEvidence(timestamp=DETECTED_AT, cpu_utilization_pct=95.0)

    with pytest.raises(AttributeError):
        item.cpu_utilization_pct = 99.0  # type: ignore[misc]


def test_interface_transition_evidence__is_immutable() -> None:
    item = InterfaceTransitionEvidence(timestamp=DETECTED_AT, oper_state=LinkState.UP)

    with pytest.raises(AttributeError):
        item.oper_state = LinkState.DOWN  # type: ignore[misc]


def test_cpu_high_evidence__is_immutable() -> None:
    evidence = _cpu_evidence()

    with pytest.raises(AttributeError):
        evidence.samples = ()  # type: ignore[misc]


def test_link_flap_evidence__is_immutable() -> None:
    evidence = _link_flap_evidence()

    with pytest.raises(AttributeError):
        evidence.interface_name = "GigabitEthernet0/2"  # type: ignore[misc]


def test_bgp_down_evidence__is_immutable() -> None:
    evidence = _bgp_down_evidence()

    with pytest.raises(AttributeError):
        evidence.neighbor_ip = "10.0.0.2"  # type: ignore[misc]


def test_anomaly__is_immutable() -> None:
    anomaly = _anomaly()

    with pytest.raises(AttributeError):
        anomaly.device_id = "leaf-01"  # type: ignore[misc]


# --- Anomaly field preservation ---------------------------------------------


def test_anomaly__valid_fields__preserves_every_field() -> None:
    evidence = _cpu_evidence()

    anomaly = Anomaly(
        device_id=DEVICE_ID, rule_id=RuleId.CPU_HIGH, evidence=evidence, detected_at=DETECTED_AT
    )

    assert anomaly.device_id == DEVICE_ID
    assert anomaly.rule_id == RuleId.CPU_HIGH
    assert anomaly.evidence == evidence
    assert anomaly.detected_at == DETECTED_AT


# --- device_id validation ----------------------------------------------------


def test_anomaly__empty_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _anomaly(device_id="")


def test_anomaly__whitespace_only_device_id__raises_value_error() -> None:
    with pytest.raises(ValueError, match="device_id"):
        _anomaly(device_id="   ")


# --- detected_at validation ---------------------------------------------------


def test_anomaly__naive_detected_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="detected_at"):
        _anomaly(detected_at=datetime(2026, 7, 18, 10, 0, 0))


def test_anomaly__non_utc_offset_detected_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="detected_at"):
        _anomaly(detected_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))))


# --- CPU evidence timestamp validation -----------------------------------------


def test_anomaly__naive_cpu_evidence_timestamp__raises_value_error() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        CpuHighEvidence(
            samples=(
                CpuSampleEvidence(
                    timestamp=datetime(2026, 7, 18, 10, 0, 0), cpu_utilization_pct=95.0
                ),
            )
        )


def test_anomaly__non_utc_cpu_evidence_timestamp__raises_value_error() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        CpuHighEvidence(
            samples=(
                CpuSampleEvidence(
                    timestamp=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))),
                    cpu_utilization_pct=95.0,
                ),
            )
        )


# --- Interface-transition timestamp validation ------------------------------------


def test_anomaly__naive_interface_transition_timestamp__raises_value_error() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        LinkFlapEvidence(
            interface_name="GigabitEthernet0/1",
            transitions=(
                InterfaceTransitionEvidence(
                    timestamp=datetime(2026, 7, 18, 10, 0, 0), oper_state=LinkState.DOWN
                ),
            ),
        )


def test_anomaly__non_utc_interface_transition_timestamp__raises_value_error() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        LinkFlapEvidence(
            interface_name="GigabitEthernet0/1",
            transitions=(
                InterfaceTransitionEvidence(
                    timestamp=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))),
                    oper_state=LinkState.DOWN,
                ),
            ),
        )


# --- rule_id / evidence-type pairing ----------------------------------------------


def test_anomaly__cpu_high_rule_with_link_flap_evidence__raises_value_error() -> None:
    with pytest.raises(ValueError, match="evidence"):
        _anomaly(rule_id=RuleId.CPU_HIGH, evidence=_link_flap_evidence())


def test_anomaly__link_flap_rule_with_bgp_down_evidence__raises_value_error() -> None:
    with pytest.raises(ValueError, match="evidence"):
        _anomaly(rule_id=RuleId.LINK_FLAP, evidence=_bgp_down_evidence())


def test_anomaly__bgp_down_rule_with_cpu_high_evidence__raises_value_error() -> None:
    with pytest.raises(ValueError, match="evidence"):
        _anomaly(rule_id=RuleId.BGP_DOWN, evidence=_cpu_evidence())


def test_anomaly__cpu_high_rule_with_cpu_high_evidence__accepted() -> None:
    anomaly = _anomaly(rule_id=RuleId.CPU_HIGH, evidence=_cpu_evidence())

    assert anomaly.rule_id == RuleId.CPU_HIGH


def test_anomaly__link_flap_rule_with_link_flap_evidence__accepted() -> None:
    anomaly = _anomaly(rule_id=RuleId.LINK_FLAP, evidence=_link_flap_evidence())

    assert anomaly.rule_id == RuleId.LINK_FLAP


def test_anomaly__bgp_down_rule_with_bgp_down_evidence__accepted() -> None:
    anomaly = _anomaly(rule_id=RuleId.BGP_DOWN, evidence=_bgp_down_evidence())

    assert anomaly.rule_id == RuleId.BGP_DOWN
