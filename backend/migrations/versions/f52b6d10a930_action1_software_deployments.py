"""Action1 endpoint organisation and reviewed software deployment intent.

Revision ID: f52b6d10a930
Revises: c3d58f1b7a20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f52b6d10a930"
down_revision = "c3d58f1b7a20"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("device_patches", sa.Column("organization_id", sa.String(64), nullable=False, server_default=""))
    op.create_table(
        "software_deployments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="SET NULL")),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="review"),
        sa.Column("instance_id", sa.String(200)),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_software_deployments_device_id", "software_deployments", ["device_id"])
    op.create_index("ix_software_deployments_ticket_id", "software_deployments", ["ticket_id"])


def downgrade():
    op.drop_table("software_deployments")
    op.drop_column("device_patches", "organization_id")
