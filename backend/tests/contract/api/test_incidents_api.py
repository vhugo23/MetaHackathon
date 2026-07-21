"""Contract tests for ``GET /incidents`` (Day 5B).

Each test builds its own isolated ``create_app(...)`` instance — never the
module-level production ``app`` and never ``app.dependency_overrides``.
"""

from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.persistence.memory.policy_repository import InMemoryConfigurationPolicyRepository
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)

_MISSING_ACL_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"


def _policy() -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id="policy-acl-external-in",
        applies_to=DEVICE_ID,
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-EXTERNAL-IN",
                interface_name="GigabitEthernet0/1",
                direction=AclDirection.IN,
                severity=Severity.MEDIUM,
                recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
            ),
        ),
        created_at=T0,
    )


def _test_app(
    *,
    store: InMemoryStore | None = None,
    clock: object = lambda: T0,
    snapshot_id_factory: object = lambda: "snap-1",
    unit_of_work_factory: object = None,
) -> TestClient:
    store = store if store is not None else InMemoryStore()
    uow_factory = unit_of_work_factory or (lambda: InMemoryUnitOfWork(store))
    app = create_app(
        unit_of_work_factory=uow_factory,
        clock=clock,
        snapshot_id_factory=snapshot_id_factory,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    return TestClient(app)


def test_incidents_api__empty_store__returns_empty_list() -> None:
    client = _test_app()

    response = client.get("/incidents")

    assert response.status_code == 200
    assert response.json() == []


def test_incidents_api__get_incidents__returns_created_incident() -> None:
    store = InMemoryStore()
    InMemoryConfigurationPolicyRepository(store).seed_if_missing((_policy(),))
    client = _test_app(store=store)
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    response = client.get("/incidents")

    assert response.status_code == 200
    incidents = response.json()
    assert isinstance(incidents, list)
    assert len(incidents) == 1
    incident = incidents[0]
    assert set(incident.keys()) == {
        "incident_id",
        "fingerprint",
        "device_id",
        "source",
        "rule_ref",
        "affected_resource",
        "severity",
        "status",
        "evidence",
        "recommendation",
        "created_at",
        "last_seen_at",
        "occurrence_count",
        "updated_at",
        "resolved_at",
    }
    assert incident["device_id"] == DEVICE_ID
    assert incident["source"] == "POLICY_VIOLATION"
    assert incident["rule_ref"] == "policy-acl-external-in"
    assert incident["severity"] == "Medium"
    assert incident["status"] == "OPEN"
    assert incident["occurrence_count"] == 1
    assert incident["fingerprint"]


def test_incidents_api__evidence_fields_fully_serialized() -> None:
    store = InMemoryStore()
    InMemoryConfigurationPolicyRepository(store).seed_if_missing((_policy(),))
    client = _test_app(store=store)
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    evidence = client.get("/incidents").json()[0]["evidence"]

    assert evidence == {
        "source_snapshot_id": "snap-1",
        "violation_type": "MISSING_REQUIRED_ACL",
        "expected_acl_name": "ACL-EXTERNAL-IN",
        "actual_acl_name": None,
        "interface_name": "GigabitEthernet0/1",
        "direction": "in",
    }


def test_incidents_api__datetimes_serialize_as_iso8601() -> None:
    store = InMemoryStore()
    InMemoryConfigurationPolicyRepository(store).seed_if_missing((_policy(),))
    client = _test_app(store=store)
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    incident = client.get("/incidents").json()[0]

    assert incident["created_at"] == "2026-07-18T10:00:00Z"
    assert incident["last_seen_at"] == "2026-07-18T10:00:00Z"
    assert incident["updated_at"] == "2026-07-18T10:00:00Z"
    assert incident["resolved_at"] is None


def test_incidents_api__repository_ordering_preserved() -> None:
    store = InMemoryStore()
    policy_a = _policy()
    policy_b = ConfigurationPolicy(
        policy_id="policy-b-mgmt-in",
        applies_to=DEVICE_ID,
        required_acls=(
            RequiredAclRule(
                acl_name="ACL-MGMT-IN",
                interface_name="GigabitEthernet0/2",
                direction=AclDirection.IN,
                severity=Severity.HIGH,
                recommendation="Assign ACL-MGMT-IN inbound to GigabitEthernet0/2",
            ),
        ),
        created_at=T0,
    )
    InMemoryConfigurationPolicyRepository(store).seed_if_missing((policy_a, policy_b))
    raw_config = (
        "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\ninterface GigabitEthernet0/2\n!\n"
    )
    client = _test_app(store=store)
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": raw_config},
    )

    incidents = client.get("/incidents").json()

    assert len(incidents) == 2
    created_ats = [i["created_at"] for i in incidents]
    incident_ids = [i["incident_id"] for i in incidents]
    assert created_ats == sorted(created_ats)
    if created_ats[0] == created_ats[1]:
        assert incident_ids == sorted(incident_ids)


def test_incidents_api__does_not_call_the_clock() -> None:
    calls: list[int] = []

    def spy_clock() -> datetime:
        calls.append(1)
        return T0

    client = _test_app(clock=spy_clock)

    client.get("/incidents")

    assert calls == []


def test_incidents_api__query_service_called_exactly_once() -> None:
    store = InMemoryStore()

    class _CountingFactory:
        def __init__(self, inner: Any) -> None:
            self._inner = inner
            self.call_count = 0

        def __call__(self) -> Any:
            self.call_count += 1
            return self._inner()

    factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    client = _test_app(unit_of_work_factory=factory)

    client.get("/incidents")

    assert factory.call_count == 1


def test_incidents_api__query_failure__returns_generic_production_500() -> None:
    class _FailingIncidents:
        def list_all(self) -> tuple[Any, ...]:
            raise RuntimeError("boom")

    class _BoomUnitOfWork:
        def __init__(self) -> None:
            self.incidents = _FailingIncidents()

        def commit(self) -> None:
            pass

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    app = create_app(
        unit_of_work_factory=lambda: _BoomUnitOfWork(),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/incidents")

    assert response.status_code == 500
