"""Real PostgreSQL proof of Day 7A's reingestion-after-resolution behavior
(Gate 7A-D, binding) and a regression proof that existing OPEN
deduplication is unweakened.

Uses the real ``ConfigIngestionService`` (ingestion) and
``ResolveIncidentService`` (resolution) against a real
``SqlAlchemyUnitOfWork`` — never raw SQL to create or resolve an incident;
the supported application-layer entry points do that. This is what proves
the partial unique index ``ux_incidents_open_fingerprint`` (``WHERE status =
'OPEN'``, revision 0001) already lets a resolved incident's fingerprint
recur as a brand-new OPEN row (domain-model.md Section 11,
architecture.md Section 11) — no index or migration change was needed for
this, per Gate 7A-A's analysis.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.application.config_ingestion import ConfigIngestionService
from meta_rne.application.incident_resolution import ResolveIncidentService
from meta_rne.application.models import ConfigIngestionResult, IngestConfigurationCommand
from meta_rne.domain.config import AclDirection
from meta_rne.domain.incident import Incident, IncidentStatus
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=2)
T3 = T0 + timedelta(hours=3)

_MISSING_ACL_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"


class _FixedClock:
    """A ``ResolveIncidentService.Clock``-satisfying object returning one
    fixed, injected value — never the real system clock."""

    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


def _policy() -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id="policy-acl-external-in",
        applies_to=DEVICE_ID,
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-EXTERNAL-IN",
                interface_name="GigabitEthernet0/1",
                direction=AclDirection.IN,
                severity=Severity.MEDIUM,
                recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
            ),
        ),
        created_at=T0,
    )


def _seed_policy(session_factory: Callable[[], Session]) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.configuration_policies.seed_if_missing((_policy(),))
    uow.commit()
    uow.close()


def _ingest(
    session_factory: Callable[[], Session],
    *,
    observed_at: datetime,
    snapshot_id: str,
) -> ConfigIngestionResult:
    service = ConfigIngestionService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        snapshot_id_factory=lambda: snapshot_id,
    )
    return service.ingest(
        IngestConfigurationCommand(
            device_id=DEVICE_ID,
            vendor="cisco-ios-xe",
            raw_config_text=_MISSING_ACL_RAW_CONFIG,
            observed_at=observed_at,
        )
    )


def _resolve(session_factory: Callable[[], Session], incident_id: str, at: datetime) -> Incident:
    service = ResolveIncidentService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        clock=_FixedClock(at),
    )
    return service.resolve(incident_id)


def _all_incidents(session_factory: Callable[[], Session]) -> tuple[Incident, ...]:
    uow = SqlAlchemyUnitOfWork(session_factory)
    incidents = uow.incidents.list_all()
    uow.close()
    return incidents


def test_reingestion_after_resolution__creates_new_open_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_policy(sqlalchemy_session_factory)
    _ingest(sqlalchemy_session_factory, observed_at=T0, snapshot_id="snap-1")
    incident_a = _all_incidents(sqlalchemy_session_factory)[0]

    _resolve(sqlalchemy_session_factory, incident_a.incident_id, T1)

    reingest_result = _ingest(sqlalchemy_session_factory, observed_at=T2, snapshot_id="snap-2")

    assert reingest_result.incidents_created == 1
    assert reingest_result.incidents_updated == 0

    incidents = _all_incidents(sqlalchemy_session_factory)
    assert len(incidents) == 2
    by_id = {i.incident_id: i for i in incidents}
    resolved_a = by_id[incident_a.incident_id]
    incident_b = next(i for i in incidents if i.incident_id != incident_a.incident_id)

    # --- Original incident A -------------------------------------------------
    assert resolved_a.status is IncidentStatus.RESOLVED
    assert resolved_a.resolved_at == T1
    assert resolved_a.updated_at == T1
    assert resolved_a.created_at == T0
    assert resolved_a.last_seen_at == T0
    assert resolved_a.occurrence_count == 1
    assert resolved_a.fingerprint == incident_a.fingerprint
    assert resolved_a.evidence == incident_a.evidence
    assert resolved_a.severity == incident_a.severity

    # --- New incident B -------------------------------------------------------
    assert incident_b.incident_id != incident_a.incident_id
    assert incident_b.status is IncidentStatus.OPEN
    assert incident_b.resolved_at is None
    assert incident_b.occurrence_count == 1
    assert incident_b.created_at == T2
    assert incident_b.last_seen_at == T2
    assert incident_b.updated_at == T2
    assert incident_b.fingerprint == incident_a.fingerprint
    assert incident_b.device_id == incident_a.device_id
    assert incident_b.rule_ref == incident_a.rule_ref
    assert incident_b.affected_resource == incident_a.affected_resource
    assert incident_b.source == incident_a.source

    # --- Database-wide invariant ----------------------------------------------
    same_fingerprint = [i for i in incidents if i.fingerprint == incident_a.fingerprint]
    assert len(same_fingerprint) == 2
    assert len([i for i in same_fingerprint if i.status is IncidentStatus.RESOLVED]) == 1
    assert len([i for i in same_fingerprint if i.status is IncidentStatus.OPEN]) == 1


def test_reingestion_after_resolution__new_open_incident_still_deduplicates_on_repeat(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_policy(sqlalchemy_session_factory)
    _ingest(sqlalchemy_session_factory, observed_at=T0, snapshot_id="snap-1")
    incident_a = _all_incidents(sqlalchemy_session_factory)[0]
    _resolve(sqlalchemy_session_factory, incident_a.incident_id, T1)
    _ingest(sqlalchemy_session_factory, observed_at=T2, snapshot_id="snap-2")
    incidents_after_b = _all_incidents(sqlalchemy_session_factory)
    incident_b = next(i for i in incidents_after_b if i.incident_id != incident_a.incident_id)

    third_result = _ingest(sqlalchemy_session_factory, observed_at=T3, snapshot_id="snap-3")

    assert third_result.incidents_created == 0
    assert third_result.incidents_updated == 1

    incidents = _all_incidents(sqlalchemy_session_factory)
    assert len(incidents) == 2  # no third incident created
    by_id = {i.incident_id: i for i in incidents}

    updated_b = by_id[incident_b.incident_id]
    assert updated_b.status is IncidentStatus.OPEN
    assert updated_b.occurrence_count == 2
    assert updated_b.last_seen_at == T3
    assert updated_b.updated_at == T3
    assert updated_b.created_at == T2

    unchanged_a = by_id[incident_a.incident_id]
    assert unchanged_a.status is IncidentStatus.RESOLVED
    assert unchanged_a.resolved_at == T1
    assert unchanged_a.updated_at == T1
    assert unchanged_a.occurrence_count == 1

    same_fingerprint_open = [
        i
        for i in incidents
        if i.fingerprint == incident_a.fingerprint and i.status is IncidentStatus.OPEN
    ]
    assert len(same_fingerprint_open) == 1
    assert same_fingerprint_open[0].incident_id == incident_b.incident_id
