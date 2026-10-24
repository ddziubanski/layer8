# SPDX-License-Identifier: AGPL-3.0-or-later
"""action1 enabled for installs that use it

Revision ID: d5ae285fd8a4
Revises: 87fb44a8410e
Create Date: 2026-10-21 18:14:54.692781

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5ae285fd8a4'
down_revision: Union[str, Sequence[str], None] = '87fb44a8410e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Installs that already have Action1 credentials keep it switched on (Settings > General)."""
    op.execute("""
        INSERT INTO app_settings (key, value)
        SELECT 'general', '{"action1_enabled": true}'::jsonb
        WHERE EXISTS (SELECT 1 FROM app_settings WHERE key = 'action1' AND coalesce(value->>'action1_client_id', '') <> '')
        ON CONFLICT (key) DO UPDATE SET value = app_settings.value || '{"action1_enabled": true}'::jsonb
    """)


def downgrade() -> None:
    op.execute("UPDATE app_settings SET value = value - 'action1_enabled' WHERE key = 'general'")
