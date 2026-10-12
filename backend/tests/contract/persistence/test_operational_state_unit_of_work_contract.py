"""Shared UnitOfWork contract for ``operational_state`` (NPE-1D1A), run against
the in-memory and SQLAlchemy UnitOfWork: the latest operational state shares
the same commit / rollback / close transaction boundary as every other
repository. (Cross-UnitOfWork isolation of uncommitted data is asserted in
the in-memory unit tests: the shared SQLAlchemy fixture joins every session
to one connection.)"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from meta_rne.domain.operational_state import OperationalStateSaveOutcome
from meta_rne.domain.ports import UnitOfWork
from meta_rne.persistence.errors import StaleOperationalStateError
from operational_state_support import rich_fixture_fabric

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=30)


def test_repository_is_available(unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
    assert unit_of_work_factory().operational_state is not None


def test_uncommitted_save__is_visible_in_its_own_unit_of_work(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    fabric = rich_fixture_fabric("baseline", T0)

    uow.operational_state.save_latest(fabric)

    assert uow.operational_state.get_latest("lab1") == fabric


def test_commit__publishes_state_to_a_new_unit_of_work(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    fabric = rich_fixture_fabric("baseline", T0)
    uow.operational_state.save_latest(fabric)

    uow.commit()
    uow.close()

    assert unit_of_work_factory().operational_state.get_latest("lab1") == fabric


def test_rollback_of_initial_create__leaves_no_state(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    uow = unit_of_work_factory()
    uow.operational_state.save_latest(rich_fixture_fabric("baseline", T0))

    uow.rollback()

    assert uow.operational_state.get_latest("lab1") is None
    assert unit_of_work_factory().operational_state.get_latest("lab1") is None


def test_rollback_of_replacement__restores_the_previous_state_exactly(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    first = unit_of_work_factory()
    previous = rich_fixture_fabric("baseline", T0, "run-1")
    first.operational_state.save_latest(previous)
    first.commit()
    first.close()

    second = unit_of_work_factory()
    outcome = second.operational_state.save_latest(
        rich_fixture_fabric("degraded-active", T1, "run-2")
    )
    assert outcome is OperationalStateSaveOutcome.REPLACED
    second.rollback()

    assert second.operational_state.get_latest("lab1") == previous
    assert unit_of_work_factory().operational_state.get_latest("lab1") == previous


def test_rejected_stale_save__leaves_state_untouched_after_rollback(
    unit_of_work_factory: Callable[[], UnitOfWork],
) -> None:
    first = unit_of_work_factory()
    current = rich_fixture_fabric("baseline", T1, "run-2")
    first.operational_state.save_latest(current)
    first.commit()
    first.close()

    second = unit_of_work_factory()
    with pytest.raises(StaleOperationalStateError):
        second.operational_state.save_latest(rich_fixture_fabric("baseline", T0, "run-1"))
    second.rollback()

    assert unit_of_work_factory().operational_state.get_latest("lab1") == current
