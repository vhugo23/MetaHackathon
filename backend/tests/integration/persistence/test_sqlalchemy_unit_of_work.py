"""SqlAlchemyUnitOfWork tests (Day 4B3).

Constructed from a ``session_factory: Callable[[], Session]`` (never an
already-created Session) — creates exactly one Session and gives that same
Session to all four repositories. ``commit()`` calls the real
``Session.commit()``, rolling back and re-raising on any exception (never
swallowed or replaced); ``rollback()``/``close()`` delegate directly to the
Session. See ``sqlalchemy_session_factory`` (tests/conftest.py) for how a
test can call a real ``commit()`` while staying isolated via SAVEPOINT
joining.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _device(device_id: str = "spine-01") -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


def test_sqlalchemy_unit_of_work__all_four_repositories_are_available(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)

    assert uow.devices is not None
    assert uow.configuration_snapshots is not None
    assert uow.configuration_policies is not None
    assert uow.incidents is not None


def test_sqlalchemy_unit_of_work__repositories_share_one_session(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)

    sessions = {
        uow.devices._session,  # type: ignore[attr-defined]
        uow.configuration_snapshots._session,  # type: ignore[attr-defined]
        uow.configuration_policies._session,  # type: ignore[attr-defined]
        uow.incidents._session,  # type: ignore[attr-defined]
    }

    assert len(sessions) == 1


def test_sqlalchemy_unit_of_work__commit__publishes_staged_multi_repository_transaction(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    uow.devices.save(_device())

    uow.commit()

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.devices.get_by_id("spine-01") == _device()


def test_sqlalchemy_unit_of_work__rollback__discards_staged_data(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    uow.devices.save(_device())

    uow.rollback()

    assert uow.devices.get_by_id("spine-01") is None


def test_sqlalchemy_unit_of_work__close_without_commit__publishes_nothing(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    uow.devices.save(_device())

    uow.close()

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.devices.get_by_id("spine-01") is None


def test_sqlalchemy_unit_of_work__new_unit_of_work__sees_committed_data(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    first = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    first.devices.save(_device())
    first.commit()

    second = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)

    assert second.devices.get_by_id("spine-01") == _device()


def test_sqlalchemy_unit_of_work__commit_failure__invokes_rollback_and_reraises_original_exception(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    class _BoomOnCommit(Exception):
        pass

    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    uow.devices.save(_device())

    rollback_calls: list[bool] = []
    original_rollback = uow._session.rollback

    def _failing_commit() -> None:
        raise _BoomOnCommit("simulated commit failure")

    def _tracking_rollback() -> None:
        rollback_calls.append(True)
        original_rollback()

    uow._session.commit = _failing_commit  # type: ignore[method-assign]
    uow._session.rollback = _tracking_rollback  # type: ignore[method-assign]

    with pytest.raises(_BoomOnCommit):
        uow.commit()

    assert rollback_calls == [True]


def test_sqlalchemy_unit_of_work__commit_failure__leaves_no_partial_persisted_state(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    class _BoomOnCommit(Exception):
        pass

    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    uow.devices.save(_device())
    uow._session.commit = lambda: (_ for _ in ()).throw(_BoomOnCommit())  # type: ignore[method-assign]

    with pytest.raises(_BoomOnCommit):
        uow.commit()

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.devices.get_by_id("spine-01") is None


def test_sqlalchemy_unit_of_work__close__delegates_to_session_close(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    close_calls: list[bool] = []
    original_close = uow._session.close

    def _tracking_close() -> None:
        close_calls.append(True)
        original_close()

    uow._session.close = _tracking_close  # type: ignore[method-assign]

    uow.close()

    assert close_calls == [True]
