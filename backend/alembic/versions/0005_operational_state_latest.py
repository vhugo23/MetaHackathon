"""Latest live operational state (NPE-1D1A)

Adds ``operational_state_latest``: one row per lab holding the most recent
accepted ``FabricOperationalState`` as a complete JSONB document.
``collection_id`` and ``collected_at`` duplicate fields of that document on
purpose; the repository validates them against the decoded state on every
read. ``schema_version`` versions the document format. No foreign keys (the
lab is not a platform entity), no history table, and no index beyond the
primary key. No existing table, constraint or index is altered.

Downgrade drops only this table.

Revision ID: 0005_operational_state_latest
Revises: 0004_frr_device_vendor
Create Date: 2026-10-11

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_operational_state_latest"
down_revision: str | None = "0004_frr_device_vendor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "operational_state_latest",
        sa.Column("lab_name", sa.Text(), primary_key=True),
        sa.Column("collection_id", sa.Text(), nullable=False),
        sa.Column("collected_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("btrim(lab_name) <> ''", name="ck_operational_state_latest_lab_name"),
        sa.CheckConstraint(
            "btrim(collection_id) <> ''", name="ck_operational_state_latest_collection_id"
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_operational_state_latest_schema_version"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(state) = 'object'", name="ck_operational_state_latest_state_object"
        ),
    )


def downgrade() -> None:
    op.drop_table("operational_state_latest")
