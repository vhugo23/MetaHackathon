"""Real PostgreSQL concurrency proof for
``IncidentRepository.upsert_open_incident`` (Day 4B3,
test-strategy.md Section 9's named concurrency test).

Four worker threads, each with its own connection/Session/repository
instance, race to upsert the identical fingerprint at (as close to) the same
instant via a Barrier, each issuing an explicit commit. The partial unique
index ``ux_incidents_open_fingerprint`` plus the ``INSERT ... ON CONFLICT``
statement (never a read-before-write) must make exactly one worker's INSERT
branch fire and every other worker's call resolve through the UPDATE branch
— never an unhandled unique-violation exception escaping to a worker, and
never two OPEN rows for the same fingerprint.
"""

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from meta_rne.domain.config import AclDirection, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.incident import (
    Incident,
    IncidentCandidate,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository

pytestmark = pytest.mark.postgres

DEVICE_ID = "concurrency-spine-01"
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


def test_incident_repository_sqlalchemy__concurrent_upsert_same_fingerprint__yields_one_open(
    postgres_test_database_url: str,
    _meta_rne_test_migrated: None,
) -> None:
    setup_engine = create_engine(postgres_test_database_url)
    setup_session = Session(bind=setup_engine)
    try:
        SqlAlchemyDeviceRepository(setup_session).save(
            Device(
                device_id=DEVICE_ID,
                vendor=VendorType.CISCO_IOS_XE,
                current_snapshot_id=None,
                baseline_snapshot_id=None,
                created_at=T0,
                updated_at=T0,
            )
        )
        setup_session.commit()

        candidate = _candidate()
        fingerprint = compute_fingerprint(
            candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
        )
        barrier = threading.Barrier(WORKER_COUNT)
        results: list[IncidentUpsertResult] = []
        errors: list[BaseException] = []
        state_lock = threading.Lock()

        def worker(worker_index: int) -> None:
            worker_engine = create_engine(postgres_test_database_url)
            session = Session(bind=worker_engine)
            try:
                repo = SqlAlchemyIncidentRepository(
                    session, incident_id_factory=lambda: f"concurrency-worker-{worker_index}"
                )
                barrier.wait()
                try:
                    result = repo.upsert_open_incident(candidate, fingerprint, T0)
                    session.commit()
                    with state_lock:
                        results.append(result)
                except BaseException as exc:
                    session.rollback()
                    with state_lock:
                        errors.append(exc)
            finally:
                session.close()
                worker_engine.dispose()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(WORKER_COUNT)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == [], f"no unhandled exception should escape a worker, got: {errors}"
        assert len(results) == WORKER_COUNT

        created = [r for r in results if r.outcome == IncidentUpsertOutcome.CREATED]
        updated = [r for r in results if r.outcome == IncidentUpsertOutcome.UPDATED]
        assert len(created) == 1
        assert len(updated) == WORKER_COUNT - 1

        incident_ids = {r.incident.incident_id for r in results}
        assert len(incident_ids) == 1

        verify_session = Session(bind=setup_engine)
        try:
            repo = SqlAlchemyIncidentRepository(verify_session)
            open_rows = [i for i in repo.list_all() if i.fingerprint == fingerprint]
            assert len(open_rows) == 1
            assert open_rows[0].occurrence_count == WORKER_COUNT
        finally:
            verify_session.close()
    finally:
        cleanup_session = Session(bind=setup_engine)
        try:
            cleanup_session.execute(
                text("DELETE FROM incidents WHERE device_id = :d"), {"d": DEVICE_ID}
            )
            cleanup_session.execute(
                text("DELETE FROM devices WHERE device_id = :d"), {"d": DEVICE_ID}
            )
            cleanup_session.commit()
        finally:
            cleanup_session.close()
        setup_session.close()
        setup_engine.dispose()


# --- Concurrent IncidentRepository.resolve() (Day 7A, Gate 7A-D) -----------
#
# Two independent worker threads, each with its own connection/Session/
# repository instance, resolve the *same* already-OPEN incident at (as
# close to) the same instant via a Barrier, each issuing an explicit
# commit. The atomic conditional UPDATE (WHERE status = 'OPEN', Gate 7A-A)
# must let exactly one worker's UPDATE branch actually transition the row;
# the other's own conditional UPDATE affects no row (the first worker's
# commit already flipped status away from 'OPEN'), so it falls through to
# the internal follow-up SELECT and returns the now-persisted RESOLVED
# incident unchanged — never raising, never a second row, never corrupting
# occurrence_count/evidence/last_seen_at (resolve() only ever writes
# status/resolved_at/updated_at). Both workers use the identical resolved_at
# value, so there is no ambiguity about "which value wins" to assert on.

_RESOLVE_DEVICE_ID = "concurrency-resolve-spine-01"
_RESOLVED_AT = T0 + timedelta(hours=1)
_RESOLVE_WORKER_COUNT = 2


def test_incident_repository_sqlalchemy__concurrent_resolve_same_incident__yields_one_resolved_row(
    postgres_test_database_url: str,
    _meta_rne_test_migrated: None,
) -> None:
    setup_engine = create_engine(postgres_test_database_url)
    setup_session = Session(bind=setup_engine)
    try:
        SqlAlchemyDeviceRepository(setup_session).save(
            Device(
                device_id=_RESOLVE_DEVICE_ID,
                vendor=VendorType.CISCO_IOS_XE,
                current_snapshot_id=None,
                baseline_snapshot_id=None,
                created_at=T0,
                updated_at=T0,
            )
        )
        setup_session.commit()

        candidate = IncidentCandidate(
            device_id=_RESOLVE_DEVICE_ID,
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
        fingerprint = compute_fingerprint(
            candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
        )
        seed_repo = SqlAlchemyIncidentRepository(
            setup_session, incident_id_factory=lambda: "concurrency-resolve-target"
        )
        created = seed_repo.upsert_open_incident(candidate, fingerprint, T0)
        setup_session.commit()
        incident_id = created.incident.incident_id

        barrier = threading.Barrier(_RESOLVE_WORKER_COUNT)
        results: list[Incident | None] = []
        errors: list[BaseException] = []
        state_lock = threading.Lock()

        def worker() -> None:
            worker_engine = create_engine(postgres_test_database_url)
            session = Session(bind=worker_engine)
            try:
                repo = SqlAlchemyIncidentRepository(session)
                barrier.wait()
                try:
                    result = repo.resolve(incident_id, _RESOLVED_AT)
                    session.commit()
                    with state_lock:
                        results.append(result)
                except BaseException as exc:
                    session.rollback()
                    with state_lock:
                        errors.append(exc)
            finally:
                session.close()
                worker_engine.dispose()

        threads = [threading.Thread(target=worker) for _ in range(_RESOLVE_WORKER_COUNT)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == [], f"no unhandled exception should escape a worker, got: {errors}"
        assert len(results) == _RESOLVE_WORKER_COUNT

        # Both callers must receive a RESOLVED incident — never None, never
        # an exception — and, since both used the identical resolved_at,
        # both must report the same persisted resolved_at/updated_at.
        for result in results:
            assert result is not None
            assert result.status is IncidentStatus.RESOLVED
            assert result.resolved_at == _RESOLVED_AT
            assert result.updated_at == _RESOLVED_AT

        verify_session = Session(bind=setup_engine)
        try:
            repo = SqlAlchemyIncidentRepository(verify_session)
            rows = [i for i in repo.list_all() if i.fingerprint == fingerprint]
            assert len(rows) == 1  # only one row for this incident_id/fingerprint
            final = rows[0]
            assert final.incident_id == incident_id
            assert final.status is IncidentStatus.RESOLVED
            assert final.resolved_at == final.updated_at == _RESOLVED_AT
            assert final.occurrence_count == 1  # unchanged by resolution
            assert final.last_seen_at == T0  # unchanged by resolution
            assert final.fingerprint == fingerprint
            assert final.device_id == _RESOLVE_DEVICE_ID
            assert final.rule_ref == candidate.rule_ref
            assert final.affected_resource == candidate.affected_resource

            open_rows = [
                i
                for i in repo.list_all()
                if i.fingerprint == fingerprint and i.status is IncidentStatus.OPEN
            ]
            assert open_rows == []  # no duplicate active OPEN incident
        finally:
            verify_session.close()
    finally:
        cleanup_session = Session(bind=setup_engine)
        try:
            cleanup_session.execute(
                text("DELETE FROM incidents WHERE device_id = :d"), {"d": _RESOLVE_DEVICE_ID}
            )
            cleanup_session.execute(
                text("DELETE FROM devices WHERE device_id = :d"), {"d": _RESOLVE_DEVICE_ID}
            )
            cleanup_session.commit()
        finally:
            cleanup_session.close()
        setup_session.close()
        setup_engine.dispose()
