# SPDX-License-Identifier: AGPL-3.0-or-later
"""People: mobile, office, company and manager from the directory.

Revision ID: b5d81e2c4a90
Revises: 892b614ce3af
"""
from alembic import op
import sqlalchemy as sa

revision = "b5d81e2c4a90"
down_revision = "892b614ce3af"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("mobile", sa.String(64), nullable=True))
    op.add_column("users", sa.Column("office", sa.String(200), nullable=True))
    op.add_column("users", sa.Column("company", sa.String(200), nullable=True))
    op.add_column("users", sa.Column("manager_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_users_manager_id", "users", ["manager_id"])


def downgrade():
    op.drop_index("ix_users_manager_id", table_name="users")
    op.drop_column("users", "manager_id")
    op.drop_column("users", "company")
    op.drop_column("users", "office")
    op.drop_column("users", "mobile")
