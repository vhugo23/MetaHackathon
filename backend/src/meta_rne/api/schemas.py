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

from pydantic import BaseModel, ConfigDict, field_validator

from meta_rne.application.models import ConfigIngestionResult
from meta_rne.domain.config import (
    NormalizedAcl,
    NormalizedAclEntry,
    NormalizedBgpNeighbor,
    NormalizedConfiguration,
    NormalizedInterface,
    NormalizedRouting,
)
from meta_rne.domain.incident import Incident, PolicyViolationIncidentEvidence


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


class IncidentResponse(BaseModel):
    incident_id: str
    fingerprint: str
    device_id: str
    source: str
    rule_ref: str
    affected_resource: str
    severity: str
    status: str
    evidence: PolicyViolationIncidentEvidenceResponse
    recommendation: str
    created_at: datetime
    last_seen_at: datetime
    occurrence_count: int
    updated_at: datetime
    resolved_at: datetime | None

    @classmethod
    def from_domain(cls, incident: Incident) -> "IncidentResponse":
        return cls(
            incident_id=incident.incident_id,
            fingerprint=incident.fingerprint,
            device_id=incident.device_id,
            source=incident.source.value,
            rule_ref=incident.rule_ref,
            affected_resource=incident.affected_resource,
            severity=incident.severity.value,
            status=incident.status.value,
            evidence=PolicyViolationIncidentEvidenceResponse.from_domain(incident.evidence),
            recommendation=incident.recommendation,
            created_at=incident.created_at,
            last_seen_at=incident.last_seen_at,
            occurrence_count=incident.occurrence_count,
            updated_at=incident.updated_at,
            resolved_at=incident.resolved_at,
        )


class ApiErrorResponse(BaseModel):
    code: str
    detail: str
