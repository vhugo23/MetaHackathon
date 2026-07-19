"""SQLAlchemy/PostgreSQL DeviceRepository (Day 4B2).

Accepts an already-open ``Session`` — never creates, commits, rolls back,
or closes it (the caller, a test fixture today and the concrete
``UnitOfWork`` in Day 4B3, owns the transaction). Every rejected Device
lifecycle transition is validated *before* any ORM mutation and raises
``DeviceConflictError``, leaving the stored row completely unchanged.
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.persistence.errors import DeviceConflictError
from meta_rne.persistence.sqlalchemy.models import _ConfigurationSnapshotModel, _DeviceModel


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("database timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _to_domain(model: _DeviceModel) -> Device:
    return Device(
        device_id=model.device_id,
        vendor=VendorType(model.vendor),
        current_snapshot_id=model.current_snapshot_id,
        baseline_snapshot_id=model.baseline_snapshot_id,
        created_at=_to_utc(model.created_at),
        updated_at=_to_utc(model.updated_at),
    )


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


class SqlAlchemyDeviceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, device_id: str) -> Device | None:
        model = self._session.get(_DeviceModel, device_id)
        return None if model is None else _to_domain(model)

    def save(self, device: Device) -> None:
        model = self._session.get(_DeviceModel, device.device_id)
        if model is not None:
            _validate_transition(_to_domain(model), device)

        for snapshot_id in (device.current_snapshot_id, device.baseline_snapshot_id):
            if (
                snapshot_id is not None
                and self._session.get(_ConfigurationSnapshotModel, snapshot_id) is None
            ):
                raise DeviceConflictError(
                    f"Device {device.device_id!r} references a snapshot that does "
                    f"not exist: {snapshot_id!r}"
                )

        if model is None:
            self._session.add(
                _DeviceModel(
                    device_id=device.device_id,
                    vendor=device.vendor.value,
                    current_snapshot_id=device.current_snapshot_id,
                    baseline_snapshot_id=device.baseline_snapshot_id,
                    created_at=device.created_at,
                    updated_at=device.updated_at,
                )
            )
        else:
            model.current_snapshot_id = device.current_snapshot_id
            model.baseline_snapshot_id = device.baseline_snapshot_id
            model.updated_at = device.updated_at
        self._session.flush()
