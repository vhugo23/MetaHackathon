"""In-memory IncidentRepository (Day 4B3) — a fast conformance-test double,
never used in production (ADR-0002).

``upsert_open_incident`` is the only write path (domain-model.md Section 11):
candidate/fingerprint/observed_at consistency is validated before any lock is
taken or any mutation happens; the whole find-OPEN-by-fingerprint -> decide
-> mutate sequence (including the Device-existence check on the create
branch) then runs inside ``store.incidents_lock``, the single critical
section the in-memory conformance contract requires.
"""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from meta_rne.domain.incident import (
    Incident,
    IncidentCandidate,
    IncidentStatus,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
)
from meta_rne.persistence.errors import ReferencedDeviceNotFoundError
from meta_rne.persistence.incident_id import default_incident_id_factory
from meta_rne.persistence.incident_validation import (
    require_non_empty_incident_id,
    validate_candidate_consistency,
)
from meta_rne.persistence.memory.store import InMemoryStore


def _find_open_by_fingerprint(store: InMemoryStore, fingerprint: str) -> Incident | None:
    for incident in store.incidents.values():
        if incident.status == IncidentStatus.OPEN and incident.fingerprint == fingerprint:
            return incident
    return None


class InMemoryIncidentRepository:
    def __init__(
        self,
        store: InMemoryStore,
        incident_id_factory: Callable[[], str] = default_incident_id_factory,
    ) -> None:
        self._store = store
        self._incident_id_factory = incident_id_factory

    def get_by_id(self, incident_id: str) -> Incident | None:
        return self._store.incidents.get(incident_id)

    def list_all(self) -> tuple[Incident, ...]:
        return tuple(
            sorted(self._store.incidents.values(), key=lambda i: (i.created_at, i.incident_id))
        )

    def upsert_open_incident(
        self, candidate: IncidentCandidate, fingerprint: str, observed_at: datetime
    ) -> IncidentUpsertResult:
        validate_candidate_consistency(candidate, fingerprint, observed_at)

        with self._store.incidents_lock:
            existing = _find_open_by_fingerprint(self._store, fingerprint)

            if existing is None:
                if candidate.device_id not in self._store.devices:
                    raise ReferencedDeviceNotFoundError(candidate.device_id)

                incident_id = self._incident_id_factory()
                require_non_empty_incident_id(incident_id)

                incident = Incident(
                    incident_id=incident_id,
                    fingerprint=fingerprint,
                    device_id=candidate.device_id,
                    source=candidate.source,
                    rule_ref=candidate.rule_ref,
                    affected_resource=candidate.affected_resource,
                    severity=candidate.severity,
                    status=IncidentStatus.OPEN,
                    evidence=candidate.evidence,
                    recommendation=candidate.recommendation,
                    created_at=observed_at,
                    last_seen_at=observed_at,
                    occurrence_count=1,
                    updated_at=observed_at,
                    resolved_at=None,
                )
                self._store.incidents[incident_id] = incident
                return IncidentUpsertResult(
                    incident=incident, outcome=IncidentUpsertOutcome.CREATED
                )

            if observed_at < existing.last_seen_at:
                raise ValueError(
                    "stale observation: observed_at precedes the existing OPEN incident's "
                    "last_seen_at"
                )

            updated = replace(
                existing,
                severity=candidate.severity,
                evidence=candidate.evidence,
                recommendation=candidate.recommendation,
                last_seen_at=observed_at,
                occurrence_count=existing.occurrence_count + 1,
                updated_at=observed_at,
            )
            self._store.incidents[existing.incident_id] = updated
            return IncidentUpsertResult(incident=updated, outcome=IncidentUpsertOutcome.UPDATED)

    def resolve(self, incident_id: str, resolved_at: datetime) -> Incident | None:
        """Atomic (under the existing incidents_lock) OPEN -> RESOLVED
        transition, delegating the actual transition/invariant enforcement
        to Incident.resolve() (Day 7A) — no full-row save(), so a concurrent
        upsert_open_incident on this same row can never be clobbered."""
        with self._store.incidents_lock:
            existing = self._store.incidents.get(incident_id)
            if existing is None:
                return None
            resolved = existing.resolve(resolved_at)
            self._store.incidents[incident_id] = resolved
            return resolved
