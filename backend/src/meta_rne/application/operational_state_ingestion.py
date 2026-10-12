"""``OperationalStateIngestionService`` — NPE-1C3B3's persistence of live
operational-state BGP incidents (ADR-0003).

An already-collected ``FabricOperationalState`` goes in; the existing pure
``OperationalStateDetector`` produces anomalies; each anomaly is mapped by the
existing ``AnomalyIncidentMapper``, fingerprinted by the existing
``compute_fingerprint`` and persisted by the existing atomic
``upsert_open_incident`` — the same pipeline ``TelemetryIngestionService`` uses.
No deduplication logic lives here: the repository upsert is the mechanism.

This service never collects state, spawns a process, calls Docker or
Containerlab, performs HTTP, reads a clock, persists any ``TelemetrySample``,
registers a device, or resolves an incident outside the recovery rule above.
Timestamps come from the anomaly (``detected_at`` ==
``FabricOperationalState.collected_at``).

Latest-state persistence (NPE-1D1A). The fabric itself is saved as its lab's
latest operational state in the same transaction as the incident lifecycle
writes, so the operator-visible state and the incident changes of one
collection are atomic. The order is: derive the fabric's single lab identity
(an empty or mixed fabric is rejected), detect, validate every referenced
device, ``save_latest``, then upsert/resolve incidents, then one commit.

A collection is identified by ``(lab_name, collection_id)``. The state row and
the incident mutations commit together, so an EXACT replay of an
already-stored collection (``OperationalStateSaveOutcome.UNCHANGED``) was
already fully accepted: it is a whole-ingestion no-op. It does not touch
incidents (no ``occurrence_count`` increment, no ``last_seen_at`` advance, no
resolution, no events); the detector's anomalies are still returned. This is
different from a NEW collection that observes the same network condition
again, which still updates OPEN incidents. A stale or conflicting collection
(``StaleOperationalStateError`` / ``OperationalStateConflictError``) rolls
everything back — including when it would otherwise have caused no incident
mutation.

Every referenced device must already be registered (``RegisterLabDevicesService``
owns that). Before anything is written, a missing device raises the existing
``DeviceNotFoundError`` and nothing is persisted.

One ``UnitOfWork`` per call, one ``commit()`` on success (no per-anomaly
commits), and ``TelemetryIngestionService``'s exception-preserving
rollback/close lifecycle on failure.

Recovery (NPE-1C3C) is positive-evidence only and lives here, not in the
detector, because it needs persisted incident state and the transaction. A
neighbor observed ``Established`` on a registered router resolves the OPEN
RULE-BGP-DOWN incident with the same fingerprint (same device, same neighbor)
via the existing atomic ``resolve``. Idle/Active/Connect/OpenSent/OpenConfirm/
UNKNOWN, an unavailable/missing BGP facet and a missing neighbor never
resolve: absence of failure evidence is not recovery. An Established
observation older than the incident's ``last_seen_at`` is rejected as a stale
observation (rolled back), consistent with the upsert's stale rule. The two
endpoint incidents of one link recover independently. A single physical link
failure yields two device-scoped incidents (one per end);
correlating them is RCA's job, not this service's.

Structured incident events are emitted best-effort after commit, with the same
``IncidentLogEvent`` shape ``TelemetryIngestionService`` emits.
"""

from collections.abc import Callable
from dataclasses import dataclass

from meta_rne.application.errors import DeviceNotFoundError, IncidentNotFoundError
from meta_rne.detection.anomaly_incident_mapper import AnomalyIncidentMapper
from meta_rne.detection.operational_state_detector import (
    LAB1_NODE_DEVICE_IDS,
    OperationalStateDetector,
)
from meta_rne.domain.anomaly import Anomaly, RuleId
from meta_rne.domain.incident import (
    Incident,
    IncidentSource,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
    compute_fingerprint,
)
from meta_rne.domain.operational_state import (
    FabricOperationalState,
    NodeRole,
    OperationalStateSaveOutcome,
    derive_fabric_source,
)
from meta_rne.domain.ports import UnitOfWork
from meta_rne.observability import IncidentEventSink, IncidentLogEvent, StdoutIncidentEventSink


@dataclass(frozen=True, slots=True)
class OperationalStateIngestionResult:
    collection_id: str
    anomalies: tuple[Anomaly, ...]
    upserts: tuple[IncidentUpsertResult, ...]
    state_outcome: OperationalStateSaveOutcome
    incidents_created: int
    incidents_updated: int
    resolved_incidents: tuple[Incident, ...] = ()
    incidents_resolved: int = 0


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
        upserts: list[IncidentUpsertResult] = []
        resolved: list[Incident] = []
        try:
            # Rejects an empty or mixed fabric before anything else happens.
            derive_fabric_source(fabric)
            anomalies = OperationalStateDetector.detect(fabric)

            # Validate every referenced device before writing anything.
            for device_id in dict.fromkeys(anomaly.device_id for anomaly in anomalies):
                if uow.devices.get_by_id(device_id) is None:
                    raise DeviceNotFoundError(device_id)

            state_outcome = uow.operational_state.save_latest(fabric)

            # An exact replay of an already-accepted collection mutates no
            # incident: its effects were committed together with its state.
            if state_outcome is not OperationalStateSaveOutcome.UNCHANGED:
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
                resolved = self._recover(uow, fabric, upserts)

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
            self._emit_incident_events(upserts, resolved)
            uow.close()
            return OperationalStateIngestionResult(
                collection_id=fabric.collection_id,
                anomalies=anomalies,
                upserts=tuple(upserts),
                state_outcome=state_outcome,
                incidents_created=sum(
                    1 for upsert in upserts if upsert.outcome is IncidentUpsertOutcome.CREATED
                ),
                incidents_updated=sum(
                    1 for upsert in upserts if upsert.outcome is IncidentUpsertOutcome.UPDATED
                ),
                resolved_incidents=tuple(resolved),
                incidents_resolved=len(resolved),
            )

    @staticmethod
    def _recover(
        uow: UnitOfWork,
        fabric: FabricOperationalState,
        upserts: list[IncidentUpsertResult],
    ) -> list[Incident]:
        """Resolve the matching OPEN RULE-BGP-DOWN incident for every neighbor
        observed Established. Never reads anything but the fabric and the
        OPEN incident found by its exact fingerprint."""
        # A neighbor failing in this same snapshot can never also recover.
        failing = {upsert.incident.fingerprint for upsert in upserts}
        resolved: list[Incident] = []
        for node in fabric.nodes:
            if node.role is not NodeRole.ROUTER or node.bgp is None:
                continue
            device_id = LAB1_NODE_DEVICE_IDS[node.node_id]
            for neighbor in node.bgp.neighbors:
                if not neighbor.is_established:
                    continue
                fingerprint = compute_fingerprint(
                    device_id,
                    IncidentSource.ANOMALY,
                    RuleId.BGP_DOWN.value,
                    AnomalyIncidentMapper.bgp_down_affected_resource(neighbor.neighbor_ip),
                )
                if fingerprint in failing:
                    continue
                open_incident = uow.incidents.get_open_by_fingerprint(fingerprint)
                if open_incident is None:
                    continue
                if fabric.collected_at < open_incident.last_seen_at:
                    raise ValueError(
                        "stale observation: collected_at precedes the existing OPEN "
                        "incident's last_seen_at"
                    )
                result = uow.incidents.resolve(open_incident.incident_id, fabric.collected_at)
                if result is None:
                    raise IncidentNotFoundError(open_incident.incident_id)
                resolved.append(result)
        return resolved

    def _emit_incident_events(
        self, results: list[IncidentUpsertResult], resolved: list[Incident]
    ) -> None:
        """Best-effort and post-commit only: one failing emission never stops
        later ones, is never retried, and never propagates."""
        events = [IncidentLogEvent.from_upsert_result(result) for result in results]
        events.extend(IncidentLogEvent.from_resolved(incident) for incident in resolved)
        for event in events:
            try:
                self._incident_event_sink.emit(event)
            except Exception:
                pass
