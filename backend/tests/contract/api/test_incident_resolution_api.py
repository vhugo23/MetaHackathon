"""Contract tests for ``POST /incidents/{incident_id}/resolve`` (Day 7A,
Gate 7A-C).

Each test builds its own isolated ``create_app(...)`` instance — never the
module-level production ``app`` and never ``app.dependency_overrides``, same
convention as ``test_incidents_api.py``/``test_config_ingestion_api.py``. An
OPEN incident is always created through the existing supported
``POST /devices/{device_id}/config`` endpoint — never inserted directly via
SQL or a bare repository call.
"""

from collections.abc import Callable
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
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)

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


class _SequenceClock:
    """Deterministic clock consuming one value per call, in order, with a
    running call count — never the real system clock."""

    def __init__(self, values: list[datetime]) -> None:
        self._iterator = iter(values)
        self.call_count = 0

    def __call__(self) -> datetime:
        self.call_count += 1
        return next(self._iterator)


def _test_app(
    *,
    store: InMemoryStore | None = None,
    clock: Callable[[], datetime] = lambda: T0,
    snapshot_id_factory: Callable[[], str] = lambda: "snap-1",
) -> TestClient:
    store = store if store is not None else InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=clock,
        snapshot_id_factory=snapshot_id_factory,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    return TestClient(app)


def _create_open_incident(client: TestClient) -> dict[str, Any]:
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    incidents = client.get("/incidents").json()
    result: dict[str, Any] = incidents[0]
    return result


def _seeded_store() -> InMemoryStore:
    store = InMemoryStore()
    InMemoryConfigurationPolicyRepository(store).seed_if_missing((_policy(),))
    return store


# --- Successful resolution ----------------------------------------------------


def test_resolve_incident_api__open_incident__returns_200() -> None:
    clock = _SequenceClock([T0, T1])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)

    response = client.post(f"/incidents/{open_incident['incident_id']}/resolve")

    assert response.status_code == 200


def test_resolve_incident_api__open_incident__returns_direct_object_not_a_wrapper() -> None:
    clock = _SequenceClock([T0, T1])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)

    body = client.post(f"/incidents/{open_incident['incident_id']}/resolve").json()

    assert "data" not in body
    assert "error" not in body
    assert body["incident_id"] == open_incident["incident_id"]


def test_resolve_incident_api__open_incident__status_becomes_resolved() -> None:
    clock = _SequenceClock([T0, T1])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)

    body = client.post(f"/incidents/{open_incident['incident_id']}/resolve").json()

    assert body["status"] == "RESOLVED"


def test_resolve_incident_api__open_incident__resolved_at_and_updated_at_equal_clock_value() -> (
    None
):
    clock = _SequenceClock([T0, T1])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)

    body = client.post(f"/incidents/{open_incident['incident_id']}/resolve").json()

    assert body["resolved_at"] == "2026-07-18T11:00:00Z"
    assert body["updated_at"] == "2026-07-18T11:00:00Z"


def test_resolve_incident_api__open_incident__preserves_immutable_and_detection_owned_fields() -> (
    None
):
    clock = _SequenceClock([T0, T1])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)

    body = client.post(f"/incidents/{open_incident['incident_id']}/resolve").json()

    assert body["fingerprint"] == open_incident["fingerprint"]
    assert body["device_id"] == open_incident["device_id"]
    assert body["rule_ref"] == open_incident["rule_ref"]
    assert body["affected_resource"] == open_incident["affected_resource"]
    assert body["severity"] == open_incident["severity"]
    assert body["evidence"] == open_incident["evidence"]
    assert body["recommendation"] == open_incident["recommendation"]
    assert body["created_at"] == open_incident["created_at"]
    assert body["occurrence_count"] == open_incident["occurrence_count"]
    assert body["last_seen_at"] == open_incident["last_seen_at"]


# --- No request body -----------------------------------------------------------


def test_resolve_incident_api__no_json_body__succeeds() -> None:
    clock = _SequenceClock([T0, T1])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)

    response = client.post(f"/incidents/{open_incident['incident_id']}/resolve")

    assert response.status_code == 200


def test_resolve_incident_api__openapi_operation_has_no_request_body() -> None:
    client = _test_app(store=_seeded_store())

    schema = client.get("/openapi.json").json()

    operation = schema["paths"]["/incidents/{incident_id}/resolve"]["post"]
    assert "requestBody" not in operation


# --- Unknown incident ----------------------------------------------------------


def test_resolve_incident_api__unknown_id__returns_exact_404_body() -> None:
    client = _test_app()

    response = client.post("/incidents/missing-incident/resolve")

    assert response.status_code == 404
    assert response.json() == {
        "code": "incident_not_found",
        "detail": "Incident 'missing-incident' was not found.",
    }


# --- Idempotent repeated resolution ---------------------------------------------


def test_resolve_incident_api__repeated_resolution__returns_200_with_unchanged_timestamps() -> None:
    clock = _SequenceClock([T0, T1, T2])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)
    first = client.post(f"/incidents/{open_incident['incident_id']}/resolve").json()

    second_response = client.post(f"/incidents/{open_incident['incident_id']}/resolve")

    assert second_response.status_code == 200
    second = second_response.json()
    assert second["resolved_at"] == first["resolved_at"] == "2026-07-18T11:00:00Z"
    assert second["updated_at"] == first["updated_at"] == "2026-07-18T11:00:00Z"
    assert second["occurrence_count"] == first["occurrence_count"]


def test_resolve_incident_api__repeated_resolution__clock_not_called_a_second_time() -> None:
    clock = _SequenceClock([T0, T1, T2])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)
    client.post(f"/incidents/{open_incident['incident_id']}/resolve")
    assert clock.call_count == 2  # one for ingestion's observed_at, one for resolve

    client.post(f"/incidents/{open_incident['incident_id']}/resolve")

    assert clock.call_count == 2  # unchanged: the second resolve never reads the clock


# --- GET /incidents consistency after resolution --------------------------------


def test_resolve_incident_api__get_incidents_after_resolve__reflects_resolved_status() -> None:
    clock = _SequenceClock([T0, T1])
    client = _test_app(store=_seeded_store(), clock=clock)
    open_incident = _create_open_incident(client)
    resolve_body = client.post(f"/incidents/{open_incident['incident_id']}/resolve").json()

    incidents = client.get("/incidents").json()

    assert len(incidents) == 1
    fetched = incidents[0]
    assert fetched["status"] == "RESOLVED"
    assert fetched["updated_at"] == resolve_body["updated_at"]
    assert fetched["resolved_at"] == resolve_body["resolved_at"]


# --- OPEN response compatibility (before resolution) -----------------------------


def test_resolve_incident_api__before_resolution__open_incident_has_null_resolved_at() -> None:
    client = _test_app(store=_seeded_store(), clock=lambda: T0)

    open_incident = _create_open_incident(client)

    assert open_incident["status"] == "OPEN"
    assert open_incident["updated_at"] == "2026-07-18T10:00:00Z"
    assert open_incident["resolved_at"] is None
