"""PostgreSQL-only CHECK constraint tests (Day 4B2).

These deliberately bypass the repository layer (raw SQL) to prove the
database constraints from the Day 4B1 migration are real, not merely an
assumption the ORM/domain layer happens to make. Repository-level
validation already prevents these values from ever being attempted through
normal use — this is a white-box proof that the schema itself would still
reject them.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.postgres


def test_devices_table__vendor_check_constraint__rejects_invalid_value_at_db_level(
    sqlalchemy_session: Session,
) -> None:
    with pytest.raises(IntegrityError):
        sqlalchemy_session.execute(
            text(
                "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
                "VALUES ('bad-device', 'not-a-real-vendor', now(), now())"
            )
        )
        sqlalchemy_session.flush()


def test_configuration_snapshots_table__hash_format_check_constraint__rejects_invalid_value(
    sqlalchemy_session: Session,
) -> None:
    sqlalchemy_session.execute(
        text(
            "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
            "VALUES ('spine-01', 'cisco-ios-xe', now(), now())"
        )
    )
    sqlalchemy_session.flush()

    with pytest.raises(IntegrityError):
        sqlalchemy_session.execute(
            text(
                "INSERT INTO configuration_snapshots "
                "(snapshot_id, device_id, vendor, raw_config_text, raw_text_hash, "
                " normalized_config, submitted_at) "
                "VALUES ('snap-1', 'spine-01', 'cisco-ios-xe', 'hostname x', "
                " 'NOT-A-VALID-HASH', '{}'::jsonb, now())"
            )
        )
        sqlalchemy_session.flush()
