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
