"""In-memory DeviceRepository (Day 4B2) — a fast conformance-test double,
never used in production (ADR-0002).

Enforces the same lifecycle invariants as the SQLAlchemy implementation:
every rejected transition raises ``DeviceConflictError`` and leaves the
stored ``Device`` completely unchanged (validate everything first, then
mutate).
"""

from meta_rne.domain.device import Device
from meta_rne.persistence.errors import DeviceConflictError
from meta_rne.persistence.memory.store import InMemoryStore


def _validate_transition(existing: Device, candidate: Device) -> None:
    if candidate.vendor != existing.vendor:
        raise DeviceConflictError(
            f"Device {candidate.device_id!r} vendor may not change "
            f"({existing.vendor!r} -> {candidate.vendor!r})"
        )
    if candidate.created_at != existing.created_at:
        raise DeviceConflictError(f"Device {candidate.device_id!r} created_at may not change")
    if candidate.updated_at < existing.updated_at:
        raise DeviceConflictError(
            f"Device {candidate.device_id!r} updated_at may not move backward"
        )
    if (
        existing.baseline_snapshot_id is not None
        and candidate.baseline_snapshot_id != existing.baseline_snapshot_id
    ):
        raise DeviceConflictError(
            f"Device {candidate.device_id!r} baseline_snapshot_id is already set "
            "and may not be replaced"
        )
    if existing.current_snapshot_id is not None and candidate.current_snapshot_id is None:
        raise DeviceConflictError(
            f"Device {candidate.device_id!r} current_snapshot_id may not be cleared"
        )


class InMemoryDeviceRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def get_by_id(self, device_id: str) -> Device | None:
        return self._store.devices.get(device_id)

    def save(self, device: Device) -> None:
        existing = self._store.devices.get(device.device_id)
        if existing is not None:
            _validate_transition(existing, device)

        for snapshot_id in (device.current_snapshot_id, device.baseline_snapshot_id):
            if snapshot_id is not None and snapshot_id not in self._store.snapshots:
                raise DeviceConflictError(
                    f"Device {device.device_id!r} references a snapshot that does "
                    f"not exist: {snapshot_id!r}"
                )

        self._store.devices[device.device_id] = device
