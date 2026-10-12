"""Latest-state persistence inside ``OperationalStateIngestionService``
(NPE-1D1A), against the real ``InMemoryUnitOfWork``.

Proves: the fabric is saved as the lab's latest state in the SAME transaction
as the incident lifecycle changes; an exact replay of an already-accepted
collection is a whole-ingestion no-op; stale/conflicting collections are
rejected with everything rolled back; and the C3B/C3C incident semantics are
otherwise unchanged. Row-level rollback on PostgreSQL is proven in
tests/integration/application/test_operational_state_ingestion_postgres.py.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.operational_state_ingestion import OperationalStateIngestionService
from meta_rne.domain.incident import Incident, IncidentStatus, IncidentUpsertResult
from meta_rne.domain.operational_state import (
    BgpSessionState,
    EmptyFabricError,
    FabricOperationalState,
    MixedCollectorError,
    MixedLabNameError,
    OperationalStateSaveOutcome,
)
from meta_rne.observability import IncidentLogEvent
from meta_rne.persistence.errors import OperationalStateConflictError, StaleOperationalStateError
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork
from meta_rne.persistence.serialization import SerializationError
from operational_state_support import (
    REGISTERED_DEVICE_IDS,
    fixture_fabric,
    frr_device,
    with_neighbor_states,
)

T0 = datetime(2026, 10, 7, 12, 0, 0, tzinfo=UTC)
T1 = T0 + timedelta(seconds=30)
T2 = T0 + timedelta(seconds=90)

CREATED = OperationalStateSaveOutcome.CREATED
REPLACED = OperationalStateSaveOutcome.REPLACED
UNCHANGED = OperationalStateSaveOutcome.UNCHANGED


class _Sink:
    def __init__(self) -> None:
        self.events: list[IncidentLogEvent] = []

    def emit(self, event: IncidentLogEvent) -> None:
        self.events.append(event)


class _Uow(InMemoryUnitOfWork):
    """Counts the lifecycle and can fail ``commit`` before publishing."""

    def __init__(self, store: InMemoryStore, fail_commit: bool = False) -> None:
        super().__init__(store)
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0
        self._fail_commit = fail_commit

    def commit(self) -> None:
        self.commits += 1
        if self._fail_commit:
            raise RuntimeError("commit failed")
        super().commit()

    def rollback(self) -> None:
        self.rollbacks += 1
        super().rollback()

    def close(self) -> None:
        self.closes += 1
        super().close()


@dataclass
class _H:
    store: InMemoryStore
    sink: _Sink
    units: list[_Uow]
    service: OperationalStateIngestionService
    make: Callable[[Callable[[_Uow], None] | None, bool], OperationalStateIngestionService]


def _harness() -> _H:
    store = InMemoryStore()
    for device_id in REGISTERED_DEVICE_IDS:
        store.devices[device_id] = frr_device(device_id, T0)
    sink = _Sink()
    units: list[_Uow] = []

    def make(
        tweak: Callable[[_Uow], None] | None = None, fail_commit: bool = False
    ) -> OperationalStateIngestionService:
        def factory() -> _Uow:
            unit = _Uow(store, fail_commit=fail_commit)
            if tweak is not None:
                tweak(unit)
            units.append(unit)
            return unit

        return OperationalStateIngestionService(factory, sink)

    return _H(store, sink, units, make(None, False), make)


def _incidents(store: InMemoryStore) -> list[Incident]:
    return sorted(store.incidents.values(), key=lambda i: i.device_id)


def _by_status(store: InMemoryStore) -> dict[str, IncidentStatus]:
    return {i.device_id: i.status for i in store.incidents.values()}


# --- latest state is persisted with the incident changes -------------------


def test_baseline__persists_as_latest_and_commits_once() -> None:
    h = _harness()
    fabric = fixture_fabric("baseline", T0)

    result = h.service.ingest(fabric)

    assert result.state_outcome is CREATED
    assert h.store.operational_states == {"lab1": fabric}
    assert (h.units[0].commits, h.units[0].rollbacks, h.units[0].closes) == (1, 0, 1)


def test_degraded__persists_latest_and_incidents_in_one_commit() -> None:
    h = _harness()
    fabric = fixture_fabric("degraded-active", T0)

    result = h.service.ingest(fabric)

    assert result.state_outcome is CREATED
    assert (result.incidents_created, result.incidents_updated) == (2, 0)
    assert h.store.operational_states["lab1"] == fabric
    assert len(h.store.incidents) == 2
    assert [u.commits for u in h.units] == [1]


def test_recovery__persists_latest_and_resolution_in_one_commit() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))
    healthy = fixture_fabric("baseline", T1, "run-2")

    result = h.service.ingest(healthy)

    assert result.state_outcome is REPLACED
    assert result.incidents_resolved == 2
    assert h.store.operational_states["lab1"] == healthy
    assert set(_by_status(h.store).values()) == {IncidentStatus.RESOLVED}
    assert h.units[1].commits == 1


def test_new_degraded_collection__still_updates_open_incidents() -> None:
    """Same network condition observed again is NOT the same collection."""
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))

    result = h.service.ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert result.state_outcome is REPLACED
    assert (result.incidents_created, result.incidents_updated) == (0, 2)
    for incident in _incidents(h.store):
        assert incident.occurrence_count == 2
        assert incident.last_seen_at == T1
    assert h.store.operational_states["lab1"].collection_id == "run-2"


# --- exact replay is a whole-ingestion idempotent no-op --------------------


def test_exact_replay_of_degraded__does_not_touch_incidents() -> None:
    h = _harness()
    fabric = fixture_fabric("degraded-active", T0)
    first = h.service.ingest(fabric)
    incidents_before = _incidents(h.store)
    state_before = dict(h.store.operational_states)
    h.sink.events.clear()

    replay = h.service.ingest(fixture_fabric("degraded-active", T0))

    assert replay.state_outcome is UNCHANGED
    # Zero persistence mutations of any kind.
    assert (replay.incidents_created, replay.incidents_updated) == (0, 0)
    assert (replay.incidents_resolved, replay.upserts, replay.resolved_incidents) == (0, (), ())
    assert _incidents(h.store) == incidents_before
    for incident in _incidents(h.store):
        assert incident.occurrence_count == 1  # NOT incremented
        assert incident.last_seen_at == T0  # NOT advanced
        assert incident.updated_at == T0
        assert incident.status is IncidentStatus.OPEN
    assert h.store.operational_states == state_before
    assert h.sink.events == []  # no duplicate incident events
    # The detector's finding is still reported; the original result contract.
    assert replay.anomalies == first.anomalies
    assert replay.collection_id == first.collection_id
    # One transaction, committed once, closed once.
    assert (h.units[-1].commits, h.units[-1].rollbacks, h.units[-1].closes) == (1, 0, 1)


def test_exact_replay_repeated_many_times__occurrence_count_stays_one() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))

    for _ in range(5):
        result = h.service.ingest(fixture_fabric("degraded-active", T0))
        assert result.state_outcome is UNCHANGED

    assert [i.occurrence_count for i in _incidents(h.store)] == [1, 1]


def test_exact_replay_of_recovery__does_not_resolve_again_or_emit_events() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))
    h.service.ingest(fixture_fabric("baseline", T1, "run-2"))
    resolved_before = _incidents(h.store)
    h.sink.events.clear()

    replay = h.service.ingest(fixture_fabric("baseline", T1, "run-2"))

    assert replay.state_outcome is UNCHANGED
    assert replay.incidents_resolved == 0 and replay.resolved_incidents == ()
    assert _incidents(h.store) == resolved_before  # resolved_at / updated_at untouched
    assert h.sink.events == []


def test_replay_of_an_already_superseded_collection__is_stale_not_a_replay() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))
    h.service.ingest(fixture_fabric("baseline", T1, "run-2"))

    with pytest.raises(StaleOperationalStateError):
        h.service.ingest(fixture_fabric("degraded-active", T0))


# --- rejected collections --------------------------------------------------


def test_stale_healthy_collection__is_rejected_and_changes_nothing() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T1, "run-2"))
    incidents_before = _incidents(h.store)
    state_before = dict(h.store.operational_states)
    h.sink.events.clear()

    with pytest.raises(StaleOperationalStateError):
        h.service.ingest(fixture_fabric("baseline", T0, "run-1"))

    assert h.store.operational_states == state_before
    assert _incidents(h.store) == incidents_before
    assert all(i.status is IncidentStatus.OPEN for i in incidents_before)
    assert (h.units[-1].commits, h.units[-1].rollbacks, h.units[-1].closes) == (0, 1, 1)
    assert h.sink.events == []


def test_stale_collection_is_rejected_even_when_it_would_mutate_no_incident() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("baseline", T1, "run-2"))
    state_before = dict(h.store.operational_states)

    with pytest.raises(StaleOperationalStateError):
        h.service.ingest(fixture_fabric("baseline", T0, "run-1"))

    assert h.store.operational_states == state_before
    assert h.store.incidents == {}


def test_equal_time_conflicting_collection__is_rejected() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0, "run-1"))
    incidents_before = _incidents(h.store)
    state_before = dict(h.store.operational_states)

    with pytest.raises(OperationalStateConflictError, match="identical collected_at"):
        h.service.ingest(fixture_fabric("baseline", T0, "run-2"))

    assert h.store.operational_states == state_before
    assert _incidents(h.store) == incidents_before
    assert h.units[-1].rollbacks == 1 and h.units[-1].commits == 0


@pytest.mark.parametrize(
    ("fabric", "reason"),
    [
        (fixture_fabric("baseline", T1, "run-1"), "different collected_at"),
        (fixture_fabric("baseline", T0, "run-1"), "different state"),
    ],
    ids=["same-id-different-time", "same-id-same-time-different-payload"],
)
def test_same_collection_id_with_other_time_or_payload__is_rejected(
    fabric: FabricOperationalState, reason: str
) -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0, "run-1"))
    incidents_before = _incidents(h.store)
    state_before = dict(h.store.operational_states)

    with pytest.raises(OperationalStateConflictError, match=reason):
        h.service.ingest(fabric)

    assert h.store.operational_states == state_before
    assert _incidents(h.store) == incidents_before


def test_empty_and_mixed_fabrics__are_rejected_before_anything_is_written() -> None:
    h = _harness()
    base = fixture_fabric("baseline", T0)
    mixed_lab = list(base.nodes)
    mixed_lab[1] = replace(mixed_lab[1], source=replace(mixed_lab[1].source, lab_name="lab2"))
    mixed_collector = list(base.nodes)
    mixed_collector[2] = replace(
        mixed_collector[2], source=replace(mixed_collector[2].source, collector="other")
    )
    cases: list[tuple[FabricOperationalState, type[Exception]]] = [
        (FabricOperationalState("run-1", T0, ()), EmptyFabricError),
        (replace(base, nodes=tuple(mixed_lab)), MixedLabNameError),
        (replace(base, nodes=tuple(mixed_collector)), MixedCollectorError),
    ]

    for fabric, error in cases:
        with pytest.raises(error):
            h.service.ingest(fabric)

    assert h.store.operational_states == {}
    assert h.store.incidents == {}
    assert all((u.commits, u.rollbacks, u.closes) == (0, 1, 1) for u in h.units)


def test_late_refailure_after_resolution__new_incident_and_history_preserved() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))
    h.service.ingest(fixture_fabric("baseline", T1, "run-2"))
    resolved = {i.incident_id: i for i in h.store.incidents.values()}

    result = h.service.ingest(fixture_fabric("degraded-active", T2, "run-3"))

    assert (result.incidents_created, result.incidents_updated) == (2, 0)
    assert len(h.store.incidents) == 4
    for incident_id, old in resolved.items():
        assert h.store.incidents[incident_id] == old
        assert old.status is IncidentStatus.RESOLVED
    assert h.store.operational_states["lab1"].collection_id == "run-3"


# --- rollback invariants ---------------------------------------------------


def _committed_state(h: _H) -> tuple[Any, Any]:
    return dict(h.store.operational_states), dict(h.store.incidents)


def test_rollback_A__incident_upsert_fails_after_state_save() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("baseline", T0, "run-1"))
    before = _committed_state(h)
    saved: list[OperationalStateSaveOutcome] = []

    def tweak(unit: _Uow) -> None:
        real_save = unit.operational_state.save_latest

        def save(fabric: FabricOperationalState) -> OperationalStateSaveOutcome:
            outcome = real_save(fabric)
            saved.append(outcome)
            return outcome

        def boom(*args: Any, **kwargs: Any) -> IncidentUpsertResult:
            raise RuntimeError("upsert failed")

        unit.operational_state.save_latest = save  # type: ignore[method-assign]
        unit.incidents.upsert_open_incident = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="upsert failed"):
        h.make(tweak, False).ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert saved == [REPLACED]  # the state write really happened first
    assert _committed_state(h) == before
    assert (h.units[-1].commits, h.units[-1].rollbacks, h.units[-1].closes) == (0, 1, 1)
    # The unit's own working view was rolled back too.
    assert h.units[-1].operational_state.get_latest("lab1").collection_id == "run-1"  # type: ignore[union-attr]


def test_rollback_B__recovery_resolution_fails_after_state_save() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0, "run-1"))
    before = _committed_state(h)

    def tweak(unit: _Uow) -> None:
        def boom(*args: Any, **kwargs: Any) -> Incident | None:
            raise RuntimeError("resolve failed")

        unit.incidents.resolve = boom  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="resolve failed"):
        h.make(tweak, False).ingest(fixture_fabric("baseline", T1, "run-2"))

    assert _committed_state(h) == before
    assert h.store.operational_states["lab1"].collection_id == "run-1"
    assert set(_by_status(h.store).values()) == {IncidentStatus.OPEN}
    assert (h.units[-1].commits, h.units[-1].rollbacks) == (0, 1)


def test_rollback_C__state_serialization_failure_commits_no_incident_mutation() -> None:
    h = _harness()
    upserts: list[Any] = []

    def tweak(unit: _Uow) -> None:
        def fail(fabric: FabricOperationalState) -> OperationalStateSaveOutcome:
            raise SerializationError("cannot serialize operational state")

        real_upsert = unit.incidents.upsert_open_incident

        def spy(*args: Any, **kwargs: Any) -> IncidentUpsertResult:
            upserts.append(args)
            return real_upsert(*args, **kwargs)

        unit.operational_state.save_latest = fail  # type: ignore[method-assign]
        unit.incidents.upsert_open_incident = spy  # type: ignore[method-assign]

    with pytest.raises(SerializationError, match="cannot serialize"):
        h.make(tweak, False).ingest(fixture_fabric("degraded-active", T0))

    assert upserts == []
    assert h.store.incidents == {}
    assert h.store.operational_states == {}
    assert (h.units[-1].commits, h.units[-1].rollbacks, h.units[-1].closes) == (0, 1, 1)


def test_rollback_D__incident_validation_fails_before_the_state_write() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("baseline", T0, "run-1"))
    before = _committed_state(h)
    save_calls: list[Any] = []
    del h.store.devices["lab1-spine-1"]

    def tweak(unit: _Uow) -> None:
        real = unit.operational_state.save_latest

        def spy(fabric: FabricOperationalState) -> OperationalStateSaveOutcome:
            save_calls.append(fabric)
            return real(fabric)

        unit.operational_state.save_latest = spy  # type: ignore[method-assign]

    with pytest.raises(DeviceNotFoundError):
        h.make(tweak, False).ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert save_calls == []  # validated before the state write
    assert _committed_state(h) == before
    assert h.store.operational_states["lab1"].collection_id == "run-1"


def test_rollback_E__commit_failure_rolls_back_state_and_incidents() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0, "run-1"))
    before = _committed_state(h)
    h.sink.events.clear()

    with pytest.raises(RuntimeError, match="commit failed"):
        h.make(None, True).ingest(fixture_fabric("degraded-active", T1, "run-2"))

    assert _committed_state(h) == before
    assert [i.occurrence_count for i in _incidents(h.store)] == [1, 1]
    assert h.store.operational_states["lab1"].collection_id == "run-1"
    assert (h.units[-1].commits, h.units[-1].rollbacks, h.units[-1].closes) == (1, 1, 1)
    assert h.sink.events == []  # events are emitted only after a successful commit


def test_state_and_incidents_are_never_visible_separately_before_commit() -> None:
    h = _harness()
    seen: dict[str, Any] = {}

    def tweak(unit: _Uow) -> None:
        real_commit = unit.commit

        def commit() -> None:
            # Right before the single commit: both staged in the working view,
            # neither yet visible to another UnitOfWork on the committed store.
            seen["staged_state"] = unit.operational_state.get_latest("lab1")
            seen["staged_incidents"] = len(unit.incidents.list_all())
            other = InMemoryUnitOfWork(h.store)
            seen["other_state"] = other.operational_state.get_latest("lab1")
            seen["other_incidents"] = len(other.incidents.list_all())
            real_commit()

        unit.commit = commit  # type: ignore[method-assign]

    h.make(tweak, False).ingest(fixture_fabric("degraded-active", T0))

    assert seen["staged_state"] is not None and seen["staged_incidents"] == 2
    assert seen["other_state"] is None and seen["other_incidents"] == 0
    assert h.store.operational_states["lab1"] is not None and len(h.store.incidents) == 2


def test_recovery_via_neighbor_override_still_resolves_only_that_side() -> None:
    h = _harness()
    h.service.ingest(fixture_fabric("degraded-active", T0))
    fabric = with_neighbor_states(
        fixture_fabric("baseline", T1, "run-2"),
        {("spine-1", "10.255.0.1"): BgpSessionState.ACTIVE},
    )

    result = h.service.ingest(fabric)

    assert result.state_outcome is REPLACED
    assert result.incidents_resolved == 1 and result.incidents_updated == 1
    assert h.store.operational_states["lab1"] == fabric
