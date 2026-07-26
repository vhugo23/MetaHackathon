"""Contract tests for ``GET /incidents`` (Day 5B).

Each test builds its own isolated ``create_app(...)`` instance — never the
module-level production ``app`` and never ``app.dependency_overrides``.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.domain.config import AclDirection, VendorType
from meta_rne.domain.device import Device
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


# --- Gate H4A: anomaly-incident API acceptance (AC-07/AC-08/AC-09) ----------
#
# POST /devices/{device_id}/telemetry -> GET /incidents, exactly as an
# operator would observe it. No repository is inspected directly here — only
# HTTP requests/responses (matching this file's existing convention).

T1 = T0 + timedelta(seconds=30)


def _seed_device(store: InMemoryStore, device_id: str = DEVICE_ID) -> None:
    uow = InMemoryUnitOfWork(store)
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


def _incident_by_rule_ref(incidents: list[dict[str, Any]], rule_ref: str) -> dict[str, Any]:
    matches = [i for i in incidents if i["rule_ref"] == rule_ref]
    assert len(matches) == 1, f"expected exactly one {rule_ref} incident, found {len(matches)}"
    return matches[0]


def test_ac07_sustained_cpu__get_incidents_returns_matching_open_incident() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store=store)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    post_response = client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T1.isoformat(), cpu_utilization_pct=96.0),
    )

    # POST telemetry response is unaffected by this gate.
    assert set(post_response.json().keys()) == {"sample", "anomalies"}

    incidents = client.get("/incidents").json()
    incident = _incident_by_rule_ref(incidents, "RULE-CPU-HIGH")
    assert incident["source"] == "ANOMALY"
    assert incident["severity"] == "High"
    assert incident["affected_resource"] == "device"
    assert incident["recommendation"] == (
        f"Investigate sustained high CPU utilization on {DEVICE_ID}."
    )
    assert incident["occurrence_count"] == 1
    assert incident["evidence"]["samples"] == [
        {"timestamp": T0.isoformat().replace("+00:00", "Z"), "cpu_utilization_pct": 95.0},
        {"timestamp": T1.isoformat().replace("+00:00", "Z"), "cpu_utilization_pct": 96.0},
    ]


def test_ac08_link_flap__get_incidents_returns_matching_open_incident() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store=store)
    interface = "GigabitEthernet0/1"
    states = ["up", "down", "up", "down", "up"]

    for offset, state in enumerate(states):
        client.post(
            f"/devices/{DEVICE_ID}/telemetry",
            json=_telemetry_payload(
                sampled_at=(T0 + timedelta(seconds=12 * offset)).isoformat(),
                interface_states=[{"name": interface, "oper_state": state}],
            ),
        )

    incidents = client.get("/incidents").json()
    incident = _incident_by_rule_ref(incidents, "RULE-LINK-FLAP")
    assert incident["source"] == "ANOMALY"
    assert incident["severity"] == "High"
    assert incident["affected_resource"] == f"interface:{interface}"
    assert incident["recommendation"] == (
        f"Investigate unstable link state on {DEVICE_ID} interface {interface}."
    )
    transitions = incident["evidence"]["transitions"]
    assert [t["oper_state"] for t in transitions] == ["down", "up", "down", "up"]
    assert incident["evidence"]["interface_name"] == interface


def test_ac09_bgp_down__get_incidents_returns_matching_open_incident() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store=store)
    neighbor = "10.0.0.2"

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(
            sampled_at=T0.isoformat(),
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Established"}],
        ),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(
            sampled_at=T1.isoformat(),
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Idle"}],
        ),
    )

    incidents = client.get("/incidents").json()
    incident = _incident_by_rule_ref(incidents, "RULE-BGP-DOWN")
    assert incident["source"] == "ANOMALY"
    assert incident["severity"] == "Critical"
    assert incident["affected_resource"] == f"bgp-neighbor:{neighbor}"
    assert incident["recommendation"] == (
        f"Investigate BGP session down on {DEVICE_ID} neighbor {neighbor}."
    )
    assert incident["evidence"] == {
        "neighbor_ip": neighbor,
        "previous_state": "Established",
        "state": "Idle",
    }


def test_multiple_anomalies__one_ingestion__all_appear_in_get_incidents() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store=store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"

    for offset, state in enumerate(["up", "down", "up", "down"]):
        client.post(
            f"/devices/{DEVICE_ID}/telemetry",
            json=_telemetry_payload(
                sampled_at=(T0 + timedelta(seconds=10 * offset)).isoformat(),
                cpu_utilization_pct=95.0,
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
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(
            sampled_at=(T0 + timedelta(seconds=50)).isoformat(),
            cpu_utilization_pct=95.0,
            interface_states=[{"name": interface, "oper_state": "up"}],
            bgp_sessions=[{"neighbor_ip": neighbor, "state": "Idle"}],
        ),
    )

    incidents = client.get("/incidents").json()
    anomaly_incidents = [i for i in incidents if i["source"] == "ANOMALY"]
    assert {i["rule_ref"] for i in anomaly_incidents} == {
        "RULE-CPU-HIGH",
        "RULE-LINK-FLAP",
        "RULE-BGP-DOWN",
    }
    assert len(anomaly_incidents) == 3


def test_repeated_cpu_detection__appears_as_one_open_incident_with_updated_fields() -> None:
    store = InMemoryStore()
    _seed_device(store)
    # A distinct observed_at per request (the API's server-generated clock,
    # not sampled_at) is required to prove last_seen_at actually advances —
    # _test_app's default clock is fixed at T0.
    clock_values = iter([T0, T1, T0 + timedelta(seconds=60)])
    client = _test_app(store=store, clock=lambda: next(clock_values))

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T1.isoformat(), cpu_utilization_pct=96.0),
    )
    first_incident = _incident_by_rule_ref(client.get("/incidents").json(), "RULE-CPU-HIGH")

    t2 = T0 + timedelta(seconds=60)
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=t2.isoformat(), cpu_utilization_pct=97.0),
    )

    incidents = client.get("/incidents").json()
    assert len([i for i in incidents if i["rule_ref"] == "RULE-CPU-HIGH"]) == 1
    second_incident = _incident_by_rule_ref(incidents, "RULE-CPU-HIGH")
    assert second_incident["incident_id"] == first_incident["incident_id"]
    assert second_incident["occurrence_count"] == 2
    assert second_incident["created_at"] == first_incident["created_at"]
    assert second_incident["last_seen_at"] != first_incident["last_seen_at"]
    assert second_incident["evidence"] != first_incident["evidence"]


def test_resolved_anomaly_recurrence__creates_new_open_incident_preserves_resolved() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _test_app(store=store)

    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T1.isoformat(), cpu_utilization_pct=96.0),
    )
    first_incident = _incident_by_rule_ref(client.get("/incidents").json(), "RULE-CPU-HIGH")

    resolve_response = client.post(f"/incidents/{first_incident['incident_id']}/resolve")
    assert resolve_response.status_code == 200

    # A single further qualifying sample is enough to re-trigger CPU-HIGH:
    # the prior (still-retained, unpruned) sample from T1 plus this one form
    # a new qualifying pair immediately — sending a second post-resolution
    # sample would legitimately fire CPU-HIGH a second time against the
    # already-recreated OPEN incident, incrementing its occurrence_count to
    # 2, which is not what this test is proving.
    t2 = T0 + timedelta(seconds=60)
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=t2.isoformat(), cpu_utilization_pct=95.0),
    )

    incidents = client.get("/incidents").json()
    cpu_incidents = [i for i in incidents if i["rule_ref"] == "RULE-CPU-HIGH"]
    assert len(cpu_incidents) == 2
    old = next(i for i in cpu_incidents if i["incident_id"] == first_incident["incident_id"])
    new = next(i for i in cpu_incidents if i["incident_id"] != first_incident["incident_id"])
    assert old["status"] == "RESOLVED"
    assert new["status"] == "OPEN"
    assert new["occurrence_count"] == 1


def test_policy_and_anomaly_incidents__coexist_without_breaking_policy_evidence() -> None:
    store = InMemoryStore()
    InMemoryConfigurationPolicyRepository(store).seed_if_missing((_policy(),))
    _seed_device(store)
    client = _test_app(store=store)

    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T0.isoformat(), cpu_utilization_pct=95.0),
    )
    client.post(
        f"/devices/{DEVICE_ID}/telemetry",
        json=_telemetry_payload(sampled_at=T1.isoformat(), cpu_utilization_pct=96.0),
    )

    incidents = client.get("/incidents").json()
    assert len(incidents) == 2
    policy_incident = _incident_by_rule_ref(incidents, "policy-acl-external-in")
    anomaly_incident = _incident_by_rule_ref(incidents, "RULE-CPU-HIGH")
    assert policy_incident["source"] == "POLICY_VIOLATION"
    assert policy_incident["evidence"] == {
        "source_snapshot_id": "snap-1",
        "violation_type": "MISSING_REQUIRED_ACL",
        "expected_acl_name": "ACL-EXTERNAL-IN",
        "actual_acl_name": None,
        "interface_name": "GigabitEthernet0/1",
        "direction": "in",
    }
    assert anomaly_incident["source"] == "ANOMALY"
