"""Unit tests for ``GetRecentTelemetryService`` (Gate G2B) against a real
``InMemoryUnitOfWork`` — never a mocked repository when the in-memory double
can prove the behavior, matching ``test_device_drift.py``'s hand-written
fake/spy convention for lifecycle-call-count assertions.

Per the approved Gate G2A/G2B plan: ``since`` is validated *before* any
``UnitOfWork`` is created (an invalid ``since`` creates zero UnitOfWorks,
performs no Device lookup, no telemetry query, no rollback, no close);
Device existence is checked only after validation passes, inside the one
``UnitOfWork`` the call creates; the service never commits; and backend
divergence is never asserted here — every case below uses data inside the
common repository envelope (<= 100 samples, within 5 minutes of the
watermark) so it is equally valid against either backend.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.telemetry_query import GetRecentTelemetryService
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

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


def _sample(
    device_id: str = DEVICE_ID,
    sampled_at: datetime = T0,
) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )


def _seed_device(store: InMemoryStore, device_id: str = DEVICE_ID) -> None:
    uow = InMemoryUnitOfWork(store)
    uow.devices.save(_device(device_id))
    uow.commit()


def _seed_sample(store: InMemoryStore, sample: TelemetrySample) -> None:
    uow = InMemoryUnitOfWork(store)
    uow.telemetry_samples.save(sample.device_id, sample)
    uow.commit()


def _make_service(store: InMemoryStore) -> GetRecentTelemetryService:
    return GetRecentTelemetryService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))


class _CountingFactory:
    """Counts calls to the wrapped ``unit_of_work_factory`` — used to prove
    an invalid ``since`` creates zero UnitOfWorks."""

    def __init__(self) -> None:
        self.call_count = 0
        self._store = InMemoryStore()

    def __call__(self) -> InMemoryUnitOfWork:
        self.call_count += 1
        return InMemoryUnitOfWork(self._store)


@dataclass
class _Calls:
    device_get_by_id: list[str] = field(default_factory=list)
    telemetry_get_recent: list[tuple[str, datetime]] = field(default_factory=list)
    commit: int = 0
    rollback: int = 0
    close: int = 0


class _FakeDeviceRepository:
    def __init__(self, calls: _Calls, device: Device | None) -> None:
        self._calls = calls
        self._device = device

    def get_by_id(self, device_id: str) -> Device | None:
        self._calls.device_get_by_id.append(device_id)
        return self._device


class _FakeTelemetryRepository:
    def __init__(self, calls: _Calls, samples: tuple[TelemetrySample, ...]) -> None:
        self._calls = calls
        self._samples = samples

    def get_recent(self, device_id: str, since: datetime) -> list[TelemetrySample]:
        self._calls.telemetry_get_recent.append((device_id, since))
        return list(self._samples)

    def save(self, device_id: str, sample: TelemetrySample) -> None:
        raise AssertionError("save must never be called by a read-only service")

    def get_latest(self, device_id: str) -> TelemetrySample | None:
        raise AssertionError("get_latest must never be called by this service")


class _FakeUnitOfWork:
    def __init__(
        self,
        calls: _Calls,
        device: Device | None,
        samples: tuple[TelemetrySample, ...] = (),
    ) -> None:
        self._calls = calls
        self.devices = _FakeDeviceRepository(calls, device)
        self.telemetry_samples = _FakeTelemetryRepository(calls, samples)

    def commit(self) -> None:
        self._calls.commit += 1

    def rollback(self) -> None:
        self._calls.rollback += 1

    def close(self) -> None:
        self._calls.close += 1


# --- 1-3. since validation happens before any UnitOfWork is created --------


def test_naive_since__creates_no_unit_of_work() -> None:
    counting_factory = _CountingFactory()
    service = GetRecentTelemetryService(unit_of_work_factory=counting_factory)

    with pytest.raises(ValueError, match="since must be timezone-aware UTC"):
        service.get(DEVICE_ID, datetime(2026, 7, 18, 10, 0, 0))

    assert counting_factory.call_count == 0


def test_non_utc_offset_since__creates_no_unit_of_work() -> None:
    counting_factory = _CountingFactory()
    service = GetRecentTelemetryService(unit_of_work_factory=counting_factory)
    non_utc = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(ValueError, match="since must be timezone-aware UTC"):
        service.get(DEVICE_ID, non_utc)

    assert counting_factory.call_count == 0


def test_naive_since__exact_error_message() -> None:
    counting_factory = _CountingFactory()
    service = GetRecentTelemetryService(unit_of_work_factory=counting_factory)

    with pytest.raises(ValueError) as exc_info:
        service.get(DEVICE_ID, datetime(2026, 7, 18, 10, 0, 0))

    assert str(exc_info.value) == "since must be timezone-aware UTC"


def test_non_utc_offset_since__exact_error_message() -> None:
    counting_factory = _CountingFactory()
    service = GetRecentTelemetryService(unit_of_work_factory=counting_factory)
    non_utc = datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    with pytest.raises(ValueError) as exc_info:
        service.get(DEVICE_ID, non_utc)

    assert str(exc_info.value) == "since must be timezone-aware UTC"


# --- 4. Missing-device lifecycle, only after valid since --------------------


def test_missing_device_with_valid_since__creates_exactly_one_unit_of_work() -> None:
    calls = _Calls()
    uow = _FakeUnitOfWork(calls, device=None)
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    with pytest.raises(DeviceNotFoundError):
        service.get(DEVICE_ID, T0)

    assert calls.device_get_by_id == [DEVICE_ID]


def test_missing_device_with_valid_since__never_queries_telemetry() -> None:
    calls = _Calls()
    uow = _FakeUnitOfWork(calls, device=None)
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    with pytest.raises(DeviceNotFoundError):
        service.get(DEVICE_ID, T0)

    assert calls.telemetry_get_recent == []


def test_missing_device__raises_device_not_found_error() -> None:
    store = InMemoryStore()
    service = _make_service(store)

    with pytest.raises(DeviceNotFoundError) as exc_info:
        service.get(DEVICE_ID, T0)

    assert exc_info.value.device_id == DEVICE_ID


def test_missing_device__does_not_commit() -> None:
    calls = _Calls()
    uow = _FakeUnitOfWork(calls, device=None)
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    with pytest.raises(DeviceNotFoundError):
        service.get(DEVICE_ID, T0)

    assert calls.commit == 0


def test_missing_device__rollback_and_close_attempted() -> None:
    calls = _Calls()
    uow = _FakeUnitOfWork(calls, device=None)
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    with pytest.raises(DeviceNotFoundError):
        service.get(DEVICE_ID, T0)

    assert calls.rollback == 1
    assert calls.close == 1


# --- 5-6. Faithful passthrough: no sort/limit/filter/dedup ------------------


def test_empty_history__returns_empty_tuple() -> None:
    store = InMemoryStore()
    _seed_device(store)
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    assert result == ()


def test_inclusive_since_boundary__sample_at_since_is_included() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample(sampled_at=T0)
    _seed_sample(store, sample)
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    assert sample in result


def test_ascending_ordering__preserved_unchanged_from_repository() -> None:
    store = InMemoryStore()
    _seed_device(store)
    samples = [_sample(sampled_at=T0 + timedelta(seconds=i)) for i in (30, 0, 10, 20)]
    for sample in samples:
        _seed_sample(store, sample)
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    direct = InMemoryUnitOfWork(store).telemetry_samples.get_recent(DEVICE_ID, since=T0)
    assert list(result) == direct


def test_equal_timestamp_ordering__preserved_unchanged_from_repository() -> None:
    store = InMemoryStore()
    _seed_device(store)
    for _ in range(3):
        _seed_sample(store, _sample(sampled_at=T0))
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    direct = InMemoryUnitOfWork(store).telemetry_samples.get_recent(DEVICE_ID, since=T0)
    assert list(result) == direct
    assert len(result) == 3


def test_exact_duplicates__all_preserved_unchanged() -> None:
    store = InMemoryStore()
    _seed_device(store)
    duplicate = _sample(sampled_at=T0)
    _seed_sample(store, duplicate)
    _seed_sample(store, duplicate)
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    assert len(result) == 2
    assert all(sample == duplicate for sample in result)


def test_future_rows__included_unchanged() -> None:
    store = InMemoryStore()
    _seed_device(store)
    future_sample = _sample(sampled_at=T0 + timedelta(hours=1))
    _seed_sample(store, future_sample)
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    assert future_sample in result


def test_result_is_a_tuple() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample())
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    assert isinstance(result, tuple)


def test_no_sorting_limiting_filtering_or_deduplication_occurs() -> None:
    """The service performs exactly one transformation: converting the
    repository's list return value to a tuple — nothing else."""
    store = InMemoryStore()
    _seed_device(store)
    samples = [_sample(sampled_at=T0 + timedelta(seconds=i)) for i in range(5)]
    duplicate = samples[2]
    _seed_sample(store, duplicate)  # an intentional extra exact duplicate
    for sample in samples:
        _seed_sample(store, sample)
    service = _make_service(store)

    result = service.get(DEVICE_ID, T0)

    direct = InMemoryUnitOfWork(store).telemetry_samples.get_recent(DEVICE_ID, since=T0)
    assert list(result) == direct
    assert len(result) == len(direct)


# --- 7-8. Commit/rollback/close behavior ------------------------------------


def test_success__commit_is_never_called() -> None:
    calls = _Calls()
    uow = _FakeUnitOfWork(calls, device=_device(), samples=(_sample(),))
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    service.get(DEVICE_ID, T0)

    assert calls.commit == 0


def test_success__close_called_exactly_once() -> None:
    calls = _Calls()
    uow = _FakeUnitOfWork(calls, device=_device(), samples=(_sample(),))
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    service.get(DEVICE_ID, T0)

    assert calls.close == 1
    assert calls.rollback == 0


def test_repository_failure__rollback_and_close_preserve_original_exception() -> None:
    class _FailingTelemetryRepository:
        def get_recent(self, device_id: str, since: datetime) -> list[TelemetrySample]:
            raise RuntimeError("telemetry read boom")

    class _FailingUnitOfWork:
        def __init__(self, calls: _Calls) -> None:
            self._calls = calls
            self.devices = _FakeDeviceRepository(calls, _device())
            self.telemetry_samples = _FailingTelemetryRepository()

        def commit(self) -> None:
            self._calls.commit += 1

        def rollback(self) -> None:
            self._calls.rollback += 1

        def close(self) -> None:
            self._calls.close += 1

    calls = _Calls()
    uow = _FailingUnitOfWork(calls)
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    with pytest.raises(RuntimeError, match="telemetry read boom"):
        service.get(DEVICE_ID, T0)

    assert calls.rollback == 1
    assert calls.close == 1
    assert calls.commit == 0


def test_rollback_also_fails__original_exception_preserved_with_note() -> None:
    class _FailingTelemetryRepository:
        def get_recent(self, device_id: str, since: datetime) -> list[TelemetrySample]:
            raise ValueError("processing boom")

    class _FailingRollbackUnitOfWork:
        def __init__(self, calls: _Calls) -> None:
            self._calls = calls
            self.devices = _FakeDeviceRepository(calls, _device())
            self.telemetry_samples = _FailingTelemetryRepository()

        def commit(self) -> None:
            self._calls.commit += 1

        def rollback(self) -> None:
            self._calls.rollback += 1
            raise RuntimeError("rollback boom")

        def close(self) -> None:
            self._calls.close += 1

    calls = _Calls()
    uow = _FailingRollbackUnitOfWork(calls)
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    with pytest.raises(ValueError, match="processing boom") as exc_info:
        service.get(DEVICE_ID, T0)

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("rollback also failed" in note for note in notes)
    assert calls.close == 1


def test_close_also_fails__original_exception_preserved_with_note() -> None:
    class _FailingTelemetryRepository:
        def get_recent(self, device_id: str, since: datetime) -> list[TelemetrySample]:
            raise ValueError("processing boom")

    class _FailingCloseUnitOfWork:
        def __init__(self, calls: _Calls) -> None:
            self._calls = calls
            self.devices = _FakeDeviceRepository(calls, _device())
            self.telemetry_samples = _FailingTelemetryRepository()

        def commit(self) -> None:
            self._calls.commit += 1

        def rollback(self) -> None:
            self._calls.rollback += 1

        def close(self) -> None:
            self._calls.close += 1
            raise RuntimeError("close boom")

    calls = _Calls()
    uow = _FailingCloseUnitOfWork(calls)
    service = GetRecentTelemetryService(unit_of_work_factory=lambda: uow)

    with pytest.raises(ValueError, match="processing boom") as exc_info:
        service.get(DEVICE_ID, T0)

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("close also failed" in note for note in notes)
