"""``DeterministicDiagnosisEngine`` — pure link-level RCA (NPE-1E1A, ADR-0003).

``FabricOperationalState`` (plus the declared Lab 1 topology) in,
``tuple[Diagnosis, ...]`` out. No repository, UnitOfWork, database, HTTP,
subprocess, clock or incident access, and no hidden history: the same input
always yields equal, identically ordered output.

Per declared router-router link the engine classifies four endpoint facts, each
only from what the fabric actually observed:

* interface state, per end: ``UP`` / ``DOWN`` / ``UNKNOWN``;
* BGP state of the session to the peer's declared address, per end:
  ``DOWN`` (Idle/Active) / ``ESTABLISHED`` / ``OTHER`` (Connect, OpenSent,
  OpenConfirm) / ``UNKNOWN``.

``UNKNOWN`` covers a missing node, an unavailable facet, a missing expected
interface or neighbor, and a state the model does not recognize (including a
normalized ``Clearing``). It is never treated as ``DOWN`` and never as ``UP``.

Trigger. A link is diagnosed only if a relevant failure symptom is *explicitly
observed*: an endpoint interface ``DOWN``, or an endpoint BGP session ``DOWN``.
Unknown/unavailable/missing alone never creates a diagnosis, and neither does a
failed host reachability probe.

Classification (``E`` = an endpoint BGP session is ``ESTABLISHED``):

* some interface ``DOWN`` and not ``E`` -> ``PHYSICAL_LINK_FAILURE``
* both interfaces ``UP``, some BGP ``DOWN`` and not ``E`` -> ``BGP_SESSION_FAILURE``
* any other symptom -> ``INSUFFICIENT_EVIDENCE`` (``confidence`` is ``None``)

Confidence is a fixed table over interface/BGP facts only; routes, ECMP and
reachability are supporting evidence and never move it. Route next-hop and ECMP
evidence is specific to the diagnosed link (the peer's address as next hop; the
single ECMP path that losing that link removes); reachability is context only:

* PHYSICAL: both interfaces ``DOWN`` -> ``HIGH`` if a BGP side is ``DOWN`` else
  ``MEDIUM``; exactly one ``DOWN`` -> ``MEDIUM`` if a BGP side is ``DOWN`` else
  ``LOW``.
* BGP_SESSION: both sides ``DOWN`` -> ``HIGH``; exactly one -> ``MEDIUM``.
* An observed interface address that conflicts with the declared address is
  possible topology drift: it is recorded as ``CONTRADICTS`` and caps any
  confidence at ``MEDIUM``. The declared topology is never reinterpreted.

Ordering. Diagnoses are ordered by link ID; evidence by (kind in
``EvidenceKind`` declaration order, node ID, subject); symptom keys by
(device ID, affected resource).
"""

from dataclasses import dataclass
from ipaddress import IPv4Interface, ip_interface

from meta_rne.domain.diagnosis import (
    AddressObserved,
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
    InterfaceStateObserved,
    LinkRef,
    NextHopObserved,
    ReachabilityObserved,
    SymptomKey,
)
from meta_rne.domain.operational_state import (
    BgpNeighborObservation,
    BgpSessionState,
    FabricOperationalState,
    InterfaceObservation,
    InterfaceOperState,
    NormalizedOperationalState,
    ObservationFacet,
)
from meta_rne.lab1_identity import LAB1_NODE_DEVICE_IDS
from meta_rne.lab1_topology import (
    LAB1_ECMP_EXPECTATIONS,
    LAB1_HOST_NODE_IDS,
    LAB1_ROUTER_LINKS,
    PhysicalLink,
    TopologyEndpoint,
    ecmp_next_hop_lost_with_link,
)

_KIND_ORDER = {kind: index for index, kind in enumerate(EvidenceKind)}

_INTERFACE_STATE = {
    InterfaceOperState.UP: EndpointInterfaceState.UP,
    InterfaceOperState.DOWN: EndpointInterfaceState.DOWN,
    InterfaceOperState.UNKNOWN: EndpointInterfaceState.UNKNOWN,
}

_BGP_STATE = {
    BgpSessionState.IDLE: EndpointBgpState.DOWN,
    BgpSessionState.ACTIVE: EndpointBgpState.DOWN,
    BgpSessionState.ESTABLISHED: EndpointBgpState.ESTABLISHED,
    BgpSessionState.CONNECT: EndpointBgpState.OTHER,
    BgpSessionState.OPEN_SENT: EndpointBgpState.OTHER,
    BgpSessionState.OPEN_CONFIRM: EndpointBgpState.OTHER,
    BgpSessionState.UNKNOWN: EndpointBgpState.UNKNOWN,
}

_BGP_ROUTE_PROTOCOL = "bgp"


def bgp_neighbor_resource(neighbor_ip: str) -> str:
    """The ``affected_resource`` of a RULE-BGP-DOWN incident. Duplicated here
    on purpose (the engine must not import incident code); a test keeps it
    equal to ``AnomalyIncidentMapper.bgp_down_affected_resource``."""
    return f"bgp-neighbor:{neighbor_ip}"


@dataclass(frozen=True, slots=True)
class _EndView:
    """One end of a link: what the fabric actually observed there."""

    endpoint: TopologyEndpoint
    peer: TopologyEndpoint
    node: NormalizedOperationalState | None
    interface: InterfaceObservation | None
    interface_state: EndpointInterfaceState
    neighbor: BgpNeighborObservation | None
    bgp_state: EndpointBgpState


def _find_node(fabric: FabricOperationalState, node_id: str) -> NormalizedOperationalState | None:
    for node in fabric.nodes:
        if node.node_id == node_id:
            return node
    return None


def _view(
    fabric: FabricOperationalState, endpoint: TopologyEndpoint, peer: TopologyEndpoint
) -> _EndView:
    node = _find_node(fabric, endpoint.node_id)
    interface: InterfaceObservation | None = None
    neighbor: BgpNeighborObservation | None = None
    if node is not None:
        if not node.is_unavailable(ObservationFacet.INTERFACES):
            interface = next(
                (item for item in node.interfaces if item.name == endpoint.interface_name), None
            )
        if node.bgp is not None:
            neighbor = next(
                (item for item in node.bgp.neighbors if item.neighbor_ip == peer.address), None
            )
    return _EndView(
        endpoint=endpoint,
        peer=peer,
        node=node,
        interface=interface,
        interface_state=(
            _INTERFACE_STATE[interface.oper_state]
            if interface is not None
            else EndpointInterfaceState.UNKNOWN
        ),
        neighbor=neighbor,
        bgp_state=(
            _BGP_STATE[neighbor.state] if neighbor is not None else EndpointBgpState.UNKNOWN
        ),
    )


def _observed_ipv4(interface: InterfaceObservation) -> tuple[str, ...]:
    addresses = []
    for address in interface.addresses:
        parsed = ip_interface(address)
        if isinstance(parsed, IPv4Interface):
            addresses.append(str(parsed.ip))
    return tuple(sorted(addresses))


def _address_matches(view: _EndView) -> tuple[tuple[str, ...], bool | None]:
    """``(observed IPv4 addresses, matches)``. ``matches`` is ``None`` when the
    interface or any IPv4 address on it was not observed: absence is not a
    conflict."""
    if view.interface is None:
        return (), None
    observed = _observed_ipv4(view.interface)
    if not observed:
        return (), None
    return observed, view.endpoint.address in observed


# -- evidence roles, relative to the diagnosis type ---------------------------


def _interface_role(dtype: DiagnosisType, state: EndpointInterfaceState) -> EvidenceRole:
    if dtype is DiagnosisType.PHYSICAL_LINK_FAILURE:
        return {
            EndpointInterfaceState.DOWN: EvidenceRole.SUPPORTS,
            EndpointInterfaceState.UP: EvidenceRole.CONTRADICTS,
            EndpointInterfaceState.UNKNOWN: EvidenceRole.UNKNOWN,
        }[state]
    if dtype is DiagnosisType.BGP_SESSION_FAILURE:
        return {
            EndpointInterfaceState.UP: EvidenceRole.SUPPORTS,
            EndpointInterfaceState.DOWN: EvidenceRole.CONTRADICTS,
            EndpointInterfaceState.UNKNOWN: EvidenceRole.UNKNOWN,
        }[state]
    return EvidenceRole.SUPPORTS if state is EndpointInterfaceState.DOWN else EvidenceRole.UNKNOWN


def _bgp_role(state: EndpointBgpState) -> EvidenceRole:
    if state is EndpointBgpState.DOWN:
        return EvidenceRole.SUPPORTS
    if state is EndpointBgpState.ESTABLISHED:
        return EvidenceRole.CONTRADICTS
    return EvidenceRole.UNKNOWN


# -- evidence builders --------------------------------------------------------


def _interface_item(dtype: DiagnosisType, view: _EndView) -> EvidenceItem:
    return EvidenceItem(
        kind=EvidenceKind.INTERFACE_STATE,
        node_id=view.endpoint.node_id,
        subject=view.endpoint.interface_name,
        observed=InterfaceStateObserved(view.endpoint.interface_name, view.interface_state),
        role=_interface_role(dtype, view.interface_state),
    )


def _bgp_item(view: _EndView) -> EvidenceItem:
    return EvidenceItem(
        kind=EvidenceKind.BGP_SESSION,
        node_id=view.endpoint.node_id,
        subject=bgp_neighbor_resource(view.peer.address),
        observed=BgpSessionObserved(
            neighbor_ip=view.peer.address,
            state=view.bgp_state,
            raw_state=view.neighbor.raw_state if view.neighbor is not None else None,
        ),
        role=_bgp_role(view.bgp_state),
    )


def _address_item(view: _EndView) -> EvidenceItem:
    observed, matches = _address_matches(view)
    role = (
        EvidenceRole.UNKNOWN
        if matches is None
        else EvidenceRole.SUPPORTS
        if matches
        else EvidenceRole.CONTRADICTS
    )
    return EvidenceItem(
        kind=EvidenceKind.ADDRESS_MATCH,
        node_id=view.endpoint.node_id,
        subject=view.endpoint.interface_name,
        observed=AddressObserved(
            interface_name=view.endpoint.interface_name,
            declared_address=view.endpoint.address,
            observed_addresses=observed,
            matches=matches,
        ),
        role=role,
    )


def _next_hop_item(view: _EndView) -> EvidenceItem:
    """Literal: how many *active* routes list the peer as next hop. Absence is
    only reported, never explained."""
    node = view.node
    count: int | None = None
    if node is not None and not node.is_unavailable(ObservationFacet.ROUTES):
        count = sum(
            1
            for route in node.routes
            if any(next_hop.ip == view.peer.address for next_hop in route.next_hops)
        )
    return EvidenceItem(
        kind=EvidenceKind.ROUTE_NEXT_HOP,
        node_id=view.endpoint.node_id,
        subject=f"next-hop:{view.peer.address}",
        observed=NextHopObserved(view.peer.address, view.endpoint.interface_name, count),
        role=EvidenceRole.SUPPORTS if count == 0 else EvidenceRole.UNKNOWN,
    )


def _ecmp_items(fabric: FabricOperationalState, link: PhysicalLink) -> list[EvidenceItem]:
    """ECMP context for *this* link only: an expectation is included only if
    losing ``link`` removes one of its paths (declared in ``lab1_topology``),
    and it SUPPORTS only if that exact path (next hop) is absent from a node
    whose BGP routes were observed. A reduction whose lost path belongs to a
    different link, or healthy ECMP, is neutral."""
    items = []
    for expectation in LAB1_ECMP_EXPECTATIONS:
        link_next_hop = ecmp_next_hop_lost_with_link(expectation, link)
        if link_next_hop is None:
            continue
        node = _find_node(fabric, expectation.node_id)
        count: int | None = None
        path_present: bool | None = None
        if node is not None and not node.is_unavailable(ObservationFacet.ROUTES):
            routes = [
                route
                for route in node.routes
                if route.prefix == expectation.prefix and route.protocol == _BGP_ROUTE_PROTOCOL
            ]
            count = sum(route.ecmp_path_count for route in routes)
            path_present = any(
                next_hop.ip == link_next_hop for route in routes for next_hop in route.next_hops
            )
        items.append(
            EvidenceItem(
                kind=EvidenceKind.ECMP_PATHS,
                node_id=expectation.node_id,
                subject=expectation.prefix,
                observed=EcmpPathsObserved(
                    prefix=expectation.prefix,
                    observed_path_count=count,
                    expected_path_count=expectation.expected_path_count,
                    link_next_hop_ip=link_next_hop,
                    link_path_present=path_present,
                ),
                role=EvidenceRole.SUPPORTS if path_present is False else EvidenceRole.UNKNOWN,
            )
        )
    return items


def _reachability_items(fabric: FabricOperationalState) -> list[EvidenceItem]:
    items = []
    for host_id in LAB1_HOST_NODE_IDS:
        node = _find_node(fabric, host_id)
        if (
            node is None
            or node.is_unavailable(ObservationFacet.REACHABILITY)
            or not node.reachability
        ):
            items.append(
                EvidenceItem(
                    kind=EvidenceKind.REACHABILITY,
                    node_id=host_id,
                    subject="reachability",
                    observed=ReachabilityObserved(target="", sent=None, received=None),
                    role=EvidenceRole.UNKNOWN,
                )
            )
            continue
        for probe in node.reachability:
            items.append(
                EvidenceItem(
                    kind=EvidenceKind.REACHABILITY,
                    node_id=host_id,
                    subject=probe.target,
                    observed=ReachabilityObserved(probe.target, probe.sent, probe.received),
                    # A healthy probe is consistent with a redundant single-link
                    # failure (neutral); loss is a consequence.
                    role=EvidenceRole.UNKNOWN if probe.succeeded else EvidenceRole.SUPPORTS,
                )
            )
    return items


# -- classification -----------------------------------------------------------


def _physical_confidence(
    interfaces: tuple[EndpointInterfaceState, EndpointInterfaceState],
    bgp: tuple[EndpointBgpState, EndpointBgpState],
) -> DiagnosisConfidence:
    down_interfaces = sum(1 for state in interfaces if state is EndpointInterfaceState.DOWN)
    bgp_down = EndpointBgpState.DOWN in bgp
    if down_interfaces == 2:
        return DiagnosisConfidence.HIGH if bgp_down else DiagnosisConfidence.MEDIUM
    return DiagnosisConfidence.MEDIUM if bgp_down else DiagnosisConfidence.LOW


def _session_confidence(bgp: tuple[EndpointBgpState, EndpointBgpState]) -> DiagnosisConfidence:
    both_down = all(state is EndpointBgpState.DOWN for state in bgp)
    return DiagnosisConfidence.HIGH if both_down else DiagnosisConfidence.MEDIUM


def _cap_at_medium(confidence: DiagnosisConfidence) -> DiagnosisConfidence:
    return DiagnosisConfidence.MEDIUM if confidence is DiagnosisConfidence.HIGH else confidence


def _diagnose_link(fabric: FabricOperationalState, link: PhysicalLink) -> Diagnosis | None:
    a = _view(fabric, link.endpoint_a, link.endpoint_b)
    b = _view(fabric, link.endpoint_b, link.endpoint_a)
    interfaces = (a.interface_state, b.interface_state)
    bgp = (a.bgp_state, b.bgp_state)

    interface_down = EndpointInterfaceState.DOWN in interfaces
    bgp_down = EndpointBgpState.DOWN in bgp
    if not (interface_down or bgp_down):
        return None  # no explicitly observed failure symptom

    established = EndpointBgpState.ESTABLISHED in bgp
    confidence: DiagnosisConfidence | None
    if interface_down and not established:
        dtype = DiagnosisType.PHYSICAL_LINK_FAILURE
        confidence = _physical_confidence(interfaces, bgp)
    elif (
        all(state is EndpointInterfaceState.UP for state in interfaces)
        and bgp_down
        and not established
    ):
        dtype = DiagnosisType.BGP_SESSION_FAILURE
        confidence = _session_confidence(bgp)
    else:
        dtype = DiagnosisType.INSUFFICIENT_EVIDENCE
        confidence = None

    evidence = [
        _interface_item(dtype, a),
        _interface_item(dtype, b),
        _bgp_item(a),
        _bgp_item(b),
        _address_item(a),
        _address_item(b),
    ]
    if confidence is not None and any(
        item.kind is EvidenceKind.ADDRESS_MATCH and item.role is EvidenceRole.CONTRADICTS
        for item in evidence
    ):
        confidence = _cap_at_medium(confidence)
    if dtype is not DiagnosisType.INSUFFICIENT_EVIDENCE:
        evidence.extend([_next_hop_item(a), _next_hop_item(b)])
        evidence.extend(_ecmp_items(fabric, link))
        evidence.extend(_reachability_items(fabric))

    symptom_keys = sorted(
        {
            SymptomKey(
                device_id=LAB1_NODE_DEVICE_IDS[view.endpoint.node_id],
                affected_resource=bgp_neighbor_resource(view.peer.address),
            )
            for view in (a, b)
            if view.bgp_state is EndpointBgpState.DOWN
        },
        key=lambda key: (key.device_id, key.affected_resource),
    )
    evidence.sort(key=lambda item: (_KIND_ORDER[item.kind], item.node_id, item.subject))

    return Diagnosis(
        diagnosis_type=dtype,
        resource=LinkRef(
            a_node=link.endpoint_a.node_id,
            a_interface=link.endpoint_a.interface_name,
            b_node=link.endpoint_b.node_id,
            b_interface=link.endpoint_b.interface_name,
        ),
        confidence=confidence,
        evidence=tuple(evidence),
        symptom_keys=tuple(symptom_keys),
        collection_id=fabric.collection_id,
        collected_at=fabric.collected_at,
    )


class DeterministicDiagnosisEngine:
    """Stateless."""

    @staticmethod
    def diagnose(fabric: FabricOperationalState) -> tuple[Diagnosis, ...]:
        diagnoses = [
            diagnosis
            for link in LAB1_ROUTER_LINKS
            if (diagnosis := _diagnose_link(fabric, link)) is not None
        ]
        diagnoses.sort(key=lambda diagnosis: diagnosis.resource.resource_id)
        return tuple(diagnoses)
