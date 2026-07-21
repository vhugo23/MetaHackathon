"""Focused PostgreSQL API integration tests (Day 5B plan item 13).

Real ``SqlAlchemyUnitOfWork``, real PostgreSQL repositories, the real
``AdapterRegistry``/``CiscoAdapter``, and real database transaction
behavior, driven through the actual FastAPI app via ``TestClient`` — not a
re-run of the in-memory contract suite (``tests/contract/api/``) or the
Day 5A application-level Postgres suite
(``tests/integration/application/test_config_ingestion_postgres.py``).
These prove what only a real database transaction, reached via real HTTP,
can prove.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import NormalizedConfiguration, NormalizedRouting, VendorType
from meta_rne.persistence.errors import PolicySeedConflictError
from meta_rne.persistence.seeds import build_slice1_policies
from meta_rne.persistence.sqlalchemy.policy_repository import (
    SqlAlchemyConfigurationPolicyRepository,
)
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)

_MISSING_ACL_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"
_NO_HOSTNAME_RAW_CONFIG = "interface GigabitEthernet0/1\n!\n"


class _FakeAristaAdapter:
    vendor_id: str = VendorType.ARISTA_EOS

    def parse(self, raw_text: str) -> NormalizedConfiguration:
        return NormalizedConfiguration(
            hostname="arista-1",
            interfaces=(),
            routing=NormalizedRouting(bgp_neighbors=()),
            acls=(),
        )


def _seed_slice1_policy_directly(session_factory: Callable[[], Session]) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    uow.configuration_policies.seed_if_missing(build_slice1_policies(T0))
    uow.commit()
    uow.close()


def _app(
    session_factory: Callable[[], Session],
    *,
    clock: Callable[[], datetime] = lambda: T0,
    snapshot_id_factory: Callable[[], str] = lambda: "snap-1",
    adapter_registry: AdapterRegistry | None = None,
    seed_on_startup: bool = False,
) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(session_factory),
        clock=clock,
        snapshot_id_factory=snapshot_id_factory,
        adapter_registry=adapter_registry or AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=seed_on_startup,
    )
    return TestClient(app)


# --- Startup seeding ---------------------------------------------------------


def test_startup_postgres__seeds_the_exact_slice1_policy(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    app = create_app(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(sqlalchemy_session_factory),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=True,
    )

    with TestClient(app):
        pass

    expected = build_slice1_policies(T0)[0]
    repo = SqlAlchemyConfigurationPolicyRepository(sqlalchemy_session_factory())
    policies = repo.get_applicable_to_device(expected.applies_to)
    assert any(p.policy_id == expected.policy_id for p in policies)


def test_startup_postgres__second_startup__is_idempotent(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    def build_app() -> Any:
        return create_app(
            unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(sqlalchemy_session_factory),
            clock=lambda: T0,
            adapter_registry=AdapterRegistry([CiscoAdapter()]),
            seed_on_startup=True,
        )

    with TestClient(build_app()):
        pass
    with TestClient(build_app()):
        pass

    expected = build_slice1_policies(T0)[0]
    repo = SqlAlchemyConfigurationPolicyRepository(sqlalchemy_session_factory())
    policies = repo.get_applicable_to_device(expected.applies_to)
    assert len([p for p in policies if p.policy_id == expected.policy_id]) == 1


def test_startup_postgres__policy_conflict__fails_application_startup(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    from meta_rne.domain.config import AclDirection
    from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity

    conflicting_policy_id = build_slice1_policies(T0)[0].policy_id
    conflicting = ConfigurationPolicy(
        policy_id=conflicting_policy_id,
        applies_to="a-different-device",
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-OTHER",
                interface_name="GigabitEthernet0/9",
                direction=AclDirection.OUT,
                severity=Severity.LOW,
                recommendation="irrelevant",
            ),
        ),
        created_at=T0,
    )
    seeded_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    seeded_uow.configuration_policies.seed_if_missing((conflicting,))
    seeded_uow.commit()
    seeded_uow.close()

    app = create_app(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(sqlalchemy_session_factory),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=True,
    )

    with pytest.raises(PolicySeedConflictError), TestClient(app):
        pass


# --- POST / GET atomicity ----------------------------------------------------


def test_post_postgres__missing_acl__atomically_creates_device_snapshot_and_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["violations_detected"] == 1
    assert body["incidents_created"] == 1

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.current_snapshot_id == "snap-1"
    assert device.baseline_snapshot_id == "snap-1"
    assert verify_uow.configuration_snapshots.get_by_id("snap-1") is not None
    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    assert incidents[0].status.value == "OPEN"
    verify_uow.close()


def test_post_postgres__repeated_submission__updates_same_open_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    ids = iter(["snap-1", "snap-2"])
    client = _app(
        sqlalchemy_session_factory, snapshot_id_factory=lambda: next(ids), clock=lambda: T0
    )
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    first_incident_id = (
        SqlAlchemyUnitOfWork(sqlalchemy_session_factory).incidents.list_all()[0].incident_id
    )
    client2 = _app(
        sqlalchemy_session_factory, snapshot_id_factory=lambda: "snap-2", clock=lambda: T1
    )

    response = client2.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["incidents_created"] == 0
    assert body["incidents_updated"] == 1

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.current_snapshot_id == "snap-2"
    assert device.baseline_snapshot_id == "snap-1"
    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    assert incidents[0].incident_id == first_incident_id
    assert incidents[0].occurrence_count == 2
    verify_uow.close()


def test_get_incidents_postgres__returns_stored_incident_with_fingerprint(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    response = client.get("/incidents")

    assert response.status_code == 200
    incidents = response.json()
    assert len(incidents) == 1
    assert incidents[0]["fingerprint"]
    assert incidents[0]["device_id"] == DEVICE_ID


def test_api_postgres__post_and_get_use_independent_sessions(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    session_ids: list[int] = []

    def tracking_session_factory() -> Session:
        session = sqlalchemy_session_factory()
        session_ids.append(id(session))
        return session

    client = _app(tracking_session_factory)
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    client.get("/incidents")

    assert len(session_ids) >= 2
    assert len(set(session_ids)) == len(session_ids)


# --- Error paths --------------------------------------------------------------


def test_post_postgres__parse_error__returns_422_and_persists_nothing(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client = _app(sqlalchemy_session_factory)

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _NO_HOSTNAME_RAW_CONFIG},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "configuration_parse_error"
    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    assert verify_uow.devices.get_by_id(DEVICE_ID) is None
    verify_uow.close()


def test_post_postgres__vendor_conflict__returns_409_and_rolls_back_staged_snapshot(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    registry = AdapterRegistry([CiscoAdapter(), _FakeAristaAdapter()])
    client1 = _app(
        sqlalchemy_session_factory, adapter_registry=registry, snapshot_id_factory=lambda: "snap-1"
    )
    client1.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    client2 = _app(
        sqlalchemy_session_factory, adapter_registry=registry, snapshot_id_factory=lambda: "snap-2"
    )

    response = client2.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "arista-eos", "raw_config_text": "hostname arista-1\n"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "device_conflict"

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.vendor == VendorType.CISCO_IOS_XE
    assert device.current_snapshot_id == "snap-1"
    assert verify_uow.configuration_snapshots.get_by_id("snap-2") is None
    verify_uow.close()


# --- Lazy production engine composition --------------------------------------


# --- POST /incidents/{incident_id}/resolve (Day 7A, Gate 7A-C) --------------


def test_resolve_incident_postgres__open_incident__resolves_and_persists(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    clock_values = iter([T0, T1])
    client = _app(sqlalchemy_session_factory, clock=lambda: next(clock_values))
    create_response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    assert create_response.status_code == 201
    open_incident = client.get("/incidents").json()[0]

    response = client.post(f"/incidents/{open_incident['incident_id']}/resolve")

    assert response.status_code == 200
    body = response.json()
    assert body["incident_id"] == open_incident["incident_id"]
    assert body["status"] == "RESOLVED"
    assert body["resolved_at"] == "2026-07-18T11:00:00Z"
    assert body["updated_at"] == "2026-07-18T11:00:00Z"

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    stored = verify_uow.incidents.get_by_id(open_incident["incident_id"])
    assert stored is not None
    assert stored.status.value == "RESOLVED"
    assert stored.resolved_at == T1
    assert stored.updated_at == T1
    assert stored.occurrence_count == 1
    verify_uow.close()


def test_resolve_incident_postgres__get_incidents_after_resolve__reflects_persisted_state(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    clock_values = iter([T0, T1])
    client = _app(sqlalchemy_session_factory, clock=lambda: next(clock_values))
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    open_incident = client.get("/incidents").json()[0]
    client.post(f"/incidents/{open_incident['incident_id']}/resolve")

    incidents = client.get("/incidents").json()

    assert len(incidents) == 1
    fetched = incidents[0]
    assert fetched["status"] == "RESOLVED"
    assert fetched["resolved_at"] == "2026-07-18T11:00:00Z"
    assert fetched["updated_at"] == "2026-07-18T11:00:00Z"


def test_resolve_incident_postgres__repeated_resolution__is_idempotent(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    clock_values = iter([T0, T1, T0 + timedelta(hours=2)])
    client = _app(sqlalchemy_session_factory, clock=lambda: next(clock_values))
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    open_incident = client.get("/incidents").json()[0]
    first = client.post(f"/incidents/{open_incident['incident_id']}/resolve").json()

    second_response = client.post(f"/incidents/{open_incident['incident_id']}/resolve")

    assert second_response.status_code == 200
    second = second_response.json()
    assert second["resolved_at"] == first["resolved_at"] == "2026-07-18T11:00:00Z"
    assert second["updated_at"] == first["updated_at"] == "2026-07-18T11:00:00Z"


def test_resolve_incident_postgres__unknown_id__returns_exact_404_body(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client = _app(sqlalchemy_session_factory)

    response = client.post("/incidents/does-not-exist/resolve")

    assert response.status_code == 404
    assert response.json() == {
        "code": "incident_not_found",
        "detail": "Incident 'does-not-exist' was not found.",
    }


def test_resolve_incident_postgres__reingestion_after_resolve__creates_new_open_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    """Real HTTP, end-to-end proof of Gate 7A-D's binding reingestion
    scenario: ingest -> resolve -> reingest -> both incidents visible via
    GET /incidents. Detailed field-level invariants (timestamps, fingerprint,
    evidence) are already proven at the application/repository layer in
    test_incident_resolution_reingestion_postgres.py — this test proves the
    system wiring and HTTP response behavior instead."""
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    clock_values = iter([T0, T1, T0 + timedelta(hours=2), T0 + timedelta(hours=3)])
    snapshot_ids = iter(["snap-1", "snap-2", "snap-3"])
    client = _app(
        sqlalchemy_session_factory,
        clock=lambda: next(clock_values),
        snapshot_id_factory=lambda: next(snapshot_ids),
    )

    first = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    assert first.status_code == 201
    incident_a = client.get("/incidents").json()[0]

    resolve_response = client.post(f"/incidents/{incident_a['incident_id']}/resolve")
    assert resolve_response.status_code == 200

    second = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    assert second.status_code == 201
    second_body = second.json()
    assert second_body["incidents_created"] == 1
    assert second_body["incidents_updated"] == 0

    incidents = client.get("/incidents").json()
    assert len(incidents) == 2
    by_id = {i["incident_id"]: i for i in incidents}
    assert by_id[incident_a["incident_id"]]["status"] == "RESOLVED"
    incident_b = next(i for i in incidents if i["incident_id"] != incident_a["incident_id"])
    assert incident_b["status"] == "OPEN"
    assert incident_b["fingerprint"] == incident_a["fingerprint"]

    third = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    assert third.status_code == 201
    third_body = third.json()
    assert third_body["incidents_created"] == 0
    assert third_body["incidents_updated"] == 1


def test_api_postgres__lazy_database_url_composition__creates_and_disposes_engine(
    _meta_rne_test_migrated: None,
    postgres_test_database_url: str,
) -> None:
    """Exercises ``create_app``'s lazy production path (no
    ``unit_of_work_factory`` override) against the real test database — the
    only place ``DATABASE_URL``-driven engine creation and shutdown
    disposal can be proven end to end."""
    app = create_app(
        database_url=postgres_test_database_url,
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )

    with TestClient(app) as client:
        response = client.get("/incidents")

    assert response.status_code == 200
