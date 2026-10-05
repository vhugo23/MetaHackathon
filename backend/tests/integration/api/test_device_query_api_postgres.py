"""Focused PostgreSQL API integration tests for ``GET /devices`` (Day 12A1)
and ``GET /devices/{device_id}`` (Day 12B).

Real ``SqlAlchemyUnitOfWork``, real PostgreSQL device/snapshot repositories,
driven through the actual FastAPI app via ``TestClient`` — not a re-run of
the in-memory contract suite (``tests/contract/api/test_devices_api.py``).
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import NormalizedConfiguration, NormalizedRouting, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.snapshot import ConfigurationSnapshot, compute_raw_text_hash
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)


def _app(session_factory: Callable[[], Session]) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        clock=lambda: T0,
        seed_on_startup=False,
    )
    return TestClient(app)


def _seed_device(
    session_factory: Callable[[], Session],
    device_id: str,
    created_at: datetime,
) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.devices.save(
        Device(
            device_id=device_id,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=created_at,
            updated_at=created_at,
        )
    )
    uow.commit()
    uow.close()


def test_devices_api_postgres__empty_database__returns_empty_list(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client = _app(sqlalchemy_session_factory)

    response = client.get("/devices")

    assert response.status_code == 200
    assert response.json() == []


def test_devices_api_postgres__returns_registered_device_via_real_http(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory, "spine-01", T0)
    client = _app(sqlalchemy_session_factory)

    response = client.get("/devices")

    assert response.status_code == 200
    devices = response.json()
    assert len(devices) == 1
    assert devices[0]["device_id"] == "spine-01"
    assert devices[0]["vendor"] == "cisco-ios-xe"


def test_devices_api_postgres__multiple_devices__ordered_by_created_at_then_device_id(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory, "zulu", T0)
    _seed_device(sqlalchemy_session_factory, "alpha", T0)
    _seed_device(sqlalchemy_session_factory, "leaf-01", T1)
    client = _app(sqlalchemy_session_factory)

    response = client.get("/devices")

    assert response.status_code == 200
    devices = response.json()
    assert [d["device_id"] for d in devices] == ["alpha", "zulu", "leaf-01"]


# --- Day 12B: GET /devices/{device_id} ---------------------------------------


def _seed_device_with_current_snapshot(
    session_factory: Callable[[], Session],
    device_id: str,
    hostname: str,
) -> None:
    raw = f"raw-config-{device_id}"
    snapshot = ConfigurationSnapshot(
        snapshot_id=f"snap-{device_id}",
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        raw_config_text=raw,
        raw_text_hash=compute_raw_text_hash(raw),
        normalized_config=NormalizedConfiguration(
            hostname=hostname,
            interfaces=(),
            routing=NormalizedRouting(bgp_neighbors=()),
            acls=(),
        ),
        submitted_at=T0,
    )
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.devices.save(
        Device(
            device_id=device_id,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    uow.configuration_snapshots.add(snapshot)
    uow.devices.save(
        Device(
            device_id=device_id,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=snapshot.snapshot_id,
            baseline_snapshot_id=snapshot.snapshot_id,
            created_at=T0,
            updated_at=T0,
        )
    )
    uow.commit()
    uow.close()


def test_device_detail_api_postgres__missing_device__returns_404(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client = _app(sqlalchemy_session_factory)

    response = client.get("/devices/missing-device")

    assert response.status_code == 404
    assert response.json() == {
        "code": "device_not_found",
        "detail": "device not found: 'missing-device'",
    }


def test_device_detail_api_postgres__existing_device__returns_current_config_via_real_http(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device_with_current_snapshot(sqlalchemy_session_factory, "spine-01", "spine-01")
    client = _app(sqlalchemy_session_factory)

    response = client.get("/devices/spine-01")

    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == "spine-01"
    assert body["current_snapshot_id"] == "snap-spine-01"
    assert body["normalized_config"]["hostname"] == "spine-01"
