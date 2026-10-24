# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opening hours per queue; technicians' Entra ID and Teams presence.

Revision ID: c3d58f1b7a20
Revises: b7c41e2a9d05
"""
from alembic import op
import sqlalchemy as sa

revision = "c3d58f1b7a20"
down_revision = "b7c41e2a9d05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("queues", sa.Column("hours", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("entra_id", sa.String(36), nullable=True))
    op.add_column("users", sa.Column("presence", sa.String(16), nullable=True))
    op.add_column("users", sa.Column("presence_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("users", "presence_at")
    op.drop_column("users", "presence")
    op.drop_column("users", "entra_id")
    op.drop_column("queues", "hours")
