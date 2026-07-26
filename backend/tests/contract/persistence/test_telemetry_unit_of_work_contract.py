"""Shared UnitOfWork telemetry contract tests (Gate E2C).

Run against both the in-memory and SQLAlchemy `UnitOfWork` implementations
via the shared `unit_of_work_factory` fixture (conftest.py in this
directory) — proves `telemetry_samples` participates in the same
transaction boundary as every other repository, for both backends
identically. Backend-specific transactional mechanics (lock-blocking,
SAVEPOINT joining, sequence/identity behavior) live in
tests/unit/persistence/test_in_memory_unit_of_work.py and
tests/integration/persistence/test_sqlalchemy_unit_of_work.py.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.ports import UnitOfWork
from meta_rne.domain.telemetry import TelemetrySample

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


def _sample(device_id: str = DEVICE_ID) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=T0,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )


def test_unit_of_work__telemetry_samples_repository_is_available(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()

    assert uow.telemetry_samples is not None


def test_unit_of_work__save_then_get_latest_before_commit__is_visible(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    sample = _sample()

    uow.telemetry_samples.save(DEVICE_ID, sample)

    assert uow.telemetry_samples.get_latest(DEVICE_ID) == sample


def test_unit_of_work__save_then_get_recent_before_commit__is_visible(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    sample = _sample()

    uow.telemetry_samples.save(DEVICE_ID, sample)

    assert uow.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [sample]


def test_unit_of_work__commit__publishes_telemetry_sample(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    sample = _sample()
    uow.telemetry_samples.save(DEVICE_ID, sample)

    uow.commit()

    other = unit_of_work_factory()
    assert other.telemetry_samples.get_latest(DEVICE_ID) == sample


def test_unit_of_work__later_unit_of_work__sees_committed_telemetry(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    first = unit_of_work_factory()
    first.devices.save(_device())
    sample = _sample()
    first.telemetry_samples.save(DEVICE_ID, sample)
    first.commit()

    second = unit_of_work_factory()

    assert second.telemetry_samples.get_recent(DEVICE_ID, since=T0) == [sample]


def test_unit_of_work__rollback__discards_uncommitted_telemetry(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    uow.telemetry_samples.save(DEVICE_ID, _sample())

    uow.rollback()

    assert uow.telemetry_samples.get_latest(DEVICE_ID) is None


def test_unit_of_work__later_unit_of_work__does_not_see_rolled_back_telemetry(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    first = unit_of_work_factory()
    first.devices.save(_device())
    first.telemetry_samples.save(DEVICE_ID, _sample())
    first.rollback()

    second = unit_of_work_factory()

    assert second.telemetry_samples.get_recent(DEVICE_ID, since=T0) == []


def test_unit_of_work__device_and_telemetry_saved_together__commit_together(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    sample = _sample()
    uow.telemetry_samples.save(DEVICE_ID, sample)

    uow.commit()

    verify = unit_of_work_factory()
    assert verify.devices.get_by_id(DEVICE_ID) == _device()
    assert verify.telemetry_samples.get_latest(DEVICE_ID) == sample


def test_unit_of_work__device_and_telemetry_saved_together__roll_back_together(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    uow.telemetry_samples.save(DEVICE_ID, _sample())

    uow.rollback()

    verify = unit_of_work_factory()
    assert verify.devices.get_by_id(DEVICE_ID) is None
    assert verify.telemetry_samples.get_latest(DEVICE_ID) is None


def test_unit_of_work__telemetry_repository_shares_transaction_boundary_with_devices(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    # Proven via commit-then-verify (matching test_unit_of_work_contract.py's
    # established pattern), not via a second UnitOfWork's view before any
    # commit/rollback — the SQLAlchemy fixture intentionally joins every
    # Session in one test to the same underlying connection/transaction
    # (SAVEPOINT-based test isolation), so an uncommitted write is visible
    # to a second SqlAlchemyUnitOfWork sharing that connection; this is a
    # property of the test fixture, not a production guarantee, and is
    # therefore not asserted here.
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    sample = _sample()
    uow.telemetry_samples.save(DEVICE_ID, sample)

    uow.commit()

    verify_after_commit = unit_of_work_factory()
    assert verify_after_commit.devices.get_by_id(DEVICE_ID) == _device()
    assert verify_after_commit.telemetry_samples.get_latest(DEVICE_ID) == sample


def test_unit_of_work__close_without_commit__publishes_nothing(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.devices.save(_device())
    uow.telemetry_samples.save(DEVICE_ID, _sample())

    uow.close()

    verify = unit_of_work_factory()
    assert verify.telemetry_samples.get_latest(DEVICE_ID) is None


def test_unit_of_work__repeated_instances__do_not_retain_uncommitted_state_after_close(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    # "Repeated instances don't retain uncommitted state" is proven via the
    # existing close()-without-commit pattern above, not via a second
    # UnitOfWork's view before any commit/rollback/close on the first (see
    # the transaction-boundary test's note for why that pattern doesn't
    # hold for the shared SQLAlchemy test fixture).
    first = unit_of_work_factory()
    first.devices.save(_device())
    first.telemetry_samples.save(DEVICE_ID, _sample())
    first.close()

    second = unit_of_work_factory()

    assert second.telemetry_samples.get_latest(DEVICE_ID) is None
