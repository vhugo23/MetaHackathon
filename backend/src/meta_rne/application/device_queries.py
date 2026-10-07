"""``ListDevicesService``/``GetDeviceDetailService`` — Day 12A1/12B's
read-only registered-device query use cases.

Both mirror ``ListIncidentsService``'s exception-preserving ``UnitOfWork``
lifecycle style (Day 5B): one ``UnitOfWork`` per call, ``close()`` always
attempted exactly once. Unlike ``ConfigIngestionService`` there is nothing
to ``commit()`` — these are pure reads — but ``rollback()`` is still
attempted on failure, since a SQLAlchemy read can open a transaction that
needs explicit rollback before the ``Session`` is closed.

``GetDeviceDetailService`` (Day 12B) loads the device and its current
configuration snapshot through the existing ``DeviceRepository``/
``ConfigurationSnapshotRepository`` ports only — no new repository method
is introduced, mirroring ``GetDeviceDriftService``'s established pattern.
A persisted ``Device`` always has ``current_snapshot_id`` set
(``ConfigIngestionService`` sets it on first submission and never clears
it), so an unexpectedly missing referenced snapshot is a broken invariant,
not an expected business case — it is never silently worked around.
"""

from collections.abc import Callable
from dataclasses import dataclass

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.domain.config import NormalizedConfiguration, VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.ports import UnitOfWork


class ListDevicesService:
    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def list_all(self) -> tuple[Device, ...]:
        uow = self._unit_of_work_factory()
        try:
            devices = uow.devices.list_all()
        except Exception as original_error:
            try:
                uow.rollback()
            except Exception as rollback_error:
                original_error.add_note(f"UnitOfWork rollback also failed: {rollback_error!r}")
            try:
                uow.close()
            except Exception as close_error:
                original_error.add_note(f"UnitOfWork close also failed: {close_error!r}")
            raise
        else:
            uow.close()
            return devices


@dataclass(frozen=True, slots=True)
class DeviceDetailResult:
    """``normalized_config`` is ``None`` only for a device kind that carries
    no configuration snapshots (an FRR Lab 1 router) — never fabricated."""

    device: Device
    normalized_config: NormalizedConfiguration | None


def _detail_from_current_snapshot(uow: UnitOfWork, device: Device) -> DeviceDetailResult:
    device_id = device.device_id
    current_snapshot_id = device.current_snapshot_id
    if current_snapshot_id is None:
        raise RuntimeError(
            f"Device {device_id!r} exists but has no current_snapshot_id; a "
            "persisted device must have one set (see ConfigIngestionService)"
        )

    current_snapshot = uow.configuration_snapshots.get_by_id(current_snapshot_id)
    if current_snapshot is None:
        raise RuntimeError(
            f"Device {device_id!r} references a current snapshot that does not "
            f"exist: {current_snapshot_id!r}"
        )

    if current_snapshot.device_id != device.device_id:
        raise RuntimeError(
            f"Device {device_id!r} current snapshot {current_snapshot_id!r} belongs "
            f"to a different device_id: {current_snapshot.device_id!r}"
        )
    if current_snapshot.vendor != device.vendor:
        raise RuntimeError(
            f"Device {device_id!r} current snapshot {current_snapshot_id!r} vendor "
            f"{current_snapshot.vendor.value!r} differs from device vendor "
            f"{device.vendor.value!r}"
        )

    return DeviceDetailResult(
        device=device,
        normalized_config=current_snapshot.normalized_config,
    )


class GetDeviceDetailService:
    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def get(self, device_id: str) -> DeviceDetailResult:
        uow = self._unit_of_work_factory()
        try:
            device = uow.devices.get_by_id(device_id)
            if device is None:
                raise DeviceNotFoundError(device_id)

            if device.vendor is VendorType.FRR:
                # Snapshot-less by design (Device enforces both pointers None).
                result = DeviceDetailResult(device=device, normalized_config=None)
            else:
                result = _detail_from_current_snapshot(uow, device)
        except Exception as original_error:
            try:
                uow.rollback()
            except Exception as rollback_error:
                original_error.add_note(f"UnitOfWork rollback also failed: {rollback_error!r}")
            try:
                uow.close()
            except Exception as close_error:
                original_error.add_note(f"UnitOfWork close also failed: {close_error!r}")
            raise
        else:
            uow.close()
            return result
