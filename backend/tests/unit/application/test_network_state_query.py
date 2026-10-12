"""Unit tests for ``GetNetworkStateService`` (NPE-1D1B) against the real
``InMemoryUnitOfWork``: a pure read, one UnitOfWork per call, no commit, an
absent state is an error, and the injected clock is read exactly once."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meta_rne.application.errors import OperationalStateNotFoundError
from meta_rne.application.network_state_query import GetNetworkStateService
from meta_rne.lab1_identity import LAB1_LAB_NAME
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from operational_state_support import fixture_fabric

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
RETRIEVED = T0 + timedelta(seconds=5)


class _Uow(InMemoryUnitOfWork):
    def __init__(self, store: InMemoryStore) -> None:
        super().__init__(store)
        self.calls: list[str] = []

    def commit(self) -> None:
        self.calls.append("commit")
        super().commit()

    def rollback(self) -> None:
        self.calls.append("rollback")
        super().rollback()

    def close(self) -> None:
        self.calls.append("close")
        super().close()


class _Clock:
    def __init__(self) -> None:
        self.reads = 0

    def __call__(self) -> datetime:
        self.reads += 1
        return RETRIEVED


def _seed(store: InMemoryStore, collection_id: str = "run-1") -> Any:
    fabric = fixture_fabric("baseline", T0, collection_id, lab_name=LAB1_LAB_NAME)
    uow = InMemoryUnitOfWork(store)
    uow.operational_state.save_latest(fabric)
    uow.commit()
    uow.close()
    return fabric


def _service(store: InMemoryStore, clock: _Clock, uows: list[_Uow]) -> GetNetworkStateService:
    def factory() -> _Uow:
        uow = _Uow(store)
        uows.append(uow)
        return uow

    return GetNetworkStateService(factory, lab_name=LAB1_LAB_NAME, clock=clock)


def test_get__returns_latest_fabric_source_and_injected_retrieved_at() -> None:
    store, clock, uows = InMemoryStore(), _Clock(), []
    fabric = _seed(store)

    result = _service(store, clock, uows).get()

    assert result.fabric == fabric
    assert result.source.lab_name == LAB1_LAB_NAME
    assert result.source.collector == "test"
    assert result.retrieved_at == RETRIEVED


def test_get__reads_clock_once_closes_once_and_never_commits() -> None:
    store, clock, uows = InMemoryStore(), _Clock(), []
    _seed(store)

    _service(store, clock, uows).get()

    assert clock.reads == 1
    assert len(uows) == 1
    assert uows[0].calls == ["close"]


def test_get__no_state__raises_not_found_with_lab_name_and_rolls_back() -> None:
    store, clock, uows = InMemoryStore(), _Clock(), []

    with pytest.raises(OperationalStateNotFoundError) as excinfo:
        _service(store, clock, uows).get()

    assert excinfo.value.lab_name == LAB1_LAB_NAME
    assert clock.reads == 0
    assert uows[0].calls == ["rollback", "close"]


def test_get__other_labs_state_is_not_returned() -> None:
    store, clock, uows = InMemoryStore(), _Clock(), []
    other = fixture_fabric("baseline", T0, "run-x", lab_name="another-lab")
    uow = InMemoryUnitOfWork(store)
    uow.operational_state.save_latest(other)
    uow.commit()

    with pytest.raises(OperationalStateNotFoundError):
        _service(store, clock, uows).get()


def test_get__repository_failure__preserves_original_exception_and_cleans_up() -> None:
    store, clock, uows = InMemoryStore(), _Clock(), []
    boom = RuntimeError("repository exploded")

    def broken(lab_name: str) -> Any:
        raise boom

    service = _service(store, clock, uows)
    original_factory = service._unit_of_work_factory

    def factory() -> _Uow:
        uow = original_factory()  # type: ignore[assignment]
        uow.operational_state.get_latest = broken  # type: ignore[method-assign]
        return uow  # type: ignore[return-value]

    service._unit_of_work_factory = factory  # type: ignore[assignment]

    with pytest.raises(RuntimeError) as excinfo:
        service.get()

    assert excinfo.value is boom
    assert uows[0].calls == ["rollback", "close"]


def test_get__rollback_and_close_failures_are_noted_not_replacing_original() -> None:
    store, clock = InMemoryStore(), _Clock()

    class _BadUow(_Uow):
        def rollback(self) -> None:
            raise RuntimeError("rollback failed")

        def close(self) -> None:
            raise RuntimeError("close failed")

    service = GetNetworkStateService(lambda: _BadUow(store), lab_name=LAB1_LAB_NAME, clock=clock)

    with pytest.raises(OperationalStateNotFoundError) as excinfo:
        service.get()

    notes = "\n".join(excinfo.value.__notes__)
    assert "rollback also failed" in notes
    assert "close also failed" in notes
