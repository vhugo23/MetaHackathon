"""PostgreSQL behavior of ``OperationalStateIngestionService`` (NPE-1C3B3),
using the real ``SqlAlchemyUnitOfWork`` and the partial unique index on the
migrated test database. Devices are registered through the real
``RegisterLabDevicesService``, as the CLI precondition requires."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.lab_device_registration import RegisterLabDevicesService
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from operational_state_support import fixture_fabric

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=45)

EXPECTED_PAIRS = [
    ("lab1-leaf-1", "bgp-neighbor:10.255.0.0"),
    ("lab1-spine-1", "bgp-neighbor:10.255.0.1"),
]


class _NullSink:
    def emit(self, event: Any) -> None:
        pass


def _uow_factory(session_factory: Callable[[], Session]) -> Callable[[], SqlAlchemyUnitOfWork]:
    return lambda: SqlAlchemyUnitOfWork(session_factory)  # type: ignore[arg-type,return-value]


def _service(session_factory: Callable[[], Session]) -> OperationalStateIngestionService:
    return OperationalStateIngestionService(
        _uow_factory(session_factory),  # type: ignore[arg-type]
        _NullSink(),
    )


def _register(session_factory: Callable[[], Session]) -> None:
    RegisterLabDevicesService(_uow_factory(session_factory)).register(T0)  # type: ignore[arg-type]


def _rows(session_factory: Callable[[], Session]) -> list[Any]:
    with session_factory() as session:
        return list(
            session.execute(
                text(
                    "SELECT device_id, affected_resource, source, rule_ref, status, fingerprint, "
                    "occurrence_count, created_at, last_seen_at, resolved_at, evidence "
                    "FROM incidents ORDER BY device_id"
                )
            ).all()
        )


def test_degraded__creates_exactly_two_open_incidents_with_exact_pairs(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _register(sqlalchemy_session_factory)

    result = _service(sqlalchemy_session_factory).ingest(fixture_fabric("degraded-active", T0))

    assert (result.incidents_created, result.incidents_updated) == (2, 0)
    rows = _rows(sqlalchemy_session_factory)
    assert [(r.device_id, r.affected_resource) for r in rows] == EXPECTED_PAIRS
    for row in rows:
        assert (row.source, row.rule_ref, row.status) == ("ANOMALY", "RULE-BGP-DOWN", "OPEN")
        assert row.occurrence_count == 1
        assert row.created_at == row.last_seen_at == T0


def test_degraded__previous_state_persists_as_json_null(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _register(sqlalchemy_session_factory)
    _service(sqlalchemy_session_factory).ingest(fixture_fabric("degraded-active", T0))

    with sqlalchemy_session_factory() as session:
        raw = session.execute(
            text(
                "SELECT evidence->'previous_state' IS NOT NULL AS has_key, "
                "jsonb_typeof(evidence->'previous_state') AS kind, "
                "evidence ? 'previous_state' AS key_present "
                "FROM incidents"
            )
        ).all()
        assert len(raw) == 2
        assert all(r.key_present and r.kind == "null" for r in raw)

        from meta_rne.persistence.sqlalchemy.incident_repository import (
            SqlAlchemyIncidentRepository,
        )

        incidents = SqlAlchemyIncidentRepository(session).list_all()
    assert len(incidents) == 2
    for incident in incidents:
        assert incident.evidence.previous_state is None  # type: ignore[union-attr]


def test_repeated_degraded__dedups_and_advances_last_seen(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _register(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)
    service.ingest(fixture_fabric("degraded-active", T0))
    first = _rows(sqlalchemy_session_factory)

    second_result = service.ingest(fixture_fabric("degraded-active", T1, collection_id="run-2"))

    assert (second_result.incidents_created, second_result.incidents_updated) == (0, 2)
    rows = _rows(sqlalchemy_session_factory)
    assert len(rows) == 2
    assert len({r.fingerprint for r in rows}) == 2
    assert [r.fingerprint for r in rows] == [r.fingerprint for r in first]
    for row in rows:
        assert row.status == "OPEN"
        assert row.occurrence_count == 2
        assert row.created_at == T0
        assert row.last_seen_at == T1


def test_missing_device__commits_zero_incidents(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    with sqlalchemy_session_factory() as session:
        for device_id in ("lab1-leaf-1", "lab1-spine-2", "lab1-leaf-2"):
            session.execute(
                text(
                    "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
                    "VALUES (:d, 'frr', now(), now())"
                ),
                {"d": device_id},
            )
        session.commit()

    with pytest.raises(DeviceNotFoundError) as excinfo:
        _service(sqlalchemy_session_factory).ingest(fixture_fabric("degraded-active", T0))

    assert excinfo.value.device_id == "lab1-spine-1"
    assert _rows(sqlalchemy_session_factory) == []


def test_healthy_after_degraded__incidents_unchanged_and_open(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _register(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)
    service.ingest(fixture_fabric("degraded-active", T0))
    before = _rows(sqlalchemy_session_factory)

    result = service.ingest(fixture_fabric("baseline", T1, collection_id="run-2"))

    assert result.anomalies == ()
    assert (result.incidents_created, result.incidents_updated) == (0, 0)
    after = _rows(sqlalchemy_session_factory)
    assert after == before
    assert all(r.status == "OPEN" and r.resolved_at is None for r in after)
