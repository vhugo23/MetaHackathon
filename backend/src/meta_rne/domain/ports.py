"""Vendor adapter and persistence ports (architecture.md Sections 5, 11).

All structural (``typing.Protocol``) interfaces, not ABCs — any object with
matching attributes/methods satisfies a port without inheriting from
anything in this module. Pure interfaces: no SQLAlchemy or other framework
type appears here (NFR-02). Day 4B1 defines these Protocols only — no
concrete (SQLAlchemy or in-memory) implementation exists yet; those are
Day 4B2 (Device/ConfigurationSnapshot/ConfigurationPolicy repositories,
policy seeding) and Day 4B3 (IncidentRepository's atomic upsert, the
concrete UnitOfWork) — see CLAUDE.md "Current Phase".
"""

from datetime import datetime
from typing import Protocol, runtime_checkable

from meta_rne.domain.config import NormalizedConfiguration
from meta_rne.domain.device import Device
from meta_rne.domain.errors import ParseError
from meta_rne.domain.incident import Incident, IncidentCandidate, IncidentUpsertResult
from meta_rne.domain.policy import ConfigurationPolicy
from meta_rne.domain.snapshot import ConfigurationSnapshot


@runtime_checkable
class VendorConfigAdapter(Protocol):
    vendor_id: str

    def parse(self, raw_text: str) -> NormalizedConfiguration | ParseError: ...


class DeviceRepository(Protocol):
    """Upsert-by-``device_id`` semantics (Day 4B2 implements the lifecycle
    invariants: created_at immutable, baseline set-once, vendor immutable)."""

    def get_by_id(self, device_id: str) -> Device | None: ...
    def save(self, device: Device) -> None: ...


class ConfigurationSnapshotRepository(Protocol):
    """Append-only — ``add`` must reject a repeated ``snapshot_id`` (Day 4B2,
    via a persistence-facing conflict error, never a leaked SQLAlchemy
    exception)."""

    def add(self, snapshot: ConfigurationSnapshot) -> None: ...
    def get_by_id(self, snapshot_id: str) -> ConfigurationSnapshot | None: ...


class ConfigurationPolicyRepository(Protocol):
    """Read-mostly, seeded. Exact ``applies_to == device_id`` matching only —
    no ``"*"`` wildcard resolution (domain-model.md Section 6, Day 3B)."""

    def get_applicable_to_device(self, device_id: str) -> tuple[ConfigurationPolicy, ...]: ...
    def seed_if_missing(self, policies: tuple[ConfigurationPolicy, ...]) -> None: ...


class IncidentRepository(Protocol):
    """``upsert_open_incident`` is the only write path — atomic create-or-
    update, never a find-then-save sequence (domain-model.md Section 11).
    No ``find_open_by_fingerprint``: dropped from this port's public surface
    per Day 4B1's binding decision — the atomic upsert itself is the
    deduplication mechanism."""

    def upsert_open_incident(
        self,
        candidate: IncidentCandidate,
        fingerprint: str,
        observed_at: datetime,
    ) -> IncidentUpsertResult: ...

    def get_by_id(self, incident_id: str) -> Incident | None: ...
    def list_all(self) -> tuple[Incident, ...]: ...


class UnitOfWork(Protocol):
    """One session/transaction per instance (Day 4B3's concrete
    ``SqlAlchemyUnitOfWork``); every repository below shares it."""

    devices: DeviceRepository
    configuration_snapshots: ConfigurationSnapshotRepository
    configuration_policies: ConfigurationPolicyRepository
    incidents: IncidentRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...
