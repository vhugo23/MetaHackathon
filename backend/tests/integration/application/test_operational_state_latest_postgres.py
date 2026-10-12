"""Latest operational state + incident lifecycle on real PostgreSQL
(NPE-1D1A): one transaction, real rollback, exact-replay idempotency.

Uses the real ``SqlAlchemyUnitOfWork`` and ``RegisterLabDevicesService`` as the
CLI precondition requires.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.lab_device_registration import RegisterLabDevicesService
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.domain.operational_state import FabricOperationalState, OperationalStateSaveOutcome
from meta_rne.persistence.errors import OperationalStateConflictError, StaleOperationalStateError
from meta_rne.persistence.serialization import SerializationError
from meta_rne.persistence.sqlalchemy import operational_state_repository
from meta_rne.persistence.sqlalchemy.operational_state_repository import (
    SqlAlchemyOperationalStateRepository,
)
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from operational_state_support import fixture_fabric, rich_fixture_fabric

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=45)
T2 = T0 + timedelta(seconds=90)

SessionFactory = Callable[[], Session]


class _Sink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)


class _FailingCommitUow(SqlAlchemyUnitOfWork):
    def commit(self) -> None:
        raise RuntimeError("commit failed")


def _service(
    session_factory: SessionFactory,
    sink: _Sink | None = None,
    uow_class: type[SqlAlchemyUnitOfWork] = SqlAlchemyUnitOfWork,
) -> OperationalStateIngestionService:
    return OperationalStateIngestionService(
        lambda: uow_class(session_factory),  # type: ignore[arg-type,return-value]
        sink or _Sink(),
    )


def _register(session_factory: SessionFactory) -> None:
    RegisterLabDevicesService(lambda: SqlAlchemyUnitOfWork(session_factory)).register(T0)  # type: ignore[arg-type,return-value]


def _latest(session_factory: SessionFactory) -> FabricOperationalState | None:
    with session_factory() as session:
        return SqlAlchemyOperationalStateRepository(session).get_latest("lab1")


def _incident_rows(session_factory: SessionFactory) -> list[Any]:
    with session_factory() as session:
        return list(
            session.execute(
                text(
                    "SELECT incident_id, device_id, status, occurrence_count, created_at, "
                    "last_seen_at, resolved_at, updated_at FROM incidents ORDER BY device_id"
                )
            ).all()
        )


def test_each_collection_persists_as_the_labs_latest_state(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)
    baseline = rich_fixture_fabric("baseline", T0, "run-1")
    degraded = rich_fixture_fabric("degraded-active", T1, "run-2")
    healthy = rich_fixture_fabric("baseline", T2, "run-3")

    first = service.ingest(baseline)
    assert first.state_outcome is OperationalStateSaveOutcome.CREATED
    assert _latest(sqlalchemy_session_factory) == baseline

    second = service.ingest(degraded)
    assert second.state_outcome is OperationalStateSaveOutcome.REPLACED
    assert second.incidents_created == 2
    assert _latest(sqlalchemy_session_factory) == degraded
    assert len(_incident_rows(sqlalchemy_session_factory)) == 2

    third = service.ingest(healthy)
    assert third.state_outcome is OperationalStateSaveOutcome.REPLACED
    assert third.incidents_resolved == 2
    assert _latest(sqlalchemy_session_factory) == healthy
    assert {r.status for r in _incident_rows(sqlalchemy_session_factory)} == {"RESOLVED"}


def test_exact_replay__changes_neither_latest_state_nor_incidents(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    sink = _Sink()
    service = _service(sqlalchemy_session_factory, sink)
    fabric = rich_fixture_fabric("degraded-active", T0, "run-1")
    service.ingest(fabric)
    incidents_before = _incident_rows(sqlalchemy_session_factory)
    sink.events.clear()

    for _ in range(3):
        replay = service.ingest(rich_fixture_fabric("degraded-active", T0, "run-1"))
        assert replay.state_outcome is OperationalStateSaveOutcome.UNCHANGED
        assert (replay.incidents_created, replay.incidents_updated) == (0, 0)
        assert replay.incidents_resolved == 0
        assert len(replay.anomalies) == 2  # still reported, not persisted

    incidents_after = _incident_rows(sqlalchemy_session_factory)
    assert incidents_after == incidents_before
    for row in incidents_after:
        assert row.occurrence_count == 1
        assert row.last_seen_at == row.updated_at == T0
        assert row.status == "OPEN"
    assert _latest(sqlalchemy_session_factory) == fabric
    assert sink.events == []


def test_exact_replay_of_recovery__does_not_resolve_again(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)
    service.ingest(fixture_fabric("degraded-active", T0))
    service.ingest(fixture_fabric("baseline", T1, "run-2"))
    resolved_before = _incident_rows(sqlalchemy_session_factory)

    replay = service.ingest(fixture_fabric("baseline", T1, "run-2"))

    assert replay.state_outcome is OperationalStateSaveOutcome.UNCHANGED
    assert replay.incidents_resolved == 0
    assert _incident_rows(sqlalchemy_session_factory) == resolved_before


def test_new_degraded_collection__still_increments_occurrence_count(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)
    service.ingest(fixture_fabric("degraded-active", T0))

    result = service.ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert result.incidents_updated == 2
    assert [
        (r.occurrence_count, r.last_seen_at) for r in _incident_rows(sqlalchemy_session_factory)
    ] == [(2, T1), (2, T1)]


def test_stale_healthy_collection__is_rejected_and_everything_is_unchanged(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)
    degraded = fixture_fabric("degraded-active", T1, "run-2")
    service.ingest(degraded)
    incidents_before = _incident_rows(sqlalchemy_session_factory)

    with pytest.raises(StaleOperationalStateError):
        service.ingest(fixture_fabric("baseline", T0, "run-1"))

    assert _latest(sqlalchemy_session_factory) == degraded
    assert _incident_rows(sqlalchemy_session_factory) == incidents_before


def test_equal_time_conflicting_collection__is_rejected(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    service = _service(sqlalchemy_session_factory)
    degraded = fixture_fabric("degraded-active", T0, "run-1")
    service.ingest(degraded)
    incidents_before = _incident_rows(sqlalchemy_session_factory)

    with pytest.raises(OperationalStateConflictError):
        service.ingest(fixture_fabric("baseline", T0, "run-2"))

    assert _latest(sqlalchemy_session_factory) == degraded
    assert _incident_rows(sqlalchemy_session_factory) == incidents_before


# --- atomicity: state and incidents commit or roll back together -----------


def test_rollback_A__incident_upsert_failure_rolls_back_the_state_update(
    sqlalchemy_session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(sqlalchemy_session_factory)
    previous = fixture_fabric("baseline", T0, "run-1")
    _service(sqlalchemy_session_factory).ingest(previous)

    from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("upsert failed")

    monkeypatch.setattr(SqlAlchemyIncidentRepository, "upsert_open_incident", boom)

    with pytest.raises(RuntimeError, match="upsert failed"):
        _service(sqlalchemy_session_factory).ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert _latest(sqlalchemy_session_factory) == previous
    assert _incident_rows(sqlalchemy_session_factory) == []


def test_rollback_A2__second_upsert_failure_rolls_back_state_and_first_incident(
    sqlalchemy_session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(sqlalchemy_session_factory)
    previous = fixture_fabric("baseline", T0, "run-1")
    _service(sqlalchemy_session_factory).ingest(previous)

    from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository

    real = SqlAlchemyIncidentRepository.upsert_open_incident
    calls: list[int] = []

    def flaky(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("second upsert failed")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(SqlAlchemyIncidentRepository, "upsert_open_incident", flaky)

    with pytest.raises(RuntimeError, match="second upsert failed"):
        _service(sqlalchemy_session_factory).ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert len(calls) == 2  # the first incident really was written, then undone
    assert _latest(sqlalchemy_session_factory) == previous
    assert _incident_rows(sqlalchemy_session_factory) == []


def test_rollback_B__recovery_failure_rolls_back_the_state_update(
    sqlalchemy_session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(sqlalchemy_session_factory)
    degraded = fixture_fabric("degraded-active", T0, "run-1")
    _service(sqlalchemy_session_factory).ingest(degraded)
    incidents_before = _incident_rows(sqlalchemy_session_factory)

    from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("resolve failed")

    monkeypatch.setattr(SqlAlchemyIncidentRepository, "resolve", boom)

    with pytest.raises(RuntimeError, match="resolve failed"):
        _service(sqlalchemy_session_factory).ingest(fixture_fabric("baseline", T1, "run-2"))

    assert _latest(sqlalchemy_session_factory) == degraded
    assert _incident_rows(sqlalchemy_session_factory) == incidents_before


def test_rollback_C__state_serialization_failure_commits_no_incident(
    sqlalchemy_session_factory: SessionFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(sqlalchemy_session_factory)

    def fail(fabric: FabricOperationalState) -> Any:
        raise SerializationError("cannot serialize operational state")

    monkeypatch.setattr(operational_state_repository, "fabric_to_json", fail)

    with pytest.raises(SerializationError, match="cannot serialize"):
        _service(sqlalchemy_session_factory).ingest(fixture_fabric("degraded-active", T0))

    assert _latest(sqlalchemy_session_factory) is None
    assert _incident_rows(sqlalchemy_session_factory) == []


def test_rollback_D__missing_device_leaves_latest_state_untouched(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    previous = fixture_fabric("baseline", T0, "run-1")
    # Baseline needs no devices, so it persists without registration.
    _service(sqlalchemy_session_factory).ingest(previous)

    with pytest.raises(DeviceNotFoundError):
        _service(sqlalchemy_session_factory).ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert _latest(sqlalchemy_session_factory) == previous


def test_rollback_E__commit_failure_rolls_back_state_and_incidents(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    sink = _Sink()
    previous = fixture_fabric("degraded-active", T0, "run-1")
    _service(sqlalchemy_session_factory, sink).ingest(previous)
    incidents_before = _incident_rows(sqlalchemy_session_factory)
    sink.events.clear()

    with pytest.raises(RuntimeError, match="commit failed"):
        _service(sqlalchemy_session_factory, sink, _FailingCommitUow).ingest(
            fixture_fabric("degraded-active", T1, "run-2")
        )

    assert _latest(sqlalchemy_session_factory) == previous
    assert _incident_rows(sqlalchemy_session_factory) == incidents_before
    assert sink.events == []


def test_failed_ingestion_in_one_unit_of_work__is_invisible_to_the_next(
    sqlalchemy_session_factory: SessionFactory,
) -> None:
    _register(sqlalchemy_session_factory)
    previous = fixture_fabric("baseline", T1, "run-2")
    service = _service(sqlalchemy_session_factory)
    service.ingest(previous)

    with pytest.raises(StaleOperationalStateError):
        service.ingest(fixture_fabric("degraded-active", T0, "run-1"))

    # A later valid collection still lands normally.
    later = fixture_fabric("degraded-active", T2, "run-3")
    assert service.ingest(later).state_outcome is OperationalStateSaveOutcome.REPLACED
    assert _latest(sqlalchemy_session_factory) == later
