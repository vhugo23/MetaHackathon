"""Anomaly-evidence serialization (Gate H1).

Explicit, discriminated (de)serialization for the three ``RuleEvidence``
subtypes (``CpuHighEvidence``/``LinkFlapEvidence``/``BgpDownEvidence``,
``meta_rne.domain.anomaly``) — mirroring the exact structural pattern already
used by ``policy_violation_evidence_to_json``/``_from_json`` and
``interface_states_to_json``/``_from_json`` elsewhere in
``persistence/serialization.py`` (same ``_require_dict``/``_require_list``/
``_get``/``_enum`` helpers, same single-stable-``SerializationError``
contract). No generic recursive serializer — three explicit per-type
function pairs plus two small dispatch functions
(``anomaly_evidence_to_json``/``anomaly_evidence_from_json``), dispatching by
concrete type (to-JSON direction) and by the raw persisted ``rule_ref``
string (from-JSON direction), mirroring ``domain/anomaly.py``'s own
``_EVIDENCE_TYPE_BY_RULE_ID`` table.

This module has never serialized a ``datetime`` into a JSON blob before (no
existing production precedent anywhere in this codebase) — the round-trip
pair used here is plain ``datetime.isoformat()``/``datetime.fromisoformat()``,
asserted only by parsed-value equality and a UTC-offset check, never a
hardcoded string literal. ``CpuHighEvidence``/``LinkFlapEvidence`` already
enforce UTC-awareness at construction (``domain/anomaly.py``'s own
``_require_utc``, raising a plain ``ValueError``) — the two corresponding
``_from_json`` functions here must translate that ``ValueError`` into
``SerializationError`` like every other checked exception in this module, so
a naive or non-UTC stored timestamp never leaks a raw ``ValueError``.

Gate H1 scope only: evidence type widening and serialization. No severity,
recommendation, affected_resource, fingerprint, or incident-mapping code
exists here or anywhere else in this gate.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.anomaly import (
    BgpDownEvidence,
    CpuHighEvidence,
    CpuSampleEvidence,
    InterfaceTransitionEvidence,
    LinkFlapEvidence,
)
from meta_rne.domain.telemetry import BgpState, LinkState
from meta_rne.persistence.serialization import (
    SerializationError,
    anomaly_evidence_from_json,
    anomaly_evidence_to_json,
    bgp_down_evidence_from_json,
    bgp_down_evidence_to_json,
    cpu_high_evidence_from_json,
    cpu_high_evidence_to_json,
    link_flap_evidence_from_json,
    link_flap_evidence_to_json,
)

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


# --- CpuHighEvidence: direct to_json/from_json/round-trip --------------------


def _cpu_high_evidence() -> CpuHighEvidence:
    return CpuHighEvidence(
        samples=(
            CpuSampleEvidence(timestamp=T0, cpu_utilization_pct=95.0),
            CpuSampleEvidence(timestamp=T0 + timedelta(seconds=30), cpu_utilization_pct=96.5),
        )
    )


def test_cpu_high_evidence_to_json__exact_keys() -> None:
    result = cpu_high_evidence_to_json(_cpu_high_evidence())

    assert set(result.keys()) == {"samples"}
    assert set(result["samples"][0].keys()) == {"timestamp", "cpu_utilization_pct"}


def test_cpu_high_evidence_to_json__preserves_sample_order() -> None:
    result = cpu_high_evidence_to_json(_cpu_high_evidence())

    assert result["samples"][0]["cpu_utilization_pct"] == 95.0
    assert result["samples"][1]["cpu_utilization_pct"] == 96.5


def test_cpu_high_evidence_from_json__valid_payload__reconstructs_exact_value() -> None:
    evidence = _cpu_high_evidence()

    result = cpu_high_evidence_from_json(cpu_high_evidence_to_json(evidence))

    assert result == evidence


def test_cpu_high_evidence__direct_round_trip__equals_original() -> None:
    evidence = _cpu_high_evidence()

    assert cpu_high_evidence_from_json(cpu_high_evidence_to_json(evidence)) == evidence


def test_cpu_high_evidence__round_trip__preserves_tuple_ordering() -> None:
    evidence = CpuHighEvidence(
        samples=(
            CpuSampleEvidence(timestamp=T0, cpu_utilization_pct=91.0),
            CpuSampleEvidence(timestamp=T0 + timedelta(seconds=10), cpu_utilization_pct=99.0),
            CpuSampleEvidence(timestamp=T0 + timedelta(seconds=20), cpu_utilization_pct=92.0),
        )
    )

    result = cpu_high_evidence_from_json(cpu_high_evidence_to_json(evidence))

    assert [sample.cpu_utilization_pct for sample in result.samples] == [91.0, 99.0, 92.0]


def test_cpu_high_evidence__round_trip__timestamp_is_parsed_and_utc() -> None:
    evidence = _cpu_high_evidence()

    result = cpu_high_evidence_from_json(cpu_high_evidence_to_json(evidence))

    assert result.samples[0].timestamp == evidence.samples[0].timestamp
    assert result.samples[0].timestamp.utcoffset() == UTC.utcoffset(None)


# --- CpuHighEvidence: malformed payloads --------------------------------------


def test_cpu_high_evidence_from_json__missing_samples__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json({})


def test_cpu_high_evidence_from_json__samples_not_a_list__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json({"samples": "not-a-list"})


def test_cpu_high_evidence_from_json__sample_item_not_an_object__raises_serialization_error() -> (
    None
):
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json({"samples": ["not-an-object"]})


def test_cpu_high_evidence_from_json__missing_timestamp__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json({"samples": [{"cpu_utilization_pct": 95.0}]})


def test_cpu_high_evidence_from_json__missing_cpu_value__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json({"samples": [{"timestamp": T0.isoformat()}]})


def test_cpu_high_evidence_from_json__invalid_cpu_value_type__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json(
            {"samples": [{"timestamp": T0.isoformat(), "cpu_utilization_pct": "not-a-number"}]}
        )


def test_cpu_high_evidence_from_json__malformed_timestamp_text__raises_serialization_error() -> (
    None
):
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json(
            {"samples": [{"timestamp": "not-a-timestamp", "cpu_utilization_pct": 95.0}]}
        )


def test_cpu_high_evidence_from_json__non_string_timestamp__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json(
            {"samples": [{"timestamp": 12345, "cpu_utilization_pct": 95.0}]}
        )


def test_cpu_high_evidence_from_json__naive_timestamp__raises_serialization_error() -> None:
    naive = datetime(2026, 7, 18, 10, 0, 0)

    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json(
            {"samples": [{"timestamp": naive.isoformat(), "cpu_utilization_pct": 95.0}]}
        )


def test_cpu_high_evidence_from_json__non_utc_offset_timestamp__raises_serialization_error() -> (
    None
):
    non_utc = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(SerializationError):
        cpu_high_evidence_from_json(
            {"samples": [{"timestamp": non_utc.isoformat(), "cpu_utilization_pct": 95.0}]}
        )


# --- LinkFlapEvidence: direct to_json/from_json/round-trip -------------------


def _link_flap_evidence() -> LinkFlapEvidence:
    return LinkFlapEvidence(
        interface_name="GigabitEthernet0/1",
        transitions=(
            InterfaceTransitionEvidence(timestamp=T0, oper_state=LinkState.DOWN),
            InterfaceTransitionEvidence(
                timestamp=T0 + timedelta(seconds=10), oper_state=LinkState.UP
            ),
            InterfaceTransitionEvidence(
                timestamp=T0 + timedelta(seconds=20), oper_state=LinkState.DOWN
            ),
            InterfaceTransitionEvidence(
                timestamp=T0 + timedelta(seconds=30), oper_state=LinkState.UP
            ),
        ),
    )


def test_link_flap_evidence_to_json__exact_keys() -> None:
    result = link_flap_evidence_to_json(_link_flap_evidence())

    assert set(result.keys()) == {"interface_name", "transitions"}
    assert set(result["transitions"][0].keys()) == {"timestamp", "oper_state"}


def test_link_flap_evidence_to_json__oper_state_serialized_through_value() -> None:
    result = link_flap_evidence_to_json(_link_flap_evidence())

    assert result["transitions"][0]["oper_state"] == "down"
    assert result["transitions"][1]["oper_state"] == "up"


def test_link_flap_evidence_from_json__valid_payload__reconstructs_exact_value() -> None:
    evidence = _link_flap_evidence()

    result = link_flap_evidence_from_json(link_flap_evidence_to_json(evidence))

    assert result == evidence


def test_link_flap_evidence__direct_round_trip__equals_original() -> None:
    evidence = _link_flap_evidence()

    assert link_flap_evidence_from_json(link_flap_evidence_to_json(evidence)) == evidence


def test_link_flap_evidence__round_trip__preserves_tuple_ordering() -> None:
    evidence = _link_flap_evidence()

    result = link_flap_evidence_from_json(link_flap_evidence_to_json(evidence))

    assert [t.oper_state for t in result.transitions] == [
        LinkState.DOWN,
        LinkState.UP,
        LinkState.DOWN,
        LinkState.UP,
    ]


def test_link_flap_evidence__round_trip__timestamp_is_parsed_and_utc() -> None:
    evidence = _link_flap_evidence()

    result = link_flap_evidence_from_json(link_flap_evidence_to_json(evidence))

    assert result.transitions[0].timestamp == evidence.transitions[0].timestamp
    assert result.transitions[0].timestamp.utcoffset() == UTC.utcoffset(None)


# --- LinkFlapEvidence: malformed payloads -------------------------------------
# (No "blank interface_name rejected" case: domain/anomaly.py's
# LinkFlapEvidence/InterfaceTransitionEvidence do not validate non-blank
# interface_name at construction, so asserting rejection here would assert
# behavior the domain layer does not actually implement.)


def test_link_flap_evidence_from_json__missing_interface_name__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json({"transitions": []})


def test_link_flap_evidence_from_json__missing_transitions__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json({"interface_name": "GigabitEthernet0/1"})


def test_link_flap_evidence_from_json__transitions_not_a_list__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {"interface_name": "GigabitEthernet0/1", "transitions": "not-a-list"}
        )


def test_link_flap_evidence_from_json__malformed_transition_item__raises_serialization_error() -> (
    None
):
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {"interface_name": "GigabitEthernet0/1", "transitions": ["not-an-object"]}
        )


def test_link_flap_evidence_from_json__missing_timestamp__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {
                "interface_name": "GigabitEthernet0/1",
                "transitions": [{"oper_state": "up"}],
            }
        )


def test_link_flap_evidence_from_json__invalid_oper_state__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {
                "interface_name": "GigabitEthernet0/1",
                "transitions": [{"timestamp": T0.isoformat(), "oper_state": "sideways"}],
            }
        )


def test_link_flap_evidence_from_json__malformed_timestamp_text__raises_serialization_error() -> (
    None
):
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {
                "interface_name": "GigabitEthernet0/1",
                "transitions": [{"timestamp": "not-a-timestamp", "oper_state": "up"}],
            }
        )


def test_link_flap_evidence_from_json__naive_timestamp__raises_serialization_error() -> None:
    naive = datetime(2026, 7, 18, 10, 0, 0)

    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {
                "interface_name": "GigabitEthernet0/1",
                "transitions": [{"timestamp": naive.isoformat(), "oper_state": "up"}],
            }
        )


def test_link_flap_evidence_from_json__non_utc_offset_timestamp__raises_serialization_error() -> (
    None
):
    non_utc = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {
                "interface_name": "GigabitEthernet0/1",
                "transitions": [{"timestamp": non_utc.isoformat(), "oper_state": "up"}],
            }
        )


def test_link_flap_evidence_from_json__non_string_timestamp__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        link_flap_evidence_from_json(
            {
                "interface_name": "GigabitEthernet0/1",
                "transitions": [{"timestamp": 12345, "oper_state": "up"}],
            }
        )


# --- BgpDownEvidence: direct to_json/from_json/round-trip --------------------


def _bgp_down_evidence() -> BgpDownEvidence:
    return BgpDownEvidence(
        neighbor_ip="10.0.0.1", state=BgpState.IDLE, previous_state=BgpState.ESTABLISHED
    )


def test_bgp_down_evidence_to_json__exact_keys() -> None:
    result = bgp_down_evidence_to_json(_bgp_down_evidence())

    assert set(result.keys()) == {"neighbor_ip", "state", "previous_state"}


def test_bgp_down_evidence_to_json__states_serialized_through_value() -> None:
    result = bgp_down_evidence_to_json(_bgp_down_evidence())

    assert result["state"] == "Idle"
    assert result["previous_state"] == "Established"


def test_bgp_down_evidence_from_json__valid_payload__reconstructs_exact_value() -> None:
    evidence = _bgp_down_evidence()

    result = bgp_down_evidence_from_json(bgp_down_evidence_to_json(evidence))

    assert result == evidence


def test_bgp_down_evidence__direct_round_trip__equals_original() -> None:
    evidence = _bgp_down_evidence()

    assert bgp_down_evidence_from_json(bgp_down_evidence_to_json(evidence)) == evidence


# --- BgpDownEvidence: malformed payloads --------------------------------------


def test_bgp_down_evidence_from_json__missing_neighbor_ip__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_down_evidence_from_json({"state": "Idle", "previous_state": "Established"})


def test_bgp_down_evidence_from_json__missing_state__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_down_evidence_from_json({"neighbor_ip": "10.0.0.1", "previous_state": "Established"})


def test_bgp_down_evidence_from_json__missing_previous_state__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_down_evidence_from_json({"neighbor_ip": "10.0.0.1", "state": "Idle"})


def test_bgp_down_evidence_from_json__invalid_current_state__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_down_evidence_from_json(
            {"neighbor_ip": "10.0.0.1", "state": "NotAState", "previous_state": "Established"}
        )


def test_bgp_down_evidence_from_json__invalid_previous_state__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_down_evidence_from_json(
            {"neighbor_ip": "10.0.0.1", "state": "Idle", "previous_state": "NotAState"}
        )


def test_bgp_down_evidence_from_json__malformed_field_types__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        bgp_down_evidence_from_json({"neighbor_ip": 12345, "state": "Idle", "previous_state": 987})


# --- anomaly_evidence_to_json / anomaly_evidence_from_json: dispatch ---------


def test_anomaly_evidence_to_json__cpu_high__equals_direct_serializer_output() -> None:
    evidence = _cpu_high_evidence()

    assert anomaly_evidence_to_json(evidence) == cpu_high_evidence_to_json(evidence)


def test_anomaly_evidence_to_json__link_flap__equals_direct_serializer_output() -> None:
    evidence = _link_flap_evidence()

    assert anomaly_evidence_to_json(evidence) == link_flap_evidence_to_json(evidence)


def test_anomaly_evidence_to_json__bgp_down__equals_direct_serializer_output() -> None:
    evidence = _bgp_down_evidence()

    assert anomaly_evidence_to_json(evidence) == bgp_down_evidence_to_json(evidence)


def test_anomaly_evidence_from_json__cpu_high_rule_ref__equals_direct_deserializer_output() -> None:
    evidence = _cpu_high_evidence()
    data = cpu_high_evidence_to_json(evidence)

    assert anomaly_evidence_from_json("RULE-CPU-HIGH", data) == cpu_high_evidence_from_json(data)


def test_anomaly_evidence_from_json__link_flap_rule_ref__equals_direct_deserializer_output() -> (
    None
):
    evidence = _link_flap_evidence()
    data = link_flap_evidence_to_json(evidence)

    assert anomaly_evidence_from_json("RULE-LINK-FLAP", data) == link_flap_evidence_from_json(data)


def test_anomaly_evidence_from_json__bgp_down_rule_ref__equals_direct_deserializer_output() -> None:
    evidence = _bgp_down_evidence()
    data = bgp_down_evidence_to_json(evidence)

    assert anomaly_evidence_from_json("RULE-BGP-DOWN", data) == bgp_down_evidence_from_json(data)


def test_anomaly_evidence_to_json__cpu_high__dispatches_to_cpu_high_type() -> None:
    evidence = _cpu_high_evidence()

    result = anomaly_evidence_to_json(evidence)

    assert "samples" in result


def test_anomaly_evidence_to_json__link_flap__dispatches_to_link_flap_type() -> None:
    evidence = _link_flap_evidence()

    result = anomaly_evidence_to_json(evidence)

    assert "interface_name" in result
    assert "transitions" in result


def test_anomaly_evidence_to_json__bgp_down__dispatches_to_bgp_down_type() -> None:
    evidence = _bgp_down_evidence()

    result = anomaly_evidence_to_json(evidence)

    assert "neighbor_ip" in result


def test_anomaly_evidence_from_json__cpu_high_rule_ref__returns_cpu_high_evidence() -> None:
    evidence = _cpu_high_evidence()
    data = cpu_high_evidence_to_json(evidence)

    result = anomaly_evidence_from_json("RULE-CPU-HIGH", data)

    assert isinstance(result, CpuHighEvidence)


def test_anomaly_evidence_from_json__link_flap_rule_ref__returns_link_flap_evidence() -> None:
    evidence = _link_flap_evidence()
    data = link_flap_evidence_to_json(evidence)

    result = anomaly_evidence_from_json("RULE-LINK-FLAP", data)

    assert isinstance(result, LinkFlapEvidence)


def test_anomaly_evidence_from_json__bgp_down_rule_ref__returns_bgp_down_evidence() -> None:
    evidence = _bgp_down_evidence()
    data = bgp_down_evidence_to_json(evidence)

    result = anomaly_evidence_from_json("RULE-BGP-DOWN", data)

    assert isinstance(result, BgpDownEvidence)


# --- Dispatcher error behavior -------------------------------------------------


def test_anomaly_evidence_to_json__unsupported_evidence_type__raises_serialization_error() -> None:
    class _NotARuleEvidence:
        pass

    with pytest.raises(SerializationError):
        anomaly_evidence_to_json(_NotARuleEvidence())  # type: ignore[arg-type]


def test_anomaly_evidence_from_json__unknown_rule_ref__raises_serialization_error() -> None:
    with pytest.raises(SerializationError):
        anomaly_evidence_from_json("RULE-DOES-NOT-EXIST", {})


def test_anomaly_evidence_from_json__valid_rule_ref_wrong_shape__raises_serialization_error() -> (
    None
):
    # A syntactically valid rule_ref ("RULE-BGP-DOWN") whose data matches
    # CpuHighEvidence's shape instead of BgpDownEvidence's.
    wrong_shape_data = cpu_high_evidence_to_json(_cpu_high_evidence())

    with pytest.raises(SerializationError):
        anomaly_evidence_from_json("RULE-BGP-DOWN", wrong_shape_data)
