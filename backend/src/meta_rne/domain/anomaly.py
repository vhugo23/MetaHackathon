"""Anomaly domain value objects (FR-06, Gate B).

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. Immutable
``@dataclass(frozen=True, slots=True)`` using ``tuple`` for collections, per
the existing engineering constraints. See docs/domain-model.md Section 9 for
the full (eventual) shape.

No rule trigger logic exists here — this module only defines the shapes
`RuleEngine.evaluate` (a later gate's logic, `detection/rule_engine.py`)
will populate once RULE-CPU-HIGH/RULE-LINK-FLAP/RULE-BGP-DOWN are
implemented.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from meta_rne.domain.telemetry import BgpState, LinkState


class RuleId(StrEnum):
    CPU_HIGH = "RULE-CPU-HIGH"
    LINK_FLAP = "RULE-LINK-FLAP"
    BGP_DOWN = "RULE-BGP-DOWN"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


@dataclass(frozen=True, slots=True)
class CpuSampleEvidence:
    timestamp: datetime
    cpu_utilization_pct: float


@dataclass(frozen=True, slots=True)
class CpuHighEvidence:
    samples: tuple[CpuSampleEvidence, ...]

    def __post_init__(self) -> None:
        for sample in self.samples:
            _require_utc(sample.timestamp, "CpuSampleEvidence.timestamp")


@dataclass(frozen=True, slots=True)
class InterfaceTransitionEvidence:
    timestamp: datetime
    oper_state: LinkState


@dataclass(frozen=True, slots=True)
class LinkFlapEvidence:
    interface_name: str
    transitions: tuple[InterfaceTransitionEvidence, ...]

    def __post_init__(self) -> None:
        for transition in self.transitions:
            _require_utc(transition.timestamp, "InterfaceTransitionEvidence.timestamp")


@dataclass(frozen=True, slots=True)
class BgpDownEvidence:
    neighbor_ip: str
    state: BgpState
    previous_state: BgpState


RuleEvidence = CpuHighEvidence | LinkFlapEvidence | BgpDownEvidence

_EVIDENCE_TYPE_BY_RULE_ID: dict[RuleId, type[RuleEvidence]] = {
    RuleId.CPU_HIGH: CpuHighEvidence,
    RuleId.LINK_FLAP: LinkFlapEvidence,
    RuleId.BGP_DOWN: BgpDownEvidence,
}


@dataclass(frozen=True, slots=True)
class Anomaly:
    device_id: str
    rule_id: RuleId
    evidence: RuleEvidence
    detected_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.device_id, "Anomaly.device_id")
        _require_utc(self.detected_at, "Anomaly.detected_at")

        expected_evidence_type = _EVIDENCE_TYPE_BY_RULE_ID[self.rule_id]
        if not isinstance(self.evidence, expected_evidence_type):
            raise ValueError(
                f"Anomaly.evidence must be a {expected_evidence_type.__name__} "
                f"for rule_id {self.rule_id.value}, got {type(self.evidence).__name__}"
            )
