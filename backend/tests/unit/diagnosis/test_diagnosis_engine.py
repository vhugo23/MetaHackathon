"""Deterministic RCA engine tests (NPE-1E1A): captured Lab 1 fixtures plus
explicit ``dataclasses.replace`` mutations. No repository, clock or I/O."""

import ast
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import meta_rne.diagnosis.engine as engine_module
from meta_rne.adapters.containerlab_frr import CommandResult, ContainerlabFrrCollector
from meta_rne.adapters.containerlab_frr.collector import CONTAINER_PREFIX
from meta_rne.detection.anomaly_incident_mapper import AnomalyIncidentMapper
from meta_rne.diagnosis import DeterministicDiagnosisEngine
from meta_rne.domain.diagnosis import (
    BgpSessionObserved,
    Diagnosis,
    DiagnosisConfidence,
    DiagnosisType,
    EcmpPathsObserved,
    EndpointBgpState,
    EndpointInterfaceState,
    EvidenceItem,
    EvidenceKind,
    EvidenceRole,
    ReachabilityObserved,
    SymptomKey,
)
from meta_rne.domain.operational_state import (
    BgpSessionState,
    FabricOperationalState,
    InterfaceOperState,
    ObservationFacet,
    UnavailableObservation,
)
from operational_state_support import with_neighbor_states

NOW = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures" / "containerlab_frr"
_FILE_FOR_COMMAND = {
    ("ip", "-j", "addr"): "ip_j_addr.json",
    ("vtysh", "-c", "show bgp summary json"): "show_bgp_summary.json",
    ("vtysh", "-c", "show ip route json"): "show_ip_route.json",
    ("ip", "-o", "link", "show"): "ip_o_link.txt",
    ("ip", "-o", "addr", "show"): "ip_o_addr.txt",
}

LINK_L1_S1 = "leaf-1:eth1--spine-1:eth1"
LINK_L2_S2 = "leaf-2:eth2--spine-2:eth2"

HIGH = DiagnosisConfidence.HIGH
MEDIUM = DiagnosisConfidence.MEDIUM
LOW = DiagnosisConfidence.LOW
PHYSICAL = DiagnosisType.PHYSICAL_LINK_FAILURE
SESSION = DiagnosisType.BGP_SESSION_FAILURE
INSUFFICIENT = DiagnosisType.INSUFFICIENT_EVIDENCE

# -- fixtures and mutations ---------------------------------------------------


def load(fixture_set: str) -> FabricOperationalState:
    """The captured set through the real collector; a node file that was not
    captured (the partial sets) is a failed command, i.e. unavailable."""
    root = _FIXTURE_ROOT / fixture_set

    def runner(container: str, command: Sequence[str]) -> CommandResult:
        node = container.removeprefix(CONTAINER_PREFIX)
        command = tuple(command)
        name = f"ping_{command[-1]}.txt" if command[0] == "ping" else _FILE_FOR_COMMAND[command]
        path = root / node / name
        if not path.exists():
            return CommandResult(1, "", "not captured")
        return CommandResult(0, path.read_text(encoding="utf-8"))

    return ContainerlabFrrCollector(runner, lambda: NOW, lambda: "run-1").collect()


def baseline() -> FabricOperationalState:
    return load("baseline")


def _map_nodes(fabric: FabricOperationalState, node_id: str, change) -> FabricOperationalState:  # type: ignore[no-untyped-def]
    return replace(
        fabric,
        nodes=tuple(change(n) if n.node_id == node_id else n for n in fabric.nodes),
    )


def set_interface(
    fabric: FabricOperationalState,
    node_id: str,
    name: str,
    state: InterfaceOperState | None = None,
    addresses: tuple[str, ...] | None = None,
) -> FabricOperationalState:
    def change(node):  # type: ignore[no-untyped-def]
        interfaces = tuple(
            replace(
                i,
                oper_state=state if state is not None else i.oper_state,
                addresses=addresses if addresses is not None else i.addresses,
            )
            if i.name == name
            else i
            for i in node.interfaces
        )
        return replace(node, interfaces=interfaces)

    return _map_nodes(fabric, node_id, change)


def drop_interface(
    fabric: FabricOperationalState, node_id: str, name: str
) -> FabricOperationalState:
    return _map_nodes(
        fabric,
        node_id,
        lambda n: replace(n, interfaces=tuple(i for i in n.interfaces if i.name != name)),
    )


def bgp(fabric: FabricOperationalState, node_id: str, ip: str, state: BgpSessionState | None):  # type: ignore[no-untyped-def]
    return with_neighbor_states(fabric, {(node_id, ip): state})


def unavailable(
    fabric: FabricOperationalState, node_id: str, facet: ObservationFacet
) -> FabricOperationalState:
    def change(node):  # type: ignore[no-untyped-def]
        cleared = {
            ObservationFacet.INTERFACES: {"interfaces": ()},
            ObservationFacet.BGP: {"bgp": None},
            ObservationFacet.ROUTES: {"routes": ()},
            ObservationFacet.REACHABILITY: {"reachability": ()},
        }[facet]
        return replace(
            node,
            unavailable=(*node.unavailable, UnavailableObservation(facet, "not collected")),
            **cleared,
        )

    return _map_nodes(fabric, node_id, change)


def without_node(fabric: FabricOperationalState, node_id: str) -> FabricOperationalState:
    return replace(fabric, nodes=tuple(n for n in fabric.nodes if n.node_id != node_id))


def link_down(
    fabric: FabricOperationalState, leaf: str, leaf_if: str, spine: str, spine_if: str
) -> FabricOperationalState:
    fabric = set_interface(fabric, leaf, leaf_if, InterfaceOperState.DOWN)
    return set_interface(fabric, spine, spine_if, InterfaceOperState.DOWN)


def only(diagnoses: tuple[Diagnosis, ...]) -> Diagnosis:
    assert len(diagnoses) == 1, [d.resource.resource_id for d in diagnoses]
    return diagnoses[0]


def items(diagnosis: Diagnosis, kind: EvidenceKind) -> tuple[EvidenceItem, ...]:
    return tuple(i for i in diagnosis.evidence if i.kind is kind)


def diagnose(fabric: FabricOperationalState) -> tuple[Diagnosis, ...]:
    return DeterministicDiagnosisEngine.diagnose(fabric)


L1_NB = "10.255.0.0"  # leaf-1's neighbor on leaf-1:eth1--spine-1:eth1 (spine-1's address)
S1_NB = "10.255.0.1"  # spine-1's neighbor on the same link (leaf-1's address)

# -- A / R: nothing wrong -----------------------------------------------------


def test_baseline_has_no_diagnosis() -> None:
    assert diagnose(baseline()) == ()


def test_recovered_network_has_no_diagnosis() -> None:
    degraded = diagnose(load("degraded-active"))
    assert only(degraded).diagnosis_type is PHYSICAL
    assert diagnose(baseline()) == ()


# -- B / C / D: captured live failure sets ------------------------------------


def test_degraded_active_is_one_high_physical_link_failure() -> None:
    diagnosis = only(diagnose(load("degraded-active")))
    assert diagnosis.diagnosis_type is PHYSICAL
    assert diagnosis.resource.resource_id == LINK_L1_S1
    assert diagnosis.confidence is HIGH
    assert diagnosis.collection_id == "run-1" and diagnosis.collected_at == NOW


def test_degraded_idle_is_the_same_high_diagnosis() -> None:
    diagnosis = only(diagnose(load("degraded-idle")))
    assert (diagnosis.diagnosis_type, diagnosis.resource.resource_id, diagnosis.confidence) == (
        PHYSICAL,
        LINK_L1_S1,
        HIGH,
    )
    states = {i.node_id: i.observed.raw_state for i in items(diagnosis, EvidenceKind.BGP_SESSION)}  # type: ignore[union-attr]
    assert states == {"leaf-1": "Idle", "spine-1": "Active"}


def test_degraded_clearing_follows_the_exact_rule() -> None:
    # leaf-1 reports Clearing (normalized UNKNOWN), spine-1 reports Idle (DOWN);
    # both eth1 are DOWN -> HIGH needs only one BGP side DOWN.
    diagnosis = only(diagnose(load("degraded-clearing")))
    assert (diagnosis.diagnosis_type, diagnosis.confidence) == (PHYSICAL, HIGH)
    by_node = {i.node_id: i for i in items(diagnosis, EvidenceKind.BGP_SESSION)}
    assert by_node["leaf-1"].observed == BgpSessionObserved(
        L1_NB, EndpointBgpState.UNKNOWN, "Clearing"
    )
    assert by_node["leaf-1"].role is EvidenceRole.UNKNOWN
    assert by_node["spine-1"].role is EvidenceRole.SUPPORTS
    assert diagnosis.symptom_keys == (SymptomKey("lab1-spine-1", f"bgp-neighbor:{S1_NB}"),)


# -- T: symptom keys ----------------------------------------------------------


def test_degraded_active_symptom_keys() -> None:
    diagnosis = only(diagnose(load("degraded-active")))
    assert diagnosis.symptom_keys == (
        SymptomKey("lab1-leaf-1", "bgp-neighbor:10.255.0.0"),
        SymptomKey("lab1-spine-1", "bgp-neighbor:10.255.0.1"),
    )


def test_symptom_resource_format_matches_the_incident_mapper() -> None:
    assert engine_module.bgp_neighbor_resource("10.255.0.0") == (
        AnomalyIncidentMapper.bgp_down_affected_resource("10.255.0.0")
    )


# -- physical confidence tiers (E, F, G) --------------------------------------


def test_both_interfaces_down_with_bgp_unavailable_is_medium_without_bgp_evidence() -> None:
    fabric = link_down(baseline(), "leaf-1", "eth1", "spine-1", "eth1")
    fabric = unavailable(fabric, "leaf-1", ObservationFacet.BGP)
    fabric = unavailable(fabric, "spine-1", ObservationFacet.BGP)
    diagnosis = only(diagnose(fabric))
    assert (diagnosis.diagnosis_type, diagnosis.confidence) == (PHYSICAL, MEDIUM)
    assert diagnosis.symptom_keys == ()
    for item in items(diagnosis, EvidenceKind.BGP_SESSION):
        assert item.observed == BgpSessionObserved(
            item.observed.neighbor_ip, EndpointBgpState.UNKNOWN, None
        )  # type: ignore[union-attr]
        assert item.role is EvidenceRole.UNKNOWN


@pytest.mark.parametrize("other", [BgpSessionState.CONNECT, BgpSessionState.OPEN_CONFIRM])
def test_both_interfaces_down_with_non_down_bgp_is_medium(other: BgpSessionState) -> None:
    fabric = link_down(baseline(), "leaf-1", "eth1", "spine-1", "eth1")
    fabric = bgp(fabric, "leaf-1", L1_NB, other)
    fabric = bgp(fabric, "spine-1", S1_NB, other)
    assert only(diagnose(fabric)).confidence is MEDIUM


def test_one_interface_down_with_bgp_unavailable_is_low() -> None:
    fabric = set_interface(baseline(), "leaf-1", "eth1", InterfaceOperState.DOWN)
    fabric = unavailable(fabric, "leaf-1", ObservationFacet.BGP)
    fabric = unavailable(fabric, "spine-1", ObservationFacet.BGP)
    diagnosis = only(diagnose(fabric))
    assert (diagnosis.diagnosis_type, diagnosis.confidence) == (PHYSICAL, LOW)


@pytest.mark.parametrize(
    ("leaf_bgp", "spine_bgp"),
    [
        (BgpSessionState.ACTIVE, BgpSessionState.CONNECT),
        (BgpSessionState.ACTIVE, None),
        (BgpSessionState.IDLE, BgpSessionState.ACTIVE),
        (BgpSessionState.UNKNOWN, BgpSessionState.IDLE),
    ],
)
def test_one_interface_down_with_a_bgp_side_down_is_medium(
    leaf_bgp: BgpSessionState, spine_bgp: BgpSessionState | None
) -> None:
    fabric = set_interface(baseline(), "leaf-1", "eth1", InterfaceOperState.DOWN)
    fabric = bgp(fabric, "leaf-1", L1_NB, leaf_bgp)
    fabric = bgp(fabric, "spine-1", S1_NB, spine_bgp)
    diagnosis = only(diagnose(fabric))
    assert (diagnosis.diagnosis_type, diagnosis.confidence) == (PHYSICAL, MEDIUM)


def test_one_interface_down_other_unknown_is_still_physical() -> None:
    fabric = set_interface(baseline(), "leaf-1", "eth1", InterfaceOperState.DOWN)
    fabric = drop_interface(fabric, "spine-1", "eth1")
    fabric = bgp(fabric, "leaf-1", L1_NB, BgpSessionState.ACTIVE)
    fabric = bgp(fabric, "spine-1", S1_NB, BgpSessionState.ACTIVE)
    assert only(diagnose(fabric)).confidence is MEDIUM


# -- BGP session failure (H, I) -----------------------------------------------


def _bgp_only(leaf: BgpSessionState, spine: BgpSessionState | None) -> FabricOperationalState:
    fabric = bgp(baseline(), "leaf-1", L1_NB, leaf)
    return bgp(fabric, "spine-1", S1_NB, spine)


def test_interfaces_up_and_both_bgp_down_is_high_session_failure() -> None:
    diagnosis = only(diagnose(_bgp_only(BgpSessionState.ACTIVE, BgpSessionState.IDLE)))
    assert (diagnosis.diagnosis_type, diagnosis.confidence) == (SESSION, HIGH)
    assert diagnosis.resource.resource_id == LINK_L1_S1
    assert len(diagnosis.symptom_keys) == 2
    assert {i.role for i in items(diagnosis, EvidenceKind.INTERFACE_STATE)} == {
        EvidenceRole.SUPPORTS
    }


@pytest.mark.parametrize("other", [BgpSessionState.CONNECT, BgpSessionState.UNKNOWN, None])
def test_interfaces_up_and_one_bgp_down_is_medium_session_failure(
    other: BgpSessionState | None,
) -> None:
    diagnosis = only(diagnose(_bgp_only(BgpSessionState.ACTIVE, other)))
    assert (diagnosis.diagnosis_type, diagnosis.confidence) == (SESSION, MEDIUM)
    assert len(diagnosis.symptom_keys) == 1


# -- INSUFFICIENT_EVIDENCE (J, K, P) ------------------------------------------


def test_unknown_interface_with_bgp_down_is_insufficient_evidence() -> None:
    fabric = bgp(baseline(), "leaf-1", L1_NB, BgpSessionState.ACTIVE)
    fabric = bgp(fabric, "spine-1", S1_NB, BgpSessionState.CONNECT)
    fabric = unavailable(fabric, "leaf-1", ObservationFacet.INTERFACES)
    diagnosis = only(diagnose(fabric))
    assert diagnosis.diagnosis_type is INSUFFICIENT
    assert diagnosis.confidence is None
    leaf_if = next(
        i for i in items(diagnosis, EvidenceKind.INTERFACE_STATE) if i.node_id == "leaf-1"
    )
    assert leaf_if.role is EvidenceRole.UNKNOWN
    assert leaf_if.observed.state is EndpointInterfaceState.UNKNOWN  # type: ignore[union-attr]
    assert diagnosis.symptom_keys == (SymptomKey("lab1-leaf-1", f"bgp-neighbor:{L1_NB}"),)
    # Why attribution stopped is explained by interface/BGP/address facts only.
    assert {i.kind for i in diagnosis.evidence} == {
        EvidenceKind.INTERFACE_STATE,
        EvidenceKind.BGP_SESSION,
        EvidenceKind.ADDRESS_MATCH,
    }


def test_missing_interface_with_one_up_peer_and_bgp_down_is_insufficient() -> None:
    fabric = drop_interface(baseline(), "leaf-1", "eth1")
    fabric = bgp(fabric, "leaf-1", L1_NB, BgpSessionState.IDLE)
    assert only(diagnose(fabric)).diagnosis_type is INSUFFICIENT


def test_interface_down_with_established_bgp_is_insufficient_with_contradiction() -> None:
    fabric = set_interface(baseline(), "leaf-1", "eth1", InterfaceOperState.DOWN)
    diagnosis = only(diagnose(fabric))
    assert diagnosis.diagnosis_type is INSUFFICIENT and diagnosis.confidence is None
    contradictions = [i for i in diagnosis.evidence if i.role is EvidenceRole.CONTRADICTS]
    assert {(i.kind, i.node_id) for i in contradictions} == {
        (EvidenceKind.BGP_SESSION, "leaf-1"),
        (EvidenceKind.BGP_SESSION, "spine-1"),
    }
    assert diagnosis.symptom_keys == ()


def test_established_side_blocks_both_causal_diagnoses() -> None:
    # BGP down on one side while the other is Established, interfaces up.
    fabric = bgp(baseline(), "leaf-1", L1_NB, BgpSessionState.ACTIVE)
    assert only(diagnose(fabric)).diagnosis_type is INSUFFICIENT


def test_absent_node_with_peer_bgp_failure_is_insufficient_evidence() -> None:
    fabric = without_node(baseline(), "spine-1")
    fabric = bgp(fabric, "leaf-1", L1_NB, BgpSessionState.ACTIVE)
    diagnosis = only(diagnose(fabric))
    assert diagnosis.diagnosis_type is INSUFFICIENT
    assert diagnosis.resource.resource_id == LINK_L1_S1
    assert diagnosis.confidence is None


# -- no symptom, no diagnosis (L, N) ------------------------------------------


def test_unknown_unavailable_and_missing_alone_never_create_a_diagnosis() -> None:
    fabric = baseline()
    for router in ("spine-1", "spine-2", "leaf-1", "leaf-2"):
        fabric = unavailable(fabric, router, ObservationFacet.INTERFACES)
        fabric = unavailable(fabric, router, ObservationFacet.BGP)
    assert diagnose(fabric) == ()
    assert diagnose(without_node(without_node(baseline(), "spine-1"), "leaf-1")) == ()


def test_transitional_and_unknown_bgp_states_are_not_symptoms() -> None:
    for state in (
        BgpSessionState.CONNECT,
        BgpSessionState.OPEN_SENT,
        BgpSessionState.OPEN_CONFIRM,
        BgpSessionState.UNKNOWN,
    ):
        fabric = bgp(baseline(), "leaf-1", L1_NB, state)
        fabric = bgp(fabric, "spine-1", S1_NB, state)
        assert diagnose(fabric) == (), state


def test_unknown_interface_state_alone_is_not_a_symptom() -> None:
    fabric = set_interface(baseline(), "leaf-1", "eth1", InterfaceOperState.UNKNOWN)
    assert diagnose(fabric) == ()


def test_reachability_failure_alone_creates_no_diagnosis() -> None:
    fabric = _map_nodes(
        baseline(),
        "host-1",
        lambda n: replace(n, reachability=(replace(n.reachability[0], received=0),)),
    )
    assert diagnose(fabric) == ()


def test_host_link_interface_down_is_not_evaluated() -> None:
    fabric = set_interface(baseline(), "leaf-1", "eth3", InterfaceOperState.DOWN)
    assert diagnose(fabric) == ()


# -- supporting evidence: ECMP, next hop, reachability (M) --------------------


def test_degraded_active_supporting_evidence() -> None:
    diagnosis = only(diagnose(load("degraded-active")))
    ecmp = {(i.node_id, i.observed.prefix): i for i in items(diagnosis, EvidenceKind.ECMP_PATHS)}  # type: ignore[union-attr]
    assert set(ecmp) == {("leaf-1", "10.1.2.0/24"), ("leaf-2", "10.1.1.0/24")}
    # The lost path is the one via spine-1: leaf-1 uses next hop 10.255.0.0,
    # leaf-2 uses 10.255.0.4. Both are absent from the observed routes.
    assert ecmp[("leaf-1", "10.1.2.0/24")].observed == EcmpPathsObserved(
        "10.1.2.0/24", 1, 2, "10.255.0.0", False
    )
    assert ecmp[("leaf-2", "10.1.1.0/24")].observed == EcmpPathsObserved(
        "10.1.1.0/24", 1, 2, "10.255.0.4", False
    )
    for item in ecmp.values():
        assert item.role is EvidenceRole.SUPPORTS

    hops = {i.node_id: i for i in items(diagnosis, EvidenceKind.ROUTE_NEXT_HOP)}
    assert hops["leaf-1"].subject == "next-hop:10.255.0.0"
    assert hops["spine-1"].subject == "next-hop:10.255.0.1"
    for item in hops.values():
        assert item.observed.active_route_count == 0  # type: ignore[union-attr]
        assert item.role is EvidenceRole.SUPPORTS

    reach = items(diagnosis, EvidenceKind.REACHABILITY)
    assert [(i.node_id, i.observed) for i in reach] == [
        ("host-1", ReachabilityObserved("10.1.2.10", 3, 3)),
        ("host-2", ReachabilityObserved("10.1.1.10", 3, 3)),
    ]
    assert {i.role for i in reach} == {EvidenceRole.UNKNOWN}  # healthy: neutral
    assert not [i for i in diagnosis.evidence if i.role is EvidenceRole.CONTRADICTS]


def test_ecmp_and_reachability_never_change_confidence() -> None:
    degraded = load("degraded-active")
    healthy_context = degraded
    for node in ("spine-1", "spine-2", "leaf-1", "leaf-2"):
        healthy_context = _map_nodes(
            healthy_context,
            node,
            lambda n, node=node: replace(n, routes=baseline().node(node).routes),
        )
    assert only(diagnose(degraded)).confidence is only(diagnose(healthy_context)).confidence is HIGH
    ecmp = items(only(diagnose(healthy_context)), EvidenceKind.ECMP_PATHS)
    assert {i.role for i in ecmp} == {EvidenceRole.UNKNOWN}  # intact: neutral, never CONTRADICTS

    for node in ("leaf-1", "leaf-2", "spine-1"):
        degraded = unavailable(degraded, node, ObservationFacet.ROUTES)
    for host in ("host-1", "host-2"):
        degraded = unavailable(degraded, host, ObservationFacet.REACHABILITY)
    diagnosis = only(diagnose(degraded))
    assert diagnosis.confidence is HIGH
    # Unavailable facets yield UNKNOWN items with no observed value, not facts.
    ecmp_unavailable = items(diagnosis, EvidenceKind.ECMP_PATHS)
    assert {i.observed.observed_path_count for i in ecmp_unavailable} == {None}  # type: ignore[union-attr]
    assert {i.role for i in ecmp_unavailable} == {EvidenceRole.UNKNOWN}
    assert {i.observed.sent for i in items(diagnosis, EvidenceKind.REACHABILITY)} == {None}  # type: ignore[union-attr]


def test_failed_reachability_is_supporting_not_contradicting_and_keeps_tier() -> None:
    fabric = _map_nodes(
        load("degraded-active"),
        "host-1",
        lambda n: replace(n, reachability=(replace(n.reachability[0], received=0),)),
    )
    diagnosis = only(diagnose(fabric))
    assert diagnosis.confidence is HIGH
    host_1 = next(i for i in items(diagnosis, EvidenceKind.REACHABILITY) if i.node_id == "host-1")
    assert host_1.role is EvidenceRole.SUPPORTS


# -- ECMP relevance: link-specific supporting evidence -----------------------


def _ecmp_by_link_path(diagnosis: Diagnosis) -> dict[tuple[str, str], EvidenceRole]:
    return {
        (i.node_id, i.observed.link_next_hop_ip): i.role  # type: ignore[union-attr]
        for i in items(diagnosis, EvidenceKind.ECMP_PATHS)
    }


def _without_bgp_route(fabric: FabricOperationalState, node_id: str, prefix: str):  # type: ignore[no-untyped-def]
    return _map_nodes(
        fabric,
        node_id,
        lambda n: replace(
            n, routes=tuple(r for r in n.routes if not (r.prefix == prefix and r.protocol == "bgp"))
        ),
    )


def test_ecmp_reduction_from_another_link_does_not_support_this_diagnosis() -> None:
    # leaf-1:eth1--spine-1:eth1 is down (both leaves lost the spine-1 path and
    # show 1 of 2 paths). leaf-2:eth2--spine-2:eth2 only has a BGP session
    # problem: its own spine-2 paths are still in both leaves' routes.
    fabric = bgp(load("degraded-active"), "leaf-2", "10.255.0.6", BgpSessionState.ACTIVE)
    fabric = bgp(fabric, "spine-2", "10.255.0.7", BgpSessionState.ACTIVE)
    physical, session = diagnose(fabric)
    assert (physical.resource.resource_id, physical.confidence) == (LINK_L1_S1, HIGH)
    assert (session.resource.resource_id, session.diagnosis_type) == (LINK_L2_S2, SESSION)
    assert _ecmp_by_link_path(physical) == {
        ("leaf-1", "10.255.0.0"): EvidenceRole.SUPPORTS,
        ("leaf-2", "10.255.0.4"): EvidenceRole.SUPPORTS,
    }
    # The reduced counts (1 of 2) are real, but they belong to the other link.
    other = items(session, EvidenceKind.ECMP_PATHS)
    assert {i.observed.observed_path_count for i in other} == {1}  # type: ignore[union-attr]
    assert {i.observed.link_path_present for i in other} == {True}  # type: ignore[union-attr]
    assert _ecmp_by_link_path(session) == {
        ("leaf-1", "10.255.0.2"): EvidenceRole.UNKNOWN,
        ("leaf-2", "10.255.0.6"): EvidenceRole.UNKNOWN,
    }
    assert session.confidence is HIGH  # BGP-only tier is untouched by ECMP


def test_two_failures_each_get_only_their_own_ecmp_support() -> None:
    fabric = _two_link_failure()
    for leaf, prefix in (("leaf-1", "10.1.2.0/24"), ("leaf-2", "10.1.1.0/24")):
        fabric = _without_bgp_route(fabric, leaf, prefix)  # both spine paths lost
    first, second = diagnose(fabric)
    assert (first.resource.resource_id, second.resource.resource_id) == (LINK_L1_S1, LINK_L2_S2)
    assert _ecmp_by_link_path(first) == {
        ("leaf-1", "10.255.0.0"): EvidenceRole.SUPPORTS,
        ("leaf-2", "10.255.0.4"): EvidenceRole.SUPPORTS,
    }
    assert _ecmp_by_link_path(second) == {
        ("leaf-1", "10.255.0.2"): EvidenceRole.SUPPORTS,
        ("leaf-2", "10.255.0.6"): EvidenceRole.SUPPORTS,
    }
    assert (first.confidence, second.confidence) == (HIGH, HIGH)


def test_one_failure_in_a_two_failure_state_supports_only_the_lost_path() -> None:
    # Only the spine-1 paths are gone from leaf-1's route; leaf-1 still has
    # its spine-2 path, so the spine-2 link's ECMP item is neutral.
    fabric = _two_link_failure()
    first, second = diagnose(fabric)
    assert _ecmp_by_link_path(first) == {
        ("leaf-1", "10.255.0.0"): EvidenceRole.SUPPORTS,
        ("leaf-2", "10.255.0.4"): EvidenceRole.SUPPORTS,
    }
    assert set(_ecmp_by_link_path(second).values()) == {EvidenceRole.UNKNOWN}


def test_healthy_ecmp_is_neutral_and_no_ecmp_without_a_diagnosis() -> None:
    assert diagnose(baseline()) == ()
    fabric = link_down(baseline(), "leaf-1", "eth1", "spine-1", "eth1")
    fabric = bgp(fabric, "leaf-1", L1_NB, BgpSessionState.ACTIVE)
    fabric = bgp(fabric, "spine-1", S1_NB, BgpSessionState.ACTIVE)
    diagnosis = only(diagnose(fabric))  # baseline routes: every path still present
    assert {i.role for i in items(diagnosis, EvidenceKind.ECMP_PATHS)} == {EvidenceRole.UNKNOWN}


def test_ecmp_support_never_changes_the_tier() -> None:
    reduced = load("degraded-active")
    intact = reduced
    for node in ("leaf-1", "leaf-2"):
        intact = _map_nodes(
            intact, node, lambda n, node=node: replace(n, routes=baseline().node(node).routes)
        )
    assert only(diagnose(reduced)).confidence is only(diagnose(intact)).confidence is HIGH


def test_route_next_hop_evidence_is_specific_to_the_diagnosed_link() -> None:
    for diagnosis in diagnose(_two_link_failure()):
        a, b = diagnosis.resource.a_node, diagnosis.resource.b_node
        hops = items(diagnosis, EvidenceKind.ROUTE_NEXT_HOP)
        assert sorted(i.node_id for i in hops) == sorted([a, b])


# -- address mismatch cap (O) -------------------------------------------------


def test_address_mismatch_caps_physical_confidence_at_medium() -> None:
    fabric = set_interface(load("degraded-active"), "leaf-1", "eth1", addresses=("10.255.0.9/31",))
    diagnosis = only(diagnose(fabric))
    assert (diagnosis.diagnosis_type, diagnosis.confidence) == (PHYSICAL, MEDIUM)
    mismatch = [
        i
        for i in items(diagnosis, EvidenceKind.ADDRESS_MATCH)
        if i.role is EvidenceRole.CONTRADICTS
    ]
    assert [(i.node_id, i.observed.matches) for i in mismatch] == [("leaf-1", False)]  # type: ignore[union-attr]
    assert mismatch[0].observed.declared_address == "10.255.0.1"  # type: ignore[union-attr]
    assert mismatch[0].observed.observed_addresses == ("10.255.0.9",)  # type: ignore[union-attr]


def test_address_mismatch_caps_session_failure_and_never_raises_lower_tiers() -> None:
    capped = set_interface(
        _bgp_only(BgpSessionState.ACTIVE, BgpSessionState.IDLE),
        "spine-1",
        "eth1",
        addresses=("10.255.0.77/31",),
    )
    assert only(diagnose(capped)).confidence is MEDIUM

    low = set_interface(baseline(), "leaf-1", "eth1", InterfaceOperState.DOWN)
    low = unavailable(
        unavailable(low, "leaf-1", ObservationFacet.BGP), "spine-1", ObservationFacet.BGP
    )
    low = set_interface(low, "leaf-1", "eth1", addresses=("10.255.0.9/31",))
    assert only(diagnose(low)).confidence is LOW


def test_absent_addresses_are_unknown_not_a_mismatch() -> None:
    fabric = set_interface(load("degraded-active"), "leaf-1", "eth1", addresses=())
    diagnosis = only(diagnose(fabric))
    assert diagnosis.confidence is HIGH
    leaf = next(i for i in items(diagnosis, EvidenceKind.ADDRESS_MATCH) if i.node_id == "leaf-1")
    assert leaf.role is EvidenceRole.UNKNOWN and leaf.observed.matches is None  # type: ignore[union-attr]


# -- Q / S: multiple links, determinism ---------------------------------------


def _two_link_failure() -> FabricOperationalState:
    fabric = load("degraded-active")
    fabric = link_down(fabric, "leaf-2", "eth2", "spine-2", "eth2")
    fabric = bgp(fabric, "leaf-2", "10.255.0.6", BgpSessionState.ACTIVE)
    return bgp(fabric, "spine-2", "10.255.0.7", BgpSessionState.ACTIVE)


def test_two_independent_link_failures_give_two_ordered_diagnoses() -> None:
    diagnoses = diagnose(_two_link_failure())
    assert [d.resource.resource_id for d in diagnoses] == [LINK_L1_S1, LINK_L2_S2]
    assert [d.diagnosis_type for d in diagnoses] == [PHYSICAL, PHYSICAL]
    assert [d.confidence for d in diagnoses] == [HIGH, HIGH]
    assert diagnoses[1].symptom_keys == (
        SymptomKey("lab1-leaf-2", "bgp-neighbor:10.255.0.6"),
        SymptomKey("lab1-spine-2", "bgp-neighbor:10.255.0.7"),
    )


def test_same_input_gives_equal_identically_ordered_output() -> None:
    fabric = _two_link_failure()
    runs = [diagnose(fabric) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]
    assert diagnose(replace(fabric, nodes=tuple(reversed(fabric.nodes)))) == runs[0]


def test_evidence_is_ordered_by_kind_node_and_subject() -> None:
    for diagnosis in diagnose(_two_link_failure()):
        order = list(EvidenceKind)
        keys = [(order.index(i.kind), i.node_id, i.subject) for i in diagnosis.evidence]
        assert keys == sorted(keys)
        assert len(keys) == len(set(keys))
        assert list(diagnosis.symptom_keys) == sorted(
            diagnosis.symptom_keys, key=lambda k: (k.device_id, k.affected_resource)
        )


# -- purity -------------------------------------------------------------------


def test_engine_imports_no_persistence_incident_or_framework_code() -> None:
    tree = ast.parse(Path(engine_module.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name if isinstance(node, ast.Import) else node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    forbidden = (
        "meta_rne.domain.incident",
        "meta_rne.domain.ports",
        "meta_rne.persistence",
        "meta_rne.application",
        "meta_rne.api",
        "meta_rne.detection",
        "sqlalchemy",
        "fastapi",
        "subprocess",
        "datetime",
    )
    for module in imported:
        assert not module.startswith(forbidden), module
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not names & {"compute_fingerprint", "UnitOfWork", "IncidentRepository", "now", "utcnow"}
