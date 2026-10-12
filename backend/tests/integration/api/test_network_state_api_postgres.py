"""``GET /network/state`` on real PostgreSQL (NPE-1D1B).

The API reads through its own ``SqlAlchemyUnitOfWork`` (a separate session from
the ingestion service's) and must always return the exact latest accepted
collection: a newer collection replaces what GET returns; an exact replay and a
stale/conflicting rejected ingestion leave it unchanged. No write route exists.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.application.lab_device_registration import RegisterLabDevicesService
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.domain.operational_state import FabricOperationalState
from meta_rne.lab1_identity import LAB1_LAB_NAME
from meta_rne.persistence.errors import OperationalStateConflictError, StaleOperationalStateError
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from operational_state_support import rich_fixture_fabric

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=45)
T2 = T0 + timedelta(seconds=90)
RETRIEVED = T2 + timedelta(minutes=5)

SessionFactory = Callable[[], Session]


class _Sink:
    def emit(self, event: Any) -> None:
        pass


def _fabric(fixture_set: str, at: datetime, collection_id: str) -> FabricOperationalState:
    return rich_fixture_fabric(
        fixture_set, at, collection_id, lab_name=LAB1_LAB_NAME, collector="containerlab-frr"
    )


def _ingestion(session_factory: SessionFactory) -> OperationalStateIngestionService:
    return OperationalStateIngestionService(
        lambda: SqlAlchemyUnitOfWork(session_factory),  # type: ignore[arg-type,return-value]
        _Sink(),  # type: ignore[arg-type]
    )


def _register(session_factory: SessionFactory) -> None:
    RegisterLabDevicesService(lambda: SqlAlchemyUnitOfWork(session_factory)).register(T0)  # type: ignore[arg-type,return-value]


def _client(session_factory: SessionFactory) -> TestClient:
    return TestClient(
        create_app(
            unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),  # type: ignore[arg-type,return-value]
            adapter_registry=AdapterRegistry([CiscoAdapter()]),
            clock=lambda: RETRIEVED,
            seed_on_startup=False,
        )
    )


def _get(client: TestClient) -> dict[str, Any]:
    response = client.get("/network/state")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def test_migration_head_includes_0005_operational_state_table(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    with sqlalchemy_session_factory() as session:
        revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        table = session.execute(
            text("SELECT to_regclass('public.operational_state_latest')")
        ).scalar_one()

    assert revision == "0005_operational_state_latest"
    assert table is not None


def test_no_state__404(sqlalchemy_session_factory: SessionFactory) -> None:
    with sqlalchemy_session_factory() as session:
        session.execute(text("DELETE FROM operational_state_latest"))
        session.commit()

    response = _client(sqlalchemy_session_factory).get("/network/state")

    assert response.status_code == 404
    assert response.json()["code"] == "operational_state_not_found"


def test_get_returns_exact_latest_ingested_collection_via_a_separate_unit_of_work(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    fabric = _fabric("baseline", T0, "run-1")
    _ingestion(sqlalchemy_session_factory).ingest(fabric)

    body = _get(_client(sqlalchemy_session_factory))

    assert body["collection_id"] == "run-1"
    assert body["collected_at"] == "2026-10-07T12:00:00Z"
    assert body["retrieved_at"] == "2026-10-07T12:06:30Z"
    assert body["source"] == {"collector": "containerlab-frr", "lab_name": LAB1_LAB_NAME}
    assert len(body["nodes"]) == 6
    nodes = {n["node_id"]: n for n in body["nodes"]}
    assert nodes["leaf-1"]["device_id"] == "lab1-leaf-1"
    assert nodes["host-1"]["device_id"] is None
    assert nodes["leaf-1"]["bgp"]["established_count"] == 2


def test_newer_collection_replaces_what_get_returns(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    service = _ingestion(sqlalchemy_session_factory)
    client = _client(sqlalchemy_session_factory)
    service.ingest(_fabric("baseline", T0, "run-1"))
    assert _get(client)["collection_id"] == "run-1"

    service.ingest(_fabric("degraded-active", T1, "run-2"))

    body = _get(client)
    assert body["collection_id"] == "run-2"
    assert body["collected_at"] == "2026-10-07T12:00:45Z"
    leaf = {n["node_id"]: n for n in body["nodes"]}["leaf-1"]
    assert leaf["bgp"]["established_count"] == 1


def test_exact_replay_does_not_alter_get(sqlalchemy_session_factory: SessionFactory) -> None:
    _register(sqlalchemy_session_factory)
    service = _ingestion(sqlalchemy_session_factory)
    client = _client(sqlalchemy_session_factory)
    service.ingest(_fabric("degraded-active", T1, "run-2"))
    before = _get(client)

    service.ingest(_fabric("degraded-active", T1, "run-2"))

    assert _get(client) == before


def test_stale_and_conflicting_rejected_ingestion_do_not_alter_get(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    service = _ingestion(sqlalchemy_session_factory)
    client = _client(sqlalchemy_session_factory)
    service.ingest(_fabric("baseline", T1, "run-2"))
    before = _get(client)

    with pytest.raises(StaleOperationalStateError):
        service.ingest(_fabric("degraded-active", T0, "run-1"))
    assert _get(client) == before

    # same collection_id and collected_at as the stored state, different content
    conflicting = _fabric("degraded-active", T1, "run-2")
    with pytest.raises(OperationalStateConflictError):
        service.ingest(conflicting)
    assert _get(client) == before


def test_no_write_route_for_network_state(sqlalchemy_session_factory: SessionFactory) -> None:
    client = _client(sqlalchemy_session_factory)

    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/network/state").status_code == 405
