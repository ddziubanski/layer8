# SPDX-License-Identifier: AGPL-3.0-or-later
"""Time spent on tickets.

Revision ID: d6e3a9b2c418
Revises: c5d2e8f1a347
"""
from alembic import op
import sqlalchemy as sa

revision = "d6e3a9b2c418"
down_revision = "c5d2e8f1a347"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "time_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("worked_on", sa.Date(), nullable=False),
        sa.Column("note", sa.String(500), nullable=False, server_default=""),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("comments.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_time_entries_ticket_id", "time_entries", ["ticket_id"])
    op.create_index("ix_time_entries_user_id", "time_entries", ["user_id"])
    op.create_index("ix_time_entries_worked_on", "time_entries", ["worked_on"])


def downgrade():
    op.drop_table("time_entries")
