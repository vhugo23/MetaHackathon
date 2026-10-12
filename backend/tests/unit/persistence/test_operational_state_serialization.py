"""Operational-state JSON codec (NPE-1D1A): exact round-trip of every domain
field, and fail-closed decoding of anything malformed."""

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest

from meta_rne.domain.operational_state import (
    BgpSessionState,
    FabricOperationalState,
    InterfaceOperState,
    NodeRole,
    ObservationFacet,
    UnavailableObservation,
)
from meta_rne.persistence.operational_state_serialization import (
    OPERATIONAL_STATE_SCHEMA_VERSION,
    fabric_from_json,
    fabric_to_json,
)
from meta_rne.persistence.serialization import SerializationError
from operational_state_support import RICH_FIXTURE_SETS, rich_fixture_fabric

T0 = datetime(2026, 10, 7, 12, 0, 0, 123456, tzinfo=UTC)


def _roundtrip(fabric: FabricOperationalState) -> FabricOperationalState:
    # Through real JSON text, as PostgreSQL JSONB would hand it back.
    document = json.loads(json.dumps(fabric_to_json(fabric)))
    return fabric_from_json(OPERATIONAL_STATE_SCHEMA_VERSION, document)


@pytest.mark.parametrize("fixture_set", RICH_FIXTURE_SETS)
def test_fixture_fabric__round_trips_exactly(fixture_set: str) -> None:
    fabric = rich_fixture_fabric(fixture_set, T0)

    assert _roundtrip(fabric) == fabric


def test_schema_version__is_one() -> None:
    assert OPERATIONAL_STATE_SCHEMA_VERSION == 1


def test_baseline__keeps_routes_active_next_hops_reachability_and_none_values() -> None:
    fabric = rich_fixture_fabric("baseline", T0)
    restored = _roundtrip(fabric)

    ecmp = [route for node in restored.nodes for route in node.routes if route.is_ecmp]
    assert ecmp, "the baseline fixtures carry ECMP routes"
    assert any(hop.ip is not None for route in ecmp for hop in route.next_hops)
    assert any(
        hop.ip is None and hop.interface is not None
        for node in restored.nodes
        for route in node.routes
        for hop in route.next_hops
    ), "a directly connected next hop (ip None) survives"
    probes = [probe for node in restored.nodes for probe in node.reachability]
    assert len(probes) == 2
    assert all(probe.succeeded for probe in probes)
    assert restored.node("host-1").bgp is None
    assert restored.node("host-1").role is NodeRole.HOST


def test_degraded_idle__keeps_session_states_and_clears_established_only_fields() -> None:
    restored = _roundtrip(rich_fixture_fabric("degraded-idle", T0))

    neighbors = {
        (node.node_id, n.neighbor_ip): n
        for node in restored.nodes
        if node.bgp
        for n in node.bgp.neighbors
    }
    idle = neighbors[("leaf-1", "10.255.0.0")]
    assert idle.state is BgpSessionState.IDLE
    assert idle.prefixes_received is None and idle.uptime_ms is None
    assert neighbors[("spine-1", "10.255.0.1")].state is BgpSessionState.ACTIVE


def test_degraded_clearing__unknown_state_and_raw_state_survive() -> None:
    restored = _roundtrip(rich_fixture_fabric("degraded-clearing", T0))

    clearing = [
        n
        for node in restored.nodes
        if node.bgp
        for n in node.bgp.neighbors
        if n.state is BgpSessionState.UNKNOWN
    ]
    assert len(clearing) == 1
    assert clearing[0].raw_state == "Clearing"


def test_unavailable_facets_and_empty_data_survive() -> None:
    fabric = rich_fixture_fabric("baseline", T0)
    router = fabric.node("leaf-1")
    unavailable = replace(
        router,
        interfaces=(),
        routes=(),
        bgp=None,
        unavailable=(
            UnavailableObservation(ObservationFacet.INTERFACES, "ip -j addr failed"),
            UnavailableObservation(ObservationFacet.BGP, "vtysh timed out"),
            UnavailableObservation(ObservationFacet.ROUTES, "unparseable"),
        ),
    )
    nodes = tuple(unavailable if n.node_id == "leaf-1" else n for n in fabric.nodes)
    fabric = replace(fabric, nodes=nodes)

    restored = _roundtrip(fabric)

    node = restored.node("leaf-1")
    assert node.bgp is None
    assert node.interfaces == () and node.routes == ()
    assert node.is_unavailable(ObservationFacet.BGP)
    assert [(u.facet, u.reason) for u in node.unavailable] == [
        (ObservationFacet.INTERFACES, "ip -j addr failed"),
        (ObservationFacet.BGP, "vtysh timed out"),
        (ObservationFacet.ROUTES, "unparseable"),
    ]
    assert restored == fabric


def test_interface_states_and_addresses_survive() -> None:
    restored = _roundtrip(rich_fixture_fabric("baseline", T0))

    interfaces = [i for n in restored.nodes for i in n.interfaces]
    assert any(i.oper_state is InterfaceOperState.UP and i.addresses for i in interfaces)


def test_timestamp__keeps_microseconds_and_utc() -> None:
    restored = _roundtrip(rich_fixture_fabric("baseline", T0))

    assert restored.collected_at == T0
    assert restored.collected_at.microsecond == 123456
    assert all(node.collected_at == T0 for node in restored.nodes)


# --- corruption: fail closed, never default -------------------------------


def _document() -> dict[str, Any]:
    return fabric_to_json(rich_fixture_fabric("baseline", T0))


def _decode(document: Any, version: Any = OPERATIONAL_STATE_SCHEMA_VERSION) -> object:
    return fabric_from_json(version, document)


@pytest.mark.parametrize("version", [0, 2, -1, "1", None, True, 1.0])
def test_unsupported_schema_version__is_rejected(version: Any) -> None:
    with pytest.raises(SerializationError, match="schema_version"):
        _decode(_document(), version)


@pytest.mark.parametrize("document", [None, [], "x", 3])
def test_document_that_is_not_an_object__is_rejected(document: Any) -> None:
    with pytest.raises(SerializationError):
        _decode(document)


def _mutations() -> list[tuple[str, Any]]:
    def drop_top(d: dict[str, Any]) -> None:
        del d["nodes"]

    def drop_collection_id(d: dict[str, Any]) -> None:
        del d["collection_id"]

    def drop_node_bgp(d: dict[str, Any]) -> None:
        del d["nodes"][2]["bgp"]

    def drop_neighbor_state(d: dict[str, Any]) -> None:
        del d["nodes"][2]["bgp"]["neighbors"][0]["state"]

    def drop_unavailable(d: dict[str, Any]) -> None:
        del d["nodes"][0]["unavailable"]

    def nodes_not_list(d: dict[str, Any]) -> None:
        d["nodes"] = {"a": 1}

    def node_not_object(d: dict[str, Any]) -> None:
        d["nodes"][0] = "leaf-1"

    def collected_at_int(d: dict[str, Any]) -> None:
        d["collected_at"] = 1760000000

    def collected_at_naive(d: dict[str, Any]) -> None:
        d["collected_at"] = "2026-10-07T12:00:00"

    def collected_at_non_utc(d: dict[str, Any]) -> None:
        d["collected_at"] = "2026-10-07T12:00:00+02:00"

    def collected_at_garbage(d: dict[str, Any]) -> None:
        d["collected_at"] = "yesterday"

    def local_as_string(d: dict[str, Any]) -> None:
        d["nodes"][2]["bgp"]["local_as"] = "65101"

    def local_as_bool(d: dict[str, Any]) -> None:
        d["nodes"][2]["bgp"]["local_as"] = True

    def uptime_float(d: dict[str, Any]) -> None:
        d["nodes"][2]["bgp"]["neighbors"][0]["uptime_ms"] = 1.5

    def addresses_not_strings(d: dict[str, Any]) -> None:
        d["nodes"][2]["interfaces"][0]["addresses"] = [1]

    def bad_role(d: dict[str, Any]) -> None:
        d["nodes"][0]["role"] = "switch"

    def bad_bgp_state(d: dict[str, Any]) -> None:
        d["nodes"][2]["bgp"]["neighbors"][0]["state"] = "Flapping"

    def bad_oper_state(d: dict[str, Any]) -> None:
        d["nodes"][2]["interfaces"][0]["oper_state"] = "sideways"

    def bad_facet(d: dict[str, Any]) -> None:
        d["nodes"][0]["unavailable"] = [{"facet": "telemetry", "reason": "x"}]

    def bad_ip(d: dict[str, Any]) -> None:
        d["nodes"][2]["bgp"]["neighbors"][0]["neighbor_ip"] = "not-an-ip"

    def duplicate_node(d: dict[str, Any]) -> None:
        d["nodes"].append(copy.deepcopy(d["nodes"][0]))

    def node_collection_id_differs(d: dict[str, Any]) -> None:
        d["nodes"][0]["source"]["collection_id"] = "other"

    def empty_source_lab(d: dict[str, Any]) -> None:
        d["nodes"][0]["source"]["lab_name"] = " "

    def established_fields_on_down(d: dict[str, Any]) -> None:
        neighbor = d["nodes"][2]["bgp"]["neighbors"][0]
        neighbor["state"] = "Idle"
        neighbor["uptime_ms"] = 5

    def empty_next_hops(d: dict[str, Any]) -> None:
        d["nodes"][2]["routes"][0]["next_hops"] = []

    def probe_received_over_sent(d: dict[str, Any]) -> None:
        d["nodes"][0]["reachability"][0]["received"] = 99

    return [
        ("missing nodes", drop_top),
        ("missing collection_id", drop_collection_id),
        ("missing node bgp", drop_node_bgp),
        ("missing neighbor state", drop_neighbor_state),
        ("missing unavailable", drop_unavailable),
        ("nodes not a list", nodes_not_list),
        ("node not an object", node_not_object),
        ("collected_at int", collected_at_int),
        ("collected_at naive", collected_at_naive),
        ("collected_at non-UTC", collected_at_non_utc),
        ("collected_at garbage", collected_at_garbage),
        ("local_as string", local_as_string),
        ("local_as bool", local_as_bool),
        ("uptime float", uptime_float),
        ("addresses not strings", addresses_not_strings),
        ("invalid role", bad_role),
        ("invalid bgp state", bad_bgp_state),
        ("invalid oper state", bad_oper_state),
        ("invalid facet", bad_facet),
        ("invalid neighbor ip", bad_ip),
        ("duplicate node", duplicate_node),
        ("node collection_id differs", node_collection_id_differs),
        ("blank source lab", empty_source_lab),
        ("established-only fields on a down session", established_fields_on_down),
        ("route without next hops", empty_next_hops),
        ("received over sent", probe_received_over_sent),
    ]


@pytest.mark.parametrize(("label", "mutate"), _mutations(), ids=[m[0] for m in _mutations()])
def test_corrupt_document__raises_serialization_error(label: str, mutate: Any) -> None:
    document = _document()
    mutate(document)

    with pytest.raises(SerializationError):
        _decode(document)
