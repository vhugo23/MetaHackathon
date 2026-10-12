"""API contract for ``GET /network/state`` (NPE-1D1B): the latest ingested
``FabricOperationalState`` exposed read-only. Isolated ``create_app`` per test.
Fixture fabrics use the real Lab 1 identity (``meta-rne-bgp``)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.operational_state import (
    FabricOperationalState,
    ObservationFacet,
    UnavailableObservation,
)
from meta_rne.lab1_identity import LAB1_LAB_NAME
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from operational_state_support import rich_fixture_fabric

COLLECTED = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
RETRIEVED = COLLECTED + timedelta(seconds=7)

ROUTER_DEVICE_IDS = {
    "spine-1": "lab1-spine-1",
    "spine-2": "lab1-spine-2",
    "leaf-1": "lab1-leaf-1",
    "leaf-2": "lab1-leaf-2",
}


def _fabric(fixture_set: str = "baseline", collection_id: str = "run-1") -> FabricOperationalState:
    return rich_fixture_fabric(
        fixture_set,
        COLLECTED,
        collection_id,
        lab_name=LAB1_LAB_NAME,
        collector="containerlab-frr",
    )


def _client(fabric: FabricOperationalState | None = None) -> TestClient:
    store = InMemoryStore()
    if fabric is not None:
        uow = InMemoryUnitOfWork(store)
        uow.operational_state.save_latest(fabric)
        uow.commit()
        uow.close()
    return TestClient(
        create_app(
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            adapter_registry=AdapterRegistry([CiscoAdapter()]),
            clock=lambda: RETRIEVED,
            seed_on_startup=False,
        )
    )


def _body(fabric: FabricOperationalState) -> dict[str, Any]:
    response = _client(fabric).get("/network/state")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _nodes(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["node_id"]: node for node in body["nodes"]}


def _with_node(
    fabric: FabricOperationalState, node_id: str, **changes: Any
) -> FabricOperationalState:
    return replace(
        fabric,
        nodes=tuple(
            replace(node, **changes) if node.node_id == node_id else node for node in fabric.nodes
        ),
    )


# A. no persisted state


def test_no_state__404_operational_state_not_found_never_200() -> None:
    response = _client().get("/network/state")

    assert response.status_code == 404
    assert response.json() == {
        "code": "operational_state_not_found",
        "detail": "No operational state is available for lab 'meta-rne-bgp'.",
    }


# B. baseline


def test_baseline__top_level_shape_and_six_nodes() -> None:
    body = _body(_fabric())

    assert set(body) == {"collection_id", "collected_at", "retrieved_at", "source", "nodes"}
    assert body["collection_id"] == "run-1"
    assert body["collected_at"] == "2026-10-07T12:00:00Z"
    assert body["retrieved_at"] == "2026-10-07T12:00:07Z"
    assert body["source"] == {"collector": "containerlab-frr", "lab_name": "meta-rne-bgp"}
    assert sorted(_nodes(body)) == ["host-1", "host-2", "leaf-1", "leaf-2", "spine-1", "spine-2"]
    for node in body["nodes"]:
        assert set(node) == {
            "node_id",
            "device_id",
            "role",
            "interfaces",
            "bgp",
            "routes",
            "reachability",
            "unavailable",
        }


def test_baseline__interfaces_preserve_state_and_addresses() -> None:
    fabric = _fabric()
    leaf = _nodes(_body(fabric))["leaf-1"]

    assert leaf["interfaces"] == [
        {"name": i.name, "oper_state": i.oper_state.value, "addresses": list(i.addresses)}
        for i in fabric.node("leaf-1").interfaces
    ]


def test_baseline__bgp_established_count_and_neighbor_fields() -> None:
    bgp = _nodes(_body(_fabric()))["leaf-1"]["bgp"]

    assert bgp["established_count"] == 2
    assert set(bgp) == {"local_as", "router_id", "established_count", "neighbors"}
    neighbor = bgp["neighbors"][0]
    assert set(neighbor) == {
        "neighbor_ip",
        "remote_as",
        "state",
        "raw_state",
        "prefixes_received",
        "uptime_ms",
    }
    assert neighbor["state"] == "Established"
    assert neighbor["prefixes_received"] is not None
    assert neighbor["uptime_ms"] is not None


# C / D. device mapping


def test_routers__have_platform_device_ids() -> None:
    nodes = _nodes(_body(_fabric()))

    for node_id, device_id in ROUTER_DEVICE_IDS.items():
        assert nodes[node_id]["device_id"] == device_id
        assert nodes[node_id]["role"] == "router"


def test_hosts__device_id_is_null() -> None:
    nodes = _nodes(_body(_fabric()))

    for host in ("host-1", "host-2"):
        assert nodes[host]["device_id"] is None
        assert nodes[host]["role"] == "host"


# E / F / G. degraded fixtures


def test_degraded_active__failure_states_and_truthful_ecmp() -> None:
    nodes = _nodes(_body(_fabric("degraded-active")))

    leaf = nodes["leaf-1"]
    assert leaf["bgp"]["established_count"] == 1
    neighbors = {n["neighbor_ip"]: n for n in leaf["bgp"]["neighbors"]}
    assert neighbors["10.255.0.0"]["state"] == "Active"
    assert neighbors["10.255.0.0"]["prefixes_received"] is None
    assert neighbors["10.255.0.0"]["uptime_ms"] is None
    assert neighbors["10.255.0.2"]["state"] == "Established"
    remote = {r["prefix"]: r for r in leaf["routes"] if r["protocol"] == "bgp"}
    assert remote["10.1.2.0/24"]["ecmp_path_count"] == 1
    assert len(remote["10.1.2.0/24"]["next_hops"]) == 1
    assert nodes["spine-2"]["bgp"]["established_count"] == 2


def test_degraded_idle__mixed_idle_and_active_preserved() -> None:
    nodes = _nodes(_body(_fabric("degraded-idle")))

    assert nodes["leaf-1"]["bgp"]["neighbors"][0]["state"] == "Idle"
    assert nodes["spine-1"]["bgp"]["neighbors"][0]["state"] == "Active"
    assert sorted(nodes) == ["leaf-1", "spine-1"]


def test_degraded_clearing__unknown_state_keeps_raw_state() -> None:
    nodes = _nodes(_body(_fabric("degraded-clearing")))

    neighbor = nodes["leaf-1"]["bgp"]["neighbors"][0]
    assert neighbor["state"] == "Unknown"
    assert neighbor["raw_state"] == "Clearing"
    assert nodes["leaf-1"]["bgp"]["established_count"] == 1
    assert nodes["spine-1"]["bgp"]["neighbors"][0]["state"] == "Idle"


# H. unavailable facets vs observed-empty vs not applicable


def test_unavailable_bgp_routes_interfaces__null_with_reason_not_empty() -> None:
    fabric = _with_node(
        _fabric(),
        "leaf-1",
        interfaces=(),
        bgp=None,
        routes=(),
        unavailable=(
            UnavailableObservation(ObservationFacet.INTERFACES, "ip failed"),
            UnavailableObservation(ObservationFacet.BGP, "vtysh failed"),
            UnavailableObservation(ObservationFacet.ROUTES, "route parse failed"),
        ),
    )

    leaf = _nodes(_body(fabric))["leaf-1"]

    assert leaf["interfaces"] is None
    assert leaf["bgp"] is None
    assert leaf["routes"] is None
    assert leaf["unavailable"] == [
        {"facet": "interfaces", "reason": "ip failed"},
        {"facet": "bgp", "reason": "vtysh failed"},
        {"facet": "routes", "reason": "route parse failed"},
    ]


def test_observed_empty_routes_and_interfaces__empty_arrays_not_null() -> None:
    fabric = _with_node(_fabric(), "leaf-1", interfaces=(), routes=())

    leaf = _nodes(_body(fabric))["leaf-1"]

    assert leaf["interfaces"] == []
    assert leaf["routes"] == []
    assert leaf["unavailable"] == []


def test_not_applicable__router_reachability_and_host_bgp_routes_are_null() -> None:
    nodes = _nodes(_body(_fabric()))

    assert nodes["leaf-1"]["reachability"] is None
    assert nodes["host-1"]["bgp"] is None
    assert nodes["host-1"]["routes"] is None


def test_unavailable_host_reachability__null_with_reason() -> None:
    fabric = _with_node(
        _fabric(),
        "host-1",
        reachability=(),
        unavailable=(UnavailableObservation(ObservationFacet.REACHABILITY, "ping did not run"),),
    )

    host = _nodes(_body(fabric))["host-1"]

    assert host["reachability"] is None
    assert host["unavailable"] == [{"facet": "reachability", "reason": "ping did not run"}]


# I. routes / ECMP


def test_baseline__routes_ecmp_and_active_next_hops_only() -> None:
    fabric = _fabric()
    leaf = _nodes(_body(fabric))["leaf-1"]
    domain = {(r.prefix, r.protocol): r for r in fabric.node("leaf-1").routes}

    route = next(r for r in leaf["routes"] if r["prefix"] == "10.1.2.0/24")
    assert set(route) == {"prefix", "protocol", "ecmp_path_count", "next_hops"}
    assert route["protocol"] == "bgp"
    assert route["ecmp_path_count"] == 2
    expected = domain[("10.1.2.0/24", "bgp")].next_hops
    assert route["next_hops"] == [{"ip": h.ip, "interface": h.interface} for h in expected]
    for r in leaf["routes"]:
        assert r["ecmp_path_count"] == len(r["next_hops"])


# J. reachability


def test_baseline__host_reachability_fields() -> None:
    nodes = _nodes(_body(_fabric()))

    assert nodes["host-1"]["reachability"] == [
        {"from_node": "host-1", "target": "10.1.2.10", "sent": 3, "received": 3, "succeeded": True}
    ]


def test_total_loss_reachability__observed_not_succeeded() -> None:
    fabric = _fabric()
    lost = replace(fabric.node("host-1").reachability[0], received=0)

    body = _body(_with_node(fabric, "host-1", reachability=(lost,)))

    assert _nodes(body)["host-1"]["reachability"] == [
        {"from_node": "host-1", "target": "10.1.2.10", "sent": 3, "received": 0, "succeeded": False}
    ]


# corrupt data fails closed


def test_unknown_router_node__fails_closed_500_not_invented_device_id() -> None:
    fabric = _fabric()
    renamed = tuple(
        replace(n, node_id="leaf-9") if n.node_id == "leaf-1" else n for n in fabric.nodes
    )

    response = _client(replace(fabric, nodes=renamed)).get("/network/state")

    assert response.status_code == 500
    assert response.json()["code"] == "serialization_error"


# read-only


def test_write_methods_on_network_state__not_allowed() -> None:
    client = _client(_fabric())

    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/network/state").status_code == 405


def test_repeated_get__same_state() -> None:
    client = _client(_fabric())

    assert client.get("/network/state").json() == client.get("/network/state").json()
