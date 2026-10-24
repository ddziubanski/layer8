# SPDX-License-Identifier: AGPL-3.0-or-later
"""Runner-driven vendor reads (Juniper).

Revision ID: e2b84d6f1c57
Revises: d19a5c3e7b40
"""
from alembic import op
import sqlalchemy as sa

revision = "e2b84d6f1c57"
down_revision = "d19a5c3e7b40"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "network_reads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("script_runs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("scheduled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
    )


def downgrade():
    op.drop_table("network_reads")
