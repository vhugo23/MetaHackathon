"""Explicit Pydantic request/response schemas (Day 5B).

Every response schema has an explicit ``from_domain`` classmethod — no
domain dataclass is ever returned directly, and no schema uses
``ConfigDict(from_attributes=True)`` to auto-copy fields, since several
domain fields are enums that need explicit ``.value`` extraction. Field
sets mirror the *actual* current domain/application types
(``domain/config.py``, ``domain/incident.py``, ``application/models.py``)
exactly — no field is invented to match older planning-doc examples (e.g.
``NormalizedRouting`` has no ``static_routes`` field yet, so this module
adds none).

Success responses are the resource itself — no ``{"data": ..., "error":
None}`` envelope (Day 5B binding correction).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from meta_rne.application.models import ConfigIngestionResult, TelemetryIngestionResult
from meta_rne.domain.anomaly import (
    Anomaly,
    BgpDownEvidence,
    CpuHighEvidence,
    LinkFlapEvidence,
)
from meta_rne.domain.config import (
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
)
from meta_rne.domain.drift import DriftEntry, DriftReport
from meta_rne.domain.incident import Incident, PolicyViolationIncidentEvidence
from meta_rne.domain.telemetry import BgpState, LinkState, TelemetrySample


class SubmitConfigurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str
    raw_config_text: str

    @field_validator("vendor")
    @classmethod
    def _vendor_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("vendor must not be empty or whitespace-only")
        return value

    @field_validator("raw_config_text")
    @classmethod
    def _raw_config_text_not_empty(cls, value: str) -> str:
        if value == "":
            raise ValueError("raw_config_text must not be empty")
        return value


class NormalizedAclEntryResponse(BaseModel):
    sequence: int
    action: str
    protocol: str
    source: str
    destination: str

    @classmethod
    def from_domain(cls, entry: NormalizedAclEntry) -> "NormalizedAclEntryResponse":
        return cls(
            sequence=entry.sequence,
            action=entry.action.value,
            protocol=entry.protocol,
            source=entry.source,
            destination=entry.destination,
        )


class NormalizedAclResponse(BaseModel):
    name: str
    entries: list[NormalizedAclEntryResponse]

    @classmethod
    def from_domain(cls, acl: NormalizedAcl) -> "NormalizedAclResponse":
        return cls(
            name=acl.name,
            entries=[NormalizedAclEntryResponse.from_domain(entry) for entry in acl.entries],
        )


class NormalizedInterfaceResponse(BaseModel):
    name: str
    description: str | None
    ip_address: str | None
    mtu: int | None
    admin_state: str
    acl_in: str | None
    acl_out: str | None

    @classmethod
    def from_domain(cls, interface: NormalizedInterface) -> "NormalizedInterfaceResponse":
        return cls(
            name=interface.name,
            description=interface.description,
            ip_address=interface.ip_address,
            mtu=interface.mtu,
            admin_state=interface.admin_state.value,
            acl_in=interface.acl_in,
            acl_out=interface.acl_out,
        )


class NormalizedBgpNeighborResponse(BaseModel):
    neighbor_ip: str
    remote_as: int

    @classmethod
    def from_domain(cls, neighbor: NormalizedBgpNeighbor) -> "NormalizedBgpNeighborResponse":
        return cls(neighbor_ip=neighbor.neighbor_ip, remote_as=neighbor.remote_as)


class NormalizedRoutingResponse(BaseModel):
    bgp_neighbors: list[NormalizedBgpNeighborResponse]

    @classmethod
    def from_domain(cls, routing: NormalizedRouting) -> "NormalizedRoutingResponse":
        return cls(
            bgp_neighbors=[
                NormalizedBgpNeighborResponse.from_domain(neighbor)
                for neighbor in routing.bgp_neighbors
            ]
        )


class NormalizedConfigurationResponse(BaseModel):
    hostname: str
    interfaces: list[NormalizedInterfaceResponse]
    routing: NormalizedRoutingResponse
    acls: list[NormalizedAclResponse]

    @classmethod
    def from_domain(cls, config: NormalizedConfiguration) -> "NormalizedConfigurationResponse":
        return cls(
            hostname=config.hostname,
            interfaces=[
                NormalizedInterfaceResponse.from_domain(interface)
                for interface in config.interfaces
            ],
            routing=NormalizedRoutingResponse.from_domain(config.routing),
            acls=[NormalizedAclResponse.from_domain(acl) for acl in config.acls],
        )


class SubmitConfigurationResponse(BaseModel):
    device_id: str
    snapshot_id: str
    normalized_config: NormalizedConfigurationResponse
    violations_detected: int
    incidents_created: int
    incidents_updated: int

    @classmethod
    def from_domain(cls, result: ConfigIngestionResult) -> "SubmitConfigurationResponse":
        return cls(
            device_id=result.device_id,
            snapshot_id=result.snapshot_id,
            normalized_config=NormalizedConfigurationResponse.from_domain(result.normalized_config),
            violations_detected=result.violations_detected,
            incidents_created=result.incidents_created,
            incidents_updated=result.incidents_updated,
        )


class PolicyViolationIncidentEvidenceResponse(BaseModel):
    source_snapshot_id: str
    violation_type: str
    expected_acl_name: str
    actual_acl_name: str | None
    interface_name: str
    direction: str

    @classmethod
    def from_domain(
        cls, evidence: PolicyViolationIncidentEvidence
    ) -> "PolicyViolationIncidentEvidenceResponse":
        return cls(
            source_snapshot_id=evidence.source_snapshot_id,
            violation_type=evidence.violation_type.value,
            expected_acl_name=evidence.expected_acl_name,
            actual_acl_name=evidence.actual_acl_name,
            interface_name=evidence.interface_name,
            direction=evidence.direction.value,
        )


class CpuSampleEvidenceResponse(BaseModel):
    timestamp: datetime
    cpu_utilization_pct: float


class CpuHighEvidenceResponse(BaseModel):
    samples: list[CpuSampleEvidenceResponse]

    @classmethod
    def from_domain(cls, evidence: CpuHighEvidence) -> "CpuHighEvidenceResponse":
        return cls(
            samples=[
                CpuSampleEvidenceResponse(
                    timestamp=sample.timestamp, cpu_utilization_pct=sample.cpu_utilization_pct
                )
                for sample in evidence.samples
            ]
        )


class InterfaceTransitionEvidenceResponse(BaseModel):
    timestamp: datetime
    oper_state: str


class LinkFlapEvidenceResponse(BaseModel):
    interface_name: str
    transitions: list[InterfaceTransitionEvidenceResponse]

    @classmethod
    def from_domain(cls, evidence: LinkFlapEvidence) -> "LinkFlapEvidenceResponse":
        return cls(
            interface_name=evidence.interface_name,
            transitions=[
                InterfaceTransitionEvidenceResponse(
                    timestamp=transition.timestamp, oper_state=transition.oper_state.value
                )
                for transition in evidence.transitions
            ],
        )


class BgpDownEvidenceResponse(BaseModel):
    neighbor_ip: str
    previous_state: str
    state: str

    @classmethod
    def from_domain(cls, evidence: BgpDownEvidence) -> "BgpDownEvidenceResponse":
        return cls(
            neighbor_ip=evidence.neighbor_ip,
            previous_state=evidence.previous_state.value,
            state=evidence.state.value,
        )


class IncidentResponse(BaseModel):
    incident_id: str
    fingerprint: str
    device_id: str
    source: str
    rule_ref: str
    affected_resource: str
    severity: str
    status: str
    evidence: (
        PolicyViolationIncidentEvidenceResponse
        | CpuHighEvidenceResponse
        | LinkFlapEvidenceResponse
        | BgpDownEvidenceResponse
    )
    recommendation: str
    created_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    updated_at: datetime
    resolved_at: datetime | None

    @classmethod
    def from_domain(cls, incident: Incident) -> "IncidentResponse":
        evidence_response: (
            PolicyViolationIncidentEvidenceResponse
            | CpuHighEvidenceResponse
            | LinkFlapEvidenceResponse
            | BgpDownEvidenceResponse
        )
        if isinstance(incident.evidence, PolicyViolationIncidentEvidence):
            evidence_response = PolicyViolationIncidentEvidenceResponse.from_domain(
                incident.evidence
            )
        elif isinstance(incident.evidence, CpuHighEvidence):
            evidence_response = CpuHighEvidenceResponse.from_domain(incident.evidence)
        elif isinstance(incident.evidence, LinkFlapEvidence):
            evidence_response = LinkFlapEvidenceResponse.from_domain(incident.evidence)
        elif isinstance(incident.evidence, BgpDownEvidence):
            evidence_response = BgpDownEvidenceResponse.from_domain(incident.evidence)
        else:
            raise ValueError(
                f"unrecognized Incident.evidence type: {type(incident.evidence).__name__}"
            )

        return cls(
            incident_id=incident.incident_id,
            fingerprint=incident.fingerprint,
            device_id=incident.device_id,
            source=incident.source.value,
            rule_ref=incident.rule_ref,
            affected_resource=incident.affected_resource,
            severity=incident.severity.value,
            status=incident.status.value,
            evidence=evidence_response,
            recommendation=incident.recommendation,
            created_at=incident.created_at,
            last_seen_at=incident.last_seen_at,
            occurrence_count=incident.occurrence_count,
            updated_at=incident.updated_at,
            resolved_at=incident.resolved_at,
        )


class DriftEntryResponse(BaseModel):
    resource: str
    field: str | None
    old_value: str | None
    new_value: str | None

    @classmethod
    def from_domain(cls, entry: DriftEntry) -> "DriftEntryResponse":
        return cls(
            resource=entry.resource,
            field=entry.field,
            old_value=entry.old_value,
            new_value=entry.new_value,
        )


class DriftReportResponse(BaseModel):
    added: list[DriftEntryResponse]
    removed: list[DriftEntryResponse]
    changed: list[DriftEntryResponse]

    @classmethod
    def from_domain(cls, report: DriftReport) -> "DriftReportResponse":
        return cls(
            added=[DriftEntryResponse.from_domain(entry) for entry in report.added],
            removed=[DriftEntryResponse.from_domain(entry) for entry in report.removed],
            changed=[DriftEntryResponse.from_domain(entry) for entry in report.changed],
        )


class InterfaceStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    oper_state: LinkState


class BgpSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    neighbor_ip: str
    state: BgpState


class SubmitTelemetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sampled_at: datetime
    cpu_utilization_pct: float
    memory_utilization_pct: float
    interface_error_rate: float
    interface_states: list[InterfaceStateRequest] = Field(default_factory=list)
    bgp_sessions: list[BgpSessionRequest] = Field(default_factory=list)


class InterfaceStateResponse(BaseModel):
    name: str
    oper_state: str


class BgpSessionResponse(BaseModel):
    neighbor_ip: str
    state: str


class TelemetrySampleResponse(BaseModel):
    device_id: str
    sampled_at: datetime
    cpu_utilization_pct: float
    memory_utilization_pct: float
    interface_error_rate: float
    interface_states: list[InterfaceStateResponse]
    bgp_sessions: list[BgpSessionResponse]

    @classmethod
    def from_domain(cls, sample: TelemetrySample) -> "TelemetrySampleResponse":
        return cls(
            device_id=sample.device_id,
            sampled_at=sample.sampled_at,
            cpu_utilization_pct=sample.cpu_utilization_pct,
            memory_utilization_pct=sample.memory_utilization_pct,
            interface_error_rate=sample.interface_error_rate,
            interface_states=[
                InterfaceStateResponse(name=state.name, oper_state=state.oper_state.value)
                for state in sample.interface_states
            ],
            bgp_sessions=[
                BgpSessionResponse(neighbor_ip=session.neighbor_ip, state=session.state.value)
                for session in sample.bgp_sessions
            ],
        )


class AnomalyResponse(BaseModel):
    device_id: str
    rule_id: str
    detected_at: datetime
    evidence: CpuHighEvidenceResponse | LinkFlapEvidenceResponse | BgpDownEvidenceResponse

    @classmethod
    def from_domain(cls, anomaly: Anomaly) -> "AnomalyResponse":
        evidence_response: (
            CpuHighEvidenceResponse | LinkFlapEvidenceResponse | BgpDownEvidenceResponse
        )
        if isinstance(anomaly.evidence, CpuHighEvidence):
            evidence_response = CpuHighEvidenceResponse.from_domain(anomaly.evidence)
        elif isinstance(anomaly.evidence, LinkFlapEvidence):
            evidence_response = LinkFlapEvidenceResponse.from_domain(anomaly.evidence)
        elif isinstance(anomaly.evidence, BgpDownEvidence):
            evidence_response = BgpDownEvidenceResponse.from_domain(anomaly.evidence)
        else:
            raise ValueError(f"unrecognized RuleEvidence type: {type(anomaly.evidence).__name__}")

        return cls(
            device_id=anomaly.device_id,
            rule_id=anomaly.rule_id.value,
            detected_at=anomaly.detected_at,
            evidence=evidence_response,
        )


class SubmitTelemetryResponse(BaseModel):
    sample: TelemetrySampleResponse
    anomalies: list[AnomalyResponse]

    @classmethod
    def from_domain(cls, result: TelemetryIngestionResult) -> "SubmitTelemetryResponse":
        return cls(
            sample=TelemetrySampleResponse.from_domain(result.sample),
            anomalies=[AnomalyResponse.from_domain(anomaly) for anomaly in result.anomalies],
        )


class ApiErrorResponse(BaseModel):
    code: str
    detail: str
