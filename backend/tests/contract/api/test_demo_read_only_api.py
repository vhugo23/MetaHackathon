"""Contract tests for the public-demo read-only mode (conference hardening).

Each test builds its own isolated ``create_app(...)`` instance, never the
module-level ``app``. A writable app and a read-only app are composed over the
same in-memory store, so "blocked" is proven by the store being unchanged, not
only by the status code.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.api.read_only import (
    DEMO_READ_ONLY_CODE,
    DEMO_READ_ONLY_DETAIL,
    parse_demo_read_only,
)
from meta_rne.domain.config import AclDirection, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.persistence.memory.policy_repository import InMemoryConfigurationPolicyRepository
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
ORIGIN = "https://demo.example"
_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"
_BLOCKED_BODY = {"code": DEMO_READ_ONLY_CODE, "detail": DEMO_READ_ONLY_DETAIL}


def _client(store: InMemoryStore, *, read_only: bool, cors: tuple[str, ...] = ()) -> TestClient:
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        snapshot_id_factory=lambda: "snap-1",
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
        cors_allowed_origins=cors,
        demo_read_only=read_only,
    )
    return TestClient(app)


def _seed_device(store: InMemoryStore) -> None:
    uow = InMemoryUnitOfWork(store)
    uow.devices.save(
        Device(
            device_id=DEVICE_ID,
            vendor=VendorType.CISCO_IOS_XE,
            current_snapshot_id=None,
            baseline_snapshot_id=None,
            created_at=T0,
            updated_at=T0,
        )
    )
    uow.commit()


def _seed_policy(store: InMemoryStore) -> None:
    InMemoryConfigurationPolicyRepository(store).seed_if_missing(
        (
            ConfigurationPolicy(
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
            ),
        )
    )


def _config_body() -> dict[str, Any]:
    return {"vendor": "cisco-ios-xe", "raw_config_text": _RAW_CONFIG}


def _telemetry_body() -> dict[str, Any]:
    return {
        "sampled_at": T0.isoformat(),
        "cpu_utilization_pct": 50.0,
        "memory_utilization_pct": 50.0,
        "interface_error_rate": 0.0,
        "interface_states": [],
        "bgp_sessions": [],
    }


# --- flag parsing --------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, "", "   ", "false", "FALSE", " False "])
def test_parse__unset_blank_or_false__is_off(raw: str | None) -> None:
    assert parse_demo_read_only(raw) is False


@pytest.mark.parametrize("raw", ["true", "TRUE", " True "])
def test_parse__true__is_on(raw: str) -> None:
    assert parse_demo_read_only(raw) is True


@pytest.mark.parametrize("raw", ["1", "yes", "on", "tru", "enabled"])
def test_parse__anything_else__fails_closed_instead_of_leaving_the_demo_writable(
    raw: str,
) -> None:
    with pytest.raises(ValueError, match="META_RNE_DEMO_READ_ONLY"):
        parse_demo_read_only(raw)


# --- default (off) preserves existing behaviour ---------------------------------


def test_default_false__config_post_still_works() -> None:
    client = _client(InMemoryStore(), read_only=False)

    response = client.post(f"/devices/{DEVICE_ID}/config", json=_config_body())

    assert response.status_code == 201


def test_default_false__telemetry_post_still_works() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _client(store, read_only=False)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_telemetry_body())

    assert response.status_code == 201


def test_default_false__incident_resolution_still_works() -> None:
    store = InMemoryStore()
    _seed_policy(store)
    client = _client(store, read_only=False)
    client.post(f"/devices/{DEVICE_ID}/config", json=_config_body())
    incident_id = client.get("/incidents").json()[0]["incident_id"]

    response = client.post(f"/incidents/{incident_id}/resolve")

    assert response.status_code == 200
    assert response.json()["status"] == "RESOLVED"


def test_create_app_without_the_argument__is_not_read_only() -> None:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )

    response = TestClient(app).post(f"/devices/{DEVICE_ID}/config", json=_config_body())

    assert response.status_code == 201


# --- read-only blocks every mutating method -------------------------------------


def test_read_only__config_post_is_blocked_with_the_controlled_body_and_writes_nothing() -> None:
    store = InMemoryStore()
    client = _client(store, read_only=True)

    response = client.post(f"/devices/{DEVICE_ID}/config", json=_config_body())

    assert response.status_code == 403
    assert response.json() == _BLOCKED_BODY
    assert client.get("/incidents").json() == []
    assert InMemoryUnitOfWork(store).devices.get_by_id(DEVICE_ID) is None


def test_read_only__telemetry_post_is_blocked_and_stores_no_sample() -> None:
    store = InMemoryStore()
    _seed_device(store)
    client = _client(store, read_only=True)

    response = client.post(f"/devices/{DEVICE_ID}/telemetry", json=_telemetry_body())

    assert response.status_code == 403
    assert response.json() == _BLOCKED_BODY
    recent = client.get(
        f"/devices/{DEVICE_ID}/telemetry/recent", params={"since": "2000-01-01T00:00:00Z"}
    )
    assert recent.json() == []


def test_read_only__incident_resolution_is_blocked_and_the_incident_stays_open() -> None:
    store = InMemoryStore()
    _seed_policy(store)
    _client(store, read_only=False).post(f"/devices/{DEVICE_ID}/config", json=_config_body())
    read_only = _client(store, read_only=True)
    incident_id = read_only.get("/incidents").json()[0]["incident_id"]

    response = read_only.post(f"/incidents/{incident_id}/resolve")

    assert response.status_code == 403
    assert response.json() == _BLOCKED_BODY
    assert read_only.get("/incidents").json()[0]["status"] == "OPEN"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_read_only__every_mutating_method_is_blocked_on_any_path(method: str) -> None:
    client = _client(InMemoryStore(), read_only=True)

    for path in ("/incidents/some-id/resolve", "/anything/not/a/route"):
        response = client.request(method, path)
        assert response.status_code == 403
        assert response.json() == _BLOCKED_BODY


def test_read_only__blocked_response_exposes_no_internals() -> None:
    client = _client(InMemoryStore(), read_only=True)

    response = client.post("/incidents/x/resolve")

    assert set(response.json()) == {"code", "detail"}
    assert "Traceback" not in response.text


# --- read-only keeps reads working ----------------------------------------------


def test_read_only__health_is_unaffected() -> None:
    client = _client(InMemoryStore(), read_only=True)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_read_only__network_state_and_diagnosis_reach_their_routes() -> None:
    client = _client(InMemoryStore(), read_only=True)

    for path in ("/network/state", "/network/diagnosis"):
        response = client.get(path)
        # Empty store: the route's own controlled 404, never the demo guard's 403.
        assert response.status_code == 404
        assert response.json()["code"] == "operational_state_not_found"


def test_read_only__incident_list_and_head_are_allowed() -> None:
    client = _client(InMemoryStore(), read_only=True)

    assert client.get("/incidents").status_code == 200
    assert client.head("/health").status_code != 403


def test_read_only__options_preflight_is_allowed_for_cors() -> None:
    client = _client(InMemoryStore(), read_only=True, cors=(ORIGIN,))

    response = client.options(
        "/incidents",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Accept",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN


def test_read_only__blocked_response_still_carries_cors_headers() -> None:
    client = _client(InMemoryStore(), read_only=True, cors=(ORIGIN,))

    response = client.post("/incidents/x/resolve", headers={"Origin": ORIGIN})

    assert response.status_code == 403
    assert response.headers["access-control-allow-origin"] == ORIGIN


# --- the guard is HTTP-only ------------------------------------------------------


def test_read_only__does_not_block_writes_outside_http() -> None:
    """Direct unit-of-work writes (as the seed scripts and services do) are
    never behind the HTTP guard."""
    store = InMemoryStore()
    client = _client(store, read_only=True)
    assert client.post(f"/devices/{DEVICE_ID}/config", json=_config_body()).status_code == 403

    _seed_device(store)

    assert InMemoryUnitOfWork(store).devices.get_by_id(DEVICE_ID) is not None


def test_read_only__startup_policy_seeding_still_runs() -> None:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=True,
        demo_read_only=True,
    )
    with TestClient(app):  # entering the context runs the lifespan startup
        pass

    policies = InMemoryUnitOfWork(store).configuration_policies.get_applicable_to_device(DEVICE_ID)
    assert len(policies) >= 1
