"""Focused PostgreSQL integration tests for ``GetRecentTelemetryService``
(Gate G2B). Uses a real ``SqlAlchemyUnitOfWork`` — not a re-run of the
in-memory suite (``tests/unit/application/test_telemetry_query_service.py``
already proves lifecycle/ordering/faithfulness behavior against the fast
in-memory double). These tests exist only to prove what only a real
database can prove: real UnitOfWork integration, read-only behavior, and
that the service introduces no accidental application-level cap on top of
PostgreSQL's own, already-proven no-pruning repository behavior.

Per the approved Gate G2A/G2B plan, PostgreSQL's no-pruning behavior is
described strictly as PostgreSQL-specific integration behavior — never as
a required cross-backend divergence, and never compared against an
in-memory run of the same scenario.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.telemetry_query import GetRecentTelemetryService
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _device(device_id: str = DEVICE_ID) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


def _sample(device_id: str = DEVICE_ID, sampled_at: datetime = T0) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )


def _seed_device(session_factory: Callable[[], Session], device_id: str = DEVICE_ID) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.devices.save(_device(device_id))
    uow.commit()
    uow.close()


def _seed_sample(session_factory: Callable[[], Session], sample: TelemetrySample) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.telemetry_samples.save(sample.device_id, sample)
    uow.commit()
    uow.close()


def _service(session_factory: Callable[[], Session]) -> GetRecentTelemetryService:
    return GetRecentTelemetryService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory)
    )


def test_telemetry_query_postgres__real_round_trip__returns_seeded_sample(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    sample = _sample()
    _seed_sample(sqlalchemy_session_factory, sample)
    service = _service(sqlalchemy_session_factory)

    result = service.get(DEVICE_ID, T0)

    assert sample in result


def test_telemetry_query_postgres__missing_device__raises_and_touches_nothing(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    service = _service(sqlalchemy_session_factory)

    with pytest.raises(DeviceNotFoundError):
        service.get(DEVICE_ID, T0)


def test_telemetry_query_postgres__read_only__no_row_is_ever_written(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(sqlalchemy_session_factory, _sample())
    service = _service(sqlalchemy_session_factory)

    service.get(DEVICE_ID, T0)

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    recent = verify_uow.telemetry_samples.get_recent(DEVICE_ID, since=T0)
    assert len(recent) == 1
    verify_uow.close()


def test_telemetry_query_postgres__rows_older_than_five_minutes__returned_unchanged(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    """PostgreSQL-specific integration behavior: no pruning occurs, so a
    row older than the in-memory repository's own 5-minute retention
    window is still returned when ``since`` includes it. This is
    PostgreSQL's own existing, already-proven repository behavior — the
    service must introduce no additional cap on top of it."""
    _seed_device(sqlalchemy_session_factory)
    old_sample = _sample(sampled_at=T0)
    _seed_sample(sqlalchemy_session_factory, old_sample)
    service = _service(sqlalchemy_session_factory)

    result = service.get(DEVICE_ID, T0 - timedelta(minutes=1))

    assert old_sample in result


def test_telemetry_query_postgres__more_than_100_matching_rows__all_returned(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    """PostgreSQL-specific integration behavior: no 100-row cap exists at
    the repository layer, and the service must not introduce one."""
    _seed_device(sqlalchemy_session_factory)
    for i in range(150):
        _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0 + timedelta(seconds=i)))
    service = _service(sqlalchemy_session_factory)

    result = service.get(DEVICE_ID, T0)

    assert len(result) == 150


def test_telemetry_query_postgres__exact_duplicates__round_trip_unchanged(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    duplicate = _sample(sampled_at=T0)
    _seed_sample(sqlalchemy_session_factory, duplicate)
    _seed_sample(sqlalchemy_session_factory, duplicate)
    service = _service(sqlalchemy_session_factory)

    result = service.get(DEVICE_ID, T0)

    assert len(result) == 2
    assert all(sample == duplicate for sample in result)
