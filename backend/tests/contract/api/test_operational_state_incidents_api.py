"""``GET /incidents`` represents incidents persisted by
``OperationalStateIngestionService`` (NPE-1C3B3) through the unchanged
``IncidentResponse`` schema, including a null ``previous_state``."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from operational_state_support import REGISTERED_DEVICE_IDS, fixture_fabric, frr_device

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)


class _NullSink:
    def emit(self, event: object) -> None:
        pass


def test_get_incidents__lists_operational_state_bgp_incidents() -> None:
    store = InMemoryStore()
    for device_id in REGISTERED_DEVICE_IDS:
        store.devices[device_id] = frr_device(device_id, T0)
    OperationalStateIngestionService(
        lambda: InMemoryUnitOfWork(store),
        _NullSink(),  # type: ignore[arg-type]
    ).ingest(fixture_fabric("degraded-active", T0))
    client = TestClient(
        create_app(
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            clock=lambda: T0,
            snapshot_id_factory=lambda: "snap-1",
            adapter_registry=AdapterRegistry([]),
            seed_on_startup=False,
        )
    )

    response = client.get("/incidents")

    assert response.status_code == 200
    body = sorted(response.json(), key=lambda item: item["device_id"])
    assert [(i["device_id"], i["affected_resource"]) for i in body] == [
        ("lab1-leaf-1", "bgp-neighbor:10.255.0.0"),
        ("lab1-spine-1", "bgp-neighbor:10.255.0.1"),
    ]
    for item in body:
        assert item["source"] == "ANOMALY"
        assert item["rule_ref"] == "RULE-BGP-DOWN"
        assert item["status"] == "OPEN"
        assert item["occurrence_count"] == 1
        assert item["evidence"]["previous_state"] is None
        assert item["evidence"]["state"] == "Active"


def test_get_incidents__shows_resolved_after_operational_recovery() -> None:
    store = InMemoryStore()
    for device_id in REGISTERED_DEVICE_IDS:
        store.devices[device_id] = frr_device(device_id, T0)
    service = OperationalStateIngestionService(
        lambda: InMemoryUnitOfWork(store),
        _NullSink(),  # type: ignore[arg-type]
    )
    t1 = T0 + timedelta(seconds=60)
    service.ingest(fixture_fabric("degraded-active", T0))
    service.ingest(fixture_fabric("baseline", t1, collection_id="run-2"))
    client = TestClient(
        create_app(
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            clock=lambda: t1,
            snapshot_id_factory=lambda: "snap-1",
            adapter_registry=AdapterRegistry([]),
            seed_on_startup=False,
        )
    )

    body = client.get("/incidents").json()

    assert len(body) == 2
    for item in body:
        assert item["status"] == "RESOLVED"
        assert item["resolved_at"] == "2026-10-07T12:01:00Z"
        assert item["last_seen_at"] == "2026-10-07T12:00:00Z"
    # The manual endpoint stays an idempotent no-op on an auto-resolved incident.
    again = client.post(f"/incidents/{body[0]['incident_id']}/resolve")
    assert again.status_code == 200
    assert again.json()["resolved_at"] == "2026-10-07T12:01:00Z"
