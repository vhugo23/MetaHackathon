"""Pure BGP-down detection over normalized live operational state (NPE-1C3B2).

``FabricOperationalState`` in, ``tuple[Anomaly, ...]`` out: no repository,
UnitOfWork, database, subprocess, HTTP, or clock access. ``detected_at`` is
``FabricOperationalState.collected_at``.

Level-triggered and stateless. Every neighbor *observed* in ``Idle`` or
``Active`` yields one RULE-BGP-DOWN anomaly on every evaluation, so running the
detector twice on the same degraded state yields the same anomalies twice.
Deduplication belongs to the incident repository, not here. A healthy snapshot
yields no anomalies and nothing is resolved or inspected.

``BgpDownEvidence.previous_state`` is ``None``: this detector keeps no history
and never invents a predecessor. (The edge-triggered telemetry ``RuleEngine``
is unchanged and still records a real prior state.)

Only a real observed ``Idle``/``Active`` (``BGP_DOWN_STATES``, shared with
``RuleEngine``) is a failure. ``Established``/``Connect``/``OpenSent``/
``OpenConfirm`` are not, and ``UNKNOWN`` (including a normalized ``Clearing``)
or an unavailable/missing BGP facet is observation uncertainty, never failure.
Hosts carry no BGP and produce nothing.

A single physical link failure is seen from both ends, so it yields two
device-scoped anomalies (e.g. lab1-leaf-1 / 10.255.0.0 and lab1-spine-1 /
10.255.0.1). They are intentionally not correlated here; that is RCA's job.

Device identity is the fixed Lab 1 collector-node -> registered-device-ID map
below. A router node outside it fails closed with ``ValueError``: a platform
device ID is never invented.
"""

from meta_rne.domain.anomaly import Anomaly, BgpDownEvidence, RuleId
from meta_rne.domain.operational_state import (
    BgpSessionState,
    FabricOperationalState,
    NodeRole,
)
from meta_rne.domain.telemetry import BGP_DOWN_STATES, BgpState

LAB1_NODE_DEVICE_IDS: dict[str, str] = {
    "spine-1": "lab1-spine-1",
    "spine-2": "lab1-spine-2",
    "leaf-1": "lab1-leaf-1",
    "leaf-2": "lab1-leaf-2",
}


def _observed_down_state(state: BgpSessionState) -> BgpState | None:
    if state is BgpSessionState.UNKNOWN:
        return None
    telemetry_state = BgpState(state.value)
    return telemetry_state if telemetry_state in BGP_DOWN_STATES else None


class OperationalStateDetector:
    """Stateless."""

    @staticmethod
    def detect(fabric: FabricOperationalState) -> tuple[Anomaly, ...]:
        anomalies: list[Anomaly] = []
        for node in fabric.nodes:
            if node.role is not NodeRole.ROUTER:
                continue
            device_id = LAB1_NODE_DEVICE_IDS.get(node.node_id)
            if device_id is None:
                raise ValueError(
                    f"OperationalStateDetector: unknown router node {node.node_id!r}; "
                    "no registered device ID"
                )
            if node.bgp is None:
                continue
            for neighbor in node.bgp.neighbors:
                down_state = _observed_down_state(neighbor.state)
                if down_state is None:
                    continue
                anomalies.append(
                    Anomaly(
                        device_id=device_id,
                        rule_id=RuleId.BGP_DOWN,
                        evidence=BgpDownEvidence(
                            neighbor_ip=neighbor.neighbor_ip,
                            state=down_state,
                            previous_state=None,
                        ),
                        detected_at=fabric.collected_at,
                    )
                )
        return tuple(anomalies)
