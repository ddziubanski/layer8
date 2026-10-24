# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canned replies.

Revision ID: c5d2e8f1a347
Revises: b81f3c5e6a27
"""
from alembic import op
import sqlalchemy as sa

revision = "c5d2e8f1a347"
down_revision = "b81f3c5e6a27"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "canned_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("to_requester", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("queue_id", sa.Integer(), sa.ForeignKey("queues.id", ondelete="CASCADE")),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_canned_replies_queue_id", "canned_replies", ["queue_id"])
    op.create_index("ix_canned_replies_owner_id", "canned_replies", ["owner_id"])


def downgrade():
    op.drop_table("canned_replies")
