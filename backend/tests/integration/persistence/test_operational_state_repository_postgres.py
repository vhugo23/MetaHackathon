"""PostgreSQL-specific OperationalStateRepository proofs (NPE-1D1A): fail-closed
reads of inconsistent or corrupt rows, and real concurrent writers.

The shared semantics (create / replace / stale / conflicts / replay) are
covered for both backends by
tests/contract/persistence/test_operational_state_repository_contract.py.
"""

import json
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from meta_rne.domain.operational_state import FabricOperationalState, OperationalStateSaveOutcome
from meta_rne.persistence.errors import (
    OperationalStateConflictError,
    PersistenceError,
    StaleOperationalStateError,
)
from meta_rne.persistence.operational_state_serialization import fabric_to_json
from meta_rne.persistence.serialization import SerializationError
from meta_rne.persistence.sqlalchemy.operational_state_repository import (
    SqlAlchemyOperationalStateRepository,
)
from operational_state_support import fixture_fabric, rich_fixture_fabric

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)

_INSERT = (
    "INSERT INTO operational_state_latest "
    "(lab_name, collection_id, collected_at, schema_version, state) "
    "VALUES (:lab_name, :collection_id, :collected_at, :schema_version, CAST(:state AS jsonb))"
)


def _raw_insert(session: Session, fabric: FabricOperationalState, **overrides: Any) -> None:
    row: dict[str, Any] = {
        "lab_name": "lab1",
        "collection_id": fabric.collection_id,
        "collected_at": fabric.collected_at,
        "schema_version": 1,
        "state": json.dumps(fabric_to_json(fabric)),
    }
    row.update(overrides)
    session.execute(text(_INSERT), row)


# --- stored data is validated on read; nothing is repaired -----------------


def test_row_and_document_agree__reads_back(sqlalchemy_session: Session) -> None:
    fabric = rich_fixture_fabric("baseline", T0)
    _raw_insert(sqlalchemy_session, fabric)

    assert SqlAlchemyOperationalStateRepository(sqlalchemy_session).get_latest("lab1") == fabric


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"collection_id": "someone-else"}, "collection_id column"),
        ({"collected_at": T0 + timedelta(seconds=1)}, "collected_at column"),
        ({"lab_name": "lab2"}, "belongs to lab 'lab1'"),
        ({"schema_version": 2}, "schema_version"),
        ({"state": "{}"}, "missing required key"),
        ({"state": json.dumps({"collection_id": 5})}, "must be a string"),
    ],
    ids=[
        "collection-id",
        "collected-at",
        "lab-name",
        "schema-version",
        "empty-document",
        "wrong-type",
    ],
)
def test_inconsistent_or_corrupt_row__fails_closed_on_read(
    sqlalchemy_session: Session, overrides: dict[str, Any], match: str
) -> None:
    _raw_insert(sqlalchemy_session, rich_fixture_fabric("baseline", T0), **overrides)
    repo = SqlAlchemyOperationalStateRepository(sqlalchemy_session)

    with pytest.raises(SerializationError, match=match):
        repo.get_latest(overrides.get("lab_name", "lab1"))


def test_document_with_mixed_collectors__fails_closed_on_read(
    sqlalchemy_session: Session,
) -> None:
    fabric = rich_fixture_fabric("baseline", T0)
    document = fabric_to_json(fabric)
    document["nodes"][0]["source"]["collector"] = "someone-else"
    _raw_insert(sqlalchemy_session, fabric, state=json.dumps(document))

    with pytest.raises(SerializationError, match="different collectors"):
        SqlAlchemyOperationalStateRepository(sqlalchemy_session).get_latest("lab1")


def test_document_with_no_nodes__fails_closed_on_read(sqlalchemy_session: Session) -> None:
    fabric = rich_fixture_fabric("baseline", T0)
    document = fabric_to_json(fabric)
    document["nodes"] = []
    _raw_insert(sqlalchemy_session, fabric, state=json.dumps(document))

    with pytest.raises(SerializationError, match="no single source"):
        SqlAlchemyOperationalStateRepository(sqlalchemy_session).get_latest("lab1")


def test_save_over_a_corrupt_stored_row__fails_closed_and_leaves_it_alone(
    sqlalchemy_session: Session,
) -> None:
    _raw_insert(
        sqlalchemy_session,
        rich_fixture_fabric("baseline", T0, "run-1"),
        collection_id="tampered",
    )
    repo = SqlAlchemyOperationalStateRepository(sqlalchemy_session)

    # An older observation takes the no-write path and must read the corrupt row.
    with pytest.raises(SerializationError):
        repo.save_latest(rich_fixture_fabric("baseline", T0 - timedelta(seconds=1), "run-0"))

    stored = sqlalchemy_session.execute(
        text("SELECT collection_id FROM operational_state_latest WHERE lab_name = 'lab1'")
    ).scalar_one()
    assert stored == "tampered"


def test_stored_row_columns__mirror_the_document(sqlalchemy_session: Session) -> None:
    fabric = rich_fixture_fabric("degraded-active", T0, "run-9")
    SqlAlchemyOperationalStateRepository(sqlalchemy_session).save_latest(fabric)

    row = sqlalchemy_session.execute(
        text(
            "SELECT lab_name, collection_id, collected_at, schema_version, "
            "jsonb_typeof(state) AS kind, state->>'collection_id' AS doc_id "
            "FROM operational_state_latest WHERE lab_name = 'lab1'"
        )
    ).one()
    assert (row.lab_name, row.collection_id, row.schema_version) == ("lab1", "run-9", 1)
    assert row.collected_at == T0
    assert (row.kind, row.doc_id) == ("object", "run-9")


# --- concurrency -----------------------------------------------------------


class _Race:
    """Runs one writer per fabric, each on its own connection/Session, all
    released together by a Barrier, each committing its own transaction."""

    def __init__(self, database_url: str) -> None:
        self._engine = create_engine(database_url)

    def run(self, fabrics: list[FabricOperationalState]) -> list[object]:
        barrier = threading.Barrier(len(fabrics))
        outcomes: list[object] = [None] * len(fabrics)

        def worker(index: int) -> None:
            session = Session(bind=self._engine)
            try:
                repo = SqlAlchemyOperationalStateRepository(session)
                barrier.wait(timeout=10)
                try:
                    outcomes[index] = repo.save_latest(fabrics[index])
                    session.commit()
                except Exception as error:  # noqa: BLE001 - recorded for assertions
                    session.rollback()
                    outcomes[index] = error
            finally:
                session.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(fabrics))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            assert not thread.is_alive(), "writer thread hung"
        return outcomes

    def latest(self, lab_name: str) -> FabricOperationalState | None:
        with Session(bind=self._engine) as session:
            return SqlAlchemyOperationalStateRepository(session).get_latest(lab_name)

    def cleanup(self, lab_name: str) -> None:
        with self._engine.begin() as connection:
            connection.execute(
                text("DELETE FROM operational_state_latest WHERE lab_name = :lab"),
                {"lab": lab_name},
            )
        self._engine.dispose()


@pytest.fixture()
def race(
    postgres_test_database_url: str, _meta_rne_test_migrated: None
) -> Callable[[], tuple[_Race, str]]:
    created: list[tuple[_Race, str]] = []

    def make() -> tuple[_Race, str]:
        harness = _Race(postgres_test_database_url)
        lab = f"race-{uuid.uuid4().hex[:8]}"
        created.append((harness, lab))
        return harness, lab

    yield make  # type: ignore[misc]
    for harness, lab in created:
        harness.cleanup(lab)


def test_concurrent_competing_collections__latest_never_regresses(
    race: Callable[[], tuple[_Race, str]],
) -> None:
    harness, lab = race()
    # One writer per timestamp, ids deliberately unordered relative to time.
    fabrics = [
        fixture_fabric(
            "baseline", T0 + timedelta(seconds=i), f"id-{(i * 7) % 10:02d}-{i}", lab_name=lab
        )
        for i in range(10)
    ]

    outcomes = harness.run(fabrics)

    newest = fabrics[-1]
    assert harness.latest(lab) == newest
    for outcome in outcomes:
        assert isinstance(
            outcome,
            OperationalStateSaveOutcome | StaleOperationalStateError,
        ), outcome
    # The newest writer can never be rejected as stale, and always lands.
    assert outcomes[-1] in (
        OperationalStateSaveOutcome.CREATED,
        OperationalStateSaveOutcome.REPLACED,
    )
    assert OperationalStateSaveOutcome.UNCHANGED not in outcomes


def test_repeated_races__latest_is_always_the_newest(
    race: Callable[[], tuple[_Race, str]],
) -> None:
    for round_index in range(5):
        harness, lab = race()
        order = list(range(8))
        # Rotate so the newest writer sits at a different thread index each round.
        order = order[round_index:] + order[:round_index]
        fabrics = [
            fixture_fabric("baseline", T0 + timedelta(seconds=n), f"c-{n}", lab_name=lab)
            for n in order
        ]

        harness.run(fabrics)

        latest = harness.latest(lab)
        assert latest is not None
        assert latest.collected_at == T0 + timedelta(seconds=7)
        assert latest.collection_id == "c-7"


def test_concurrent_exact_replays__one_creates_and_the_rest_are_unchanged(
    race: Callable[[], tuple[_Race, str]],
) -> None:
    harness, lab = race()
    fabrics = [fixture_fabric("baseline", T0, "run-1", lab_name=lab) for _ in range(6)]

    outcomes = harness.run(fabrics)

    assert outcomes.count(OperationalStateSaveOutcome.CREATED) == 1
    assert outcomes.count(OperationalStateSaveOutcome.UNCHANGED) == 5
    assert harness.latest(lab) == fabrics[0]


def test_concurrent_same_timestamp_distinct_ids__exactly_one_wins(
    race: Callable[[], tuple[_Race, str]],
) -> None:
    harness, lab = race()
    fabrics = [fixture_fabric("baseline", T0, f"run-{i}", lab_name=lab) for i in range(6)]

    outcomes = harness.run(fabrics)

    winners = [i for i, o in enumerate(outcomes) if o is OperationalStateSaveOutcome.CREATED]
    assert len(winners) == 1
    assert all(
        isinstance(o, OperationalStateConflictError)
        for i, o in enumerate(outcomes)
        if i not in winners
    ), outcomes
    assert harness.latest(lab) == fabrics[winners[0]]


def test_concurrent_same_id_different_payload__exactly_one_wins(
    race: Callable[[], tuple[_Race, str]],
) -> None:
    harness, lab = race()
    healthy = fixture_fabric("baseline", T0, "run-1", lab_name=lab)
    degraded = fixture_fabric("degraded-active", T0, "run-1", lab_name=lab)

    outcomes = harness.run([healthy, degraded, healthy, degraded])

    created = [o for o in outcomes if o is OperationalStateSaveOutcome.CREATED]
    assert len(created) == 1
    stored = harness.latest(lab)
    assert stored in (healthy, degraded)
    for fabric, outcome in zip([healthy, degraded, healthy, degraded], outcomes, strict=True):
        if outcome is OperationalStateSaveOutcome.CREATED:
            continue
        if fabric == stored:
            assert outcome is OperationalStateSaveOutcome.UNCHANGED
        else:
            assert isinstance(outcome, OperationalStateConflictError)
    assert not any(
        isinstance(o, PersistenceError) and type(o) is PersistenceError for o in outcomes
    )
