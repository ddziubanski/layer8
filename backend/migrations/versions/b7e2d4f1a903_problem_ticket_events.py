# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ticket history records linking a ticket to a problem and unlinking it.

Revision ID: b7e2d4f1a903
Revises: 0d4c77c2c48f
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e2d4f1a903"
down_revision = "0d4c77c2c48f"
branch_labels = None
depends_on = None

OLD_EVENTS = ['created', 'routed', 'assigned', 'escalated', 'status_changed', 'priority_changed', 'updated', 'comment_added',
              'attachment_added', 'requester_changed', 'device_linked', 'device_unlinked', 'script_run', 'remote_session', 'merged',
              'system_linked', 'system_unlinked', 'parent_changed', 'child_changed', 'kb_linked', 'survey_sent', 'survey_answered']
EVENTS = OLD_EVENTS + ['problem_linked', 'problem_unlinked']


def _event_check(values: list[str]) -> None:
    op.drop_constraint("ck_ticket_events_type", "ticket_events", type_="check")
    op.create_check_constraint("ck_ticket_events_type", "ticket_events", sa.column("type").in_(values))


def upgrade():
    _event_check(EVENTS)


def downgrade():
    op.execute("DELETE FROM ticket_events WHERE type IN ('problem_linked', 'problem_unlinked')")
    _event_check(OLD_EVENTS)
