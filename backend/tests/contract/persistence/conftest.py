"""Shared repository-conformance fixture for Day 4B2 contract tests.

One ``repositories`` fixture, parameterized over the in-memory and
SQLAlchemy implementations, so every test in this directory runs against
both without duplicating test bodies (test-strategy.md Section 9). The
SQLAlchemy param is marked ``postgres`` so ``pytest -m "not postgres"``
only exercises the in-memory side, and ``pytest -m postgres`` only the
SQLAlchemy side against a real, isolated PostgreSQL transaction
(``sqlalchemy_session``, see ``tests/conftest.py``).

For the in-memory param, all three repositories share one
``InMemoryStore`` so cross-repository reference checks (a snapshot must
reference an existing device; a device's snapshot references must
reference an existing snapshot) are enforced the same way PostgreSQL's
foreign keys enforce them.
"""

from collections.abc import Callable
from types import SimpleNamespace

import pytest


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("sqlalchemy", id="sqlalchemy", marks=pytest.mark.postgres),
    ]
)
def repositories(request: pytest.FixtureRequest) -> SimpleNamespace:
    if request.param == "memory":
        from meta_rne.persistence.memory.device_repository import InMemoryDeviceRepository
        from meta_rne.persistence.memory.incident_repository import InMemoryIncidentRepository
        from meta_rne.persistence.memory.policy_repository import (
            InMemoryConfigurationPolicyRepository,
        )
        from meta_rne.persistence.memory.snapshot_repository import (
            InMemoryConfigurationSnapshotRepository,
        )
        from meta_rne.persistence.memory.store import InMemoryStore

        store = InMemoryStore()

        def make_incidents(id_factory: Callable[[], str]) -> InMemoryIncidentRepository:
            return InMemoryIncidentRepository(store, incident_id_factory=id_factory)

        return SimpleNamespace(
            devices=InMemoryDeviceRepository(store),
            snapshots=InMemoryConfigurationSnapshotRepository(store),
            policies=InMemoryConfigurationPolicyRepository(store),
            incidents=InMemoryIncidentRepository(store),
            make_incidents=make_incidents,
        )

    from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
    from meta_rne.persistence.sqlalchemy.incident_repository import SqlAlchemyIncidentRepository
    from meta_rne.persistence.sqlalchemy.policy_repository import (
        SqlAlchemyConfigurationPolicyRepository,
    )
    from meta_rne.persistence.sqlalchemy.snapshot_repository import (
        SqlAlchemyConfigurationSnapshotRepository,
    )

    session = request.getfixturevalue("sqlalchemy_session")

    def make_incidents_sqlalchemy(id_factory: Callable[[], str]) -> SqlAlchemyIncidentRepository:
        return SqlAlchemyIncidentRepository(session, incident_id_factory=id_factory)

    return SimpleNamespace(
        devices=SqlAlchemyDeviceRepository(session),
        snapshots=SqlAlchemyConfigurationSnapshotRepository(session),
        policies=SqlAlchemyConfigurationPolicyRepository(session),
        incidents=SqlAlchemyIncidentRepository(session),
        make_incidents=make_incidents_sqlalchemy,
    )


@pytest.fixture(
    params=[
        pytest.param("memory", id="memory"),
        pytest.param("sqlalchemy", id="sqlalchemy", marks=pytest.mark.postgres),
    ]
)
def unit_of_work_factory(request: pytest.FixtureRequest) -> Callable[[], object]:
    """A zero-arg callable that builds a fresh UnitOfWork against shared
    committed state for this one test — one ``InMemoryStore``/session_factory
    per test, so a second call proves what a *new* UnitOfWork observes after
    the first one commits/rolls back/closes (test-strategy.md Section 9)."""
    if request.param == "memory":
        from meta_rne.persistence.memory.store import InMemoryStore
        from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

        committed_store = InMemoryStore()

        return lambda: InMemoryUnitOfWork(committed_store)

    from meta_rne.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

    session_factory = request.getfixturevalue("sqlalchemy_session_factory")

    return lambda: SqlAlchemyUnitOfWork(session_factory)
