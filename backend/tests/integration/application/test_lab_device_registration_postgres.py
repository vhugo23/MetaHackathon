"""PostgreSQL transaction and foreign-key behavior for
``RegisterLabDevicesService`` (NPE-1C3B1), using the real
``SqlAlchemyUnitOfWork`` on the migrated test database."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from meta_rne.application.errors import LabDeviceConflictError
from meta_rne.application.lab_device_registration import (
    LAB1_ROUTER_DEVICE_IDS,
    RegisterLabDevicesService,
)
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)


def _service(session_factory: Callable[[], Session]) -> RegisterLabDevicesService:
    return RegisterLabDevicesService(lambda: SqlAlchemyUnitOfWork(session_factory))  # type: ignore[arg-type,return-value]


def _verify(
    session_factory: Callable[[], Session],
) -> list[tuple[str, str, str | None, str | None]]:
    with session_factory() as session:
        rows = session.execute(
            text(
                "SELECT device_id, vendor, current_snapshot_id, baseline_snapshot_id "
                "FROM devices ORDER BY device_id"
            )
        ).all()
        return [tuple(row) for row in rows]  # type: ignore[misc]


def test_register__postgres__persists_four_snapshot_less_frr_routers_and_is_idempotent(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    service = _service(sqlalchemy_session_factory)

    first = service.register(T0)
    second = service.register(T0 + timedelta(hours=1))

    assert first.registered == LAB1_ROUTER_DEVICE_IDS
    assert second.registered == ()
    assert second.already_registered == LAB1_ROUTER_DEVICE_IDS
    assert _verify(sqlalchemy_session_factory) == sorted(
        (device_id, "frr", None, None) for device_id in LAB1_ROUTER_DEVICE_IDS
    )
    with sqlalchemy_session_factory() as session:
        updated = session.execute(text("SELECT DISTINCT updated_at FROM devices")).scalars().all()
    assert updated == [T0]


def test_register__postgres__existing_cisco_device_conflicts_and_nothing_written(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    with sqlalchemy_session_factory() as session:
        session.execute(
            text(
                "INSERT INTO devices (device_id, vendor, created_at, updated_at) "
                "VALUES ('lab1-leaf-2', 'cisco-ios-xe', now(), now())"
            )
        )
        session.commit()

    with pytest.raises(LabDeviceConflictError):
        _service(sqlalchemy_session_factory).register(T0)

    rows = _verify(sqlalchemy_session_factory)
    assert [(row[0], row[1]) for row in rows] == [("lab1-leaf-2", "cisco-ios-xe")]
