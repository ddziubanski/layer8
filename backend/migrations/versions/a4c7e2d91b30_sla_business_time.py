# SPDX-License-Identifier: AGPL-3.0-or-later
"""SLA in business time: paused clocks and the policy window on tickets.

Revision ID: a4c7e2d91b30
Revises: f3a91c27d5e8
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a4c7e2d91b30"
down_revision = "f3a91c27d5e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tickets", sa.Column("sla_paused_at", sa.DateTime(timezone=True)))
    op.add_column("tickets", sa.Column("sla_left", postgresql.JSONB()))
    op.add_column("tickets", sa.Column("sla_paused_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")))
    op.add_column("tickets", sa.Column("sla_resolution_minutes", sa.Integer()))


def downgrade():
    for column in ("sla_resolution_minutes", "sla_paused_minutes", "sla_left", "sla_paused_at"):
        op.drop_column("tickets", column)
