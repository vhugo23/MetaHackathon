"""In-memory UnitOfWork (Day 4B3) — a fast conformance-test double, never
used in production (ADR-0002).

Each ``InMemoryUnitOfWork`` is constructed against a *committed*
``InMemoryStore`` (shared across UnitOfWork instances, representing durable
state) but works against its own, isolated *working* ``InMemoryStore``,
copied from the committed store's current contents at construction time —
with a brand-new ``threading.Lock`` for the working store's
``incidents_lock``/``publish_lock``, never the committed store's lock
instances. The four repositories are constructed once, bound only to that
one working-store object, so ``rollback()`` must reset the *same* object's
collections in place rather than swap in a new store the repositories
wouldn't see.

``commit()`` publishes all four working collections into the committed
store at once, while holding the committed store's ``publish_lock`` — never
exposing a partially-published collection set to a *new* UnitOfWork
constructed against the same committed store afterward. Construction and
``rollback()`` read the committed store's four collections through the same
``publish_lock`` (via ``_snapshot_committed_store``), so a concurrent
``commit()`` on another ``InMemoryUnitOfWork`` sharing this committed store
can never be observed mid-publish — the lock only guarantees atomicity if
every reader of the committed store's four collections also takes it, not
only the writer. ``close()`` performs no I/O and publishes nothing.
"""

import threading

from meta_rne.domain.device import Device
from meta_rne.domain.incident import Incident
from meta_rne.domain.policy import ConfigurationPolicy
from meta_rne.domain.snapshot import ConfigurationSnapshot
from meta_rne.persistence.memory.device_repository import InMemoryDeviceRepository
from meta_rne.persistence.memory.incident_repository import InMemoryIncidentRepository
from meta_rne.persistence.memory.policy_repository import InMemoryConfigurationPolicyRepository
from meta_rne.persistence.memory.snapshot_repository import (
    InMemoryConfigurationSnapshotRepository,
)
from meta_rne.persistence.memory.store import InMemoryStore

_CommittedSnapshot = tuple[
    dict[str, Device],
    dict[str, ConfigurationSnapshot],
    dict[str, ConfigurationPolicy],
    dict[str, Incident],
]


def _snapshot_committed_store(committed: InMemoryStore) -> _CommittedSnapshot:
    # The one place construction/rollback read the committed store's four
    # collections — done under the same publish_lock commit() writes under,
    # so a concurrent commit() is never observed mid-publish.
    with committed.publish_lock:
        return (
            dict(committed.devices),
            dict(committed.snapshots),
            dict(committed.policies),
            dict(committed.incidents),
        )


def _apply_snapshot_to_working_store(working: InMemoryStore, snapshot: _CommittedSnapshot) -> None:
    working.devices, working.snapshots, working.policies, working.incidents = snapshot
    # Fresh locks, never the committed store's lock instances.
    working.incidents_lock = threading.Lock()
    working.publish_lock = threading.Lock()


class InMemoryUnitOfWork:
    def __init__(self, committed_store: InMemoryStore) -> None:
        self._committed_store = committed_store
        self._working_store = InMemoryStore()
        _apply_snapshot_to_working_store(
            self._working_store, _snapshot_committed_store(committed_store)
        )

        self.devices = InMemoryDeviceRepository(self._working_store)
        self.configuration_snapshots = InMemoryConfigurationSnapshotRepository(self._working_store)
        self.configuration_policies = InMemoryConfigurationPolicyRepository(self._working_store)
        self.incidents = InMemoryIncidentRepository(self._working_store)

    def commit(self) -> None:
        working_devices = dict(self._working_store.devices)
        working_snapshots = dict(self._working_store.snapshots)
        working_policies = dict(self._working_store.policies)
        working_incidents = dict(self._working_store.incidents)
        with self._committed_store.publish_lock:
            self._committed_store.devices = working_devices
            self._committed_store.snapshots = working_snapshots
            self._committed_store.policies = working_policies
            self._committed_store.incidents = working_incidents

    def rollback(self) -> None:
        _apply_snapshot_to_working_store(
            self._working_store, _snapshot_committed_store(self._committed_store)
        )

    def close(self) -> None:
        pass
