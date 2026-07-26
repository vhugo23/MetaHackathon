"""InMemoryUnitOfWork tests (Day 4B3).

Each UnitOfWork gets an isolated *working* InMemoryStore, copied from the
*committed* store at construction time (a fresh lock, never the committed
store's lock instance). Repositories are bound only to the working store.
``commit()`` publishes all four collections into the committed store at
once, under the committed store's ``publish_lock``; ``rollback()`` discards
the working store's changes by resetting it back to the committed store's
current state; ``close()`` performs no I/O and publishes nothing.

Construction/``rollback()`` must read the committed store's four
collections through that same ``publish_lock`` — otherwise a concurrent
``commit()`` on another ``InMemoryUnitOfWork`` sharing the committed store
could be observed mid-publish (a hybrid state where some collections are
the old generation and some are the new one). The tests below hold
``publish_lock`` in the main thread and prove construction/``rollback()``/
``commit()`` each genuinely block on it, using ``threading.Event``s with
bounded waits/joins so a regression hangs the affected assertion for at
most its timeout, never the whole suite.
"""

import threading
from datetime import UTC, datetime

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.memory.store import InMemoryStore
from meta_rne.persistence.memory.unit_of_work import InMemoryUnitOfWork

T0 = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)
_BOUNDED_WAIT_SECONDS = 0.2
_JOIN_TIMEOUT_SECONDS = 2.0


def _device(device_id: str = "spine-01") -> Device:
    return Device(
        device_id=device_id,
        vendor=VendorType.CISCO_IOS_XE,
        current_snapshot_id=None,
        baseline_snapshot_id=None,
        created_at=T0,
        updated_at=T0,
    )


def _sample(device_id: str = "spine-01") -> TelemetrySample:
    return TelemetrySample(
        device_id=device_id,
        sampled_at=T0,
        cpu_utilization_pct=50.0,
        memory_utilization_pct=50.0,
        interface_error_rate=0.0,
        interface_states=(),
        bgp_sessions=(),
    )


def test_in_memory_unit_of_work__all_four_repositories_are_available() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)

    assert uow.devices is not None
    assert uow.configuration_snapshots is not None
    assert uow.configuration_policies is not None
    assert uow.incidents is not None


def test_in_memory_unit_of_work__uncommitted_data__is_invisible_to_another_unit_of_work() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())

    other = InMemoryUnitOfWork(committed)

    assert other.devices.get_by_id("spine-01") is None
    assert committed.devices == {}


def test_in_memory_unit_of_work__commit__publishes_all_collections() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())

    uow.commit()

    assert committed.devices["spine-01"] == _device()


def test_in_memory_unit_of_work__rollback__discards_all_staged_data() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())

    uow.rollback()

    assert uow.devices.get_by_id("spine-01") is None
    assert committed.devices == {}


def test_in_memory_unit_of_work__close_without_commit__publishes_nothing() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())

    uow.close()

    assert committed.devices == {}


def test_in_memory_unit_of_work__new_unit_of_work__sees_committed_data() -> None:
    committed = InMemoryStore()
    first = InMemoryUnitOfWork(committed)
    first.devices.save(_device())
    first.commit()

    second = InMemoryUnitOfWork(committed)

    assert second.devices.get_by_id("spine-01") == _device()


def test_in_memory_unit_of_work__new_unit_of_work__does_not_see_rolled_back_data() -> None:
    committed = InMemoryStore()
    first = InMemoryUnitOfWork(committed)
    first.devices.save(_device())
    first.rollback()

    second = InMemoryUnitOfWork(committed)

    assert second.devices.get_by_id("spine-01") is None


def test_in_memory_unit_of_work__working_store_uses_a_fresh_lock_not_the_committed_ones() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)

    assert uow._working_store.incidents_lock is not committed.incidents_lock
    assert uow._working_store.publish_lock is not committed.publish_lock


def test_in_memory_unit_of_work__construction__waits_for_committed_publish_lock() -> None:
    committed = InMemoryStore()
    committed.devices["spine-01"] = _device()

    finished = threading.Event()
    constructed: list[InMemoryUnitOfWork] = []

    def worker() -> None:
        constructed.append(InMemoryUnitOfWork(committed))
        finished.set()

    committed.publish_lock.acquire()
    thread = threading.Thread(target=worker)
    try:
        thread.start()

        # Bounded wait, not a blind sleep: while we hold publish_lock,
        # construction must not be able to complete.
        assert not finished.wait(timeout=_BOUNDED_WAIT_SECONDS)
        assert constructed == []
    finally:
        committed.publish_lock.release()

    assert finished.wait(
        timeout=_JOIN_TIMEOUT_SECONDS
    ), "construction should complete once publish_lock is released"
    thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not thread.is_alive()

    assert len(constructed) == 1
    assert constructed[0].devices.get_by_id("spine-01") == _device()


def test_in_memory_unit_of_work__rollback__waits_for_committed_publish_lock() -> None:
    committed = InMemoryStore()
    committed.devices["spine-02"] = _device("spine-02")
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device("spine-01"))  # staged in the working store only

    finished = threading.Event()

    def worker() -> None:
        uow.rollback()
        finished.set()

    committed.publish_lock.acquire()
    thread = threading.Thread(target=worker)
    try:
        thread.start()

        assert not finished.wait(timeout=_BOUNDED_WAIT_SECONDS)
        # The working store must still reflect the pre-rollback staged data
        # while rollback is blocked on the lock.
        assert uow.devices.get_by_id("spine-01") == _device("spine-01")
    finally:
        committed.publish_lock.release()

    assert finished.wait(
        timeout=_JOIN_TIMEOUT_SECONDS
    ), "rollback should complete once publish_lock is released"
    thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not thread.is_alive()

    # Repositories remain bound to the same working-store object and now
    # reflect the committed store's state, not the discarded staged data.
    assert uow.devices.get_by_id("spine-01") is None
    assert uow.devices.get_by_id("spine-02") == _device("spine-02")


def test_in_memory_unit_of_work__commit__waits_for_committed_publish_lock() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())

    finished = threading.Event()

    def worker() -> None:
        uow.commit()
        finished.set()

    committed.publish_lock.acquire()
    committed_devices_before = dict(committed.devices)
    thread = threading.Thread(target=worker)
    try:
        thread.start()

        assert not finished.wait(timeout=_BOUNDED_WAIT_SECONDS)
        # Committed collections must remain exactly as they were while
        # commit() is blocked on the lock — no partial publish.
        assert committed.devices == committed_devices_before
    finally:
        committed.publish_lock.release()

    assert finished.wait(
        timeout=_JOIN_TIMEOUT_SECONDS
    ), "commit should complete once publish_lock is released"
    thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
    assert not thread.is_alive()

    assert committed.devices["spine-01"] == _device()


# --- telemetry_samples (Gate E2C) ---------------------------------------


def test_in_memory_unit_of_work__commit__publishes_telemetry_samples_and_sequence() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())
    uow.telemetry_samples.save("spine-01", _sample())

    uow.commit()

    assert committed.telemetry_samples["spine-01"][0].sample == _sample()
    assert committed.telemetry_sequence == 1


def test_in_memory_unit_of_work__rollback__restores_telemetry_samples_and_sequence() -> None:
    committed = InMemoryStore()
    committed.telemetry_samples["spine-02"] = ()
    committed.telemetry_sequence = 3
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())
    uow.telemetry_samples.save("spine-01", _sample())

    uow.rollback()

    assert uow.telemetry_samples.get_latest("spine-01") is None
    assert uow._working_store.telemetry_sequence == 3


def test_in_memory_unit_of_work__rollback_after_telemetry_save__committed_store_unchanged() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())
    uow.telemetry_samples.save("spine-01", _sample())

    uow.rollback()

    assert committed.telemetry_samples == {}
    assert committed.telemetry_sequence == 0


def test_in_memory_unit_of_work__new_unit_of_work__sees_committed_telemetry() -> None:
    committed = InMemoryStore()
    first = InMemoryUnitOfWork(committed)
    first.devices.save(_device())
    first.telemetry_samples.save("spine-01", _sample())
    first.commit()

    second = InMemoryUnitOfWork(committed)

    assert second.telemetry_samples.get_latest("spine-01") == _sample()


def test_in_memory_unit_of_work__new_unit_of_work__does_not_see_rolled_back_telemetry() -> None:
    committed = InMemoryStore()
    first = InMemoryUnitOfWork(committed)
    first.devices.save(_device())
    first.telemetry_samples.save("spine-01", _sample())
    first.rollback()

    second = InMemoryUnitOfWork(committed)

    assert second.telemetry_samples.get_latest("spine-01") is None


def test_in_memory_unit_of_work__rolled_back_sequence_value__may_be_reused() -> None:
    committed = InMemoryStore()
    first = InMemoryUnitOfWork(committed)
    first.devices.save(_device())
    first.telemetry_samples.save("spine-01", _sample())  # consumes sequence 0 in the working store
    first.rollback()  # discarded — never committed

    second = InMemoryUnitOfWork(committed)
    second.devices.save(_device())
    second.telemetry_samples.save("spine-01", _sample())
    second.commit()

    # The committed store's sequence starts fresh at 0 again — the
    # rolled-back attempt's consumed value (0) is legitimately reused,
    # since it was never observed by any committed state.
    assert committed.telemetry_sequence == 1


def test_in_memory_unit_of_work__committed_and_working_telemetry_dicts_are_separate_objects() -> (
    None
):
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)

    assert uow._working_store.telemetry_samples is not committed.telemetry_samples


def test_in_memory_unit_of_work__per_device_stored_tuples_are_replaced_not_mutated() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())
    uow.telemetry_samples.save("spine-01", _sample())
    first_tuple = uow._working_store.telemetry_samples["spine-01"]

    uow.telemetry_samples.save("spine-01", _sample())
    second_tuple = uow._working_store.telemetry_samples["spine-01"]

    assert first_tuple is not second_tuple
    assert len(first_tuple) == 1
    assert len(second_tuple) == 2


def test_in_memory_unit_of_work__existing_device_commit_rollback_behavior_is_unchanged() -> None:
    committed = InMemoryStore()
    uow = InMemoryUnitOfWork(committed)
    uow.devices.save(_device())

    uow.commit()

    assert committed.devices["spine-01"] == _device()
    assert committed.snapshots == {}
    assert committed.policies == {}
    assert committed.incidents == {}
