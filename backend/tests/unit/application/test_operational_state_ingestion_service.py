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
from meta_rne.domain.operational_state import BgpSessionState
from meta_rne.domain.telemetry import BgpState
from meta_rne.observability import IncidentLogEvent
from meta_rne.persistence.errors import StaleOperationalStateError
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from operational_state_support import (
    REGISTERED_DEVICE_IDS,
    fixture_fabric,
    frr_device,
    unavailable_fabric,
    with_neighbor_states,
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


def test_unavailable_bgp_facet__creates_no_incident() -> None:
    h = _harness()

    result = h.service.ingest(unavailable_fabric(T0))

    assert result.anomalies == ()
    assert h.store.incidents == {}


def test_stale_collection__rolls_back_and_preserves_error() -> None:
    # NPE-1D1A: the older collection is now rejected by the latest-state
    # monotonicity rule before any incident logic runs.
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T1))
    before = _incidents(h.store)

    with pytest.raises(StaleOperationalStateError):
        h.service.ingest(fixture_fabric("degraded-active", T0, collection_id="run-old"))

    assert _incidents(h.store) == before


# --- NPE-1C3C: positive-evidence recovery ---------------------------------

LEAF = ("leaf-1", "10.255.0.0")  # lab1-leaf-1's session toward spine-1
SPINE = ("spine-1", "10.255.0.1")  # lab1-spine-1's session toward leaf-1
T2 = T0 + timedelta(seconds=90)


def _degraded(h: _Harness) -> None:
    h.service.ingest(fixture_fabric("degraded-active", T0))
    h.sink.events.clear()


def _status_by_device(store: InMemoryStore) -> dict[str, IncidentStatus]:
    return {i.device_id: i.status for i in store.incidents.values()}


def test_degraded_then_healthy__resolves_both_incidents_in_one_commit() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))
    h.service.ingest(fixture_fabric("degraded-active", T1, collection_id="run-2"))
    h.sink.events.clear()

    result = h.service.ingest(fixture_fabric("baseline", T2, collection_id="run-3"))

    assert result.anomalies == ()
    assert result.upserts == ()
    assert (result.incidents_created, result.incidents_updated) == (0, 0)
    assert result.incidents_resolved == 2
    assert len(result.resolved_incidents) == 2
    assert len(h.units) == 3
    assert (h.units[2].commits, h.units[2].rollbacks, h.units[2].closes) == (1, 0, 1)
    incidents = _incidents(h.store)
    assert {(i.device_id, i.affected_resource) for i in incidents} == EXPECTED_PAIRS
    for incident in incidents:
        assert incident.status is IncidentStatus.RESOLVED
        assert incident.resolved_at == T2
        assert incident.updated_at == T2
        assert incident.last_seen_at == T1
        assert incident.occurrence_count == 2
    assert {i.incident_id for i in result.resolved_incidents} == {i.incident_id for i in incidents}
    assert len(h.sink.events) == 2
    for event in h.sink.events:
        assert event.status is IncidentStatus.RESOLVED
        assert event.outcome.value == "RESOLVED"
        assert event.timestamp == T2


def test_only_leaf_side_established__resolves_only_leaf_incident() -> None:
    h = _harness()
    _degraded(h)
    fabric = with_neighbor_states(
        fixture_fabric("baseline", T1, collection_id="run-2"),
        {SPINE: BgpSessionState.ACTIVE},
    )

    result = h.service.ingest(fabric)

    assert len(result.anomalies) == 1
    assert result.anomalies[0].device_id == "lab1-spine-1"
    assert (result.incidents_created, result.incidents_updated) == (0, 1)
    assert result.incidents_resolved == 1
    assert result.resolved_incidents[0].device_id == "lab1-leaf-1"
    assert _status_by_device(h.store) == {
        "lab1-leaf-1": IncidentStatus.RESOLVED,
        "lab1-spine-1": IncidentStatus.OPEN,
    }
    spine = next(i for i in h.store.incidents.values() if i.device_id == "lab1-spine-1")
    assert spine.occurrence_count == 2


@pytest.mark.parametrize("state", [BgpSessionState.IDLE, BgpSessionState.ACTIVE])
def test_failure_state__is_never_recovery(state: BgpSessionState) -> None:
    h = _harness()
    _degraded(h)

    result = h.service.ingest(
        with_neighbor_states(
            fixture_fabric("baseline", T1, collection_id="run-2"), {LEAF: state, SPINE: state}
        )
    )

    assert result.incidents_resolved == 0
    assert result.incidents_updated == 2
    assert set(_status_by_device(h.store).values()) == {IncidentStatus.OPEN}


@pytest.mark.parametrize(
    "state",
    [
        BgpSessionState.UNKNOWN,
        BgpSessionState.CONNECT,
        BgpSessionState.OPEN_SENT,
        BgpSessionState.OPEN_CONFIRM,
    ],
)
def test_unknown_and_transitional_states__never_resolve(state: BgpSessionState) -> None:
    h = _harness()
    _degraded(h)
    before = _incidents(h.store)

    result = h.service.ingest(
        with_neighbor_states(
            fixture_fabric("baseline", T1, collection_id="run-2"), {LEAF: state, SPINE: state}
        )
    )

    assert result.anomalies == ()
    assert result.incidents_resolved == 0
    assert _incidents(h.store) == before
    assert h.sink.events == []


def test_unavailable_bgp_facet__does_not_resolve() -> None:
    h = _harness()
    _degraded(h)
    before = _incidents(h.store)

    result = h.service.ingest(unavailable_fabric(T1))

    assert result.incidents_resolved == 0
    assert _incidents(h.store) == before


def test_missing_neighbor__does_not_resolve() -> None:
    h = _harness()
    _degraded(h)
    before = _incidents(h.store)

    result = h.service.ingest(
        with_neighbor_states(
            fixture_fabric("baseline", T1, collection_id="run-2"), {LEAF: None, SPINE: None}
        )
    )

    assert result.incidents_resolved == 0
    assert _incidents(h.store) == before


def test_missing_node__does_not_resolve() -> None:
    h = _harness()
    _degraded(h)
    before = _incidents(h.store)

    result = h.service.ingest(
        fixture_fabric("baseline", T1, collection_id="run-2", routers=("spine-2", "leaf-2"))
    )

    assert result.incidents_resolved == 0
    assert _incidents(h.store) == before


def test_stale_established_observation__rolls_back_and_stays_open() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T1))
    before = _incidents(h.store)

    with pytest.raises(StaleOperationalStateError):
        h.service.ingest(fixture_fabric("baseline", T0, collection_id="run-old"))

    assert _incidents(h.store) == before
    assert all(i.status is IncidentStatus.OPEN for i in before)
    unit = h.units[-1]
    assert (unit.commits, unit.rollbacks, unit.closes) == (0, 1, 1)


def test_established_without_open_incident__resolves_nothing_and_creates_nothing() -> None:
    h = _harness()

    result = h.service.ingest(fixture_fabric("baseline", T0))

    assert result.incidents_resolved == 0
    assert h.store.incidents == {}
    assert h.sink.events == []


def test_repeated_healthy__is_idempotent() -> None:
    h = _harness()
    _degraded(h)
    h.service.ingest(fixture_fabric("baseline", T1, collection_id="run-2"))
    h.sink.events.clear()
    resolved = _incidents(h.store)

    result = h.service.ingest(fixture_fabric("baseline", T2, collection_id="run-3"))

    assert result.incidents_resolved == 0
    assert _incidents(h.store) == resolved
    assert h.sink.events == []


def test_refailure_after_recovery__creates_new_open_incidents_and_keeps_history() -> None:
    h = _harness()
    _degraded(h)
    original = {i.incident_id: i for i in h.store.incidents.values()}
    h.service.ingest(fixture_fabric("baseline", T1, collection_id="run-2"))
    resolved = {i.incident_id: i for i in h.store.incidents.values()}

    result = h.service.ingest(fixture_fabric("degraded-active", T2, collection_id="run-3"))

    assert (result.incidents_created, result.incidents_updated) == (2, 0)
    assert result.incidents_resolved == 0
    assert len(h.store.incidents) == 4
    # Old rows are untouched RESOLVED history.
    for incident_id, old in resolved.items():
        assert h.store.incidents[incident_id] == old
        assert old.status is IncidentStatus.RESOLVED
    new = [i for i in h.store.incidents.values() if i.incident_id not in original]
    assert len(new) == 2
    for incident in new:
        assert incident.status is IncidentStatus.OPEN
        assert incident.occurrence_count == 1
        assert incident.created_at == T2
        # Same logical identity: same fingerprint as the resolved predecessor.
        assert incident.fingerprint in {i.fingerprint for i in original.values()}


def test_failure_during_recovery__preserves_error_and_lifecycle_and_emits_nothing() -> None:
    h = _harness()
    _degraded(h)
    calls: list[str] = []
    units: list[_CountingUnitOfWork] = []

    def factory() -> _CountingUnitOfWork:
        unit = _CountingUnitOfWork(h.store)
        real = unit.incidents.resolve

        def resolve(incident_id: str, resolved_at: datetime) -> Incident | None:
            calls.append(incident_id)
            if len(calls) == 2:
                raise RuntimeError("boom on second resolve")
            return real(incident_id, resolved_at)

        unit.incidents.resolve = resolve  # type: ignore[method-assign]
        units.append(unit)
        return unit

    service = OperationalStateIngestionService(factory, h.sink)

    with pytest.raises(RuntimeError, match="boom on second resolve"):
        service.ingest(fixture_fabric("baseline", T1, collection_id="run-2"))

    # The in-memory store has no real rollback, so only the exception and
    # lifecycle contract is asserted here; row-level atomicity is proven on
    # PostgreSQL.
    assert len(calls) == 2
    assert (units[0].commits, units[0].rollbacks, units[0].closes) == (0, 1, 1)
    assert h.sink.events == []


def test_persisted_evidence_state__matches_observed_idle_and_active() -> None:
    h = _harness()

    # degraded-idle capture: leaf-1 observed Idle, spine-1 observed Active.
    result = h.service.ingest(fixture_fabric("degraded-idle", T0, routers=("leaf-1", "spine-1")))

    observed = {
        (a.device_id, a.evidence.state)  # type: ignore[union-attr]
        for a in result.anomalies
    }
    assert observed == {("lab1-leaf-1", BgpState.IDLE), ("lab1-spine-1", BgpState.ACTIVE)}
    persisted = {
        i.device_id: i.evidence.state  # type: ignore[union-attr]
        for i in h.store.incidents.values()
    }
    assert persisted == {"lab1-leaf-1": BgpState.IDLE, "lab1-spine-1": BgpState.ACTIVE}
