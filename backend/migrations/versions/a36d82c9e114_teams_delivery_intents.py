"""Teams delivery intents, separate from append-only ticket history.

Revision ID: a36d82c9e114
Revises: a18d2040b002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a36d82c9e114"
down_revision = "a18d2040b002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("teams_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_teams_deliveries_ticket_id", "teams_deliveries", ["ticket_id"])


def downgrade():
    op.drop_table("teams_deliveries")
