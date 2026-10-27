# SPDX-License-Identifier: AGPL-3.0-or-later
"""Link cached UniFi equipment/clients and IPAM records in ticket Devices.

Revision ID: f1a7c9d3b205
Revises: e6b3f8a2c1d4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f1a7c9d3b205"
down_revision = "e6b3f8a2c1d4"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ticket_network_links",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("source_id", sa.String(200), nullable=False),
        sa.Column("object_key", sa.String(200), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("linked_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ticket_id", "kind", "source_id", "object_key", name="uq_ticket_network_links_target"))
    op.create_index("ix_ticket_network_links_ticket_id", "ticket_network_links", ["ticket_id"])


def downgrade():
    op.drop_table("ticket_network_links")
