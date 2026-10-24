# SPDX-License-Identifier: AGPL-3.0-or-later
"""Retain successful worker pass time across subsequent failures.

Revision ID: 892b614ce3af
Revises: e7f4b1c3d529
"""
from alembic import op
import sqlalchemy as sa

revision = "892b614ce3af"
down_revision = "e7f4b1c3d529"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("worker_jobs", sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True))
    # Only the latest successful run is known on older installations. Do not invent history.
    op.execute("UPDATE worker_jobs SET last_success_at = last_finished_at WHERE last_ok IS TRUE")


def downgrade():
    op.drop_column("worker_jobs", "last_success_at")
