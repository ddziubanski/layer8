# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problem records, linked tickets and internal updates.

Revision ID: 0d4c77c2c48f
Revises: a4c7e9b2d518
"""
from alembic import op
import sqlalchemy as sa

revision = "0d4c77c2c48f"
down_revision = "a4c7e9b2d518"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "problems",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("status", sa.String(32), server_default="open", nullable=False),
        sa.Column("root_cause", sa.Text(), server_default="", nullable=False),
        sa.Column("workaround", sa.Text(), server_default="", nullable=False),
        sa.Column("resolution", sa.Text(), server_default="", nullable=False),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('open', 'investigating', 'known_error', 'resolved', 'closed')", name="ck_problems_status_problemstatus"),
    )
    op.create_index("ix_problems_status", "problems", ["status"])
    op.create_table(
        "problem_tickets",
        sa.Column("problem_id", sa.Integer(), sa.ForeignKey("problems.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_problem_tickets_ticket_id", "problem_tickets", ["ticket_id"])
    op.create_table(
        "problem_updates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("problem_id", sa.Integer(), sa.ForeignKey("problems.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("body", sa.Text(), server_default="", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_problem_updates_problem_id", "problem_updates", ["problem_id"])


def downgrade():
    op.drop_index("ix_problem_updates_problem_id", table_name="problem_updates")
    op.drop_table("problem_updates")
    op.drop_index("ix_problem_tickets_ticket_id", table_name="problem_tickets")
    op.drop_table("problem_tickets")
    op.drop_index("ix_problems_status", table_name="problems")
    op.drop_table("problems")
