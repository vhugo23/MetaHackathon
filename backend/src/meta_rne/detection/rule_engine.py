"""Deterministic telemetry anomaly detection (FR-06).

Pure domain/detection logic: plain `TelemetrySample` inputs in, a list of
`Anomaly` out, no I/O, no clock access, no repository access. See
docs/domain-model.md Section 17's `RuleEngine` port signature.

RULE-CPU-HIGH is implemented (Gate C): CPU utilization strictly above 90.0
on the latest two samples for a device, in caller-provided encounter order
(never sorted by `sampled_at`, never combined across devices, no maximum
time gap enforced — undocumented and therefore not invented). Only the
latest two samples per device are ever inspected, so a stale older
qualifying pair produces no anomaly once the latest sample is normal.

RULE-LINK-FLAP is implemented (Gate D1): a transition is a state change
between two consecutive *observations* of the same (device_id,
interface_name) — the first observed state is initial only, never a
transition; a sample that omits an interface simply contributes no
observation for it (never inferred as "down"). Four or more transitions
are required; only the latest four transitions per resource are ever
inspected; the 60-second window is inclusive; a four-transition candidate
must have nondecreasing transition timestamps in caller-provided encounter
order to qualify. `evaluate`'s output is CPU anomalies first, then
link-flap anomalies in first-(device, interface)-encounter order.

RULE-BGP-DOWN is implemented (Gate D2): down-family states are exactly
Idle/Active; non-down predecessor states are exactly Established/Connect/
OpenSent/OpenConfirm. A trigger requires the latest two *observations*
for the same (device_id, neighbor_ip) to be a non-down-family state
followed by a down-family state — Idle<->Active, repeated down-family
states, and any non-down-to-non-down transition never trigger. Only the
latest two observations per resource are ever inspected, so a stale older
qualifying pair produces no anomaly once the resource has recovered or
moved to another down-family state. A neighbor absent from a sample
contributes no observation (never inferred as any state). The latest
pair must have nondecreasing `sampled_at` timestamps to qualify.
`evaluate`'s output is CPU anomalies, then link-flap anomalies, then
BGP-down anomalies (each in first-resource-encounter order).
"""

from datetime import UTC, datetime, timedelta

from meta_rne.domain.anomaly import (
    Anomaly,
    BgpDownEvidence,
    CpuHighEvidence,
    CpuSampleEvidence,
    InterfaceTransitionEvidence,
    LinkFlapEvidence,
    RuleId,
)
from meta_rne.domain.telemetry import BgpState, LinkState, TelemetrySample

_CPU_HIGH_THRESHOLD_PCT = 90.0
_LINK_FLAP_TRANSITION_COUNT = 4
_LINK_FLAP_WINDOW = timedelta(seconds=60)
_BGP_DOWN_FAMILY_STATES = frozenset({BgpState.IDLE, BgpState.ACTIVE})
_BGP_NON_DOWN_STATES = frozenset(
    {BgpState.ESTABLISHED, BgpState.CONNECT, BgpState.OPEN_SENT, BgpState.OPEN_CONFIRM}
)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


def _samples_by_device_in_encounter_order(
    recent_samples: list[TelemetrySample],
) -> dict[str, list[TelemetrySample]]:
    by_device: dict[str, list[TelemetrySample]] = {}
    for sample in recent_samples:
        by_device.setdefault(sample.device_id, []).append(sample)
    return by_device


def _evaluate_cpu_high(
    observed_at: datetime, device_samples: list[TelemetrySample]
) -> Anomaly | None:
    if len(device_samples) < 2:
        return None

    latest_two = device_samples[-2:]
    if not all(sample.cpu_utilization_pct > _CPU_HIGH_THRESHOLD_PCT for sample in latest_two):
        return None

    return Anomaly(
        device_id=latest_two[0].device_id,
        rule_id=RuleId.CPU_HIGH,
        evidence=CpuHighEvidence(
            samples=tuple(
                CpuSampleEvidence(
                    timestamp=sample.sampled_at, cpu_utilization_pct=sample.cpu_utilization_pct
                )
                for sample in latest_two
            )
        ),
        detected_at=observed_at,
    )


def _interface_observations_by_resource(
    recent_samples: list[TelemetrySample],
) -> dict[tuple[str, str], list[tuple[datetime, LinkState]]]:
    observations: dict[tuple[str, str], list[tuple[datetime, LinkState]]] = {}
    for sample in recent_samples:
        for interface_state in sample.interface_states:
            key = (sample.device_id, interface_state.name)
            observations.setdefault(key, []).append((sample.sampled_at, interface_state.oper_state))
    return observations


def _transitions_for_resource(
    observations: list[tuple[datetime, LinkState]],
) -> list[InterfaceTransitionEvidence]:
    transitions: list[InterfaceTransitionEvidence] = []
    previous_state = observations[0][1]
    for timestamp, state in observations[1:]:
        if state != previous_state:
            transitions.append(InterfaceTransitionEvidence(timestamp=timestamp, oper_state=state))
        previous_state = state
    return transitions


def _evaluate_link_flap(
    observed_at: datetime,
    device_id: str,
    interface_name: str,
    observations: list[tuple[datetime, LinkState]],
) -> Anomaly | None:
    transitions = _transitions_for_resource(observations)
    if len(transitions) < _LINK_FLAP_TRANSITION_COUNT:
        return None

    latest_four = transitions[-_LINK_FLAP_TRANSITION_COUNT:]
    timestamps = [transition.timestamp for transition in latest_four]
    is_nondecreasing = all(
        timestamps[index] <= timestamps[index + 1] for index in range(len(timestamps) - 1)
    )
    if not is_nondecreasing:
        return None

    if timestamps[-1] - timestamps[0] > _LINK_FLAP_WINDOW:
        return None

    return Anomaly(
        device_id=device_id,
        rule_id=RuleId.LINK_FLAP,
        evidence=LinkFlapEvidence(interface_name=interface_name, transitions=tuple(latest_four)),
        detected_at=observed_at,
    )


def _bgp_observations_by_resource(
    recent_samples: list[TelemetrySample],
) -> dict[tuple[str, str], list[tuple[datetime, BgpState]]]:
    observations: dict[tuple[str, str], list[tuple[datetime, BgpState]]] = {}
    for sample in recent_samples:
        for bgp_session in sample.bgp_sessions:
            key = (sample.device_id, bgp_session.neighbor_ip)
            observations.setdefault(key, []).append((sample.sampled_at, bgp_session.state))
    return observations


def _evaluate_bgp_down(
    observed_at: datetime,
    device_id: str,
    neighbor_ip: str,
    observations: list[tuple[datetime, BgpState]],
) -> Anomaly | None:
    if len(observations) < 2:
        return None

    (previous_timestamp, previous_state), (current_timestamp, current_state) = observations[-2:]
    if current_timestamp < previous_timestamp:
        return None
    if previous_state not in _BGP_NON_DOWN_STATES:
        return None
    if current_state not in _BGP_DOWN_FAMILY_STATES:
        return None

    return Anomaly(
        device_id=device_id,
        rule_id=RuleId.BGP_DOWN,
        evidence=BgpDownEvidence(
            neighbor_ip=neighbor_ip, state=current_state, previous_state=previous_state
        ),
        detected_at=observed_at,
    )


class RuleEngine:
    """Stateless; see docs/domain-model.md Section 17."""

    @staticmethod
    def evaluate(
        observed_at: datetime,
        recent_samples: list[TelemetrySample],
    ) -> list[Anomaly]:
        _require_utc(observed_at, "observed_at")

        anomalies: list[Anomaly] = []
        for device_samples in _samples_by_device_in_encounter_order(recent_samples).values():
            cpu_anomaly = _evaluate_cpu_high(observed_at, device_samples)
            if cpu_anomaly is not None:
                anomalies.append(cpu_anomaly)

        for (device_id, interface_name), observations in _interface_observations_by_resource(
            recent_samples
        ).items():
            link_flap_anomaly = _evaluate_link_flap(
                observed_at, device_id, interface_name, observations
            )
            if link_flap_anomaly is not None:
                anomalies.append(link_flap_anomaly)

        for (device_id, neighbor_ip), bgp_observations in _bgp_observations_by_resource(
            recent_samples
        ).items():
            bgp_down_anomaly = _evaluate_bgp_down(
                observed_at, device_id, neighbor_ip, bgp_observations
            )
            if bgp_down_anomaly is not None:
                anomalies.append(bgp_down_anomaly)

        return anomalies
