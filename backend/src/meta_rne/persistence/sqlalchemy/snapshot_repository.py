"""SQLAlchemy/PostgreSQL ConfigurationSnapshotRepository (Day 4B2).

Accepts an already-open ``Session`` — never creates, commits, rolls back,
or closes it. ``add`` distinguishes a duplicate primary key from a missing
referenced device by inspecting the PostgreSQL SQLSTATE of a translated
``IntegrityError`` (never exposing the exception, constraint name, or
psycopg type itself) — recovered via a SAVEPOINT (``session.begin_nested``)
so the caller's outer transaction is never touched and the Session remains
fully usable afterward.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meta_rne.domain.config import VendorType
from meta_rne.domain.snapshot import ConfigurationSnapshot
from meta_rne.persistence.errors import (
    PersistenceError,
    ReferencedDeviceNotFoundError,
    SnapshotAlreadyExistsError,
)
from meta_rne.persistence.serialization import (
    normalized_config_from_json,
    normalized_config_to_json,
)
from meta_rne.persistence.sqlalchemy.models import _ConfigurationSnapshotModel

_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("database timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _to_domain(model: _ConfigurationSnapshotModel) -> ConfigurationSnapshot:
    return ConfigurationSnapshot(
        snapshot_id=model.snapshot_id,
        device_id=model.device_id,
        vendor=VendorType(model.vendor),
        raw_config_text=model.raw_config_text,
        raw_text_hash=model.raw_text_hash,
        normalized_config=normalized_config_from_json(model.normalized_config),
        submitted_at=_to_utc(model.submitted_at),
    )


def _translate_integrity_error(exc: IntegrityError, snapshot: ConfigurationSnapshot) -> Exception:
    sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
    if sqlstate == _UNIQUE_VIOLATION:
        return SnapshotAlreadyExistsError(snapshot.snapshot_id)
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        return ReferencedDeviceNotFoundError(snapshot.device_id)
    return PersistenceError("unexpected persistence failure while adding configuration snapshot")


class SqlAlchemyConfigurationSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, snapshot_id: str) -> ConfigurationSnapshot | None:
        model = self._session.get(_ConfigurationSnapshotModel, snapshot_id)
        return None if model is None else _to_domain(model)

    def add(self, snapshot: ConfigurationSnapshot) -> None:
        model = _ConfigurationSnapshotModel(
            snapshot_id=snapshot.snapshot_id,
            device_id=snapshot.device_id,
            vendor=snapshot.vendor.value,
            raw_config_text=snapshot.raw_config_text,
            raw_text_hash=snapshot.raw_text_hash,
            normalized_config=normalized_config_to_json(snapshot.normalized_config),
            submitted_at=snapshot.submitted_at,
        )
        try:
            with self._session.begin_nested():
                self._session.add(model)
                self._session.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc, snapshot) from None
