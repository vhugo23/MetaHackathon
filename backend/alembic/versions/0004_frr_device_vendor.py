"""Allow the FRR vendor identity on devices (NPE-1C3B1)

Widens only ``ck_devices_vendor`` to admit ``'frr'`` so Lab 1 routers can be
registered as platform devices. ``ck_configuration_snapshots_vendor`` is
deliberately left untouched: no FRR configuration adapter exists and no FRR
configuration snapshot may be stored. No data is changed and no other schema
is altered.

Downgrade restores the previous two-vendor CHECK. It refuses (rather than
deleting or rewriting rows) if any ``frr`` device still exists, since devices
are referenced by incidents and telemetry.

Revision ID: 0004_frr_device_vendor
Revises: 0003_telemetry_samples
Create Date: 2026-10-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_frr_device_vendor"
down_revision: str | None = "0003_telemetry_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_devices_vendor", "devices", type_="check")
    op.create_check_constraint(
        "ck_devices_vendor",
        "devices",
        "vendor IN ('cisco-ios-xe', 'arista-eos', 'frr')",
    )


def downgrade() -> None:
    remaining = op.get_bind().execute(sa.text("SELECT count(*) FROM devices WHERE vendor = 'frr'"))
    if remaining.scalar_one() > 0:
        raise RuntimeError(
            "cannot downgrade 0004_frr_device_vendor: devices with vendor 'frr' still exist"
        )
    op.drop_constraint("ck_devices_vendor", "devices", type_="check")
    op.create_check_constraint(
        "ck_devices_vendor",
        "devices",
        "vendor IN ('cisco-ios-xe', 'arista-eos')",
    )
