"""Unit tests for ``ListIncidentsService`` (Day 5B) against a real
``InMemoryUnitOfWork`` — mirrors ``ConfigIngestionService``'s
exception-preserving lifecycle test style (Day 5A).
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from meta_rne.application.incident_queries import ListIncidentsService
from meta_rne.domain.config import AclDirection, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.incident import (
    IncidentCandidate,
    IncidentSource,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.persistence.memory.incident_repository import InMemoryIncidentRepository
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
DEVICE_ID = "spine-01"


def _candidate(rule_ref: str = "policy-a") -> IncidentCandidate:
    return IncidentCandidate(
        device_id=DEVICE_ID,
        source=IncidentSource.POLICY_VIOLATION,
        rule_ref=rule_ref,
        affected_resource="interface:GigabitEthernet0/1:acl_in",
        severity=Severity.MEDIUM,
        evidence=PolicyViolationIncidentEvidence(
            source_snapshot_id="snap-1",
            violation_type=ViolationType.MISSING_REQUIRED_ACL,
            expected_acl_name="ACL-EXTERNAL-IN",
            actual_acl_name=None,
            interface_name="GigabitEthernet0/1",
            direction=AclDirection.IN,
        ),
        recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        observed_at=T0,
    )


def _seed_incident(store: InMemoryStore, rule_ref: str = "policy-a") -> None:
    store.devices[DEVICE_ID] = Device(
        device_id=DEVICE_ID,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )
    candidate = _candidate(rule_ref)
    fingerprint = compute_fingerprint(
        candidate.device_id, candidate.source, candidate.rule_ref, candidate.affected_resource
    )
    InMemoryIncidentRepository(store).upsert_open_incident(candidate, fingerprint, T0)


@dataclass
class _LifecycleCounts:
    commit: int = 0
    rollback: int = 0
    close: int = 0


class _FailingIncidentsRepository:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def list_all(self) -> tuple[Any, ...]:
        raise self._error

    def get_by_id(self, incident_id: str) -> None:
        return None

    def upsert_open_incident(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


@dataclass
class _LifecycleSpyUnitOfWork:
    _wrapped: Any
    _counts: _LifecycleCounts
    _fail_rollback: Exception | None = None
    _fail_close: Exception | None = None
    _incidents_override: Any = None
    devices: Any = field(init=False)
    configuration_snapshots: Any = field(init=False)
    configuration_policies: Any = field(init=False)
    incidents: Any = field(init=False)

    def __post_init__(self) -> None:
        self.devices = self._wrapped.devices
        self.configuration_snapshots = self._wrapped.configuration_snapshots
        self.configuration_policies = self._wrapped.configuration_policies
        self.incidents = self._incidents_override or self._wrapped.incidents

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


def test_list_incidents_service__empty_store__returns_empty_tuple() -> None:
    store = InMemoryStore()
    service = ListIncidentsService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    assert service.list_all() == ()


def test_list_incidents_service__populated_store__returns_all_incidents_in_order() -> None:
    store = InMemoryStore()
    _seed_incident(store, "policy-a")
    _seed_incident(store, "policy-b")
    service = ListIncidentsService(unit_of_work_factory=lambda: InMemoryUnitOfWork(store))

    incidents = service.list_all()

    assert len(incidents) == 2
    assert [i.created_at for i in incidents] == sorted(i.created_at for i in incidents)


def test_list_incidents_service__creates_exactly_one_unit_of_work() -> None:
    store = InMemoryStore()
    factory = _CountingFactory(lambda: InMemoryUnitOfWork(store))
    service = ListIncidentsService(unit_of_work_factory=factory)

    service.list_all()

    assert factory.call_count == 1


def test_list_incidents_service__never_commits() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    service = ListIncidentsService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.list_all()

    assert counts.commit == 0


def test_list_incidents_service__closes_exactly_once_after_success() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    service = ListIncidentsService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.list_all()

    assert counts.close == 1
    assert counts.rollback == 0


def test_list_incidents_service__read_failure__preserves_original_exception() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    read_error = RuntimeError("read boom")
    service = ListIncidentsService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store),
            counts,
            _incidents_override=_FailingIncidentsRepository(read_error),
        )
    )

    with pytest.raises(RuntimeError, match="read boom") as exc_info:
        service.list_all()

    assert exc_info.value is read_error
    assert counts.rollback == 1
    assert counts.close == 1


def test_list_incidents_service__rollback_also_fails__original_exception_preserved() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    read_error = RuntimeError("read boom")
    rollback_error = RuntimeError("rollback boom")
    service = ListIncidentsService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store),
            counts,
            _fail_rollback=rollback_error,
            _incidents_override=_FailingIncidentsRepository(read_error),
        )
    )

    with pytest.raises(RuntimeError, match="read boom") as exc_info:
        service.list_all()

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("rollback also failed" in note for note in notes)
    assert counts.close == 1


def test_list_incidents_service__close_also_fails__original_exception_preserved() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    read_error = RuntimeError("read boom")
    close_error = RuntimeError("close boom")
    service = ListIncidentsService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store),
            counts,
            _fail_close=close_error,
            _incidents_override=_FailingIncidentsRepository(read_error),
        )
    )

    with pytest.raises(RuntimeError, match="read boom") as exc_info:
        service.list_all()

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("close also failed" in note for note in notes)
    assert counts.close == 1
