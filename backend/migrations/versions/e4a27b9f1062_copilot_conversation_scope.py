"""Bind Copilot conversations to a technician, ticket and Microsoft account.

Revision ID: e4a27b9f1062
Revises: c3d58f1b7a20
"""
from alembic import op
import sqlalchemy as sa

revision = "e4a27b9f1062"
down_revision = "c3d58f1b7a20"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("copilot_conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.String(200), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_key", sa.String(64), nullable=False),
        sa.Column("shared_ticket", sa.Boolean(), nullable=False),
        sa.Column("shared_internal", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "conversation_id", name="uq_copilot_user_conversation"),
    )
    op.create_index("ix_copilot_conversations_ticket_id", "copilot_conversations", ["ticket_id"])


def downgrade():
    op.drop_table("copilot_conversations")
