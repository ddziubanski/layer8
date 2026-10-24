"""Durable Meraki availability alert receipts and device state.

Revision ID: c08f42b61e93
Revises: b97e31a50d82
"""
from alembic import op
import sqlalchemy as sa

revision = "c08f42b61e93"
down_revision = "b97e31a50d82"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("meraki_webhook_receipts",
        sa.Column("organization_id", sa.String(100), nullable=False),
        sa.Column("alert_id", sa.String(100), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("network_id", sa.String(100), nullable=False),
        sa.Column("serial", sa.String(32), nullable=False),
        sa.Column("alert_type", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("organization_id", "alert_id"))
    op.create_index("ix_meraki_webhook_receipts_received_at", "meraki_webhook_receipts", ["received_at"])
    op.create_table("meraki_webhook_incidents",
        sa.Column("organization_id", sa.String(100), nullable=False),
        sa.Column("network_id", sa.String(100), nullable=False),
        sa.Column("serial", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_down", sa.Boolean(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("organization_id", "network_id", "serial"))
    op.create_index("ix_meraki_webhook_incidents_received_at", "meraki_webhook_incidents", ["received_at"])


def downgrade():
    op.drop_table("meraki_webhook_incidents")
    op.drop_table("meraki_webhook_receipts")
