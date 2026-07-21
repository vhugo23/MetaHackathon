"""PostgreSQL-only proof that malformed stored evidence JSONB on an
``incidents`` row surfaces as ``SerializationError`` — never a leaked
``KeyError``/``TypeError``/``ValueError``/``AttributeError``, never a guessed
default — and that the Session remains fully usable afterward (Day 4B3,
mirroring the Day 4B2 pattern in test_repository_serialization_errors.py).

Deliberately inserts malformed JSONB via raw SQL, bypassing the repository's
own (always-valid) serialization functions, to prove what happens when a
stored row doesn't match what the repository expects.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.domain.device import Device
from meta_rne.persistence.serialization import SerializationError
from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def test_incident_repository_sqlalchemy__malformed_evidence__raises_serialization_error(
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
            "INSERT INTO incidents "
            "(incident_id, fingerprint, device_id, source, rule_ref, affected_resource, "
            " severity, status, evidence, recommendation, created_at, last_seen_at, "
            " occurrence_count, updated_at) "
            "VALUES ('incident-malformed', "
            " 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', "
            " 'spine-01', 'POLICY_VIOLATION', 'policy-acl-external-in', "
            " 'interface:GigabitEthernet0/1:acl_in', 'Medium', 'OPEN', "
            " :evidence, 'Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1', "
            " :t, :t, 1, :t)"
        ),
        {
            # structurally malformed: missing every required key
            "evidence": '{"not_a_real_field": true}',
            "t": T0,
        },
    )
    sqlalchemy_session.flush()

    repo = SqlAlchemyIncidentRepository(sqlalchemy_session)

    with pytest.raises(SerializationError):
        repo.get_by_id("incident-malformed")

    with pytest.raises(SerializationError):
        repo.list_all()

    # The Session must remain fully usable after the raised SerializationError.
    devices = SqlAlchemyDeviceRepository(sqlalchemy_session)
    fetched_device = devices.get_by_id("spine-01")
    assert fetched_device is not None
    assert isinstance(fetched_device, Device)
