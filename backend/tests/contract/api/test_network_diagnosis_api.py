"""API contract for ``GET /network/diagnosis`` (NPE-1E1B): on-demand
deterministic RCA over the latest ingested ``FabricOperationalState``, with
OPEN incidents linked as correlation metadata. Isolated ``create_app`` per test."""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.domain.operational_state import BgpSessionState, FabricOperationalState
from meta_rne.lab1_identity import LAB1_LAB_NAME
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from operational_state_support import (
    REGISTERED_DEVICE_IDS,
    frr_device,
    rich_fixture_fabric,
    with_neighbor_states,
)

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)

EVIDENCE_KIND_ORDER = (
    "INTERFACE_STATE",
    "BGP_SESSION",
    "ADDRESS_MATCH",
    "ROUTE_NEXT_HOP",
    "ECMP_PATHS",
    "REACHABILITY",
)
LEAF = ("leaf-1", "10.255.0.0")
SPINE = ("spine-1", "10.255.0.1")


class _NullSink:
    def emit(self, event: object) -> None:
        pass


def _fabric(fixture_set: str) -> FabricOperationalState:
    return rich_fixture_fabric(
        fixture_set, T0, "run-1", lab_name=LAB1_LAB_NAME, collector="containerlab-frr"
    )


def _client(fabric: FabricOperationalState | None = None, ingest: bool = True) -> TestClient:
    store = InMemoryStore()
    for device_id in REGISTERED_DEVICE_IDS:
        store.devices[device_id] = frr_device(device_id, T0)
    if fabric is not None:
        if ingest:
            OperationalStateIngestionService(
                lambda: InMemoryUnitOfWork(store),
                _NullSink(),  # type: ignore[arg-type]
            ).ingest(fabric)
        else:
            uow = InMemoryUnitOfWork(store)
            uow.operational_state.save_latest(fabric)
            uow.commit()
            uow.close()
    return TestClient(
        create_app(
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            adapter_registry=AdapterRegistry([]),
            clock=lambda: T0,
            seed_on_startup=False,
        )
    )


def _get(fabric: FabricOperationalState, ingest: bool = True) -> dict[str, Any]:
    response = _client(fabric, ingest).get("/network/diagnosis")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_no_state__404_operational_state_not_found() -> None:
    response = _client().get("/network/diagnosis")

    assert response.status_code == 404
    assert response.json()["code"] == "operational_state_not_found"


def test_healthy__200_with_empty_diagnoses() -> None:
    body = _get(_fabric("baseline"))

    assert body == {
        "collection_id": "run-1",
        "collected_at": "2026-10-07T12:00:00Z",
        "diagnoses": [],
    }


def test_degraded__single_physical_link_failure_with_exact_shape() -> None:
    body = _get(_fabric("degraded-active"))

    assert set(body) == {"collection_id", "collected_at", "diagnoses"}
    assert len(body["diagnoses"]) == 1
    diagnosis = body["diagnoses"][0]
    assert set(diagnosis) == {
        "diagnosis_type",
        "confidence",
        "resource",
        "evidence",
        "symptom_keys",
        "linked_incidents",
    }
    assert diagnosis["diagnosis_type"] == "PHYSICAL_LINK_FAILURE"
    assert diagnosis["confidence"] == "HIGH"
    assert diagnosis["resource"] == {
        "resource_id": "leaf-1:eth1--spine-1:eth1",
        "endpoint_a": {"node_id": "leaf-1", "interface": "eth1"},
        "endpoint_b": {"node_id": "spine-1", "interface": "eth1"},
    }


def test_degraded__symptom_keys_and_linked_incidents() -> None:
    diagnosis = _get(_fabric("degraded-active"))["diagnoses"][0]

    assert diagnosis["symptom_keys"] == [
        {"device_id": "lab1-leaf-1", "affected_resource": "bgp-neighbor:10.255.0.0"},
        {"device_id": "lab1-spine-1", "affected_resource": "bgp-neighbor:10.255.0.1"},
    ]
    linked = diagnosis["linked_incidents"]
    assert [(i["device_id"], i["affected_resource"]) for i in linked] == [
        ("lab1-leaf-1", "bgp-neighbor:10.255.0.0"),
        ("lab1-spine-1", "bgp-neighbor:10.255.0.1"),
    ]
    for item in linked:
        assert set(item) == {
            "incident_id",
            "device_id",
            "affected_resource",
            "severity",
            "status",
            "created_at",
            "last_seen_at",
            "occurrence_count",
        }
        assert item["status"] == "OPEN"
        assert item["severity"] == "Critical"
        assert item["occurrence_count"] == 1


def test_degraded_without_incidents__diagnosis_present_with_empty_links() -> None:
    diagnosis = _get(_fabric("degraded-active"), ingest=False)["diagnoses"][0]

    assert diagnosis["diagnosis_type"] == "PHYSICAL_LINK_FAILURE"
    assert diagnosis["linked_incidents"] == []
    assert len(diagnosis["symptom_keys"]) == 2


def test_degraded__evidence_is_structured_with_one_shape_per_kind() -> None:
    evidence = _get(_fabric("degraded-active"))["diagnoses"][0]["evidence"]

    by_kind: dict[str, list[dict[str, Any]]] = {}
    for item in evidence:
        assert set(item) == {"kind", "role", "node_id", "subject", "observed"}
        by_kind.setdefault(item["kind"], []).append(item)
    assert set(by_kind) >= {
        "INTERFACE_STATE",
        "BGP_SESSION",
        "ROUTE_NEXT_HOP",
        "ECMP_PATHS",
        "REACHABILITY",
    }
    for item in by_kind["INTERFACE_STATE"]:
        assert set(item["observed"]) == {"interface_name", "state"}
        assert (item["role"], item["observed"]["state"]) == ("SUPPORTS", "DOWN")
    for item in by_kind["BGP_SESSION"]:
        assert set(item["observed"]) == {"neighbor_ip", "state", "raw_state"}
    for item in by_kind["ROUTE_NEXT_HOP"]:
        assert set(item["observed"]) == {"next_hop_ip", "interface_name", "active_route_count"}
    for item in by_kind["ECMP_PATHS"]:
        assert set(item["observed"]) == {
            "prefix",
            "observed_paths",
            "expected_paths",
            "link_next_hop_ip",
            "link_next_hop_present",
        }
    for item in by_kind["REACHABILITY"]:
        assert set(item["observed"]) == {"target", "sent", "received"}
    # Engine order is preserved: evidence is sorted by kind declaration order.
    order = list(EVIDENCE_KIND_ORDER)
    kinds = [item["kind"] for item in evidence]
    assert kinds == sorted(kinds, key=order.index)


def test_both_bgp_sessions_down_with_interfaces_up__bgp_session_failure() -> None:
    fabric = with_neighbor_states(
        _fabric("baseline"),
        {(LEAF[0], LEAF[1]): BgpSessionState.ACTIVE, (SPINE[0], SPINE[1]): BgpSessionState.IDLE},
    )

    diagnosis = _get(fabric)["diagnoses"][0]

    assert diagnosis["diagnosis_type"] == "BGP_SESSION_FAILURE"
    assert diagnosis["confidence"] == "HIGH"
    assert diagnosis["resource"]["resource_id"] == "leaf-1:eth1--spine-1:eth1"
    assert len(diagnosis["linked_incidents"]) == 2


def test_one_side_down_other_established__insufficient_evidence_with_null_confidence() -> None:
    fabric = with_neighbor_states(_fabric("baseline"), {(LEAF[0], LEAF[1]): BgpSessionState.ACTIVE})

    diagnosis = _get(fabric)["diagnoses"][0]

    assert diagnosis["diagnosis_type"] == "INSUFFICIENT_EVIDENCE"
    assert diagnosis["confidence"] is None
    roles = {item["role"] for item in diagnosis["evidence"]}
    assert roles & {"UNKNOWN", "CONTRADICTS"}
    assert len(diagnosis["linked_incidents"]) == 1
