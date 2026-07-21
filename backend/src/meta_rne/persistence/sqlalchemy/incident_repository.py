"""SQLAlchemy/PostgreSQL IncidentRepository (Day 4B3).

Accepts an already-open ``Session`` — never creates, commits, rolls back, or
closes it (the caller, a test fixture today and the concrete ``UnitOfWork``,
owns the transaction). ``upsert_open_incident`` is a single
``INSERT ... ON CONFLICT (fingerprint) WHERE status = 'OPEN' DO UPDATE ...``
statement — never a read-before-write — targeting the partial unique index
``ux_incidents_open_fingerprint`` (alembic/versions/0001_...). The UPDATE
branch is itself conditional (``WHERE excluded.last_seen_at >=
incidents.last_seen_at``) so a stale observation never mutates the stored
row; when that condition suppresses the update, ``RETURNING`` yields no row,
and the stale-vs-unexpected distinction (domain-model.md Section 11) is
resolved by one internal, non-public follow-up SELECT — never exposed as a
``find_open_by_fingerprint`` port method.

``xmax = 0`` (Postgres's standard "freshly inserted, not touched by ON
CONFLICT" tell) is used only inside this module to map the RETURNING row to
``IncidentUpsertOutcome`` — never leaked as an ORM object or the raw
``xmax`` value itself.

A referenced ``device_id`` that does not exist raises ``IntegrityError``
(SQLSTATE 23503, ``incidents.device_id`` foreign key) inside a SAVEPOINT
(``session.begin_nested()``), translated to ``ReferencedDeviceNotFoundError``
without touching the caller's outer transaction or Session — the same
pattern already used by ``snapshot_repository.py``.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import literal_column, select, text
from sqlalchemy import update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meta_rne.domain.incident import (
    Incident,
    IncidentCandidate,
    IncidentSource,
    IncidentStatus,
    IncidentUpsertOutcome,
    IncidentUpsertResult,
)
from meta_rne.domain.policy import Severity
from meta_rne.persistence.errors import PersistenceError, ReferencedDeviceNotFoundError
from meta_rne.persistence.incident_id import default_incident_id_factory
from meta_rne.persistence.incident_validation import (
    require_non_empty_incident_id,
    validate_candidate_consistency,
)
from meta_rne.persistence.serialization import (
    policy_violation_evidence_from_json,
    policy_violation_evidence_to_json,
)
from meta_rne.persistence.sqlalchemy.models import _IncidentModel

_FOREIGN_KEY_VIOLATION = "23503"


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("database timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _build_domain_incident(
    *,
    incident_id: str,
    fingerprint: str,
    device_id: str,
    source: str,
    rule_ref: str,
    affected_resource: str,
    severity: str,
    status: str,
    evidence: Any,
    recommendation: str,
    created_at: datetime,
    last_seen_at: datetime,
    occurrence_count: int,
    updated_at: datetime,
    resolved_at: datetime | None,
) -> Incident:
    return Incident(
        incident_id=incident_id,
        fingerprint=fingerprint,
        device_id=device_id,
        source=IncidentSource(source),
        rule_ref=rule_ref,
        affected_resource=affected_resource,
        severity=Severity(severity),
        status=IncidentStatus(status),
        evidence=policy_violation_evidence_from_json(evidence),
        recommendation=recommendation,
        created_at=_to_utc(created_at),
        last_seen_at=_to_utc(last_seen_at),
        occurrence_count=occurrence_count,
        updated_at=_to_utc(updated_at),
        resolved_at=_to_utc(resolved_at) if resolved_at is not None else None,
    )


def _to_domain(model: _IncidentModel) -> Incident:
    return _build_domain_incident(
        incident_id=model.incident_id,
        fingerprint=model.fingerprint,
        device_id=model.device_id,
        source=model.source,
        rule_ref=model.rule_ref,
        affected_resource=model.affected_resource,
        severity=model.severity,
        status=model.status,
        evidence=model.evidence,
        recommendation=model.recommendation,
        created_at=model.created_at,
        last_seen_at=model.last_seen_at,
        occurrence_count=model.occurrence_count,
        updated_at=model.updated_at,
        resolved_at=model.resolved_at,
    )


def _to_domain_from_row(row: Any) -> Incident:
    return _build_domain_incident(
        incident_id=row.incident_id,
        fingerprint=row.fingerprint,
        device_id=row.device_id,
        source=row.source,
        rule_ref=row.rule_ref,
        affected_resource=row.affected_resource,
        severity=row.severity,
        status=row.status,
        evidence=row.evidence,
        recommendation=row.recommendation,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        occurrence_count=row.occurrence_count,
        updated_at=row.updated_at,
        resolved_at=row.resolved_at,
    )


class SqlAlchemyIncidentRepository:
    def __init__(
        self,
        session: Session,
        incident_id_factory: Callable[[], str] = default_incident_id_factory,
    ) -> None:
        self._session = session
        self._incident_id_factory = incident_id_factory

    def get_by_id(self, incident_id: str) -> Incident | None:
        model = self._session.get(_IncidentModel, incident_id)
        return None if model is None else _to_domain(model)

    def list_all(self) -> tuple[Incident, ...]:
        stmt = select(_IncidentModel).order_by(
            _IncidentModel.created_at, _IncidentModel.incident_id
        )
        return tuple(_to_domain(model) for model in self._session.scalars(stmt).all())

    def _get_open_by_fingerprint(self, fingerprint: str) -> Incident | None:
        # Internal only — never exposed on the public IncidentRepository
        # port (Day 4B1 binding decision). Used solely to distinguish a
        # stale observation from a genuinely unexpected no-row result after
        # the conditional ON CONFLICT DO UPDATE below suppresses its update.
        stmt = select(_IncidentModel).where(
            _IncidentModel.fingerprint == fingerprint,
            _IncidentModel.status == IncidentStatus.OPEN.value,
        )
        model = self._session.execute(stmt).scalar_one_or_none()
        return None if model is None else _to_domain(model)

    def upsert_open_incident(
        self, candidate: IncidentCandidate, fingerprint: str, observed_at: datetime
    ) -> IncidentUpsertResult:
        validate_candidate_consistency(candidate, fingerprint, observed_at)

        incident_id = self._incident_id_factory()
        require_non_empty_incident_id(incident_id)

        insert_stmt = pg_insert(_IncidentModel).values(
            incident_id=incident_id,
            fingerprint=fingerprint,
            device_id=candidate.device_id,
            source=candidate.source.value,
            rule_ref=candidate.rule_ref,
            affected_resource=candidate.affected_resource,
            severity=candidate.severity.value,
            status=IncidentStatus.OPEN.value,
            evidence=policy_violation_evidence_to_json(candidate.evidence),
            recommendation=candidate.recommendation,
            created_at=observed_at,
            last_seen_at=observed_at,
            occurrence_count=1,
            updated_at=observed_at,
            resolved_at=None,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[_IncidentModel.fingerprint],
            index_where=text("status = 'OPEN'"),
            set_={
                "last_seen_at": insert_stmt.excluded.last_seen_at,
                "occurrence_count": _IncidentModel.occurrence_count + 1,
                "severity": insert_stmt.excluded.severity,
                "evidence": insert_stmt.excluded.evidence,
                "recommendation": insert_stmt.excluded.recommendation,
                "updated_at": insert_stmt.excluded.updated_at,
            },
            where=(insert_stmt.excluded.last_seen_at >= _IncidentModel.last_seen_at),
        ).returning(
            _IncidentModel.incident_id,
            _IncidentModel.fingerprint,
            _IncidentModel.device_id,
            _IncidentModel.source,
            _IncidentModel.rule_ref,
            _IncidentModel.affected_resource,
            _IncidentModel.severity,
            _IncidentModel.status,
            _IncidentModel.evidence,
            _IncidentModel.recommendation,
            _IncidentModel.created_at,
            _IncidentModel.last_seen_at,
            _IncidentModel.occurrence_count,
            _IncidentModel.updated_at,
            _IncidentModel.resolved_at,
            literal_column("(xmax = 0)").label("was_inserted"),
        )

        try:
            with self._session.begin_nested():
                row = self._session.execute(stmt).first()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc, candidate) from None

        if row is not None:
            outcome = (
                IncidentUpsertOutcome.CREATED if row.was_inserted else IncidentUpsertOutcome.UPDATED
            )
            return IncidentUpsertResult(incident=_to_domain_from_row(row), outcome=outcome)

        # The conditional ON CONFLICT ... WHERE excluded.last_seen_at >=
        # incidents.last_seen_at suppressed the update: either this was a
        # genuine stale observation, or something unexpected happened
        # between the statement and this check. Only one internal SELECT,
        # never the primary write mechanism (item 2's "no read-before-write").
        existing = self._get_open_by_fingerprint(fingerprint)
        if existing is not None and observed_at < existing.last_seen_at:
            raise ValueError(
                "stale observation: observed_at precedes the existing OPEN incident's "
                "last_seen_at"
            )
        raise PersistenceError(
            "unexpected persistence failure: upsert_open_incident's conditional "
            "ON CONFLICT DO UPDATE affected no row"
        )

    def resolve(self, incident_id: str, resolved_at: datetime) -> Incident | None:
        """One atomic conditional UPDATE, same idiom as upsert_open_incident:
        never a read-before-write. Only status/resolved_at/updated_at are
        ever written here, so a concurrent upsert_open_incident on this same
        row (occurrence_count/evidence/last_seen_at/severity) can never be
        clobbered by a resolve(), or vice versa. The WHERE clause's own
        `updated_at <= resolved_at` guard (not `last_seen_at`) is what makes
        this monotonic: an OPEN incident may legally already have
        `last_seen_at < updated_at`, and checking only against last_seen_at
        would let a resolve() move updated_at backward."""
        resolved_at = _to_utc(resolved_at)

        stmt = (
            sa_update(_IncidentModel)
            .where(
                _IncidentModel.incident_id == incident_id,
                _IncidentModel.status == IncidentStatus.OPEN.value,
                _IncidentModel.updated_at <= resolved_at,
            )
            .values(
                status=IncidentStatus.RESOLVED.value,
                resolved_at=resolved_at,
                updated_at=resolved_at,
            )
            .returning(
                _IncidentModel.incident_id,
                _IncidentModel.fingerprint,
                _IncidentModel.device_id,
                _IncidentModel.source,
                _IncidentModel.rule_ref,
                _IncidentModel.affected_resource,
                _IncidentModel.severity,
                _IncidentModel.status,
                _IncidentModel.evidence,
                _IncidentModel.recommendation,
                _IncidentModel.created_at,
                _IncidentModel.last_seen_at,
                _IncidentModel.occurrence_count,
                _IncidentModel.updated_at,
                _IncidentModel.resolved_at,
            )
        )

        with self._session.begin_nested():
            row = self._session.execute(stmt).first()

        if row is not None:
            return _to_domain_from_row(row)

        # The conditional UPDATE matched no row: either incident_id doesn't
        # exist, the row's status isn't OPEN (already RESOLVED, or the
        # dormant ACKNOWLEDGED), or it is still OPEN but its persisted
        # updated_at is later than the supplied resolved_at. One internal
        # follow-up SELECT only, never the primary write mechanism (same
        # idiom as upsert_open_incident's stale-observation check).
        # populate_existing=True guarantees this returns the row's true
        # current persisted state rather than a stale identity-map-cached
        # object from an earlier get_by_id() call on this same Session.
        existing_stmt = (
            select(_IncidentModel)
            .where(_IncidentModel.incident_id == incident_id)
            .execution_options(populate_existing=True)
        )
        existing_model = self._session.execute(existing_stmt).scalar_one_or_none()
        if existing_model is None:
            return None
        if existing_model.status == IncidentStatus.RESOLVED.value:
            return _to_domain(existing_model)
        if existing_model.status == IncidentStatus.OPEN.value:
            # Never return an unchanged OPEN incident as apparent success:
            # the row is still OPEN only because the timestamp guard above
            # rejected it, not because of a status mismatch.
            raise ValueError(
                f"cannot resolve incident {incident_id!r}: resolved_at {resolved_at!r} "
                f"precedes the persisted updated_at {existing_model.updated_at!r}"
            )
        raise ValueError(
            f"cannot resolve incident {incident_id!r}: persisted status is "
            f"{existing_model.status!r}, not OPEN or RESOLVED"
        )


def _translate_integrity_error(exc: IntegrityError, candidate: IncidentCandidate) -> Exception:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        return ReferencedDeviceNotFoundError(candidate.device_id)
    return PersistenceError("unexpected persistence failure while upserting incident")
