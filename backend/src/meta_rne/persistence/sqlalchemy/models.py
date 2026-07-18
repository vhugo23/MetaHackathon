"""Private SQLAlchemy declarative ORM models (Day 4B1).

These classes are internal to the ``persistence`` package — never imported
or returned outside it (architecture.md Section 2: "``persistence``... must
not [leak] to `domain`"). Repositories (Day 4B2/4B3) will explicitly convert
between these ORM rows and the immutable domain dataclasses in
``meta_rne.domain``; nothing here is ever handed back to a caller directly.

The migration (``alembic/versions/``) is the source of truth for the actual
DDL, including the two-stage ``devices <-> configuration_snapshots`` foreign
keys and every CHECK constraint — the ``__table_args__`` below mirror that
DDL for documentation and any future Alembic autogenerate diffing, they do
not drive schema creation (``Base.metadata.create_all()`` is never used).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    TIMESTAMP,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    pass


class _DeviceModel(_Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(Text, primary_key=True)
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    current_snapshot_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "configuration_snapshots.snapshot_id",
            use_alter=True,
            name="fk_devices_current_snapshot",
        ),
    )
    baseline_snapshot_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey(
            "configuration_snapshots.snapshot_id",
            use_alter=True,
            name="fk_devices_baseline_snapshot",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("updated_at >= created_at", name="ck_devices_updated_at_after_created_at"),
        CheckConstraint("vendor IN ('cisco-ios-xe', 'arista-eos')", name="ck_devices_vendor"),
    )


class _ConfigurationSnapshotModel(_Base):
    __tablename__ = "configuration_snapshots"

    snapshot_id: Mapped[str] = mapped_column(Text, primary_key=True)
    device_id: Mapped[str] = mapped_column(Text, ForeignKey("devices.device_id"), nullable=False)
    vendor: Mapped[str] = mapped_column(Text, nullable=False)
    raw_config_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_text_hash: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "raw_text_hash ~ '^[0-9a-f]{64}$'",
            name="ck_configuration_snapshots_hash_format",
        ),
        CheckConstraint(
            "vendor IN ('cisco-ios-xe', 'arista-eos')",
            name="ck_configuration_snapshots_vendor",
        ),
        Index("ix_configuration_snapshots_device_id", "device_id"),
    )


class _ConfigurationPolicyModel(_Base):
    __tablename__ = "configuration_policies"

    policy_id: Mapped[str] = mapped_column(Text, primary_key=True)
    # No FK to devices: seed policies are inserted before any device row
    # exists (architecture.md Section 11.2's seeding-after-migration order).
    applies_to: Mapped[str] = mapped_column(Text, nullable=False)
    required_acls: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class _IncidentModel(_Base):
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(Text, primary_key=True)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    device_id: Mapped[str] = mapped_column(Text, ForeignKey("devices.device_id"), nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    rule_ref: Mapped[str] = mapped_column(Text, nullable=False)
    affected_resource: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        CheckConstraint(
            "source IN ('POLICY_VIOLATION', 'DRIFT', 'ANOMALY')", name="ck_incidents_source"
        ),
        CheckConstraint(
            "severity IN ('Critical', 'High', 'Medium', 'Low')", name="ck_incidents_severity"
        ),
        CheckConstraint(
            "status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')", name="ck_incidents_status"
        ),
        CheckConstraint("occurrence_count >= 1", name="ck_incidents_occurrence_count_min"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="ck_incidents_fingerprint_format"),
        CheckConstraint("last_seen_at >= created_at", name="ck_incidents_last_seen_after_created"),
        Index("ix_incidents_device_id", "device_id"),
        Index(
            "ux_incidents_open_fingerprint",
            "fingerprint",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )


metadata = _Base.metadata
