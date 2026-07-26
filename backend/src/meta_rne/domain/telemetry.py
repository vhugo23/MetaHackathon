"""Telemetry domain value objects (FR-05, Gate A).

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. Immutable
``@dataclass(frozen=True, slots=True)`` using ``tuple`` for collections, per
the existing Day 3A/3B/Day 9 engineering constraints. See
docs/domain-model.md Section 8 for the full (eventual) shape.

`RuleEngine`, `Anomaly`, telemetry ingestion, and persistence are later
gates — this module only defines the shape a caller or simulator submits.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class LinkState(StrEnum):
    UP = "up"
    DOWN = "down"


class BgpState(StrEnum):
    IDLE = "Idle"
    CONNECT = "Connect"
    ACTIVE = "Active"
    OPEN_SENT = "OpenSent"
    OPEN_CONFIRM = "OpenConfirm"
    ESTABLISHED = "Established"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


def _require_percentage(value: float, field_name: str) -> None:
    if not 0 <= value <= 100:
        raise ValueError(f"{field_name} must be within [0, 100], got {value}")


@dataclass(frozen=True, slots=True)
class InterfaceState:
    name: str
    oper_state: LinkState


@dataclass(frozen=True, slots=True)
class BgpSession:
    neighbor_ip: str
    state: BgpState


@dataclass(frozen=True, slots=True)
class TelemetrySample:
    device_id: str
    sampled_at: datetime
    cpu_utilization_pct: float
    memory_utilization_pct: float
    interface_error_rate: float
    interface_states: tuple[InterfaceState, ...]
    bgp_sessions: tuple[BgpSession, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.device_id, "TelemetrySample.device_id")
        _require_utc(self.sampled_at, "TelemetrySample.sampled_at")
        _require_percentage(self.cpu_utilization_pct, "TelemetrySample.cpu_utilization_pct")
        _require_percentage(self.memory_utilization_pct, "TelemetrySample.memory_utilization_pct")
