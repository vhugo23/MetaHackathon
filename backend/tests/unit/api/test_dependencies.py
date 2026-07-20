"""Unit tests for ``seed_slice1_policies`` (Day 5B) — the startup
policy-seeding lifecycle, tested directly against a real
``InMemoryUnitOfWork`` without spinning up a FastAPI app.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from meta_rne.api.clock import InvalidClockError
from meta_rne.api.dependencies import (
    build_lazy_sqlalchemy_unit_of_work_factory,
    seed_slice1_policies,
)
from meta_rne.persistence.errors import PolicySeedConflictError
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from meta_rne.persistence.seeds import build_slice1_policies

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


@dataclass
class _LifecycleCounts:
    commit: int = 0
    rollback: int = 0
    close: int = 0


@dataclass
class _LifecycleSpyUnitOfWork:
    _wrapped: Any
    _counts: _LifecycleCounts
    _fail_rollback: Exception | None = None
    _fail_close: Exception | None = None
    devices: Any = field(init=False)
    configuration_snapshots: Any = field(init=False)
    configuration_policies: Any = field(init=False)
    incidents: Any = field(init=False)

    def __post_init__(self) -> None:
        self.devices = self._wrapped.devices
        self.configuration_snapshots = self._wrapped.configuration_snapshots
        self.configuration_policies = self._wrapped.configuration_policies
        self.incidents = self._wrapped.incidents

    def commit(self) -> None:
        self._counts.commit += 1
        self._wrapped.commit()

    def rollback(self) -> None:
        self._counts.rollback += 1
        if self._fail_rollback is not None:
            raise self._fail_rollback
        self._wrapped.rollback()

    def close(self) -> None:
        self._counts.close += 1
        if self._fail_close is not None:
            raise self._fail_close
        self._wrapped.close()


class _CountingFactory:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.call_count = 0

    def __call__(self) -> Any:
        self.call_count += 1
        return self._inner()


def test_seed_slice1_policies__seeds_the_exact_slice1_policy() -> None:
    store = InMemoryStore()

    seed_slice1_policies(lambda: InMemoryUnitOfWork(store), clock=lambda: T0)

    expected = build_slice1_policies(T0)
    verify_uow = InMemoryUnitOfWork(store)
    for policy in expected:
        found = verify_uow.configuration_policies.get_applicable_to_device(policy.applies_to)
        assert any(p.policy_id == policy.policy_id for p in found)


def test_seed_slice1_policies__second_call__is_idempotent() -> None:
    store = InMemoryStore()

    seed_slice1_policies(lambda: InMemoryUnitOfWork(store), clock=lambda: T0)
    seed_slice1_policies(lambda: InMemoryUnitOfWork(store), clock=lambda: T0)

    verify_uow = InMemoryUnitOfWork(store)
    policies = verify_uow.configuration_policies.get_applicable_to_device(
        build_slice1_policies(T0)[0].applies_to
    )
    assert len(policies) == 1


def test_seed_slice1_policies__semantic_conflict__raises_and_fails() -> None:
    store = InMemoryStore()
    conflicting_policy_id = build_slice1_policies(T0)[0].policy_id
    from meta_rne.domain.config import AclDirection
    from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity

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
    seeded_uow = InMemoryUnitOfWork(store)
    seeded_uow.configuration_policies.seed_if_missing((conflicting,))
    seeded_uow.commit()

    with pytest.raises(PolicySeedConflictError):
        seed_slice1_policies(lambda: InMemoryUnitOfWork(store), clock=lambda: T0)


def test_seed_slice1_policies__commit_called_exactly_once() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()

    seed_slice1_policies(
        lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts), clock=lambda: T0
    )

    assert counts.commit == 1
    assert counts.rollback == 0


def test_seed_slice1_policies__close_called_exactly_once() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()

    seed_slice1_policies(
        lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts), clock=lambda: T0
    )

    assert counts.close == 1


def test_seed_slice1_policies__invalid_clock__fails_before_persistence() -> None:
    store = InMemoryStore()
    factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))

    def naive_clock() -> datetime:
        return datetime(2026, 7, 18, 10, 0, 0)

    with pytest.raises(InvalidClockError):
        seed_slice1_policies(factory, clock=naive_clock)

    assert factory.call_count == 0


def _conflicting_policy() -> Any:
    from meta_rne.domain.config import AclDirection
    from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity

    return ConfigurationPolicy(
        policy_id=build_slice1_policies(T0)[0].policy_id,
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


def test_seed_slice1_policies__rollback_also_fails__original_exception_preserved() -> None:
    store = InMemoryStore()
    seeded_uow = InMemoryUnitOfWork(store)
    seeded_uow.configuration_policies.seed_if_missing((_conflicting_policy(),))
    seeded_uow.commit()
    counts = _LifecycleCounts()
    rollback_error = RuntimeError("rollback boom")

    with pytest.raises(PolicySeedConflictError) as exc_info:
        seed_slice1_policies(
            lambda: _LifecycleSpyUnitOfWork(
                InMemoryUnitOfWork(store), counts, _fail_rollback=rollback_error
            ),
            clock=lambda: T0,
        )

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("rollback also failed" in note for note in notes)
    assert counts.close == 1


def test_seed_slice1_policies__close_also_fails__original_exception_preserved() -> None:
    store = InMemoryStore()
    seeded_uow = InMemoryUnitOfWork(store)
    seeded_uow.configuration_policies.seed_if_missing((_conflicting_policy(),))
    seeded_uow.commit()
    counts = _LifecycleCounts()
    close_error = RuntimeError("close boom")

    with pytest.raises(PolicySeedConflictError) as exc_info:
        seed_slice1_policies(
            lambda: _LifecycleSpyUnitOfWork(
                InMemoryUnitOfWork(store), counts, _fail_close=close_error
            ),
            clock=lambda: T0,
        )

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("close also failed" in note for note in notes)
    assert counts.close == 1


def test_seed_slice1_policies__conflict__leaves_no_partial_seed_changes() -> None:
    store = InMemoryStore()
    conflicting_policy_id = build_slice1_policies(T0)[0].policy_id
    from meta_rne.domain.config import AclDirection
    from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity

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
    seeded_uow = InMemoryUnitOfWork(store)
    seeded_uow.configuration_policies.seed_if_missing((conflicting,))
    seeded_uow.commit()

    with pytest.raises(PolicySeedConflictError):
        seed_slice1_policies(lambda: InMemoryUnitOfWork(store), clock=lambda: T0)

    verify_uow = InMemoryUnitOfWork(store)
    policies = verify_uow.configuration_policies.get_applicable_to_device("a-different-device")
    assert len(policies) == 1
    assert policies[0] is not None


def test_lazy_sqlalchemy_unit_of_work_factory__no_database_url__raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    factory = build_lazy_sqlalchemy_unit_of_work_factory(None)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        factory()
