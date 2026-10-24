# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nutanix VMs, their ticket links and power actions.

Revision ID: f3a91c27d5e8
Revises: e2b84d6f1c57
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "f3a91c27d5e8"
down_revision = "e2b84d6f1c57"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nutanix_vms",
        sa.Column("ext_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("power_state", sa.String(20), nullable=False, server_default=""),
        sa.Column("cluster_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("host_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("vcpus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("memory_mb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ips", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("macs", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("guest_tools", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("guest_tools_reachable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("present", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_nutanix_vms_name", "nutanix_vms", ["name"])
    op.create_index("ix_nutanix_vms_device_id", "nutanix_vms", ["device_id"])
    op.create_table(
        "ticket_nutanix_vms",
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("vm_id", sa.String(64), sa.ForeignKey("nutanix_vms.ext_id", ondelete="CASCADE"), primary_key=True),
        sa.Column("linked_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_ticket_nutanix_vms_vm_id", "ticket_nutanix_vms", ["vm_id"])
    op.create_table(
        "nutanix_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("vm_id", sa.String(64), sa.ForeignKey("nutanix_vms.ext_id", ondelete="CASCADE"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("task_id", sa.String(200), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="submitted"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_nutanix_actions_ticket_id", "nutanix_actions", ["ticket_id"])
    op.create_index("ix_nutanix_actions_status", "nutanix_actions", ["status"])


def downgrade():
    op.drop_table("nutanix_actions")
    op.drop_table("ticket_nutanix_vms")
    op.drop_table("nutanix_vms")
