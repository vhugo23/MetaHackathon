"""Day 5A application command/result types.

Pure data: no FastAPI, SQLAlchemy, or file I/O. ``IngestConfigurationCommand``
carries an external, unverified vendor string exactly as the caller provided
it — resolution against the ``AdapterRegistry`` and derivation of the
canonical ``VendorType`` are ``ConfigIngestionService`` concerns, not this
module's (Day 5A plan item 2). ``ConfigIngestionResult`` represents a
completed, persisted ingestion only — no ORM model or mutable collection may
be stored on it (Day 5A plan item 1).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from meta_rne.domain.config import NormalizedConfiguration


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


def _require_non_negative(value: int, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be >= 0")


@dataclass(frozen=True, slots=True)
class IngestConfigurationCommand:
    device_id: str
    vendor: str
    raw_config_text: str
    observed_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.device_id, "IngestConfigurationCommand.device_id")
        _require_non_empty(self.vendor, "IngestConfigurationCommand.vendor")
        if self.raw_config_text == "":
            raise ValueError("IngestConfigurationCommand.raw_config_text must not be empty")
        _require_utc(self.observed_at, "IngestConfigurationCommand.observed_at")


@dataclass(frozen=True, slots=True)
class ConfigIngestionResult:
    device_id: str
    snapshot_id: str
    normalized_config: NormalizedConfiguration
    violations_detected: int
    incidents_created: int
    incidents_updated: int

    def __post_init__(self) -> None:
        _require_non_empty(self.device_id, "ConfigIngestionResult.device_id")
        _require_non_empty(self.snapshot_id, "ConfigIngestionResult.snapshot_id")
        _require_non_negative(self.violations_detected, "ConfigIngestionResult.violations_detected")
        _require_non_negative(self.incidents_created, "ConfigIngestionResult.incidents_created")
        _require_non_negative(self.incidents_updated, "ConfigIngestionResult.incidents_updated")
        if self.incidents_created + self.incidents_updated != self.violations_detected:
            raise ValueError(
                "ConfigIngestionResult.incidents_created + incidents_updated must equal "
                "violations_detected"
            )
