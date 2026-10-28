# SPDX-License-Identifier: AGPL-3.0-or-later
"""Archive queues: retired and hidden from Settings, tickets kept.

Revision ID: a4c7e9b2d518
Revises: f3b8d2a6c901
"""
from alembic import op
import sqlalchemy as sa

revision = "a4c7e9b2d518"
down_revision = "f3b8d2a6c901"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("queues", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("queues", "archived_at")
