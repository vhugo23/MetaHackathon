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
        from meta_rne.persistence.memory.policy_repository import (
            InMemoryConfigurationPolicyRepository,
        )
        from meta_rne.persistence.memory.snapshot_repository import (
            InMemoryConfigurationSnapshotRepository,
        )
        from meta_rne.persistence.memory.store import InMemoryStore

        store = InMemoryStore()
        return SimpleNamespace(
            devices=InMemoryDeviceRepository(store),
            snapshots=InMemoryConfigurationSnapshotRepository(store),
            policies=InMemoryConfigurationPolicyRepository(store),
        )

    from meta_rne.persistence.sqlalchemy.device_repository import SqlAlchemyDeviceRepository
    from meta_rne.persistence.sqlalchemy.policy_repository import (
        SqlAlchemyConfigurationPolicyRepository,
    )
    from meta_rne.persistence.sqlalchemy.snapshot_repository import (
        SqlAlchemyConfigurationSnapshotRepository,
    )

    session = request.getfixturevalue("sqlalchemy_session")
    return SimpleNamespace(
        devices=SqlAlchemyDeviceRepository(session),
        snapshots=SqlAlchemyConfigurationSnapshotRepository(session),
        policies=SqlAlchemyConfigurationPolicyRepository(session),
    )
