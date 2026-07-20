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
"""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from meta_rne.adapters.cisco import CiscoAdapter
from meta_rne.adapters.registry import AdapterRegistry
from meta_rne.api.app import create_app
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from meta_rne.persistence.seeds import build_slice1_policies

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def test_startup__seed_on_startup_true__seeds_slice1_policy_before_serving() -> None:
    store = InMemoryStore()
    app = create_app(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        clock=lambda: T0,
        adapter_registry=AdapterRegistry([CiscoAdapter()]),
        seed_on_startup=True,
    )

    with TestClient(app):
        pass

    expected_policy_id = build_slice1_policies(T0)[0].policy_id
    verify_uow = InMemoryUnitOfWork(store)
    policies = verify_uow.configuration_policies.get_applicable_to_device(
        build_slice1_policies(T0)[0].applies_to
    )
    assert any(p.policy_id == expected_policy_id for p in policies)


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
    policies = verify_uow.configuration_policies.get_applicable_to_device(
        build_slice1_policies(T0)[0].applies_to
    )
    assert policies == ()
