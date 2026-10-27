# SPDX-License-Identifier: AGPL-3.0-or-later
"""Index derived recovery review lookups by ticket.

Revision ID: f3b8d2a6c901
Revises: f1a7c9d3b205
"""
from alembic import op

revision = "f3b8d2a6c901"
down_revision = "f1a7c9d3b205"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_network_outages_ticket_id", "network_outages", ["ticket_id"])


def downgrade():
    op.drop_index("ix_network_outages_ticket_id", table_name="network_outages")
