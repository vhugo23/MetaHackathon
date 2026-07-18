"""Persisted Device domain object (Day 4B1).

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. Lifecycle
enforcement (baseline set-once, vendor immutable, created_at immutable
across an upsert) is a repository-level concern (Day 4B2) — this dataclass
only validates its own field-level invariants at construction time. See
docs/domain-model.md Section 2.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from meta_rne.domain.config import VendorType


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str
    vendor: VendorType
    current_snapshot_id: str | None
    baseline_snapshot_id: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.device_id, "Device.device_id")
        _require_utc(self.created_at, "Device.created_at")
        _require_utc(self.updated_at, "Device.updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("Device.updated_at must not precede Device.created_at")
