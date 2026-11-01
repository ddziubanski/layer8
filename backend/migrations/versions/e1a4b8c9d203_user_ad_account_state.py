# SPDX-License-Identifier: AGPL-3.0-or-later
"""Users: AD lock and account expiry, for ticket context.

Revision ID: e1a4b8c9d203
Revises: d5a2c9e7b318
"""
from alembic import op
import sqlalchemy as sa

revision = "e1a4b8c9d203"
down_revision = "d5a2c9e7b318"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("ad_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("users", sa.Column("account_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("users", "account_expires_at")
    op.drop_column("users", "ad_locked")
