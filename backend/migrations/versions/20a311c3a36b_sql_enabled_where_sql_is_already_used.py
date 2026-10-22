"""sql enabled where sql is already used

Revision ID: 20a311c3a36b
Revises: d5ae285fd8a4
Create Date: 2026-10-21 20:47:15.991353

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20a311c3a36b'
down_revision: Union[str, Sequence[str], None] = 'd5ae285fd8a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Installs already using SQL (SQL rules or library queries) keep it allowed (Settings > General)."""
    op.execute("""
        INSERT INTO app_settings (key, value)
        SELECT 'general', '{"sql_enabled": true}'::jsonb
        WHERE EXISTS (SELECT 1 FROM ticket_rules WHERE mode = 'sql') OR EXISTS (SELECT 1 FROM scripts WHERE kind = 'query')
        ON CONFLICT (key) DO UPDATE SET value = app_settings.value || '{"sql_enabled": true}'::jsonb
    """)


def downgrade() -> None:
    op.execute("UPDATE app_settings SET value = value - 'sql_enabled' WHERE key = 'general'")
