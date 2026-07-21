"""PostgreSQL-only proofs for SqlAlchemyIncidentRepository (Day 4B3) that
aren't part of the shared in-memory/SQLAlchemy conformance contract:
Session reuse after a translated ``ReferencedDeviceNotFoundError``, and the
sequential CREATED/UPDATED outcome mapping via the real ``xmax`` tell.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.domain.config import (
    AclDirection,
    VendorType,
)
from meta_rne.domain.device import Device
from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertOutcome,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.persistence.errors import ReferencedDeviceNotFoundError
from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)


def _candidate(**overrides: object) -> IncidentCandidate:
    defaults: dict[str, object] = {
        "device_id": DEVICE_ID,
        "source": IncidentSource.POLICY_VIOLATION,
        "rule_ref": "policy-acl-external-in",
        "affected_resource": "interface:GigabitEthernet0/1:acl_in",
        "severity": Severity.MEDIUM,
        "evidence": PolicyViolationIncidentEvidence(
            source_snapshot_id="snap-1",
            violation_type=ViolationType.MISSING_REQUIRED_ACL,
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name=None,
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
        ),
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        "observed_at": T0,
    }
    defaults.update(overrides)
    return IncidentCandidate(**defaults)  # type: ignore[arg-type]


def _fingerprint(candidate: IncidentCandidate) -> str:
    return compute_fingerprint(
        candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
    )


def test_incident_repository_sqlalchemy__session_remains_usable_after_referenced_device_not_found(
    sqlalchemy_session: Session,
) -> None:
    incidents = SqlAlchemyIncidentRepository(sqlalchemy_session, incident_id_factory=lambda: "id-1")
    candidate = _candidate(device_id="does-not-exist")

    with pytest.raises(ReferencedDeviceNotFoundError):
        incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    # The same Session must still be usable: a real Device insert followed by
    # a real Incident upsert on it must succeed (item 6's binding requirement).
    SqlAlchemyDeviceRepository(sqlalchemy_session).save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    valid_candidate = _candidate(device_id=DEVICE_ID)
    result = incidents.upsert_open_incident(valid_candidate, _fingerprint(valid_candidate), T0)

    assert result.outcome == IncidentUpsertOutcome.CREATED
    assert incidents.get_by_id(result.incident.incident_id) == result.incident


def test_incident_repository_sqlalchemy__sequential_upserts__map_created_then_updated(
    sqlalchemy_session: Session,
) -> None:
    SqlAlchemyDeviceRepository(sqlalchemy_session).save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    incidents = SqlAlchemyIncidentRepository(
        sqlalchemy_session, incident_id_factory=lambda: "sequential-id"
    )
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)

    first = incidents.upsert_open_incident(candidate, fingerprint, T0)
    second = incidents.upsert_open_incident(_candidate(observed_at=T1), fingerprint, T1)

    assert first.outcome == IncidentUpsertOutcome.CREATED
    assert second.outcome == IncidentUpsertOutcome.UPDATED
    assert first.incident.incident_id == second.incident.incident_id
    assert second.incident.occurrence_count == 2


# --- IncidentRepository.resolve() PostgreSQL-only proofs (Day 7A) -----------


def test_incident_repository_sqlalchemy__resolve__does_not_return_stale_identity_map_state(
    sqlalchemy_session: Session,
) -> None:
    """A prior get_by_id() on this same Session must not poison the
    follow-up lookup resolve() performs when its own conditional UPDATE
    affects no row (already-RESOLVED case) — populate_existing=True must
    force a fresh read from the database rather than returning the
    Session's identity-map-cached (pre-resolution) object."""
    SqlAlchemyDeviceRepository(sqlalchemy_session).save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    incidents = SqlAlchemyIncidentRepository(sqlalchemy_session, incident_id_factory=lambda: "id-1")
    candidate = _candidate()
    created = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)

    # Populate the Session's identity map with the pre-resolution (OPEN) state.
    stale = incidents.get_by_id(created.incident.incident_id)
    assert stale is not None
    assert stale.status == IncidentStatus.OPEN

    first_resolve = incidents.resolve(created.incident.incident_id, T1)
    assert first_resolve is not None
    assert first_resolve.status == IncidentStatus.RESOLVED

    # A second resolve() call finds the row already RESOLVED, so its
    # conditional UPDATE affects no row and it falls back to the internal
    # follow-up SELECT — this must reflect the true RESOLVED state, not the
    # OPEN object cached in the identity map by the earlier get_by_id() call.
    second_resolve = incidents.resolve(created.incident.incident_id, T1)
    assert second_resolve is not None
    assert second_resolve.status == IncidentStatus.RESOLVED
    assert second_resolve.resolved_at == T1


def test_incident_repository_sqlalchemy__resolve__unknown_id__returns_none(
    sqlalchemy_session: Session,
) -> None:
    incidents = SqlAlchemyIncidentRepository(sqlalchemy_session)

    assert incidents.resolve("does-not-exist", T1) is None


def test_incident_repository_sqlalchemy__resolve__acknowledged_incident__raises_value_error(
    sqlalchemy_session: Session,
) -> None:
    SqlAlchemyDeviceRepository(sqlalchemy_session).save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    incidents = SqlAlchemyIncidentRepository(sqlalchemy_session, incident_id_factory=lambda: "id-1")
    candidate = _candidate()
    created = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T0)
    # Force the row into the dormant ACKNOWLEDGED status directly, bypassing
    # the repository (no public transition into it exists yet).
    sqlalchemy_session.execute(
        text("UPDATE incidents SET status = 'ACKNOWLEDGED' WHERE incident_id = :id"),
        {"id": created.incident.incident_id},
    )

    with pytest.raises(ValueError):
        incidents.resolve(created.incident.incident_id, T1)


def test_incident_repository_sqlalchemy__resolve__does_not_touch_occurrence_count_or_evidence(
    sqlalchemy_session: Session,
) -> None:
    SqlAlchemyDeviceRepository(sqlalchemy_session).save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    incidents = SqlAlchemyIncidentRepository(sqlalchemy_session, incident_id_factory=lambda: "id-1")
    candidate = _candidate()
    fingerprint = _fingerprint(candidate)
    created = incidents.upsert_open_incident(candidate, fingerprint, T0)
    incidents.upsert_open_incident(_candidate(observed_at=T1), fingerprint, T1)

    resolved = incidents.resolve(created.incident.incident_id, T1)

    assert resolved is not None
    assert resolved.occurrence_count == 2
    assert resolved.evidence == candidate.evidence
    assert resolved.last_seen_at == T1


def test_incident_repository_sqlalchemy__resolve__stale_timestamp_sql_path__leaves_row_open(
    sqlalchemy_session: Session,
) -> None:
    """Proves the conditional SQL path itself (not just the domain method)
    rejects a resolved_at earlier than the persisted updated_at, and leaves
    the row genuinely untouched in the database — not merely returning an
    error while silently committing a partial write."""
    SqlAlchemyDeviceRepository(sqlalchemy_session).save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    incidents = SqlAlchemyIncidentRepository(sqlalchemy_session, incident_id_factory=lambda: "id-1")
    candidate = _candidate(observed_at=T1)
    created = incidents.upsert_open_incident(candidate, _fingerprint(candidate), T1)

    with pytest.raises(ValueError):
        incidents.resolve(created.incident.incident_id, T0)

    stored = incidents.get_by_id(created.incident.incident_id)
    assert stored is not None
    assert stored.status == IncidentStatus.OPEN
    assert stored.resolved_at is None
    assert stored.updated_at == T1
    assert stored.last_seen_at == T1
    assert stored.occurrence_count == 1
