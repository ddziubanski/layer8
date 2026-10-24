# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only Meraki client observations.

Revision ID: a86d20f49c71
Revises: f52b6d10a930
"""
from alembic import op
import sqlalchemy as sa

revision = "a86d20f49c71"
down_revision = "f52b6d10a930"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meraki_clients",
        sa.Column("network_id", sa.String(100), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
        *[sa.Column(name, sa.String(size), nullable=False) for name, size in (
            ("network_name", 255), ("client_id", 100), ("address", 45), ("description", 255),
            ("status", 32), ("vlan", 32), ("switch_name", 255), ("switch_serial", 100),
            ("port", 100), ("connection", 32), ("ssid", 100),
        )],
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("network_id", "mac", name="pk_meraki_clients"),
    )
    op.create_index("ix_meraki_clients_mac", "meraki_clients", ["mac"])


def downgrade():
    op.drop_table("meraki_clients")
