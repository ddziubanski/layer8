# SPDX-License-Identifier: AGPL-3.0-or-later
"""The receiving page without sign-in is now off by default. An installation that has purchase
orders and never chose keeps it on, so receiving sheets already printed go on working.

Revision ID: f1b6d8e2a357
Revises: e7a3c9d1f204
"""
from alembic import op

revision = "f1b6d8e2a357"
down_revision = "e7a3c9d1f204"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO app_settings (key, value)
        SELECT 'purchasing', '{"public_receiving": true}'::jsonb
        WHERE EXISTS (SELECT 1 FROM purchase_orders)
        ON CONFLICT (key) DO UPDATE
            SET value = app_settings.value || '{"public_receiving": true}'::jsonb
            WHERE NOT app_settings.value ? 'public_receiving'
    """)


def downgrade():
    pass
