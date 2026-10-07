"""``OperationalStateIngestionService`` — NPE-1C3B3's persistence of live
operational-state BGP incidents (ADR-0003).

An already-collected ``FabricOperationalState`` goes in; the existing pure
``OperationalStateDetector`` produces anomalies; each anomaly is mapped by the
existing ``AnomalyIncidentMapper``, fingerprinted by the existing
``compute_fingerprint`` and persisted by the existing atomic
``upsert_open_incident`` — the same pipeline ``TelemetryIngestionService`` uses.
No deduplication logic lives here: the repository upsert is the mechanism.

This service never collects state, spawns a process, calls Docker or
Containerlab, performs HTTP, reads a clock, persists the fabric state or any
``TelemetrySample``, registers a device, or resolves an incident. Timestamps
come from the anomaly (``detected_at`` == ``FabricOperationalState.collected_at``).

Every referenced device must already be registered (``RegisterLabDevicesService``
owns that). Before anything is written, a missing device raises the existing
``DeviceNotFoundError`` and nothing is persisted.

One ``UnitOfWork`` per call, one ``commit()`` on success (no per-anomaly
commits), and ``TelemetryIngestionService``'s exception-preserving
rollback/close lifecycle on failure.

No recovery semantics: a healthy fabric yields zero anomalies, therefore zero
upserts. Existing OPEN incidents stay OPEN; auto-resolution is a later gate.
A single physical link failure yields two device-scoped incidents (one per
end); correlating them is RCA's job, not this service's.

Structured incident events are emitted best-effort after commit, with the same
``IncidentLogEvent`` shape ``TelemetryIngestionService`` emits.
"""

from collections.abc import Callable
from dataclasses import dataclass

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.detection.anomaly_incident_mapper import AnomalyIncidentMapper
from meta_rne.detection.operational_state_detector import OperationalStateDetector
from meta_rne.domain.anomaly import Anomaly
from meta_rne.domain.incident import (
    IncidentUpsertOutcome,
    IncidentUpsertResult,
    compute_fingerprint,
)
from meta_rne.domain.operational_state import FabricOperationalState
from meta_rne.domain.ports import UnitOfWork
from meta_rne.observability import IncidentEventSink, IncidentLogEvent, StdoutIncidentEventSink


@dataclass(frozen=True, slots=True)
class OperationalStateIngestionResult:
    collection_id: str
    anomalies: tuple[Anomaly, ...]
    upserts: tuple[IncidentUpsertResult, ...]
    incidents_created: int
    incidents_updated: int


class OperationalStateIngestionService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        incident_event_sink: IncidentEventSink | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._incident_event_sink = (
            incident_event_sink if incident_event_sink is not None else StdoutIncidentEventSink()
        )

    def ingest(self, fabric: FabricOperationalState) -> OperationalStateIngestionResult:
        uow = self._unit_of_work_factory()
        try:
            anomalies = OperationalStateDetector.detect(fabric)

            # Validate every referenced device before writing anything.
            for device_id in dict.fromkeys(anomaly.device_id for anomaly in anomalies):
                if uow.devices.get_by_id(device_id) is None:
                    raise DeviceNotFoundError(device_id)

            upserts: list[IncidentUpsertResult] = []
            for anomaly in anomalies:
                candidate = AnomalyIncidentMapper.build_candidate(anomaly)
                fingerprint = compute_fingerprint(
                    candidate.device_id,
                    candidate.source,
                    candidate.rule_ref,
                    candidate.affected_resource,
                )
                upserts.append(
                    uow.incidents.upsert_open_incident(
                        candidate, fingerprint, candidate.observed_at
                    )
                )

            uow.commit()
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
            self._emit_incident_events(upserts)
            uow.close()
            return OperationalStateIngestionResult(
                collection_id=fabric.collection_id,
                anomalies=anomalies,
                upserts=tuple(upserts),
                incidents_created=sum(
                    1 for upsert in upserts if upsert.outcome is IncidentUpsertOutcome.CREATED
                ),
                incidents_updated=sum(
                    1 for upsert in upserts if upsert.outcome is IncidentUpsertOutcome.UPDATED
                ),
            )

    def _emit_incident_events(self, results: list[IncidentUpsertResult]) -> None:
        """Best-effort and post-commit only: one failing emission never stops
        later ones, is never retried, and never propagates."""
        for result in results:
            try:
                self._incident_event_sink.emit(IncidentLogEvent.from_upsert_result(result))
            except Exception:
                pass
