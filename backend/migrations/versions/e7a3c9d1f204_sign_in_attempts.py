# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sign-in and receiving-page attempts, counted in the database (shared by the API's workers);
sessions remember whether they were started through Microsoft.

Revision ID: e7a3c9d1f204
Revises: d4f8a2c6e913
"""
import sqlalchemy as sa
from alembic import op

revision = "e7a3c9d1f204"
down_revision = "d4f8a2c6e913"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "sign_in_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("ok", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index("ix_sign_in_attempts_address", "sign_in_attempts", ["kind", "address", "at"])
    op.create_index("ix_sign_in_attempts_user", "sign_in_attempts", ["user_id", "at"])


    op.add_column("user_sessions", sa.Column("via_microsoft", sa.Boolean(), server_default=sa.text("false"), nullable=False))


def downgrade():
    op.drop_column("user_sessions", "via_microsoft")
    op.drop_table("sign_in_attempts")
