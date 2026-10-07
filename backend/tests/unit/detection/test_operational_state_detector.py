from datetime import UTC, datetime
from pathlib import Path

import pytest

from meta_rne.adapters.containerlab_frr.parsers import parse_bgp_summary
from meta_rne.detection.anomaly_incident_mapper import AnomalyIncidentMapper
from meta_rne.detection.operational_state_detector import (
    LAB1_NODE_DEVICE_IDS,
    OperationalStateDetector,
)
from meta_rne.domain.anomaly import BgpDownEvidence, RuleId
from meta_rne.domain.incident import IncidentSource
from meta_rne.domain.operational_state import (
    BgpNeighborObservation,
    BgpObservation,
    BgpSessionState,
    CollectionSource,
    FabricOperationalState,
    NodeRole,
    NormalizedOperationalState,
    ObservationFacet,
    UnavailableObservation,
)
from meta_rne.domain.policy import Severity
from meta_rne.domain.telemetry import BgpState

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
SOURCE = CollectionSource(collector="test", lab_name="lab1", collection_id="run-1")
_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "containerlab_frr"
ROUTERS = ("spine-1", "spine-2", "leaf-1", "leaf-2")


def _router(node_id: str, bgp: BgpObservation | None) -> NormalizedOperationalState:
    unavailable = (
        (UnavailableObservation(ObservationFacet.BGP, "collection failed"),) if bgp is None else ()
    )
    return NormalizedOperationalState(
        node_id=node_id,
        role=NodeRole.ROUTER,
        collected_at=NOW,
        source=SOURCE,
        interfaces=(),
        bgp=bgp,
        routes=(),
        reachability=(),
        unavailable=unavailable,
    )


def _host(node_id: str) -> NormalizedOperationalState:
    return NormalizedOperationalState(
        node_id=node_id,
        role=NodeRole.HOST,
        collected_at=NOW,
        source=SOURCE,
        interfaces=(),
        bgp=None,
        routes=(),
        reachability=(),
    )


def _fabric(*nodes: NormalizedOperationalState) -> FabricOperationalState:
    return FabricOperationalState(collection_id="run-1", collected_at=NOW, nodes=nodes)


def _fixture_fabric(fixture_set: str, routers: tuple[str, ...] = ROUTERS) -> FabricOperationalState:
    nodes = tuple(
        _router(
            node,
            parse_bgp_summary(
                (_ROOT / fixture_set / node / "show_bgp_summary.json").read_text(encoding="utf-8")
            ),
        )
        for node in routers
    )
    return _fabric(*nodes, _host("host-1"), _host("host-2"))


def _single_neighbor_fabric(state: BgpSessionState) -> FabricOperationalState:
    neighbor = BgpNeighborObservation(
        neighbor_ip="10.255.0.0",
        remote_as=65000,
        state=state,
        raw_state=state.value,
        prefixes_received=None,
        uptime_ms=None,
    )
    bgp = BgpObservation(local_as=65001, router_id="1.1.1.1", neighbors=(neighbor,))
    return _fabric(_router("leaf-1", bgp))


def _summary(fabric: FabricOperationalState) -> set[tuple[str, str, BgpState]]:
    result = set()
    for anomaly in OperationalStateDetector.detect(fabric):
        assert isinstance(anomaly.evidence, BgpDownEvidence)
        result.add((anomaly.device_id, anomaly.evidence.neighbor_ip, anomaly.evidence.state))
    return result


def test_baseline_fixture__no_anomalies() -> None:
    assert OperationalStateDetector.detect(_fixture_fabric("baseline")) == ()


@pytest.mark.parametrize(
    ("fixture_set", "routers", "leaf_state", "spine_state"),
    [
        ("degraded-active", ROUTERS, BgpState.ACTIVE, BgpState.ACTIVE),
        # Captured mid-convergence: the two ends of one link report different
        # down-family states (leaf-1 Idle, spine-1 Active).
        ("degraded-idle", ("leaf-1", "spine-1"), BgpState.IDLE, BgpState.ACTIVE),
    ],
)
def test_degraded_fixtures__two_device_scoped_anomalies(
    fixture_set: str,
    routers: tuple[str, ...],
    leaf_state: BgpState,
    spine_state: BgpState,
) -> None:
    fabric = _fixture_fabric(fixture_set, routers)

    anomalies = OperationalStateDetector.detect(fabric)

    assert len(anomalies) == 2
    assert _summary(fabric) == {
        ("lab1-leaf-1", "10.255.0.0", leaf_state),
        ("lab1-spine-1", "10.255.0.1", spine_state),
    }
    for anomaly in anomalies:
        assert anomaly.rule_id is RuleId.BGP_DOWN
        assert anomaly.detected_at == NOW
        assert isinstance(anomaly.evidence, BgpDownEvidence)
        assert anomaly.evidence.previous_state is None


def test_degraded_clearing_fixture__unknown_side_is_not_failure() -> None:
    # leaf-1 reports the failed neighbor as Clearing (normalized UNKNOWN):
    # no anomaly from that side. spine-1's side of the same link is a real
    # Idle in this capture, so exactly that one anomaly is produced.
    fabric = _fixture_fabric("degraded-clearing", ("leaf-1", "spine-1"))
    leaf_states = {n.state for n in fabric.node("leaf-1").bgp.neighbors}  # type: ignore[union-attr]
    assert BgpSessionState.UNKNOWN in leaf_states

    assert _summary(fabric) == {("lab1-spine-1", "10.255.0.1", BgpState.IDLE)}
    assert _summary(_fabric(fabric.node("leaf-1"), _host("host-1"))) == set()


def test_unavailable_bgp_facet__no_anomalies() -> None:
    assert OperationalStateDetector.detect(_fabric(_router("leaf-1", None))) == ()


def test_hosts__no_anomalies() -> None:
    assert OperationalStateDetector.detect(_fabric(_host("host-1"), _host("host-2"))) == ()


@pytest.mark.parametrize(
    "state",
    [
        BgpSessionState.ESTABLISHED,
        BgpSessionState.CONNECT,
        BgpSessionState.OPEN_SENT,
        BgpSessionState.OPEN_CONFIRM,
        BgpSessionState.UNKNOWN,
    ],
)
def test_non_failure_states__no_anomalies(state: BgpSessionState) -> None:
    assert OperationalStateDetector.detect(_single_neighbor_fabric(state)) == ()


@pytest.mark.parametrize(
    ("state", "expected"),
    [(BgpSessionState.IDLE, BgpState.IDLE), (BgpSessionState.ACTIVE, BgpState.ACTIVE)],
)
def test_failure_states__one_anomaly_with_actual_state(
    state: BgpSessionState, expected: BgpState
) -> None:
    assert _summary(_single_neighbor_fabric(state)) == {("lab1-leaf-1", "10.255.0.0", expected)}


def test_fixed_node_mapping() -> None:
    assert LAB1_NODE_DEVICE_IDS == {
        "spine-1": "lab1-spine-1",
        "spine-2": "lab1-spine-2",
        "leaf-1": "lab1-leaf-1",
        "leaf-2": "lab1-leaf-2",
    }


def test_unknown_router_node__fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown router node"):
        OperationalStateDetector.detect(_fabric(_router("leaf-9", None)))


def test_repeated_degraded__same_anomalies_again_no_dedup() -> None:
    fabric = _fixture_fabric("degraded-active")
    first = OperationalStateDetector.detect(fabric)
    second = OperationalStateDetector.detect(fabric)
    assert len(first) == 2
    assert first == second


def test_recovery_snapshot__zero_anomalies() -> None:
    assert len(OperationalStateDetector.detect(_fixture_fabric("degraded-active"))) == 2
    assert OperationalStateDetector.detect(_fixture_fabric("baseline")) == ()


def test_operational_anomalies_map_to_bgp_neighbor_incident_candidates() -> None:
    anomalies = OperationalStateDetector.detect(_fixture_fabric("degraded-active"))
    candidates = {a.device_id: AnomalyIncidentMapper.build_candidate(a) for a in anomalies}

    assert candidates["lab1-leaf-1"].affected_resource == "bgp-neighbor:10.255.0.0"
    assert candidates["lab1-spine-1"].affected_resource == "bgp-neighbor:10.255.0.1"
    for candidate in candidates.values():
        assert candidate.source is IncidentSource.ANOMALY
        assert candidate.rule_ref == "RULE-BGP-DOWN"
        assert candidate.severity is Severity.CRITICAL
        assert isinstance(candidate.evidence, BgpDownEvidence)
        assert candidate.evidence.previous_state is None
        assert candidate.observed_at == NOW
