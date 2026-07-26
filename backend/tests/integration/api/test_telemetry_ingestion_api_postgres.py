"""Focused PostgreSQL API integration tests for
``POST /devices/{device_id}/telemetry`` (Gate G1).

Real ``SqlAlchemyUnitOfWork``, real PostgreSQL telemetry repository, driven
through the actual FastAPI app via ``TestClient`` — not a re-run of the
in-memory contract suite (``tests/contract/api/test_telemetry_ingestion_api.py``)
or the Day 5A-style application-level Postgres suite
(``tests/integration/application/test_telemetry_ingestion_service_postgres.py``).
These prove what only a real database transaction, reached via real HTTP,
can prove.

Requests submit raw JSON payloads (dicts) rather than importing the
not-yet-existing response schema classes, so the expected-red result is an
HTTP routing failure (404, unregistered path), never an import error.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _app(
    session_factory: Callable[[], Session],
    *,
    clock: Callable[[], datetime] = lambda: T0,
) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        clock=clock,
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


def _payload(**overrides: object) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "sampled_at": T0.isoformat(),
        "cpu_utilization_pct": 50.0,
        "memory_utilization_pct": 50.0,
        "interface_error_rate": 0.0,
        "interface_states": [],
        "bgp_sessions": [],
    }
    defaults.update(overrides)
    return defaults


def test_telemetry_api_postgres__zero_anomaly_ingestion__returns_201_and_persists(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["sample"]["device_id"] == DEVICE_ID
    assert body["anomalies"] == []


def test_telemetry_api_postgres__later_unit_of_work_sees_committed_sample(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    saved = verify_uow.telemetry_samples.get_latest(DEVICE_ID)
    assert saved is not None
    assert saved.device_id == DEVICE_ID
    verify_uow.close()


def test_telemetry_api_postgres__cpu_high__triggers_through_full_http_to_postgres_path(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(sampled_at=T0.replace(second=30).isoformat(), cpu_utilization_pct=95.0),
    )

    rule_ids = [a["rule_id"] for a in response.json()["anomalies"]]
    assert "RULE-CPU-HIGH" in rule_ids


def test_telemetry_api_postgres__link_flap__triggers_through_full_http_to_postgres_path(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)
    interface = "GigabitEthernet0/1"

    for offset, state in enumerate(["up", "down", "up", "down"]):
        client.post(
            f"/devices/{DEVICE_ID}/telemetry",
            json=_payload(
                sampled_at=T0.replace(second=12 * offset).isoformat(),
                interface_states=[{"name": interface, "oper_state": state}],
            ),
        )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=48).isoformat(),
            interface_states=[{"name": interface, "oper_state": "up"}],
        ),
    )

    rule_ids = [a["rule_id"] for a in response.json()["anomalies"]]
    assert "RULE-LINK-FLAP" in rule_ids


def test_telemetry_api_postgres__bgp_down__triggers_through_full_http_to_postgres_path(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)
    neighbor = "10.0.0.2"

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.isoformat(),
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Established"}],
        ),
    )
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_payload(
            sampled_at=T0.replace(second=30).isoformat(),
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Idle"}],
        ),
    )

    rule_ids = [a["rule_id"] for a in response.json()["anomalies"]]
    assert "RULE-BGP-DOWN" in rule_ids


def test_telemetry_api_postgres__missing_device__returns_404_and_persists_nothing(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client = _app(sqlalchemy_session_factory)

    response = client.post("/devices/missing-device/telemetry", json=_payload())

    assert response.status_code == 404
    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.telemetry_samples.get_latest("missing-device") is None
    verify_uow.close()


def test_telemetry_api_postgres__exact_duplicate_requests__both_rows_remain_queryable(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)
    payload = _payload()

    first = client.post(f"/devices/{DEVICE_ID}/telemetry", json=payload)
    second = client.post(f"/devices/{DEVICE_ID}/telemetry", json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    recent = verify_uow.telemetry_samples.get_recent(DEVICE_ID, since=T0)
    assert len(recent) == 2
    verify_uow.close()


def test_telemetry_api_postgres__response_contains_no_incident_shaped_fields(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_payload())

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
