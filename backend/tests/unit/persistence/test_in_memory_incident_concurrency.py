"""In-memory concurrency proof for IncidentRepository.upsert_open_incident
(Day 4B3, test-strategy.md Section 9's "parallel, lock-based version").

Four worker threads share one InMemoryStore (and therefore one
``incidents_lock``) and race to upsert the identical fingerprint at the same
instant, synchronized with a Barrier. The lock must serialize the whole
find-OPEN-by-fingerprint -> decide -> mutate sequence in each call, so this
proves the same "never two OPEN rows for one fingerprint" guarantee
domain-model.md Section 11 requires of PostgreSQL's partial unique index,
here enforced by a Python lock instead.
"""

import threading
from datetime import UTC, datetime

from meta_rne.domain.config import (
    AclDirection,
    VendorType,
)
from meta_rne.domain.device import Device
from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentSource,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.persistence.memory.device_repository import InMemoryDeviceRepository
from meta_rne.persistence.memory.incident_repository import InMemoryIncidentRepository
from meta_rne.persistence.memory.store import InMemoryStore

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
WORKER_COUNT = 4


def _candidate() -> IncidentCandidate:
    return IncidentCandidate(
        device_id=DEVICE_ID,
        source=IncidentSource.POLICY_VIOLATION,
        rule_ref="policy-acl-external-in",
        affected_resource="interface:GigabitEthernet0/1:acl_in",
        severity=Severity.MEDIUM,
        evidence=PolicyViolationIncidentEvidence(
            source_snapshot_id="snap-1",
            violation_type=ViolationType.MISSING_REQUIRED_ACL,
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name=None,
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
        ),
        recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        observed_at=T0,
    )


def test_incident_repository_memory__concurrent_upsert_same_fingerprint__yields_one_open() -> None:
    store = InMemoryStore()
    InMemoryDeviceRepository(store).save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )

    candidate = _candidate()
    fingerprint = compute_fingerprint(
        candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
    )
    barrier = threading.Barrier(WORKER_COUNT)
    results: list[IncidentUpsertResult] = []
    results_lock = threading.Lock()

    def worker(worker_index: int) -> None:
        repo = InMemoryIncidentRepository(
            store, incident_id_factory=lambda: f"worker-{worker_index}-unused-id"
        )
        barrier.wait()
        result = repo.upsert_open_incident(candidate, fingerprint, T0)
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKER_COUNT)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == WORKER_COUNT
    created = [r for r in results if r.outcome == IncidentUpsertOutcome.CREATED]
    updated = [r for r in results if r.outcome == IncidentUpsertOutcome.UPDATED]
    assert len(created) == 1
    assert len(updated) == WORKER_COUNT - 1

    incident_ids = {r.incident.incident_id for r in results}
    assert len(incident_ids) == 1

    all_incidents = InMemoryIncidentRepository(store).list_all()
    assert len(all_incidents) == 1
    assert all_incidents[0].occurrence_count == WORKER_COUNT
