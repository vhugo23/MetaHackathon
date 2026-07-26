"""SQLAlchemy/PostgreSQL TelemetryRepository (Gate E2B).

Accepts an already-open ``Session`` — never creates, commits, rolls back,
or closes it (the caller, a test fixture today and the concrete
``UnitOfWork`` once wired in a later gate, owns the transaction). ``save``
requires ``device_id == sample.device_id`` (``ValueError``, no database
operation, matching the in-memory repository's contract exactly); a
referenced ``device_id`` that does not exist raises ``IntegrityError``
(SQLSTATE 23503, ``telemetry_samples.device_id`` foreign key) inside a
SAVEPOINT (``session.begin_nested()``), translated to
``ReferencedDeviceNotFoundError`` — the same pattern already used by
``snapshot_repository.py``/``incident_repository.py``. No retention/pruning
is performed here: the production implementation may retain indefinitely,
since ``get_recent``'s ``since`` parameter makes the retention window a
query-time concern, not a storage-time one (domain-model.md Section 12).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.errors import ReferencedDeviceNotFoundError
from meta_rne.persistence.serialization import (
    bgp_sessions_from_json,
    bgp_sessions_to_json,
    interface_states_from_json,
    interface_states_to_json,
)
from meta_rne.persistence.sqlalchemy.models import _TelemetrySampleModel

_FOREIGN_KEY_VIOLATION = "23503"


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("database timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _to_domain(model: _TelemetrySampleModel) -> TelemetrySample:
    return TelemetrySample(
        device_id=model.device_id,
        sampled_at=_to_utc(model.sampled_at),
        cpu_utilization_pct=model.cpu_utilization_pct,
        memory_utilization_pct=model.memory_utilization_pct,
        interface_error_rate=model.interface_error_rate,
        interface_states=interface_states_from_json(model.interface_states),
        bgp_sessions=bgp_sessions_from_json(model.bgp_sessions),
    )


class SqlAlchemyTelemetryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, device_id: str, sample: TelemetrySample) -> None:
        if device_id != sample.device_id:
            raise ValueError(
                f"device_id {device_id!r} does not match sample.device_id " f"{sample.device_id!r}"
            )

        model = _TelemetrySampleModel(
            device_id=sample.device_id,
            sampled_at=sample.sampled_at,
            cpu_utilization_pct=sample.cpu_utilization_pct,
            memory_utilization_pct=sample.memory_utilization_pct,
            interface_error_rate=sample.interface_error_rate,
            interface_states=interface_states_to_json(sample.interface_states),
            bgp_sessions=bgp_sessions_to_json(sample.bgp_sessions),
        )
        try:
            with self._session.begin_nested():
                self._session.add(model)
                self._session.flush()
        except IntegrityError as exc:
            sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
            if sqlstate == _FOREIGN_KEY_VIOLATION:
                raise ReferencedDeviceNotFoundError(device_id) from None
            raise

    def get_latest(self, device_id: str) -> TelemetrySample | None:
        stmt = (
            select(_TelemetrySampleModel)
            .where(_TelemetrySampleModel.device_id == device_id)
            .order_by(_TelemetrySampleModel.sampled_at.desc(), _TelemetrySampleModel.id.desc())
            .limit(1)
        )
        model = self._session.execute(stmt).scalar_one_or_none()
        return None if model is None else _to_domain(model)

    def get_recent(self, device_id: str, since: datetime) -> list[TelemetrySample]:
        stmt = (
            select(_TelemetrySampleModel)
            .where(
                _TelemetrySampleModel.device_id == device_id,
                _TelemetrySampleModel.sampled_at >= since,
            )
            .order_by(_TelemetrySampleModel.sampled_at.asc(), _TelemetrySampleModel.id.asc())
        )
        return [_to_domain(model) for model in self._session.scalars(stmt).all()]
