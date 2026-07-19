"""PostgreSQL-only proof that malformed stored JSON surfaces as
``SerializationError`` — never a leaked ``KeyError``/``TypeError``/
``ValueError``/``AttributeError``, never a guessed default, never an ORM
model — and that the Session remains fully usable afterward (Day 4B2
correctness patch).

These deliberately insert malformed JSONB via raw SQL, bypassing the
repository's own (always-valid) serialization functions, to prove what
happens when a stored row doesn't match what the repository expects —
e.g. after a hand-edited row or a future schema drift.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import compute_raw_text_hash
from meta_rne.persistence.serialization import SerializationError
from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
from meta_rne.persistence.sqlalchemy.policy_repository import (
    SqlAlchemyConfigurationPolicyRepository,
)
from meta_rne.persistence.sqlalchemy.snapshot_repository import (
    SqlAlchemyConfigurationSnapshotRepository,
)

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def test_snapshot_repository_sqlalchemy__malformed_normalized_config__raises_serialization_error(
    sqlalchemy_session: Session,
) -> None:
    sqlalchemy_session.execute(
        text(
            "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
            "VALUES ('spine-01', 'cisco-ios-xe', :t, :t)"
        ),
        {"t": T0},
    )
    sqlalchemy_session.execute(
        text(
            "INSERT INTO configuration_snapshots "
            "(snapshot_id, device_id, vendor, raw_config_text, raw_text_hash, "
            " normalized_config, submitted_at) "
            "VALUES ('snap-malformed', 'spine-01', 'cisco-ios-xe', 'hostname x', "
            " :hash, :normalized_config, :t)"
        ),
        {
            "hash": compute_raw_text_hash("hostname x"),
            # structurally malformed: missing every required key
            "normalized_config": '{"not_a_real_field": true}',
            "t": T0,
        },
    )
    sqlalchemy_session.flush()

    repo = SqlAlchemyConfigurationSnapshotRepository(sqlalchemy_session)

    with pytest.raises(SerializationError):
        repo.get_by_id("snap-malformed")

    # The Session must remain fully usable after the raised SerializationError.
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    fetched_device = devices.get_by_id("spine-01")
    assert fetched_device is not None
    assert isinstance(fetched_device, Device)


def test_policy_repository_sqlalchemy__malformed_required_acls__raises_serialization_error(
    sqlalchemy_session: Session,
) -> None:
    sqlalchemy_session.execute(
        text(
            "INSERT INTO configuration_policies "
            "(policy_id, applies_to, required_acls, created_at) "
            "VALUES ('policy-malformed', 'spine-01', :required_acls, :t)"
        ),
        {
            # structurally malformed: not even a list
            "required_acls": '{"not": "a list"}',
            "t": T0,
        },
    )
    sqlalchemy_session.flush()

    repo = SqlAlchemyConfigurationPolicyRepository(sqlalchemy_session)

    with pytest.raises(SerializationError):
        repo.get_applicable_to_device("spine-01")

    # The Session must remain fully usable after the raised SerializationError.
    sqlalchemy_session.execute(
        text(
            "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
            "VALUES ('leaf-01', 'cisco-ios-xe', :t, :t)"
        ),
        {"t": T0},
    )
    sqlalchemy_session.flush()
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    fetched_device = devices.get_by_id("leaf-01")
    assert fetched_device is not None
