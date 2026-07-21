"""App-level proof that ``create_app(seed_on_startup=True)`` actually wires
``seed_slice1_policies`` into the real FastAPI lifespan (Day 5B) — the
lifecycle/idempotency guarantees themselves are proven directly against
``seed_slice1_policies`` in ``tests/unit/api/test_dependencies.py``; this
file only proves the lifespan wiring.

Lifespan only runs when ``TestClient`` is used as a context manager
(``with TestClient(app) as client:``) — a bare ``TestClient(app)`` (as the
shared ``client`` fixture in ``tests/conftest.py`` uses for ``/health``)
never triggers it, which is why ordinary contract tests in this directory
pass ``seed_on_startup=False`` and never need to worry about it.

Day 8A-D: ``build_slice1_policies`` now returns two exact-match,
device-specific policies (spine-01/Cisco, leaf-02/Arista) — both are
asserted here as what startup seeding actually persists. No wildcard
applicability is asserted or introduced anywhere in this file.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from meta_rne.adapters.arista import AristaAdapter
from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from meta_rne.persistence.seeds import build_slice1_policies

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)

_ARISTA_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "configs" / "arista"


def _load_arista_fixture(name: str) -> str:
    return (_ARISTA_FIXTURES_DIR / name).read_text()


def test_startup__seed_on_startup_true__seeds_both_slice1_policies_before_serving() -> None:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=True,
    )

    with TestClient(app):
        pass

    expected = build_slice1_policies(T0)
    verify_uow = InMemoryUnitOfWork(store)
    for policy in expected:
        found = verify_uow.configuration_policies.get_applicable_to_device(policy.applies_to)
        assert any(p.policy_id == policy.policy_id for p in found)


def test_startup__seed_on_startup_true__each_device_receives_only_its_own_policy() -> None:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=True,
    )

    with TestClient(app):
        pass

    expected = build_slice1_policies(T0)
    verify_uow = InMemoryUnitOfWork(store)

    spine_policies = verify_uow.configuration_policies.get_applicable_to_device("spine-01")
    leaf_policies = verify_uow.configuration_policies.get_applicable_to_device("leaf-02")

    assert spine_policies == (expected[0],)
    assert leaf_policies == (expected[1],)
    assert spine_policies[0].policy_id == "policy-acl-external-in"
    assert leaf_policies[0].policy_id == "policy-acl-external-in-leaf-02"

    # No wildcard: neither device receives the other device's policy.
    assert not any(p.applies_to == "leaf-02" for p in spine_policies)
    assert not any(p.applies_to == "spine-01" for p in leaf_policies)


def test_startup__seed_on_startup_true__second_startup_is_idempotent_with_no_duplicates() -> None:
    store = InMemoryStore()

    def build_app() -> Any:
        return create_app(
            unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
            clock=lambda: T0,
            adapter_registry=AdapterRegistry([CiscoAdapter()]),
            seed_on_startup=True,
        )

    with TestClient(build_app()):
        pass
    with TestClient(build_app()):
        pass

    verify_uow = InMemoryUnitOfWork(store)
    expected_ids = {p.policy_id for p in build_slice1_policies(T0)}
    for device_id in ("spine-01", "leaf-02"):
        policies = verify_uow.configuration_policies.get_applicable_to_device(device_id)
        matching_ids = [p.policy_id for p in policies if p.policy_id in expected_ids]
        assert len(matching_ids) == 1


def test_startup__seed_on_startup_false__does_not_seed() -> None:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=False,
    )

    with TestClient(app):
        pass

    verify_uow = InMemoryUnitOfWork(store)
    assert verify_uow.configuration_policies.get_applicable_to_device("spine-01") == ()
    assert verify_uow.configuration_policies.get_applicable_to_device("leaf-02") == ()


def test_startup__arista_missing_acl_submission__creates_open_incident_for_leaf02() -> None:
    """Submits the Arista missing-required-ACL fixture for leaf-02 through
    a startup-seeded app (both policies present, real AristaAdapter
    registered) and proves the existing successful-response contract plus
    a real, persisted OPEN incident tied to leaf-02/Ethernet1."""
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        snapshot_id_factory=lambda: "snap-leaf-02",
        adapter_registry=AdapterRegistry([CiscoAdapter(), AristaAdapter()]),
        seed_on_startup=True,
    )

    with TestClient(app) as client:
        response = client.post(
            "/devices/leaf-02/config",
            json={
                "vendor": "arista-eos",
                "raw_config_text": _load_arista_fixture("arista_missing_required_acl.txt"),
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["device_id"] == "leaf-02"
        assert body["violations_detected"] == 1
        assert body["incidents_created"] == 1
        assert body["incidents_updated"] == 0

        incidents_response = client.get("/incidents")
        assert incidents_response.status_code == 200
        incidents = incidents_response.json()
        leaf_incident = next(i for i in incidents if i["device_id"] == "leaf-02")
        assert leaf_incident["status"] == "OPEN"
        assert leaf_incident["rule_ref"] == "policy-acl-external-in-leaf-02"
        assert "Ethernet1" in leaf_incident["affected_resource"]
        assert leaf_incident["severity"] == "Medium"
        assert leaf_incident["evidence"]["expected_acl_name"] == "ACL-EXTERNAL-IN"
        assert leaf_incident["evidence"]["direction"] == "in"
