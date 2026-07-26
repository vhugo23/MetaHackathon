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

import json
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
from meta_rne.domain.device import Device
from meta_rne.persistence.errors import PolicySeedConflictError
from meta_rne.persistence.seeds import build_slice1_policies
from meta_rne.persistence.sqlalchemy.policy_repository import (
    SqlAlchemyConfigurationPolicyRepository,
)
from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

pytestmark = pytest.mark.postgres

_EXPECTED_EVENT_KEY_ORDER = [
    "incident_id",
    "device_id",
    "rule_ref",
    "severity",
    "status",
    "outcome",
    "timestamp",
]


def _json_lines(stdout: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip()]


DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)

_MISSING_ACL_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"
_NO_HOSTNAME_RAW_CONFIG = "interface GigabitEthernet0/1\n!\n"

# The "removed" config drops both the ACL definition block and the
# interface's "ip access-group ... in" assignment — CiscoAdapter's own rule
# 7 (UNDECLARED_ACL_REFERENCE) rejects a config that references an ACL
# without defining it, so a dangling assignment cannot be used to isolate
# only the ACL-definition removal. This legitimately produces two drift
# entries: the ACL definition itself (removed) and the interface's acl_in
# field (changed, "ACL-EXTERNAL-IN" -> null) — both asserted below.
_ACL_DEFINED_RAW_CONFIG = (
    "hostname spine-01\n"
    "!\n"
    "interface GigabitEthernet0/1\n"
    " ip address 10.0.0.1 255.255.255.252\n"
    " ip access-group ACL-EXTERNAL-IN in\n"
    " no shutdown\n"
    "!\n"
    "ip access-list extended ACL-EXTERNAL-IN\n"
    " 10 permit ip any any\n"
    "!\n"
    "end\n"
)
_ACL_REMOVED_RAW_CONFIG = (
    "hostname spine-01\n"
    "!\n"
    "interface GigabitEthernet0/1\n"
    " ip address 10.0.0.1 255.255.255.252\n"
    " no shutdown\n"
    "!\n"
    "end\n"
)


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


# --- Gate H4A: anomaly-incident API acceptance through real PostgreSQL -----
#
# T0_PLUS_30S (not this file's own T1 = T0 + 1 hour, which is unrelated —
# used elsewhere in this file for drift-timing tests, and is far outside
# TelemetryIngestionService's 5-minute retention window, which would prune
# the first sample before RuleEngine.evaluate ever sees it).

T0_PLUS_30S = T0 + timedelta(seconds=30)


def _seed_device_for_telemetry(
    session_factory: Callable[[], Session], device_id: str = DEVICE_ID
) -> None:
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


def _telemetry_payload(**overrides: object) -> dict[str, Any]:
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


def test_get_incidents_postgres__cpu_anomaly__returns_matching_anomaly_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device_for_telemetry(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0_PLUS_30S.isoformat(), cpu_utilization_pct=96.0),
    )

    incidents = client.get("/incidents").json()
    anomaly_incidents = [i for i in incidents if i["rule_ref"] == "RULE-CPU-HIGH"]
    assert len(anomaly_incidents) == 1
    incident = anomaly_incidents[0]
    assert incident["source"] == "ANOMALY"
    assert incident["severity"] == "High"
    assert incident["affected_resource"] == "device"
    assert incident["recommendation"] == (
        f"Investigate sustained high CPU utilization on {DEVICE_ID}."
    )
    assert incident["evidence"]["samples"][0]["cpu_utilization_pct"] == 95.0
    assert incident["evidence"]["samples"][1]["cpu_utilization_pct"] == 96.0


def test_get_incidents_postgres__repeated_cpu_anomaly__updates_one_open_incident(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    _seed_device_for_telemetry(sqlalchemy_session_factory)
    # A distinct observed_at per request (the API's server-generated clock,
    # not sampled_at) is required to prove last_seen_at actually advances —
    # _app's default clock is fixed at T0.
    clock_values = iter([T0, T0_PLUS_30S, T0_PLUS_30S + timedelta(minutes=1)])
    client = _app(sqlalchemy_session_factory, clock=lambda: next(clock_values))

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0_PLUS_30S.isoformat(), cpu_utilization_pct=96.0),
    )
    first = next(i for i in client.get("/incidents").json() if i["rule_ref"] == "RULE-CPU-HIGH")

    t2 = T0_PLUS_30S + timedelta(minutes=1)
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=t2.isoformat(), cpu_utilization_pct=97.0),
    )

    incidents = [i for i in client.get("/incidents").json() if i["rule_ref"] == "RULE-CPU-HIGH"]
    assert len(incidents) == 1
    second = incidents[0]
    assert second["incident_id"] == first["incident_id"]
    assert second["occurrence_count"] == 2
    assert second["created_at"] == first["created_at"]
    assert second["last_seen_at"] != first["last_seen_at"]


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


# --- GET /devices/{device_id}/drift (Day 9, Gate 4) --------------------------


def test_device_drift_postgres__ac06_single_submission__returns_empty_report(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client = _app(sqlalchemy_session_factory, snapshot_id_factory=lambda: "snap-1")
    setup_response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _ACL_DEFINED_RAW_CONFIG},
    )
    assert setup_response.status_code == 201

    response = client.get(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 200
    assert response.json() == {"added": [], "removed": [], "changed": []}


def test_device_drift_postgres__ac05_later_removal__returns_exact_removed_entry(
    sqlalchemy_session_factory: Callable[[], Session],
) -> None:
    client1 = _app(
        sqlalchemy_session_factory, snapshot_id_factory=lambda: "snap-1", clock=lambda: T0
    )
    first_setup_response = client1.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _ACL_DEFINED_RAW_CONFIG},
    )
    assert first_setup_response.status_code == 201

    client2 = _app(
        sqlalchemy_session_factory, snapshot_id_factory=lambda: "snap-2", clock=lambda: T1
    )
    second_setup_response = client2.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _ACL_REMOVED_RAW_CONFIG},
    )
    assert second_setup_response.status_code == 201

    response = client2.get(f"/devices/{DEVICE_ID}/drift")

    assert response.status_code == 200
    body = response.json()
    assert body["added"] == []
    assert body["removed"] == [
        {
            "resource": "acl:ACL-EXTERNAL-IN",
            "field": None,
            "old_value": "ACL-EXTERNAL-IN",
            "new_value": None,
        }
    ]
    assert body["changed"] == [
        {
            "resource": "interface:GigabitEthernet0/1",
            "field": "acl_in",
            "old_value": "ACL-EXTERNAL-IN",
            "new_value": None,
        }
    ]

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    device = verify_uow.devices.get_by_id(DEVICE_ID)
    assert device is not None
    assert device.baseline_snapshot_id == "snap-1"
    assert device.current_snapshot_id == "snap-2"
    verify_uow.close()

    # A fresh app/API instance re-reading the same persisted PostgreSQL
    # state (no shared Python object, no shared UnitOfWork) reports the
    # identical drift result — proving the result comes from persisted
    # state, not in-process memory.
    client3 = _app(sqlalchemy_session_factory)
    fresh_response = client3.get(f"/devices/{DEVICE_ID}/drift")
    assert fresh_response.status_code == 200
    assert fresh_response.json() == body


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


# --- AC-10F: structured incident events through real HTTP + real PostgreSQL -


def test_post_postgres__missing_acl__emits_one_created_stdout_event(
    sqlalchemy_session_factory: Callable[[], Session],
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed_slice1_policy_directly(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    capfd.readouterr()
    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {
        "device_id",
        "snapshot_id",
        "normalized_config",
        "violations_detected",
        "incidents_created",
        "incidents_updated",
    }
    assert body["incidents_created"] == 1
    assert body["incidents_updated"] == 0

    events = _json_lines(captured.out)
    assert len(events) == 1
    event = events[0]
    assert list(event.keys()) == _EXPECTED_EVENT_KEY_ORDER

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    incident = incidents[0]
    verify_uow.close()

    assert event["incident_id"] == incident.incident_id
    assert event["device_id"] == DEVICE_ID
    assert event["rule_ref"] == incident.rule_ref
    assert event["severity"] == incident.severity.value
    assert event["status"] == "OPEN"
    assert event["outcome"] == "CREATED"
    assert event["timestamp"] == incident.last_seen_at.isoformat().replace("+00:00", "Z")


def test_post_postgres__repeated_submission__emits_one_updated_stdout_event(
    sqlalchemy_session_factory: Callable[[], Session],
    capfd: pytest.CaptureFixture[str],
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
    capfd.readouterr()  # discard the first request's own captured output

    client2 = _app(
        sqlalchemy_session_factory, snapshot_id_factory=lambda: "snap-2", clock=lambda: T1
    )
    capfd.readouterr()  # clear immediately before the second request

    response = client2.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    body = response.json()
    assert body["incidents_created"] == 0
    assert body["incidents_updated"] == 1

    events = _json_lines(captured.out)
    assert len(events) == 1
    event = events[0]

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    incidents = verify_uow.incidents.list_all()
    assert len(incidents) == 1
    incident = incidents[0]
    verify_uow.close()

    assert incident.occurrence_count == 2
    assert incident.created_at == T0
    assert incident.last_seen_at == T1

    assert event["outcome"] == "UPDATED"
    assert event["incident_id"] == incident.incident_id
    assert event["timestamp"] == incident.last_seen_at.isoformat().replace("+00:00", "Z")


def test_get_incidents_postgres__cpu_anomaly__emits_one_created_stdout_event(
    sqlalchemy_session_factory: Callable[[], Session],
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed_device_for_telemetry(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    capfd.readouterr()  # discard the first request's own captured output (no anomaly yet)

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0_PLUS_30S.isoformat(), cpu_utilization_pct=96.0),
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    assert set(response.json().keys()) == {"sample", "anomalies"}

    events = _json_lines(captured.out)
    assert len(events) == 1
    event = events[0]
    assert list(event.keys()) == _EXPECTED_EVENT_KEY_ORDER
    assert event["rule_ref"] == "RULE-CPU-HIGH"
    assert event["outcome"] == "CREATED"
    assert event["severity"] == "High"
    assert event["status"] == "OPEN"

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    incidents = [i for i in verify_uow.incidents.list_all() if i.rule_ref == "RULE-CPU-HIGH"]
    assert len(incidents) == 1
    incident = incidents[0]
    verify_uow.close()

    assert event["incident_id"] == incident.incident_id
    assert event["timestamp"] == incident.last_seen_at.isoformat().replace("+00:00", "Z")


def test_get_incidents_postgres__repeated_cpu_anomaly__emits_one_updated_stdout_event(
    sqlalchemy_session_factory: Callable[[], Session],
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed_device_for_telemetry(sqlalchemy_session_factory)
    clock_values = iter([T0, T0_PLUS_30S, T0_PLUS_30S + timedelta(minutes=1)])
    client = _app(sqlalchemy_session_factory, clock=lambda: next(clock_values))

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0_PLUS_30S.isoformat(), cpu_utilization_pct=96.0),
    )
    capfd.readouterr()  # discard everything captured before the repeated-detection request

    t2 = T0_PLUS_30S + timedelta(minutes=1)
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=t2.isoformat(), cpu_utilization_pct=97.0),
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    assert set(response.json().keys()) == {"sample", "anomalies"}

    events = _json_lines(captured.out)
    assert len(events) == 1
    event = events[0]
    assert event["outcome"] == "UPDATED"

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    incidents = [i for i in verify_uow.incidents.list_all() if i.rule_ref == "RULE-CPU-HIGH"]
    assert len(incidents) == 1
    incident = incidents[0]
    verify_uow.close()

    assert incident.occurrence_count == 2
    assert event["incident_id"] == incident.incident_id
    assert event["timestamp"] == incident.last_seen_at.isoformat().replace("+00:00", "Z")


def test_telemetry_postgres__all_three_rules_trigger__emits_three_ordered_events(
    sqlalchemy_session_factory: Callable[[], Session],
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed_device_for_telemetry(sqlalchemy_session_factory)
    client = _app(sqlalchemy_session_factory)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"

    for offset, state in enumerate(["up", "down", "up", "down"]):
        client.post(
            f"/devices/{DEVICE_ID}/telemetry",
            json=_telemetry_payload(
                sampled_at=(T0 + timedelta(seconds=10 * offset)).isoformat(),
                cpu_utilization_pct=50.0,
                interface_states=[{"name": interface, "oper_state": state}],
            ),
        )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(
            sampled_at=(T0 + timedelta(seconds=40)).isoformat(),
            cpu_utilization_pct=95.0,
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Established"}],
        ),
    )
    capfd.readouterr()  # discard everything captured before the firing request

    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(
            sampled_at=(T0 + timedelta(seconds=50)).isoformat(),
            cpu_utilization_pct=95.0,
            interface_states=[{"name": interface, "oper_state": "up"}],
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Idle"}],
        ),
    )
    captured = capfd.readouterr()

    assert response.status_code == 201
    assert set(response.json().keys()) == {"sample", "anomalies"}

    events = _json_lines(captured.out)
    assert len(events) == 3
    assert [event["rule_ref"] for event in events] == [
        "RULE-CPU-HIGH",
        "RULE-LINK-FLAP",
        "RULE-BGP-DOWN",
    ]
    assert all(event["outcome"] == "CREATED" for event in events)

    # Every event is matched to its durable incident by incident_id rather
    # than trusting `list_all()`'s positional order for tied timestamps.
    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    incidents_by_id = {
        incident.incident_id: incident for incident in verify_uow.incidents.list_all()
    }
    verify_uow.close()
    assert {event["incident_id"] for event in events} == set(incidents_by_id)
    for event in events:
        persisted = incidents_by_id[event["incident_id"]]
        assert event["rule_ref"] == persisted.rule_ref
        assert event["device_id"] == DEVICE_ID
        assert event["status"] == "OPEN"
        assert event["timestamp"] == persisted.last_seen_at.isoformat().replace("+00:00", "Z")


def test_telemetry_postgres__resolved_recurrence__emits_created_event_with_new_incident_id(
    sqlalchemy_session_factory: Callable[[], Session],
    capfd: pytest.CaptureFixture[str],
) -> None:
    _seed_device_for_telemetry(sqlalchemy_session_factory)
    t_resolve = T0_PLUS_30S + timedelta(minutes=1)
    t_recur = T0_PLUS_30S + timedelta(minutes=2)
    clock_values = iter([T0, T0_PLUS_30S, t_resolve, t_recur])
    client = _app(sqlalchemy_session_factory, clock=lambda: next(clock_values))

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0_PLUS_30S.isoformat(), cpu_utilization_pct=96.0),
    )
    resolved_incident = next(
        i for i in client.get("/incidents").json() if i["rule_ref"] == "RULE-CPU-HIGH"
    )
    assert resolved_incident["status"] == "OPEN"

    capfd.readouterr()  # clear immediately before the resolution request
    resolve_response = client.post(f"/incidents/{resolved_incident['incident_id']}/resolve")
    resolve_captured = capfd.readouterr()

    assert resolve_response.status_code == 200
    assert resolve_response.json()["status"] == "RESOLVED"
    # Resolution is out of AC-10 scope (Gate AC-10C binding decision) — it
    # must never emit a structured incident event.
    assert resolve_captured.out == ""

    capfd.readouterr()  # clear immediately before the recurrence request
    response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=t_recur.isoformat(), cpu_utilization_pct=97.0),
    )
    captured = capfd.readouterr()

    assert response.status_code == 201

    events = _json_lines(captured.out)
    assert len(events) == 1
    event = events[0]
    assert event["outcome"] == "CREATED"
    assert event["incident_id"] != resolved_incident["incident_id"]

    verify_uow = SqlAlchemyUnitOfWork(sqlalchemy_session_factory)
    cpu_incidents = [i for i in verify_uow.incidents.list_all() if i.rule_ref == "RULE-CPU-HIGH"]
    verify_uow.close()
    assert len(cpu_incidents) == 2
    resolved = next(i for i in cpu_incidents if i.incident_id == resolved_incident["incident_id"])
    new_incident = next(
        i for i in cpu_incidents if i.incident_id != resolved_incident["incident_id"]
    )
    assert resolved.status.value == "RESOLVED"
    assert new_incident.status.value == "OPEN"
    assert new_incident.fingerprint == resolved.fingerprint

    assert event["incident_id"] == new_incident.incident_id
    assert event["timestamp"] == new_incident.last_seen_at.isoformat().replace("+00:00", "Z")
