# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tickets nobody has taken: since when, and when that alerted.

Revision ID: b81f3c5e6a27
Revises: a4c7e2d91b30
"""
from alembic import op
import sqlalchemy as sa

revision = "b81f3c5e6a27"
down_revision = "a4c7e2d91b30"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("tickets", sa.Column("unassigned_since", sa.DateTime(timezone=True)))
    op.add_column("tickets", sa.Column("unaccepted_alerted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_tickets_unassigned_since", "tickets", ["unassigned_since"])
    # Open tickets nobody has taken have waited since they were made.
    op.execute("UPDATE tickets SET unassigned_since = created_at WHERE assignee_id IS NULL AND status IN ('new', 'open') AND merged_into_id IS NULL")


def downgrade():
    op.drop_index("ix_tickets_unassigned_since", table_name="tickets")
    op.drop_column("tickets", "unaccepted_alerted_at")
    op.drop_column("tickets", "unassigned_since")
