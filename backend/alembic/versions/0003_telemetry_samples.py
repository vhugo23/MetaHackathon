"""Telemetry samples (Gate E2A)

Adds ``telemetry_samples`` (FR-05): one row per ingested `TelemetrySample`,
``device_id`` foreign-keyed to ``devices`` (no explicit cascade/delete
action, matching ``configuration_snapshots``/``incidents``' precedent),
``interface_states``/``bgp_sessions`` as JSONB (matching
``configuration_snapshots.normalized_config``/``incidents.evidence``'s
precedent). ``id`` is a ``BIGINT GENERATED ALWAYS AS IDENTITY`` primary key
— repository metadata only, never surfaced on the domain `TelemetrySample`
dataclass — used purely as a deterministic tie-break for equal `sampled_at`
values (Gate E2A design audit). No ingestion/created-at timestamp, sample
UUID, uniqueness constraint, or physical-retention field is added: the
`since` parameter on `TelemetryRepository.get_recent` makes retention a
query-time concern, not a storage-time one (domain-model.md Section 12).

Revision ID: 0003_telemetry_samples
Revises: 0002_incident_resolution
Create Date: 2026-07-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003_telemetry_samples"
down_revision: str | None = "0002_incident_resolution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telemetry_samples",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column("device_id", sa.Text(), sa.ForeignKey("devices.device_id"), nullable=False),
        sa.Column("sampled_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("cpu_utilization_pct", sa.Double(), nullable=False),
        sa.Column("memory_utilization_pct", sa.Double(), nullable=False),
        sa.Column("interface_error_rate", sa.Double(), nullable=False),
        sa.Column("interface_states", postgresql.JSONB(), nullable=False),
        sa.Column("bgp_sessions", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "cpu_utilization_pct BETWEEN 0 AND 100", name="ck_telemetry_samples_cpu_range"
        ),
        sa.CheckConstraint(
            "memory_utilization_pct BETWEEN 0 AND 100", name="ck_telemetry_samples_memory_range"
        ),
        sa.CheckConstraint(
            "btrim(device_id) <> ''", name="ck_telemetry_samples_device_id_not_blank"
        ),
    )
    op.create_index(
        "ix_telemetry_samples_device_sampled_at",
        "telemetry_samples",
        ["device_id", "sampled_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_telemetry_samples_device_sampled_at", table_name="telemetry_samples")
    op.drop_table("telemetry_samples")
