# SPDX-License-Identifier: AGPL-3.0-or-later
"""Action1 batch deployments (several machines or a group, scheduled) and software bundles.

Revision ID: d4f8a2c6e913
Revises: c8e1d5b2a740
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d4f8a2c6e913"
down_revision = "c8e1d5b2a740"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("software_deployments", sa.Column("schedule_id", sa.String(200), nullable=True))
    op.add_column("software_deployments", sa.Column("results", JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.create_table(
        "software_bundles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("organization_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("platform", sa.String(20), nullable=False),
        sa.Column("packages", JSONB(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_software_bundles_org_name"),
    )


def downgrade():
    op.drop_table("software_bundles")
    op.drop_column("software_deployments", "results")
    op.drop_column("software_deployments", "schedule_id")
