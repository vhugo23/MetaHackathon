"""Declared physical topology of Lab 1 (NPE-1E1A, ADR-0003).

Pure constants: no framework, I/O, YAML parsing or frontend dependency. This is
presentation-free *design* metadata copied from ``lab/topology.clab.yml`` (node
names, link endpoints), the Lab 1 addressing contract in
``lab/frr/<router>/frr.conf`` (endpoint addresses, ``maximum-paths``) and
``lab1_identity`` (router device IDs). ``tests/unit/test_lab1_topology_drift.py``
fails if this module diverges from those files, so production code never reads
them. Physical links are declared here and are never inferred from BGP
neighbors, subnets or routes.

Canonical link identity. A link's two endpoints are ordered by
``(node_id, interface_name)`` (plain string comparison), smaller first, and its
ID is ``"<a.node>:<a.interface>--<b.node>:<b.interface>"``, e.g.
``leaf-1:eth1--spine-1:eth1``. ``PhysicalLink`` rejects a non-canonical
ordering, and ``LAB1_LINKS`` is sorted by that ID, so neither depends on
declaration order.
"""

from dataclasses import dataclass
from ipaddress import IPv4Network, ip_address

from meta_rne.lab1_identity import LAB1_NODE_DEVICE_IDS


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class TopologyEndpoint:
    node_id: str
    interface_name: str
    address: str  # the endpoint's own IPv4 address, without a prefix length

    def __post_init__(self) -> None:
        _require_non_empty(self.node_id, "TopologyEndpoint.node_id")
        _require_non_empty(self.interface_name, "TopologyEndpoint.interface_name")
        ip_address(self.address)

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.node_id, self.interface_name)


@dataclass(frozen=True, slots=True)
class PhysicalLink:
    """One declared physical link; endpoints are in canonical order."""

    endpoint_a: TopologyEndpoint
    endpoint_b: TopologyEndpoint

    def __post_init__(self) -> None:
        if self.endpoint_a.node_id == self.endpoint_b.node_id:
            raise ValueError("PhysicalLink endpoints must be on different nodes")
        if not self.endpoint_a.sort_key < self.endpoint_b.sort_key:
            raise ValueError(
                "PhysicalLink endpoints must be in canonical (node_id, interface_name) order"
            )

    @property
    def link_id(self) -> str:
        a, b = self.endpoint_a, self.endpoint_b
        return f"{a.node_id}:{a.interface_name}--{b.node_id}:{b.interface_name}"

    @property
    def is_router_link(self) -> bool:
        """Router-to-router (one eBGP adjacency); otherwise a host access link."""
        return (
            self.endpoint_a.node_id in LAB1_NODE_DEVICE_IDS
            and self.endpoint_b.node_id in LAB1_NODE_DEVICE_IDS
        )


@dataclass(frozen=True, slots=True)
class EcmpExpectation:
    """Declared design: ``node_id`` should hold ``expected_path_count`` active
    paths to ``prefix``, a network originated by ``origin_node_id``. Observed
    counts always come from live state."""

    node_id: str
    prefix: str
    origin_node_id: str
    expected_path_count: int

    def __post_init__(self) -> None:
        _require_non_empty(self.node_id, "EcmpExpectation.node_id")
        _require_non_empty(self.origin_node_id, "EcmpExpectation.origin_node_id")
        if self.node_id == self.origin_node_id:
            raise ValueError("EcmpExpectation.origin_node_id must differ from node_id")
        IPv4Network(self.prefix)
        if self.expected_path_count < 2:
            raise ValueError("EcmpExpectation.expected_path_count must be >= 2")


def _link(x: TopologyEndpoint, y: TopologyEndpoint) -> PhysicalLink:
    first, second = sorted((x, y), key=lambda endpoint: endpoint.sort_key)
    return PhysicalLink(first, second)


LAB1_ROUTER_NODE_IDS: tuple[str, ...] = ("spine-1", "spine-2", "leaf-1", "leaf-2")
LAB1_HOST_NODE_IDS: tuple[str, ...] = ("host-1", "host-2")
LAB1_SPINE_NODE_IDS: tuple[str, ...] = ("spine-1", "spine-2")
LAB1_LEAF_NODE_IDS: tuple[str, ...] = ("leaf-1", "leaf-2")
LAB1_NODE_IDS: tuple[str, ...] = (*LAB1_ROUTER_NODE_IDS, *LAB1_HOST_NODE_IDS)

LAB1_LINKS: tuple[PhysicalLink, ...] = tuple(
    sorted(
        (
            # leaf-spine fabric links
            _link(
                TopologyEndpoint("leaf-1", "eth1", "10.255.0.1"),
                TopologyEndpoint("spine-1", "eth1", "10.255.0.0"),
            ),
            _link(
                TopologyEndpoint("leaf-1", "eth2", "10.255.0.3"),
                TopologyEndpoint("spine-2", "eth1", "10.255.0.2"),
            ),
            _link(
                TopologyEndpoint("leaf-2", "eth1", "10.255.0.5"),
                TopologyEndpoint("spine-1", "eth2", "10.255.0.4"),
            ),
            _link(
                TopologyEndpoint("leaf-2", "eth2", "10.255.0.7"),
                TopologyEndpoint("spine-2", "eth2", "10.255.0.6"),
            ),
            # host access links
            _link(
                TopologyEndpoint("host-1", "eth1", "10.1.1.10"),
                TopologyEndpoint("leaf-1", "eth3", "10.1.1.1"),
            ),
            _link(
                TopologyEndpoint("host-2", "eth1", "10.1.2.10"),
                TopologyEndpoint("leaf-2", "eth3", "10.1.2.1"),
            ),
        ),
        key=lambda link: link.link_id,
    )
)

LAB1_ROUTER_LINKS: tuple[PhysicalLink, ...] = tuple(
    link for link in LAB1_LINKS if link.is_router_link
)

# Each leaf reaches the other leaf's host network through both spines
# (``maximum-paths 2`` in each leaf's frr.conf).
LAB1_ECMP_EXPECTATIONS: tuple[EcmpExpectation, ...] = (
    EcmpExpectation("leaf-1", "10.1.2.0/24", "leaf-2", 2),
    EcmpExpectation("leaf-2", "10.1.1.0/24", "leaf-1", 2),
)


def ecmp_next_hop_lost_with_link(expectation: EcmpExpectation, link: PhysicalLink) -> str | None:
    """The next-hop address by which ``expectation.node_id`` reaches the spine
    of ``link``, if losing ``link`` removes that one path; otherwise ``None``.

    Lab 1 is a two-tier leaf-spine fabric: a leaf reaches another leaf's
    network through each spine, and the path via spine S exists only while both
    the node's own link to S and the origin leaf's link to S are up. So a
    fabric link removes exactly one path of an expectation, the one via its
    spine, and only if it connects the expectation's node or its origin leaf.
    A host access link, or a link of an unrelated leaf, removes none. This is
    declared-design reasoning only: no path simulation."""
    if not link.is_router_link:
        return None
    ends = (link.endpoint_a, link.endpoint_b)
    spines = [end for end in ends if end.node_id in LAB1_SPINE_NODE_IDS]
    leaves = [end for end in ends if end.node_id in LAB1_LEAF_NODE_IDS]
    if len(spines) != 1 or len(leaves) != 1:
        return None
    if leaves[0].node_id not in (expectation.node_id, expectation.origin_node_id):
        return None
    for own_link in LAB1_ROUTER_LINKS:
        for own, spine in (
            (own_link.endpoint_a, own_link.endpoint_b),
            (own_link.endpoint_b, own_link.endpoint_a),
        ):
            if own.node_id == expectation.node_id and spine.node_id == spines[0].node_id:
                return spine.address
    return None
