"""Shared builders for operational-state ingestion tests (NPE-1C3B3): build a
``FabricOperationalState`` from the captured Lab 1 BGP fixtures at a chosen
``collected_at``. Test-only; never imported by production code."""

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from meta_rne.adapters.containerlab_frr.parsers import (
    parse_bgp_summary,
    parse_frr_routes,
    parse_ip_json_interfaces,
    parse_ip_oneline_interfaces,
    parse_ping_summary,
)
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.operational_state import (
    BgpSessionState,
    CollectionSource,
    FabricOperationalState,
    NodeRole,
    NormalizedOperationalState,
    ObservationFacet,
    ReachabilityObservation,
    UnavailableObservation,
)

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "containerlab_frr"
ROUTERS = ("spine-1", "spine-2", "leaf-1", "leaf-2")
REGISTERED_DEVICE_IDS = tuple(f"lab1-{node}" for node in ROUTERS)


def fixture_fabric(
    fixture_set: str,
    collected_at: datetime,
    collection_id: str = "run-1",
    routers: tuple[str, ...] = ROUTERS,
    lab_name: str = "lab1",
) -> FabricOperationalState:
    source = CollectionSource(collector="test", lab_name=lab_name, collection_id=collection_id)
    nodes = [
        NormalizedOperationalState(
            node_id=node,
            role=NodeRole.ROUTER,
            collected_at=collected_at,
            source=source,
            interfaces=(),
            bgp=parse_bgp_summary(
                (FIXTURE_ROOT / fixture_set / node / "show_bgp_summary.json").read_text(
                    encoding="utf-8"
                )
            ),
            routes=(),
            reachability=(),
        )
        for node in routers
    ]
    nodes.extend(
        NormalizedOperationalState(
            node_id=host,
            role=NodeRole.HOST,
            collected_at=collected_at,
            source=source,
            interfaces=(),
            bgp=None,
            routes=(),
            reachability=(),
        )
        for host in ("host-1", "host-2")
    )
    return FabricOperationalState(
        collection_id=collection_id, collected_at=collected_at, nodes=tuple(nodes)
    )


def unavailable_fabric(collected_at: datetime) -> FabricOperationalState:
    """Every router's BGP facet unavailable (observation uncertainty)."""
    source = CollectionSource(collector="test", lab_name="lab1", collection_id="run-unavail")
    nodes = tuple(
        NormalizedOperationalState(
            node_id=node,
            role=NodeRole.ROUTER,
            collected_at=collected_at,
            source=source,
            interfaces=(),
            bgp=None,
            routes=(),
            reachability=(),
            unavailable=(UnavailableObservation(ObservationFacet.BGP, "collection failed"),),
        )
        for node in ROUTERS
    )
    return FabricOperationalState(
        collection_id="run-unavail", collected_at=collected_at, nodes=nodes
    )


def frr_device(device_id: str, at: datetime) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.FRR,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=at,
        updated_at=at,
    )


def with_neighbor_states(
    fabric: FabricOperationalState,
    overrides: dict[tuple[str, str], BgpSessionState | None],
) -> FabricOperationalState:
    """Return ``fabric`` with chosen ``(node_id, neighbor_ip)`` BGP states
    replaced; a ``None`` value removes that neighbor (a missing neighbor)."""
    nodes = []
    for node in fabric.nodes:
        if node.bgp is None:
            nodes.append(node)
            continue
        neighbors = []
        for neighbor in node.bgp.neighbors:
            key = (node.node_id, neighbor.neighbor_ip)
            if key not in overrides:
                neighbors.append(neighbor)
                continue
            state = overrides[key]
            if state is None:
                continue
            established = state is BgpSessionState.ESTABLISHED
            neighbors.append(
                replace(
                    neighbor,
                    state=state,
                    raw_state=state.value,
                    prefixes_received=neighbor.prefixes_received or 0 if established else None,
                    uptime_ms=neighbor.uptime_ms or 0 if established else None,
                )
            )
        nodes.append(replace(node, bgp=replace(node.bgp, neighbors=tuple(neighbors))))
    return replace(fabric, nodes=tuple(nodes))


RICH_FIXTURE_SETS = ("baseline", "degraded-active", "degraded-idle", "degraded-clearing")


def rich_fixture_fabric(
    fixture_set: str,
    collected_at: datetime,
    collection_id: str = "run-1",
    lab_name: str = "lab1",
    collector: str = "test",
) -> FabricOperationalState:
    """Every facet the captured fixtures carry (interfaces, BGP, routes with
    active next hops, host reachability) for each node directory present in
    ``fixture_set``. The degraded-idle / degraded-clearing captures only hold
    leaf-1 and spine-1."""
    source = CollectionSource(collector=collector, lab_name=lab_name, collection_id=collection_id)
    root = FIXTURE_ROOT / fixture_set
    nodes = []
    for node_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        node_id = node_dir.name

        def read(name: str, directory: Path = node_dir) -> str:
            return (directory / name).read_text(encoding="utf-8")

        if node_id.startswith("host-"):
            ping = next(node_dir.glob("ping_*.txt"))
            sent, received = parse_ping_summary(ping.read_text(encoding="utf-8"))
            nodes.append(
                NormalizedOperationalState(
                    node_id=node_id,
                    role=NodeRole.HOST,
                    collected_at=collected_at,
                    source=source,
                    interfaces=parse_ip_oneline_interfaces(
                        read("ip_o_link.txt"), read("ip_o_addr.txt")
                    ),
                    bgp=None,
                    routes=(),
                    reachability=(
                        ReachabilityObservation(
                            from_node=node_id,
                            target=ping.stem.removeprefix("ping_"),
                            sent=sent,
                            received=received,
                        ),
                    ),
                )
            )
        else:
            nodes.append(
                NormalizedOperationalState(
                    node_id=node_id,
                    role=NodeRole.ROUTER,
                    collected_at=collected_at,
                    source=source,
                    interfaces=parse_ip_json_interfaces(read("ip_j_addr.json")),
                    bgp=parse_bgp_summary(read("show_bgp_summary.json")),
                    routes=parse_frr_routes(read("show_ip_route.json")),
                    reachability=(),
                )
            )
    return FabricOperationalState(
        collection_id=collection_id, collected_at=collected_at, nodes=tuple(nodes)
    )
