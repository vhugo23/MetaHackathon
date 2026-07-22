"""``GetDeviceDriftService`` — Day 9 Gate 3's on-demand drift query use case.

Mirrors ``ListIncidentsService``'s exception-preserving ``UnitOfWork``
lifecycle style (Day 5B): one ``UnitOfWork`` per call, no ``commit()`` — this
is a pure read, computing a structural diff and returning it, never writing
anything. ``rollback()`` is still attempted on failure, since a SQLAlchemy
read can open a transaction that needs explicit rollback before the
``Session`` is closed.

Loads the device and its two documented snapshot pointers
(``baseline_snapshot_id``, ``current_snapshot_id`` — domain-model.md Section
2) through the existing ``DeviceRepository``/``ConfigurationSnapshotRepository``
ports only; no new repository method is introduced. A persisted ``Device``
always has both pointers set (``ConfigIngestionService`` sets both together
on first submission and never clears/replaces them afterward), so an
unexpectedly missing referenced snapshot is a broken invariant, not an
expected business case — it is never silently worked around by selecting a
different snapshot.
"""

from collections.abc import Callable

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.detection.drift_detector import DriftDetector
from meta_rne.domain.drift import DriftReport
from meta_rne.domain.ports import UnitOfWork


class GetDeviceDriftService:
    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def get_drift(self, device_id: str) -> DriftReport:
        uow = self._unit_of_work_factory()
        try:
            device = uow.devices.get_by_id(device_id)
            if device is None:
                raise DeviceNotFoundError(device_id)

            baseline_snapshot_id = device.baseline_snapshot_id
            if baseline_snapshot_id is None:
                raise RuntimeError(
                    f"Device {device_id!r} exists but has no baseline_snapshot_id; a "
                    "persisted device must have one set (see ConfigIngestionService)"
                )
            current_snapshot_id = device.current_snapshot_id
            if current_snapshot_id is None:
                raise RuntimeError(
                    f"Device {device_id!r} exists but has no current_snapshot_id; a "
                    "persisted device must have one set (see ConfigIngestionService)"
                )

            baseline_snapshot = uow.configuration_snapshots.get_by_id(baseline_snapshot_id)
            if baseline_snapshot is None:
                raise RuntimeError(
                    f"Device {device_id!r} references a baseline snapshot that does not "
                    f"exist: {baseline_snapshot_id!r}"
                )

            current_snapshot = uow.configuration_snapshots.get_by_id(current_snapshot_id)
            if current_snapshot is None:
                raise RuntimeError(
                    f"Device {device_id!r} references a current snapshot that does not "
                    f"exist: {current_snapshot_id!r}"
                )

            report = DriftDetector.compare(
                baseline_snapshot.normalized_config, current_snapshot.normalized_config
            )
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
            return report
