"""Focused PostgreSQL API integration tests for
``GET /devices/{device_id}/telemetry/recent`` (Gate G2C). Real
``SqlAlchemyUnitOfWork``, real PostgreSQL telemetry repository, driven
through the actual FastAPI app via ``TestClient`` — not a re-run of the
in-memory contract suite or the Day 5A-style application-level Postgres
suite (``tests/integration/application/test_telemetry_query_service_postgres.py``).

Per the approved Gate G2A/G2B/G2C plan, old-row and >100-row cases are
PostgreSQL-specific no-pruning integration behavior — never compared
against an in-memory run of the same scenario.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _app(session_factory: Callable[[], Session]) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        clock=lambda: T0,
        seed_on_startup=False,
    )
    return TestClient(app)


def _seed_device(session_factory: Callable[[], Session], device_id: str = DEVICE_ID) -> None:
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
    uow.commit()
    uow.close()


def _seed_sample(session_factory: Callable[[], Session], sample: TelemetrySample) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.telemetry_samples.save(sample.device_id, sample)
    uow.commit()
    uow.close()


def _sample(device_id: str = DEVICE_ID, sampled_at: datetime = T0) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )


def test_telemetry_query_api_postgres__no_telemetry__returns_200_empty_array(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.status_code == 200
    assert response.json() == []


def test_telemetry_query_api_postgres__persisted_sample__round_trips_through_http(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    sample = _sample()
    _seed_sample(sqlalchemy_session_factory, sample)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    body = response.json()
    assert len(body) == 1
    assert body[0]["device_id"] == DEVICE_ID


def test_telemetry_query_api_postgres__inclusive_since_boundary(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0))
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 1


def test_telemetry_query_api_postgres__row_older_than_five_minutes__returned_when_since_includes_it(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    """PostgreSQL-specific no-pruning integration behavior — not compared
    against an in-memory run."""
    _seed_device(sqlalchemy_session_factory)
    old_sample = _sample(sampled_at=T0)
    _seed_sample(sqlalchemy_session_factory, old_sample)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent",
        params={"since": (T0 - timedelta(minutes=10)).isoformat()},
    )

    assert len(response.json()) == 1


def test_telemetry_query_api_postgres__more_than_100_matching_rows__no_api_level_cap(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    for i in range(150):
        _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0 + timedelta(seconds=i)))
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 150


def test_telemetry_query_api_postgres__equal_timestamps__preserve_identity_order(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    for _ in range(3):
        _seed_sample(sqlalchemy_session_factory, _sample(sampled_at=T0))
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 3


def test_telemetry_query_api_postgres__exact_duplicate_rows__returned_independently(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    duplicate = _sample(sampled_at=T0)
    _seed_sample(sqlalchemy_session_factory, duplicate)
    _seed_sample(sqlalchemy_session_factory, duplicate)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 2


def test_telemetry_query_api_postgres__future_dated_row__returned(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    future_sample = _sample(sampled_at=T0 + timedelta(hours=1))
    _seed_sample(sqlalchemy_session_factory, future_sample)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert len(response.json()) == 1


def test_telemetry_query_api_postgres__missing_device__returns_existing_exact_404_body(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        "/devices/missing-device/telemetry/recent", params={"since": T0.isoformat()}
    )

    assert response.status_code == 404
    assert response.json() == {
        "code": "device_not_found",
        "detail": "device not found: 'missing-device'",
    }


def test_telemetry_query_api_postgres__missing_since__returns_422(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.get(f"/devices/{DEVICE_ID}/telemetry/recent")

    assert response.status_code == 422


def test_telemetry_query_api_postgres__malformed_since__returns_422(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": "not-a-timestamp"}
    )

    assert response.status_code == 422


def test_telemetry_query_api_postgres__naive_since__returns_existing_exact_422_body(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent",
        params={"since": "2026-07-18T10:00:00"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "detail": "since must be timezone-aware UTC",
    }


def test_telemetry_query_api_postgres__non_utc_offset_since__returns_existing_exact_422_body(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent",
        params={"since": "2026-07-18T10:00:00+02:00"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "code": "invalid_request",
        "detail": "since must be timezone-aware UTC",
    }


def test_telemetry_query_api_postgres__no_telemetry_row_is_ever_written(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    client.get(f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()})

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.telemetry_samples.get_recent(DEVICE_ID, since=T0) == []
    verify_uow.close()


def test_telemetry_query_api_postgres__response_contains_no_incident_or_identity_fields(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    _seed_sample(sqlalchemy_session_factory, _sample())
    client = _app(sqlalchemy_session_factory)

    response = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": T0.isoformat()}
    )

    body_text = response.text
    for forbidden in (
        "incident_id",
        "fingerprint",
        "severity",
        "recommendation",
        "occurrence_count",
        "status",
    ):
        assert f'"{forbidden}"' not in body_text
    assert "insertion_sequence" not in body_text
