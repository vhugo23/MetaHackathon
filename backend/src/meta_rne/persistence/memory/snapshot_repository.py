"""In-memory ConfigurationSnapshotRepository (Day 4B2) — a fast
conformance-test double, never used in production (ADR-0002).

Append-only: ``add`` explicitly checks (1) duplicate snapshot_id, then
(2) referenced device existence — the same two failure modes the
SQLAlchemy implementation distinguishes from a real PostgreSQL
``IntegrityError``.
"""

from meta_rne.domain.snapshot import ConfigurationSnapshot
from meta_rne.persistence.errors import ReferencedDeviceNotFoundError, SnapshotAlreadyExistsError
from meta_rne.persistence.memory.store import InMemoryStore


class InMemoryConfigurationSnapshotRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def get_by_id(self, snapshot_id: str) -> ConfigurationSnapshot | None:
        return self._store.snapshots.get(snapshot_id)

    def add(self, snapshot: ConfigurationSnapshot) -> None:
        if snapshot.snapshot_id in self._store.snapshots:
            raise SnapshotAlreadyExistsError(snapshot.snapshot_id)
        if snapshot.device_id not in self._store.devices:
            raise ReferencedDeviceNotFoundError(snapshot.device_id)
        self._store.snapshots[snapshot.snapshot_id] = snapshot
