"""Migration 0005 (NPE-1D1A) against a real, disposable PostgreSQL database:
``operational_state_latest`` has exactly the intended shape, and downgrade
removes only that table."""

from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.postgres

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_PREVIOUS_REVISION = "0004_frr_device_vendor"
_TABLE = "operational_state_latest"

_INSERT = (
    "INSERT INTO operational_state_latest "
    "(lab_name, collection_id, collected_at, schema_version, state) "
    "VALUES (:lab_name, :collection_id, now(), :schema_version, CAST(:state AS jsonb))"
)


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "lab_name": "lab1",
        "collection_id": "run-1",
        "schema_version": 1,
        "state": "{}",
    }
    row.update(overrides)
    return row


def _insert(engine: Engine, **overrides: Any) -> None:
    with engine.begin() as connection:
        connection.execute(text(_INSERT), _row(**overrides))


def _schema_snapshot(engine: Engine, exclude: set[str] | None = None) -> dict[str, Any]:
    """Columns, PK, FKs, indexes and CHECK constraints of every table."""
    inspector = inspect(engine)
    snapshot: dict[str, Any] = {}
    for table in sorted(set(inspector.get_table_names()) - (exclude or set())):
        snapshot[table] = {
            "columns": [
                (c["name"], str(c["type"]), c["nullable"]) for c in inspector.get_columns(table)
            ],
            "pk": inspector.get_pk_constraint(table),
            "fks": sorted(
                (fk["name"] or "", fk["referred_table"]) for fk in inspector.get_foreign_keys(table)
            ),
            "indexes": sorted(
                (i["name"] or "", tuple(i["column_names"]), bool(i["unique"]))
                for i in inspector.get_indexes(table)
            ),
            "checks": sorted(
                (c["name"] or "", c["sqltext"]) for c in inspector.get_check_constraints(table)
            ),
        }
    return snapshot


def test_upgrade__creates_exactly_the_intended_columns(reset_migration_database: str) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")
    engine = create_engine(reset_migration_database)
    try:
        inspector = inspect(engine)
        assert _TABLE in inspector.get_table_names()
        columns = {c["name"]: c for c in inspector.get_columns(_TABLE)}
        assert list(columns) == [
            "lab_name",
            "collection_id",
            "collected_at",
            "schema_version",
            "state",
        ]
        assert {name: str(c["type"]) for name, c in columns.items()} == {
            "lab_name": "TEXT",
            "collection_id": "TEXT",
            "collected_at": "TIMESTAMP",
            "schema_version": "INTEGER",
            "state": "JSONB",
        }
        assert columns["collected_at"]["type"].timezone is True
        assert all(c["nullable"] is False for c in columns.values())
    finally:
        engine.dispose()


def test_upgrade__lab_name_is_the_only_primary_key_column_with_no_other_structure(
    reset_migration_database: str,
) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")
    engine = create_engine(reset_migration_database)
    try:
        inspector = inspect(engine)
        assert inspector.get_pk_constraint(_TABLE)["constrained_columns"] == ["lab_name"]
        assert inspector.get_foreign_keys(_TABLE) == []
        assert inspector.get_unique_constraints(_TABLE) == []
        # Only the primary key's implicit index; no extra index.
        assert [i for i in inspector.get_indexes(_TABLE) if not i["unique"]] == []
        assert {c["name"] for c in inspector.get_check_constraints(_TABLE)} == {
            "ck_operational_state_latest_lab_name",
            "ck_operational_state_latest_collection_id",
            "ck_operational_state_latest_schema_version",
            "ck_operational_state_latest_state_object",
        }
        with engine.connect() as connection:
            referencing = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.constraint_column_usage "
                    "WHERE table_name = :t AND constraint_name IN "
                    "(SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE constraint_type = 'FOREIGN KEY')"
                ),
                {"t": _TABLE},
            ).scalar_one()
        assert referencing == 0, "no table references operational_state_latest"
    finally:
        engine.dispose()


def test_upgrade__state_and_collected_at_have_the_intended_postgres_types(
    reset_migration_database: str,
) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")
    engine = create_engine(reset_migration_database)
    try:
        with engine.connect() as connection:
            types = dict(
                connection.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_name = :t"
                    ),
                    {"t": _TABLE},
                ).all()
            )
        assert types["state"] == "jsonb"
        assert types["collected_at"] == "timestamp with time zone"
        assert types["schema_version"] == "integer"
    finally:
        engine.dispose()


def test_constraints__accept_a_valid_row_and_reject_duplicate_lab(
    reset_migration_database: str,
) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")
    engine = create_engine(reset_migration_database)
    try:
        _insert(engine)
        with pytest.raises(IntegrityError, match="operational_state_latest_pkey"):
            _insert(engine, collection_id="run-2")
        _insert(engine, lab_name="lab2")
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        ({"lab_name": ""}, "ck_operational_state_latest_lab_name"),
        ({"lab_name": "   "}, "ck_operational_state_latest_lab_name"),
        ({"collection_id": ""}, "ck_operational_state_latest_collection_id"),
        ({"collection_id": "  "}, "ck_operational_state_latest_collection_id"),
        ({"schema_version": 0}, "ck_operational_state_latest_schema_version"),
        ({"schema_version": -1}, "ck_operational_state_latest_schema_version"),
        ({"state": "[]"}, "ck_operational_state_latest_state_object"),
        ({"state": '"text"'}, "ck_operational_state_latest_state_object"),
        ({"state": "3"}, "ck_operational_state_latest_state_object"),
        ({"state": "null"}, "ck_operational_state_latest_state_object"),
    ],
)
def test_constraints__reject_invalid_rows(
    reset_migration_database: str, overrides: dict[str, Any], constraint: str
) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")
    engine = create_engine(reset_migration_database)
    try:
        with pytest.raises(IntegrityError, match=constraint):
            _insert(engine, **overrides)
    finally:
        engine.dispose()


def test_constraints__reject_null_columns(reset_migration_database: str) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")
    engine = create_engine(reset_migration_database)
    try:
        for column in ("collection_id", "schema_version"):
            with pytest.raises(IntegrityError, match="not-null|null value"):
                _insert(engine, **{column: None})
        with pytest.raises(IntegrityError, match="not-null|null value"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO operational_state_latest "
                        "(lab_name, collection_id, collected_at, schema_version, state) "
                        "VALUES ('lab1', 'run-1', NULL, 1, '{}')"
                    )
                )
    finally:
        engine.dispose()


def test_upgrade__does_not_alter_any_existing_table(reset_migration_database: str) -> None:
    config = _alembic_config(reset_migration_database)
    command.upgrade(config, _PREVIOUS_REVISION)
    engine = create_engine(reset_migration_database)
    try:
        before = _schema_snapshot(engine)

        command.upgrade(config, "head")

        after = _schema_snapshot(engine, exclude={_TABLE})
        assert after == before
    finally:
        engine.dispose()


def test_downgrade__removes_only_this_table(reset_migration_database: str) -> None:
    config = _alembic_config(reset_migration_database)
    command.upgrade(config, "head")
    engine = create_engine(reset_migration_database)
    try:
        with_table = _schema_snapshot(engine, exclude={_TABLE})
        _insert(engine)

        command.downgrade(config, _PREVIOUS_REVISION)

        names = set(inspect(engine).get_table_names())
        assert _TABLE not in names
        assert {
            "devices",
            "configuration_snapshots",
            "configuration_policies",
            "incidents",
            "telemetry_samples",
        } <= names
        assert _schema_snapshot(engine) == with_table
    finally:
        engine.dispose()


def test_upgrade_again_after_downgrade__succeeds_with_an_empty_table(
    reset_migration_database: str,
) -> None:
    config = _alembic_config(reset_migration_database)
    command.upgrade(config, "head")
    engine = create_engine(reset_migration_database)
    try:
        _insert(engine)
        command.downgrade(config, _PREVIOUS_REVISION)

        command.upgrade(config, "head")

        assert _TABLE in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM operational_state_latest")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
