"""Alembic migration tests against a real, disposable PostgreSQL database
(meta_rne_migration_test) — Day 4B1.

Every test is independent: the function-scoped ``reset_migration_database``
fixture drops and recreates the ``public`` schema before each test runs, so
no test may rely on pytest's execution order or on a previous test having
already upgraded/downgraded the database (Day 4B1 binding decision). Each
test performs, from Alembic base, exactly the upgrade/downgrade operations
it needs.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

pytestmark = pytest.mark.postgres

_BACKEND_ROOT = Path(__file__).resolve().parents[3]

_EXPECTED_TABLES = {
    "devices",
    "configuration_snapshots",
    "configuration_policies",
    "incidents",
}


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def test_alembic_upgrade_head__succeeds_on_fresh_database(reset_migration_database: str) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")


def test_alembic_upgrade_head__creates_expected_tables_and_columns(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())
        assert _EXPECTED_TABLES <= table_names

        device_columns = {col["name"] for col in inspector.get_columns("devices")}
        assert device_columns == {
            "device_id",
            "vendor",
            "current_snapshot_id",
            "baseline_snapshot_id",
            "created_at",
            "updated_at",
        }

        snapshot_columns = {col["name"] for col in inspector.get_columns("configuration_snapshots")}
        assert snapshot_columns == {
            "snapshot_id",
            "device_id",
            "vendor",
            "raw_config_text",
            "raw_text_hash",
            "normalized_config",
            "submitted_at",
        }

        policy_columns = {col["name"] for col in inspector.get_columns("configuration_policies")}
        assert policy_columns == {"policy_id", "applies_to", "required_acls", "created_at"}

        incident_columns = {col["name"] for col in inspector.get_columns("incidents")}
        assert incident_columns == {
            "incident_id",
            "fingerprint",
            "device_id",
            "source",
            "rule_ref",
            "affected_resource",
            "severity",
            "status",
            "evidence",
            "recommendation",
            "created_at",
            "last_seen_at",
            "occurrence_count",
            "updated_at",
            "resolved_at",
        }
    finally:
        engine.dispose()


def test_alembic_upgrade_head__creates_expected_foreign_keys(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)

        device_fks = {fk["name"] for fk in inspector.get_foreign_keys("devices")}
        assert device_fks == {"fk_devices_current_snapshot", "fk_devices_baseline_snapshot"}

        snapshot_fks = inspector.get_foreign_keys("configuration_snapshots")
        assert any(fk["referred_table"] == "devices" for fk in snapshot_fks)

        incident_fks = inspector.get_foreign_keys("incidents")
        assert any(fk["referred_table"] == "devices" for fk in incident_fks)

        policy_fks = inspector.get_foreign_keys("configuration_policies")
        assert policy_fks == []
    finally:
        engine.dispose()


def test_alembic_upgrade_head__creates_check_constraints(reset_migration_database: str) -> None:
    database_url = reset_migration_database
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)

        device_checks = {c["name"] for c in inspector.get_check_constraints("devices")}
        assert {"ck_devices_updated_at_after_created_at", "ck_devices_vendor"} <= device_checks

        snapshot_checks = {
            c["name"] for c in inspector.get_check_constraints("configuration_snapshots")
        }
        assert {
            "ck_configuration_snapshots_hash_format",
            "ck_configuration_snapshots_vendor",
        } <= snapshot_checks

        incident_checks = {c["name"] for c in inspector.get_check_constraints("incidents")}
        assert {
            "ck_incidents_source",
            "ck_incidents_severity",
            "ck_incidents_status",
            "ck_incidents_occurrence_count_min",
            "ck_incidents_fingerprint_format",
            "ck_incidents_last_seen_after_created",
            "ck_incidents_updated_at_after_last_seen_at",
            "ck_incidents_resolved_at_matches_status",
            "ck_incidents_resolved_at_before_or_equal_updated_at",
        } <= incident_checks
    finally:
        engine.dispose()


def test_alembic_upgrade_head__updated_at_is_not_nullable(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("incidents")}
        assert columns["updated_at"]["nullable"] is False
        assert columns["resolved_at"]["nullable"] is True
    finally:
        engine.dispose()


def test_alembic_upgrade_head__backfills_updated_at_from_last_seen_at_for_pre_existing_rows(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, "0001_slice1_persistence")

    engine = create_engine(database_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
                    "VALUES ('spine-01', 'cisco-ios-xe', '2026-07-18T10:00:00Z', "
                    "'2026-07-18T10:00:00Z')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO incidents "
                    "(incident_id, fingerprint, device_id, source, rule_ref, "
                    " affected_resource, severity, status, evidence, recommendation, "
                    " created_at, last_seen_at, occurrence_count) "
                    "VALUES ('pre-existing-incident', "
                    "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
                    "'spine-01', 'POLICY_VIOLATION', 'policy-acl-external-in', "
                    "'interface:GigabitEthernet0/1:acl_in', 'Medium', 'OPEN', "
                    '\'{"source_snapshot_id": "snap-1", "violation_type": '
                    '"MISSING_REQUIRED_ACL", "expected_acl_name": "ACL-EXTERNAL-IN", '
                    '"actual_acl_name": null, "interface_name": "GigabitEthernet0/1", '
                    '"direction": "in"}\', '
                    "'Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1', "
                    "'2026-07-18T10:00:00Z', '2026-07-18T11:00:00Z', 1)"
                )
            )

        command.upgrade(config, "head")

        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT last_seen_at, updated_at, resolved_at, status FROM incidents "
                    "WHERE incident_id = 'pre-existing-incident'"
                )
            ).one()
        assert row.updated_at == row.last_seen_at
        assert row.resolved_at is None
        assert row.status == "OPEN"
    finally:
        engine.dispose()


def test_alembic_upgrade_head__partial_open_fingerprint_index_survives_unchanged(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            index_def = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
                {"name": "ux_incidents_open_fingerprint"},
            ).scalar_one()
        assert "UNIQUE" in index_def
        assert "(fingerprint)" in index_def
        assert "WHERE" in index_def and "status = 'OPEN'" in index_def
    finally:
        engine.dispose()


def test_alembic_downgrade_one_revision__drops_resolution_columns_and_constraints(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "0001_slice1_persistence")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        incident_columns = {col["name"] for col in inspector.get_columns("incidents")}
        assert "updated_at" not in incident_columns
        assert "resolved_at" not in incident_columns
    finally:
        engine.dispose()


def test_alembic_upgrade_head__succeeds_again_after_downgrading_one_revision(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "0001_slice1_persistence")

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        incident_columns = {col["name"] for col in inspector.get_columns("incidents")}
        assert {"updated_at", "resolved_at"} <= incident_columns
    finally:
        engine.dispose()


def test_alembic_upgrade_head__creates_partial_unique_index_on_open_fingerprint(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            index_def = conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :name"),
                {"name": "ux_incidents_open_fingerprint"},
            ).scalar_one()
        assert "UNIQUE" in index_def
        assert "(fingerprint)" in index_def
        assert "WHERE" in index_def and "status = 'OPEN'" in index_def
    finally:
        engine.dispose()


def test_alembic_downgrade_base__succeeds(reset_migration_database: str) -> None:
    database_url = reset_migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        remaining = set(inspector.get_table_names()) - {"alembic_version"}
        assert remaining == set()
    finally:
        engine.dispose()


def test_alembic_upgrade_head__succeeds_a_second_time_after_downgrade(
    reset_migration_database: str,
) -> None:
    database_url = reset_migration_database
    config = _alembic_config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        assert _EXPECTED_TABLES <= set(inspector.get_table_names())
    finally:
        engine.dispose()
