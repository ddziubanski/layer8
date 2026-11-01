# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problem suggestions people dismissed.

Revision ID: c8e1d5b2a740
Revises: b3d7f1a6c248
"""
import sqlalchemy as sa
from alembic import op

revision = "c8e1d5b2a740"
down_revision = "b3d7f1a6c248"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "problem_suggestion_dismissals",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("dismissed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade():
    op.drop_table("problem_suggestion_dismissals")
