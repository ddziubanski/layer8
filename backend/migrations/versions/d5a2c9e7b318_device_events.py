# SPDX-License-Identifier: AGPL-3.0-or-later
"""Device events: changes the syncs notice, for the device timeline.

Revision ID: d5a2c9e7b318
Revises: c3f8a1d7e624
"""
from alembic import op
import sqlalchemy as sa

revision = "d5a2c9e7b318"
down_revision = "c3f8a1d7e624"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE", name="fk_device_events_device_id_devices"), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("detail", sa.String(500), server_default="", nullable=False),
        sa.Column("tone", sa.String(8), server_default="info", nullable=False),
        sa.Column("source", sa.String(32), server_default="", nullable=False),
    )
    op.create_index("ix_device_events_device_at", "device_events", ["device_id", "at"])


def downgrade():
    op.drop_table("device_events")
