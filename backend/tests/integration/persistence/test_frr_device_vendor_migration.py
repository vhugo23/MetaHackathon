"""Migration 0004 (NPE-1C3B1) against a real, disposable PostgreSQL database:
``devices.vendor`` admits ``'frr'``, ``configuration_snapshots.vendor`` does
not, and downgrade restores the previous devices constraint."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.postgres

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_PREVIOUS_REVISION = "0003_telemetry_samples"

_INSERT_DEVICE = (
    "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
    "VALUES (:device_id, :vendor, now(), now())"
)


def _alembic_config(database_url: str) -> Config:
    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _insert_device(engine: Engine, device_id: str, vendor: str) -> None:
    with engine.begin() as connection:
        connection.execute(text(_INSERT_DEVICE), {"device_id": device_id, "vendor": vendor})


def test_upgrade_head__devices_vendor_check_admits_frr(reset_migration_database: str) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")

    engine = create_engine(reset_migration_database)
    try:
        _insert_device(engine, "lab1-leaf-1", "frr")
        _insert_device(engine, "spine-01", "cisco-ios-xe")
        _insert_device(engine, "leaf-02", "arista-eos")
        with pytest.raises(IntegrityError, match="ck_devices_vendor"):
            _insert_device(engine, "bad", "juniper-junos")
    finally:
        engine.dispose()


def test_upgrade_head__snapshots_vendor_check_still_rejects_frr(
    reset_migration_database: str,
) -> None:
    command.upgrade(_alembic_config(reset_migration_database), "head")

    engine = create_engine(reset_migration_database)
    try:
        _insert_device(engine, "lab1-leaf-1", "frr")
        with pytest.raises(IntegrityError, match="ck_configuration_snapshots_vendor"):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO configuration_snapshots "
                        "(snapshot_id, device_id, vendor, raw_config_text, raw_text_hash, "
                        " normalized_config, submitted_at) "
                        "VALUES ('snap-1', 'lab1-leaf-1', 'frr', 'hostname x', "
                        f" '{'0' * 64}', '{{}}'::jsonb, now())"
                    )
                )
    finally:
        engine.dispose()


def test_downgrade_one_revision__restores_previous_devices_constraint(
    reset_migration_database: str,
) -> None:
    config = _alembic_config(reset_migration_database)
    command.upgrade(config, "head")

    command.downgrade(config, _PREVIOUS_REVISION)

    engine = create_engine(reset_migration_database)
    try:
        with pytest.raises(IntegrityError, match="ck_devices_vendor"):
            _insert_device(engine, "lab1-leaf-1", "frr")
        _insert_device(engine, "spine-01", "cisco-ios-xe")
        _insert_device(engine, "leaf-02", "arista-eos")
    finally:
        engine.dispose()


def test_downgrade__refuses_while_frr_devices_exist_and_changes_nothing(
    reset_migration_database: str,
) -> None:
    config = _alembic_config(reset_migration_database)
    command.upgrade(config, "head")
    engine = create_engine(reset_migration_database)
    try:
        _insert_device(engine, "lab1-leaf-1", "frr")

        with pytest.raises(RuntimeError, match="frr"):
            command.downgrade(config, _PREVIOUS_REVISION)

        with engine.connect() as connection:
            vendor = connection.execute(
                text("SELECT vendor FROM devices WHERE device_id = 'lab1-leaf-1'")
            ).scalar_one()
        assert vendor == "frr"
    finally:
        engine.dispose()


def test_upgrade_head__succeeds_again_after_downgrading_frr_vendor(
    reset_migration_database: str,
) -> None:
    config = _alembic_config(reset_migration_database)
    command.upgrade(config, "head")
    command.downgrade(config, _PREVIOUS_REVISION)

    command.upgrade(config, "head")

    engine = create_engine(reset_migration_database)
    try:
        _insert_device(engine, "lab1-leaf-1", "frr")
    finally:
        engine.dispose()
