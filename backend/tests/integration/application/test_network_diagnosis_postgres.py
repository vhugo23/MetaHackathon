"""PostgreSQL behavior of ``GetNetworkDiagnosisService`` (NPE-1E1B) with the
real ``SqlAlchemyUnitOfWork``: diagnosis authority is the persisted network
state, never the incident lifecycle. Incidents are produced and resolved only
through the supported application services."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from meta_rne.application.lab_device_registration import RegisterLabDevicesService
from meta_rne.application.network_diagnosis_query import (
    GetNetworkDiagnosisService,
    NetworkDiagnosisResult,
)
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.domain.incident import IncidentStatus
from meta_rne.lab1_identity import LAB1_LAB_NAME
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from operational_state_support import rich_fixture_fabric

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=45)
T2 = T0 + timedelta(seconds=90)
T3 = T0 + timedelta(seconds=135)

LEAF_KEY = ("lab1-leaf-1", "bgp-neighbor:10.255.0.0")
SPINE_KEY = ("lab1-spine-1", "bgp-neighbor:10.255.0.1")


class _NullSink:
    def emit(self, event: Any) -> None:
        pass


def _factory(session_factory: Callable[[], Session]) -> Callable[[], SqlAlchemyUnitOfWork]:
    return lambda: SqlAlchemyUnitOfWork(session_factory)  # type: ignore[arg-type,return-value]


def _ingest(
    session_factory: Callable[[], Session], fixture_set: str, at: datetime, run: str
) -> None:
    OperationalStateIngestionService(
        _factory(session_factory),  # type: ignore[arg-type]
        _NullSink(),
    ).ingest(
        rich_fixture_fabric(
            fixture_set, at, run, lab_name=LAB1_LAB_NAME, collector="containerlab-frr"
        )
    )


def _diagnose(session_factory: Callable[[], Session]) -> NetworkDiagnosisResult:
    return GetNetworkDiagnosisService(
        _factory(session_factory),  # type: ignore[arg-type]
        lab_name=LAB1_LAB_NAME,
    ).get()


def _resolve_open(
    session_factory: Callable[[], Session], key: tuple[str, str], at: datetime
) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    incident = next(
        i
        for i in uow.incidents.list_all()
        if (i.device_id, i.affected_resource) == key and i.status is IncidentStatus.OPEN
    )
    uow.incidents.resolve(incident.incident_id, at)
    uow.commit()
    uow.close()


def _linked(result: NetworkDiagnosisResult) -> dict[tuple[str, str], str]:
    return {
        (r.device_id, r.affected_resource): r.incident_id
        for r in result.diagnoses[0].linked_incidents
    }


def test_diagnosis_lifecycle__authority_is_network_state_not_incidents(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    factory = sqlalchemy_session_factory
    RegisterLabDevicesService(_factory(factory)).register(T0)  # type: ignore[arg-type]

    # 1-3. degraded state + the two OPEN incidents it produced.
    _ingest(factory, "degraded-active", T0, "run-1")
    result = _diagnose(factory)
    assert len(result.diagnoses) == 1
    diagnosis = result.diagnoses[0].diagnosis
    assert diagnosis.diagnosis_type.value == "PHYSICAL_LINK_FAILURE"
    assert diagnosis.confidence is not None and diagnosis.confidence.value == "HIGH"
    assert diagnosis.resource.resource_id == "leaf-1:eth1--spine-1:eth1"
    first_episode = _linked(result)
    assert set(first_episode) == {LEAF_KEY, SPINE_KEY}
    assert all(r.status is IncidentStatus.OPEN for r in result.diagnoses[0].linked_incidents)

    # 4. resolving one incident leaves the diagnosis and the other link.
    _resolve_open(factory, LEAF_KEY, T0)
    result = _diagnose(factory)
    assert len(result.diagnoses) == 1
    assert result.diagnoses[0].diagnosis == diagnosis
    assert _linked(result) == {SPINE_KEY: first_episode[SPINE_KEY]}

    # 5. resolving both while the fabric is still degraded keeps the diagnosis.
    _resolve_open(factory, SPINE_KEY, T0)
    result = _diagnose(factory)
    assert len(result.diagnoses) == 1
    assert result.diagnoses[0].diagnosis == diagnosis
    assert result.diagnoses[0].linked_incidents == ()

    # 6. recovered state: no diagnosis.
    _ingest(factory, "baseline", T1, "run-2")
    result = _diagnose(factory)
    assert result.diagnoses == ()
    assert result.collection_id == "run-2"

    # 7. re-failure: the diagnosis returns linked to the NEW OPEN episode.
    _ingest(factory, "degraded-active", T2, "run-3")
    result = _diagnose(factory)
    assert len(result.diagnoses) == 1
    second_episode = _linked(result)
    assert set(second_episode) == {LEAF_KEY, SPINE_KEY}
    assert set(second_episode.values()).isdisjoint(first_episode.values())
    assert all(r.created_at == T2 for r in result.diagnoses[0].linked_incidents)
