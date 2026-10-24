# SPDX-License-Identifier: AGPL-3.0-or-later
"""Wallboards: read-only wall displays with a secret link.

Revision ID: b7c41e2a9d05
Revises: a36d82c9e114
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b7c41e2a9d05"
down_revision = "a36d82c9e114"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "wallboards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("queue_ids", postgresql.ARRAY(sa.Integer()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("unassigned_alert_minutes", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("wallboards")
