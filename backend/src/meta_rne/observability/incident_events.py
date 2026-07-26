"""Structured JSON event model, serialization, and emission sink for
incident create/update outcomes (AC-10).

Approved contract (docs/product-spec.md AC-10, docs/architecture.md Section
13): exactly seven fields — incident_id, device_id, rule_ref, severity,
status, outcome, timestamp. `timestamp` is `Incident.last_seen_at`, never a
fresh clock read (Day 10A0 binding decision — see CLAUDE.md "Current
Phase"). `source`, `affected_resource`, `evidence`, `fingerprint`,
`recommendation`, `occurrence_count`, `created_at`, `last_seen_at`, and
`resolved_at` are deliberately excluded.

This module defines the event shape, its serialization, and a stdout sink
only. No application service wires emission yet (Gate AC-10A).
"""

import json
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, TextIO

from meta_rne.domain.incident import IncidentStatus, IncidentUpsertOutcome, IncidentUpsertResult
from meta_rne.domain.policy import Severity


def _format_utc_timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class IncidentLogEvent:
    """The seven-field AC-10 event. Typed enum/datetime values are kept
    internally; `to_json` renders each as its documented public string
    representation."""

    incident_id: str
    device_id: str
    rule_ref: str
    severity: Severity
    status: IncidentStatus
    outcome: IncidentUpsertOutcome
    timestamp: datetime

    @classmethod
    def from_upsert_result(cls, result: IncidentUpsertResult) -> "IncidentLogEvent":
        incident = result.incident
        return cls(
            incident_id=incident.incident_id,
            device_id=incident.device_id,
            rule_ref=incident.rule_ref,
            severity=incident.severity,
            status=incident.status,
            outcome=result.outcome,
            timestamp=incident.last_seen_at,
        )

    def to_json(self) -> str:
        payload = {
            "incident_id": self.incident_id,
            "device_id": self.device_id,
            "rule_ref": self.rule_ref,
            "severity": self.severity.value,
            "status": self.status.value,
            "outcome": self.outcome.value,
            "timestamp": _format_utc_timestamp(self.timestamp),
        }
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class IncidentEventSink(Protocol):
    def emit(self, event: IncidentLogEvent) -> None: ...


class StdoutIncidentEventSink:
    """Writes one JSON-lines event per `emit` call to the supplied stream
    (production default: `sys.stdout`, read at call time, never captured at
    construction time), flushing after every write."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream

    def emit(self, event: IncidentLogEvent) -> None:
        stream = self._stream if self._stream is not None else sys.stdout
        stream.write(event.to_json())
        stream.write("\n")
        stream.flush()
