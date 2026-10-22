"""attachment_added event type

Revision ID: 91da8c7ff4d3
Revises: 63a512b58734
Create Date: 2026-10-18 19:45:58.222441

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '91da8c7ff4d3'
down_revision: Union[str, Sequence[str], None] = '63a512b58734'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD = ["created", "routed", "assigned", "escalated", "status_changed", "priority_changed", "updated", "comment_added"]
NEW = OLD + ["attachment_added"]


def _check(values: list[str]) -> None:
    op.drop_constraint("ck_ticket_events_type", "ticket_events", type_="check")
    op.create_check_constraint("ck_ticket_events_type", "ticket_events", sa.column("type").in_(values))


def upgrade() -> None:
    _check(NEW)


def downgrade() -> None:
    op.execute("DELETE FROM ticket_events WHERE type = 'attachment_added'")
    _check(OLD)
