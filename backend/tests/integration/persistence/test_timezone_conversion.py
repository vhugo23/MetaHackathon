"""PostgreSQL-only proof that ORM-to-domain timestamp conversion does not
depend on the server/session timezone being UTC (Day 4B2 binding decision).

Sets a non-UTC session timezone explicitly, then proves the domain object
returned by the repository still carries a UTC-aware timestamp
representing the same instant.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def test_device_repository_sqlalchemy__non_utc_session_timezone__returns_utc_timestamp(
    sqlalchemy_session: Session,
) -> None:
    sqlalchemy_session.execute(text("SET TIME ZONE 'America/New_York'"))
    repo = SqlAlchemyDeviceRepository(sqlalchemy_session)
    device = Device(
        device_id="spine-01",
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )

    repo.save(device)
    fetched = repo.get_by_id("spine-01")

    assert fetched is not None
    assert fetched.created_at.utcoffset() == UTC.utcoffset(None)
    assert fetched.created_at == T0
    assert fetched.updated_at == T0
