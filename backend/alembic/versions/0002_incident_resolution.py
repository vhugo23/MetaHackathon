"""Incident resolution (Day 7A)

Adds ``updated_at`` (backfilled from ``last_seen_at``, then tightened to
NOT NULL) and ``resolved_at`` (nullable) to ``incidents``, plus their
chronological/consistency CHECK constraints. No status-column migration is
required: ``incidents.status`` is already TEXT with a CHECK constraint that
permits ``RESOLVED`` (revision 0001). The existing partial unique index
``ux_incidents_open_fingerprint`` (``WHERE status = 'OPEN'``) is untouched —
a resolved row already falls outside it, which is what lets a fingerprint
recur as a brand-new OPEN incident after its predecessor is resolved
(docs/architecture.md Section 11, domain-model.md Section 11).

Revision ID: 0002_incident_resolution
Revises: 0001_slice1_persistence
Create Date: 2026-07-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_incident_resolution"
down_revision: str | None = "0001_slice1_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add updated_at, nullable first so the backfill below can run.
    op.add_column("incidents", sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True))

    # 2. Backfill every existing row from last_seen_at (already NOT NULL,
    #    per revision 0001), so no incident is left without a value.
    op.execute("UPDATE incidents SET updated_at = last_seen_at")

    # 3. Tighten to NOT NULL now that every row has a value.
    op.alter_column("incidents", "updated_at", nullable=False)

    # 4. Chronological constraint: updated_at can never precede last_seen_at
    #    (which is itself already constrained to be >= created_at).
    op.create_check_constraint(
        "ck_incidents_updated_at_after_last_seen_at",
        "incidents",
        "updated_at >= last_seen_at",
    )

    # 5. Add resolved_at, nullable — NULL for every pre-existing (and every
    #    current OPEN) row.
    op.add_column("incidents", sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True))

    # 6. resolved_at/status consistency: set iff RESOLVED.
    op.create_check_constraint(
        "ck_incidents_resolved_at_matches_status",
        "incidents",
        "(status = 'RESOLVED' AND resolved_at IS NOT NULL) "
        "OR (status <> 'RESOLVED' AND resolved_at IS NULL)",
    )

    # 7. resolved_at, when present, may not be later than updated_at (both
    #    are set from the same captured Clock value on resolve()).
    op.create_check_constraint(
        "ck_incidents_resolved_at_before_or_equal_updated_at",
        "incidents",
        "resolved_at IS NULL OR resolved_at <= updated_at",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_incidents_resolved_at_before_or_equal_updated_at", "incidents", type_="check"
    )
    op.drop_constraint("ck_incidents_resolved_at_matches_status", "incidents", type_="check")
    op.drop_column("incidents", "resolved_at")

    op.drop_constraint("ck_incidents_updated_at_after_last_seen_at", "incidents", type_="check")
    op.drop_column("incidents", "updated_at")
