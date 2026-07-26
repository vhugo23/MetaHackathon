"""Structured JSON log emission on incident creation/update (AC-10).

See docs/architecture.md Section 13 and docs/product-spec.md AC-10 for the
approved event contract. Gate AC-10A (CLAUDE.md "Current Phase") defines the
event model, serialization, and sink only; no application service wires
emission yet.
"""

from meta_rne.observability.incident_events import (
    IncidentEventSink,
    IncidentLogEvent,
    StdoutIncidentEventSink,
)

__all__ = [
    "IncidentEventSink",
    "IncidentLogEvent",
    "StdoutIncidentEventSink",
]
