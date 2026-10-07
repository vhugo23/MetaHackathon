"""Shared builders for operational-state ingestion tests (NPE-1C3B3): build a
``FabricOperationalState`` from the captured Lab 1 BGP fixtures at a chosen
``collected_at``. Test-only; never imported by production code."""

from datetime import datetime
from pathlib import Path

from meta_rne.adapters.containerlab_frr.parsers import parse_bgp_summary
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.operational_state import (
    CollectionSource,
    FabricOperationalState,
    NodeRole,
    NormalizedOperationalState,
    ObservationFacet,
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
) -> FabricOperationalState:
    source = CollectionSource(collector="test", lab_name="lab1", collection_id=collection_id)
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
