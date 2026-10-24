# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded cache for explicit read-only network diagnostics.

Revision ID: b97e31a50d82
Revises: a86d20f49c71
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b97e31a50d82"
down_revision = "a86d20f49c71"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "meraki_diagnostics",
        sa.Column("network_id", sa.String(100), nullable=False),
        sa.Column("mac", sa.String(17), nullable=False),
        sa.Column("identity_hash", sa.String(64), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("last_error", sa.String(255)),
        sa.PrimaryKeyConstraint("network_id", "mac", name="pk_meraki_diagnostics"),
    )
    op.create_index("ix_meraki_diagnostics_attempted_at", "meraki_diagnostics", ["attempted_at"])


def downgrade():
    op.drop_table("meraki_diagnostics")
