# SPDX-License-Identifier: AGPL-3.0-or-later
"""Vendor-neutral network observations, inventory and outage state (UniFi, Juniper).

Revision ID: d19a5c3e7b40
Revises: c08f42b61e93
"""
from alembic import op
import sqlalchemy as sa

revision = "d19a5c3e7b40"
down_revision = "c08f42b61e93"
branch_labels = None
depends_on = None


def _text(name, size):
    return sa.Column(name, sa.String(size), nullable=False, server_default="")


def upgrade():
    op.create_table(
        "network_observations",
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
        _text("source_name", 255), _text("address", 45), _text("hostname", 255), _text("network", 100),
        _text("uplink_name", 255), _text("port", 100), _text("ssid", 100), _text("connection", 16),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider", "source_id", "mac", name="pk_network_observations"),
    )
    op.create_index("ix_network_observations_mac", "network_observations", ["mac"])
    op.create_table(
        "network_inventory",
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("device_key", sa.String(200), nullable=False),
        _text("source_name", 255), _text("name", 255), _text("model", 100), _text("kind", 16), _text("mac", 17),
        _text("address", 45), _text("state", 16), _text("firmware", 100),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider", "source_id", "device_key", name="pk_network_inventory"),
    )
    op.create_table(
        "network_outages",
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("device_key", sa.String(200), nullable=False),
        sa.Column("is_down", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("down_since", sa.DateTime(timezone=True)),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="SET NULL")),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("provider", "source_id", "device_key", name="pk_network_outages"),
    )


def downgrade():
    op.drop_table("network_outages")
    op.drop_table("network_inventory")
    op.drop_index("ix_network_observations_mac", table_name="network_observations")
    op.drop_table("network_observations")
