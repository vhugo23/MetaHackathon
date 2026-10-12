"""``GetNetworkDiagnosisService`` — NPE-1E1B's read-only on-demand RCA query.

One ``UnitOfWork`` per call, no ``commit()``. The latest persisted
``FabricOperationalState`` is the only diagnosis evidence: the pure
``DeterministicDiagnosisEngine`` runs over it, and nothing is stored. Each
diagnosis symptom key is then mapped to the existing RULE-BGP-DOWN incident
fingerprint and looked up with ``get_open_by_fingerprint``; a matching OPEN
incident is attached as correlation metadata. A diagnosis is valid with zero
linked incidents, and incident lifecycle never changes a diagnosis.

An OPEN incident whose ``last_seen_at`` is later than the fabric's
``collected_at`` belongs to a newer ingestion than the diagnosed snapshot and
is not linked. An absent state raises ``OperationalStateNotFoundError``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from meta_rne.application.errors import OperationalStateNotFoundError
from meta_rne.diagnosis import DeterministicDiagnosisEngine
from meta_rne.domain.anomaly import RuleId
from meta_rne.domain.diagnosis import Diagnosis
from meta_rne.domain.incident import (
    Incident,
    IncidentSource,
    IncidentStatus,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity
from meta_rne.domain.ports import UnitOfWork


@dataclass(frozen=True, slots=True)
class IncidentReference:
    incident_id: str
    device_id: str
    affected_resource: str
    severity: Severity
    status: IncidentStatus
    created_at: datetime
    last_seen_at: datetime
    occurrence_count: int

    @classmethod
    def from_incident(cls, incident: Incident) -> "IncidentReference":
        return cls(
            incident_id=incident.incident_id,
            device_id=incident.device_id,
            affected_resource=incident.affected_resource,
            severity=incident.severity,
            status=incident.status,
            created_at=incident.created_at,
            last_seen_at=incident.last_seen_at,
            occurrence_count=incident.occurrence_count,
        )


@dataclass(frozen=True, slots=True)
class CorrelatedDiagnosis:
    diagnosis: Diagnosis
    linked_incidents: tuple[IncidentReference, ...]


@dataclass(frozen=True, slots=True)
class NetworkDiagnosisResult:
    collection_id: str
    collected_at: datetime
    diagnoses: tuple[CorrelatedDiagnosis, ...]


class GetNetworkDiagnosisService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        *,
        lab_name: str,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._lab_name = lab_name

    def get(self) -> NetworkDiagnosisResult:
        uow = self._unit_of_work_factory()
        try:
            fabric = uow.operational_state.get_latest(self._lab_name)
            if fabric is None:
                raise OperationalStateNotFoundError(self._lab_name)
            correlated: list[CorrelatedDiagnosis] = []
            for diagnosis in DeterministicDiagnosisEngine.diagnose(fabric):
                linked: dict[str, IncidentReference] = {}
                for key in diagnosis.symptom_keys:
                    fingerprint = compute_fingerprint(
                        key.device_id,
                        IncidentSource.ANOMALY,
                        RuleId.BGP_DOWN.value,
                        key.affected_resource,
                    )
                    incident = uow.incidents.get_open_by_fingerprint(fingerprint)
                    if incident is None or incident.last_seen_at > fabric.collected_at:
                        continue
                    linked.setdefault(
                        incident.incident_id, IncidentReference.from_incident(incident)
                    )
                correlated.append(
                    CorrelatedDiagnosis(
                        diagnosis=diagnosis, linked_incidents=tuple(linked.values())
                    )
                )
            result = NetworkDiagnosisResult(
                collection_id=fabric.collection_id,
                collected_at=fabric.collected_at,
                diagnoses=tuple(correlated),
            )
        except Exception as original_error:
            try:
                uow.rollback()
            except Exception as rollback_error:
                original_error.add_note(f"UnitOfWork rollback also failed: {rollback_error!r}")
            try:
                uow.close()
            except Exception as close_error:
                original_error.add_note(f"UnitOfWork close also failed: {close_error!r}")
            raise
        else:
            uow.close()
            return result


__all__ = [
    "CorrelatedDiagnosis",
    "GetNetworkDiagnosisService",
    "IncidentReference",
    "NetworkDiagnosisResult",
]
