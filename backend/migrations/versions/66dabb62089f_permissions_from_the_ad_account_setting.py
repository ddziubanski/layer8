"""permissions from the AD account setting

Revision ID: 66dabb62089f
Revises: 0e7406fed2ef
Create Date: 2026-10-20 18:03:29.163133

"""
from typing import Sequence, Union

from alembic import op
import json

import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '66dabb62089f'
down_revision: Union[str, Sequence[str], None] = '0e7406fed2ef'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Who may create AD accounts moves from Settings > Directory to Settings > Permissions."""
    conn = op.get_bind()
    row = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = 'directory'")).scalar()
    if not row:
        return
    value = dict(row)
    who = value.pop("ad_new_user_allowed", None)
    group = (value.pop("ad_new_user_group", None) or "").strip()
    conn.execute(sa.text("UPDATE app_settings SET value = CAST(:v AS jsonb) WHERE key = 'directory'"), {"v": json.dumps(value)})
    if who in (None, "agents"):
        return
    rule = {"who": "admins", "group_ids": []}
    if who == "group" and group:
        gid = conn.execute(sa.text("SELECT id FROM groups WHERE lower(name) = lower(:n) ORDER BY id LIMIT 1"), {"n": group}).scalar()
        if gid is not None:
            rule = {"who": "groups", "group_ids": [gid]}
    current = conn.execute(sa.text("SELECT value FROM app_settings WHERE key = 'permissions'")).scalar()
    perms = dict(current or {})
    perms["ad_create_accounts"] = rule
    if current is None:
        conn.execute(sa.text("INSERT INTO app_settings (key, value) VALUES ('permissions', CAST(:v AS jsonb))"), {"v": json.dumps(perms)})
    else:
        conn.execute(sa.text("UPDATE app_settings SET value = CAST(:v AS jsonb) WHERE key = 'permissions'"), {"v": json.dumps(perms)})


def downgrade() -> None:
    """The permission stays; the old setting goes back to its default (technicians and admins)."""
