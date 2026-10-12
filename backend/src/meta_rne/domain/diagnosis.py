"""Deterministic diagnosis domain model (NPE-1E1A, ADR-0003).

Pure data: no FastAPI, Pydantic, SQLAlchemy or file I/O. Immutable
``@dataclass(frozen=True, slots=True)`` with ``tuple`` collections.

A ``Diagnosis`` attributes observed symptoms to one declared physical link and a
fault class. It is not an incident and not an anomaly: incidents are
device-scoped symptom records, a diagnosis spans the link's two endpoints.
It is computed on demand from one ``FabricOperationalState`` and is never
persisted here, so it carries no ID, status, incident IDs or stored summary.

``EvidenceRole`` is always relative to the diagnosis the item belongs to:

* ``SUPPORTS`` — the fact is consistent with, and argues for, that diagnosis;
* ``CONTRADICTS`` — the fact argues against it;
* ``UNKNOWN`` — the fact is neutral or indeterminate for it (an unobserved
  fact, a transitional BGP state, or healthy context). It is never failure
  evidence, and it is never proof of health either.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


class DiagnosisType(StrEnum):
    PHYSICAL_LINK_FAILURE = "PHYSICAL_LINK_FAILURE"
    BGP_SESSION_FAILURE = "BGP_SESSION_FAILURE"
    # A diagnostic outcome, not proof of a root cause: a relevant failure
    # symptom exists but no attribution is defensible.
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DiagnosisConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class EndpointInterfaceState(StrEnum):
    UP = "UP"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class EndpointBgpState(StrEnum):
    DOWN = "DOWN"  # observed Idle or Active
    ESTABLISHED = "ESTABLISHED"
    OTHER = "OTHER"  # observed Connect, OpenSent or OpenConfirm
    UNKNOWN = "UNKNOWN"


class EvidenceKind(StrEnum):
    """Declaration order is the evidence sort order."""

    INTERFACE_STATE = "INTERFACE_STATE"
    BGP_SESSION = "BGP_SESSION"
    ADDRESS_MATCH = "ADDRESS_MATCH"
    ROUTE_NEXT_HOP = "ROUTE_NEXT_HOP"
    ECMP_PATHS = "ECMP_PATHS"
    REACHABILITY = "REACHABILITY"


class EvidenceRole(StrEnum):
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class LinkRef:
    """A declared physical link, endpoints in canonical order: ``(node,
    interface)`` of A strictly before B."""

    a_node: str
    a_interface: str
    b_node: str
    b_interface: str

    def __post_init__(self) -> None:
        _require_non_empty(self.a_node, "LinkRef.a_node")
        _require_non_empty(self.a_interface, "LinkRef.a_interface")
        _require_non_empty(self.b_node, "LinkRef.b_node")
        _require_non_empty(self.b_interface, "LinkRef.b_interface")
        if not (self.a_node, self.a_interface) < (self.b_node, self.b_interface):
            raise ValueError("LinkRef endpoints must be in canonical (node, interface) order")

    @property
    def resource_id(self) -> str:
        return f"{self.a_node}:{self.a_interface}--{self.b_node}:{self.b_interface}"


@dataclass(frozen=True, slots=True)
class SymptomKey:
    """Locator of a device-scoped symptom (an eventual incident's
    ``device_id`` / ``affected_resource``). The engine never loads incidents."""

    device_id: str
    affected_resource: str

    def __post_init__(self) -> None:
        _require_non_empty(self.device_id, "SymptomKey.device_id")
        _require_non_empty(self.affected_resource, "SymptomKey.affected_resource")


@dataclass(frozen=True, slots=True)
class InterfaceStateObserved:
    interface_name: str
    state: EndpointInterfaceState


@dataclass(frozen=True, slots=True)
class BgpSessionObserved:
    neighbor_ip: str
    state: EndpointBgpState
    raw_state: str | None  # the device's own state string; None if not observed


@dataclass(frozen=True, slots=True)
class AddressObserved:
    """``matches`` is ``None`` when no comparison was possible."""

    interface_name: str
    declared_address: str
    observed_addresses: tuple[str, ...]
    matches: bool | None


@dataclass(frozen=True, slots=True)
class NextHopObserved:
    """How many active routes list ``next_hop_ip``; ``None`` if routes were not
    observed. Literal: it says nothing about why a route is or is not there."""

    next_hop_ip: str
    interface_name: str
    active_route_count: int | None


@dataclass(frozen=True, slots=True)
class EcmpPathsObserved:
    """Paths to ``prefix`` against the declared design, for the one path that
    losing the diagnosed link would remove (``link_next_hop_ip``).
    ``link_path_present`` is ``None`` if routes were not observed."""

    prefix: str
    observed_path_count: int | None  # None if routes were not observed
    expected_path_count: int
    link_next_hop_ip: str
    link_path_present: bool | None


@dataclass(frozen=True, slots=True)
class ReachabilityObserved:
    """``sent`` / ``received`` are ``None`` if the probe was not observed."""

    target: str
    sent: int | None
    received: int | None


EvidenceObservation = (
    InterfaceStateObserved
    | BgpSessionObserved
    | AddressObserved
    | NextHopObserved
    | EcmpPathsObserved
    | ReachabilityObserved
)

_OBSERVATION_TYPE_BY_KIND: dict[EvidenceKind, type[EvidenceObservation]] = {
    EvidenceKind.INTERFACE_STATE: InterfaceStateObserved,
    EvidenceKind.BGP_SESSION: BgpSessionObserved,
    EvidenceKind.ADDRESS_MATCH: AddressObserved,
    EvidenceKind.ROUTE_NEXT_HOP: NextHopObserved,
    EvidenceKind.ECMP_PATHS: EcmpPathsObserved,
    EvidenceKind.REACHABILITY: ReachabilityObserved,
}


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    kind: EvidenceKind
    node_id: str
    subject: str
    observed: EvidenceObservation
    role: EvidenceRole

    def __post_init__(self) -> None:
        _require_non_empty(self.node_id, "EvidenceItem.node_id")
        _require_non_empty(self.subject, "EvidenceItem.subject")
        expected = _OBSERVATION_TYPE_BY_KIND[self.kind]
        if not isinstance(self.observed, expected):
            raise ValueError(
                f"EvidenceItem.observed must be a {expected.__name__} for kind "
                f"{self.kind.value}, got {type(self.observed).__name__}"
            )


@dataclass(frozen=True, slots=True)
class Diagnosis:
    """``confidence`` is ``None`` exactly for ``INSUFFICIENT_EVIDENCE``."""

    diagnosis_type: DiagnosisType
    resource: LinkRef
    confidence: DiagnosisConfidence | None
    evidence: tuple[EvidenceItem, ...]
    symptom_keys: tuple[SymptomKey, ...]
    collection_id: str
    collected_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.collection_id, "Diagnosis.collection_id")
        _require_utc(self.collected_at, "Diagnosis.collected_at")
        if self.diagnosis_type is DiagnosisType.INSUFFICIENT_EVIDENCE:
            if self.confidence is not None:
                raise ValueError("Diagnosis.confidence must be None for INSUFFICIENT_EVIDENCE")
        elif self.confidence is None:
            raise ValueError(f"Diagnosis.confidence is required for {self.diagnosis_type.value}")
        if not self.evidence:
            raise ValueError("Diagnosis.evidence must not be empty")
