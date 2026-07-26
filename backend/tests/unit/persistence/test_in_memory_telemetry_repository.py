"""InMemoryTelemetryRepository behavior (Gate E1).

Constructed directly against a bare InMemoryStore — no UnitOfWork, no shared
persistence fixture, since this gate has exactly one implementation (see
domain-model.md Section 12's TelemetryRepository port and the Gate E0 plan's
binding decisions on retention/ordering/tie-breaking).

Retention contract under test: a per-device event-time watermark (the
greatest sampled_at ever saved for that device — never datetime.now() or an
injected clock), an inclusive 5-minute window relative to that watermark,
and a 100-entry cap applied after the time filter, both bounds intersected.
Ordering contract: get_recent/get_latest are ascending-sampled_at,
insertion_sequence-ascending tie-break — insertion_sequence is
repository-internal metadata, never added to TelemetrySample itself.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.telemetry_repository import InMemoryTelemetryRepository

DEVICE_ID = "spine-01"
OTHER_DEVICE_ID = "leaf-01"
T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _sample(device_id: str = DEVICE_ID, sampled_at: datetime = T0) -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=sampled_at,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )


def _repository() -> tuple[InMemoryStore, InMemoryTelemetryRepository]:
    store = InMemoryStore()
    return store, InMemoryTelemetryRepository(store)


# --- Empty repository --------------------------------------------------------


def test_get_latest__empty_repository__returns_none() -> None:
    _, repository = _repository()

    assert repository.get_latest(DEVICE_ID) is None


def test_get_recent__empty_repository__returns_empty_list() -> None:
    _, repository = _repository()

    assert repository.get_recent(DEVICE_ID, since=T0) == []


# --- One-sample round trip ----------------------------------------------------


def test_save__one_sample__round_trips_through_get_latest_and_get_recent() -> None:
    _, repository = _repository()
    sample = _sample()

    repository.save(DEVICE_ID, sample)

    assert repository.get_latest(DEVICE_ID) == sample
    assert repository.get_recent(DEVICE_ID, since=T0) == [sample]


# --- Mismatched device IDs -----------------------------------------------------


def test_save__device_id_mismatch__raises_value_error() -> None:
    store, repository = _repository()
    sample = _sample(device_id=DEVICE_ID)

    with pytest.raises(ValueError, match="device_id"):
        repository.save(OTHER_DEVICE_ID, sample)

    assert store.telemetry_sequence == 0
    assert store.telemetry_samples == {}


# --- Exact duplicate samples ---------------------------------------------------


def test_save__two_exact_duplicate_samples__both_retained() -> None:
    _, repository = _repository()
    sample = _sample()

    repository.save(DEVICE_ID, sample)
    repository.save(DEVICE_ID, sample)

    assert repository.get_recent(DEVICE_ID, since=T0) == [sample, sample]


# --- Equal timestamps ----------------------------------------------------------


def test_save__equal_timestamps__get_recent_preserves_insertion_order() -> None:
    _, repository = _repository()
    first = replace(_sample(sampled_at=T0), cpu_utilization_pct=10.0)
    second = replace(_sample(sampled_at=T0), cpu_utilization_pct=20.0)

    repository.save(DEVICE_ID, first)
    repository.save(DEVICE_ID, second)

    assert repository.get_recent(DEVICE_ID, since=T0) == [first, second]


def test_save__equal_timestamps__get_latest_returns_later_inserted_sample() -> None:
    _, repository = _repository()
    first = replace(_sample(sampled_at=T0), cpu_utilization_pct=10.0)
    second = replace(_sample(sampled_at=T0), cpu_utilization_pct=99.0)

    repository.save(DEVICE_ID, first)
    repository.save(DEVICE_ID, second)

    assert repository.get_latest(DEVICE_ID) == second


# --- Out-of-order save ----------------------------------------------------------


def test_save__out_of_order__accepted_without_exception() -> None:
    _, repository = _repository()
    later = _sample(sampled_at=T0 + timedelta(seconds=60))
    earlier = _sample(sampled_at=T0)

    repository.save(DEVICE_ID, later)
    repository.save(DEVICE_ID, earlier)


def test_save__out_of_order__get_recent_returns_event_time_ascending_order() -> None:
    _, repository = _repository()
    later = _sample(sampled_at=T0 + timedelta(seconds=60))
    earlier = _sample(sampled_at=T0)

    repository.save(DEVICE_ID, later)
    repository.save(DEVICE_ID, earlier)

    assert repository.get_recent(DEVICE_ID, since=T0) == [earlier, later]


def test_save__out_of_order__get_latest_returns_greatest_sampled_at_not_last_call() -> None:
    _, repository = _repository()
    later = _sample(sampled_at=T0 + timedelta(seconds=60))
    earlier = _sample(sampled_at=T0)

    repository.save(DEVICE_ID, later)
    repository.save(DEVICE_ID, earlier)

    assert repository.get_latest(DEVICE_ID) == later


# --- since filtering -------------------------------------------------------------


def test_get_recent__excludes_samples_older_than_since() -> None:
    _, repository = _repository()
    old = _sample(sampled_at=T0)
    new = _sample(sampled_at=T0 + timedelta(seconds=30))
    repository.save(DEVICE_ID, old)
    repository.save(DEVICE_ID, new)

    result = repository.get_recent(DEVICE_ID, since=T0 + timedelta(seconds=1))

    assert result == [new]


def test_get_recent__sample_exactly_equal_to_since__is_included() -> None:
    _, repository = _repository()
    sample = _sample(sampled_at=T0)
    repository.save(DEVICE_ID, sample)

    result = repository.get_recent(DEVICE_ID, since=T0)

    assert result == [sample]


# --- Five-minute retention boundary ----------------------------------------------


def test_save__sample_exactly_five_minutes_behind_watermark__is_retained() -> None:
    _, repository = _repository()
    watermark_sample = _sample(sampled_at=T0)
    boundary_sample = _sample(sampled_at=T0 - timedelta(minutes=5))

    repository.save(DEVICE_ID, boundary_sample)
    repository.save(DEVICE_ID, watermark_sample)

    assert repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=5)) == [
        boundary_sample,
        watermark_sample,
    ]


def test_save__sample_older_than_five_minutes__is_pruned() -> None:
    _, repository = _repository()
    too_old_sample = _sample(sampled_at=T0 - timedelta(minutes=5, seconds=1))
    watermark_sample = _sample(sampled_at=T0)

    repository.save(DEVICE_ID, too_old_sample)
    repository.save(DEVICE_ID, watermark_sample)

    assert repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=10)) == [watermark_sample]


# --- More than 100 samples inside five minutes -----------------------------------


def test_save__105_samples_within_five_minutes__exactly_100_retained() -> None:
    _, repository = _repository()
    samples = [_sample(sampled_at=T0 + timedelta(seconds=i)) for i in range(105)]

    for sample in samples:
        repository.save(DEVICE_ID, sample)

    result = repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=10))

    assert len(result) == 100
    assert result == samples[-100:]


# --- Late sample inside watermark window -----------------------------------------


def test_save__late_sample_inside_watermark_window__accepted_and_retained() -> None:
    _, repository = _repository()
    first = _sample(sampled_at=T0)
    watermark_sample = _sample(sampled_at=T0 + timedelta(minutes=2))
    late_but_within_window = _sample(sampled_at=T0 + timedelta(minutes=1))

    repository.save(DEVICE_ID, first)
    repository.save(DEVICE_ID, watermark_sample)
    repository.save(DEVICE_ID, late_but_within_window)

    result = repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=10))

    assert result == [first, late_but_within_window, watermark_sample]


# --- Late sample outside watermark window -----------------------------------------


def test_save__late_sample_outside_watermark_window__accepted_then_immediately_absent() -> None:
    store, repository = _repository()
    watermark_sample = _sample(sampled_at=T0)
    late_sample = _sample(sampled_at=T0 - timedelta(minutes=10))

    repository.save(DEVICE_ID, watermark_sample)
    sequence_before = store.telemetry_sequence
    repository.save(DEVICE_ID, late_sample)

    assert store.telemetry_sequence == sequence_before + 1
    assert repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=20)) == [watermark_sample]
    assert repository.get_latest(DEVICE_ID) == watermark_sample


# --- Watermark advancement ---------------------------------------------------------


def test_save__watermark_advancement__prunes_entries_outside_new_window() -> None:
    _, repository = _repository()
    first = _sample(sampled_at=T0)
    repository.save(DEVICE_ID, first)

    advanced = _sample(sampled_at=T0 + timedelta(minutes=10))
    repository.save(DEVICE_ID, advanced)

    result = repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=20))

    assert result == [advanced]


# --- Device isolation ----------------------------------------------------------------


def test_save__two_devices__retention_for_one_does_not_affect_the_other() -> None:
    _, repository = _repository()
    device_a_sample = _sample(device_id=DEVICE_ID, sampled_at=T0)
    device_b_sample = _sample(device_id=OTHER_DEVICE_ID, sampled_at=T0 + timedelta(minutes=10))

    repository.save(DEVICE_ID, device_a_sample)
    repository.save(OTHER_DEVICE_ID, device_b_sample)

    assert repository.get_recent(DEVICE_ID, since=T0 - timedelta(minutes=20)) == [device_a_sample]
    assert repository.get_recent(OTHER_DEVICE_ID, since=T0 - timedelta(minutes=20)) == [
        device_b_sample
    ]


# --- Non-mutation --------------------------------------------------------------------


def test_save_and_retrieve__does_not_rewrite_any_sample_field() -> None:
    _, repository = _repository()
    sample = TelemetrySample(
        device_id=DEVICE_ID,
        sampled_at=T0,
        cpu_utilization_pct=91.0,
        memory_utilization_pct=42.0,
        interface_error_rate=0.02,
        interface_states=(),
        bgp_sessions=(),
    )

    repository.save(DEVICE_ID, sample)
    retrieved = repository.get_latest(DEVICE_ID)

    assert retrieved is not None
    assert retrieved.device_id == sample.device_id
    assert retrieved.sampled_at == sample.sampled_at
    assert retrieved.cpu_utilization_pct == sample.cpu_utilization_pct
    assert retrieved.memory_utilization_pct == sample.memory_utilization_pct
    assert retrieved.interface_error_rate == sample.interface_error_rate
    assert retrieved.interface_states == sample.interface_states
    assert retrieved.bgp_sessions == sample.bgp_sessions
    assert retrieved == sample


# --- get_recent returns a fresh list --------------------------------------------------


def test_get_recent__returns_newly_allocated_list_each_call() -> None:
    _, repository = _repository()
    sample = _sample()
    repository.save(DEVICE_ID, sample)

    first_call = repository.get_recent(DEVICE_ID, since=T0)
    second_call = repository.get_recent(DEVICE_ID, since=T0)

    assert first_call == second_call
    assert first_call is not second_call
