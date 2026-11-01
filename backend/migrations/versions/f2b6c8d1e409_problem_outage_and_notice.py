# SPDX-License-Identifier: AGPL-3.0-or-later
"""Problem outage scope and draft service notice link.

Revision ID: f2b6c8d1e409
Revises: e1a4b8c9d203
Create Date: 2026-11-01 00:56:00
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b6c8d1e409"
down_revision = "e1a4b8c9d203"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("problems", sa.Column("outage_scope", sa.String(length=255), nullable=False, server_default=sa.text("''")))
    op.add_column("problems", sa.Column("notice_id", sa.Integer(), nullable=True))
    op.create_foreign_key("problems_notice_id_fkey", "problems", "service_notices", ["notice_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    op.drop_constraint("problems_notice_id_fkey", "problems", type_="foreignkey")
    op.drop_column("problems", "notice_id")
    op.drop_column("problems", "outage_scope")
