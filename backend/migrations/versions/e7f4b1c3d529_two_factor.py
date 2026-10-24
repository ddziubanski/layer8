# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two-factor sign-in (authenticator app) on user accounts.

Revision ID: e7f4b1c3d529
Revises: d6e3a9b2c418
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e7f4b1c3d529"
down_revision = "d6e3a9b2c418"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("totp_secret", sa.Text()))
    op.add_column("users", sa.Column("totp_pending", sa.Text()))
    op.add_column("users", sa.Column("totp_enabled_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger()))
    op.add_column("users", sa.Column("totp_recovery", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")))


def downgrade():
    for column in ("totp_recovery", "totp_last_step", "totp_enabled_at", "totp_pending", "totp_secret"):
        op.drop_column("users", column)
