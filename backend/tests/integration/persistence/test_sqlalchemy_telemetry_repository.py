"""PostgreSQL-only SqlAlchemyTelemetryRepository behavior (Gate E2B).

Covers only backend-specific behavior that the shared contract suite
(tests/contract/persistence/test_telemetry_repository_contract.py)
deliberately excludes: FK-enforced unknown-device rejection, the absence
of any retention/pruning (domain-model.md Section 12: "the production
(PostgreSQL) implementation may retain longer... a query-time concern, not
a storage-time one"), malformed-persisted-JSON error propagation, and
same-Session read-your-writes. UnitOfWork commit/rollback is out of scope
here (Gate E2C).
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.errors import ReferencedDeviceNotFoundError
from meta_rne.persistence.serialization import SerializationError
from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
from meta_rne.persistence.sqlalchemy.telemetry_repository import SqlAlchemyTelemetryRepository

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


def test_save__nonexistent_device__raises_referenced_device_not_found_error(
    sqlalchemy_session: Session,
) -> None:
    repository = SqlAlchemyTelemetryRepository(sqlalchemy_session)

    with pytest.raises(ReferencedDeviceNotFoundError):
        repository.save(DEVICE_ID, _sample())


def test_save__nonexistent_device__does_not_leak_raw_integrity_error(
    sqlalchemy_session: Session,
) -> None:
    repository = SqlAlchemyTelemetryRepository(sqlalchemy_session)

    try:
        repository.save(DEVICE_ID, _sample())
        raised: type[Exception] | None = None
    except Exception as exc:  # noqa: BLE001 - intentionally inspecting the type
        raised = type(exc)

    assert raised is ReferencedDeviceNotFoundError


def test_save__samples_spanning_more_than_five_minutes__no_pruning(
    sqlalchemy_session: Session,
) -> None:
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    devices.save(_device())
    repository = SqlAlchemyTelemetryRepository(sqlalchemy_session)

    early = _sample(sampled_at=T0)
    late = _sample(sampled_at=T0 + timedelta(minutes=30))
    repository.save(DEVICE_ID, early)
    repository.save(DEVICE_ID, late)

    result = repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=1))

    assert early in result
    assert late in result
    assert len(result) == 2


def test_save__105_samples__no_100_row_pruning(sqlalchemy_session: Session) -> None:
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    devices.save(_device())
    repository = SqlAlchemyTelemetryRepository(sqlalchemy_session)

    samples = [_sample(sampled_at=T0 + timedelta(seconds=i)) for i in range(105)]
    for sample in samples:
        repository.save(DEVICE_ID, sample)

    result = repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=1))

    assert len(result) == 105


def test_get_latest__malformed_interface_states_json__raises_serialization_error(
    sqlalchemy_session: Session,
) -> None:
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    devices.save(_device())

    sqlalchemy_session.execute(
        text(
            "INSERT INTO telemetry_samples "
            "(device_id, sampled_at, cpu_utilization_pct, memory_utilization_pct, "
            " interface_error_rate, interface_states, bgp_sessions) "
            "VALUES (:device_id, :sampled_at, 50.0, 50.0, 0.0, "
            " '[{\"not_a_name\": \"x\"}]'::jsonb, '[]'::jsonb)"
        ),
        {"device_id": DEVICE_ID, "sampled_at": T0},
    )
    sqlalchemy_session.flush()

    repository = SqlAlchemyTelemetryRepository(sqlalchemy_session)
    with pytest.raises(SerializationError):
        repository.get_latest(DEVICE_ID)


def test_get_recent__malformed_bgp_sessions_json__raises_serialization_error(
    sqlalchemy_session: Session,
) -> None:
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    devices.save(_device())

    sqlalchemy_session.execute(
        text(
            "INSERT INTO telemetry_samples "
            "(device_id, sampled_at, cpu_utilization_pct, memory_utilization_pct, "
            " interface_error_rate, interface_states, bgp_sessions) "
            "VALUES (:device_id, :sampled_at, 50.0, 50.0, 0.0, "
            ' \'[]\'::jsonb, \'[{"neighbor_ip": "10.0.0.1", "state": "NotAState"}]\'::jsonb)'
        ),
        {"device_id": DEVICE_ID, "sampled_at": T0},
    )
    sqlalchemy_session.flush()

    repository = SqlAlchemyTelemetryRepository(sqlalchemy_session)
    with pytest.raises(SerializationError):
        repository.get_recent(DEVICE_ID, since=T0)


def test_save_then_get_latest_and_get_recent_in_same_session__sees_new_row(
    sqlalchemy_session: Session,
) -> None:
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    devices.save(_device())
    repository = SqlAlchemyTelemetryRepository(sqlalchemy_session)
    sample = _sample()

    repository.save(DEVICE_ID, sample)

    assert repository.get_latest(DEVICE_ID) == sample
    assert repository.get_recent(DEVICE_ID, since=T0) == [sample]
