# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket timers: a technician's running clock on a ticket.

Revision ID: c3f8a1d7e624
Revises: b7e2d4f1a903
"""
from alembic import op
import sqlalchemy as sa

revision = "c3f8a1d7e624"
down_revision = "b7e2d4f1a903"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ticket_timers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE", name="fk_ticket_timers_ticket_id_tickets"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE", name="fk_ticket_timers_user_id_users"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ticket_id", "user_id", name="uq_ticket_timers_ticket_user"),
    )
    op.create_index("ix_ticket_timers_ticket_id", "ticket_timers", ["ticket_id"])
    op.create_index("ix_ticket_timers_user_id", "ticket_timers", ["user_id"])


def downgrade():
    op.drop_table("ticket_timers")
