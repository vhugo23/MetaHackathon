"""Unit tests for ``ResolveIncidentService`` (Day 7A, Gate 7A-B) — mirrors
``ListIncidentsService``'s/``ConfigIngestionService``'s exception-preserving
``UnitOfWork`` lifecycle test style (Day 5A/5B), using focused hand-written
fakes/spies rather than a mocking library.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from meta_rne.application.errors import IncidentNotFoundError
from meta_rne.application.incident_resolution import ResolveIncidentService
from meta_rne.domain.config import AclDirection
from meta_rne.domain.incident import (
    Incident,
    IncidentSource,
    IncidentStatus,
    PolicyViolationIncidentEvidence,
    compute_fingerprint,
)
from meta_rne.domain.policy import Severity, ViolationType
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

DEVICE_ID = "spine-01"
RULE_REF = "policy-acl-external-in"
AFFECTED_RESOURCE = "interface:GigabitEthernet0/1:acl_in"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 18, 11, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 18, 12, 0, 0, tzinfo=UTC)
INCIDENT_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"

_FINGERPRINT = compute_fingerprint(
    DEVICE_ID, IncidentSource.POLICY_VIOLATION, RULE_REF, AFFECTED_RESOURCE
)


def _evidence() -> PolicyViolationIncidentEvidence:
    return PolicyViolationIncidentEvidence(
        source_snapshot_id="snap-1",
        violation_type=ViolationType.MISSING_REQUIRED_ACL,
        expected_acl_name="ACL-EXTERNAL-IN",
        actual_acl_name=None,
        interface_name="GigabitEthernet0/1",
        direction=AclDirection.IN,
    )


def _incident(**overrides: object) -> Incident:
    defaults: dict[str, object] = {
        "incident_id": INCIDENT_ID,
        "fingerprint": _FINGERPRINT,
        "device_id": DEVICE_ID,
        "source": IncidentSource.POLICY_VIOLATION,
        "rule_ref": RULE_REF,
        "affected_resource": AFFECTED_RESOURCE,
        "severity": Severity.MEDIUM,
        "status": IncidentStatus.OPEN,
        "evidence": _evidence(),
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
        "created_at": T0,
        "last_seen_at": T0,
        "occurrence_count": 1,
        "updated_at": T0,
        "resolved_at": None,
    }
    defaults.update(overrides)
    return Incident(**defaults)  # type: ignore[arg-type]


# --- Fakes/spies (mirrors tests/unit/application/test_incident_queries.py) --


@dataclass
class _RepoCalls:
    get_by_id: list[str] = field(default_factory=list)
    resolve: list[tuple[str, datetime]] = field(default_factory=list)


class _FakeIncidentsRepository:
    """Fully scripted fake: returns exactly the configured results and
    records every call, never touching a real store."""

    def __init__(
        self,
        calls: _RepoCalls,
        *,
        get_by_id_result: Incident | None,
        resolve_result: Incident | None = None,
        resolve_error: Exception | None = None,
    ) -> None:
        self._calls = calls
        self._get_by_id_result = get_by_id_result
        self._resolve_result = resolve_result
        self._resolve_error = resolve_error

    def get_by_id(self, incident_id: str) -> Incident | None:
        self._calls.get_by_id.append(incident_id)
        return self._get_by_id_result

    def resolve(self, incident_id: str, resolved_at: datetime) -> Incident | None:
        self._calls.resolve.append((incident_id, resolved_at))
        if self._resolve_error is not None:
            raise self._resolve_error
        return self._resolve_result

    def upsert_open_incident(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def list_all(self) -> tuple[Any, ...]:
        raise NotImplementedError


@dataclass
class _LifecycleCounts:
    commit: int = 0
    rollback: int = 0
    close: int = 0


@dataclass
class _LifecycleSpyUnitOfWork:
    _wrapped: Any
    _counts: _LifecycleCounts
    _fail_commit: Exception | None = None
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
        if self._fail_commit is not None:
            raise self._fail_commit
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


class _CountingClock:
    def __init__(self, value: datetime) -> None:
        self._value = value
        self.call_count = 0

    def now(self) -> datetime:
        self.call_count += 1
        return self._value


class _FailingClock:
    """Proves the already-RESOLVED no-op path never calls the clock at
    all — even a clock that would raise if invoked must never be reached."""

    def now(self) -> datetime:
        raise AssertionError("Clock.now() must not be called for an already-RESOLVED incident")


def _service(
    *,
    incidents: _FakeIncidentsRepository,
    clock: Any,
    counts: _LifecycleCounts | None = None,
    fail_commit: Exception | None = None,
    fail_rollback: Exception | None = None,
    fail_close: Exception | None = None,
) -> tuple[ResolveIncidentService, _LifecycleCounts]:
    counts = counts if counts is not None else _LifecycleCounts()
    store = InMemoryStore()

    def factory() -> Any:
        return _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store),
            counts,
            _fail_commit=fail_commit,
            _fail_rollback=fail_rollback,
            _fail_close=fail_close,
            _incidents_override=incidents,
        )

    return ResolveIncidentService(unit_of_work_factory=factory, clock=clock), counts


# --- 1. Existing OPEN incident -----------------------------------------------


def test_resolve__open_incident__calls_clock_exactly_once() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    service, _ = _service(incidents=repo, clock=clock)

    service.resolve(INCIDENT_ID)

    assert clock.call_count == 1


def test_resolve__open_incident__calls_repository_resolve_with_id_and_clock_value() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    service, _ = _service(incidents=repo, clock=clock)

    service.resolve(INCIDENT_ID)

    assert calls.resolve == [(INCIDENT_ID, T1)]


def test_resolve__open_incident__commits_exactly_once_and_never_rolls_back() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    service, counts = _service(incidents=repo, clock=clock)

    service.resolve(INCIDENT_ID)

    assert counts.commit == 1
    assert counts.rollback == 0
    assert counts.close == 1


def test_resolve__open_incident__returns_the_persisted_resolved_incident() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    service, _ = _service(incidents=repo, clock=clock)

    result = service.resolve(INCIDENT_ID)

    assert result is resolved_incident
    assert result.status is IncidentStatus.RESOLVED
    assert result.resolved_at == T1
    assert result.updated_at == T1


# --- 2. Existing RESOLVED incident -------------------------------------------


def test_resolve__already_resolved__returns_unchanged_incident() -> None:
    calls = _RepoCalls()
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T0, updated_at=T0)
    repo = _FakeIncidentsRepository(calls, get_by_id_result=resolved_incident)
    service, _ = _service(incidents=repo, clock=_FailingClock())

    result = service.resolve(INCIDENT_ID)

    assert result is resolved_incident
    assert result.resolved_at == T0
    assert result.updated_at == T0


def test_resolve__already_resolved__clock_never_called() -> None:
    calls = _RepoCalls()
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T0, updated_at=T0)
    repo = _FakeIncidentsRepository(calls, get_by_id_result=resolved_incident)
    clock = _CountingClock(T1)
    service, _ = _service(incidents=repo, clock=clock)

    service.resolve(INCIDENT_ID)

    assert clock.call_count == 0


def test_resolve__already_resolved__repository_resolve_never_called() -> None:
    calls = _RepoCalls()
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T0, updated_at=T0)
    repo = _FakeIncidentsRepository(calls, get_by_id_result=resolved_incident)
    service, _ = _service(incidents=repo, clock=_FailingClock())

    service.resolve(INCIDENT_ID)

    assert calls.resolve == []


def test_resolve__already_resolved__never_commits_and_closes_once() -> None:
    calls = _RepoCalls()
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T0, updated_at=T0)
    repo = _FakeIncidentsRepository(calls, get_by_id_result=resolved_incident)
    service, counts = _service(incidents=repo, clock=_FailingClock())

    service.resolve(INCIDENT_ID)

    assert counts.commit == 0
    assert counts.rollback == 0
    assert counts.close == 1


def test_resolve__already_resolved__even_with_a_clock_that_would_fail_it_is_never_called() -> None:
    # Demonstrates the no-op path is a true short-circuit: it never even
    # attempts to read the clock, proven by a clock that raises if called.
    calls = _RepoCalls()
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T0, updated_at=T0)
    repo = _FakeIncidentsRepository(calls, get_by_id_result=resolved_incident)
    service, _ = _service(incidents=repo, clock=_FailingClock())

    result = service.resolve(INCIDENT_ID)

    assert result is resolved_incident


# --- 3. Unknown incident ------------------------------------------------------


def test_resolve__unknown_incident__raises_incident_not_found_error() -> None:
    calls = _RepoCalls()
    repo = _FakeIncidentsRepository(calls, get_by_id_result=None)
    service, _ = _service(incidents=repo, clock=_FailingClock())

    with pytest.raises(IncidentNotFoundError) as exc_info:
        service.resolve(INCIDENT_ID)

    assert exc_info.value.incident_id == INCIDENT_ID


def test_resolve__unknown_incident__clock_and_repository_resolve_never_called() -> None:
    calls = _RepoCalls()
    repo = _FakeIncidentsRepository(calls, get_by_id_result=None)
    clock = _CountingClock(T1)
    service, _ = _service(incidents=repo, clock=clock)

    with pytest.raises(IncidentNotFoundError):
        service.resolve(INCIDENT_ID)

    assert clock.call_count == 0
    assert calls.resolve == []


def test_resolve__unknown_incident__no_commit_rollback_and_close_follow_convention() -> None:
    calls = _RepoCalls()
    repo = _FakeIncidentsRepository(calls, get_by_id_result=None)
    service, counts = _service(incidents=repo, clock=_FailingClock())

    with pytest.raises(IncidentNotFoundError):
        service.resolve(INCIDENT_ID)

    assert counts.commit == 0
    assert counts.rollback == 1
    assert counts.close == 1


# --- 4. Repository.resolve() returns None after the initial OPEN read -------


def test_resolve__repository_resolve_returns_none_after_open_read__raises_incident_not_found() -> (
    None
):
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    repo = _FakeIncidentsRepository(calls, get_by_id_result=open_incident, resolve_result=None)
    clock = _CountingClock(T1)
    service, counts = _service(incidents=repo, clock=clock)

    with pytest.raises(IncidentNotFoundError) as exc_info:
        service.resolve(INCIDENT_ID)

    assert exc_info.value.incident_id == INCIDENT_ID
    assert counts.commit == 0
    assert counts.rollback == 1
    assert counts.close == 1


# --- 5. Repository.resolve() returns an already-resolved incident (a ---------
# --- concurrent request won the race between get_by_id() and resolve()) -----


def test_resolve__concurrent_request_already_resolved_it__accepts_persisted_result() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    # Another client's resolve() committed first, with its own captured
    # Clock value (T0), between this service's get_by_id() and its own
    # resolve() call — the repository's own atomic follow-up lookup already
    # resolved this ambiguity (Gate 7A-A) and simply returns the true
    # persisted (already-RESOLVED) state.
    concurrently_resolved = _incident(status=IncidentStatus.RESOLVED, resolved_at=T0, updated_at=T0)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=concurrently_resolved
    )
    service, counts = _service(incidents=repo, clock=clock)

    result = service.resolve(INCIDENT_ID)

    assert result is concurrently_resolved
    assert clock.call_count == 1
    assert counts.commit == 1
    assert counts.rollback == 0
    assert counts.close == 1


# --- 6. Repository.resolve() failure ------------------------------------------


def test_resolve__repository_resolve_raises__original_exception_propagates() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolve_error = ValueError("cannot resolve incident: persisted status is 'ACKNOWLEDGED'")
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_error=resolve_error
    )
    clock = _CountingClock(T1)
    service, counts = _service(incidents=repo, clock=clock)

    with pytest.raises(ValueError, match="ACKNOWLEDGED") as exc_info:
        service.resolve(INCIDENT_ID)

    assert exc_info.value is resolve_error
    assert counts.commit == 0
    assert counts.rollback == 1
    assert counts.close == 1


# --- 7. Commit failure ---------------------------------------------------------


def test_resolve__commit_fails__original_commit_error_propagates_without_second_commit() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    commit_error = RuntimeError("commit boom")
    service, counts = _service(incidents=repo, clock=clock, fail_commit=commit_error)

    with pytest.raises(RuntimeError, match="commit boom") as exc_info:
        service.resolve(INCIDENT_ID)

    assert exc_info.value is commit_error
    assert counts.commit == 1
    assert counts.rollback == 1
    assert counts.close == 1


def test_resolve__rollback_also_fails_after_commit_failure__original_exception_preserved() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    commit_error = RuntimeError("commit boom")
    rollback_error = RuntimeError("rollback boom")
    service, counts = _service(
        incidents=repo, clock=clock, fail_commit=commit_error, fail_rollback=rollback_error
    )

    with pytest.raises(RuntimeError, match="commit boom") as exc_info:
        service.resolve(INCIDENT_ID)

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("rollback also failed" in note for note in notes)
    assert counts.close == 1


def test_resolve__close_also_fails_after_commit_failure__original_exception_preserved() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    commit_error = RuntimeError("commit boom")
    close_error = RuntimeError("close boom")
    service, counts = _service(
        incidents=repo, clock=clock, fail_commit=commit_error, fail_close=close_error
    )

    with pytest.raises(RuntimeError, match="commit boom") as exc_info:
        service.resolve(INCIDENT_ID)

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("close also failed" in note for note in notes)
    assert counts.close == 1


# --- Creates exactly one UnitOfWork per call ---------------------------------


class _CountingFactory:
    def __init__(self, inner: Callable[[], Any]) -> None:
        self._inner = inner
        self.call_count = 0

    def __call__(self) -> Any:
        self.call_count += 1
        return self._inner()


def test_resolve__creates_exactly_one_unit_of_work() -> None:
    calls = _RepoCalls()
    open_incident = _incident(status=IncidentStatus.OPEN)
    resolved_incident = _incident(status=IncidentStatus.RESOLVED, resolved_at=T1, updated_at=T1)
    clock = _CountingClock(T1)
    repo = _FakeIncidentsRepository(
        calls, get_by_id_result=open_incident, resolve_result=resolved_incident
    )
    counts = _LifecycleCounts()
    store = InMemoryStore()
    factory = _CountingFactory(
        lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts, _incidents_override=repo)
    )
    service = ResolveIncidentService(unit_of_work_factory=factory, clock=clock)

    service.resolve(INCIDENT_ID)

    assert factory.call_count == 1
