# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proactive tickets: the condition each was opened for.

Revision ID: b3d7f1a6c248
Revises: a9c4e2f7b135
"""
import sqlalchemy as sa
from alembic import op

revision = "b3d7f1a6c248"
down_revision = "a9c4e2f7b135"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "proactive_alerts",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_detail", sa.String(500), nullable=False, server_default=""),
    )
    op.create_index("ix_proactive_alerts_ticket_id", "proactive_alerts", ["ticket_id"])


def downgrade():
    op.drop_table("proactive_alerts")
