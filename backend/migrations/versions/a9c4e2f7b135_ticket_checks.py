# SPDX-License-Identifier: AGPL-3.0-or-later
"""Self-checking tickets: conditions a ticket waits on, and their history events.

Revision ID: a9c4e2f7b135
Revises: f2b6c8d1e409
"""
import sqlalchemy as sa
from alembic import op

revision = "a9c4e2f7b135"
down_revision = "f2b6c8d1e409"
branch_labels = None
depends_on = None

OLD_EVENTS = ['created', 'routed', 'assigned', 'escalated', 'status_changed', 'priority_changed', 'updated', 'comment_added',
              'attachment_added', 'requester_changed', 'device_linked', 'device_unlinked', 'script_run', 'remote_session', 'merged',
              'system_linked', 'system_unlinked', 'parent_changed', 'child_changed', 'kb_linked', 'survey_sent', 'survey_answered',
              'problem_linked', 'problem_unlinked']
NEW = ['check_added', 'check_cleared', 'check_confirmed', 'check_cancelled']


def _event_check(values: list[str]) -> None:
    op.drop_constraint("ck_ticket_events_type", "ticket_events", type_="check")
    op.create_check_constraint("ck_ticket_events_type", "ticket_events", sa.column("type").in_(values))


def upgrade():
    op.create_table(
        "ticket_checks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticket_id", sa.BigInteger(), sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("label", sa.String(300), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="waiting"),
        sa.Column("detail", sa.String(500), nullable=False, server_default=""),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_by_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ticket_checks_ticket_id", "ticket_checks", ["ticket_id"])
    op.create_index("ix_ticket_checks_state", "ticket_checks", ["state"])
    _event_check(OLD_EVENTS + NEW)


def downgrade():
    op.execute("DELETE FROM ticket_events WHERE type IN ('check_added', 'check_cleared', 'check_confirmed', 'check_cancelled')")
    _event_check(OLD_EVENTS)
    op.drop_table("ticket_checks")
