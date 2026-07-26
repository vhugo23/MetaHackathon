"""In-memory TelemetryRepository (Gate E1) — a fast, standalone test double,
never used in production (ADR-0002), and not yet wired into
InMemoryUnitOfWork (that is Gate E2's concern, alongside the SQLAlchemy
implementation, so the UnitOfWork Protocol is never left unsatisfiable).

Retention is a per-device event-time watermark (the greatest sampled_at
ever saved for that device — never datetime.now(), processing time, or an
injected clock), recomputed from scratch on every save(): entries older
than watermark - 5 minutes (inclusive boundary) are pruned, then at most
the latest 100 survivors (by sampled_at, insertion_sequence ascending) are
retained. A late, out-of-order sample is always accepted by save() — it may
be immediately pruned by that same call's retention pass, but it is never
rejected. Duplicate samples and duplicate sampled_at values are both
retained; there is no identity/deduplication concept for TelemetrySample.
"""

from datetime import datetime, timedelta

from meta_rne.domain.telemetry import TelemetrySample
from meta_rne.persistence.memory.store import InMemoryStore, _StoredTelemetrySample

_RETENTION_WINDOW = timedelta(minutes=5)
_RETENTION_CAP = 100


def _canonical_order(entry: _StoredTelemetrySample) -> tuple[datetime, int]:
    return (entry.sample.sampled_at, entry.insertion_sequence)


class InMemoryTelemetryRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def save(self, device_id: str, sample: TelemetrySample) -> None:
        if device_id != sample.device_id:
            raise ValueError(
                f"device_id {device_id!r} does not match sample.device_id " f"{sample.device_id!r}"
            )

        sequence = self._store.telemetry_sequence
        self._store.telemetry_sequence += 1

        existing = self._store.telemetry_samples.get(device_id, ())
        entries = (*existing, _StoredTelemetrySample(sample=sample, insertion_sequence=sequence))
        entries = tuple(sorted(entries, key=_canonical_order))

        watermark = entries[-1].sample.sampled_at
        cutoff = watermark - _RETENTION_WINDOW
        entries = tuple(entry for entry in entries if entry.sample.sampled_at >= cutoff)
        entries = entries[-_RETENTION_CAP:]

        self._store.telemetry_samples[device_id] = entries

    def get_latest(self, device_id: str) -> TelemetrySample | None:
        entries = self._store.telemetry_samples.get(device_id, ())
        if not entries:
            return None
        return entries[-1].sample

    def get_recent(self, device_id: str, since: datetime) -> list[TelemetrySample]:
        entries = self._store.telemetry_samples.get(device_id, ())
        return [entry.sample for entry in entries if entry.sample.sampled_at >= since]
