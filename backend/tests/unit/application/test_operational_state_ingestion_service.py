"""Unit tests for ``OperationalStateIngestionService`` (NPE-1C3B3) against a
real ``InMemoryUnitOfWork`` — never a mocked repository."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.operational_state_ingestion import (
    OperationalStateIngestionResult,
    OperationalStateIngestionService,
)
from meta_rne.domain.anomaly import BgpDownEvidence, RuleId
from meta_rne.domain.incident import (
    Incident,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertResult,
)
from meta_rne.domain.telemetry import BgpState
from meta_rne.observability import IncidentLogEvent
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from operational_state_support import (
    REGISTERED_DEVICE_IDS,
    fixture_fabric,
    frr_device,
    unavailable_fabric,
)

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=30)

EXPECTED_PAIRS = {
    ("lab1-leaf-1", "bgp-neighbor:10.255.0.0"),
    ("lab1-spine-1", "bgp-neighbor:10.255.0.1"),
}


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[IncidentLogEvent] = []

    def emit(self, event: IncidentLogEvent) -> None:
        self.events.append(event)


class _CountingUnitOfWork(InMemoryUnitOfWork):
    def __init__(self, store: InMemoryStore) -> None:
        super().__init__(store)
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self) -> None:
        self.commits += 1
        super().commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        super().rollback()

    def close(self) -> None:
        self.closes += 1
        super().close()


@dataclass
class _Harness:
    store: InMemoryStore
    sink: _RecordingSink
    units: list[_CountingUnitOfWork]
    service: OperationalStateIngestionService


def _harness(device_ids: tuple[str, ...] = REGISTERED_DEVICE_IDS) -> _Harness:
    store = InMemoryStore()
    for device_id in device_ids:
        store.devices[device_id] = frr_device(device_id, T0)
    sink = _RecordingSink()
    units: list[_CountingUnitOfWork] = []

    def factory() -> _CountingUnitOfWork:
        unit = _CountingUnitOfWork(store)
        units.append(unit)
        return unit

    return _Harness(store, sink, units, OperationalStateIngestionService(factory, sink))


def _incidents(store: InMemoryStore) -> list[Incident]:
    return sorted(store.incidents.values(), key=lambda i: i.device_id)


def test_baseline__no_anomalies_no_incidents_one_commit() -> None:
    h = _harness()

    result = h.service.ingest(fixture_fabric("baseline", T0))

    assert isinstance(result, OperationalStateIngestionResult)
    assert result.collection_id == "run-1"
    assert result.anomalies == ()
    assert result.upserts == ()
    assert (result.incidents_created, result.incidents_updated) == (0, 0)
    assert h.store.incidents == {}
    assert len(h.units) == 1
    assert (h.units[0].commits, h.units[0].rollbacks, h.units[0].closes) == (1, 0, 1)
    assert h.sink.events == []


def test_degraded_active__two_device_scoped_incidents() -> None:
    h = _harness()

    result = h.service.ingest(fixture_fabric("degraded-active", T0))

    assert len(result.anomalies) == 2
    assert (result.incidents_created, result.incidents_updated) == (2, 0)
    assert h.units[0].commits == 1
    incidents = _incidents(h.store)
    assert {(i.device_id, i.affected_resource) for i in incidents} == EXPECTED_PAIRS
    for incident in incidents:
        assert incident.source is IncidentSource.ANOMALY
        assert incident.rule_ref == RuleId.BGP_DOWN.value
        assert incident.status is IncidentStatus.OPEN
        assert incident.occurrence_count == 1
        assert incident.created_at == incident.last_seen_at == T0
        assert isinstance(incident.evidence, BgpDownEvidence)
        assert incident.evidence.state is BgpState.ACTIVE
        assert incident.evidence.previous_state is None
    assert len(h.sink.events) == 2


def test_repeated_degraded__updates_same_two_open_incidents() -> None:
    h = _harness()
    first = h.service.ingest(fixture_fabric("degraded-active", T0))
    ids_and_fingerprints = {(i.incident_id, i.fingerprint) for i in _incidents(h.store)}

    second = h.service.ingest(fixture_fabric("degraded-active", T1, collection_id="run-2"))

    assert (first.incidents_created, first.incidents_updated) == (2, 0)
    assert (second.incidents_created, second.incidents_updated) == (0, 2)
    incidents = _incidents(h.store)
    assert len(incidents) == 2
    assert {(i.incident_id, i.fingerprint) for i in incidents} == ids_and_fingerprints
    for incident in incidents:
        assert incident.status is IncidentStatus.OPEN
        assert incident.occurrence_count == 2
        assert incident.created_at == T0
        assert incident.last_seen_at == T1


def test_missing_device__raises_and_persists_nothing() -> None:
    h = _harness(device_ids=("lab1-leaf-1", "lab1-spine-2", "lab1-leaf-2"))

    with pytest.raises(DeviceNotFoundError) as excinfo:
        h.service.ingest(fixture_fabric("degraded-active", T0))

    assert excinfo.value.device_id == "lab1-spine-1"
    assert h.store.incidents == {}
    unit = h.units[0]
    assert (unit.commits, unit.rollbacks, unit.closes) == (0, 1, 1)
    assert h.sink.events == []


def test_failure_on_second_anomaly__rolls_back_first() -> None:
    h = _harness()
    calls: list[Any] = []
    units: list[_CountingUnitOfWork] = []

    def factory() -> _CountingUnitOfWork:
        unit = _CountingUnitOfWork(h.store)
        real = unit.incidents.upsert_open_incident

        def upsert(*args: Any, **kwargs: Any) -> IncidentUpsertResult:
            calls.append(args[1])
            if len(calls) == 2:
                raise RuntimeError("boom on second anomaly")
            return real(*args, **kwargs)

        unit.incidents.upsert_open_incident = upsert  # type: ignore[method-assign]
        units.append(unit)
        return unit

    service = OperationalStateIngestionService(factory, h.sink)

    with pytest.raises(RuntimeError, match="boom on second anomaly"):
        service.ingest(fixture_fabric("degraded-active", T0))

    assert len(calls) == 2
    assert h.store.incidents == {}
    assert (units[0].commits, units[0].rollbacks, units[0].closes) == (0, 1, 1)
    assert h.sink.events == []


def test_healthy_after_degraded__no_upserts_and_incidents_stay_open() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))
    before = _incidents(h.store)

    result = h.service.ingest(fixture_fabric("baseline", T1, collection_id="run-2"))

    assert result.anomalies == ()
    assert (result.incidents_created, result.incidents_updated) == (0, 0)
    assert _incidents(h.store) == before
    assert all(i.status is IncidentStatus.OPEN and i.resolved_at is None for i in before)


def test_unavailable_bgp_facet__creates_no_incident() -> None:
    h = _harness()

    result = h.service.ingest(unavailable_fabric(T0))

    assert result.anomalies == ()
    assert h.store.incidents == {}


def test_stale_collection__rolls_back_and_preserves_error() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T1))
    before = _incidents(h.store)

    with pytest.raises(ValueError, match="stale observation"):
        h.service.ingest(fixture_fabric("degraded-active", T0, collection_id="run-old"))

    assert _incidents(h.store) == before
