"""RuleEngine.evaluate behavior (Gate D2 — all three FR-06 rules).

Pure detection logic: a `list[TelemetrySample]` in, a `list[Anomaly]` out,
no I/O, no clock, no repository. See docs/domain-model.md Section 17's
`RuleEngine` port signature and docs/product-spec.md FR-06/AC-07/AC-08/
AC-09.

CPU rule contract (Gate C): strict `> 90.0` (exactly 90.0 does not
qualify); "two consecutive samples" means the latest two samples for the
same device in caller-provided encounter order (never sorted by
`sampled_at`, never combined across devices); no maximum time gap is
enforced (undocumented, not invented); only the latest two samples per
device are ever inspected, so a stale older qualifying pair produces no
anomaly once the latest sample is normal.

Link-flap rule contract (Gate D1): a transition is a state change between
two consecutive *observations* of the same (device_id, interface_name) —
the first observed state is initial only, never a transition; an
interface absent from a sample creates no observation (and is simply
skipped when building that resource's observation subsequence, never
inferred as "down"); four or more transitions are required (three do not
trigger); only the latest four transitions per resource are ever
inspected; the 60-second window is inclusive (`<= 60s` qualifies); a
four-transition candidate must have nondecreasing transition timestamps
in caller-provided encounter order to qualify — an out-of-order candidate
is valid input but never triggers.

BGP-down rule contract (Gate D2), resolving previously-open ambiguities:
down-family states are exactly Idle/Active; non-down predecessor states
are exactly Established/Connect/OpenSent/OpenConfirm. A trigger requires
the latest two *observations* for the same (device_id, neighbor_ip) to be
a non-down-family state followed by a down-family state — Idle<->Active,
repeated down-family states, and any non-down-to-non-down transition
never trigger. Only the latest two observations per resource are ever
inspected, so a stale older qualifying pair produces no anomaly once the
resource has recovered or moved to another down-family state. A neighbor
absent from a sample creates no observation (never inferred as any
state). The latest pair must have nondecreasing `sampled_at` timestamps
to qualify.

RuleEngine's output order is fixed: CPU anomalies, then link-flap
anomalies (first-(device, interface)-encounter order), then BGP-down
anomalies (first-(device, neighbor)-encounter order).
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.detection.rule_engine import RuleEngine
from meta_rne.domain.anomaly import RuleId
from meta_rne.domain.telemetry import (
    BgpSession,
    BgpState,
    InterfaceState,
    LinkState,
    TelemetrySample,
)

OBSERVED_AT = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _sample(**overrides: object) -> TelemetrySample:
    defaults: dict[str, object] = {
        "device_id": "spine-01",
        "sampled_at": OBSERVED_AT,
        "cpu_utilization_pct": 50.0,
        "memory_utilization_pct": 50.0,
        "interface_error_rate": 0.0,
        "interface_states": (InterfaceState(name="GigabitEthernet0/1", oper_state=LinkState.UP),),
        "bgp_sessions": (BgpSession(neighbor_ip="10.0.0.1", state=BgpState.ESTABLISHED),),
    }
    defaults.update(overrides)
    return TelemetrySample(**defaults)  # type: ignore[arg-type]


def _cpu_sample(
    device_id: str = "spine-01",
    sampled_at: datetime = OBSERVED_AT,
    cpu_utilization_pct: float = 50.0,
) -> TelemetrySample:
    return _sample(
        device_id=device_id, sampled_at=sampled_at, cpu_utilization_pct=cpu_utilization_pct
    )


def _link_sample(
    device_id: str = "spine-01",
    sampled_at: datetime = OBSERVED_AT,
    interface_states: tuple[InterfaceState, ...] = (),
) -> TelemetrySample:
    return _sample(device_id=device_id, sampled_at=sampled_at, interface_states=interface_states)


def _iface(name: str, state: LinkState) -> tuple[InterfaceState, ...]:
    return (InterfaceState(name=name, oper_state=state),)


def _bgp_sample(
    device_id: str = "spine-01",
    sampled_at: datetime = OBSERVED_AT,
    bgp_sessions: tuple[BgpSession, ...] = (),
) -> TelemetrySample:
    return _sample(device_id=device_id, sampled_at=sampled_at, bgp_sessions=bgp_sessions)


def _bgp(neighbor_ip: str, state: BgpState) -> tuple[BgpSession, ...]:
    return (BgpSession(neighbor_ip=neighbor_ip, state=state),)


# --- Pre-existing Gate B contract (statelessness/UTC/immutability/determinism) ---


def test_rule_engine__empty_recent_samples__returns_empty_list() -> None:
    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=[])

    assert result == []


def test_rule_engine__non_empty_non_triggering_samples__returns_empty_list() -> None:
    samples = [_sample(), _sample(sampled_at=OBSERVED_AT + timedelta(seconds=30))]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__naive_observed_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="observed_at"):
        RuleEngine.evaluate(observed_at=datetime(2026, 7, 18, 10, 0, 0), recent_samples=[])


def test_rule_engine__non_utc_offset_observed_at__raises_value_error() -> None:
    with pytest.raises(ValueError, match="observed_at"):
        RuleEngine.evaluate(
            observed_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))),
            recent_samples=[],
        )


def test_rule_engine__does_not_reorder_input_list() -> None:
    first = _sample(sampled_at=OBSERVED_AT + timedelta(seconds=60))
    second = _sample(sampled_at=OBSERVED_AT)
    samples = [first, second]

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert samples == [first, second]


def test_rule_engine__does_not_mutate_input_samples() -> None:
    sample = _sample()
    samples = [sample]

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert samples == [sample]
    assert samples[0] is sample


def test_rule_engine__repeated_evaluation_non_triggering__returns_equal_empty_lists() -> None:
    samples = [_sample()]

    first = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)
    second = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert first == second == []


def test_rule_engine__separate_calls__do_not_retain_state_from_prior_calls() -> None:
    triggering_samples = [_sample(cpu_utilization_pct=99.0) for _ in range(5)]
    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=triggering_samples)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=[])

    assert result == []


# --- RULE-CPU-HIGH: threshold ------------------------------------------------


def test_rule_engine__cpu_high__one_sample_above_90__does_not_trigger() -> None:
    samples = [_cpu_sample(cpu_utilization_pct=95.0)]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__cpu_high__two_samples_exactly_at_90__does_not_trigger() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=90.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=90.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__cpu_high__90_then_90_01__does_not_trigger() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=90.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=90.01),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__cpu_high__90_01_then_90_01__triggers() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=90.01),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=90.01),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1
    assert result[0].rule_id == RuleId.CPU_HIGH


# --- RULE-CPU-HIGH: consecutiveness / latest-pair semantics -------------------


def test_rule_engine__cpu_high__high_then_normal__does_not_trigger() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=50.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__cpu_high__normal_then_high__does_not_trigger() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=50.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=95.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__cpu_high__high_normal_high__does_not_trigger() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=50.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=20), cpu_utilization_pct=95.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__cpu_high__three_high_samples__produces_exactly_one_anomaly() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=91.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=92.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=20), cpu_utilization_pct=93.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1


def test_rule_engine__cpu_high__three_high_samples__evidence_uses_latest_two() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=91.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=92.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=20), cpu_utilization_pct=93.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    evidence = result[0].evidence
    assert [item.cpu_utilization_pct for item in evidence.samples] == [92.0, 93.0]
    assert [item.timestamp for item in evidence.samples] == [
        OBSERVED_AT + timedelta(seconds=10),
        OBSERVED_AT + timedelta(seconds=20),
    ]


def test_rule_engine__cpu_high__older_qualifying_pair_then_normal_latest__does_not_trigger() -> (
    None
):
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=96.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=20), cpu_utilization_pct=50.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-CPU-HIGH: evidence / anomaly field mapping ---------------------------


def test_rule_engine__cpu_high__evidence_preserves_exact_timestamps() -> None:
    first_time = OBSERVED_AT
    second_time = OBSERVED_AT + timedelta(seconds=15)
    samples = [
        _cpu_sample(sampled_at=first_time, cpu_utilization_pct=91.0),
        _cpu_sample(sampled_at=second_time, cpu_utilization_pct=92.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [item.timestamp for item in result[0].evidence.samples] == [first_time, second_time]


def test_rule_engine__cpu_high__evidence_preserves_exact_cpu_values() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=91.25),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=97.75),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [item.cpu_utilization_pct for item in result[0].evidence.samples] == [91.25, 97.75]


def test_rule_engine__cpu_high__anomaly_detected_at_equals_observed_at() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=91.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=92.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].detected_at == OBSERVED_AT


def test_rule_engine__cpu_high__anomaly_rule_id_is_cpu_high() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=91.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=92.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].rule_id == RuleId.CPU_HIGH


# --- RULE-CPU-HIGH: multi-device isolation and ordering ------------------------


def test_rule_engine__cpu_high__different_devices_are_never_combined() -> None:
    samples = [
        _cpu_sample(device_id="spine-01", sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(
            device_id="leaf-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=96.0,
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__cpu_high__two_devices_trigger_independently() -> None:
    samples = [
        _cpu_sample(device_id="spine-01", sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(
            device_id="spine-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=96.0,
        ),
        _cpu_sample(device_id="leaf-01", sampled_at=OBSERVED_AT, cpu_utilization_pct=97.0),
        _cpu_sample(
            device_id="leaf-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=98.0,
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert {anomaly.device_id for anomaly in result} == {"spine-01", "leaf-01"}


def test_rule_engine__cpu_high__multi_device_order_follows_first_encounter_order() -> None:
    samples = [
        _cpu_sample(device_id="leaf-01", sampled_at=OBSERVED_AT, cpu_utilization_pct=96.0),
        _cpu_sample(device_id="spine-01", sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(
            device_id="leaf-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=97.0,
        ),
        _cpu_sample(
            device_id="spine-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=98.0,
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [anomaly.device_id for anomaly in result] == ["leaf-01", "spine-01"]


# --- RULE-CPU-HIGH: ordering / duplicate-timestamp neutrality -----------------


def test_rule_engine__cpu_high__uses_caller_order_without_sorting_by_sampled_at() -> None:
    later_timestamp_first = _cpu_sample(
        sampled_at=OBSERVED_AT + timedelta(seconds=60), cpu_utilization_pct=95.0
    )
    earlier_timestamp_second = _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=96.0)
    samples = [later_timestamp_first, earlier_timestamp_second]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1
    assert [item.timestamp for item in result[0].evidence.samples] == [
        OBSERVED_AT + timedelta(seconds=60),
        OBSERVED_AT,
    ]


def test_rule_engine__cpu_high__duplicate_timestamps_accepted_and_uninterpreted() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=96.0),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1
    assert [item.timestamp for item in result[0].evidence.samples] == [OBSERVED_AT, OBSERVED_AT]


# --- RULE-CPU-HIGH: input non-mutation / determinism / statelessness -----------


def test_rule_engine__cpu_high__does_not_reorder_input_list() -> None:
    first = _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0)
    second = _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=96.0)
    samples = [first, second]

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert samples == [first, second]


def test_rule_engine__cpu_high__does_not_mutate_telemetry_sample_objects() -> None:
    sample = _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0)
    other = _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=96.0)
    samples = [sample, other]

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert samples == [sample, other]
    assert samples[0] is sample
    assert samples[1] is other


def test_rule_engine__cpu_high__repeated_evaluation_triggering__returns_equal_results() -> None:
    samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=96.0),
    ]

    first = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)
    second = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert first == second
    assert len(first) == 1


def test_rule_engine__cpu_high__prior_triggering_call_does_not_affect_later_call() -> None:
    triggering_samples = [
        _cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=95.0),
        _cpu_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), cpu_utilization_pct=96.0),
    ]
    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=triggering_samples)

    non_triggering_samples = [_cpu_sample(sampled_at=OBSERVED_AT, cpu_utilization_pct=50.0)]
    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=non_triggering_samples)

    assert result == []


# --- RULE-LINK-FLAP: no/insufficient observations ------------------------------


def test_rule_engine__link_flap__no_interface_observations__no_anomaly() -> None:
    samples = [_link_sample(interface_states=())]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__link_flap__one_initial_observation__no_transition() -> None:
    samples = [_link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP))]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__link_flap__one_state_change__no_anomaly() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__link_flap__three_transitions_within_60s__does_not_trigger() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__link_flap__initial_state_does_not_count_as_transition() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.DOWN)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__link_flap__repeated_identical_states__no_transitions() -> None:
    samples = [
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=offset),
            interface_states=_iface("Eth1", LinkState.UP),
        )
        for offset in (0, 10, 20, 30)
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-LINK-FLAP: window boundary --------------------------------------------


def test_rule_engine__link_flap__four_transitions_within_59_seconds__triggers() -> None:
    samples = [
        _link_sample(
            sampled_at=OBSERVED_AT - timedelta(seconds=100),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.DOWN)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=59),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1
    assert result[0].rule_id == RuleId.LINK_FLAP


def test_rule_engine__link_flap__four_transitions_spanning_exactly_60_seconds__triggers() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.DOWN)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=60),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1


def test_rule_engine__link_flap__four_transitions_over_60_seconds__does_not_trigger() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.DOWN)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=61),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-LINK-FLAP: AC-08 worked example ---------------------------------------


def test_rule_engine__link_flap__up_down_up_down_up_sequence__produces_four_transitions() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1
    assert len(result[0].evidence.transitions) == 4


def test_rule_engine__link_flap__evidence_timestamps_and_states_match_new_state_samples() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    transitions = result[0].evidence.transitions
    assert [t.timestamp for t in transitions] == [
        OBSERVED_AT + timedelta(seconds=10),
        OBSERVED_AT + timedelta(seconds=20),
        OBSERVED_AT + timedelta(seconds=30),
        OBSERVED_AT + timedelta(seconds=40),
    ]
    assert [t.oper_state for t in transitions] == [
        LinkState.DOWN,
        LinkState.UP,
        LinkState.DOWN,
        LinkState.UP,
    ]


def test_rule_engine__link_flap__anomaly_detected_at_equals_observed_at() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].detected_at == OBSERVED_AT


def test_rule_engine__link_flap__anomaly_rule_id_is_link_flap() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].rule_id == RuleId.LINK_FLAP


# --- RULE-LINK-FLAP: five transitions / latest-four semantics ------------------


def test_rule_engine__link_flap__five_transitions__produces_exactly_one_anomaly() -> None:
    states = [
        LinkState.UP,
        LinkState.DOWN,
        LinkState.UP,
        LinkState.DOWN,
        LinkState.UP,
        LinkState.DOWN,
    ]
    samples = [
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=offset * 10),
            interface_states=_iface("Eth1", state),
        )
        for offset, state in enumerate(states)
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1


def test_rule_engine__link_flap__five_transitions__evidence_uses_only_latest_four() -> None:
    states = [
        LinkState.UP,
        LinkState.DOWN,
        LinkState.UP,
        LinkState.DOWN,
        LinkState.UP,
        LinkState.DOWN,
    ]
    samples = [
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=offset * 10),
            interface_states=_iface("Eth1", state),
        )
        for offset, state in enumerate(states)
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    transitions = result[0].evidence.transitions
    assert [t.timestamp for t in transitions] == [
        OBSERVED_AT + timedelta(seconds=20),
        OBSERVED_AT + timedelta(seconds=30),
        OBSERVED_AT + timedelta(seconds=40),
        OBSERVED_AT + timedelta(seconds=50),
    ]
    assert [t.oper_state for t in transitions] == [
        LinkState.UP,
        LinkState.DOWN,
        LinkState.UP,
        LinkState.DOWN,
    ]


# --- RULE-LINK-FLAP: resource isolation and multi-resource ordering ------------


def test_rule_engine__link_flap__different_interfaces_are_never_combined() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=5),
            interface_states=_iface("Eth2", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=15),
            interface_states=_iface("Eth2", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=25),
            interface_states=_iface("Eth2", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__link_flap__different_devices_are_never_combined() -> None:
    samples = [
        _link_sample(
            device_id="spine-01",
            sampled_at=OBSERVED_AT,
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            device_id="leaf-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=5),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            device_id="spine-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            device_id="leaf-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=15),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            device_id="spine-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            device_id="leaf-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=25),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def _four_transition_samples(
    device_id: str, interface_name: str, start_offset: int
) -> list[TelemetrySample]:
    states = [LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN, LinkState.UP]
    return [
        _link_sample(
            device_id=device_id,
            sampled_at=OBSERVED_AT + timedelta(seconds=start_offset + index * 10),
            interface_states=_iface(interface_name, state),
        )
        for index, state in enumerate(states)
    ]


def test_rule_engine__link_flap__two_interfaces_trigger_independently() -> None:
    samples = _four_transition_samples("spine-01", "Eth1", 0) + _four_transition_samples(
        "spine-01", "Eth2", 0
    )

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert {a.evidence.interface_name for a in result} == {"Eth1", "Eth2"}


def test_rule_engine__link_flap__two_devices_trigger_independently() -> None:
    samples = _four_transition_samples("spine-01", "Eth1", 0) + _four_transition_samples(
        "leaf-01", "Eth1", 0
    )

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert {a.device_id for a in result} == {"spine-01", "leaf-01"}


def test_rule_engine__link_flap__output_order_follows_first_resource_encounter_order() -> None:
    eth2_samples = _four_transition_samples("spine-01", "Eth2", 0)
    eth1_samples = _four_transition_samples("spine-01", "Eth1", 1)
    samples = eth2_samples + eth1_samples

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [a.evidence.interface_name for a in result] == ["Eth2", "Eth1"]


def test_rule_engine__cpu_high_anomalies_remain_before_link_flap_anomalies() -> None:
    samples = [
        _sample(
            sampled_at=OBSERVED_AT,
            cpu_utilization_pct=50.0,
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=50.0,
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            cpu_utilization_pct=50.0,
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            cpu_utilization_pct=95.0,
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            cpu_utilization_pct=96.0,
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert result[0].rule_id == RuleId.CPU_HIGH
    assert result[1].rule_id == RuleId.LINK_FLAP


def test_rule_engine__link_flap__missing_intermediate_sample__no_inferred_transition() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), interface_states=()),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(sampled_at=OBSERVED_AT + timedelta(seconds=30), interface_states=()),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-LINK-FLAP: ordering / duplicate / out-of-order timestamp behavior ----


def test_rule_engine__link_flap__duplicate_transition_timestamps_accepted() -> None:
    samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.DOWN)),
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.DOWN)),
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP)),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1


def test_rule_engine__link_flap__out_of_order_transition_timestamps__does_not_trigger() -> None:
    samples = [
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=100),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=50),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=80),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            interface_states=_iface("Eth1", LinkState.DOWN),
        ),
        _link_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=90),
            interface_states=_iface("Eth1", LinkState.UP),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-LINK-FLAP: input non-mutation / determinism / statelessness ----------


def test_rule_engine__link_flap__caller_provided_sample_order_remains_unchanged() -> None:
    samples = _four_transition_samples("spine-01", "Eth1", 0)
    original = list(samples)

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert samples == original


def test_rule_engine__link_flap__does_not_mutate_telemetry_sample_objects() -> None:
    samples = _four_transition_samples("spine-01", "Eth1", 0)
    identities = [id(sample) for sample in samples]

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [id(sample) for sample in samples] == identities


def test_rule_engine__link_flap__repeated_evaluation_triggering__returns_equal_results() -> None:
    samples = _four_transition_samples("spine-01", "Eth1", 0)

    first = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)
    second = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert first == second
    assert len(first) == 1


def test_rule_engine__link_flap__prior_triggering_call_does_not_affect_later_call() -> None:
    triggering_samples = _four_transition_samples("spine-01", "Eth1", 0)
    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=triggering_samples)

    non_triggering_samples = [
        _link_sample(sampled_at=OBSERVED_AT, interface_states=_iface("Eth1", LinkState.UP))
    ]
    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=non_triggering_samples)

    assert result == []


# --- RULE-BGP-DOWN: no/insufficient observations --------------------------------


def test_rule_engine__bgp_down__no_bgp_observations__no_anomaly() -> None:
    samples = [_bgp_sample(bgp_sessions=())]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__one_initial_established_observation__no_anomaly() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED))
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__one_initial_idle_observation__no_anomaly() -> None:
    samples = [_bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE))]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__one_initial_active_observation__no_anomaly() -> None:
    samples = [_bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ACTIVE))]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-BGP-DOWN: qualifying non-down -> down transitions ---------------------


def _bgp_transition_samples(
    previous_state: BgpState, current_state: BgpState
) -> list[TelemetrySample]:
    return [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", previous_state)),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", current_state),
        ),
    ]


@pytest.mark.parametrize(
    "previous_state",
    [BgpState.ESTABLISHED, BgpState.CONNECT, BgpState.OPEN_SENT, BgpState.OPEN_CONFIRM],
)
@pytest.mark.parametrize("current_state", [BgpState.IDLE, BgpState.ACTIVE])
def test_rule_engine__bgp_down__non_down_to_down_transition__triggers(
    previous_state: BgpState, current_state: BgpState
) -> None:
    samples = _bgp_transition_samples(previous_state, current_state)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1
    assert result[0].rule_id == RuleId.BGP_DOWN


# --- RULE-BGP-DOWN: non-triggering transitions -----------------------------------


def test_rule_engine__bgp_down__idle_to_active__does_not_trigger() -> None:
    samples = _bgp_transition_samples(BgpState.IDLE, BgpState.ACTIVE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__active_to_idle__does_not_trigger() -> None:
    samples = _bgp_transition_samples(BgpState.ACTIVE, BgpState.IDLE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__idle_to_idle__does_not_trigger() -> None:
    samples = _bgp_transition_samples(BgpState.IDLE, BgpState.IDLE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__active_to_active__does_not_trigger() -> None:
    samples = _bgp_transition_samples(BgpState.ACTIVE, BgpState.ACTIVE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__established_to_established__does_not_trigger() -> None:
    samples = _bgp_transition_samples(BgpState.ESTABLISHED, BgpState.ESTABLISHED)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__non_down_to_non_down__does_not_trigger() -> None:
    samples = _bgp_transition_samples(BgpState.CONNECT, BgpState.OPEN_SENT)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__repeated_established_observations__does_not_trigger() -> None:
    samples = [
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=offset),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        )
        for offset in (0, 10, 20)
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-BGP-DOWN: latest-pair / stale-condition semantics ---------------------


def test_rule_engine__bgp_down__older_established_to_idle_then_recovery__does_not_trigger() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__older_established_to_idle_then_active__does_not_trigger() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ACTIVE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__recovery_then_new_down_transition__triggers_once() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ACTIVE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1
    assert result[0].evidence.previous_state == BgpState.ESTABLISHED
    assert result[0].evidence.state == BgpState.ACTIVE


def test_rule_engine__bgp_down__three_or_more_observations__at_most_one_anomaly() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", BgpState.CONNECT),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1


def test_rule_engine__bgp_down__evidence_uses_only_latest_qualifying_pair() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ACTIVE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].evidence.previous_state == BgpState.ESTABLISHED
    assert result[0].evidence.state == BgpState.ACTIVE


# --- RULE-BGP-DOWN: evidence / anomaly field mapping -----------------------------


def test_rule_engine__bgp_down__evidence_neighbor_ip_is_exact() -> None:
    samples = _bgp_transition_samples(BgpState.ESTABLISHED, BgpState.IDLE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].evidence.neighbor_ip == "10.0.0.1"


def test_rule_engine__bgp_down__evidence_previous_state_is_exact() -> None:
    samples = _bgp_transition_samples(BgpState.OPEN_CONFIRM, BgpState.IDLE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].evidence.previous_state == BgpState.OPEN_CONFIRM


def test_rule_engine__bgp_down__evidence_state_is_exact() -> None:
    samples = _bgp_transition_samples(BgpState.OPEN_CONFIRM, BgpState.ACTIVE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].evidence.state == BgpState.ACTIVE


def test_rule_engine__bgp_down__anomaly_detected_at_equals_observed_at() -> None:
    samples = _bgp_transition_samples(BgpState.ESTABLISHED, BgpState.IDLE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].detected_at == OBSERVED_AT


def test_rule_engine__bgp_down__anomaly_rule_id_is_bgp_down() -> None:
    samples = _bgp_transition_samples(BgpState.ESTABLISHED, BgpState.IDLE)

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result[0].rule_id == RuleId.BGP_DOWN


# --- RULE-BGP-DOWN: resource isolation and multi-resource ordering --------------


def test_rule_engine__bgp_down__different_neighbors_are_never_combined() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=5),
            bgp_sessions=_bgp("10.0.0.2", BgpState.ESTABLISHED),
        ),
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__different_devices_are_never_combined() -> None:
    samples = [
        _bgp_sample(
            device_id="spine-01",
            sampled_at=OBSERVED_AT,
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _bgp_sample(
            device_id="leaf-01",
            sampled_at=OBSERVED_AT + timedelta(seconds=5),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def _bgp_down_samples(device_id: str, neighbor_ip: str, start_offset: int) -> list[TelemetrySample]:
    return [
        _bgp_sample(
            device_id=device_id,
            sampled_at=OBSERVED_AT + timedelta(seconds=start_offset),
            bgp_sessions=_bgp(neighbor_ip, BgpState.ESTABLISHED),
        ),
        _bgp_sample(
            device_id=device_id,
            sampled_at=OBSERVED_AT + timedelta(seconds=start_offset + 10),
            bgp_sessions=_bgp(neighbor_ip, BgpState.IDLE),
        ),
    ]


def test_rule_engine__bgp_down__two_neighbors_trigger_independently() -> None:
    samples = _bgp_down_samples("spine-01", "10.0.0.1", 0) + _bgp_down_samples(
        "spine-01", "10.0.0.2", 0
    )

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert {a.evidence.neighbor_ip for a in result} == {"10.0.0.1", "10.0.0.2"}


def test_rule_engine__bgp_down__two_devices_trigger_independently() -> None:
    samples = _bgp_down_samples("spine-01", "10.0.0.1", 0) + _bgp_down_samples(
        "leaf-01", "10.0.0.1", 0
    )

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert {a.device_id for a in result} == {"spine-01", "leaf-01"}


def test_rule_engine__bgp_down__output_order_follows_first_resource_encounter_order() -> None:
    second_neighbor_samples = _bgp_down_samples("spine-01", "10.0.0.2", 0)
    first_neighbor_samples = _bgp_down_samples("spine-01", "10.0.0.1", 1)
    samples = second_neighbor_samples + first_neighbor_samples

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [a.evidence.neighbor_ip for a in result] == ["10.0.0.2", "10.0.0.1"]


# --- CPU / link-flap / BGP-down combined ordering --------------------------------


def test_rule_engine__cpu_anomalies_remain_before_bgp_down_anomalies() -> None:
    samples = [
        _sample(
            sampled_at=OBSERVED_AT,
            cpu_utilization_pct=95.0,
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=96.0,
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert result[0].rule_id == RuleId.CPU_HIGH
    assert result[1].rule_id == RuleId.BGP_DOWN


def test_rule_engine__link_flap_anomalies_remain_before_bgp_down_anomalies() -> None:
    samples = [
        _sample(
            sampled_at=OBSERVED_AT,
            interface_states=_iface("Eth1", LinkState.UP),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            interface_states=_iface("Eth1", LinkState.DOWN),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            interface_states=_iface("Eth1", LinkState.UP),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            interface_states=_iface("Eth1", LinkState.DOWN),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            interface_states=_iface("Eth1", LinkState.UP),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 2
    assert result[0].rule_id == RuleId.LINK_FLAP
    assert result[1].rule_id == RuleId.BGP_DOWN


def test_rule_engine__cpu_link_flap_bgp_down_ordering_is_deterministic_when_all_trigger() -> None:
    samples = [
        _sample(
            sampled_at=OBSERVED_AT,
            cpu_utilization_pct=50.0,
            interface_states=_iface("Eth1", LinkState.UP),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            cpu_utilization_pct=50.0,
            interface_states=_iface("Eth1", LinkState.DOWN),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=20),
            cpu_utilization_pct=50.0,
            interface_states=_iface("Eth1", LinkState.UP),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=30),
            cpu_utilization_pct=95.0,
            interface_states=_iface("Eth1", LinkState.DOWN),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=40),
            cpu_utilization_pct=96.0,
            interface_states=_iface("Eth1", LinkState.UP),
            bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE),
        ),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [a.rule_id for a in result] == [RuleId.CPU_HIGH, RuleId.LINK_FLAP, RuleId.BGP_DOWN]


# --- RULE-BGP-DOWN: missing neighbor / ordering / duplicate-timestamp behavior ---


def test_rule_engine__bgp_down__missing_intermediate_sample__no_inferred_transition() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(sampled_at=OBSERVED_AT + timedelta(seconds=10), bgp_sessions=()),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


def test_rule_engine__bgp_down__duplicate_timestamps_accepted() -> None:
    samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED)),
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE)),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert len(result) == 1


def test_rule_engine__bgp_down__out_of_order_latest_pair_timestamps__does_not_trigger() -> None:
    samples = [
        _bgp_sample(
            sampled_at=OBSERVED_AT + timedelta(seconds=10),
            bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED),
        ),
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.IDLE)),
    ]

    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert result == []


# --- RULE-BGP-DOWN: input non-mutation / determinism / statelessness ------------


def test_rule_engine__bgp_down__caller_provided_sample_order_remains_unchanged() -> None:
    samples = _bgp_down_samples("spine-01", "10.0.0.1", 0)
    original = list(samples)

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert samples == original


def test_rule_engine__bgp_down__does_not_mutate_telemetry_sample_objects() -> None:
    samples = _bgp_down_samples("spine-01", "10.0.0.1", 0)
    identities = [id(sample) for sample in samples]

    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert [id(sample) for sample in samples] == identities


def test_rule_engine__bgp_down__repeated_evaluation_triggering__returns_equal_results() -> None:
    samples = _bgp_down_samples("spine-01", "10.0.0.1", 0)

    first = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)
    second = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=samples)

    assert first == second
    assert len(first) == 1


def test_rule_engine__bgp_down__prior_triggering_call_does_not_affect_later_call() -> None:
    triggering_samples = _bgp_down_samples("spine-01", "10.0.0.1", 0)
    RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=triggering_samples)

    non_triggering_samples = [
        _bgp_sample(sampled_at=OBSERVED_AT, bgp_sessions=_bgp("10.0.0.1", BgpState.ESTABLISHED))
    ]
    result = RuleEngine.evaluate(observed_at=OBSERVED_AT, recent_samples=non_triggering_samples)

    assert result == []
