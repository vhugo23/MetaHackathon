"""``TelemetryIngestionService`` — Gate F1's telemetry persist-then-detect
use case, built on the approved provenance-tagged Flow F algorithm (Gate
F0's plan, Sections 6/7/9).

One ``UnitOfWork`` per call, mirroring ``ConfigIngestionService``'s
exception-preserving lifecycle exactly: a single ``commit()`` on success,
rollback/close attempted independently on failure, the original exception
never replaced. ``observed_at`` is caller-supplied only (via the command) —
this service never reads a clock, matching ``ConfigIngestionService``.

Flow F computes the *logical post-save* in-memory retention state before
the physical ``save()`` call, then guarantees the current sample
participates in ``RuleEngine.evaluate``'s input exactly once:

1. Read ``get_latest`` (the stored watermark) before saving.
2. ``effective_watermark`` = ``max`` of that watermark and the current
   sample's own ``sampled_at`` — the current sample may itself advance the
   watermark.
3. ``cutoff`` = ``effective_watermark`` minus five minutes.
4. Read ``get_recent(since=cutoff)`` with **no upper bound** — already-
   stored rows *after* the current sample's own timestamp are
   deliberately included, since they legitimately compete for the same
   100-entry retention cap as older rows.
5. Wrap every historical row in a private, provenance-tagged
   ``_EvaluationEntry(sample, is_current=False)``, and append exactly one
   synthetic ``_EvaluationEntry(sample=current, is_current=True)``.
6. Stable-sort the tagged entries by ``sampled_at`` only — this preserves
   the existing repository ordering among historical entries and keeps
   the synthetic current entry after any historical entries sharing its
   timestamp, without ever comparing sample values or object identity.
7. Cap the **full** tagged sequence to the last 100 entries — *before*
   excluding anything future-relative-to-current. This reproduces the
   true post-save retained state: a future row can legitimately displace
   an older row (including the synthetic current entry itself) from the
   cap.
8. From that capped state, exclude future-relative-to-current entries and
   the one entry tagged ``is_current=True`` — never by equality or
   identity, so a historical row that happens to be field-equal to (or,
   in the in-memory backend, the same Python object as) the current
   sample remains its own independent observation.
9. Save the current sample.
10. Append the real ``command.sample`` (not the synthetic entry) exactly
    once, unconditionally — guaranteeing participation even if the
    synthetic entry was evicted by the cap in step 7.
11. Evaluate, commit once, return.

No incident is created; no API/route code; no repository or ``UnitOfWork``
change. Neither the PostgreSQL identity column nor the in-memory insertion
sequence is ever read or exposed.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from meta_rne.application.errors import DeviceNotFoundError
from meta_rne.application.models import TelemetryIngestionCommand, TelemetryIngestionResult
from meta_rne.detection.rule_engine import RuleEngine
from meta_rne.domain.anomaly import Anomaly
from meta_rne.domain.ports import UnitOfWork
from meta_rne.domain.telemetry import TelemetrySample

_RETENTION_WINDOW = timedelta(minutes=5)
_RETENTION_CAP = 100


class _RuleEngineLike(Protocol):
    def evaluate(
        self, observed_at: datetime, recent_samples: list[TelemetrySample]
    ) -> list[Anomaly]: ...


@dataclass(frozen=True, slots=True)
class _EvaluationEntry:
    """Application-internal provenance tag only — never persisted, never
    exposed outside this module, carries no repository identity or
    insertion-sequence metadata. Exactly one entry constructed during an
    ``ingest()`` call may have ``is_current=True``: the synthetic entry
    built for ``command.sample``. Every entry sourced from the repository
    is tagged ``is_current=False``, regardless of whether its ``sample``
    is field-equal to, or (in the in-memory backend) the same Python
    object as, the current sample."""

    sample: TelemetrySample
    is_current: bool


def _build_evaluation_sequence(
    history: list[TelemetrySample], current_sample: TelemetrySample
) -> list[TelemetrySample]:
    entries = [_EvaluationEntry(sample=row, is_current=False) for row in history]
    entries.append(_EvaluationEntry(sample=current_sample, is_current=True))

    # Stable sort: historical entries already precede the synthetic current
    # entry in `entries`, so equal-`sampled_at` ties keep that relative
    # order — never resolved by value or identity comparison.
    entries.sort(key=lambda entry: entry.sample.sampled_at)

    if len(entries) > _RETENTION_CAP:
        entries = entries[-_RETENTION_CAP:]

    evaluation_history = [
        entry.sample
        for entry in entries
        if entry.sample.sampled_at <= current_sample.sampled_at and not entry.is_current
    ]
    evaluation_history.append(current_sample)
    return evaluation_history


class TelemetryIngestionService:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], UnitOfWork],
        rule_engine: _RuleEngineLike = RuleEngine,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._rule_engine = rule_engine

    def ingest(self, command: TelemetryIngestionCommand) -> TelemetryIngestionResult:
        current_sample = command.sample

        uow = self._unit_of_work_factory()
        try:
            device = uow.devices.get_by_id(command.device_id)
            if device is None:
                raise DeviceNotFoundError(command.device_id)

            existing_latest = uow.telemetry_samples.get_latest(command.device_id)
            effective_watermark = max(
                existing_latest.sampled_at
                if existing_latest is not None
                else current_sample.sampled_at,
                current_sample.sampled_at,
            )
            cutoff = effective_watermark - _RETENTION_WINDOW

            history = uow.telemetry_samples.get_recent(command.device_id, since=cutoff)

            evaluation_sequence = _build_evaluation_sequence(history, current_sample)

            uow.telemetry_samples.save(command.device_id, current_sample)

            anomalies = self._rule_engine.evaluate(command.observed_at, evaluation_sequence)

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
            return TelemetryIngestionResult(sample=current_sample, anomalies=tuple(anomalies))
