"""Normalized live operational-state domain model (NPE-1C3A, ADR-0003).

Pure data: no FastAPI, Pydantic, SQLAlchemy, subprocess, or file I/O.
Immutable ``@dataclass(frozen=True, slots=True)`` with ``tuple`` collections,
matching ``domain/telemetry.py``.

This model is deliberately separate from ``TelemetrySample``: it carries live
routing/forwarding facts (addresses, ASNs, prefixes, next-hops, ECMP,
reachability) that ``TelemetrySample`` does not and must not hold. Nothing here
translates into, or is persisted as, a ``TelemetrySample``.

Observed vs unknown. A fact the device *reported* (an interface that is
``down``, a BGP neighbor in ``Idle``) is an observation. A fact that could not
be collected (a failed command, unparseable output) is **unknown** and is never
represented as ``down``/``Idle``/``0``: the whole facet is listed in
``NormalizedOperationalState.unavailable`` and its data is empty, and an
individual field the device did not report is ``None`` or ``UNKNOWN``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_address, ip_interface, ip_network


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


def _require_non_negative(value: int | None, field_name: str) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field_name} must be >= 0, got {value}")


def _require_unique(values: tuple[str, ...], field_name: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{field_name} must not contain duplicates")


class NodeRole(StrEnum):
    ROUTER = "router"
    HOST = "host"


class InterfaceOperState(StrEnum):
    UP = "up"
    DOWN = "down"
    UNKNOWN = "unknown"


class BgpSessionState(StrEnum):
    IDLE = "Idle"
    CONNECT = "Connect"
    ACTIVE = "Active"
    OPEN_SENT = "OpenSent"
    OPEN_CONFIRM = "OpenConfirm"
    ESTABLISHED = "Established"
    UNKNOWN = "Unknown"


class ObservationFacet(StrEnum):
    INTERFACES = "interfaces"
    BGP = "bgp"
    ROUTES = "routes"
    REACHABILITY = "reachability"


@dataclass(frozen=True, slots=True)
class CollectionSource:
    collector: str
    lab_name: str
    collection_id: str

    def __post_init__(self) -> None:
        _require_non_empty(self.collector, "CollectionSource.collector")
        _require_non_empty(self.lab_name, "CollectionSource.lab_name")
        _require_non_empty(self.collection_id, "CollectionSource.collection_id")


@dataclass(frozen=True, slots=True)
class InterfaceObservation:
    name: str
    oper_state: InterfaceOperState
    addresses: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.name, "InterfaceObservation.name")
        for address in self.addresses:
            ip_interface(address)  # raises ValueError on a malformed address
        _require_unique(self.addresses, "InterfaceObservation.addresses")


@dataclass(frozen=True, slots=True)
class BgpNeighborObservation:
    """``raw_state`` is the state string exactly as the device reported it
    (``None`` if absent). ``state`` is ``UNKNOWN`` whenever the device did not
    report a state this model recognizes (including transient FRR states such
    as ``Clearing``). ``prefixes_received`` and ``uptime_ms`` are populated
    only for an ``Established`` session: FRR reports a time-in-state and a
    prefix count for sessions in any state, which would misrepresent a down
    session as having an uptime or learned prefixes."""

    neighbor_ip: str
    remote_as: int | None
    state: BgpSessionState
    raw_state: str | None
    prefixes_received: int | None
    uptime_ms: int | None

    def __post_init__(self) -> None:
        ip_address(self.neighbor_ip)
        if self.remote_as is not None and self.remote_as <= 0:
            raise ValueError(f"BgpNeighborObservation.remote_as must be > 0, got {self.remote_as}")
        _require_non_negative(self.prefixes_received, "BgpNeighborObservation.prefixes_received")
        _require_non_negative(self.uptime_ms, "BgpNeighborObservation.uptime_ms")
        if self.state is not BgpSessionState.ESTABLISHED and (
            self.prefixes_received is not None or self.uptime_ms is not None
        ):
            raise ValueError(
                "BgpNeighborObservation.prefixes_received/uptime_ms are only valid "
                "for an Established session"
            )

    @property
    def is_established(self) -> bool:
        return self.state is BgpSessionState.ESTABLISHED


@dataclass(frozen=True, slots=True)
class BgpObservation:
    local_as: int
    router_id: str
    neighbors: tuple[BgpNeighborObservation, ...]

    def __post_init__(self) -> None:
        if self.local_as <= 0:
            raise ValueError(f"BgpObservation.local_as must be > 0, got {self.local_as}")
        ip_address(self.router_id)
        _require_unique(
            tuple(neighbor.neighbor_ip for neighbor in self.neighbors),
            "BgpObservation.neighbors",
        )

    @property
    def established_count(self) -> int:
        return sum(1 for neighbor in self.neighbors if neighbor.is_established)


@dataclass(frozen=True, slots=True)
class NextHop:
    """At least one of ``ip`` / ``interface``: a directly connected route has
    an interface and no gateway address."""

    ip: str | None
    interface: str | None

    def __post_init__(self) -> None:
        if self.ip is None and self.interface is None:
            raise ValueError("NextHop requires an ip, an interface, or both")
        if self.ip is not None:
            ip_address(self.ip)
        if self.interface is not None:
            _require_non_empty(self.interface, "NextHop.interface")


@dataclass(frozen=True, slots=True)
class RouteObservation:
    """An installed route with its active next-hops. ``ecmp_path_count`` is
    derived, never stored."""

    prefix: str
    protocol: str
    next_hops: tuple[NextHop, ...]

    def __post_init__(self) -> None:
        ip_network(self.prefix)
        _require_non_empty(self.protocol, "RouteObservation.protocol")
        if not self.next_hops:
            raise ValueError("RouteObservation requires at least one next hop")
        if len(set(self.next_hops)) != len(self.next_hops):
            raise ValueError("RouteObservation.next_hops must not contain duplicates")

    @property
    def ecmp_path_count(self) -> int:
        return len(self.next_hops)

    @property
    def is_ecmp(self) -> bool:
        return self.ecmp_path_count > 1


@dataclass(frozen=True, slots=True)
class ReachabilityObservation:
    """A completed probe. ``received == 0`` is an *observed* total loss; a
    probe that could not be run or parsed is not an observation at all (the
    ``reachability`` facet is then unavailable)."""

    from_node: str
    target: str
    sent: int
    received: int

    def __post_init__(self) -> None:
        _require_non_empty(self.from_node, "ReachabilityObservation.from_node")
        ip_address(self.target)
        if self.sent <= 0:
            raise ValueError(f"ReachabilityObservation.sent must be > 0, got {self.sent}")
        if not 0 <= self.received <= self.sent:
            raise ValueError(
                f"ReachabilityObservation.received must be within [0, {self.sent}], "
                f"got {self.received}"
            )

    @property
    def succeeded(self) -> bool:
        return self.received == self.sent


@dataclass(frozen=True, slots=True)
class UnavailableObservation:
    facet: ObservationFacet
    reason: str

    def __post_init__(self) -> None:
        _require_non_empty(self.reason, "UnavailableObservation.reason")


@dataclass(frozen=True, slots=True)
class NormalizedOperationalState:
    node_id: str
    role: NodeRole
    collected_at: datetime
    source: CollectionSource
    interfaces: tuple[InterfaceObservation, ...]
    bgp: BgpObservation | None
    routes: tuple[RouteObservation, ...]
    reachability: tuple[ReachabilityObservation, ...]
    unavailable: tuple[UnavailableObservation, ...] = ()

    def __post_init__(self) -> None:
        _require_non_empty(self.node_id, "NormalizedOperationalState.node_id")
        _require_utc(self.collected_at, "NormalizedOperationalState.collected_at")
        _require_unique(
            tuple(interface.name for interface in self.interfaces),
            "NormalizedOperationalState.interfaces",
        )
        _require_unique(
            tuple(f"{route.prefix}|{route.protocol}" for route in self.routes),
            "NormalizedOperationalState.routes",
        )
        facets = tuple(item.facet.value for item in self.unavailable)
        _require_unique(facets, "NormalizedOperationalState.unavailable")

        unavailable = {item.facet for item in self.unavailable}
        if ObservationFacet.INTERFACES in unavailable and self.interfaces:
            raise ValueError("an unavailable facet must carry no data: interfaces")
        if ObservationFacet.ROUTES in unavailable and self.routes:
            raise ValueError("an unavailable facet must carry no data: routes")
        if ObservationFacet.REACHABILITY in unavailable and self.reachability:
            raise ValueError("an unavailable facet must carry no data: reachability")

        bgp_unavailable = ObservationFacet.BGP in unavailable
        if self.role is NodeRole.HOST:
            if self.bgp is not None or bgp_unavailable:
                raise ValueError("a host has no BGP observation")
        elif (self.bgp is None) != bgp_unavailable:
            raise ValueError(
                "a router must carry a BGP observation or mark the bgp facet unavailable"
            )

    def is_unavailable(self, facet: ObservationFacet) -> bool:
        return any(item.facet is facet for item in self.unavailable)


@dataclass(frozen=True, slots=True)
class FabricOperationalState:
    """One collection pass: per-node states sharing a single ``collected_at``
    and ``collection_id`` (the correlation identifier)."""

    collection_id: str
    collected_at: datetime
    nodes: tuple[NormalizedOperationalState, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.collection_id, "FabricOperationalState.collection_id")
        _require_utc(self.collected_at, "FabricOperationalState.collected_at")
        _require_unique(tuple(node.node_id for node in self.nodes), "FabricOperationalState.nodes")
        for node in self.nodes:
            if node.collected_at != self.collected_at:
                raise ValueError(
                    f"FabricOperationalState node {node.node_id!r} collected_at differs "
                    "from the fabric collected_at"
                )
            if node.source.collection_id != self.collection_id:
                raise ValueError(
                    f"FabricOperationalState node {node.node_id!r} collection_id differs "
                    "from the fabric collection_id"
                )

    def node(self, node_id: str) -> NormalizedOperationalState:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(node_id)
