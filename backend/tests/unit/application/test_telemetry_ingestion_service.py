"""Unit tests for ``TelemetryIngestionService`` (Gate F1) against a real
``InMemoryUnitOfWork`` — never a mocked repository (matching
``test_config_ingestion_service.py``'s convention). Proves the approved
provenance-tagged Flow F algorithm (Gate F0's plan, Sections 6/7/9): the
current sample always participates exactly once, historical context is
computed from the logical post-save retained state (watermark-derived
cutoff, no-upper-bound history read, cap-before-future-exclusion), and
exact-duplicate historical samples are never discarded by equality or
identity.
"""

from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.models import TelemetryIngestionCommand, TelemetryIngestionResult
from meta_rne.application.telemetry_ingestion import TelemetryIngestionService
from meta_rne.detection.anomaly_incident_mapper import AnomalyIncidentMapper
from meta_rne.domain.anomaly import RuleId
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.incident import IncidentStatus, IncidentUpsertOutcome, compute_fingerprint
from meta_rne.domain.policy import Severity
from meta_rne.domain.telemetry import (
    BgpSession,
    BgpState,
    InterfaceState,
    LinkState,
    TelemetrySample,
)
from meta_rne.observability import IncidentLogEvent
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

DEVICE_ID = "spine-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _device(device_id: str = DEVICE_ID) -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


def _sample(
    device_id: str = DEVICE_ID,
    sampled_at: datetime = T0,
    cpu: float = 50.0,
    memory: float = 50.0,
    error_rate: float = 0.0,
    interface_states: tuple[InterfaceState, ...] = (),
    bgp_sessions: tuple[BgpSession, ...] = (),
) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=cpu,
        memory_utilization_pct=memory,
        interface_error_rate=error_rate,
        interface_states=interface_states,
        bgp_sessions=bgp_sessions,
    )


def _command(
    device_id: str = DEVICE_ID,
    sample: TelemetrySample | None = None,
    observed_at: datetime = T0,
) -> TelemetryIngestionCommand:
    return TelemetryIngestionCommand(
        device_id=device_id,
        sample=sample if sample is not None else _sample(device_id=device_id),
        observed_at=observed_at,
    )


def _seed_device(store: InMemoryStore, device_id: str = DEVICE_ID) -> None:
    uow = InMemoryUnitOfWork(store)
    uow.devices.save(_device(device_id))
    uow.commit()


def _seed_sample(store: InMemoryStore, sample: TelemetrySample) -> None:
    uow = InMemoryUnitOfWork(store)
    uow.telemetry_samples.save(sample.device_id, sample)
    uow.commit()


def _make_service(
    store: InMemoryStore, *, incident_event_sink: Any = None
) -> TelemetryIngestionService:
    kwargs: dict[str, Any] = {"unit_of_work_factory": lambda: InMemoryUnitOfWork(store)}
    if incident_event_sink is not None:
        kwargs["incident_event_sink"] = incident_event_sink
    return TelemetryIngestionService(**kwargs)


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
    devices: Any = field(init=False)
    configuration_snapshots: Any = field(init=False)
    configuration_policies: Any = field(init=False)
    incidents: Any = field(init=False)
    telemetry_samples: Any = field(init=False)

    def __post_init__(self) -> None:
        self.devices = self._wrapped.devices
        self.configuration_snapshots = self._wrapped.configuration_snapshots
        self.configuration_policies = self._wrapped.configuration_policies
        self.incidents = self._wrapped.incidents
        self.telemetry_samples = self._wrapped.telemetry_samples

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


@dataclass
class _IncidentsSpy:
    """Wraps a real IncidentRepository, recording every upsert_open_incident
    call and optionally raising on a specific 1-indexed call — used only to
    prove atomicity/ordering, never to replace the real repository's
    dedup/persistence behavior (Gate H3)."""

    _wrapped: Any
    calls: list[tuple[Any, str, Any]] = field(default_factory=list)
    fail_on_call: int | None = None
    fail_error: Exception | None = None

    def upsert_open_incident(self, candidate: Any, fingerprint: str, observed_at: Any) -> Any:
        self.calls.append((candidate, fingerprint, observed_at))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            assert self.fail_error is not None
            raise self.fail_error
        return self._wrapped.upsert_open_incident(candidate, fingerprint, observed_at)

    def get_by_id(self, incident_id: str) -> Any:
        return self._wrapped.get_by_id(incident_id)

    def list_all(self) -> Any:
        return self._wrapped.list_all()

    def resolve(self, incident_id: str, resolved_at: Any) -> Any:
        return self._wrapped.resolve(incident_id, resolved_at)


def _make_service_with_incidents_spy(
    store: InMemoryStore,
    fail_on_call: int | None = None,
    fail_error: Exception | None = None,
    incident_event_sink: Any = None,
) -> tuple[TelemetryIngestionService, _IncidentsSpy]:
    spy = _IncidentsSpy(_wrapped=None, fail_on_call=fail_on_call, fail_error=fail_error)

    def factory() -> InMemoryUnitOfWork:
        uow = InMemoryUnitOfWork(store)
        spy._wrapped = uow.incidents
        uow.incidents = spy  # type: ignore[assignment]
        return uow

    kwargs: dict[str, Any] = {"unit_of_work_factory": factory}
    if incident_event_sink is not None:
        kwargs["incident_event_sink"] = incident_event_sink
    return TelemetryIngestionService(**kwargs), spy


@dataclass
class _RecordingSink:
    """A plain recording double for ``IncidentEventSink`` — structural
    typing only, matching test_config_ingestion_service.py's convention."""

    calls: list[IncidentLogEvent] = field(default_factory=list)

    def emit(self, event: IncidentLogEvent) -> None:
        self.calls.append(event)


@dataclass
class _CommitOrderRecordingSink:
    """Records the enclosing ``_LifecycleSpyUnitOfWork``'s commit/rollback
    counts at the exact moment each event is emitted, proving emission
    happens strictly after commit, never before."""

    counts: _LifecycleCounts
    snapshots: list[tuple[int, int]] = field(default_factory=list)

    def emit(self, event: IncidentLogEvent) -> None:
        self.snapshots.append((self.counts.commit, self.counts.rollback))


@dataclass
class _SelectivelyFailingSink:
    """Raises on the given zero-indexed ``emit`` calls, recording every
    attempted event (including the ones it raises for) so a test can prove
    later events are still attempted after an earlier one fails."""

    fail_on_indices: frozenset[int]
    attempted: list[IncidentLogEvent] = field(default_factory=list)
    succeeded: list[IncidentLogEvent] = field(default_factory=list)

    def emit(self, event: IncidentLogEvent) -> None:
        index = len(self.attempted)
        self.attempted.append(event)
        if index in self.fail_on_indices:
            raise RuntimeError(f"sink boom on event index {index}")
        self.succeeded.append(event)


@dataclass
class _FailingTelemetrySamplesSpy:
    """Wraps a real TelemetryRepository, raising on `save` only — used to
    prove no incident event is emitted when the telemetry save itself
    fails, without replacing the real repository's read behavior."""

    _wrapped: Any
    fail_error: Exception

    def get_latest(self, device_id: str) -> Any:
        return self._wrapped.get_latest(device_id)

    def get_recent(self, device_id: str, since: datetime) -> Any:
        return self._wrapped.get_recent(device_id, since=since)

    def save(self, device_id: str, sample: TelemetrySample) -> None:
        raise self.fail_error


class _RuleEngineSpy:
    """A narrow test seam over the real ``RuleEngine.evaluate`` call —
    records call args/count and can optionally raise, without replacing the
    real UnitOfWork or telemetry repository."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[datetime, list[TelemetrySample]]] = []
        self._fail = fail

    def evaluate(self, observed_at: datetime, recent_samples: list[TelemetrySample]) -> list[Any]:
        self.calls.append((observed_at, list(recent_samples)))
        if self._fail is not None:
            raise self._fail
        from meta_rne.detection.rule_engine import RuleEngine

        return RuleEngine.evaluate(observed_at, recent_samples)


# --- Missing-device behavior ----------------------------------------------


def test_missing_device__raises_device_not_found_error() -> None:
    store = InMemoryStore()
    service = _make_service(store)

    with pytest.raises(DeviceNotFoundError):
        service.ingest(_command())


def test_missing_device__performs_no_telemetry_write() -> None:
    store = InMemoryStore()
    service = _make_service(store)

    with pytest.raises(DeviceNotFoundError):
        service.ingest(_command())

    assert InMemoryUnitOfWork(store).telemetry_samples.get_latest(DEVICE_ID) is None


def test_missing_device__performs_no_rule_engine_evaluation() -> None:
    store = InMemoryStore()
    engine = _RuleEngineSpy()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    with pytest.raises(DeviceNotFoundError):
        service.ingest(_command())

    assert engine.calls == []


def test_missing_device__does_not_commit() -> None:
    store = InMemoryStore()
    counts = _LifecycleCounts()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    with pytest.raises(DeviceNotFoundError):
        service.ingest(_command())

    assert counts.commit == 0


# --- Successful ingestion ---------------------------------------------------


def test_success__zero_anomaly_ingestion__saves_the_exact_sample() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample()
    service = _make_service(store)

    service.ingest(_command(sample=sample))

    saved = InMemoryUnitOfWork(store).telemetry_samples.get_latest(DEVICE_ID)
    assert saved == sample


def test_success__returns_the_exact_sample_object() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample()
    service = _make_service(store)

    result = service.ingest(_command(sample=sample))

    assert result.sample is sample


def test_success__result_anomalies_is_a_tuple() -> None:
    store = InMemoryStore()
    _seed_device(store)
    service = _make_service(store)

    result = service.ingest(_command())

    assert isinstance(result, TelemetryIngestionResult)
    assert isinstance(result.anomalies, tuple)


def test_success__commits_exactly_once() -> None:
    store = InMemoryStore()
    _seed_device(store)
    counts = _LifecycleCounts()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.ingest(_command())

    assert counts.commit == 1
    assert counts.rollback == 0


def test_success__closes_exactly_once_after_commit() -> None:
    store = InMemoryStore()
    _seed_device(store)
    counts = _LifecycleCounts()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.ingest(_command())

    assert counts.close == 1


def test_success__later_unit_of_work_sees_retained_committed_telemetry() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample()
    service = _make_service(store)

    service.ingest(_command(sample=sample))

    later = InMemoryUnitOfWork(store)
    assert later.telemetry_samples.get_latest(DEVICE_ID) == sample


def test_success__command_sample_is_never_reconstructed_or_mutated() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample()
    command = _command(sample=sample)
    service = _make_service(store)

    result = service.ingest(command)

    assert command.sample is sample
    assert result.sample is sample


def test_success__rule_engine_receives_observed_at_exactly() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )
    observed_at = T0 + timedelta(minutes=3)

    service.ingest(_command(observed_at=observed_at))

    assert engine.calls[0][0] == observed_at


def test_success__rule_engine_called_exactly_once() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    service.ingest(_command())

    assert len(engine.calls) == 1


# --- Rule triggering through the service ------------------------------------


def test_cpu_high__can_be_triggered_through_the_service() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    service = _make_service(store)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    assert any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_link_flap__can_be_triggered_through_the_service() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    # 5 observations -> 4 transitions (LINK_FLAP requires >= 4): initial UP
    # is not itself a transition; DOWN/UP/DOWN/UP each toggle the state.
    states = [LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]
    for offset, state in enumerate(states):
        _seed_sample(
            store,
            _sample(
                sampled_at=T0 + timedelta(seconds=12 * offset),
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    service = _make_service(store)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=48),
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
            )
        )
    )

    assert any(a.rule_id is RuleId.LINK_FLAP for a in result.anomalies)


def test_bgp_down__can_be_triggered_through_the_service() -> None:
    store = InMemoryStore()
    _seed_device(store)
    neighbor = "10.0.0.2"
    _seed_sample(
        store,
        _sample(
            sampled_at=T0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    service = _make_service(store)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=30),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    assert any(a.rule_id is RuleId.BGP_DOWN for a in result.anomalies)


def test_anomaly_order__cpu_before_link_flap_before_bgp_down() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"

    # CPU_HIGH inspects the latest two *device* samples (all of them, not
    # just interface/bgp-bearing ones), so every seeded sample here also
    # carries cpu=95.0 to keep the trailing pair above threshold.
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            store,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    service = _make_service(store)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=50),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    rule_order = [a.rule_id for a in result.anomalies]
    assert rule_order.index(RuleId.CPU_HIGH) < rule_order.index(RuleId.LINK_FLAP)
    assert rule_order.index(RuleId.LINK_FLAP) < rule_order.index(RuleId.BGP_DOWN)


# --- Rollback / failure behavior --------------------------------------------


def test_rule_engine_exception__rolls_back_the_telemetry_save() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy(fail=RuntimeError("rule engine boom"))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    with pytest.raises(RuntimeError, match="rule engine boom"):
        service.ingest(_command())

    assert InMemoryUnitOfWork(store).telemetry_samples.get_latest(DEVICE_ID) is None


def test_rule_engine_exception__does_not_commit() -> None:
    store = InMemoryStore()
    _seed_device(store)
    counts = _LifecycleCounts()
    engine = _RuleEngineSpy(fail=RuntimeError("boom"))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts),
        rule_engine=engine,
    )

    with pytest.raises(RuntimeError):
        service.ingest(_command())

    assert counts.commit == 0
    assert counts.rollback == 1


def test_commit_failure__preserves_the_original_exception() -> None:
    store = InMemoryStore()
    _seed_device(store)
    counts = _LifecycleCounts()
    commit_error = RuntimeError("commit boom")
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store), counts, _fail_commit=commit_error
        )
    )

    with pytest.raises(RuntimeError, match="commit boom") as exc_info:
        service.ingest(_command())

    assert exc_info.value is commit_error
    assert counts.rollback == 1
    assert counts.close == 1


def test_rollback_failure__attached_as_note_without_replacing_original_error() -> None:
    store = InMemoryStore()
    _seed_device(store)
    counts = _LifecycleCounts()
    rollback_error = RuntimeError("rollback boom")
    engine = _RuleEngineSpy(fail=ValueError("processing boom"))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store), counts, _fail_rollback=rollback_error
        ),
        rule_engine=engine,
    )

    with pytest.raises(ValueError, match="processing boom") as exc_info:
        service.ingest(_command())

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("rollback also failed" in note for note in notes)


def test_close_failure__attached_as_note_without_replacing_original_error() -> None:
    store = InMemoryStore()
    _seed_device(store)
    counts = _LifecycleCounts()
    close_error = RuntimeError("close boom")
    engine = _RuleEngineSpy(fail=ValueError("processing boom"))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store), counts, _fail_close=close_error
        ),
        rule_engine=engine,
    )

    with pytest.raises(ValueError, match="processing boom") as exc_info:
        service.ingest(_command())

    notes = getattr(exc_info.value, "__notes__", [])
    assert any("close also failed" in note for note in notes)


def test_no_service_state_leaks_between_calls() -> None:
    store = InMemoryStore()
    _seed_device(store)
    service = _make_service(store)

    first = service.ingest(_command(sample=_sample(sampled_at=T0)))
    second = service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(minutes=1))))

    assert first.sample != second.sample


# --- Watermark and retention parity cases -----------------------------------


def test_late_sample__participates_and_may_be_pruned_in_memory() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0 + timedelta(minutes=20)))
    service = _make_service(store)
    late_sample = _sample(sampled_at=T0)

    result = service.ingest(_command(sample=late_sample))

    assert result.sample == late_sample
    # Expected, not a bug: retention may have already pruned it.
    latest = InMemoryUnitOfWork(store).telemetry_samples.get_latest(DEVICE_ID)
    assert latest is not None
    assert latest.sampled_at >= T0 + timedelta(minutes=20)


def test_future_stored_sample__excluded_from_rule_engine_input() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    _seed_sample(store, _sample(sampled_at=T0 + timedelta(minutes=30), cpu=95.0))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )
    current = _sample(sampled_at=T0 + timedelta(minutes=0), cpu=95.0)

    service.ingest(_command(sample=current, observed_at=T0))

    evaluated = engine.calls[0][1]
    assert current in evaluated
    assert all(s.sampled_at <= current.sampled_at for s in evaluated)


def test_cpu_high__previously_pruned_sample_does_not_leak_into_evaluation() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0 + timedelta(minutes=29), cpu=95.0))
    _seed_sample(store, _sample(sampled_at=T0 + timedelta(minutes=60)))
    service = _make_service(store)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(minutes=30), cpu=95.0))
    )

    assert not any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_cpu_high__current_sample_advancing_watermark_excludes_older_context() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    service = _make_service(store)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(minutes=20), cpu=95.0))
    )

    assert not any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_link_flap__pruned_prior_transitions_do_not_leak_into_evaluation() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(minutes=29),
            interface_states=(InterfaceState(name=interface, oper_state=LinkState.DOWN),),
        ),
    )
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(minutes=29, seconds=12),
            interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
        ),
    )
    _seed_sample(store, _sample(sampled_at=T0 + timedelta(minutes=60)))
    service = _make_service(store)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(minutes=30),
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.DOWN),),
            )
        )
    )

    assert not any(a.rule_id is RuleId.LINK_FLAP for a in result.anomalies)


def test_bgp_down__pruned_prior_established_state_does_not_leak_into_evaluation() -> None:
    store = InMemoryStore()
    _seed_device(store)
    neighbor = "10.0.0.2"
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(minutes=25),
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    _seed_sample(store, _sample(sampled_at=T0 + timedelta(minutes=60)))
    service = _make_service(store)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(minutes=40),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    bgp_anomalies = [a for a in result.anomalies if a.rule_id is RuleId.BGP_DOWN]
    assert bgp_anomalies == []


def test_more_than_100_candidate_entries__final_100_cap_applied() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    for i in range(150):
        _seed_sample(store, _sample(sampled_at=T0 + timedelta(seconds=i)))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=150))))

    evaluated = engine.calls[0][1]
    assert len(evaluated) <= 100


def test_100_future_observations_displace_late_current_sample__still_participates_alone() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    base = T0 + timedelta(hours=2)
    for i in range(100):
        _seed_sample(store, _sample(sampled_at=base - timedelta(seconds=i)))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )
    old_current = _sample(sampled_at=T0)

    result = service.ingest(_command(sample=old_current, observed_at=T0))

    evaluated = engine.calls[0][1]
    assert old_current in evaluated
    assert all(s.sampled_at <= old_current.sampled_at for s in evaluated)
    assert result.sample == old_current


def test_equal_timestamps_at_100_boundary__historical_order_preserved() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    for _ in range(5):
        _seed_sample(store, _sample(sampled_at=T0))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    current = _sample(sampled_at=T0)
    service.ingest(_command(sample=current, observed_at=T0))

    evaluated = engine.calls[0][1]
    assert evaluated[-1] == current
    assert len(evaluated) == 6


# --- Exact-duplicate and provenance cases -----------------------------------


def test_exact_duplicate__prior_plus_identical_current__triggers_cpu_high() -> None:
    store = InMemoryStore()
    _seed_device(store)
    duplicate = _sample(sampled_at=T0, cpu=95.0)
    _seed_sample(store, duplicate)
    service = _make_service(store)

    result = service.ingest(_command(sample=_sample(sampled_at=T0, cpu=95.0)))

    assert any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_exact_duplicate__two_prior_duplicates_plus_current__all_three_preserved() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    duplicate = _sample(sampled_at=T0)
    _seed_sample(store, duplicate)
    _seed_sample(store, duplicate)
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    service.ingest(_command(sample=_sample(sampled_at=T0)))

    evaluated = engine.calls[0][1]
    assert len(evaluated) == 3


def test_exact_duplicate__same_python_object_reused__prior_observation_not_erased() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    shared_sample = _sample(sampled_at=T0, cpu=95.0)
    _seed_sample(store, shared_sample)
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    result = service.ingest(_command(sample=shared_sample))

    evaluated = engine.calls[0][1]
    assert len(evaluated) == 2
    assert any(a.rule_id is RuleId.CPU_HIGH for a in result.anomalies)


def test_exact_duplicates_at_100_boundary__remain_independently_ordered() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    duplicate = _sample(sampled_at=T0)
    for _ in range(100):
        _seed_sample(store, duplicate)
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    service.ingest(_command(sample=_sample(sampled_at=T0)))

    evaluated = engine.calls[0][1]
    assert len(evaluated) == 100


def test_no_application_service_deduplication_occurs() -> None:
    """No deduplication of any kind occurs anywhere in
    ``TelemetryIngestionService`` — every historical entry returned by
    ``get_recent`` is independently represented in the evaluation input,
    even when field-equal to another entry or to the current sample."""
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy()
    duplicate = _sample(sampled_at=T0)
    _seed_sample(store, duplicate)
    _seed_sample(store, duplicate)
    _seed_sample(store, duplicate)
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store), rule_engine=engine
    )

    service.ingest(_command(sample=duplicate))

    evaluated = engine.calls[0][1]
    assert len(evaluated) == 4


# --- Gate H3: anomaly-to-incident integration --------------------------------
#
# AnomalyIncidentMapper and compute_fingerprint are used exactly as approved
# (Gate H2) — no severity/recommendation/affected_resource logic is
# duplicated here.


def _incidents(store: InMemoryStore) -> tuple[Any, ...]:
    return InMemoryUnitOfWork(store).incidents.list_all()


def test_no_anomaly__no_incident_upsert_occurs() -> None:
    store = InMemoryStore()
    _seed_device(store)
    service, spy = _make_service_with_incidents_spy(store)

    result = service.ingest(_command())

    assert result.anomalies == ()
    assert spy.calls == []
    assert _incidents(store) == ()


def test_no_anomaly__telemetry_sample_still_saves() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample()
    service, _ = _make_service_with_incidents_spy(store)

    service.ingest(_command(sample=sample))

    assert InMemoryUnitOfWork(store).telemetry_samples.get_latest(DEVICE_ID) == sample


def test_no_anomaly__commits_exactly_once() -> None:
    store = InMemoryStore()
    _seed_device(store)
    counts = _LifecycleCounts()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.ingest(_command())

    assert counts.commit == 1
    assert counts.rollback == 0


def test_no_anomaly__result_remains_sample_and_empty_anomalies() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample()
    service, _ = _make_service_with_incidents_spy(store)

    result = service.ingest(_command(sample=sample))

    assert result == TelemetryIngestionResult(sample=sample, anomalies=())


def test_one_cpu_anomaly__exactly_one_incident_upsert() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    service, spy = _make_service_with_incidents_spy(store)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    assert len(spy.calls) == 1
    assert len(result.anomalies) == 1


def test_one_cpu_anomaly__candidate_matches_mapper_output() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    service, spy = _make_service_with_incidents_spy(store)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    anomaly = result.anomalies[0]
    expected_candidate = AnomalyIncidentMapper.build_candidate(anomaly)
    candidate, fingerprint, observed_at = spy.calls[0]
    assert candidate == expected_candidate
    assert fingerprint == compute_fingerprint(
        expected_candidate.device_id,
        expected_candidate.source,
        expected_candidate.rule_ref,
        expected_candidate.affected_resource,
    )
    assert observed_at == candidate.observed_at


def test_one_cpu_anomaly__commits_exactly_once() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    counts = _LifecycleCounts()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))

    assert counts.commit == 1
    assert counts.rollback == 0


def test_one_cpu_anomaly__result_shape_unchanged() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    service, _ = _make_service_with_incidents_spy(store)

    result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )

    assert {f.name for f in fields(result)} == {"sample", "anomalies"}


def test_multiple_anomalies__exactly_three_upserts_in_rule_order() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            store,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    service, spy = _make_service_with_incidents_spy(store)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=50),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    assert len(spy.calls) == 3
    assert len(result.anomalies) == 3
    upserted_rule_refs = [call[0].rule_ref for call in spy.calls]
    assert upserted_rule_refs == ["RULE-CPU-HIGH", "RULE-LINK-FLAP", "RULE-BGP-DOWN"]
    for anomaly, (candidate, fingerprint, observed_at) in zip(
        result.anomalies, spy.calls, strict=True
    ):
        expected_candidate = AnomalyIncidentMapper.build_candidate(anomaly)
        assert candidate == expected_candidate
        assert fingerprint == compute_fingerprint(
            expected_candidate.device_id,
            expected_candidate.source,
            expected_candidate.rule_ref,
            expected_candidate.affected_resource,
        )
        assert observed_at == candidate.observed_at


def test_multiple_anomalies__commits_exactly_once() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            store,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    counts = _LifecycleCounts()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts)
    )

    service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=50),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    assert counts.commit == 1
    assert counts.rollback == 0


def test_multiple_anomalies__result_still_exposes_only_sample_and_anomalies() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            store,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    service, _ = _make_service_with_incidents_spy(store)

    result = service.ingest(
        _command(
            sample=_sample(
                sampled_at=T0 + timedelta(seconds=50),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
                bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
            )
        )
    )

    assert {f.name for f in fields(result)} == {"sample", "anomalies"}


def test_repeated_cpu_detection__updates_the_same_open_incident() -> None:
    store = InMemoryStore()
    _seed_device(store)
    service, _ = _make_service_with_incidents_spy(store)

    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    first_result = service.ingest(
        _command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0))
    )
    assert len(first_result.anomalies) == 1

    second_result = service.ingest(
        _command(
            sample=_sample(sampled_at=T0 + timedelta(seconds=60), cpu=95.0),
            observed_at=T0 + timedelta(seconds=60),
        )
    )
    assert len(second_result.anomalies) == 1

    incidents = _incidents(store)
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.occurrence_count == 2
    assert incident.created_at == T0
    assert incident.last_seen_at == T0 + timedelta(seconds=60)
    assert incident.evidence == second_result.anomalies[0].evidence
    assert incident.evidence != first_result.anomalies[0].evidence


def test_failure_during_first_incident_upsert__rolls_back_everything() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    counts = _LifecycleCounts()
    upsert_error = RuntimeError("first upsert boom")
    spy = _IncidentsSpy(_wrapped=None, fail_on_call=1, fail_error=upsert_error)

    def factory() -> _LifecycleSpyUnitOfWork:
        uow = InMemoryUnitOfWork(store)
        spy._wrapped = uow.incidents
        uow.incidents = spy  # type: ignore[assignment]
        return _LifecycleSpyUnitOfWork(uow, counts)

    service = TelemetryIngestionService(unit_of_work_factory=factory)

    with pytest.raises(RuntimeError, match="first upsert boom"):
        service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))

    assert counts.commit == 0
    assert counts.rollback == 1
    assert len(spy.calls) == 1
    # The pre-existing seeded sample is the only one retained — the current
    # ingestion's own save was rolled back with the rest of the transaction.
    retained = InMemoryUnitOfWork(store).telemetry_samples.get_latest(DEVICE_ID)
    assert retained is not None
    assert retained.sampled_at == T0
    assert _incidents(store) == ()


def test_failure_during_later_incident_upsert__rolls_back_first_upsert_too() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            store,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )
    counts = _LifecycleCounts()
    upsert_error = RuntimeError("second upsert boom")
    spy = _IncidentsSpy(_wrapped=None, fail_on_call=2, fail_error=upsert_error)

    def factory() -> _LifecycleSpyUnitOfWork:
        uow = InMemoryUnitOfWork(store)
        spy._wrapped = uow.incidents
        uow.incidents = spy  # type: ignore[assignment]
        return _LifecycleSpyUnitOfWork(uow, counts)

    service = TelemetryIngestionService(unit_of_work_factory=factory)

    with pytest.raises(RuntimeError, match="second upsert boom"):
        service.ingest(
            _command(
                sample=_sample(
                    sampled_at=T0 + timedelta(seconds=50),
                    cpu=95.0,
                    interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
                    bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
                )
            )
        )

    assert counts.commit == 0
    assert counts.rollback == 1
    assert len(spy.calls) == 2  # third (BGP-down) never attempted
    assert _incidents(store) == ()


def test_rule_engine_exception__no_incident_upsert_occurs() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy(fail=RuntimeError("rule engine boom"))
    spy = _IncidentsSpy(_wrapped=None)

    def factory() -> InMemoryUnitOfWork:
        uow = InMemoryUnitOfWork(store)
        spy._wrapped = uow.incidents
        uow.incidents = spy  # type: ignore[assignment]
        return uow

    service = TelemetryIngestionService(unit_of_work_factory=factory, rule_engine=engine)

    with pytest.raises(RuntimeError, match="rule engine boom"):
        service.ingest(_command())

    assert spy.calls == []
    assert _incidents(store) == ()


# --- AC-10: structured incident events (Gate AC-10C) -------------------------


def test_ac10__created_anomaly_incident__emits_one_created_event() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    sink = _RecordingSink()
    service = _make_service(store, incident_event_sink=sink)

    service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))

    incident = _incidents(store)[0]
    assert len(sink.calls) == 1
    event = sink.calls[0]
    assert event.incident_id == incident.incident_id
    assert event.device_id == DEVICE_ID
    assert event.rule_ref == "RULE-CPU-HIGH"
    assert event.severity == Severity.HIGH
    assert event.status is IncidentStatus.OPEN
    assert event.outcome is IncidentUpsertOutcome.CREATED
    assert event.timestamp == incident.last_seen_at


def test_ac10__updated_open_anomaly__emits_exactly_one_updated_event_for_that_ingestion() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    first_sink = _RecordingSink()
    first_service = _make_service(store, incident_event_sink=first_sink)
    first_service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))
    assert len(first_sink.calls) == 1
    assert first_sink.calls[0].outcome is IncidentUpsertOutcome.CREATED

    # A fresh sink/service pair scopes the second ingestion's emitted events
    # independently, never conflated with the first ingestion's event.
    second_sink = _RecordingSink()
    second_service = _make_service(store, incident_event_sink=second_sink)
    recurrence_observed_at = T0 + timedelta(seconds=60)

    second_service.ingest(
        _command(
            sample=_sample(sampled_at=recurrence_observed_at, cpu=95.0),
            observed_at=recurrence_observed_at,
        )
    )

    incident = _incidents(store)[0]
    assert len(second_sink.calls) == 1
    event = second_sink.calls[0]
    assert event.outcome is IncidentUpsertOutcome.UPDATED
    assert event.incident_id == incident.incident_id
    assert event.timestamp == incident.last_seen_at
    assert event.timestamp == recurrence_observed_at


def test_ac10__no_anomaly__emits_no_event_and_preserves_result_shape() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sample = _sample()
    sink = _RecordingSink()
    service = _make_service(store, incident_event_sink=sink)

    result = service.ingest(_command(sample=sample))

    assert result == TelemetryIngestionResult(sample=sample, anomalies=())
    assert sink.calls == []


def _seed_multi_anomaly_history(store: InMemoryStore, interface: str, neighbor: str) -> None:
    for offset, state in enumerate([LinkState.UP, LinkState.DOWN, LinkState.UP, LinkState.DOWN]):
        _seed_sample(
            store,
            _sample(
                sampled_at=T0 + timedelta(seconds=10 * offset),
                cpu=95.0,
                interface_states=(InterfaceState(name=interface, oper_state=state),),
            ),
        )
    _seed_sample(
        store,
        _sample(
            sampled_at=T0 + timedelta(seconds=40),
            cpu=95.0,
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.ESTABLISHED),),
        ),
    )


def _multi_anomaly_command(interface: str, neighbor: str) -> TelemetryIngestionCommand:
    return _command(
        sample=_sample(
            sampled_at=T0 + timedelta(seconds=50),
            cpu=95.0,
            interface_states=(InterfaceState(name=interface, oper_state=LinkState.UP),),
            bgp_sessions=(BgpSession(neighbor_ip=neighbor, state=BgpState.IDLE),),
        )
    )


def test_ac10__multiple_anomalies__emits_three_events_matching_upsert_order() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    _seed_multi_anomaly_history(store, interface, neighbor)
    sink = _RecordingSink()
    service, spy = _make_service_with_incidents_spy(store, incident_event_sink=sink)

    service.ingest(_multi_anomaly_command(interface, neighbor))

    assert len(sink.calls) == 3
    assert [event.rule_ref for event in sink.calls] == [
        "RULE-CPU-HIGH",
        "RULE-LINK-FLAP",
        "RULE-BGP-DOWN",
    ]
    assert all(event.outcome is IncidentUpsertOutcome.CREATED for event in sink.calls)

    incidents_by_id = {incident.incident_id: incident for incident in _incidents(store)}
    assert {event.incident_id for event in sink.calls} == set(incidents_by_id)

    for event, (candidate, _fingerprint, _observed_at) in zip(
        sink.calls,
        spy.calls,
        strict=True,
    ):
        persisted = incidents_by_id[event.incident_id]

        assert event.rule_ref == candidate.rule_ref
        assert event.rule_ref == persisted.rule_ref
        assert event.device_id == persisted.device_id
        assert event.status is persisted.status
        assert event.timestamp == persisted.last_seen_at


def test_ac10__multiple_anomalies__commits_exactly_once_and_events_emitted_after() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    _seed_multi_anomaly_history(store, interface, neighbor)
    counts = _LifecycleCounts()
    sink = _CommitOrderRecordingSink(counts=counts)
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts),
        incident_event_sink=sink,
    )

    service.ingest(_multi_anomaly_command(interface, neighbor))

    assert counts.commit == 1
    assert counts.rollback == 0
    assert sink.snapshots == [(1, 0), (1, 0), (1, 0)]


def test_ac10__event_emission__happens_strictly_after_commit() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    counts = _LifecycleCounts()
    sink = _CommitOrderRecordingSink(counts=counts)
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts),
        incident_event_sink=sink,
    )

    service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))

    assert sink.snapshots == [(1, 0)]


def test_failure__telemetry_save_failure__rolls_back_and_emits_no_event() -> None:
    store = InMemoryStore()
    _seed_device(store)
    sink = _RecordingSink()
    save_error = RuntimeError("telemetry save boom")

    def factory() -> InMemoryUnitOfWork:
        uow = InMemoryUnitOfWork(store)
        uow.telemetry_samples = _FailingTelemetrySamplesSpy(  # type: ignore[assignment]
            _wrapped=uow.telemetry_samples, fail_error=save_error
        )
        return uow

    service = TelemetryIngestionService(unit_of_work_factory=factory, incident_event_sink=sink)

    with pytest.raises(RuntimeError, match="telemetry save boom"):
        service.ingest(_command())

    assert sink.calls == []
    assert _incidents(store) == ()


def test_failure__rule_engine_exception__emits_no_event() -> None:
    store = InMemoryStore()
    _seed_device(store)
    engine = _RuleEngineSpy(fail=RuntimeError("rule engine boom"))
    sink = _RecordingSink()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: InMemoryUnitOfWork(store),
        rule_engine=engine,
        incident_event_sink=sink,
    )

    with pytest.raises(RuntimeError, match="rule engine boom"):
        service.ingest(_command())

    assert sink.calls == []


def test_failure__anomaly_mapping_failure__rolls_back_and_emits_no_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    sink = _RecordingSink()

    def failing_build_candidate(anomaly: Any) -> Any:
        raise RuntimeError("mapping boom")

    monkeypatch.setattr(AnomalyIncidentMapper, "build_candidate", failing_build_candidate)
    service = _make_service(store, incident_event_sink=sink)

    with pytest.raises(RuntimeError, match="mapping boom"):
        service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))

    assert sink.calls == []
    assert _incidents(store) == ()


def test_failure__incident_upsert_failure__rolls_back_and_emits_no_event() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    upsert_error = RuntimeError("upsert boom")
    sink = _RecordingSink()
    service, spy = _make_service_with_incidents_spy(
        store, fail_on_call=1, fail_error=upsert_error, incident_event_sink=sink
    )

    with pytest.raises(RuntimeError, match="upsert boom"):
        service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))

    assert sink.calls == []
    assert _incidents(store) == ()


def test_failure__commit_failure__emits_no_event() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    counts = _LifecycleCounts()
    commit_error = RuntimeError("commit boom")
    sink = _RecordingSink()
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(
            InMemoryUnitOfWork(store), counts, _fail_commit=commit_error
        ),
        incident_event_sink=sink,
    )

    with pytest.raises(RuntimeError, match="commit boom"):
        service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))

    assert sink.calls == []


def test_ac10__sink_failure_after_commit__is_swallowed_and_does_not_affect_result() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    counts = _LifecycleCounts()

    class _AlwaysFailingSink:
        def emit(self, event: IncidentLogEvent) -> None:
            raise RuntimeError("sink boom")

    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts),
        incident_event_sink=_AlwaysFailingSink(),
    )
    sample = _sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)

    result = service.ingest(_command(sample=sample))

    assert result == TelemetryIngestionResult(sample=sample, anomalies=result.anomalies)
    assert len(result.anomalies) == 1
    assert counts.commit == 1
    assert counts.rollback == 0
    assert counts.close == 1
    incidents = _incidents(store)
    assert len(incidents) == 1
    assert incidents[0].status is IncidentStatus.OPEN


def test_ac10__sink_fails_for_one_of_three_events__continues_and_isolates_the_failure() -> None:
    store = InMemoryStore()
    _seed_device(store)
    interface = "GigabitEthernet0/1"
    neighbor = "10.0.0.2"
    _seed_multi_anomaly_history(store, interface, neighbor)
    counts = _LifecycleCounts()
    sink = _SelectivelyFailingSink(fail_on_indices=frozenset({1}))
    service = TelemetryIngestionService(
        unit_of_work_factory=lambda: _LifecycleSpyUnitOfWork(InMemoryUnitOfWork(store), counts),
        incident_event_sink=sink,
    )

    result = service.ingest(_multi_anomaly_command(interface, neighbor))

    assert len(result.anomalies) == 3
    assert counts.commit == 1
    assert counts.rollback == 0
    assert [event.rule_ref for event in sink.attempted] == [
        "RULE-CPU-HIGH",
        "RULE-LINK-FLAP",
        "RULE-BGP-DOWN",
    ]
    assert [event.rule_ref for event in sink.succeeded] == ["RULE-CPU-HIGH", "RULE-BGP-DOWN"]


def test_ac10__resolved_recurrence__emits_created_event_with_a_new_incident_id() -> None:
    store = InMemoryStore()
    _seed_device(store)
    _seed_sample(store, _sample(sampled_at=T0, cpu=95.0))
    first_sink = _RecordingSink()
    first_service = _make_service(store, incident_event_sink=first_sink)
    first_service.ingest(_command(sample=_sample(sampled_at=T0 + timedelta(seconds=30), cpu=95.0)))
    resolved_incident = _incidents(store)[0]
    assert resolved_incident.status is IncidentStatus.OPEN

    resolve_at = T0 + timedelta(seconds=40)
    resolve_uow = InMemoryUnitOfWork(store)
    resolve_uow.incidents.resolve(resolved_incident.incident_id, resolve_at)
    resolve_uow.commit()

    second_sink = _RecordingSink()
    second_service = _make_service(store, incident_event_sink=second_sink)
    recurrence_observed_at = T0 + timedelta(seconds=90)

    second_service.ingest(
        _command(
            sample=_sample(sampled_at=recurrence_observed_at, cpu=95.0),
            observed_at=recurrence_observed_at,
        )
    )

    open_incidents = [i for i in _incidents(store) if i.status is IncidentStatus.OPEN]
    assert len(open_incidents) == 1
    new_incident = open_incidents[0]
    assert new_incident.incident_id != resolved_incident.incident_id

    assert len(second_sink.calls) == 1
    event = second_sink.calls[0]
    assert event.outcome is IncidentUpsertOutcome.CREATED
    assert event.incident_id == new_incident.incident_id
    assert event.incident_id != resolved_incident.incident_id
