"""Unit tests for ``GetNetworkDiagnosisService`` (NPE-1E1B) against the real
``InMemoryUnitOfWork``: a pure read, one UnitOfWork per call, no commit. The
diagnosis comes from the latest fabric alone; OPEN incidents are attached by
the existing RULE-BGP-DOWN fingerprint as correlation metadata."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meta_rne.application.errors import OperationalStateNotFoundError
from meta_rne.application.network_diagnosis_query import GetNetworkDiagnosisService
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.domain.diagnosis import DiagnosisConfidence, DiagnosisType
from meta_rne.domain.incident import IncidentStatus
from meta_rne.lab1_identity import LAB1_LAB_NAME
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from operational_state_support import (
    REGISTERED_DEVICE_IDS,
    frr_device,
    rich_fixture_fabric,
)

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=45)
T2 = T0 + timedelta(seconds=90)

LEAF_KEY = ("lab1-leaf-1", "bgp-neighbor:10.255.0.0")
SPINE_KEY = ("lab1-spine-1", "bgp-neighbor:10.255.0.1")


class _NullSink:
    def emit(self, event: object) -> None:
        pass


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


def _fabric(fixture_set: str, at: datetime = T0, collection_id: str = "run-1") -> Any:
    return rich_fixture_fabric(
        fixture_set, at, collection_id, lab_name=LAB1_LAB_NAME, collector="containerlab-frr"
    )


def _store() -> InMemoryStore:
    store = InMemoryStore()
    for device_id in REGISTERED_DEVICE_IDS:
        store.devices[device_id] = frr_device(device_id, T0)
    return store


def _ingest(store: InMemoryStore, fixture_set: str, at: datetime, run: str = "run-1") -> None:
    OperationalStateIngestionService(
        lambda: InMemoryUnitOfWork(store),
        _NullSink(),  # type: ignore[arg-type]
    ).ingest(_fabric(fixture_set, at, run))


def _resolve(store: InMemoryStore, key: tuple[str, str], at: datetime) -> None:
    uow = InMemoryUnitOfWork(store)
    open_incident = next(
        i
        for i in uow.incidents.list_all()
        if (i.device_id, i.affected_resource) == key and i.status is IncidentStatus.OPEN
    )
    uow.incidents.resolve(open_incident.incident_id, at)
    uow.commit()
    uow.close()


def _service(store: InMemoryStore, uows: list[_Uow]) -> GetNetworkDiagnosisService:
    def factory() -> _Uow:
        uow = _Uow(store)
        uows.append(uow)
        return uow

    return GetNetworkDiagnosisService(factory, lab_name=LAB1_LAB_NAME)


def _linked_keys(result: Any) -> list[tuple[str, str]]:
    return [(r.device_id, r.affected_resource) for r in result.diagnoses[0].linked_incidents]


def test_get__no_state__raises_not_found_and_rolls_back() -> None:
    uows: list[_Uow] = []

    with pytest.raises(OperationalStateNotFoundError) as excinfo:
        _service(InMemoryStore(), uows).get()

    assert excinfo.value.lab_name == LAB1_LAB_NAME
    assert uows[0].calls == ["rollback", "close"]


def test_get__healthy_baseline__has_no_diagnoses() -> None:
    store = _store()
    _ingest(store, "baseline", T0)

    result = _service(store, []).get()

    assert result.diagnoses == ()
    assert result.collection_id == "run-1"
    assert result.collected_at == T0


def test_get__degraded_active__single_high_physical_link_failure() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)

    result = _service(store, []).get()

    assert len(result.diagnoses) == 1
    diagnosis = result.diagnoses[0].diagnosis
    assert diagnosis.diagnosis_type is DiagnosisType.PHYSICAL_LINK_FAILURE
    assert diagnosis.confidence is DiagnosisConfidence.HIGH
    assert diagnosis.resource.resource_id == "leaf-1:eth1--spine-1:eth1"


def test_get__two_symptom_keys_two_open_incidents__two_linked_references() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)

    result = _service(store, []).get()

    assert _linked_keys(result) == [LEAF_KEY, SPINE_KEY]
    for ref in result.diagnoses[0].linked_incidents:
        assert ref.status is IncidentStatus.OPEN
        assert ref.occurrence_count == 1
        assert ref.created_at == ref.last_seen_at == T0
    ids = [r.incident_id for r in result.diagnoses[0].linked_incidents]
    assert len(set(ids)) == 2


def test_get__no_matching_incident__diagnosis_remains_with_no_links() -> None:
    store = InMemoryStore()
    uow = InMemoryUnitOfWork(store)
    uow.operational_state.save_latest(_fabric("degraded-active"))
    uow.commit()
    uow.close()

    result = _service(store, []).get()

    assert len(result.diagnoses) == 1
    assert result.diagnoses[0].linked_incidents == ()
    assert result.diagnoses[0].diagnosis.confidence is DiagnosisConfidence.HIGH
    assert len(result.diagnoses[0].diagnosis.symptom_keys) == 2


def test_get__only_one_matching_incident__one_linked_reference() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)
    baseline = _service(store, []).get().diagnoses[0].diagnosis
    _resolve(store, LEAF_KEY, T0)

    result = _service(store, []).get()

    assert _linked_keys(result) == [SPINE_KEY]
    assert result.diagnoses[0].diagnosis == baseline


def test_get__resolved_incidents_only__are_not_linked_but_diagnosis_remains() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)
    _resolve(store, LEAF_KEY, T0)
    _resolve(store, SPINE_KEY, T0)

    result = _service(store, []).get()

    assert len(result.diagnoses) == 1
    assert result.diagnoses[0].linked_incidents == ()


def test_get__open_incident_newer_than_fabric__is_not_linked() -> None:
    store = _store()
    _ingest(store, "degraded-active", T1, "run-2")
    # A read straddling a newer ingestion: the latest state the read sees is older.
    store.operational_states[LAB1_LAB_NAME] = _fabric("degraded-active", T0, "run-1")

    result = _service(store, []).get()

    assert len(result.diagnoses) == 1
    assert result.diagnoses[0].linked_incidents == ()


def test_get__incident_seen_exactly_at_collected_at__is_linked() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)

    assert len(_service(store, []).get().diagnoses[0].linked_incidents) == 2


def test_get__unrelated_open_incident__is_not_linked() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)
    # spine-2 / leaf-2 incident: a device-scoped BGP incident for another link.
    from meta_rne.domain.operational_state import BgpSessionState
    from operational_state_support import with_neighbor_states

    other = with_neighbor_states(
        _fabric("degraded-active", T1, "run-2"),
        {("leaf-2", "10.255.0.4"): BgpSessionState.ACTIVE},
    )
    OperationalStateIngestionService(
        lambda: InMemoryUnitOfWork(store),
        _NullSink(),  # type: ignore[arg-type]
    ).ingest(other)

    assert len(InMemoryUnitOfWork(store).incidents.list_all()) > 2
    result = _service(store, []).get()

    assert _linked_keys(result) == [LEAF_KEY, SPINE_KEY]


def test_get__is_read_only_and_closes_once() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)
    uows: list[_Uow] = []

    _service(store, uows).get()

    assert len(uows) == 1
    assert uows[0].calls == ["close"]


def test_get__failure__rolls_back_closes_and_preserves_original_error() -> None:
    store = _store()
    _ingest(store, "degraded-active", T0)
    uows: list[_Uow] = []
    boom = RuntimeError("incident lookup exploded")

    def broken(fingerprint: str) -> Any:
        raise boom

    service = _service(store, uows)
    original_factory = service._unit_of_work_factory

    def factory() -> _Uow:
        uow = original_factory()
        uow.incidents.get_open_by_fingerprint = broken  # type: ignore[method-assign,assignment]
        return uow  # type: ignore[return-value]

    service._unit_of_work_factory = factory

    with pytest.raises(RuntimeError) as excinfo:
        service.get()

    assert excinfo.value is boom
    assert uows[-1].calls == ["rollback", "close"]
