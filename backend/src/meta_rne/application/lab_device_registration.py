"""``RegisterLabDevicesService`` — NPE-1C3B1's explicit, idempotent
registration of the four fixed Lab 1 routers as platform devices (ADR-0003).

The router IDs and the vendor are constants of this module: the service
accepts no device ID, no vendor and no configuration, and performs no
parsing and no network access. Hosts are not registered. Each router is a
snapshot-less ``VendorType.FRR`` device (``current_snapshot_id`` and
``baseline_snapshot_id`` both ``None``).

One ``UnitOfWork`` per call, a single ``commit()`` on success, and the same
exception-preserving rollback/close lifecycle as ``ConfigIngestionService``.
``observed_at`` is caller-supplied; this service never reads a clock.

Idempotent: a router already registered as a snapshot-less FRR device is left
untouched (not re-saved, ``updated_at`` unchanged). A router ID that exists
in any other state (a different vendor, or any snapshot reference) fails
closed with ``LabDeviceConflictError`` before anything is written — an
existing Cisco/Arista device is never converted into FRR.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from meta_rne.application.errors import LabDeviceConflictError
from meta_rne.domain.config import VendorType
from meta_rne.domain.device import Device
from meta_rne.domain.ports import UnitOfWork

LAB1_ROUTER_DEVICE_IDS: tuple[str, ...] = (
    "lab1-spine-1",
    "lab1-spine-2",
    "lab1-leaf-1",
    "lab1-leaf-2",
)
LAB1_ROUTER_VENDOR = VendorType.FRR


@dataclass(frozen=True, slots=True)
class LabDeviceRegistrationResult:
    registered: tuple[str, ...]
    already_registered: tuple[str, ...]


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


def _check_existing(existing: Device) -> None:
    if existing.vendor is not LAB1_ROUTER_VENDOR:
        raise LabDeviceConflictError(
            existing.device_id,
            f"already registered with vendor {existing.vendor.value!r}, "
            f"expected {LAB1_ROUTER_VENDOR.value!r}",
        )
    if existing.current_snapshot_id is not None or existing.baseline_snapshot_id is not None:
        raise LabDeviceConflictError(
            existing.device_id, "already registered with a configuration snapshot reference"
        )


class RegisterLabDevicesService:
    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def register(self, observed_at: datetime) -> LabDeviceRegistrationResult:
        _require_utc(observed_at, "observed_at")

        uow = self._unit_of_work_factory()
        try:
            # Validate every fixed router before writing any, so a conflict on
            # a later ID never leaves earlier IDs half-registered.
            missing: list[str] = []
            present: list[str] = []
            for device_id in LAB1_ROUTER_DEVICE_IDS:
                existing = uow.devices.get_by_id(device_id)
                if existing is None:
                    missing.append(device_id)
                else:
                    _check_existing(existing)
                    present.append(device_id)

            for device_id in missing:
                uow.devices.save(
                    Device(
                        device_id=device_id,
                        vendor=LAB1_ROUTER_VENDOR,
                        current_snapshot_id=None,
                        baseline_snapshot_id=None,
                        created_at=observed_at,
                        updated_at=observed_at,
                    )
                )

            uow.commit()
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
            return LabDeviceRegistrationResult(
                registered=tuple(missing), already_registered=tuple(present)
            )
