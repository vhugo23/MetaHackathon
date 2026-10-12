"""SQLAlchemy/PostgreSQL OperationalStateRepository (NPE-1D1A).

Accepts an already-open ``Session`` and never commits, rolls back or closes
it: the latest-state write shares the caller's transaction with the incident
lifecycle writes.

Concurrency is one atomic ``INSERT ... ON CONFLICT (lab_name) DO UPDATE ...
WHERE`` — never a read-decide-write. The conditional update only fires for a
*distinct* collection that is *strictly newer*::

    stored.collection_id <> excluded.collection_id
    AND stored.collected_at < excluded.collected_at

so an older collection can never replace newer state, and the same
``collection_id`` with a newer timestamp is not an update. PostgreSQL locks
the conflicting row while evaluating the predicate, so competing writers
serialize. When the statement writes nothing, the stored row (still locked by
this transaction) is read back and classified by the same
``decide_latest_write`` the in-memory repository uses: exact replay
(``UNCHANGED``), stale, or a conflict.
"""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import Table, literal_column, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import ReturningInsert

from meta_rne.domain.operational_state import (
    FabricOperationalState,
    FabricSourceError,
    OperationalStateSaveOutcome,
    derive_fabric_source,
)
from meta_rne.persistence.errors import PersistenceError
from meta_rne.persistence.operational_state_semantics import (
    LatestWriteDecision,
    decide_latest_write,
)
from meta_rne.persistence.operational_state_serialization import (
    OPERATIONAL_STATE_SCHEMA_VERSION,
    fabric_from_json,
    fabric_to_json,
)
from meta_rne.persistence.serialization import SerializationError
from meta_rne.persistence.sqlalchemy.models import _OperationalStateLatestModel

_TABLE = cast(Table, _OperationalStateLatestModel.__table__)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("database timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _decode_row(
    lab_name: str,
    collection_id: str,
    collected_at: datetime,
    schema_version: int,
    state: Any,
) -> FabricOperationalState:
    """Decode a stored row and fail closed if its metadata columns disagree
    with the document they duplicate. Nothing is repaired."""
    fabric = fabric_from_json(schema_version, state)
    if fabric.collection_id != collection_id:
        raise SerializationError(
            f"operational state row for lab {lab_name!r}: collection_id column "
            f"{collection_id!r} disagrees with the stored document {fabric.collection_id!r}"
        )
    if _to_utc(collected_at) != fabric.collected_at:
        raise SerializationError(
            f"operational state row for lab {lab_name!r}: collected_at column "
            f"{collected_at.isoformat()} disagrees with the stored document "
            f"{fabric.collected_at.isoformat()}"
        )
    try:
        document_lab = derive_fabric_source(fabric).lab_name
    except FabricSourceError as exc:
        raise SerializationError(
            f"operational state row for lab {lab_name!r}: stored document has no single "
            f"source: {exc}"
        ) from exc
    if document_lab != lab_name:
        raise SerializationError(
            f"operational state row for lab {lab_name!r}: stored document belongs to "
            f"lab {document_lab!r}"
        )
    return fabric


class SqlAlchemyOperationalStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def save_latest(self, fabric: FabricOperationalState) -> OperationalStateSaveOutcome:
        lab_name = derive_fabric_source(fabric).lab_name
        insert = pg_insert(_TABLE).values(
            lab_name=lab_name,
            collection_id=fabric.collection_id,
            collected_at=fabric.collected_at,
            schema_version=OPERATIONAL_STATE_SCHEMA_VERSION,
            state=fabric_to_json(fabric),
        )
        excluded = insert.excluded
        upsert = insert.on_conflict_do_update(
            index_elements=[_TABLE.c.lab_name],
            set_={
                "collection_id": excluded.collection_id,
                "collected_at": excluded.collected_at,
                "schema_version": excluded.schema_version,
                "state": excluded.state,
            },
            where=(_TABLE.c.collection_id != excluded.collection_id)
            & (_TABLE.c.collected_at < excluded.collected_at),
        )
        # xmax = 0 on the returned row means it was freshly inserted.
        statement: ReturningInsert[tuple[bool]] = upsert.returning(
            literal_column("(xmax = 0)").label("inserted")
        )
        row = self._session.execute(statement).first()
        if row is not None:
            return (
                OperationalStateSaveOutcome.CREATED
                if row.inserted
                else OperationalStateSaveOutcome.REPLACED
            )

        stored = self._read(lab_name)
        if stored is None:
            raise PersistenceError(
                f"operational state for lab {lab_name!r} vanished during a conflicting upsert"
            )
        if decide_latest_write(lab_name, stored, fabric) is LatestWriteDecision.UNCHANGED:
            return OperationalStateSaveOutcome.UNCHANGED
        raise PersistenceError(
            f"operational state for lab {lab_name!r} is older than the incoming collection "
            "but the conditional upsert did not apply it"
        )

    def get_latest(self, lab_name: str) -> FabricOperationalState | None:
        return self._read(lab_name)

    def _read(self, lab_name: str) -> FabricOperationalState | None:
        statement = select(
            _TABLE.c.lab_name,
            _TABLE.c.collection_id,
            _TABLE.c.collected_at,
            _TABLE.c.schema_version,
            _TABLE.c.state,
        ).where(_TABLE.c.lab_name == lab_name)
        row = self._session.execute(statement).first()
        if row is None:
            return None
        return _decode_row(
            row.lab_name, row.collection_id, row.collected_at, row.schema_version, row.state
        )
