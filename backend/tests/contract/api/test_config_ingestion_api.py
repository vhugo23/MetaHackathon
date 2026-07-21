"""Contract tests for ``POST /devices/{device_id}/config`` (Day 5B).

Each test builds its own isolated ``create_app(...)`` instance — never the
module-level production ``app`` and never ``app.dependency_overrides`` — per
the Day 5B binding correction: tests construct isolated application
instances directly.

The very first test below
(``test_submit_configuration__successful_ingestion__returns_201``) was run
against the codebase before ``create_app``/routes/schemas existed,
producing a real ``ImportError`` — genuine red-green-refactor.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.api.dependencies import build_production_adapter_registry
from meta_rne.domain.config import (
    AclDirection,
    NormalizedConfiguration,
    NormalizedRouting,
    VendorType,
)
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity
from meta_rne.persistence.errors import (
    PersistenceError,
)
from meta_rne.persistence.memory.device_repository import InMemoryDeviceRepository
from meta_rne.persistence.memory.incident_repository import InMemoryIncidentRepository
from meta_rne.persistence.memory.policy_repository import InMemoryConfigurationPolicyRepository
from meta_rne.persistence.memory.snapshot_repository import InMemoryConfigurationSnapshotRepository
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from meta_rne.persistence.serialization import SerializationError

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)

_ARISTA_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "configs" / "arista"


def _load_arista_fixture(name: str) -> str:
    return (_ARISTA_FIXTURES_DIR / name).read_text()


_SATISFIED_RAW_CONFIG = (
    "hostname spine-01\n"
    "!\n"
    "ip access-list extended ACL-EXTERNAL-IN\n"
    " 10 permit ip any any\n"
    "!\n"
    "interface GigabitEthernet0/1\n"
    " ip access-group ACL-EXTERNAL-IN in\n"
    "!\n"
)

_MISSING_ACL_RAW_CONFIG = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n"

_NO_HOSTNAME_RAW_CONFIG = "interface GigabitEthernet0/1\n!\n"

_MULTI_INTERFACE_RAW_CONFIG = (
    "hostname spine-01\n"
    "!\n"
    "interface GigabitEthernet0/2\n"
    " description second\n"
    "!\n"
    "interface GigabitEthernet0/1\n"
    " description first\n"
    " ip address 10.0.0.1 255.255.255.252\n"
    "!\n"
    "router bgp 65000\n"
    " neighbor 10.0.0.2 remote-as 65001\n"
    " neighbor 10.0.0.1 remote-as 65002\n"
    "!\n"
)


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


def _seed_policy(store: InMemoryStore, policy: ConfigurationPolicy | None = None) -> None:
    InMemoryConfigurationPolicyRepository(store).seed_if_missing((policy or _policy(),))


class _FakeAristaAdapter:
    vendor_id: str = VendorType.ARISTA_EOS

    def parse(self, raw_text: str) -> NormalizedConfiguration:
        return NormalizedConfiguration(
            hostname="arista-1",
            interfaces=(),
            routing=NormalizedRouting(bgp_neighbors=()),
            acls=(),
        )


class _SpyAdapter:
    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped
        self.vendor_id = wrapped.vendor_id
        self.calls: list[str] = []

    def parse(self, raw_text: str) -> Any:
        self.calls.append(raw_text)
        return self._wrapped.parse(raw_text)


class _StubDeviceRepository:
    """Always reports no existing device and silently discards ``save`` —
    used only to force ``ConfigurationSnapshotRepository.add`` to see a
    missing device and raise ``ReferencedDeviceNotFoundError`` for real,
    a scenario the real orchestration order otherwise never produces."""

    def get_by_id(self, device_id: str) -> None:
        return None

    def save(self, device: object) -> None:
        pass


class _ReferencedDeviceNotFoundUnitOfWork:
    def __init__(self, store: InMemoryStore) -> None:
        self.devices = _StubDeviceRepository()
        self.configuration_snapshots = InMemoryConfigurationSnapshotRepository(store)
        self.configuration_policies = InMemoryConfigurationPolicyRepository(store)
        self.incidents = InMemoryIncidentRepository(store)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FailingCommitUnitOfWork:
    def __init__(self, store: InMemoryStore, error: Exception) -> None:
        self.devices = InMemoryDeviceRepository(store)
        self.configuration_snapshots = InMemoryConfigurationSnapshotRepository(store)
        self.configuration_policies = InMemoryConfigurationPolicyRepository(store)
        self.incidents = InMemoryIncidentRepository(store)
        self._error = error

    def commit(self) -> None:
        raise self._error

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


def _test_app(
    *,
    store: InMemoryStore | None = None,
    clock: object = lambda: T0,
    snapshot_id_factory: object = lambda: "snap-1",
    adapter_registry: AdapterRegistry | None = None,
    unit_of_work_factory: object = None,
    seed_on_startup: bool = False,
) -> TestClient:
    store = store if store is not None else InMemoryStore()
    uow_factory = unit_of_work_factory or (lambda: InMemoryUnitOfWork(store))
    app = create_app(
        unit_of_work_factory=uow_factory,
        clock=clock,
        snapshot_id_factory=snapshot_id_factory,
        # Defaults to the real production composition (Gate 8A-C) rather
        # than a hand-duplicated registry literal, so a test that submits a
        # vendor without passing its own adapter_registry exercises exactly
        # what production actually resolves.
        adapter_registry=adapter_registry or build_production_adapter_registry(),
        seed_on_startup=seed_on_startup,
    )
    return TestClient(app)


# --- Success --------------------------------------------------------------


def test_submit_configuration__successful_ingestion__returns_201() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["device_id"] == DEVICE_ID
    assert body["snapshot_id"] == "snap-1"
    assert body["violations_detected"] == 0
    assert body["incidents_created"] == 0
    assert body["incidents_updated"] == 0


def test_submit_configuration__response_contains_only_approved_fields() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert set(response.json().keys()) == {
        "device_id",
        "snapshot_id",
        "normalized_config",
        "violations_detected",
        "incidents_created",
        "incidents_updated",
    }


def test_submit_configuration__path_device_id_is_authoritative() -> None:
    client = _test_app()

    response = client.post(
        "/devices/other-device/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 201
    assert response.json()["device_id"] == "other-device"


def test_submit_configuration__missing_acl__returns_violation_and_created_incident() -> None:
    store = InMemoryStore()
    _seed_policy(store)
    client = _test_app(store=store)

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["violations_detected"] == 1
    assert body["incidents_created"] == 1
    assert body["incidents_updated"] == 0


def test_submit_configuration__repeated_submission__returns_incidents_updated_not_created() -> None:
    store = InMemoryStore()
    _seed_policy(store)
    client = _test_app(store=store, snapshot_id_factory=lambda: "snap-1")
    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )
    client2 = _test_app(store=store, snapshot_id_factory=lambda: "snap-2", clock=lambda: T0)

    response = client2.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["incidents_created"] == 0
    assert body["incidents_updated"] == 1


# --- Pre-persistence / service invocation ----------------------------------


def test_submit_configuration__exact_raw_text_reaches_the_service() -> None:
    spy = _SpyAdapter(CiscoAdapter())
    client = _test_app(adapter_registry=AdapterRegistry([spy]))
    raw_text = _SATISFIED_RAW_CONFIG + " \n"

    client.post(
        f"/devices/{DEVICE_ID}/config", json={"vendor": "cisco-ios-xe", "raw_config_text": raw_text}
    )

    assert spy.calls == [raw_text]


def test_submit_configuration__service_called_exactly_once() -> None:
    spy = _SpyAdapter(CiscoAdapter())
    client = _test_app(adapter_registry=AdapterRegistry([spy]))

    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert len(spy.calls) == 1


def test_submit_configuration__clock_called_exactly_once() -> None:
    calls: list[int] = []

    def spy_clock() -> datetime:
        calls.append(1)
        return T0

    client = _test_app(clock=spy_clock)

    client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert len(calls) == 1


# --- Normalized-configuration serialization --------------------------------


def test_submit_configuration__normalized_configuration_fully_serialized() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    normalized = response.json()["normalized_config"]
    assert normalized["hostname"] == "spine-01"
    assert normalized["interfaces"] == [
        {
            "name": "GigabitEthernet0/1",
            "description": None,
            "ip_address": None,
            "mtu": None,
            "admin_state": "up",
            "acl_in": "ACL-EXTERNAL-IN",
            "acl_out": None,
        }
    ]
    assert normalized["routing"] == {"bgp_neighbors": []}
    assert normalized["acls"] == [
        {
            "name": "ACL-EXTERNAL-IN",
            "entries": [
                {
                    "sequence": 10,
                    "action": "permit",
                    "protocol": "ip",
                    "source": "any",
                    "destination": "any",
                }
            ],
        }
    ]
    assert "static_routes" not in normalized["routing"]


def test_submit_configuration__nullable_fields_serialize_as_null() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    interface = response.json()["normalized_config"]["interfaces"][0]
    assert interface["description"] is None
    assert interface["ip_address"] is None
    assert interface["mtu"] is None
    assert interface["acl_out"] is None


def test_submit_configuration__enum_values_serialize_as_strings() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    normalized = response.json()["normalized_config"]
    assert normalized["interfaces"][0]["admin_state"] == "up"
    assert normalized["acls"][0]["entries"][0]["action"] == "permit"


def test_submit_configuration__tuple_ordering_is_preserved() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MULTI_INTERFACE_RAW_CONFIG},
    )

    normalized = response.json()["normalized_config"]
    assert [i["name"] for i in normalized["interfaces"]] == [
        "GigabitEthernet0/1",
        "GigabitEthernet0/2",
    ]
    assert [n["neighbor_ip"] for n in normalized["routing"]["bgp_neighbors"]] == [
        "10.0.0.1",
        "10.0.0.2",
    ]


# --- Multi-vendor ingestion (Gate 8A-C) ------------------------------------


def test_submit_configuration__arista_vendor__traverses_pipeline_with_zero_incidents() -> None:
    """Proves ``arista-eos`` traverses the exact same HTTP ingestion
    contract as Cisco, through the real production adapter registry (no
    explicit ``adapter_registry`` override — this exercises exactly what
    ``build_production_adapter_registry`` resolves): same success status,
    same response shape, real EOS-derived normalized values. No seeded
    policy applies to "leaf-02" yet (that is Gate 8A-D's scope), so this
    asserts the current, correct zero-incident outcome — it does not claim
    to prove Arista incident creation."""
    client = _test_app()

    response = client.post(
        "/devices/leaf-02/config",
        json={
            "vendor": "arista-eos",
            "raw_config_text": _load_arista_fixture("arista_required_acl_assigned.txt"),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["device_id"] == "leaf-02"

    normalized = body["normalized_config"]
    assert normalized["hostname"] == "leaf-02"

    eth1 = next(i for i in normalized["interfaces"] if i["name"] == "Ethernet1")
    assert eth1["ip_address"] == "10.0.1.1/30"
    assert eth1["admin_state"] == "up"
    assert eth1["acl_in"] == "ACL-EXTERNAL-IN"
    assert eth1["acl_out"] == "ACL-EXTERNAL-OUT"

    acl_in = next(a for a in normalized["acls"] if a["name"] == "ACL-EXTERNAL-IN")
    assert acl_in["entries"] == [
        {
            "sequence": 10,
            "action": "permit",
            "protocol": "ip",
            "source": "any",
            "destination": "any",
        },
        {
            "sequence": 20,
            "action": "deny",
            "protocol": "ip",
            "source": "any",
            "destination": "any",
        },
    ]

    assert normalized["routing"]["bgp_neighbors"] == [
        {"neighbor_ip": "10.0.1.2", "remote_as": 65001},
    ]

    assert body["violations_detected"] == 0
    assert body["incidents_created"] == 0
    assert body["incidents_updated"] == 0


# --- Request validation ------------------------------------------------------


def test_submit_configuration__blank_vendor__returns_422() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "   ", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 422


def test_submit_configuration__empty_raw_config_text__returns_422() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config", json={"vendor": "cisco-ios-xe", "raw_config_text": ""}
    )

    assert response.status_code == 422


def test_submit_configuration__whitespace_containing_raw_text__is_preserved() -> None:
    spy = _SpyAdapter(CiscoAdapter())
    client = _test_app(adapter_registry=AdapterRegistry([spy]))
    raw_text = "hostname spine-01\n   \n\tinterface GigabitEthernet0/1\n!\n"

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": raw_text},
    )

    assert response.status_code == 201
    assert spy.calls == [raw_text]


def test_submit_configuration__body_device_id__rejected() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={
            "vendor": "cisco-ios-xe",
            "raw_config_text": _SATISFIED_RAW_CONFIG,
            "device_id": "other",
        },
    )

    assert response.status_code == 422


def test_submit_configuration__body_observed_at__rejected() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={
            "vendor": "cisco-ios-xe",
            "raw_config_text": _SATISFIED_RAW_CONFIG,
            "observed_at": "2026-07-18T10:00:00Z",
        },
    )

    assert response.status_code == 422


def test_submit_configuration__unknown_body_field__rejected() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={
            "vendor": "cisco-ios-xe",
            "raw_config_text": _SATISFIED_RAW_CONFIG,
            "something_else": True,
        },
    )

    assert response.status_code == 422


# --- Error mapping ------------------------------------------------------------


def test_submit_configuration__unsupported_vendor__returns_422_unsupported_vendor() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "juniper-junos", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "unsupported_vendor"
    assert set(body.keys()) == {"code", "detail"}


def test_submit_configuration__parse_failure__returns_422_configuration_parse_error() -> None:
    client = _test_app()

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _NO_HOSTNAME_RAW_CONFIG},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "configuration_parse_error"
    assert "hostname" in body["detail"].lower()


def test_submit_configuration__parse_failure_with_line_number__includes_it_in_detail() -> None:
    client = _test_app()
    raw_text = (
        "hostname spine-01\n!\ninterface GigabitEthernet0/1\n"
        " ip address bogus 255.255.255.0\n!\n"
    )

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": raw_text},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "configuration_parse_error"
    assert "line 4" in body["detail"]


def test_submit_configuration__device_conflict__returns_409_device_conflict() -> None:
    store = InMemoryStore()
    registry = AdapterRegistry([CiscoAdapter(), _FakeAristaAdapter()])
    client1 = _test_app(
        store=store, adapter_registry=registry, snapshot_id_factory=lambda: "snap-1"
    )
    client1.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )
    client2 = _test_app(
        store=store, adapter_registry=registry, snapshot_id_factory=lambda: "snap-2"
    )

    response = client2.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "arista-eos", "raw_config_text": "hostname arista-1\n"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "device_conflict"


def test_submit_configuration__duplicate_snapshot__returns_409_snapshot_already_exists() -> None:
    store = InMemoryStore()
    client1 = _test_app(store=store, snapshot_id_factory=lambda: "dup-1")
    client1.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )
    client2 = _test_app(store=store, snapshot_id_factory=lambda: "dup-1")

    response = client2.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _MISSING_ACL_RAW_CONFIG},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "snapshot_already_exists"


def test_submit_configuration__referenced_device_not_found__returns_409() -> None:
    store = InMemoryStore()
    client = _test_app(
        store=store, unit_of_work_factory=lambda: _ReferencedDeviceNotFoundUnitOfWork(store)
    )

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "referenced_device_not_found"


def test_submit_configuration__invalid_generated_snapshot_id__returns_422_invalid_request() -> None:
    client = _test_app(snapshot_id_factory=lambda: "   ")

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"


def test_submit_configuration__persistence_failure__returns_generic_500() -> None:
    store = InMemoryStore()
    error = PersistenceError("underlying database detail that must not leak")
    client = _test_app(
        store=store, unit_of_work_factory=lambda: _FailingCommitUnitOfWork(store, error)
    )

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "persistence_error"
    assert "underlying database detail" not in body["detail"]


def test_submit_configuration__serialization_failure__returns_generic_500() -> None:
    store = InMemoryStore()
    error = SerializationError("malformed stored JSON detail that must not leak")
    client = _test_app(
        store=store, unit_of_work_factory=lambda: _FailingCommitUnitOfWork(store, error)
    )

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "serialization_error"
    assert "malformed stored JSON detail" not in body["detail"]


def test_submit_configuration__unexpected_exception__returns_generic_production_500() -> None:
    class _BoomUnitOfWork:
        def __init__(self, store: InMemoryStore) -> None:
            self.devices = InMemoryDeviceRepository(store)
            self.configuration_snapshots = InMemoryConfigurationSnapshotRepository(store)
            self.configuration_policies = InMemoryConfigurationPolicyRepository(store)
            self.incidents = InMemoryIncidentRepository(store)

        def commit(self) -> None:
            raise RuntimeError("boom")

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: _BoomUnitOfWork(store),
        clock=lambda: T0,
        snapshot_id_factory=lambda: "snap-1",
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 500


def test_submit_configuration__invalid_clock__returns_generic_500_and_persists_nothing() -> None:
    store = InMemoryStore()

    def naive_clock() -> datetime:
        return datetime(2026, 7, 18, 10, 0, 0)

    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=naive_clock,
        snapshot_id_factory=lambda: "snap-1",
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post(
        f"/devices/{DEVICE_ID}/config",
        json={"vendor": "cisco-ios-xe", "raw_config_text": _SATISFIED_RAW_CONFIG},
    )

    assert response.status_code == 500
    assert InMemoryUnitOfWork(store).devices.get_by_id(DEVICE_ID) is None
