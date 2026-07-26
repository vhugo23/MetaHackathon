"""``GetRecentTelemetryService`` — Gate G2B's read-only recent-telemetry
query use case, mirroring ``GetDeviceDriftService``'s exact,
already-approved pattern (Day 9, Gate 3): one ``UnitOfWork`` per call, no
``commit()`` — this is a pure read, never writing anything.

``since`` is validated for UTC-awareness *before* any ``UnitOfWork`` is
created — not after querying the Device. This resolves the otherwise
unspecified error-precedence question (a request with both a missing
Device and an invalid ``since`` always raises ``ValueError``,
deterministically, never depending on which check happens to run first)
and avoids any persistence work for input that was invalid before
persistence was ever consulted, matching ``ConfigIngestionService``'s own
pre-transaction-boundary precedent (Day 5A). "One ``UnitOfWork`` per call"
means one ``UnitOfWork`` per call that passes argument validation — a call
that fails validation creates zero ``UnitOfWork``s.

The service performs exactly one transformation on the repository's
result: converting the returned ``list`` to an immutable ``tuple``. No
sorting, limiting, filtering, or deduplication occurs here — physical
retention and ordering remain entirely a ``TelemetryRepository`` concern,
already fully proven by the existing repository contract-test suites.
Backend divergence (domain-model.md Section 12: PostgreSQL "may retain
longer" than the in-memory double) is a documented permission, never a
guarantee this service enforces or depends on.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.domain.ports import UnitOfWork
from meta_rne.domain.telemetry import TelemetrySample


class GetRecentTelemetryService:
    def __init__(self, unit_of_work_factory: Callable[[], UnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def get(self, device_id: str, since: datetime) -> tuple[TelemetrySample, ...]:
        if since.tzinfo is None or since.utcoffset() != UTC.utcoffset(None):
            raise ValueError("since must be timezone-aware UTC")

        uow = self._unit_of_work_factory()
        try:
            device = uow.devices.get_by_id(device_id)
            if device is None:
                raise DeviceNotFoundError(device_id)

            samples = uow.telemetry_samples.get_recent(device_id, since)
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
            return tuple(samples)
